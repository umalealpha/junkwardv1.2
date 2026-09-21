"""
core/aria/api.py — lightweight read endpoints for ARIA.

Two endpoints, both GET, IsAuthenticated:
  /api/v1/aria/urgent-deadlines/  → next 7 days
  /api/v1/aria/deadlines/         → next 30 days (full list)
"""

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .deadlines import next_deadlines, urgent_deadlines
from .news import fetch_news, top_item


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def urgent_deadlines_view(request):
    days = int(request.query_params.get('days') or 7)
    return Response({'urgent': urgent_deadlines(urgency_days=days)})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def deadlines_view(request):
    n = int(request.query_params.get('n') or 15)
    return Response({'deadlines': next_deadlines(n=n)})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def news_view(request):
    """GET /api/v1/aria/news/?force=1
    Returns the curated news feed. Frontend takes top item and surfaces
    it as a toast subject to its own cooldown. Server caches 15 min."""
    force = (request.query_params.get('force') or '').lower() in ('1', 'true', 'yes')
    items = fetch_news(force=force)
    top = items[0] if items else None
    return Response({
        'top': top,
        'items': items[:10],
        'major': bool(top and top.get('major')),
    })
