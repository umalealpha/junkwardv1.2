"""
integrations/realpay_reconcile_views.py — the RealPay-vs-ledger check, on a screen.

GET /api/v1/integrations/realpay-ledger-reconciliation/?months=6

Same single calculation the daily command uses (integrations.realpay_ledger_reconcile),
so the screen and the morning email can never disagree with each other.

IT SERVES THIS MORNING'S SAVED ANSWER, NOT A FRESH SCAN. The comparison groups two
months of a 6.6-million-row feed per month and measured 23.5 seconds against the live
replica. An in-memory cache made repeat views instant, but the server runs four worker
processes, so up to four readers a quarter-hour still waited the full 23 seconds. So the
06:00 job stores its result (integrations.models.RealpayReconSnapshot) and this view
reads it, with the time it was computed returned beside it. `?live=1` forces a fresh
scan for anyone who wants one, and a fresh scan is what happens anyway if no snapshot
exists yet.

The daily command never reads any of this: the one job whose whole purpose is to notice
a change must always look at the database itself.

Gated on CanViewFinancials, the same permission the broker loss-ratio panel beside it
uses — this is a collections figure, not a general operational one, so plain
IsAuthenticated would be too wide.

Read-only: the calculation only ever SELECTs through integrations.graphite_ro, and this
view exposes months and counts — no customer information of any kind.
"""
from __future__ import annotations

import datetime

from django.core.cache import cache
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from core.permissions import CanViewFinancials
from integrations import realpay_ledger_reconcile as recon
from integrations.models import RealpayReconSnapshot

#: The comparison takes ~23s against the live replica. Monthly aggregates do not move
#: meaningfully inside a quarter of an hour.
CACHE_SECONDS = 15 * 60


@api_view(['GET'])
@permission_classes([CanViewFinancials])
def realpay_ledger_reconciliation(request):
    try:
        months = int(request.query_params.get('months') or 6)
    except (TypeError, ValueError):
        months = 6
    months = max(2, min(24, months))

    asof = None
    raw = (request.query_params.get('asof') or '').strip()
    if raw:
        try:
            asof = datetime.date.fromisoformat(raw[:10])
        except ValueError:
            asof = None

    live = str(request.query_params.get('live') or '').strip() in ('1', 'true', 'yes')

    # This morning's saved answer, unless a live scan was asked for.
    if not live and asof is None:
        snap = (RealpayReconSnapshot.objects
                .filter(window_months=months)
                .order_by('-created_at')
                .first())
        if snap and snap.payload:
            result = {**snap.payload,
                      'served_from': 'the daily check',
                      'computed_at': snap.created_at.isoformat(),
                      'live': False}
            result['summary'] = recon.summary_line(result)
            return Response(result)

    # No snapshot yet, a specific as-of date, or ?live=1. Compute — and keep a short
    # in-memory cache so a burst of readers does not each pay the full scan. An outage
    # is never cached: a replica blip would otherwise read as the state of the world.
    key = f'realpay-recon:v1:{months}:{asof.isoformat() if asof else "today"}'
    result = cache.get(key)
    cached = result is not None
    if result is None:
        result = recon.compare(months=months, asof=asof)
        if result.get('available'):
            cache.set(key, result, CACHE_SECONDS)

    result = {**result, 'served_from': 'a live scan', 'computed_at': None,
              'live': True, 'cached': cached, 'cache_seconds': CACHE_SECONDS}
    result['summary'] = recon.summary_line(result)
    return Response(result)
