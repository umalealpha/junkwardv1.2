"""
agent_portal/service.py — ingest, pay-run rollup, and bank-payout export.

Standalone (CFO 2026-07-06): computes + exports; no GL/payments coupling.
"""
from __future__ import annotations

import calendar
import csv
import io
import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction

from .commission_engine import STREAMS, compute_row, norm
from .models import Agent, AgentBankAccount, CommissionCycle, CommissionLine, PayoutBatch, ReportSubmission, SourceReport

ZERO = Decimal('0.00')

# Streams whose source row has NO policy number at col[1] (col[1] is the
# operation) — excluded from the never-pay-twice policy guard.
_NO_POLICY_STREAMS = {'incentives'}
# Streams where the SAME policy legitimately earns again in a later cycle
# (arrears collections; monthly dependant premium; per-item KYC claims). For
# these the SOP's never-pay-twice remains a MANUAL finance cross-check — a
# hard code block here would reject real money.
_RECURRING_STREAMS = {'collection', 'bank_confirmation', 'hospital', 'kyc_claims'}


def _month_tag(source):
    """Commission Month (c[10]) of a New Sales row -> '1' or '2'."""
    try:
        m = str(source[10])
    except (IndexError, TypeError):
        m = ''
    return '2' if '2' in m else '1'


def _apply_never_pay_twice(cycle, stream_key, lines):
    """SOP 'never pay a policy twice', enforced where a repeat is unambiguous:
    - conversion / motor: one-shot per policy -> block any cross-cycle repeat.
    - new_sales_*: month 1 then month 2 is LEGITIMATE -> block only a repeat
      of the same (policy, commission month).
    - collection / bank_confirmation: a NEW collection (different amount) is
      legitimate, but the SAME policy + SAME amount in another cycle is a
      double-claim -> blocked (Motlatsi 2026-07-07: 'not paid before').
    - hospital / kyc_claims / incentives: recurring by design -> manual check."""
    if stream_key in _NO_POLICY_STREAMS or stream_key in {'hospital', 'kyc_claims'}:
        return
    refs = {l.policy_ref for l in lines if l.payable and l.policy_ref}
    if not refs:
        return
    if stream_key in ('collection', 'bank_confirmation'):
        prior = CommissionLine.objects.filter(
            stream=stream_key, payable=True, policy_ref__in=refs,
        ).exclude(cycle=cycle)
        prior_keys = {(p.policy_ref, p.basis) for p in prior}
        for l in lines:
            if l.payable and l.policy_ref and (l.policy_ref, l.basis) in prior_keys:
                l.payable = False
                l.commission = ZERO
                l.reason = 'Same amount already paid for this policy in an earlier cycle (never-pay-twice)'
        return
    # Conversion is one-shot per policy across the WHOLE family (legacy
    # 'conversion' + the v31 MIS/Liberty split) — a policy paid under any
    # conversion key must not pay again under another.
    if stream_key.startswith('conversion'):
        prior = CommissionLine.objects.filter(
            stream__startswith='conversion', payable=True, policy_ref__in=refs,
        ).exclude(cycle=cycle)
    else:
        prior = CommissionLine.objects.filter(
            stream=stream_key, payable=True, policy_ref__in=refs,
        ).exclude(cycle=cycle)
    if stream_key.startswith('new_sales'):
        prior_keys = {(p.policy_ref, _month_tag(p.source)) for p in prior}
        def is_dup(l):
            return (l.policy_ref, _month_tag(l.source)) in prior_keys
    else:   # conversion, motor — one-shot per policy
        already = set(prior.values_list('policy_ref', flat=True))
        def is_dup(l):
            return l.policy_ref in already
    for l in lines:
        if l.payable and l.policy_ref and is_dup(l):
            l.payable = False
            l.commission = ZERO
            l.reason = 'Already paid in an earlier cycle (never-pay-twice)'


# Junk rows the JS tool's parseRows drops anywhere in the paste (template
# titles / guidance lines) — Fable review #8: without this, pasting the
# submission template creates fake Agent records from its heading rows.
_JUNK_ROW = re.compile(r'^(\[|unicoin|how we confirm|pay:|agent name$|agent$)', re.IGNORECASE)


def _rows_from_csv(text: str):
    """Parse CSV/TSV text into cell-lists, dropping header/guidance rows."""
    if not text:
        return []
    # JS parity (#13): tab wins if ANY line contains a tab — a global
    # comma-vs-tab count is tipped by thousands separators in amounts.
    sniff = '\t' if any('\t' in ln for ln in text.splitlines()) else ','
    rows = list(csv.reader(io.StringIO(text), delimiter=sniff))
    rows = [r for r in rows if any(norm(c) for c in r)]
    return [r for r in rows if not _JUNK_ROW.match(norm(r[0]))]


