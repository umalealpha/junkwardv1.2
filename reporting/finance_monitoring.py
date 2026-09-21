"""
reporting/finance_monitoring.py

The six Finance monitoring reports Keetile Mokhendo asked for (handover note,
17-Aug-2026). Read-only against the Graphite read replica — no journal, no money
movement, no policy change. Every builder returns {rows, summary, meta} so it
slots into reporting/views.py beside the other builders.

  1. RealPay / DPO vs Graphite   — build_collections_vs_graphite
  2. MIS refunds posted vs not   — build_refunds_posted_vs_unposted
  3. Policy status integrity     — build_policy_status_integrity
  4. RealPay contract expiry     — build_contract_expiry
  5. Failed debit orders         — build_failed_debits
  6. Payments vs bank            — build_payments_vs_bank

THE BLOCKER IN THE HANDOVER NOTE IS STALE. The note says our RealPay data
"stops at 30 June" and that nothing can go live until the live connection is
handed over. It is already live: `realpay_contract_installments` holds 6.64m
rows and its newest `created_at` was minutes old when this was written
(2026-08-17 18:08). Graphite has been ingesting RealPay all along. So five of
these six run today off the replica on a schedule, with no file and no new
credential. Report 1 keeps the upload path as well, because a file exported from
RealPay's own side is an INDEPENDENT check on our copy — that is the point of a
reconciliation, and it would be lost if we only ever read our own database.

STATUS CODES ARE GRAPHITE'S, NOT OURS. Read from Graphite's source rather than
inferred, because guessing here would mislabel real money:

  policies.status              0 = lapsed/inactive · 1 = ACTIVE · 2 = CANCELLED
    (frontend/src/pages/Policies/PolicyDetailPage.tsx: `status === 1` is
     isActive; `status === 2` renders the cancellation reason and offers
     reinstate.)

  realpay_contract_installments.InstalmentStatus
    S = Success · F = Failed · W = Processing · R = Retry · A = Active
    (scheduled, not yet due) · I = Cancelled · E = Error
    (backend/app/Http/Livewire/Policy/Realpay/RealpayTransactions.php)

  realpay_client_contracts.status   1 = live mandate · 0 = ended

THE JOIN. RealPay's ClientNumber is the Graphite policyNumber; its
ContractNumber is "{policy id}/{sequence}", RealPay's own reference. Graphite
itself joins `Policy::where('policyNumber', $row->clientNumber)`. Getting this
backwards made 95% of a real file look like debits against unknown policies —
see the note in realpay/recon.py.

PII. Policy numbers and customer names are PII under AD-POL-AI-GOV-001. They are
returned to a signed-in, financially-permissioned user for the on-screen report
and nothing else: they are never put in an AI prompt, never logged, and the
scheduled emails carry counts and totals with a link, not a customer list.
"""

from __future__ import annotations

import datetime as _dt
import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional
from django.utils import timezone

log = logging.getLogger(__name__)

ZERO = Decimal('0.00')

#: Said out loud whenever a row cap bites — see _truncated().
CAP_SENTENCE = (
    'The row cap of {limit} was reached, so these totals cover only the first '
    '{limit} rows. Narrow the dates, or download the file for all of them.')

#: policies.status, per Graphite's own UI.
POLICY_ACTIVE, POLICY_LAPSED, POLICY_CANCELLED = '1', '0', '2'
POLICY_STATUS_LABEL = {
    POLICY_LAPSED:    'Lapsed / inactive',
    POLICY_ACTIVE:    'Active',
    POLICY_CANCELLED: 'Cancelled',
}

#: realpay_contract_installments.InstalmentStatus, per Graphite's own UI.
INSTALMENT_STATUS_LABEL = {
    'S': 'Success', 'F': 'Failed', 'W': 'Processing', 'R': 'Retry',
    'A': 'Scheduled', 'I': 'Cancelled', 'E': 'Error',
}
#: What counts as a debit that did not collect. 'E' is included: an error is a
#: failure to collect from Finance's point of view even though RealPay
#: distinguishes it, and leaving it out would understate the chase list.
FAILED_STATUSES = ('F', 'E')


def _d(v) -> Decimal:
    """A replica value as a Decimal, never raising on a NULL or a blank."""
    if v in (None, ''):
        return ZERO
    try:
        return Decimal(str(v))
    except (ArithmeticError, ValueError):
        return ZERO


