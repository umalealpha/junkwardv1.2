"""regulatory/api_views.py"""
from datetime import date, datetime

from rest_framework import filters, status as drf_status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import get_user_profile
from django.utils import timezone

from .models import CapitalRequirementParameter, RegulatoryCapitalSnapshot
from .serializers import (
    CapitalRequirementParameterSerializer,
    RegulatoryCapitalSnapshotSerializer,
)
from .services import compute_capital_check, snapshot_method_state, take_snapshot


def _require_approver(request):
    profile = get_user_profile(request.user)
    if getattr(request.user, 'is_superuser', False):
        return
    if profile is None or not profile.can_approve_journal_entries:
        raise PermissionDenied(
            'Restricted to CFO / Finance Manager / Financial Controller.'
        )


class CapitalRequirementParameterViewSet(viewsets.ModelViewSet):
    """Edit the parameters that drive the capital-adequacy formula. Approver-only."""
    queryset           = CapitalRequirementParameter.objects.all().order_by('code')
    serializer_class   = CapitalRequirementParameterSerializer
    permission_classes = [IsAuthenticated]
    filter_backends    = [filters.SearchFilter]
    search_fields      = ['code', 'label', 'notes']

    def perform_create(self, serializer):
        _require_approver(self.request)
        serializer.save()

    def perform_update(self, serializer):
        _require_approver(self.request)
        serializer.save()

    def perform_destroy(self, instance):
        _require_approver(self.request)
        instance.delete()


class RegulatoryCapitalSnapshotViewSet(viewsets.ReadOnlyModelViewSet):
    """List + retrieve snapshots. Creation goes through /take-snapshot/."""
    queryset           = RegulatoryCapitalSnapshot.objects.select_related(
        'company', 'prepared_by', 'approved_by'
    ).order_by('-as_of_date', '-created_at')
    serializer_class   = RegulatoryCapitalSnapshotSerializer
    permission_classes = [IsAuthenticated]
    filter_backends    = [filters.SearchFilter]
    search_fields      = ['notes']

    @action(detail=False, methods=['post'], url_path='take')
    def take(self, request):
        """POST /api/v1/capital-snapshots/take/   {as_of?: YYYY-MM-DD, notes?: str}

        Approver-only. Creates a fresh DRAFT snapshot from the live GL.
        Approval (status -> compliant/margin/breach is auto-set; CFO sign-off
        is via /approve/).
        """
        _require_approver(request)
        as_of_raw = request.data.get('as_of')
        if as_of_raw:
            try:
                as_of = datetime.strptime(as_of_raw, '%Y-%m-%d').date()
            except ValueError:
                return Response({'error': f'Invalid as_of: {as_of_raw}'},
                                status=drf_status.HTTP_400_BAD_REQUEST)
        else:
            as_of = timezone.localdate()
        notes = request.data.get('notes', '')
        snap = take_snapshot(as_of=as_of, user=request.user, notes=notes)
        return Response(RegulatoryCapitalSnapshotSerializer(snap).data, status=drf_status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """CFO sign-off on a snapshot — locks it as the official record."""
        _require_approver(request)
        snap = self.get_object()
        if snap.approved_by_id is not None:
            return Response({'error': 'Snapshot already approved.'},
                            status=drf_status.HTTP_400_BAD_REQUEST)
        if snap.prepared_by_id == getattr(request.user, 'id', None):
            return Response({'error': 'Segregation of duties: preparer cannot approve their own snapshot.'},
                            status=drf_status.HTTP_400_BAD_REQUEST)
        # Approval makes a snapshot the official record behind a statutory
        # return, so it must not be possible to sign one whose method has been
        # withdrawn. The 2026-07-24 snapshot (CAR 612.02%) was computed with a
        # premium factor of 0.18 and a claims factor of 0.26, neither of which
        # exists in Botswana insurance law. Segregation of duties was the only
        # gate on it, and that stops the preparer — not a second approver.
        method = snapshot_method_state(snap)
        if not method['method_current']:
            return Response({'error': method['method_note']},
                            status=drf_status.HTTP_400_BAD_REQUEST)
        snap.approved_by = request.user
        snap.approved_at = timezone.now()
        snap.save(audit_user=request.user, audit_description='Snapshot approved')
        return Response(RegulatoryCapitalSnapshotSerializer(snap).data)


class CapitalCheckView(APIView):
    """
    GET /api/v1/regulatory/capital-check/?as_of=YYYY-MM-DD

    Live capital-adequacy computation — does NOT persist a snapshot.
    Use /capital-snapshots/take/ to save the result for audit.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        as_of_raw = request.query_params.get('as_of')
        if as_of_raw:
            try:
                as_of = datetime.strptime(as_of_raw, '%Y-%m-%d').date()
            except ValueError:
                return Response({'error': f'Invalid as_of: {as_of_raw}'},
                                status=drf_status.HTTP_400_BAD_REQUEST)
        else:
            as_of = timezone.localdate()
        # Capital adequacy is per LICENSED ENTITY, not consolidated (CFO
        # 2026-06-11: group equity was diluting ADIC's negative reserves).
        # Honour the topbar company; default to ADIC (the general insurer)
        # when none / "all companies" is selected.
        from core.mixins import resolve_company_id_param
        company_id = resolve_company_id_param(request)
        if not company_id:
            from core.models import Company
            company_id = (Company.objects.filter(code='ADIC')
                          .values_list('id', flat=True).first())
        return Response(compute_capital_check(as_of, company_id=company_id))