def _conversion_cutoff(cycle: CommissionCycle):
    """Inception must be 3+ months before the cycle end (RealPay Conversion)."""
    end = cycle.end_date
    y, m = end.year, end.month - 3
    while m <= 0:
        m += 12
        y -= 1
    # Fable review #6: clamp to the target month's real length (not 28) so a
    # cycle ending on the 29th–31st doesn't reject policies exactly 3 months old.
    day = min(end.day, calendar.monthrange(y, m)[1])
    return date(y, m, day)


@transaction.atomic
def ingest_stream_csv(cycle: CommissionCycle, stream_key: str, text: str, *, replace=True,
                      submitted_by=None, agent_name='', report_date=None, source='paste') -> dict:
    """Compute + store CommissionLines for one stream from CSV/TSV text.
    Also records the raw upload as a ReportSubmission (the audit trail of what
    people submit on the Daily tab)."""
    from .commission_engine import LEGACY_STREAMS
    if stream_key not in STREAMS and stream_key not in LEGACY_STREAMS:
        raise ValueError(f'Unknown stream: {stream_key}')
    if cycle.status != CommissionCycle.Status.OPEN:
        raise ValueError(f'Cycle is {cycle.get_status_display()} — reopen it before ingesting.')
    _spec = STREAMS.get(stream_key) or LEGACY_STREAMS[stream_key]
    ctx = {'cutoff': _conversion_cutoff(cycle)} if _spec[3] else {}
    if replace:
        CommissionLine.objects.filter(cycle=cycle, stream=stream_key).delete()

    has_policy = stream_key not in _NO_POLICY_STREAMS
    lines, agents_seen = [], {}
    for cells in _rows_from_csv(text):
        res = compute_row(stream_key, cells, ctx)
        if res is None:
            continue
        name = res['agent']
        ag = agents_seen.get(name)
        if ag is None:
            ag, _ = Agent.objects.get_or_create(name=name)
            agents_seen[name] = ag
        pref = (str(cells[1]).strip()[:60] if has_policy and len(cells) > 1 else '')
        lines.append(CommissionLine(
            cycle=cycle, agent=ag, stream=stream_key,
            basis=res['basis'], commission=res['commission'],
            payable=res['pay'], reason=res['reason'][:200], source=cells,
            policy_ref=pref,
        ))
    _apply_never_pay_twice(cycle, stream_key, lines)
    CommissionLine.objects.bulk_create(lines)
    payable = sum((l.commission for l in lines if l.payable), ZERO)
    approved = sum(1 for l in lines if l.payable)
    ReportSubmission.objects.create(
        cycle=cycle, stream=stream_key, agent_name=(agent_name or '')[:160],
        report_date=report_date, source=source, raw_text=text,
        submitted_by=submitted_by, rows_count=len(lines),
        approved_count=approved, rejected_count=len(lines) - approved,
        payable_bwp=payable,
    )
    return {
        'stream': stream_key, 'rows': len(lines),
        'approved': approved,
        'rejected': len(lines) - approved,
        'payable_bwp': str(payable),
    }


@transaction.atomic
def ingest_all_policies_csv(cycle: CommissionCycle, text: str, *, submitted_by=None,
                            agent_name='', report_date=None) -> dict:
    """The all-policies extract has the New-Sales column order; split by Book
    into MIS / Liberty, and Motor-product rows into the Motor stream.

    Atomic as a whole (Fable review #12): a failure in the motor pass must not
    leave MIS/Liberty replaced while motor keeps the prior cycle's lines.
    """
    rows = _rows_from_csv(text)
    mis, lib, motor = [], [], []
    for r in rows:
        book = norm(r[4]).lower() if len(r) > 4 else ''
        product = norm(r[3]).lower() if len(r) > 3 else ''
        if 'liber' in book or 'uni' in book:
            lib.append(r)
        else:
            mis.append(r)
        if 'motor' in product:
            # Motor stream cols: agent,policy,holder,product,book,premium,kyc,status,source,ref,pgs,paid,pdate
            motor.append([r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7],
                          r[11] if len(r) > 11 else '', r[12] if len(r) > 12 else '',
                          r[13] if len(r) > 13 else '', r[14] if len(r) > 14 else '',
                          r[15] if len(r) > 15 else ''])
    def _csv(rowlist):
        buf = io.StringIO(); csv.writer(buf).writerows(rowlist); return buf.getvalue()
    kw = {'submitted_by': submitted_by, 'agent_name': agent_name,
          'report_date': report_date, 'source': 'all_policies'}
    out = {}
    out['new_sales_mis'] = ingest_stream_csv(cycle, 'new_sales_mis', _csv(mis), **kw)
    out['new_sales_liberty'] = ingest_stream_csv(cycle, 'new_sales_liberty', _csv(lib), **kw)
    out['motor'] = ingest_stream_csv(cycle, 'motor', _csv(motor), **kw)
    return out