def _f(v) -> float:
    return float(_d(v))


def _today() -> _dt.date:
    return timezone.localdate()


def _window(date_from: Optional[_dt.date], date_to: Optional[_dt.date],
            default_days: int) -> tuple[_dt.date, _dt.date]:
    to = date_to or _today()
    frm = date_from or (to - _dt.timedelta(days=default_days))
    return frm, to


class ReplicaUnavailable(RuntimeError):
    """The Graphite replica could not be read.

    Raised rather than returning empty rows. Every one of these reports answers
    a question of the form "what is wrong out there", so an empty result reads
    as ALL CLEAR. A silent empty report on a dead connection is the failure mode
    most likely to let a real problem run for a fortnight unnoticed.
    """


def _q(sql: str, args=None, limit: int = 5000) -> List[dict]:
    from integrations import graphite_ro
    if not graphite_ro.is_configured():
        raise ReplicaUnavailable('The Graphite read connection is not configured.')
    try:
        return graphite_ro.query(sql, args, limit=limit)
    except Exception as exc:  # noqa: BLE001 — surfaced to the caller as unavailable
        log.exception('finance monitoring query failed')
        raise ReplicaUnavailable(
            'Graphite could not be read, so this report has not been run.') from exc


def _truncated(rows: List[dict], limit: int) -> bool:
    """Did the row cap bite?

    It matters because every summary on this page is totalled over the rows we
    actually read. Report 1 finds 5,824 exception policies on real data against
    a default cap of 1,000, so an unflagged cap would print a confidently wrong
    smaller number as the headline. A partial answer presented as a whole one is
    the same class of error as an empty report reading as all-clear.
    """
    return len(rows) >= limit


def _cap_note(rows: List[dict], limit: int) -> Dict[str, Any]:
    """Summary/meta additions that say plainly when the cap bit."""
    if not _truncated(rows, limit):
        return {}
    return {'truncated': 1, 'row_cap': limit}


def _meta(title: str, frm=None, to=None, **extra) -> Dict[str, Any]:
    m = {
        'title': title,
        'source': 'Graphite read replica (read-only)',
        'generated_at': _dt.datetime.now().isoformat(timespec='seconds'),
    }
    if frm:
        m['date_from'] = frm.isoformat()
    if to:
        m['date_to'] = to.isoformat()
    m.update(extra)
    return m


# ---------------------------------------------------------------- report 1 ---

def build_collections_vs_graphite(*, date_from=None, date_to=None,
                                  limit: int = 1000) -> Dict[str, Any]:
    """RealPay collections against the policy they were taken for.

    The money question is not "did the file total tie" — it is "did we debit
    somebody we should not have". A debit against a CANCELLED policy is money we
    have to give back and a complaint waiting to happen; a debit against a
    lapsed one is nearly as bad. Both are counted separately here because they
    have different owners and different urgency.

    Measured over 1 June - 17 Aug 2026 on Keetile's own export: P278,116.82
    taken on 544 cancelled policies and P757,056.57 on 5,280 lapsed ones.
    """
    frm, to = _window(date_from, date_to, 30)
    rows = _q(
        "SELECT p.policyNumber AS policy_number, p.status AS policy_status, "
        "       COALESCE(p.premium,0) AS premium, "
        "       COUNT(*) AS debits, "
        "       SUM(COALESCE(i.InstalmentAmount,0)) AS collected, "
        "       MAX(i.InstalmentActionDate) AS last_debit "
        "FROM realpay_contract_installments i "
        "JOIN policies p ON p.policyNumber = i.clientNumber "
        "WHERE i.InstalmentStatus = 'S' "
        "  AND i.InstalmentActionDate >= %s AND i.InstalmentActionDate < %s "
        "  AND p.status <> %s "
        "GROUP BY p.policyNumber, p.status, p.premium "
        "ORDER BY collected DESC",
        (frm, to + _dt.timedelta(days=1), POLICY_ACTIVE), limit=limit)

    out, by_status = [], {}
    for r in rows:
        st = str(r['policy_status'] or '')
        amt = _d(r['collected'])
        b = by_status.setdefault(st, {'policies': 0, 'amount': ZERO})
        b['policies'] += 1
        b['amount'] += amt
        out.append({
            'policy_number': r['policy_number'],
            'policy_status': POLICY_STATUS_LABEL.get(st, f'Unknown ({st})'),
            'debits': r['debits'],
            'collected': _f(amt),
            'premium': _f(r['premium']),
            'last_debit': str(r['last_debit'] or ''),
            'severity': 'critical' if st == POLICY_CANCELLED else 'warning',
        })

    return {
        'rows': out,
        'summary': {
            'policies_debited_not_active': len(out),
            'total_collected': _f(sum((_d(r['collected']) for r in rows), ZERO)),
            'cancelled_policies': by_status.get(POLICY_CANCELLED, {}).get('policies', 0),
            'cancelled_amount': _f(by_status.get(POLICY_CANCELLED, {}).get('amount', ZERO)),
            'lapsed_policies': by_status.get(POLICY_LAPSED, {}).get('policies', 0),
            'lapsed_amount': _f(by_status.get(POLICY_LAPSED, {}).get('amount', ZERO)),
            **_cap_note(rows, limit),
        },
        'meta': _meta('RealPay collections on policies that are not active', frm, to,
                      note='Successful debits only. Active policies are excluded — '
                           'they are the normal case.'
                           + (' ' + CAP_SENTENCE.format(limit=limit)
                              if _truncated(rows, limit) else '')),
    }


