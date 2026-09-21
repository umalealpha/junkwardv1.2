"""core/request_tracker_views.py — the "My Requests" API (read-only).

GET /api/v1/my-requests/         → everything the caller has submitted.
    ?ai=1                        → DeepSeek-polish each status line (PII-free).
GET /api/v1/my-requests/summary/ → the Welcome-page block: counts, leave balance
                                   and what needs the caller's own action (A1/A6).
"""
from __future__ import annotations

import logging

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.request_tracker import my_pending_actions, my_requests

log = logging.getLogger(__name__)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_requests_view(request):
    ai = str(request.GET.get('ai', '')).lower() in ('1', 'true', 'yes')
    items = my_requests(request.user, ai_polish=ai)
    active = sum(1 for r in items if r['bucket'] in ('pending', 'approved'))
    stuck = sum(1 for r in items if r.get('stuck'))
    return Response({
        'items':  items,
        'active': active,
        'stuck':  stuck,
        'total':  len(items),
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_requests_summary_view(request):
    """A1 + A6 — the compact Welcome-page block. Deliberately small: the full
    list stays on /my-requests and is not duplicated here."""
    items = my_requests(request.user)
    actions = my_pending_actions(request.user)

    # A1 — leave balance, read from the existing HRIS helper. If it is
    # unavailable we say so rather than rendering a confident zero.
    leave_balances, leave_error = [], ''
    try:
        from hris.feature_views import _profile_for
        from hris.leave_balance import balances_for_profile
        profile = _profile_for(request.user)
        if profile is None:
            leave_error = 'No HR record is linked to this login.'
        else:
            leave_balances = balances_for_profile(profile)
    except Exception:                       # noqa: BLE001
        # The user gets a soft message; ops must still get the real traceback,
        # or a genuine misconfiguration reaches nobody.
        log.exception('my_requests_summary: leave balances failed')
        leave_error = 'Leave balances could not be read just now.'

    by_kind: dict[str, int] = {}
    for r in items:
        if r['bucket'] in ('pending', 'approved'):
            by_kind[r['kind']] = by_kind.get(r['kind'], 0) + 1

    return Response({
        'active':          sum(by_kind.values()),
        'by_kind':         by_kind,
        'stuck':           sum(1 for r in items if r.get('stuck')),
        'recent':          items[:5],
        'actions':         actions['actions'],
        'actions_unavailable': actions['unavailable'],
        'leave_balances':  leave_balances,
        'leave_error':     leave_error,
    })