def payrun_summary(cycle: CommissionCycle) -> dict:
    """Per-stream + per-agent rollup of the cycle's computed lines."""
    lines = list(CommissionLine.objects.filter(cycle=cycle).select_related('agent'))
    by_stream = defaultdict(lambda: {'approved': 0, 'rejected': 0, 'payable': ZERO})
    by_agent = defaultdict(lambda: {'total': ZERO, 'items': 0})
    total = ZERO
    for l in lines:
        s = by_stream[l.stream]
        if l.payable:
            s['approved'] += 1; s['payable'] += l.commission
            a = by_agent[l.agent.name]
            a['total'] += l.commission; a['items'] += 1
            total += l.commission
        else:
            s['rejected'] += 1
    return {
        'cycle': cycle.label,
        'total_payable_bwp': str(total),
        'streams': {k: {'name': STREAMS.get(k, (k,))[0], 'approved': v['approved'],
                        'rejected': v['rejected'], 'payable_bwp': str(v['payable'])}
                    for k, v in by_stream.items()},
        'agents': sorted(({'agent': a, 'payable_bwp': str(v['total']), 'items': v['items']}
                          for a, v in by_agent.items()),
                         key=lambda x: Decimal(x['payable_bwp']), reverse=True),
    }


def payout_rows(cycle: CommissionCycle):
    """(ready, held). ready = agents with a payable total AND bank details;
    held = payable but no bank details (cannot pay yet)."""
    totals = defaultdict(lambda: ZERO)
    for l in CommissionLine.objects.filter(cycle=cycle, payable=True).select_related('agent'):
        totals[l.agent_id] += l.commission
    ready, held = [], []
    banks = {b.agent_id: b for b in AgentBankAccount.objects.filter(agent_id__in=totals.keys())}
    agents = {a.id: a for a in Agent.objects.filter(id__in=totals.keys())}
    for aid, amt in totals.items():
        if amt <= 0:
            continue
        a = agents[aid]; b = banks.get(aid)
        # Fable review #2 (CRITICAL): a bank ROW existing is not enough — a
        # blank or '0' account number must HOLD the agent, never enter the
        # bank file (the seeded sheet had one such row).
        acct = (b.account_number or '').strip() if b else ''
        if b and acct not in ('', '0'):
            ready.append({'agent': a.name, 'bank': b.bank_name, 'account': acct,
                          'branch': b.branch_code, 'amount_bwp': str(amt)})
        else:
            held.append({'agent': a.name, 'amount_bwp': str(amt)})
    ready.sort(key=lambda x: x['agent']); held.sort(key=lambda x: x['agent'])
    return ready, held


def _csv_safe(v: str) -> str:
    """Neutralise Excel formula injection (Fable review #11): a cell starting
    with = + - @ executes as a formula when the bank file opens in Excel."""
    v = str(v or '')
    return ("'" + v) if v[:1] in ('=', '+', '-', '@') else v


def export_payout_csv(cycle: CommissionCycle, user=None) -> tuple[str, PayoutBatch]:
    """Build the bank payout CSV (ready-to-pay agents) + record a PayoutBatch."""
    ready, held = payout_rows(cycle)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(['Agent Name', 'Bank', 'Account Number', 'Branch Code', 'Amount (BWP)', 'Reference'])
    total = ZERO
    for r in ready:
        w.writerow([_csv_safe(r['agent']), _csv_safe(r['bank']), r['account'], r['branch'],
                    r['amount_bwp'], f"COMM {cycle.label}"])
        total += Decimal(r['amount_bwp'])
    batch = PayoutBatch.objects.create(
        cycle=cycle, created_by=user, agent_count=len(ready),
        total_bwp=total, held_count=len(held), fmt='csv',
    )
    return buf.getvalue(), batch


@transaction.atomic
def approve_cycle(cycle: CommissionCycle, user=None) -> CommissionCycle:
    """CFO/finance sign-off: lock the cycle against re-ingest and stamp the approver."""
    from django.utils import timezone
    if cycle.status == CommissionCycle.Status.PAID:
        raise ValueError('Cycle is already marked paid.')
    cycle.status = CommissionCycle.Status.APPROVED
    cycle.approved_by = user
    cycle.approved_at = timezone.now()
    cycle.save(update_fields=['status', 'approved_by', 'approved_at', 'updated_at'])
    return cycle