# ---------------------------------------------------------------- report 2 ---

def build_refunds_posted_vs_unposted(*, date_from=None, date_to=None,
                                     limit: int = 1000) -> Dict[str, Any]:
    """Refunds Graphite has recorded, against refunds Omni has raised for payment.

    "Posted" means Omni has a premium-refund payment request for it. Anything
    Graphite has refunded with nothing on our side is money owed to a customer
    that nobody is currently getting ready to pay.
    """
    frm, to = _window(date_from, date_to, 7)
    rows = _q(
        "SELECT t.policyNumber AS policy_number, t.referenceNumber AS reference, "
        "       COALESCE(t.refunded_amount,0) AS refunded_amount, "
        "       t.last_refund_at AS refunded_at, t.reason AS reason "
        "FROM payment_transactions t "
        "WHERE COALESCE(t.refunded_amount,0) > 0 "
        "  AND t.last_refund_at >= %s AND t.last_refund_at < %s "
        "ORDER BY t.last_refund_at DESC",
        (frm, to + _dt.timedelta(days=1)), limit=limit)

    posted = _omni_refunds_by_policy([r['policy_number'] for r in rows], frm)

    out, unposted_amt, posted_amt = [], ZERO, ZERO
    for r in rows:
        amt = _d(r['refunded_amount'])
        is_posted = _matches_omni_refund(posted.get(r['policy_number'] or ''), amt)
        if is_posted:
            posted_amt += amt
        else:
            unposted_amt += amt
        out.append({
            'policy_number': r['policy_number'],
            'reference': r['reference'],
            'amount': _f(amt),
            'refunded_at': str(r['refunded_at'] or ''),
            'reason': (r['reason'] or '')[:200],
            'posted_in_omni': is_posted,
            'severity': 'ok' if is_posted else 'warning',
        })

    return {
        'rows': out,
        'summary': {
            'refunds': len(out),
            'posted': sum(1 for r in out if r['posted_in_omni']),
            'unposted': sum(1 for r in out if not r['posted_in_omni']),
            'posted_amount': _f(posted_amt),
            'unposted_amount': _f(unposted_amt),
            **_cap_note(rows, limit),
        },
        'meta': _meta('Refunds in Graphite vs payment requests in Omni', frm, to,
                      cap_note=(CAP_SENTENCE.format(limit=limit)
                                if _truncated(rows, limit) else ''),
                      note='Matched on policy number and amount. Graphite\'s '
                           'refund_requests table is not on the read replica, '
                           'so there is no shared reference to match on — treat '
                           'a single unposted line as a question, not a fact.'),
    }


