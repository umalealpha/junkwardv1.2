"""agent_portal/graphite_check.py — live verification of commission lines against
Graphite (CFO 2026-07-07 "bridge plan": the uploads are a bridge; the real check
is Omni confirming each policy straight against the live system).

Deterministic, fixed SQL — NOT the LLM/agentic path in aware.engine, and NOT
gated by the /aware chat whitelist. It reuses only aware.engine.run_select (the
guarded, read-only, LIMIT-ed replica line). For a cycle's payable lines it
confirms, per policy number:
  * the policy exists in Graphite        -> else not_found
  * it is Activated (policies.status=1)  -> else mismatch "not active"
  * the premium matches (+/-0.01)         -> premium streams only
  * KYC is approved (kyc_compliance=1)     -> else mismatch "KYC not approved"
  * conversion streams started 3+ months  -> else mismatch "inside 3 months"

Frozen field semantics (verified on the live replica 2026-07-07):
  policies.status: 1=active, 2=inactive, 0=not-activated.
  v_policy_kyc_status.kyc_compliance: 1=approved, 0=unchecked/recheck,
    2=unapprove/rejected. policies.premium = the monthly premium.

Honest scope: the UNICOIN book in Graphite is the MIS retail book (~206k rows);
Liberty/UNI policies are almost absent (~21). A Liberty policy number simply
returns not_found — the UI shows that plainly, never a fake tick.

Robustness: the replica being unreachable NEVER raises to the caller — every
line is marked 'unavailable'. Money truth stays with the engine's own
payable/reason; this only annotates alongside it.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from django.utils import timezone

from .models import CommissionLine, VerificationRun
from .service import _conversion_cutoff

log = logging.getLogger('agent_portal.graphite_check')

GS = CommissionLine.GraphiteStatus
_PREMIUM_STREAMS = {'new_sales_mis', 'new_sales_liberty',
                    'conversion', 'conversion_mis', 'conversion_liberty'}
_CONVERSION_STREAMS = {'conversion', 'conversion_mis', 'conversion_liberty'}
_BATCH = 200                       # <= aware.engine HARD_LIMIT (200)
_PREMIUM_TOL = Decimal('0.01')
_JUNK_REFS = {'', '0', '1', 'na', 'n/a', '-'}


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _dec(v) -> Decimal:
    try:
        return Decimal(str(v if v is not None else '0'))
    except Exception:
        return Decimal('0')


def _fetch(refs):
    """Pull policies + KYC for a set of refs from the Graphite replica.
    Returns (pol_by_ref, kyc_by_ref). Raises on replica failure (the caller
    then marks every line unavailable)."""
    from aware.engine import run_select
    pol, kyc = {}, {}
    for chunk in _chunks(refs, _BATCH):
        inlist = ','.join("'%s'" % str(r).replace("'", "") for r in chunk)
        _, prows = run_select(
            'SELECT policyNumber, status, premium, created_at '
            'FROM policies WHERE policyNumber IN (%s) LIMIT %d' % (inlist, _BATCH))
        for r in prows:
            pol[str(r['policyNumber'])] = r
        _, krows = run_select(
            'SELECT policyNumber, kyc_compliance '
            'FROM v_policy_kyc_status WHERE policyNumber IN (%s) LIMIT %d' % (inlist, _BATCH))
        for r in krows:
            kyc[str(r['policyNumber'])] = r['kyc_compliance']
    return pol, kyc


def _evaluate(line, pol_row, kyc_compliance, cutoff):
    """Return (status, note) for one line given its Graphite data."""
    if pol_row is None:
        return GS.NOT_FOUND, 'not found in Graphite'
    notes = []
    if pol_row.get('status') != 1:
        notes.append('policy not active in Graphite')
    if line.stream in _PREMIUM_STREAMS:
        gp, sp = _dec(pol_row.get('premium')), _dec(line.basis)
        if abs(gp - sp) > _PREMIUM_TOL:
            notes.append(f'premium differs: submitted {sp}, Graphite {gp}')
    if kyc_compliance != 1:
        notes.append('KYC not approved in Graphite')
    if line.stream in _CONVERSION_STREAMS:
        created = pol_row.get('created_at')
        if created is not None and cutoff is not None:
            cdate = created.date() if hasattr(created, 'date') else created
            if cdate > cutoff:
                notes.append(f'inception {cdate} - inside 3 months')
    if notes:
        return GS.MISMATCH, '; '.join(notes)[:200]
    return GS.OK, 'confirmed active + KYC approved'


def verify_cycle(cycle, user=None) -> VerificationRun:
    """Verify the cycle's PAYABLE lines against Graphite; stamp each line and
    record a VerificationRun. Never raises on replica failure."""
    lines = list(CommissionLine.objects.filter(cycle=cycle, payable=True)
                 .select_related('agent'))
    cutoff = _conversion_cutoff(cycle)

    # Bucket by ref; lines with no usable policy number are 'skipped'.
    by_ref: dict[str, list] = {}
    to_skip = []
    for l in lines:
        ref = (l.policy_ref or '').strip()
        if ref.lower() in _JUNK_REFS:
            to_skip.append(l)
        else:
            by_ref.setdefault(ref, []).append(l)

    now = timezone.now()
    tally = {GS.OK: 0, GS.MISMATCH: 0, GS.NOT_FOUND: 0, GS.SKIPPED: 0, GS.UNAVAILABLE: 0}
    unavailable = False
    pol, kyc = {}, {}
    if by_ref:
        try:
            pol, kyc = _fetch(list(by_ref.keys()))
        except Exception as e:  # replica down / query rejected — never crash
            unavailable = True
            log.warning('graphite verify unavailable: %r', e)

    updated = []
    for l in to_skip:
        l.graphite_status, l.graphite_note = GS.SKIPPED, 'no policy number to check'
        l.graphite_checked_at = now
        tally[GS.SKIPPED] += 1
        updated.append(l)
    for ref, ls in by_ref.items():
        for l in ls:
            if unavailable:
                st, note = GS.UNAVAILABLE, 'Graphite replica not reachable'
            else:
                st, note = _evaluate(l, pol.get(ref), kyc.get(ref), cutoff)
            l.graphite_status, l.graphite_note, l.graphite_checked_at = st, note, now
            tally[st] += 1
            updated.append(l)

    if updated:
        CommissionLine.objects.bulk_update(
            updated, ['graphite_status', 'graphite_note', 'graphite_checked_at'])

    return VerificationRun.objects.create(
        cycle=cycle, run_by=user,
        lines_checked=len(updated),
        ok_count=tally[GS.OK], mismatch_count=tally[GS.MISMATCH],
        not_found_count=tally[GS.NOT_FOUND], skipped_count=tally[GS.SKIPPED],
        unavailable=unavailable,
    )
