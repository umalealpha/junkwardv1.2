"""Reconciliation Hub — Phase 1 API.

Read endpoints reuse ``FinancialReportView`` (IsAuthenticated + CanViewFinancials).
Triggering a run only writes recon tables (never the GL), so it uses the same
CanViewFinancials gate as the rest of finance reporting. Metric-map CRUD is
admin-only.
"""

from datetime import date

from rest_framework import viewsets
from rest_framework.permissions import BasePermission
from rest_framework.response import Response

from core.models import get_user_profile
from core.permissions import CanViewFinancials
from reporting.views import FinancialReportView, _parse_company

from .constants import DEFAULT_TOLERANCE_PCT
from .models import (
    MetricSourceMap,
    SourceFigure,
    ReconciliationRun,
)
from .serializers import (
    MetricSourceMapSerializer,
    SourceFigureSerializer,
    ReconciliationRunSerializer,
    ReconciliationRunListSerializer,
)
from .services import run_reconciliation, resolve_scope_company, AGEING_SCOPE_NOTE


def _parse_body_date(raw):
    """Parse YYYY-MM-DD from a request-body value. Returns (date|None, error|None)."""
    if not raw:
        return None, 'required'
    try:
        return date.fromisoformat(str(raw).strip()), None
    except ValueError:
        return None, f"invalid date '{raw}' — expected YYYY-MM-DD"


class CanAdministerReconMap(BasePermission):
    """Admin-only writes for the metric-source map (config)."""
    message = 'Only administrators may edit the reconciliation metric map.'

    def has_permission(self, request, view):
        user = getattr(request, 'user', None)
        if not (user and getattr(user, 'is_authenticated', False)):
            return False
        if getattr(user, 'is_superuser', False):
            return True
        prof = get_user_profile(user)
        return bool(prof and getattr(prof, 'is_administrator', False))


def _resolve_company(request):
    company_id = _parse_company(request)
    if company_id:
        from core.models import Company
        return Company.objects.filter(id=company_id).first()
    return resolve_scope_company()


class ReconDashboardView(FinancialReportView):
    """GET /api/v1/recon/dashboard/?period=<label>&company=<id|code>

    Latest reconciliation run for the company (optionally a specific period),
    with the metric bridge rows and the ageing tie-out panel.
    """

    def get(self, request):
        company = _resolve_company(request)
        if company is None:
            return Response({'run': None, 'rows': [], 'ageing': [],
                             'hint': 'No company in scope. Seed core.Company (code ADIC).'})

        qs = ReconciliationRun.objects.filter(company=company)
        period = (request.query_params.get('period') or '').strip()
        if period:
            qs = qs.filter(period_label=period)
        run = qs.order_by('-run_at').first()
        if run is None:
            return Response({
                'run': None, 'rows': [], 'ageing': [], 'scope_note': AGEING_SCOPE_NOTE,
                'hint': 'No reconciliation run yet — POST /api/v1/recon/run/ to create one.',
            })

        data = ReconciliationRunSerializer(run).data
        return Response({
            'run': {k: data[k] for k in (
                'id', 'company', 'company_code', 'period_label', 'period_start',
                'period_end', 'tolerance_pct', 'status', 'run_at', 'run_by_name')},
            'rows': data['lines'],
            'ageing': data['ageing'],
            'scope_note': AGEING_SCOPE_NOTE,
        })


class ReconRunView(FinancialReportView):
    """POST /api/v1/recon/run/  {period_label, period_end, period_start?, tolerance_pct?}

    Runs a reconciliation pass. Writes only recon tables — the GL is read-only here.
    """

    def post(self, request):
        company = _resolve_company(request)
        period_label = (request.data.get('period_label') or '').strip()
        if not period_label:
            return Response({'error': 'period_label is required'}, status=400)

        period_end, err = _parse_body_date(request.data.get('period_end'))
        if err:
            return Response({'error': f'period_end: {err}'}, status=400)

        period_start = None
        if request.data.get('period_start'):
            period_start, err = _parse_body_date(request.data.get('period_start'))
            if err:
                return Response({'error': f'period_start: {err}'}, status=400)

        tol = request.data.get('tolerance_pct')
        try:
            tolerance_pct = DEFAULT_TOLERANCE_PCT if tol in (None, '') else round(float(tol), 2)
        except (TypeError, ValueError):
            return Response({'error': 'tolerance_pct must be numeric'}, status=400)

        try:
            run = run_reconciliation(
                period_label=period_label,
                period_end=period_end,
                period_start=period_start,
                company=company,
                tolerance_pct=tolerance_pct,
                user=request.user,
            )
        except ValueError as exc:
            return Response({'error': str(exc)}, status=400)

        return Response(ReconciliationRunSerializer(run).data, status=201)


class ReconRunDetailView(FinancialReportView):
    """GET /api/v1/recon/runs/<uuid>/"""

    def get(self, request, pk):
        run = ReconciliationRun.objects.filter(id=pk).first()
        if run is None:
            return Response({'error': 'not found'}, status=404)
        return Response(ReconciliationRunSerializer(run).data)


class ReconRunListView(FinancialReportView):
    """GET /api/v1/recon/runs/  — run history (light)."""

    def get(self, request):
        company = _resolve_company(request)
        qs = ReconciliationRun.objects.all()
        if company is not None:
            qs = qs.filter(company=company)
        qs = qs.order_by('-run_at')[:100]
        return Response(ReconciliationRunListSerializer(qs, many=True).data)


class MetricSourceMapViewSet(viewsets.ModelViewSet):
    queryset = MetricSourceMap.objects.prefetch_related('accounts').all()
    serializer_class = MetricSourceMapSerializer

    def get_permissions(self):
        if self.request.method in ('GET', 'HEAD', 'OPTIONS'):
            return [CanViewFinancials()]
        return [CanAdministerReconMap()]

    def perform_create(self, serializer):
        serializer.save(audit_user=self.request.user)

    def perform_update(self, serializer):
        serializer.save(audit_user=self.request.user)


class SourceFigureViewSet(viewsets.ModelViewSet):
    """The expected-figures register — finance captures external source totals."""
    queryset = SourceFigure.objects.select_related('company').all()
    serializer_class = SourceFigureSerializer
    permission_classes = [CanViewFinancials]

    def get_queryset(self):
        qs = super().get_queryset()
        period = (self.request.query_params.get('period') or '').strip()
        if period:
            qs = qs.filter(period_label=period)
        metric = (self.request.query_params.get('metric') or '').strip()
        if metric:
            qs = qs.filter(metric_key=metric)
        return qs

    def perform_create(self, serializer):
        serializer.save(captured_by=self.request.user, audit_user=self.request.user)

    def perform_update(self, serializer):
        serializer.save(audit_user=self.request.user)