def _omni_refunds_by_policy(policy_numbers: List[str],
                            since: Optional[_dt.date] = None) -> Dict[str, List[Decimal]]:
    """Omni's own refund amounts, by policy number, within the report window.

    Matched on policy number and amount rather than on a shared key, because
    there is no shared key available. `CustomerRefund.graphite_ref` holds a
    `refund_requests` id, and `refund_requests` is not one of the tables exposed
    on the read replica — the Graphite side of this report has to come from
    `payment_transactions.refunded_amount`, which carries a different reference.
    Policy + amount is therefore the honest join, and the report says so.

    Bounded by date on purpose. Refund amounts are premium-sized and recur, so
    an unbounded lookup would let a P500 refund posted in June mark an unrelated
    P500 refund in August as already handled — a false all-clear on money owed
    to a customer, in the exact direction this report exists to catch. The
    window is widened a fortnight to allow for a refund raised in Omni slightly
    ahead of Graphite recording it.
    """
    nums = {p for p in policy_numbers if p}
    if not nums:
        return {}
    try:
        from customer_refunds.models import CustomerRefund
    except Exception:  # noqa: BLE001 — module optional in some deployments
        log.warning('customer_refunds not installed; refunds all reported unposted')
        return {}
    try:
        qs = CustomerRefund.objects.filter(policy_number__in=nums)
        if since:
            qs = qs.filter(created_at__gte=since - _dt.timedelta(days=14))
        out: Dict[str, List[Decimal]] = {}
        for pn, amt in qs.values_list('policy_number', 'refund_amount'):
            out.setdefault(pn, []).append(_d(amt))
        return out
    except Exception:  # noqa: BLE001 — a schema drift must not kill the report
        log.exception('customer refund lookup failed')
        return {}


def _matches_omni_refund(amounts: Optional[List[Decimal]], amount: Decimal) -> bool:
    """Is there an unclaimed Omni refund on this policy for this amount?

    CONSUMES the match. One Omni refund can only account for one Graphite
    refund: without this, a customer refunded the same amount twice would show
    both as posted on the strength of a single Omni record, and the second —
    genuinely unpaid — would never appear on the chase list.
    """
    if not amounts:
        return False
    for i, a in enumerate(amounts):
        if abs(a - amount) <= Decimal('0.01'):
            amounts.pop(i)
            return True
    return False


# ---------------------------------------------------------------- report 3 ---

def build_policy_status_integrity(*, limit: int = 1000) -> Dict[str, Any]:
    """Policies whose status contradicts what is happening to them.

    Fortnightly to the Unicoin team. Three contradictions, each a real one:

      * cancelled or lapsed, but a live RealPay mandate is still attached — the
        debit will go out again next cycle;
      * active, but the cover period ended — we are on risk for something that
        expired;
      * active with a live mandate but no premium on the policy — the debit has
        no amount to check against.
    """
    today = _today()
    findings: List[dict] = []

    for r in _q(
        "SELECT p.policyNumber AS policy_number, p.status AS policy_status, "
        "       p.term_end_date AS term_end_date "
        "FROM realpay_client_contracts rc "
        "JOIN policies p ON p.id = rc.policy_id "
        "WHERE rc.status = 1 AND p.status <> %s "
        "ORDER BY p.id DESC", (POLICY_ACTIVE,), limit=limit
    ):
        st = str(r['policy_status'] or '')
        findings.append({
            'policy_number': r['policy_number'],
            'issue': f'{POLICY_STATUS_LABEL.get(st, st)} policy still has a live debit mandate',
            'policy_status': POLICY_STATUS_LABEL.get(st, st),
            'term_end_date': str(r['term_end_date'] or ''),
            'severity': 'critical' if st == POLICY_CANCELLED else 'warning',
        })

    for r in _q(
        "SELECT policyNumber AS policy_number, term_end_date "
        "FROM policies "
        "WHERE status = %s AND term_end_date IS NOT NULL "
        "  AND term_end_date > '2000-01-01' AND term_end_date < %s "
        "ORDER BY term_end_date DESC", (POLICY_ACTIVE, today), limit=limit
    ):
        findings.append({
            'policy_number': r['policy_number'],
            'issue': 'Active policy whose cover period has already ended',
            'policy_status': 'Active',
            'term_end_date': str(r['term_end_date'] or ''),
            'severity': 'critical',
        })

    for r in _q(
        "SELECT p.policyNumber AS policy_number, p.term_end_date "
        "FROM realpay_client_contracts rc "
        "JOIN policies p ON p.id = rc.policy_id "
        "WHERE rc.status = 1 AND p.status = %s AND COALESCE(p.premium,0) <= 0 "
        "ORDER BY p.id DESC", (POLICY_ACTIVE,), limit=limit
    ):
        findings.append({
            'policy_number': r['policy_number'],
            'issue': 'Active policy being debited but carrying no premium',
            'policy_status': 'Active',
            'term_end_date': str(r['term_end_date'] or ''),
            'severity': 'warning',
        })

    return {
        'rows': findings[:limit],
        'summary': {
            'findings': len(findings),
            'critical': sum(1 for f in findings if f['severity'] == 'critical'),
            'warning': sum(1 for f in findings if f['severity'] == 'warning'),
        },
        # `term_end_date > '2000-01-01'` is not decoration: the column holds
        # values like 0025-03-14, and without the guard every one of them lands
        # in the report as an expired policy.
        'meta': _meta('Policy status integrity', truncated=len(findings) > limit),
    }


