"""
assets/api_views.py

REST API for the Fixed Assets module.

Endpoints:

  GET/POST     /api/v1/asset-categories/
  GET/POST     /api/v1/assets/
  GET          /api/v1/assets/{id}/
  POST         /api/v1/assets/{id}/depreciate/   — post one period of depreciation
  POST         /api/v1/assets/{id}/dispose/      — record disposal
  POST         /api/v1/assets/run-monthly-depreciation/   — bulk depreciation run
  GET          /api/v1/depreciation-entries/
  GET          /api/v1/asset-disposals/

  POST         /api/v1/asset-imports/preview/    — upload + parse Odoo CSV/XLSX
  POST         /api/v1/asset-imports/{id}/commit/ — commit a previewed import
  GET          /api/v1/asset-imports/
"""

from datetime import date

from django.shortcuts import get_object_or_404
from rest_framework import filters, status as drf_status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from core.mixins import CompanyScopedViewSetMixin
from core.models import Company
from ledger.models import Account, FiscalPeriod

from .models import (
    Asset,
    AssetCategory,
    AssetDisposal,
    AssetImportBatch,
    AssetSignOff,
    DepreciationEntry,
)
from .serializers import (
    AssetCategorySerializer,
    AssetCreateSerializer,
    AssetDetailSerializer,
    AssetDisposalActionSerializer,
    AssetDisposalSerializer,
    AssetImportBatchSerializer,
    AssetImportPreviewSerializer,
    AssetListSerializer,
    AssetSignOffActionSerializer,
    AssetSignOffSerializer,
    AssetTransferActionSerializer,
    DepreciationEntrySerializer,
    RunDepreciationSerializer,
)
from .services import (
    approve_disposal,
    commit_import,
    depreciate_asset,
    reject_disposal,
    request_disposal,
    parse_odoo_file,
    run_monthly_depreciation,
    transfer_asset_custodian,
    validate_parsed_rows,
)
from django.utils import timezone


# ---------------------------------------------------------------------------
# Asset Category
# ---------------------------------------------------------------------------

class AssetCategoryViewSet(viewsets.ModelViewSet):
    """
    NOTE: explicitly NOT using CompanyScopedViewSetMixin.

    CFO directive 2026-05-20: the FAR dropdown was empty on every
    non-ADIC entity (because no AssetCategory rows had a cost_account
    owned by them). Strict scoping made the form unusable. Instead,
    show the union of:
      - categories whose cost_account.owner_company matches the topbar
      - categories whose cost_account.owner_company is NULL (shared)
    Filter via explicit ?company= when caller really wants the
    entity-only slice.
    """
    queryset         = AssetCategory.objects.select_related(
        'cost_account', 'accum_depr_account', 'depreciation_expense_account',
    ).all()
    serializer_class = AssetCategorySerializer
    filterset_fields = ['is_active']
    search_fields    = ['code', 'name']
    ordering_fields  = ['code', 'name']

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        company = (params.get('company') or '').strip()
        if not company:
            return qs
        # Union: this company's categories + shared (null-owner) categories.
        from django.db.models import Q
        return qs.filter(
            Q(cost_account__owner_company_id=company)
            | Q(cost_account__owner_company__isnull=True)
        )


# ---------------------------------------------------------------------------
# Asset
# ---------------------------------------------------------------------------

