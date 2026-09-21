"""core/adoption_views.py — read-only Omni adoption scoreboard API (C-suite/HR gated)."""
from __future__ import annotations

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def adoption_scoreboard(request):
    """Who is actually using Omni vs still hiding in Excel. Management-only."""
    from hris.document_access import is_hr_doc_admin   # C-suite + HR + superuser
    if not is_hr_doc_admin(request.user):
        return Response({'detail': 'Adoption scoreboard is management-only.'},
                        status=status.HTTP_403_FORBIDDEN)
    from core.adoption import compute
    try:
        days = int(request.query_params.get('days') or 30)
        days = max(7, min(days, 180))
    except (TypeError, ValueError):
        days = 30
    company = (request.query_params.get('company') or '').strip() or None
    return Response(compute(days=days, company=company))
