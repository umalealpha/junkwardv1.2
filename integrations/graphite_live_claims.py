"""
integrations/graphite_live_claims.py — compute the CLAIMS feed cards from omni's
own live claims mirror (GraphiteClaim) instead of the stale/broken Alpha-Brain
push.

CFO 2026-09-01: the pushed `claims_by_type` feed showed 53 claims while the live
Graphite book holds ~4,500 (Motor 2,144, Glass 1,062…). The pushed claims
aggregates (by type / by group / major claims) come from TheRiskCo's Alpha Brain
query and were BOTH wrong and stale (last pushed 31 Aug while the 1 Sep nightly
refreshed only premium/loss-ratio). Omni already holds every claim in the
GraphiteClaim mirror — the same source the Graphite-Feeds claim drill-down uses —
so it can compute these three cards correctly itself and reconcile exactly with
that drill-down.

Only the three CLAIMS cards are computed here; every other feed (premium,
renewals, broker LR, KYC, debtors) stays on the pushed snapshot. If the mirror is
empty (never synced), build() returns None and the viewer falls back to the
pushed snapshot — never a blank screen. Aggregates only: no client PII.
"""
from __future__ import annotations

from django.db.models import Count, F, Max, Sum

from .models import GraphiteClaim

LIVE_CLAIM_DATASETS = ('claims_by_type', 'claims_by_group', 'major_claims')

# Policy-number prefixes → group, longest-first so COMD is not eaten by COM*.
_GROUP_PREFIXES = ('COMG', 'DOMG', 'COMD', 'MIS')


def available() -> bool:
    return GraphiteClaim.objects.exists()


def last_synced():
    return GraphiteClaim.objects.aggregate(m=Max('updated_at'))['m']


def _f(x) -> float:
    return float(x or 0)


def _group_of(policy_number) -> str:
    p = (policy_number or '').upper()
    for pref in _GROUP_PREFIXES:
        if p.startswith(pref):
            return pref
    return 'OTHER'


def build(dataset: str):
    """Rows for one claims card, computed live from the mirror. None if the
    dataset is not one we compute, or the mirror is empty (caller falls back)."""
    if dataset not in LIVE_CLAIM_DATASETS or not available():
        return None

    if dataset == 'claims_by_type':
        agg = (GraphiteClaim.objects
               .values('claim_type')
               .annotate(claim_count=Count('id'),
                         payment=Sum('total_payment'),
                         reserve=Sum('total_reserve'))
               .order_by('-claim_count'))
        return [{'claim_type': a['claim_type'] or '(none)',
                 'payment': round(_f(a['payment']), 2),
                 'reserve': round(_f(a['reserve']), 2),
                 'claim_count': a['claim_count']} for a in agg]

    if dataset == 'claims_by_group':
        buckets: dict[str, dict] = {}
        for pol, pay, res in GraphiteClaim.objects.values_list(
                'policy_number', 'total_payment', 'total_reserve'):
            g = _group_of(pol)
            b = buckets.setdefault(g, {'group': g, 'payment': 0.0,
                                       'reserve': 0.0, 'claim_count': 0})
            b['payment'] += _f(pay)
            b['reserve'] += _f(res)
            b['claim_count'] += 1
        rows = sorted(buckets.values(), key=lambda r: -r['payment'])
        for r in rows:
            r['payment'] = round(r['payment'], 2)
            r['reserve'] = round(r['reserve'], 2)
        return rows

    if dataset == 'major_claims':
        top = (GraphiteClaim.objects
               .annotate(exposure=F('total_payment') + F('total_reserve'))
               .order_by('-exposure')[:100])
        return [{'claim_no': c.claim_number or '—',
                 'claim_type': c.claim_type or '—',
                 'status': c.status or '—',
                 'paid': round(_f(c.total_payment), 2),
                 'reserve': round(_f(c.total_reserve), 2)} for c in top]

    return None