# ---------------------------------------------------------------- report 4 ---

def build_contract_expiry(*, days_ahead: int = 60, limit: int = 1000) -> Dict[str, Any]:
    """Live mandates whose last scheduled debit falls inside the window.

    Monthly to Finance. When the schedule runs out the money stops, quietly, on
    a policy that is still on risk — there is no failure and no alert, which is
    exactly why it needs a report.
    """
    today = _today()
    horizon = today + _dt.timedelta(days=days_ahead)
    # Set difference, not GROUP BY ... HAVING.
    #
    # The obvious query — group every scheduled instalment by policy and keep
    # those whose MAX date falls inside the window — must aggregate all 1.16m
    # 'A' rows before it can discard any, and it timed out against the live
    # replica on the first run. The same answer is two bounded reads: policies
    # with a debit due inside the window, minus policies that also have one
    # beyond it. The difference is a set operation in Python.
    inside = _q(
        "SELECT i.clientNumber AS policy_number, "
        "       MAX(i.InstalmentActionDate) AS last_scheduled, "
        "       COUNT(*) AS scheduled_left "
        "FROM realpay_contract_installments i "
        "WHERE i.InstalmentStatus = 'A' "
        "  AND i.InstalmentActionDate >= %s AND i.InstalmentActionDate < %s "
        "GROUP BY i.clientNumber",
        (today, horizon), limit=20000)
    if not inside:
        return {'rows': [], 'summary': {'contracts_expiring': 0,
                                        'active_policies_affected': 0,
                                        'monthly_premium_at_risk': 0.0,
                                        'days_ahead': days_ahead},
                'meta': _meta('RealPay debit schedules running out', today, horizon)}

    beyond = {r['policy_number'] for r in _q(
        "SELECT DISTINCT i.clientNumber AS policy_number "
        "FROM realpay_contract_installments i "
        "WHERE i.InstalmentStatus = 'A' AND i.InstalmentActionDate >= %s",
        (horizon,), limit=100000)}

    expiring = [r for r in inside if r['policy_number'] not in beyond][:limit]
    rows = _attach_policies(expiring)

    out = []
    for r in rows:
        last = r['last_scheduled']
        st = str(r['policy_status'] or '')
        out.append({
            'policy_number': r['policy_number'],
            'policy_status': POLICY_STATUS_LABEL.get(st, st),
            'last_scheduled_debit': str(last or ''),
            'debits_remaining': r['scheduled_left'],
            'premium': _f(r['premium']),
            # Only an ACTIVE policy running out of schedule is a problem; a
            # cancelled one is supposed to stop.
            'severity': 'warning' if st == POLICY_ACTIVE else 'ok',
        })

    at_risk = [r for r in out if r['severity'] == 'warning']
    return {
        'rows': out,
        'summary': {
            'contracts_expiring': len(out),
            'active_policies_affected': len(at_risk),
            'monthly_premium_at_risk': _f(sum((_d(r['premium']) for r in at_risk), ZERO)),
            'days_ahead': days_ahead,
            **_cap_note(expiring, limit),
        },
        'meta': _meta('RealPay debit schedules running out', today, horizon,
                      note=(CAP_SENTENCE.format(limit=limit)
                            if _truncated(rows, limit) else '')),
    }


