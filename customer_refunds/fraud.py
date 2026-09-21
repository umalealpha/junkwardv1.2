"""
customer_refunds/fraud.py — fraud / abuse signals on customer refunds.

The refund process pays money OUT, so it is a theft target — internal (staff /
agent collusion, control bypass) and external (fake cancellations, account
swaps, cooling-off recycling). Each signal is computed over the refund + its
siblings + the Graphite payment feed; `scan_refund` runs them all and returns
flags + a score.

Flagship: the SAME bank account paid out to DIFFERENT customer names — matched
on `account_fingerprint` (keyed-HMAC blind index) so it works without ever
comparing the account number in clear.

Signals run across ALL segments on purpose (an account reused across MIS and
Commercial is stronger); results are shown only to users authorised for the
refund's segment (enforced in the views). Every Graphite-backed check is
guarded — a missing/empty feed downgrades or skips, never crashes.

Signal set distilled from a Fable-5 fraud-analytics review (2026-07-24).
Deferred signals that need fields we don't store yet are listed at the bottom.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

SEVERITY_SCORE = {'CRITICAL': 25, 'HIGH': 10, 'MEDIUM': 4, 'LOW': 1}
HARD_HOLD_SCORE = 25          # ≥ this = block approval (a CRITICAL also blocks)
VELOCITY_WINDOW_DAYS = 30
AGENT_CONCENTRATION = 12
ACCOUNT_VELOCITY = 3
STRUCTURING_BAND = Decimal('0.90')   # "just under" = ≥90% of a soft cap
AMOUNT_EPSILON = Decimal('1.00')


def _norm(s: str) -> str:
    return ' '.join((s or '').lower().split())


def _graphite_premium_stats(policy_number: str):
    """(paid_in, refunded_out, True) from the Graphite feed, or 'error' to skip,
    or None when the feed HAS data but nothing for this policy. Guarded.

    Only SUCCESS, non-reversed rows count as money (house rule: balance =
    Pay − Reverse; failed/reversed debit orders are NOT premiums paid).
    """
    try:
        from django.db.models import Sum
        from integrations.models import GraphitePaymentTransaction as G
        # If the mirror is empty/unsynced globally, we can't judge — skip, never
        # fire no_premium_history on every refund (Fable fix 1).
        if not G.objects.exists():
            return 'error'
        rows = G.objects.filter(policy_number=policy_number,
                                status_norm='success', is_reverse=False)
        if not rows.exists():
            return None  # feed populated but nothing real for this policy
        paid_in = rows.filter(is_refund=False).aggregate(s=Sum('amount'))['s'] or Decimal('0')
        refunded = rows.filter(is_refund=True).aggregate(s=Sum('amount'))['s'] or Decimal('0')
        return (Decimal(paid_in), Decimal(refunded), True)
    except Exception:  # noqa: BLE001 — feed absent/misconfigured → skip cleanly
        return 'error'


def scan_refund(refund) -> dict:
    """Return {'flags': [{severity, code, detail}], 'score': int}."""
    from .models import CustomerRefund

    flags = []

    def add(sev, code, detail):
        flags.append({'severity': sev, 'code': code, 'detail': detail})

    now = timezone.now()
    since = now - timedelta(days=VELOCITY_WINDOW_DAYS)
    base = (CustomerRefund.objects.exclude(pk=refund.pk)
            if refund.pk else CustomerRefund.objects.all())
    amt = Decimal(str(refund.refund_amount or 0))

    # ── 1. FLAGSHIP — same account, different customer names ─────────────────
    fp = refund.account_fingerprint
    if fp:
        same_acct = base.filter(account_fingerprint=fp)
        me = _norm(refund.customer_name)
        other_names = {_norm(o.customer_name) for o in same_acct
                       if _norm(o.customer_name) and _norm(o.customer_name) != me}
        if other_names:
            add('CRITICAL', 'account_shared_diff_names',
                f'Account …{refund.account_last4} refunded under '
                f'{len(other_names) + 1} different customer names.')
        vel = same_acct.filter(created_at__gte=since).count()
        if vel >= ACCOUNT_VELOCITY:
            add('HIGH', 'account_velocity',
                f'Account …{refund.account_last4} on {vel + 1} refunds in '
                f'{VELOCITY_WINDOW_DAYS} days.')

    # ── 2. Premium invariant (Graphite feed) ─────────────────────────────────
    stats = _graphite_premium_stats(refund.policy_number) if refund.policy_number else None
    if stats == 'error':
        pass  # feed unavailable — skip silently
    elif stats is None:
        # no rows at all for this policy in the feed
        add('CRITICAL', 'no_premium_history',
            f'Policy {refund.policy_number} has no premium payments in the '
            'Graphite feed — refunding a policy that never paid in.')
    else:
        paid_in, refunded, _ = stats
        # can't refund more than net paid in (tolerate small epsilon)
        if amt > (paid_in - refunded + AMOUNT_EPSILON):
            add('CRITICAL', 'refund_exceeds_premiums_paid',
                f'Refund {amt} exceeds net premiums paid ({paid_in - refunded}) '
                f'on policy {refund.policy_number}.')
        # 5. cross-channel double refund — Graphite already refunded ~this
        if refunded >= (amt - AMOUNT_EPSILON) and refunded > 0:
            add('HIGH', 'duplicate_payout_cross_channel',
                f'Graphite already shows {refunded} refunded on policy '
                f'{refund.policy_number} — possible double payout.')

    # ── 3. Duplicate policy in this module ───────────────────────────────────
    if refund.policy_number:
        dup = base.filter(policy_number=refund.policy_number)
        if dup.exists():
            add('HIGH', 'duplicate_policy_refund',
                f'Policy {refund.policy_number} has {dup.count() + 1} refunds here.')

    # ── 4. Controls-bypass tripwire (this record's own state) ────────────────
    S = CustomerRefund.Status
    bypass = []
    if refund.paid_at and not refund.finance_approved_by_id:
        bypass.append('paid with no approver')
    if refund.paid_at and refund.finance_approved_at and refund.paid_at < refund.finance_approved_at:
        bypass.append('paid before approval')
    if refund.status in (S.APPROVED, S.FNB_LOADED, S.PAID) and not refund.ai_greenlight:
        bypass.append('progressed without AI green light')
    if refund.status == S.PAID and not (refund.payment_id or refund.fnb_batch_id):
        bypass.append('paid with no payment/FNB batch')
    if bypass:
        add('CRITICAL', 'paid_without_controls',
            'Control bypass: ' + '; '.join(bypass) + '.')

    # ── 7. Structuring — split just under a soft cap ─────────────────────────
    if fp or refund.policy_number:
        peers = base.filter(created_at__gte=since).filter(
            models_q(refund))  # same account OR same policy
        if peers.count() >= 1:
            total = amt + sum(Decimal(str(p.refund_amount or 0)) for p in peers)
            cap = _soft_cap(refund.segment)
            if cap and amt < cap and total > cap:
                add('HIGH', 'structuring_below_threshold',
                    f'{peers.count() + 1} refunds on the same account/policy in '
                    f'{VELOCITY_WINDOW_DAYS} days sum to {total} (each under {cap}).')

    # ── 4b. Agent concentration ──────────────────────────────────────────────
    if refund.agent_name:
        ac = base.filter(agent_name=refund.agent_name, created_at__gte=since).count()
        if ac >= AGENT_CONCENTRATION:
            add('MEDIUM', 'agent_concentration',
                f'Agent "{refund.agent_name}" on {ac + 1} refunds in '
                f'{VELOCITY_WINDOW_DAYS} days.')

    # ── 10. Serial refunder (same customer, many policies) ───────────────────
    me = _norm(refund.customer_name)
    if me:
        # Pull only 3 small columns (not full model rows) — this runs inside a
        # select_for_update on approve, so keep it light (Fable fix 7).
        rows = base.values_list('customer_name', 'policy_number', 'segment')
        mine = [(pol, seg) for (nm, pol, seg) in rows if _norm(nm) == me]
        if len(mine) >= 2:
            policies = {pol for pol, _ in mine} | {refund.policy_number}
            if len(policies) >= 3:
                segs = {seg for _, seg in mine} | {refund.segment}
                sev = 'HIGH' if len(segs) >= 2 else 'MEDIUM'
                add(sev, 'serial_refunder_customer',
                    f'Customer "{refund.customer_name}" has {len(mine) + 1} refunds '
                    f'across {len(policies)} policies'
                    + (' in multiple segments.' if len(segs) >= 2 else '.'))

    # ── 12. Anomalous speed / off-hours approval ─────────────────────────────
    if refund.finance_approved_at and refund.created_at:
        delta = (refund.finance_approved_at - refund.created_at).total_seconds()
        if 0 <= delta < 600:
            add('MEDIUM', 'anomalous_speed',
                'Approved within 10 minutes of receipt.')
        # Local Gaborone time (UTC+2) — not UTC (Fable fix 8).
        t = timezone.localtime(refund.finance_approved_at)
        if t.hour < 7 or t.hour >= 18 or t.weekday() >= 5:
            add('MEDIUM', 'off_hours_approval',
                'Approved outside working hours / on a weekend.')

    score = sum(SEVERITY_SCORE.get(f['severity'], 0) for f in flags)
    return {'flags': flags, 'score': score}


def _soft_cap(segment) -> Decimal | None:
    """Per-segment 'just under' cap for structuring detection. Tunable."""
    from django.conf import settings
    caps = getattr(settings, 'REFUND_STRUCTURING_CAPS',
                   {'mis': '2000', 'domestic': '5000', 'commercial': '50000'})
    v = caps.get(segment)
    return Decimal(str(v)) if v else None


def models_q(refund):
    """Q: peers sharing this refund's account fingerprint OR policy number."""
    from django.db.models import Q
    q = Q()
    if refund.account_fingerprint:
        q |= Q(account_fingerprint=refund.account_fingerprint)
    if refund.policy_number:
        q |= Q(policy_number=refund.policy_number)
    return q


def apply_scan(refund, *, save: bool = True) -> dict:
    """Run the scan and stamp the result onto the refund."""
    res = scan_refund(refund)
    refund.fraud_flags = res['flags']
    refund.fraud_score = res['score']
    refund.fraud_reviewed_at = timezone.now()
    if save and refund.pk:
        refund.save(update_fields=['fraud_flags', 'fraud_score',
                                   'fraud_reviewed_at', 'updated_at'])
    return res


# ── Deferred signals (need fields/tables we don't store yet) ─────────────────
# * payee_is_staff_or_agent_account (CRITICAL): match account fingerprint vs
#   payroll + agent-payout bank accounts — needs a fingerprint index on those.
# * approver_agent_collusion_pair (HIGH): needs an approver×agent baseline.
# * bank_switch_for_known_customer (MEDIUM): needs prior-account history per name.
# * self_approval (CRITICAL): needs the Graphite requester identity carried onto
#   the record (add graphite_requested_by) to compute maker == checker.
