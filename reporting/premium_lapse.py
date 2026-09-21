"""
premium_lapse.py — premium lapse early-warning (wow feature #6).

Predicts the collections shortfall BEFORE month-end shows it. From the mirrored
Graphite payment history (integrations.GraphitePaymentTransaction) it finds
policies whose most-recent debits are FAILING and estimates the monthly premium
at risk. Pure ORM read; no AI, no writes, no PII sent anywhere.

Grounded in the real prod data (checked 2026-07-22), NOT the field docs:
  status_norm ∈ {'failed', 'success'}   (there is no 'reversed' value)
  payment_frequency is unreliable ('', '1', 'monthly') — so we do NOT filter on
  it; the lapse signal is simply the trailing run of failed debits per policy.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

FAILURE_STATUSES = {'failed'}
SUCCESS_STATUSES = {'success'}


def _median(values):
    vs = sorted(values)
    n = len(vs)
    if n == 0:
        return None
    mid = n // 2
    if n % 2 == 1:
        return vs[mid]
    return (vs[mid - 1] + vs[mid]) / Decimal(2)


def compute_lapse_risk(months: int = 6) -> dict:
    """Policies with a trailing run of failed debits, tiered by run length, with
    the monthly premium at risk estimated from the last successful payment."""
    from integrations.models import GraphitePaymentTransaction as G

    now = timezone.now()
    window_start = now - timedelta(days=30 * months)

    txns = (
        G.objects
        .filter(source_recorded_at__isnull=False, source_recorded_at__gte=window_start)
        .exclude(policy_number='')
        .exclude(is_refund=True)
        .order_by('policy_number', 'source_recorded_at')
        .values('policy_number', 'product_name', 'status_norm', 'is_reverse', 'amount', 'source_recorded_at')
    )

    # Group by policy (query is already ordered by policy, then time).
    by_policy: dict[str, list] = {}
    for t in txns.iterator():
        by_policy.setdefault(t['policy_number'], []).append(t)

    rows = []
    counts = {'tier1': 0, 'tier2': 0, 'tier3': 0}

    for policy, seq in by_policy.items():
        # Count consecutive failures from the most recent backward.
        consecutive = 0
        for t in reversed(seq):
            failed = (t['status_norm'] or '').lower() in FAILURE_STATUSES or t['is_reverse']
            if failed:
                consecutive += 1
            else:
                break
        if consecutive == 0:
            continue  # most recent debit succeeded — not at risk

        # Estimate the monthly premium at risk. A lapsing policy's most recent
        # FAILED debit carries the exact premium being missed — that is the best
        # at-risk figure. Fall back to the last successful amount, then the
        # median of successes (a policy that only ever failed still shows its
        # attempted premium).
        est = None
        for t in reversed(seq):              # most recent first
            amt = t['amount'] or Decimal('0')
            failed = (t['status_norm'] or '').lower() in FAILURE_STATUSES or t['is_reverse']
            if failed and amt > 0:
                est = amt
                break
        if est is None:
            successes = [
                t['amount'] for t in seq
                if (t['status_norm'] or '').lower() in SUCCESS_STATUSES
                and not t['is_reverse'] and t['amount']
            ]
            est = successes[-1] if successes else (_median(successes) or Decimal('0'))

        tier = 'tier1' if consecutive == 1 else ('tier2' if consecutive == 2 else 'tier3')
        counts[tier] += 1
        rows.append({
            'policy_number': policy,
            'product_name': seq[-1]['product_name'] or '',
            'tier': tier,
            'consecutive_misses': consecutive,
            'est_monthly_bwp': str((est or Decimal('0')).quantize(Decimal('0.01'))),
            'last_seen': str(seq[-1]['source_recorded_at'].date()) if seq[-1]['source_recorded_at'] else '',
        })

    rows.sort(key=lambda r: Decimal(r['est_monthly_bwp']), reverse=True)

    # Headline: annualisable monthly premium at real risk = 2+ consecutive misses.
    at_risk_monthly = sum(
        (Decimal(r['est_monthly_bwp']) for r in rows if r['tier'] in ('tier2', 'tier3')),
        Decimal('0'),
    )

    return {
        'as_of': str(now.date()),
        'window_months': months,
        'counts': counts,
        'at_risk_policies': len(rows),
        'at_risk_monthly_bwp': str(at_risk_monthly.quantize(Decimal('0.01'))),
        'rows': rows[:500],   # cap the payload; headline totals are over all rows
        'row_cap': 500,
    }