def _attach_policies(rows: List[dict]) -> List[dict]:
    """Add each policy's status and premium, by chunked indexed lookup.

    `policies.policyNumber` is unique-indexed, so this is a point lookup per key
    rather than the join that made the aggregate untenable.
    """
    keys = sorted({str(r['policy_number']) for r in rows if r.get('policy_number')})
    found: Dict[str, dict] = {}
    CHUNK = 1000
    for i in range(0, len(keys), CHUNK):
        chunk = keys[i:i + CHUNK]
        ph = ','.join(['%s'] * len(chunk))
        for p in _q("SELECT policyNumber, status, COALESCE(premium,0) AS premium "
                    f"FROM policies WHERE policyNumber IN ({ph})",
                    tuple(chunk), limit=CHUNK):
            found[str(p['policyNumber'])] = p
    out = []
    for r in rows:
        p = found.get(str(r['policy_number'])) or {}
        out.append({**r, 'policy_status': p.get('status', ''),
                    'premium': p.get('premium', 0)})
    return out


# ---------------------------------------------------------------- report 5 ---

def build_failed_debits(*, date_from=None, date_to=None,
                        limit: int = 1000) -> Dict[str, Any]:
    """Debits that did not collect, for same-day chasing.

    Note the wording constraint from the handover note: RealPay only reports on
    its daily batch, so this is a same-day report, NOT a real-time alert. It
    must never be described to a customer as immediate.

    Sized on the replica: 6,200 failures worth P444,035.41 in the 30 days to
    17-Aug-2026.
    """
    from reporting.unicoin_failed_debit_triage import triage_failed_debit, CALL_CENTRE
    frm, to = _window(date_from, date_to, 1)
    ph = ','.join(['%s'] * len(FAILED_STATUSES))
    rows = _q(
        "SELECT i.clientNumber AS policy_number, i.InstalmentStatus AS status, "
        "       COALESCE(i.InstalmentAmount,0) AS amount, "
        "       i.InstalmentActionDate AS action_date, "
        "       COALESCE(i.retry_count,0) AS retry_count, "
        "       i.instalmentResponse AS response, "
        "       p.status AS policy_status "
        "FROM realpay_contract_installments i "
        "LEFT JOIN policies p ON p.policyNumber = i.clientNumber "
        f"WHERE i.InstalmentStatus IN ({ph}) "
        "  AND i.InstalmentActionDate >= %s AND i.InstalmentActionDate < %s "
        "ORDER BY amount DESC",
        tuple(FAILED_STATUSES) + (frm, to + _dt.timedelta(days=1)), limit=limit)

    out = []
    for r in rows:
        st = str(r['policy_status'] or '')
        retries = int(r['retry_count'] or 0)
        # A3 (pack Task 3): a plain-English reason and the worklist it belongs on.
        triage = triage_failed_debit(status=r['status'], retry_count=retries,
                                     response=r.get('response'))
        out.append({
            'policy_number': r['policy_number'],
            'result': INSTALMENT_STATUS_LABEL.get(str(r['status'] or ''), str(r['status'])),
            'amount': _f(r['amount']),
            'action_date': str(r['action_date'] or ''),
            'retry_count': retries,
            'policy_status': POLICY_STATUS_LABEL.get(st, 'Unknown'),
            'reason_plain': triage['reason_plain'],
            'reason_mapped': triage['reason_mapped'],
            'list_bucket': triage['list_bucket'],
            # Already retried and still failing is the one to phone about. The
            # threshold lives once, in the triage helper — severity follows it.
            'severity': 'critical' if triage['list_bucket'] == CALL_CENTRE else 'warning',
        })

    return {
        'rows': out,
        'summary': {
            'failed_debits': len(out),
            'total_amount': _f(sum((_d(r['amount']) for r in rows), ZERO)),
            'repeat_failures': sum(1 for r in out if r['retry_count'] >= 2),
            'on_active_policies': sum(1 for r in out if r['policy_status'] == 'Active'),
            'finance_list': sum(1 for r in out if r['list_bucket'] == 'finance'),
            'call_centre_list': sum(1 for r in out if r['list_bucket'] == 'call_centre'),
            'reasons_unmapped': sum(1 for r in out if not r['reason_mapped']),
            **_cap_note(rows, limit),
        },
        'meta': _meta('Failed debit orders', frm, to,
                      note='RealPay reports on its daily batch, so this is a '
                           'same-day report, not a real-time alert.'
                           + (' ' + CAP_SENTENCE.format(limit=limit)
                              if _truncated(rows, limit) else '')),
    }


