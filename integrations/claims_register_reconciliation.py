"""
integrations/claims_register_reconciliation.py — Omni's Claims Register vs live
Graphite, the way Bokani reconciled it by hand (bug 31883c46, 17-Sep-2026).

Her workbook, rebuilt on the register so nobody redoes it in Excel:
  1. record counts        in Omni / in Graphite / matched / each side only /
                          matched with a reserve difference
  2. reserve bridge       Omni total - Omni-only + Graphite-only + restatements
                          = expected Graphite, against actual Graphite
  3. payment bridge       the same, with payment differences on matched claims
  4. the claims behind the gap

WHICH CLAIMS. The register lists Omni's mirror filtered by `registered_date`,
which is Graphite's `claims.created_at` date (measured 19-Sep-2026: 140 of 140
mirror claims for 1-16 Sep carry the same created_at date in Graphite, while
`reported_date` is blank on almost every claim). Both sides are cut by that one
date range, so the two counts describe the same population.

WHICH AMOUNTS (measured on the same 140): reserve = SUM(reserve_amt) over the
claim's reserve lines (132/140 equal the mirror; the rest are genuine
restatements), paid = SUM(payment_amt) over lines not voided (133/140).
`totalReserveAmt` is blank in the replica and is not used.

Graphite unreachable → its side is reported unavailable, never as zeros, and a
failed read is never cached.
"""
from __future__ import annotations

import datetime as dt
import logging
from decimal import ROUND_HALF_UP, Decimal

from django.core.cache import cache
from django.utils import timezone

from .graphite_ro import is_configured, query
from .models import GraphiteClaim

log = logging.getLogger(__name__)

_GRAPHITE_SQL = """
SELECT c.claim_number AS claim_number,
       SUM(COALESCE(r.reserve_amt, 0)) AS reserve,
       SUM(CASE WHEN r.is_payment_voided = 0 THEN COALESCE(r.payment_amt, 0) ELSE 0 END) AS paid
FROM claims c
LEFT JOIN claim_reserves_coverages r ON r.claim_id = c.id
WHERE DATE(c.created_at) BETWEEN %s AND %s
  AND c.deleted_at IS NULL
GROUP BY c.id, c.claim_number
"""
_MAX_ROWS = 20000
_CENT = Decimal('0.01')
CACHE_SECONDS = 300


def _money(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(_CENT, rounding=ROUND_HALF_UP)


def default_range(today: dt.date | None = None) -> tuple[dt.date, dt.date]:
    """This month to date, Botswana time."""
    today = today or timezone.localdate()
    return today.replace(day=1), today


def cached_reconciliation(date_from: dt.date, date_to: dt.date) -> dict:
    key = f'claims_register_recon:v2:{date_from}:{date_to}'
    data = cache.get(key)
    if data is None:
        data = build_reconciliation(date_from, date_to)
        if data['graphite_available']:
            cache.set(key, data, CACHE_SECONDS)
    return data


def _graphite_side(date_from, date_to) -> tuple[dict | None, str]:
    if not is_configured():
        return None, 'The Graphite read-only link is not configured.'
    try:
        rows = query(_GRAPHITE_SQL, [date_from.isoformat(), date_to.isoformat()],
                     limit=_MAX_ROWS)
    except Exception:  # noqa: BLE001 — any read failure = unavailable, never zeros
        log.exception('claims reconciliation: Graphite read failed')
        return None, 'Graphite could not be read just now. Try again in a few minutes.'
    return ({r['claim_number']: {'reserve': _money(r['reserve']), 'paid': _money(r['paid'])}
             for r in rows if r.get('claim_number')}, '')


def _bridge(omni, graphite, only_o, only_g, matched, field) -> dict:
    o_total = sum((omni[c][field] for c in omni), Decimal('0'))
    g_total = sum((graphite[c][field] for c in graphite), Decimal('0'))
    less_o = sum((omni[c][field] for c in only_o), Decimal('0'))
    add_g = sum((graphite[c][field] for c in only_g), Decimal('0'))
    restated = sum((graphite[c][field] - omni[c][field] for c in matched), Decimal('0'))
    expected = o_total - less_o + add_g + restated
    variance = g_total - expected
    return {
        'omni_total': str(_money(o_total)),
        'less_omni_only': str(_money(-less_o)),
        'add_graphite_only': str(_money(add_g)),
        'add_differences_on_matched': str(_money(restated)),
        'expected_graphite': str(_money(expected)),
        'actual_graphite': str(_money(g_total)),
        'unexplained_variance': str(_money(variance)),
        'headline_gap': str(_money(g_total - o_total)),
        'ties': abs(variance) < _CENT,
    }


def build_reconciliation(date_from: dt.date, date_to: dt.date) -> dict:
    as_of = timezone.now()
    omni = {}
    reported = {}
    for c in GraphiteClaim.objects.filter(
            registered_date__gte=date_from, registered_date__lte=date_to).values(
            'claim_number', 'total_reserve', 'total_payment', 'registered_date'):
        omni[c['claim_number']] = {'reserve': _money(c['total_reserve']),
                                   'paid': _money(c['total_payment'])}
        reported[c['claim_number']] = c['registered_date']
    mirror_synced = (GraphiteClaim.objects.order_by('-updated_at')
                     .values_list('updated_at', flat=True).first())

    graphite, note = _graphite_side(date_from, date_to)
    base = {
        'date_from': date_from.isoformat(), 'date_to': date_to.isoformat(),
        'as_of': timezone.localtime(as_of).isoformat(),
        'omni_copy_synced_at': (timezone.localtime(mirror_synced).isoformat()
                                if mirror_synced else None),
        'graphite_available': graphite is not None,
        'note': note,
    }
    if graphite is None:
        return {**base, 'counts': {'omni': len(omni)}, 'reserve_bridge': None,
                'payment_bridge': None, 'restated': [], 'payment_differences': [],
                'graphite_only': [], 'omni_only': []}

    both = sorted(set(omni) & set(graphite))
    only_o = sorted(set(omni) - set(graphite))
    only_g = sorted(set(graphite) - set(omni))
    restated = [c for c in both if omni[c]['reserve'] != graphite[c]['reserve']]
    pay_diff = [c for c in both if omni[c]['paid'] != graphite[c]['paid']]

    return {
        **base,
        'counts': {'omni': len(omni), 'graphite': len(graphite), 'matched': len(both),
                   'omni_only': len(only_o), 'graphite_only': len(only_g),
                   'matched_reserve_difference': len(restated),
                   'matched_payment_difference': len(pay_diff)},
        'reserve_bridge': _bridge(omni, graphite, only_o, only_g, both, 'reserve'),
        'payment_bridge': _bridge(omni, graphite, only_o, only_g, both, 'paid'),
        'restated': [{'claim_number': c, 'omni': str(omni[c]['reserve']),
                      'graphite': str(graphite[c]['reserve']),
                      'difference': str(graphite[c]['reserve'] - omni[c]['reserve'])}
                     for c in restated],
        'payment_differences': [{'claim_number': c, 'omni': str(omni[c]['paid']),
                                 'graphite': str(graphite[c]['paid']),
                                 'difference': str(graphite[c]['paid'] - omni[c]['paid'])}
                                for c in pay_diff],
        'graphite_only': [{'claim_number': c, 'reserve': str(graphite[c]['reserve']),
                           'paid': str(graphite[c]['paid'])} for c in only_g],
        'omni_only': [{'claim_number': c, 'reserve': str(omni[c]['reserve']),
                       'reported_date': reported[c].isoformat() if reported[c] else None}
                      for c in only_o],
    }
