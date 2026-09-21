"""Read-only API surface for the Settings → M365 Active Users page.

GET /api/v1/m365-active-users/   → list active users
GET /api/v1/m365-active-users/last-run/ → metadata for the last sync run
"""
from __future__ import annotations

from rest_framework import serializers, viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import M365ActiveUser, M365LicenseSyncRun


class M365ActiveUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = M365ActiveUser
        fields = (
            "id", "object_id", "display_name", "email", "user_principal_name",
            "job_title", "department", "license_count",
            "last_interactive_signin_at", "refreshed_at",
        )


class M365ActiveUserViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = M365ActiveUser.objects.all()
    serializer_class = M365ActiveUserSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None  # short list, return all


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def last_run(request):
    run = M365LicenseSyncRun.objects.order_by("-started_at").first()
    if run is None:
        return Response({"detail": "No sync has run yet."}, status=200)
    return Response({
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "success": run.success,
        "total_seen": run.total_seen,
        "active_count": run.active_count,
        "inserted": run.inserted,
        "updated": run.updated,
        "removed": run.removed,
        "cutoff_at": run.cutoff_at,
        "triggered_by": run.triggered_by,
        "error": run.error,
    })
