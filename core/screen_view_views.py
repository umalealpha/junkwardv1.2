"""core/screen_view_views.py — screen-usage beacon + report (CFO 2026-09-03).

Omni's frontend is a Next.js SPA, so page navigations never reach Django — a
middleware sees API calls, not screens. The frontend posts a beacon per screen
change instead; the server normalises the path to a family (record ids →
``:id``, query strings dropped) and keeps ONE row per (user, screen, minute).
Staff only, no content.
"""
from __future__ import annotations

import re

from django.db import IntegrityError
from django.db.models import Count
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.screen_view_models import ScreenView

_UUID_RE = re.compile(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')
_HEX_RE  = re.compile(r'^[0-9a-fA-F]{8,}$')
_INT_RE  = re.compile(r'^\d+$')
_SURFACES = {c for c, _ in ScreenView.Surface.choices}


def normalise_screen(path: str) -> str:
    """``/payment-requests/<uuid>/edit?tab=x`` → ``/payment-requests/:id/edit``.

    Drops the query string / fragment, collapses UUIDs, hex ids (≥ 8 chars) and
    pure-integer segments to ``:id``, strips a trailing slash. Never returns an
    empty string — an unparseable path becomes ``/``.
    """
    # Cap the raw input before any work: this is telemetry, never a text field.
    raw = (path or '')[:500].split('?', 1)[0].split('#', 1)[0].strip()
    parts = []
    for seg in raw.split('/'):
        if not seg:
            continue
        if _UUID_RE.match(seg) or _HEX_RE.match(seg) or _INT_RE.match(seg):
            parts.append(':id')
        else:
            parts.append(seg)
    return ('/' + '/'.join(parts))[:200]


def _minute_now():
    """Current time truncated to the minute (patched in tests)."""
    return timezone.now().replace(second=0, microsecond=0)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def screen_view_beacon(request):
    """POST /api/v1/adoption/screen-view/  body {screen, surface}."""
    surface = (request.data.get('surface') or '').strip()
    if surface not in _SURFACES:
        return Response({'detail': 'surface must be one of app / m / desktop.'},
                        status=status.HTTP_400_BAD_REQUEST)
    screen = normalise_screen(str(request.data.get('screen') or ''))
    try:
        _, created = ScreenView.objects.get_or_create(
            user=request.user, screen=screen, minute=_minute_now(),
            defaults={'surface': surface})
    except IntegrityError:  # two beacons raced inside the same minute — row exists
        created = False
    return Response({'ok': True},
                    status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def screen_view_report(request):
    """GET /api/v1/adoption/screens/?days=30 — top 50 screens by distinct users."""
    from hris.document_access import is_hr_doc_admin   # same gate as /adoption/
    if not is_hr_doc_admin(request.user):
        return Response({'detail': 'Screen usage is management-only.'},
                        status=status.HTTP_403_FORBIDDEN)
    try:
        days = int(request.query_params.get('days') or 30)
        days = max(7, min(days, 180))
    except (TypeError, ValueError):  # junk ?days= → default window
        days = 30
    since = timezone.now() - timezone.timedelta(days=days)
    rows = (ScreenView.objects.filter(minute__gte=since)
            .values('screen', 'surface')
            .annotate(users=Count('user', distinct=True), hits=Count('id'))
            .order_by('-users', '-hits', 'screen')[:50])
    return Response({'days': days, 'rows': list(rows)})