@transaction.atomic
def reopen_cycle(cycle: CommissionCycle) -> CommissionCycle:
    """Unlock an approved cycle so lines can be re-ingested/corrected.

    A PAID cycle is NOT reopenable — the payout has already gone out, so
    un-approving it here would let the figures drift away from what was paid
    (DeepSeek review 2026-07-27)."""
    if cycle.status == CommissionCycle.Status.PAID:
        raise ValueError('This pay cycle is marked paid and cannot be reopened.')
    cycle.status = CommissionCycle.Status.OPEN
    cycle.approved_by = None
    cycle.approved_at = None
    cycle.save(update_fields=['status', 'approved_by', 'approved_at', 'updated_at'])
    return cycle


def add_source_report(cycle: CommissionCycle, kind: str, text: str, user=None) -> SourceReport:
    """Store a control report (Reports tab). Raw rows kept verbatim."""
    if kind not in SourceReport.Kind.values:
        raise ValueError(f'Unknown report kind: {kind}')
    rows = _rows_from_csv(text)
    return SourceReport.objects.create(
        cycle=cycle, kind=kind, raw_text=text, rows_count=len(rows), uploaded_by=user)


def _parse_policy_report(text: str) -> dict:
    """All-Policies report -> {policyNumber: {'status': str, 'premium': str}}.
    Tolerant of BOTH layouts: the raw export (header row with policyNumber /
    policyStatus / premium in any case) and the 16-col New-Sales template
    order (policy=1, premium=5, status=7)."""
    rows = _rows_from_csv(text)
    if not rows:
        return {}
    ip, istat, iprem = 1, 7, 5
    head = [norm(c).lower().replace(' ', '').replace('_', '') for c in rows[0]]
    data = rows
    if any('policynumber' in h for h in head):
        for i, h in enumerate(head):
            if 'policynumber' in h:
                ip = i
            elif h in ('policystatus', 'status'):
                istat = i
            elif h == 'premium':
                iprem = i
        data = rows[1:]
    out = {}
    for r in data:
        pol = norm(r[ip]) if len(r) > ip else ''
        if not pol or pol.lower() in ('policynumber', 'policy number'):
            continue
        out[pol.upper()] = {
            'status': norm(r[istat]) if len(r) > istat else '',
            'premium': norm(r[iprem]) if len(r) > iprem else '',
        }
    return out


def policy_status_check(cycle: CommissionCycle) -> dict:
    """Cross-check the cycle's PAYABLE lines against the latest uploaded
    All-Policies report: is the policy in the report, is it still active,
    does the premium agree? Verdicts are flags for finance — the SOP math
    is untouched."""
    from .commission_engine import is_cancelled, num as eng_num
    import math as _math
    rep = SourceReport.objects.filter(
        cycle=cycle, kind=SourceReport.Kind.ALL_POLICIES).order_by('-created_at').first()
    if not rep:
        return {'available': False, 'detail': 'No All Policies Report uploaded for this cycle yet.'}
    book = _parse_policy_report(rep.raw_text)
    lines = (CommissionLine.objects.filter(cycle=cycle, payable=True)
             .exclude(policy_ref='').select_related('agent'))
    ok = cancelled = missing = premium_diff = 0
    issues = []
    for l in lines:
        info = book.get((l.policy_ref or '').upper())
        if info is None:
            missing += 1
            verdict, note = 'not_in_report', 'not found in the All Policies Report'
        elif is_cancelled(info['status']):
            cancelled += 1
            verdict, note = 'cancelled', f"report says: {info['status'] or 'cancelled'}"
        else:
            rp = eng_num(info['premium'])
            lb = float(l.basis or 0)
            if l.stream.startswith(('new_sales', 'conversion')) and not _math.isnan(rp) and lb and abs(rp - lb) > 0.01:
                premium_diff += 1
                verdict, note = 'premium_differs', f'submitted {lb:.2f}, report {rp:.2f}'
            else:
                ok += 1
                continue
        if len(issues) < 200:
            issues.append({'policy': l.policy_ref, 'agent': l.agent.name,
                           'stream': l.stream, 'verdict': verdict, 'note': note})
    return {
        'available': True, 'report_date': rep.created_at, 'report_rows': rep.rows_count,
        'checked': ok + cancelled + missing + premium_diff,
        'ok': ok, 'cancelled': cancelled, 'not_in_report': missing,
        'premium_differs': premium_diff, 'issues': issues,
    }
