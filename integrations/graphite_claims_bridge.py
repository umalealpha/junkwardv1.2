"""
integrations/graphite_claims_bridge.py — Omni's claims mirror vs live Graphite,
read at the same moment (CFO file 2 Medium, 18-Sep-2026).

The Graphite claims cards are computed from Omni's mirror (GraphiteClaim). The
CFO asked for a same-timestamp bridge: the mirror's figure next to Graphite's
own, both read in the same request under ONE "as of" time, with the gap shown
rather than hidden.

Measured live on 18-Sep-2026 21:32 CAT: claim COUNT ties exactly (4,646 both
sides); PAID does not — mirror P133.09M vs Graphite P163.54M. The gap is not
just staleness (claims whose detail synced within the day disagree too), so
the two sides define "paid" differently. Until that is explained the bridge
says so; it never forces a tie.

Graphite's side uses the same non-voided payment sum as the broker loss-ratio
and claims-registry reads (integrations.graphite_live_broker_lr,
aware.modes.claims_registry). Aggregates only — no claim or client detail.
"""
from __future__ import annotations

import logging
from decimal import ROUND_HALF_UP, Decimal

from django.core.cache import cache
from django.db.models import Count, Max, Sum
from django.utils import timezone

from .graphite_ro import is_configured, query
from .models import GraphiteClaim

log = logging.getLogger(__name__)

_GRAPHITE_SQL = (
    "SELECT (SELECT COUNT(*) FROM claims) AS claim_count, "
    "(SELECT ROUND(SUM(CASE WHEN crc.is_payment_voided=0 "
    "THEN COALESCE(crc.payment_amt,0) ELSE 0 END),2) "
    "FROM claim_reserves_coverages crc JOIN claims c ON c.id = crc.claim_id) AS paid")

# A difference under one thebe is rounding, not a gap.
_TOLERANCE = Decimal('0.01')


def _money(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


CACHE_KEY = 'graphite_claims_bridge:v1'
CACHE_SECONDS = 300


def cached_bridge() -> dict:
    """The bridge sums every claim payment on the replica; cache it so a
    page view does not re-run that read. The as-of stamp says how old it is."""
    data = cache.get(CACHE_KEY)
    if data is None:
        data = build_bridge()
        if data['graphite'] is not None:   # never pin a failed read for 5 min
            cache.set(CACHE_KEY, data, CACHE_SECONDS)
    return data


def build_bridge() -> dict:
    """Both sides under one as-of stamp. Graphite unreachable → its side is
    None and the note says so; nothing is invented to fill it."""
    as_of = timezone.now()
    m = GraphiteClaim.objects.aggregate(
        n=Count('id'), paid=Sum('total_payment'), synced=Max('updated_at'))
    omni = {'claim_count': m['n'] or 0, 'paid': str(_money(m['paid']))}

    graphite = None
    note = ''
    if not is_configured():
        note = 'Graphite read-only link is not configured, so only Omni’s side is shown.'
    else:
        try:
            row = (query(_GRAPHITE_SQL, limit=1) or [{}])[0]
            graphite = {'claim_count': int(row.get('claim_count') or 0),
                        'paid': str(_money(row.get('paid')))}
        except Exception:  # noqa: BLE001 — any replica failure must show as "unavailable"
            log.exception('claims bridge: Graphite read failed')
            note = 'Graphite could not be read just now, so only Omni’s side is shown.'

    diff = ties = None
    if graphite is not None:
        count_gap = graphite['claim_count'] - omni['claim_count']
        paid_gap = _money(graphite['paid']) - _money(omni['paid'])
        diff = {'claim_count': count_gap, 'paid': str(paid_gap)}
        ties = {'claim_count': count_gap == 0, 'paid': abs(paid_gap) < _TOLERANCE}
        if not ties['paid']:
            note = ('Paid does not tie. Omni’s copy and Graphite count “paid” differently '
                    '(the gap remains on claims refreshed today), so treat Graphite as '
                    'the source until the difference is explained.')

    return {
        'as_of': as_of.isoformat(),
        'omni_mirror_synced_at': m['synced'].isoformat() if m['synced'] else None,
        'omni': omni,
        'graphite': graphite,
        'difference': diff,
        'ties': ties,
        'note': note,
    }
