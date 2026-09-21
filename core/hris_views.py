"""
core/hris_views.py — HRIS access-probe endpoint.

Frontend (Sidebar HRIS group + /hris page + /hr-analytics page) calls
`GET /api/v1/admin/hris-access/` on mount. If `allowed` is False the
page redirects to /dashboard and the sidebar entry is hidden.

The actual whitelist logic lives in `core.hris_access`.
"""
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .hris_access import hris_access_payload


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def hris_access_status(request):
    """Reports whether the current user is allowed into HRIS."""
    return Response(hris_access_payload(request.user))
