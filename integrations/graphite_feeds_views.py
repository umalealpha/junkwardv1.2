"""
integrations/graphite_feeds_views.py — read-only viewer for the Graphite
analytics snapshots that land via graphite_ingest.py.

    GET /api/v1/graphite-feeds/            -> the list of feeds (one per dataset)
    GET /api/v1/graphite-feeds/<dataset>/  -> the rows of one feed (paginated)

Read-only. This only reads GraphiteSnapshot; it never writes, and it starts
nothing. It exists so a person can SEE what Graphite has pushed into Omni, which
the ingest endpoint alone gives no way to do.
"""
from __future__ import annotations

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import GraphiteSnapshot
from .graphite_feed_filters import visible_rows, column_totals
from . import graphite_live_claims
from . import graphite_live_broker_lr
from . import graphite_claims_bridge

# Plain-English names for the dataset keys Graphite sends. An unknown key falls
# back to a title-cased version of the key itself, so a new feed still shows up
# with a readable name without a code change.
_LABELS = {
    'broker_lr': 'Broker loss ratios',
    'claims_by_group': 'Claims by group',
    'claims_by_type': 'Claims by type',
    'debtors_aging': 'Debtors ageing',
    'inforce_by_type': 'Policies in force by type',
    'kyc_completeness': 'KYC completeness',
    'loss_ratio_detail': 'Loss ratio detail',
    'major_claims': 'Major claims',
    'premium_by_group': 'Premium by group',
    'premium_by_line': 'Premium by line',
    'renewals_trigger': 'Renewals due',
}


def _label(key: str) -> str:
    return _LABELS.get(key, key.replace('_', ' ').title())


def _rows(snap) -> list:
    return (snap.payload or {}).get('rows') or []


def _columns(rows) -> list:
    return list(rows[0].keys()) if rows and isinstance(rows[0], dict) else []


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def graphite_feeds_list(request):
    feeds = []
    live_synced = graphite_live_claims.last_synced() if graphite_live_claims.available() else None
    for s in GraphiteSnapshot.objects.all().order_by('dataset'):
        # The three CLAIMS cards are computed live from omni's claims mirror,
        # not from the stale/broken Alpha-Brain push (CFO 2026-09-01). Other
        # feeds read the pushed snapshot.
        # Explicit None checks: an empty list is a real answer ("computed live,
        # nothing in it") and must not fall through to the pushed snapshot.
        live = graphite_live_claims.build(s.dataset)
        received_live = live_synced.isoformat() if live_synced else None
        if live is None:
            live = graphite_live_broker_lr.build(s.dataset)
            # Broker LR is computed on request, so it carries no mirror
            # timestamp — the claims mirror's would be the wrong date to show.
            received_live = None
        if live is not None:
            rows, received = live, received_live
        else:
            rows = visible_rows(s.dataset, _rows(s))
            received = s.received_at.isoformat() if s.received_at else None
        feeds.append({
            'dataset': s.dataset,
            'label': _label(s.dataset),
            # The card shows what the viewer will actually see, so a feed we
            # narrow (renewals: auto-renewing Instant removed) or compute live
            # (claims) reads its real count, not the raw snapshot count.
            'row_count': len(rows),
            'columns': _columns(rows),
            'received_at': received,
        })
    return Response({
        'feeds': feeds,
        'count': len(feeds),
        'source': ('Graphite Alpha Brain analytics (read-only, refreshed nightly). '
                   'Claims cards are computed live from Omni’s claims mirror.'),
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def graphite_claims_bridge_view(request):
    """Same-timestamp bridge: the mirror behind the claims cards vs live
    Graphite (CFO file 2 Medium, 18-Sep-2026). Its own endpoint, loaded after
    the page, so a slow replica never holds the feeds page blank. Same access
    as the claims cards it explains."""
    if not graphite_live_claims.available():
        return Response({'claims_bridge': None})
    return Response({'claims_bridge': graphite_claims_bridge.cached_bridge()})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def graphite_feed_detail(request, dataset):
    # Claims cards are computed live from omni's claims mirror (the same source
    # as the drill-down), overriding the stale/broken Alpha-Brain push so the
    # numbers are real and reconcile (CFO 2026-09-01). Everything else reads the
    # pushed snapshot.
    live = graphite_live_claims.build(dataset)
    broker_live = None if live is not None else graphite_live_broker_lr.build(dataset)
    if live is not None:
        rows = live
        synced = graphite_live_claims.last_synced()
        received = synced.isoformat() if synced else None
        source = 'Graphite V2 claims (live from Omni’s claims mirror)'
    elif broker_live is not None:
        # The pushed broker figures took the broker from the selling agent and
        # double-counted settled claims (CFO 2026-09-08), so this one is computed
        # live from the replica like the claims cards above.
        rows, received = broker_live, None
        source = 'Graphite V2 broker loss ratios (live from the read replica)'
    else:
        snap = GraphiteSnapshot.objects.filter(dataset=dataset).first()
        if snap is None:
            return Response({'detail': 'No such feed.'}, status=404)
        rows = visible_rows(snap.dataset, _rows(snap))
        received = snap.received_at.isoformat() if snap.received_at else None
        source = 'Graphite Alpha Brain (read-only, refreshed nightly)'

    try:
        page = max(1, int(request.query_params.get('page') or 1))
        per = min(500, max(10, int(request.query_params.get('per_page') or 50)))
    except (TypeError, ValueError):
        page, per = 1, 50
    start = (page - 1) * per

    return Response({
        'dataset': dataset,
        'label': _label(dataset),
        'columns': _columns(rows),
        'total': len(rows),
        'page': page,
        'per_page': per,
        'results': rows[start:start + per],
        # Totals are over EVERY row of the feed, not just this page, so the
        # figure is the true total even while the table is paginated.
        'totals': column_totals(rows),
        'received_at': received,
        'source': source,
    })
