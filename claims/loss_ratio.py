"""
claims/loss_ratio.py

Loss-ratio analytics for the Finance/Claims module.

Loss ratio = Incurred Losses / Earned Premium  (× 100, as %)
  Incurred Losses = Claims Paid + Case Reserves + IBNR + LAE
  Earned Premium  = the portion of premium that applies to the period
(Use earned, not written, so income matches the exposure that produced losses.)

Two reports (CFO directive 2026-06-15):
  1. build_client_loss_ratio   — individual client / policy loss history
     (Babusi's columns: policy number, reported date, total premium per year,
      total reserves + payments, overall loss ratio).
  2. build_large_loss_clients  — portfolio view: clients whose loss ratio is
     large (≥ threshold) — the bad-risk watchlist.

DATA SOURCES
------------
* Earned/written PREMIUM per policy comes from the Graphite payment feed
  (integrations.GraphitePaymentTransaction) — successful, non-refund,
  non-reverse payments summed by policy, net of refunds. This is REAL data
  once the finance:read token lands (see docs/graphite-omni-payment-integration).
* CLAIMS (paid + reserves) per policy are NOT held in omni — omni's claims app
  only tracks subrogation/salvage recoveries, not a claims register with
  reserves. The incurred-loss side needs a Graphite **claims** feed (a sibling
  endpoint to /finance/payment-transactions). Until that exists, the claims
  provider returns nothing and every row is flagged `claims_status =
  'claims_feed_pending'`. The engine + report shaping are complete and tested;
  only the live claims input is outstanding. DO NOT fabricate claim numbers.

The premium/claims providers are injectable so the math is unit-testable
without a live feed.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Callable, Dict, Optional, Tuple

TWO = Decimal('0.01')
ZERO = Decimal('0')


# ---------------------------------------------------------------------------
# Bands — how Finance reads a loss ratio
# ---------------------------------------------------------------------------

def loss_ratio_band(ratio_pct: Optional[float]) -> str:
    """Classify a loss ratio % into a Finance-readable band."""
    if ratio_pct is None:
        return 'no_premium'
    if ratio_pct < 40:
        return 'low'            # very profitable
    if ratio_pct < 70:
        return 'healthy'        # within target
    if ratio_pct < 100:
        return 'high'           # eroding margin
    return 'underwater'         # losses exceed premium


def loss_ratio_pct(earned_premium, incurred_losses) -> Optional[float]:
    """incurred / earned × 100, rounded to 2dp. None when premium is 0
    (a ratio is meaningless with no premium base)."""
    ep = Decimal(str(earned_premium or 0))
    il = Decimal(str(incurred_losses or 0))
    if ep <= 0:
        return None
    return float((il / ep * 100).quantize(TWO, rounding=ROUND_HALF_UP))


# ---------------------------------------------------------------------------
# Data providers (injectable — defaults read live omni/Graphite-feed data)
# ---------------------------------------------------------------------------

def premium_by_policy(date_from: Optional[date], date_to: Optional[date]) -> Dict[str, Decimal]:
    """Net premium collected per policy_number from the Graphite payment feed.

    Successful payments minus refunds/reversals. Returns {} (not an error) when
    the feed is empty / not yet syncing."""
    from integrations.models import GraphitePaymentTransaction

    qs = GraphitePaymentTransaction.objects.all()
    if date_from:
        qs = qs.filter(source_recorded_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(source_recorded_at__date__lte=date_to)

    out: Dict[str, Decimal] = defaultdict(lambda: ZERO)
    # Successful, non-refund, non-reverse = premium in. Refunds/reverses net down.
    for pol, amt, is_refund, is_reverse, status_norm in qs.values_list(
        'policy_number', 'amount', 'is_refund', 'is_reverse', 'status_norm',
    ):
        if not pol:
            continue
        if status_norm and status_norm not in ('success', 'successful', 'paid', 'completed'):
            continue
        a = Decimal(str(amt or 0))
        if is_refund or is_reverse:
            out[pol] -= a
        else:
            out[pol] += a
    return dict(out)


def reserves_payments_by_policy(
    date_from: Optional[date], date_to: Optional[date]
) -> Tuple[Dict[str, Decimal], Dict[str, Decimal], str]:
    """Reserves and Payments per policy_number, kept SEPARATE.

    Babusi's spec (2026-06-16) wants reserves and payments as two distinct
    columns, each with its own loss ratio against premium — not one combined
    'incurred' figure.

    omni has no general claims register — this needs a Graphite claims feed
    (sibling to the payment-transactions endpoint). Until that lands, returns
    ({}, {}, 'claims_feed_pending'). When wired, sum reserve_amount and
    paid_amount per policy into the two maps and return (..., ..., 'ok')."""
    # Hook point: once integrations.GraphiteClaimTransaction (or equivalent)
    # exists, build reserves_map (sum reserve_amount) and payments_map
    # (sum paid_amount) per policy here and return 'ok'.
    return {}, {}, 'claims_feed_pending'


def customer_by_policy() -> Dict[str, str]:
    """policy_number → customer_name, from the payment feed (best-effort)."""
    from integrations.models import GraphitePaymentTransaction

    out: Dict[str, str] = {}
    for pol, cust in (GraphitePaymentTransaction.objects
                      .exclude(policy_number='')
                      .values_list('policy_number', 'customer_name')):
        if pol and cust and pol not in out:
            out[pol] = cust
    return out


# ---------------------------------------------------------------------------
# Report 1 — individual client / policy loss history
# ---------------------------------------------------------------------------

def build_client_loss_ratio(
    *,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    policy_number: str = '',
    customer: str = '',
    _premium_map: Optional[Dict[str, Decimal]] = None,
    _reserves_map: Optional[Dict[str, Decimal]] = None,
    _payments_map: Optional[Dict[str, Decimal]] = None,
    _claims_status: str = '',
    _customer_map: Optional[Dict[str, str]] = None,
) -> dict:
    """Per-policy loss-history rows for one client (or one policy).

    Columns mirror Babusi's spec (2026-06-16): policy number, total premium,
    Total Reserves, Total Payments, Loss ratio on Reserves, Loss ratio on
    Payments. Reserves and Payments are SEPARATE columns each with their own
    ratio against premium.
    """
    premium = _premium_map if _premium_map is not None else premium_by_policy(date_from, date_to)
    if _reserves_map is not None or _payments_map is not None:
        reserves = _reserves_map or {}
        payments = _payments_map or {}
        claims_status = _claims_status or 'ok'
    else:
        reserves, payments, claims_status = reserves_payments_by_policy(date_from, date_to)
    cust_map = _customer_map if _customer_map is not None else customer_by_policy()

    policies = set(premium) | set(reserves) | set(payments)
    if policy_number:
        policies = {p for p in policies if p == policy_number}
    if customer:
        c = customer.lower()
        policies = {p for p in policies if c in (cust_map.get(p, '') or '').lower()}

    rows = []
    tot_prem = tot_reserves = tot_payments = ZERO
    for pol in sorted(policies):
        prem = Decimal(str(premium.get(pol, 0)))
        res  = Decimal(str(reserves.get(pol, 0)))
        pay  = Decimal(str(payments.get(pol, 0)))
        tot_prem += prem
        tot_reserves += res
        tot_payments += pay
        ratio_res = loss_ratio_pct(prem, res)
        ratio_pay = loss_ratio_pct(prem, pay)
        rows.append({
            'policy_number':              pol,
            'customer_name':              cust_map.get(pol, ''),
            'total_premium':              float(prem.quantize(TWO)),
            'total_reserves':             float(res.quantize(TWO)),
            'total_payments':             float(pay.quantize(TWO)),
            'loss_ratio_on_reserves_pct': ratio_res,
            'loss_ratio_on_payments_pct': ratio_pay,
            # Band reflects realized loss (payments) for the watchlist colour.
            'band':                       loss_ratio_band(ratio_pay),
        })

    overall_res = loss_ratio_pct(tot_prem, tot_reserves)
    overall_pay = loss_ratio_pct(tot_prem, tot_payments)
    return {
        'report': 'client_loss_ratio',
        'filters': {
            'date_from': date_from.isoformat() if date_from else None,
            'date_to':   date_to.isoformat() if date_to else None,
            'policy_number': policy_number or None,
            'customer': customer or None,
        },
        'claims_status': claims_status,   # 'ok' | 'claims_feed_pending'
        'summary': {
            'policy_count':    len(rows),
            'total_premium':   float(tot_prem.quantize(TWO)),
            'total_reserves':  float(tot_reserves.quantize(TWO)),
            'total_payments':  float(tot_payments.quantize(TWO)),
            'overall_loss_ratio_on_reserves_pct': overall_res,
            'overall_loss_ratio_on_payments_pct': overall_pay,
            'overall_band':    loss_ratio_band(overall_pay),
        },
        'rows': rows,
    }


# ---------------------------------------------------------------------------
# Report 2 — large-loss client watchlist (portfolio view)
# ---------------------------------------------------------------------------

def build_large_loss_clients(
    *,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    threshold_pct: float = 70.0,
    min_premium: float = 0.0,
    _premium_map: Optional[Dict[str, Decimal]] = None,
    _reserves_map: Optional[Dict[str, Decimal]] = None,
    _payments_map: Optional[Dict[str, Decimal]] = None,
    _claims_status: str = '',
    _customer_map: Optional[Dict[str, str]] = None,
) -> dict:
    """Clients whose loss ratio ≥ threshold (the bad-risk watchlist),
    aggregated per customer across all their policies, sorted worst-first.

    A client is flagged when EITHER the reserves ratio or the payments ratio
    meets the threshold; both ratios are shown (Babusi's split, 2026-06-16)."""
    premium = _premium_map if _premium_map is not None else premium_by_policy(date_from, date_to)
    if _reserves_map is not None or _payments_map is not None:
        reserves = _reserves_map or {}
        payments = _payments_map or {}
        claims_status = _claims_status or 'ok'
    else:
        reserves, payments, claims_status = reserves_payments_by_policy(date_from, date_to)
    cust_map = _customer_map if _customer_map is not None else customer_by_policy()

    # Aggregate per customer (fall back to policy_number when name unknown).
    agg: Dict[str, Dict] = defaultdict(
        lambda: {'premium': ZERO, 'reserves': ZERO, 'payments': ZERO, 'policies': 0})
    for pol in set(premium) | set(reserves) | set(payments):
        key = cust_map.get(pol) or f'(policy {pol})'
        agg[key]['premium']  += Decimal(str(premium.get(pol, 0)))
        agg[key]['reserves'] += Decimal(str(reserves.get(pol, 0)))
        agg[key]['payments'] += Decimal(str(payments.get(pol, 0)))
        agg[key]['policies'] += 1

    min_prem = Decimal(str(min_premium))
    rows = []
    for cust, v in agg.items():
        if v['premium'] < min_prem:
            continue
        ratio_res = loss_ratio_pct(v['premium'], v['reserves'])
        ratio_pay = loss_ratio_pct(v['premium'], v['payments'])
        worst = max([r for r in (ratio_res, ratio_pay) if r is not None], default=None)
        if worst is None or worst < threshold_pct:
            continue
        rows.append({
            'customer_name':  cust,
            'policy_count':   v['policies'],
            'total_premium':  float(v['premium'].quantize(TWO)),
            'total_reserves': float(v['reserves'].quantize(TWO)),
            'total_payments': float(v['payments'].quantize(TWO)),
            'loss_ratio_on_reserves_pct': ratio_res,
            'loss_ratio_on_payments_pct': ratio_pay,
            'band':           loss_ratio_band(worst),
        })
    rows.sort(key=lambda r: max(
        r['loss_ratio_on_reserves_pct'] or 0, r['loss_ratio_on_payments_pct'] or 0), reverse=True)

    return {
        'report': 'large_loss_clients',
        'filters': {
            'date_from': date_from.isoformat() if date_from else None,
            'date_to':   date_to.isoformat() if date_to else None,
            'threshold_pct': threshold_pct,
            'min_premium':   min_premium,
        },
        'claims_status': claims_status,
        'summary': {'flagged_clients': len(rows)},
        'rows': rows,
    }