class AssetViewSet(CompanyScopedViewSetMixin, viewsets.ModelViewSet):
    queryset = Asset.objects.select_related(
        'company', 'category', 'created_by',
    ).prefetch_related('depreciation_entries', 'disposal').all()

    filterset_fields = ['status', 'category', 'company']
    search_fields    = ['tag_number', 'external_ref', 'name', 'serial_number', 'location', 'custodian']
    ordering_fields  = ['tag_number', 'purchase_date', 'cost', 'created_at']

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        # Purchase-date range filter (CFO directive 2026-05-17 — register
        # views need a date window for board packs / FY cuts).
        if params.get('purchase_date_from'):
            qs = qs.filter(purchase_date__gte=params['purchase_date_from'])
        if params.get('purchase_date_to'):
            qs = qs.filter(purchase_date__lte=params['purchase_date_to'])
        # Company filter is applied by CompanyScopedViewSetMixin (handles
        # both UUID and Company.code; honours ?company__code= alias).
        if params.get('status'):
            qs = qs.filter(status=params['status'])
        if params.get('category'):
            qs = qs.filter(category_id=params['category'])
        return qs

    def get_serializer_class(self):
        if self.action == 'create':
            return AssetCreateSerializer
        if self.action == 'retrieve':
            return AssetDetailSerializer
        return AssetListSerializer

    # ---- dashboard rollup ------------------------------------------------------

    @action(detail=False, methods=['get'])
    def summary(self, request):
        """GET /api/v1/assets/summary/ — Fixed-assets dashboard rollup: totals,
        a by-category breakdown, and a 12-month movements series (additions +
        depreciation). Company-scoped exactly like the register (CFO 2026-08-30).
        NBV = cost - (opening accumulated depreciation + posted, non-reversed
        depreciation entries)."""
        from django.db.models import Count, Sum
        from django.db.models.functions import TruncMonth
        import datetime as _dt
        from assets.models import DepreciationEntry

        qs = self.get_queryset()

        def _f(x):
            return float(x or 0)

        agg = qs.aggregate(cost=Sum('cost'),
                           opening=Sum('opening_accumulated_depreciation'),
                           n=Count('id'))
        booked = (DepreciationEntry.objects
                  .filter(asset__in=qs, reversed_at__isnull=True)
                  .aggregate(t=Sum('amount'))['t'])
        total_cost = _f(agg['cost'])
        total_accum = _f(agg['opening']) + _f(booked)
        totals = {
            'count': agg['n'] or 0,
            'cost': round(total_cost, 2),
            'accumulated_depreciation': round(total_accum, 2),
            'net_book_value': round(total_cost - total_accum, 2),
        }

        cat_rows = qs.values('category__name').annotate(
            cost=Sum('cost'), opening=Sum('opening_accumulated_depreciation'), n=Count('id'))
        cat_booked = {(r['asset__category__name'] or 'Uncategorised'): _f(r['t']) for r in
                      (DepreciationEntry.objects
                       .filter(asset__in=qs, reversed_at__isnull=True)
                       .values('asset__category__name').annotate(t=Sum('amount')))}
        by_category = []
        for r in cat_rows:
            name = r['category__name'] or 'Uncategorised'
            cost = _f(r['cost'])
            accum = _f(r['opening']) + cat_booked.get(name, 0.0)
            by_category.append({'category': name, 'count': r['n'],
                                'cost': round(cost, 2),
                                'accumulated_depreciation': round(accum, 2),
                                'net_book_value': round(cost - accum, 2)})
        by_category.sort(key=lambda c: -c['net_book_value'])

        today = timezone.localdate()
        first = (today.replace(day=1) - _dt.timedelta(days=365)).replace(day=1)
        add = {r['m'].strftime('%Y-%m'): _f(r['t']) for r in
               (qs.filter(purchase_date__gte=first)
                .annotate(m=TruncMonth('purchase_date')).values('m').annotate(t=Sum('cost'))) if r['m']}
        dep = {r['m'].strftime('%Y-%m'): _f(r['t']) for r in
               (DepreciationEntry.objects
                .filter(asset__in=qs, reversed_at__isnull=True, period_end_date__gte=first)
                .annotate(m=TruncMonth('period_end_date')).values('m').annotate(t=Sum('amount'))) if r['m']}
        movements = []
        d = first
        while d <= today.replace(day=1):
            k = d.strftime('%Y-%m')
            movements.append({'month': k,
                              'additions': round(add.get(k, 0.0), 2),
                              'depreciation': round(dep.get(k, 0.0), 2)})
            d = (d.replace(day=28) + _dt.timedelta(days=4)).replace(day=1)

        return Response({'totals': totals, 'by_category': by_category, 'movements': movements})

    # ---- export the filtered register to CSV ----------------------------------

    @action(detail=False, methods=['get'])
    def export_csv(self, request):
        """GET /api/v1/assets/export_csv/ — returns the current filtered list
        as a CSV download. Honours all the same filters as the list endpoint
        (status, category, search, purchase_date_from/to).
        """
        import csv as _csv
        from django.http import HttpResponse as _HttpResponse

        qs = self.filter_queryset(self.get_queryset())

        resp = _HttpResponse(content_type='text/csv')
        stamp = timezone.localdate().isoformat()
        resp['Content-Disposition'] = f'attachment; filename="fixed_assets_{stamp}.csv"'

        w = _csv.writer(resp)
        w.writerow([
            'tag_number', 'name', 'category', 'company', 'status',
            'purchase_date', 'in_service_date', 'cost', 'salvage_value',
            'accumulated_depreciation', 'net_book_value',
            'useful_life_months', 'method',
            'serial_number', 'location', 'custodian', 'external_ref',
        ])
        for a in qs.iterator(chunk_size=500):
            w.writerow([
                a.tag_number, a.name,
                a.category.code if a.category_id else '',
                a.company.code if a.company_id else '',
                a.status,
                a.purchase_date.isoformat() if a.purchase_date else '',
                a.in_service_date.isoformat() if a.in_service_date else '',
                str(a.cost),
                str(a.salvage_value),
                str(a.accumulated_depreciation),
                str(a.net_book_value),
                a.useful_life_months,
                a.method,
                a.serial_number or '', a.location or '',
                a.custodian or '', a.external_ref or '',
            ])
        return resp

    def perform_create(self, serializer):
        instance = serializer.save(created_by=self.request.user)
        instance.save(audit_user=self.request.user, audit_description='Created asset')

    # ---- depreciate one period -------------------------------------------

    @action(detail=True, methods=['post'])
    def depreciate(self, request, pk=None):
        """POST /api/v1/assets/{id}/depreciate/?period=YYYY-MM"""
        asset = self.get_object()
        period_name = request.data.get('period') or request.query_params.get('period')
        if not period_name:
            return Response(
                {'detail': 'Provide a period (YYYY-MM).'},
                status=drf_status.HTTP_400_BAD_REQUEST,
            )
        period = FiscalPeriod.objects.filter(period_name=period_name).first()
        if not period:
            return Response(
                {'detail': f'No fiscal period named {period_name}.'},
                status=drf_status.HTTP_400_BAD_REQUEST,
            )
        entry = depreciate_asset(asset, period, request.user)
        if entry is None:
            return Response(
                {'detail': 'No depreciation posted (already booked, fully depreciated, or before in-service).'},
                status=drf_status.HTTP_200_OK,
            )
        return Response(DepreciationEntrySerializer(entry).data, status=drf_status.HTTP_201_CREATED)

    # ---- dispose ----------------------------------------------------------

    @action(detail=True, methods=['post'])
    def dispose(self, request, pk=None):
        """POST /api/v1/assets/{id}/dispose/

        Requests a disposal — does NOT post to the GL yet. Status is set to
        PENDING_APPROVAL. An approver (CFO / Finance Manager / Financial
        Controller, NOT the requester) must call /asset-disposals/{id}/approve/
        to actually hit the books.
        """
        asset = self.get_object()
        s = AssetDisposalActionSerializer(data=request.data)
        s.is_valid(raise_exception=True)

        bank_account = None
        if s.validated_data.get('bank_account'):
            bank_account = get_object_or_404(Account, pk=s.validated_data['bank_account'])

        disposal = request_disposal(
            asset,
            disposal_type=s.validated_data['disposal_type'],
            disposal_date=s.validated_data['disposal_date'],
            proceeds=s.validated_data.get('proceeds') or 0,
            bank_account=bank_account,
            user=request.user,
            notes=s.validated_data.get('notes', ''),
        )
        # Notify approvers asynchronously of the new request
        try:
            from core.notifications import notify_disposal_pending
            notify_disposal_pending(disposal)
        except Exception:  # noqa: BLE001
            pass
        return Response(AssetDisposalSerializer(disposal).data, status=drf_status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def transfer(self, request, pk=None):
        """POST /api/v1/assets/{id}/transfer/

        Hand the asset over to a new custodian (staff member) and/or location.
        NON-GL — custodianship + physical location only; depreciation, cost and
        the owning legal entity are untouched. Writes an append-only
        AssetAssignment history row so the chain A → B → C is auditable.
        CFO 2026-06-26.
        """
        asset = self.get_object()
        s = AssetTransferActionSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        assignment = transfer_asset_custodian(
            asset,
            to_employee=s.validated_data.get('to_employee'),
            to_location=s.validated_data.get('to_location', ''),
            reason=s.validated_data.get('reason', ''),
            transferred_at=s.validated_data.get('transferred_at'),
            user=request.user,
        )
        # Return the full refreshed asset (with its updated history) for the UI.
        asset.refresh_from_db()
        return Response(AssetDetailSerializer(asset).data, status=drf_status.HTTP_200_OK)

    @action(detail=True, methods=['patch'])
    def condition(self, request, pk=None):
        """PATCH /api/v1/assets/{id}/condition/"""
        asset = self.get_object()
        value = (request.data.get('condition') or '').strip().lower()
        valid = {c[0] for c in Asset.Condition.choices}
        if value not in valid:
            return Response({'error': f'condition must be one of {sorted(valid)}'},
                            status=drf_status.HTTP_400_BAD_REQUEST)
        asset.condition = value
        asset.save(update_fields=['condition', 'updated_at'])
        return Response({'id': str(asset.pk), 'condition': asset.condition})

    @action(detail=False, methods=['get'])
    def employees(self, request):
        """GET /api/v1/assets/employees/?q=<search>

        Lightweight staff name list for the asset-transfer picker. Names + numbers
        + entity ONLY — no pay data — so it is safe for anyone who can see the
        asset register (not payroll-gated). CFO 2026-06-26.
        """
        from payroll.models import Employee
        qs = Employee.objects.filter(status='active').select_related('company').order_by('full_name')
        q = (request.query_params.get('q') or '').strip()
        if q:
            qs = qs.filter(full_name__icontains=q) | qs.filter(employee_number__icontains=q)
        out = [{
            'id': str(e.id),
            'full_name': e.full_name,
            'email': e.email or '',
            'employee_number': e.employee_number or '',
            'company_code': e.company.code if e.company_id else '',
        } for e in qs[:500]]
        return Response({'employees': out})

    # ---- run monthly depreciation (bulk) ---------------------------------

    @action(detail=False, methods=['post'], url_path='run-monthly-depreciation')
    def run_monthly_depreciation(self, request):
        """POST /api/v1/assets/run-monthly-depreciation/"""
        s = RunDepreciationSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        period = FiscalPeriod.objects.filter(period_name=s.validated_data['period_name']).first()
        if not period:
            return Response(
                {'detail': f"Unknown period {s.validated_data['period_name']}."},
                status=drf_status.HTTP_400_BAD_REQUEST,
            )
        company = None
        if s.validated_data.get('company'):
            company = get_object_or_404(Company, pk=s.validated_data['company'])
        result = run_monthly_depreciation(
            period, request.user,
            company=company,
            dry_run=s.validated_data.get('dry_run', False),
        )
        return Response({
            'period':                    result.period,
            'assets_considered':         result.assets_considered,
            'assets_depreciated':        result.assets_depreciated,
            'assets_skipped':            result.assets_skipped,
            'assets_fully_depreciated':  result.assets_fully_depreciated,
            'total_amount':              str(result.total_amount),
            'errors':                    result.errors,
            'dry_run':                   s.validated_data.get('dry_run', False),
        })


# ---------------------------------------------------------------------------
# Depreciation Entries (read-only list)
# ---------------------------------------------------------------------------

class DepreciationEntryViewSet(viewsets.ReadOnlyModelViewSet):
    queryset         = DepreciationEntry.objects.select_related(
        'asset', 'period', 'journal_entry',
    ).all()
    serializer_class = DepreciationEntrySerializer
    filterset_fields = ['asset', 'period']
    ordering_fields  = ['period_end_date', 'amount']


# ---------------------------------------------------------------------------
# Disposals (read-only list)
# ---------------------------------------------------------------------------

class AssetDisposalViewSet(viewsets.ReadOnlyModelViewSet):
    queryset         = AssetDisposal.objects.select_related(
        'asset', 'bank_account', 'journal_entry',
    ).all()
    serializer_class = AssetDisposalSerializer
    filterset_fields = ['disposal_type', 'status']
    ordering_fields  = ['disposal_date']

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """POST /api/v1/asset-disposals/{id}/approve/ — checker step.

        Approver must hold an approver title (CFO / Finance Manager /
        Financial Controller) and cannot be the requester (segregation of
        duties). Posts the GL journal in the same transaction.
        """
        disposal = self.get_object()
        try:
            approve_disposal(disposal, request.user)
        except Exception as e:
            msg = e.messages[0] if hasattr(e, 'messages') else str(e)
            return Response({'error': msg}, status=drf_status.HTTP_400_BAD_REQUEST)
        return Response(AssetDisposalSerializer(disposal).data)

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        """POST /api/v1/asset-disposals/{id}/reject/   {reason: str}"""
        disposal = self.get_object()
        reason = request.data.get('reason', '')
        try:
            reject_disposal(disposal, request.user, reason)
        except Exception as e:
            msg = e.messages[0] if hasattr(e, 'messages') else str(e)
            return Response({'error': msg}, status=drf_status.HTTP_400_BAD_REQUEST)
        return Response(AssetDisposalSerializer(disposal).data)


# ---------------------------------------------------------------------------
# Imports (Odoo)
# ---------------------------------------------------------------------------

class AssetImportBatchViewSet(viewsets.ReadOnlyModelViewSet):
    queryset         = AssetImportBatch.objects.select_related(
        'company', 'created_by',
    ).all()
    serializer_class = AssetImportBatchSerializer
    filterset_fields = ['status', 'source', 'company']
    ordering_fields  = ['created_at', 'committed_at']

    @action(
        detail=False, methods=['post'], url_path='preview',
        parser_classes=[MultiPartParser, FormParser, JSONParser],
    )
    def preview(self, request):
        """
        POST /api/v1/asset-imports/preview/

        Multipart form:
            file         (CSV or XLSX export from Odoo)
            cutover_date (YYYY-MM-DD)
            company      (UUID)
            source       (optional, default 'odoo')

        Returns a draft AssetImportBatch with parsed_rows + validation_errors.
        Nothing is written to the Asset table yet — call /commit/ to do that.
        """
        s = AssetImportPreviewSerializer(data=request.data)
        s.is_valid(raise_exception=True)

        company = get_object_or_404(Company, pk=s.validated_data['company'])
        upload = s.validated_data['file']
        rows, parse_errors = parse_odoo_file(upload, file_name=getattr(upload, 'name', '') or '')
        validation_errors  = validate_parsed_rows(rows)
        all_errors = parse_errors + validation_errors

        rows_invalid_idx = {e['row_index'] for e in validation_errors if e['row_index'] != -1}
        rows_invalid     = len({i for i in rows_invalid_idx})
        rows_valid       = max(0, len(rows) - rows_invalid)

        batch = AssetImportBatch.objects.create(
            source=s.validated_data.get('source', 'odoo'),
            file_name=getattr(upload, 'name', '') or '',
            cutover_date=s.validated_data['cutover_date'],
            rows_total=len(rows),
            rows_valid=rows_valid,
            rows_invalid=rows_invalid,
            parsed_rows=rows,
            validation_errors=all_errors,
            status=AssetImportBatch.Status.DRAFT,
            company=company,
            created_by=request.user,
        )
        batch.save(audit_user=request.user, audit_description=f'Previewed {len(rows)} rows')
        return Response(AssetImportBatchSerializer(batch).data, status=drf_status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """POST /api/v1/asset-imports/{id}/approve/

        Dual-authorisation: first eligible approver moves the batch to
        partially_approved; second (different) approver moves it to approved.
        """
        from core.dual_auth import record_approval
        batch = self.get_object()
        try:
            record_approval(
                batch, request.user,
                partial_status=AssetImportBatch.Status.PARTIALLY_APPROVED,
                approved_status=AssetImportBatch.Status.APPROVED,
            )
            batch.save(audit_user=request.user, audit_description='Approved asset import')
        except Exception as e:
            msg = e.messages[0] if hasattr(e, 'messages') else str(e)
            return Response({'error': msg}, status=drf_status.HTTP_400_BAD_REQUEST)
        return Response(AssetImportBatchSerializer(batch).data)

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        """POST /api/v1/asset-imports/{id}/reject/   {reason: str}"""
        from core.dual_auth import record_rejection
        batch = self.get_object()
        reason = request.data.get('reason', '')
        try:
            record_rejection(
                batch, request.user, reason,
                rejected_status=AssetImportBatch.Status.REJECTED,
            )
            batch.save(audit_user=request.user, audit_description=f'Rejected: {reason}')
        except Exception as e:
            msg = e.messages[0] if hasattr(e, 'messages') else str(e)
            return Response({'error': msg}, status=drf_status.HTTP_400_BAD_REQUEST)
        return Response(AssetImportBatchSerializer(batch).data)

    @action(detail=True, methods=['post'])
    def commit(self, request, pk=None):
        """POST /api/v1/asset-imports/{id}/commit/

        Requires the batch to be APPROVED — both approvers have signed off.
        """
        from core.dual_auth import assert_ready_to_commit
        batch = self.get_object()
        try:
            assert_ready_to_commit(batch)
        except Exception as e:
            msg = e.messages[0] if hasattr(e, 'messages') else str(e)
            return Response({'error': msg}, status=drf_status.HTTP_400_BAD_REQUEST)
        if batch.rows_invalid > 0:
            return Response(
                {'detail': f'{batch.rows_invalid} row(s) have validation errors. Fix the file and re-preview before committing.'},
                status=drf_status.HTTP_400_BAD_REQUEST,
            )
        created, errors = commit_import(batch, request.user)
        return Response({
            'created':           created,
            'errors':            errors,
            'batch':             AssetImportBatchSerializer(batch).data,
        })


class AssetSignOffViewSet(viewsets.ModelViewSet):
    """
    Sign-off queue for fixed-asset internal controls.

    Two kinds of sign-off:
      - fully_depreciated_review: any active asset whose NBV is at salvage.
        Manager + Finance Manager both sign.
      - semi_annual_count: company-wide physical count, due 30-Jun and 31-Dec.
        CFO signs.

    The scan_asset_signoffs management command (run daily via cron) keeps
    this queue current. Records auto-flag as overdue once the due_date
    passes without completion.
    """
    queryset           = AssetSignOff.objects.select_related(
        'asset', 'company', 'first_signed_by', 'second_signed_by',
    ).order_by('due_date', '-created_at')
    serializer_class   = AssetSignOffSerializer
    filter_backends    = [filters.SearchFilter, filters.OrderingFilter]
    search_fields      = ['period_label', 'asset__tag_number', 'asset__name', 'notes']
    ordering_fields    = ['due_date', 'status', 'created_at']

    def get_queryset(self):
        qs = super().get_queryset()
        kind = self.request.query_params.get('kind')
        if kind:
            qs = qs.filter(kind=kind)
        st = self.request.query_params.get('status')
        if st:
            qs = qs.filter(status=st)
        if self.request.query_params.get('open_only', '').lower() == 'true':
            qs = qs.filter(status__in=[
                AssetSignOff.Status.PENDING,
                AssetSignOff.Status.PARTIALLY_SIGNED,
                AssetSignOff.Status.OVERDUE,
            ])
        return qs

    def _can_sign(self, request, slot):
        """slot in {'first', 'second'} — currently both slots open to any approver."""
        from core.models import get_user_profile
        profile = get_user_profile(request.user)
        if getattr(request.user, 'is_superuser', False):
            return True
        # Second slot (Finance Manager / CFO) requires approver title
        if slot == 'second':
            return bool(profile and profile.can_approve_journal_entries)
        # First slot (Manager) — allow any active staff with creation rights
        return bool(profile and (profile.can_create_journal_entries or profile.can_approve_journal_entries))

    @action(detail=True, methods=['post'], url_path='sign-first')
    def sign_first(self, request, pk=None):
        """POST /api/v1/asset-signoffs/{id}/sign-first/ — manager signs."""
        from django.utils import timezone
        signoff = self.get_object()
        if not self._can_sign(request, 'first'):
            return Response({'error': 'Not authorised to sign this slot.'},
                            status=drf_status.HTTP_403_FORBIDDEN)
        if signoff.status in (AssetSignOff.Status.COMPLETED, AssetSignOff.Status.CANCELLED):
            return Response({'error': f'Sign-off is {signoff.status}.'},
                            status=drf_status.HTTP_400_BAD_REQUEST)
        if signoff.first_signed_by_id is not None:
            return Response({'error': 'First slot already signed.'},
                            status=drf_status.HTTP_400_BAD_REQUEST)

        s = AssetSignOffActionSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        signoff.first_signed_by = request.user
        signoff.first_signed_at = timezone.now()
        if 'counted_assets' in s.validated_data:
            signoff.counted_assets = s.validated_data['counted_assets']
        if 'discrepancies_text' in s.validated_data:
            signoff.discrepancies_text = s.validated_data['discrepancies_text']
        if s.validated_data.get('notes'):
            signoff.notes = (signoff.notes + '\n' + s.validated_data['notes']).strip()
        if signoff.second_signed_by_id is not None:
            signoff.status = AssetSignOff.Status.COMPLETED
        else:
            signoff.status = AssetSignOff.Status.PARTIALLY_SIGNED
        signoff.save(audit_user=request.user, audit_description='First sign-off')
        return Response(AssetSignOffSerializer(signoff).data)

    @action(detail=True, methods=['post'], url_path='sign-second')
    def sign_second(self, request, pk=None):
        """POST /api/v1/asset-signoffs/{id}/sign-second/ — finance manager / CFO signs."""
        from django.utils import timezone
        signoff = self.get_object()
        if not self._can_sign(request, 'second'):
            return Response({'error': 'Approver title (CFO / Finance Manager / Financial Controller) required.'},
                            status=drf_status.HTTP_403_FORBIDDEN)
        if signoff.status in (AssetSignOff.Status.COMPLETED, AssetSignOff.Status.CANCELLED):
            return Response({'error': f'Sign-off is {signoff.status}.'},
                            status=drf_status.HTTP_400_BAD_REQUEST)
        if signoff.second_signed_by_id is not None:
            return Response({'error': 'Second slot already signed.'},
                            status=drf_status.HTTP_400_BAD_REQUEST)
        if signoff.first_signed_by_id == getattr(request.user, 'id', None):
            return Response({'error': 'Segregation of duties: cannot provide both signatures.'},
                            status=drf_status.HTTP_400_BAD_REQUEST)

        s = AssetSignOffActionSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        signoff.second_signed_by = request.user
        signoff.second_signed_at = timezone.now()
        if 'counted_assets' in s.validated_data:
            signoff.counted_assets = s.validated_data['counted_assets']
        if 'discrepancies_text' in s.validated_data:
            signoff.discrepancies_text = s.validated_data['discrepancies_text']
        if s.validated_data.get('notes'):
            signoff.notes = (signoff.notes + '\n' + s.validated_data['notes']).strip()
        if signoff.first_signed_by_id is not None:
            signoff.status = AssetSignOff.Status.COMPLETED
        else:
            signoff.status = AssetSignOff.Status.PARTIALLY_SIGNED
        signoff.save(audit_user=request.user, audit_description='Second sign-off')
        return Response(AssetSignOffSerializer(signoff).data)