# ---------------------------------------------------------------- report 6 ---

def build_payments_vs_bank(*, date_from=None, date_to=None,
                           limit: int = 1000) -> Dict[str, Any]:
    """What Graphite says it received, against what the bank statement shows.

    Friday close of business to Keetile. Compared day by day rather than
    transaction by transaction: a debit-order batch lands in the bank as one
    lump, so matching individual receipts to individual bank lines would report
    a break on every single row.

    `paymentDate` is NOT used for the window. It is a free-text column on the
    replica holding values like '9-12-2023' and empty strings, so filtering on
    it silently drops rows. `created_at` is a real timestamp.
    """
    frm, to = _window(date_from, date_to, 7)
    rows = _q(
        "SELECT DATE(t.created_at) AS day, COUNT(*) AS receipts, "
        "       SUM(COALESCE(t.amount,0)) AS amount "
        "FROM payment_transactions t "
        "WHERE t.created_at >= %s AND t.created_at < %s "
        "  AND COALESCE(t.amount,0) > 0 "
        "GROUP BY DATE(t.created_at) ORDER BY day",
        (frm, to + _dt.timedelta(days=1)), limit=limit)

    bank = _bank_totals_by_day(frm, to)

    out, matched, broken = [], 0, ZERO
    for r in rows:
        day = r['day']
        key = day.isoformat() if hasattr(day, 'isoformat') else str(day)
        g_amt = _d(r['amount'])
        b_amt = bank.get(key)
        gap = None if b_amt is None else (g_amt - b_amt)
        if gap is not None and abs(gap) <= Decimal('0.01'):
            matched += 1
        elif gap is not None:
            broken += abs(gap)
        out.append({
            'day': key,
            'receipts': r['receipts'],
            'graphite_amount': _f(g_amt),
            'bank_amount': None if b_amt is None else _f(b_amt),
            'difference': None if gap is None else _f(gap),
            'severity': ('ok' if gap is not None and abs(gap) <= Decimal('0.01')
                         else 'warning' if gap is not None else 'unknown'),
        })

    return {
        'rows': out,
        'summary': {
            'days': len(out),
            'days_agreeing': matched,
            'days_with_no_bank_data': sum(1 for r in out if r['bank_amount'] is None),
            'total_difference': _f(broken),
            **_cap_note(rows, limit),
        },
        'meta': _meta('Graphite receipts vs bank statement', frm, to,
                      note='Compared by day, because a debit-order batch lands '
                           'in the bank as one lump sum. The bank side counts '
                           'each identical line once: the statement feed '
                           're-imports, and most money-in lines currently have '
                           'a duplicate. Fix the import and this gets simpler.'),
    }


def _bank_totals_by_day(frm: _dt.date, to: _dt.date) -> Dict[str, Decimal]:
    """Money IN per day from the bank statement lines Omni already holds.

    Returns {} when the bank feed is unavailable — and the caller renders those
    days as 'no bank data' rather than as a break, because "we have not loaded
    the statement" and "the bank disagrees" are different answers.
    """
    try:
        from banking.models import BankStatementLine
    except Exception:  # noqa: BLE001 — optional in some deployments
        log.warning('banking not available; payments vs bank shows no bank side')
        return {}
    try:
        # DE-DUPLICATED, and this is not defensive tidiness. Checked on prod
        # 18-Aug-2026: of 9,498 money-in lines, 7,782 are surplus copies and NOT
        # ONE is marked excluded — the daily 06:00 feed re-imports the same
        # statement ([[project_bank_statement_duplicate_imports]]). Summing the
        # raw column inflates the bank side several times over and would print a
        # fake break on every single day of the week.
        rows = (BankStatementLine.objects
                .filter(transaction_date__gte=frm, transaction_date__lte=to,
                        amount__gt=0)
                .exclude(match_status=BankStatementLine.MatchStatus.EXCLUDED)
                # .order_by() FIRST: the model has a default ordering, and
                # Django appends ordering columns to SELECT DISTINCT, which
                # makes every row unique again and silently undoes this.
                .order_by()
                .values_list('transaction_date', 'amount', 'description',
                             'reference')
                .distinct())
        out: Dict[str, Decimal] = {}
        for day, amount, _desc, _ref in rows:
            key = day.isoformat()
            out[key] = out.get(key, ZERO) + _d(amount)
        return out
    except Exception:  # noqa: BLE001 — schema drift must not kill the report
        log.exception('bank statement totals failed')
        return {}


def build_payment_status_summary(*, date_from=None, date_to=None,
                                 limit: int = 20000) -> Dict[str, Any]:
    """Payment status reader (UniCoin debtors pack #4). A read-only tally of how
    RealPay debits landed over the fortnight — how many collected, how many
    failed, how many are still scheduled — as COUNTS and AMOUNTS only, never a
    customer list. Fortnightly to Lindani + Bakang, cc Keetile.

    Same daily-batch caveat as the failed-debit report: RealPay reports on a
    daily batch, so the most recent day may still be filling in.
    """
    frm, to = _window(date_from, date_to, 14)
    rows = _q(
        "SELECT i.InstalmentStatus AS status, COUNT(*) AS n, "
        "       SUM(COALESCE(i.InstalmentAmount,0)) AS amount "
        "FROM realpay_contract_installments i "
        "WHERE i.InstalmentActionDate >= %s AND i.InstalmentActionDate < %s "
        "GROUP BY i.InstalmentStatus ORDER BY n DESC",
        (frm, to + _dt.timedelta(days=1)), limit=limit)

    out, total_n, failed_n, collected = [], 0, 0, ZERO
    for r in rows:
        st = str(r['status'] or '')
        n = int(r['n'] or 0)
        amt = _d(r['amount'])
        total_n += n
        if st == 'S':
            collected += amt
        if st in FAILED_STATUSES:
            failed_n += n
        out.append({
            'status': INSTALMENT_STATUS_LABEL.get(st, st or 'Unknown'),
            'count': n,
            'amount': _f(amt),
        })

    return {
        'rows': out,
        'summary': {
            'debits_seen': total_n,
            'amount_collected': _f(collected),
            'failed_count': failed_n,
            **_cap_note(rows, limit),
        },
        'meta': _meta('RealPay payment status', frm, to,
                      note='Counts and amounts by status over the fortnight. '
                           'RealPay reports on a daily batch, so the latest day '
                           'may still be filling in.'),
    }


# ------------------------------------------------------------------ registry -

#: Everything the API, the scheduler and the screen need to know about a report,
#: in one place — so adding a seventh is one entry, not four edits.
REPORTS = {
    'collections-vs-graphite': {
        'title': 'RealPay collections vs Graphite',
        'builder': build_collections_vs_graphite,
        'cadence': 'weekly',
        'owner': 'kmokhendo@alphadirect.co.bw',
    },
    'refunds-posted': {
        'title': 'MIS refunds posted vs unposted',
        'builder': build_refunds_posted_vs_unposted,
        'cadence': 'friday-1630',
        'owner': 'kmokhendo@alphadirect.co.bw',
    },
    'policy-status-integrity': {
        'title': 'Policy status integrity',
        'builder': build_policy_status_integrity,
        'cadence': 'fortnightly',
        'owner': 'kmokhendo@alphadirect.co.bw',
    },
    'contract-expiry': {
        'title': 'RealPay contract expiry',
        'builder': build_contract_expiry,
        'cadence': 'monthly',
        'owner': 'kmokhendo@alphadirect.co.bw',
    },
    'failed-debits': {
        'title': 'Failed debit orders',
        'builder': build_failed_debits,
        'cadence': 'daily',
        'owner': 'kmokhendo@alphadirect.co.bw',
    },
    'payment-status': {
        'title': 'RealPay payment status',
        'builder': build_payment_status_summary,
        'cadence': 'fortnightly',
        'owner': 'kmokhendo@alphadirect.co.bw',
    },
    'payments-vs-bank': {
        'title': 'Payment transactions vs bank',
        'builder': build_payments_vs_bank,
        'cadence': 'friday-cob',
        'owner': 'kmokhendo@alphadirect.co.bw',
    },
}
