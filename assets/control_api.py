"""
assets/control_api.py

REST API for the Asset Control & Handover module.

  GET/POST  /api/v1/asset-requisitions/
  POST      /api/v1/asset-requisitions/{id}/fm-approve/
  POST      /api/v1/asset-requisitions/{id}/cfo-approve/
  POST      /api/v1/asset-requisitions/{id}/reject/
  POST      /api/v1/asset-requisitions/{id}/cancel/
  GET       /api/v1/asset-requisitions/queue/          — my pending approvals

  GET/POST  /api/v1/asset-handovers/
  POST      /api/v1/asset-handovers/{id}/it-release/
  POST      /api/v1/asset-handovers/{id}/finance-record/
  POST      /api/v1/asset-handovers/{id}/accept/
  GET       /api/v1/asset-handovers/{id}/pdf/

  GET       /api/v1/asset-control/spare-pool/
  GET       /api/v1/asset-control/my-assets/
  GET       /api/v1/asset-control/reconciliation/
  GET/PATCH /api/v1/asset-control-policy/               — §11 threshold (CFO)

All writes are gated inside control_services.py, so every entry point enforces
the same controls.
"""
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status as drf_status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError

from core.mixins import UNRESOLVED, CompanyScopedViewSetMixin, _resolve_company_id
from core.models import Company, allowed_company_ids, get_user_profile

from . import control_services as svc
from .control_models import AssetControlPolicy, AssetHandover, AssetRequisition
from .control_serializers import (
    AssetControlPolicySerializer,
    AssetHandoverSerializer,
    AssetRequisitionCreateSerializer,
    AssetRequisitionSerializer,
    HandoverCreateSerializer,
    HandoverSignSerializer,
    RequisitionDecisionSerializer,
    RequisitionRejectSerializer,
)
from .models import Asset, AssetCategory, AssetSignOff
from .serializers import AssetListSerializer


def _err(exc):
    msg = exc.messages[0] if hasattr(exc, 'messages') and exc.messages else str(exc)
    return Response({'error': msg}, status=drf_status.HTTP_400_BAD_REQUEST)


def _company_allowed(request, company_id) -> bool:
    """True if the signed-in user may act within `company_id`."""
    allowed = allowed_company_ids(getattr(request, 'user', None))
    return allowed == {'*'} or (company_id is not None and str(company_id) in allowed)


def _scope_company_qs(request, qs, field='company_id'):
    """Restrict a queryset to the caller's entity (explicit ?company=, else the
    user's allowed set). Never returns another entity's rows, and never the whole
    group when no company is selected."""
    allowed = allowed_company_ids(getattr(request, 'user', None))
    cid = _resolve_company_id(request)
    if cid is UNRESOLVED:            # a bad ?company= value — show nothing
        return qs.none()
    if cid is not None:
        if allowed != {'*'} and str(cid) not in allowed:
            return qs.none()
        return qs.filter(**{field: cid})
    if allowed == {'*'}:
        return qs
    return qs.filter(**{f'{field}__in': list(allowed)})


class AssetRequisitionViewSet(CompanyScopedViewSetMixin, viewsets.ModelViewSet):
    """Requisitions are created + advanced through service actions only; they are
    never edited or deleted in place."""
    queryset = AssetRequisition.objects.select_related(
        'category', 'company', 'recipient', 'requested_by',
        'fm_approved_by', 'cfo_approved_by', 'spare_asset', 'resulting_asset',
    ).all()
    serializer_class = AssetRequisitionSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'post', 'head', 'options']

    def create(self, request, *args, **kwargs):
        from payroll.models import Employee
        s = AssetRequisitionCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data

        recipient = get_object_or_404(Employee, pk=d['recipient'])
        # The requisition belongs to the recipient's entity — never a company id
        # the caller passes. Enforce that the caller may act in that entity.
        company = recipient.company
        if company is None or not _company_allowed(request, company.id):
            return Response({'error': 'You cannot raise a requisition for this entity.'},
                            status=drf_status.HTTP_403_FORBIDDEN)
        category = get_object_or_404(AssetCategory, pk=d['category'])
        spare = None
        if d.get('spare_asset'):
            spare = get_object_or_404(Asset, pk=d['spare_asset'])
            if not _company_allowed(request, spare.company_id):
                return Response({'error': 'That spare asset is in another entity.'},
                                status=drf_status.HTTP_403_FORBIDDEN)

        try:
            req = svc.create_requisition(
                company=company, req_type=d['req_type'], category=category,
                description=d['description'], estimated_value=d.get('estimated_value') or 0,
                reason=d['reason'], recipient=recipient, user=request.user, spare_asset=spare,
            )
        except (DjangoValidationError, IntegrityError) as exc:
            return _err(exc)
        return Response(AssetRequisitionSerializer(req).data, status=drf_status.HTTP_201_CREATED)

    def _act(self, request, pk, fn, ser_cls=None, **extra):
        req = self.get_object()
        payload = {}
        if ser_cls is not None:
            s = ser_cls(data=request.data)
            s.is_valid(raise_exception=True)
            payload = s.validated_data
        try:
            fn(req, request.user, **{**payload, **extra})
        except (DjangoValidationError, IntegrityError) as exc:
            return _err(exc)
        req.refresh_from_db()
        return Response(AssetRequisitionSerializer(req).data)

    @action(detail=True, methods=['post'], url_path='fm-approve')
    def fm_approve(self, request, pk=None):
        return self._act(request, pk, lambda r, u, comment='': svc.fm_approve(r, u, comment),
                         ser_cls=RequisitionDecisionSerializer)

    @action(detail=True, methods=['post'], url_path='cfo-approve')
    def cfo_approve(self, request, pk=None):
        return self._act(request, pk, lambda r, u, comment='': svc.cfo_approve(r, u, comment),
                         ser_cls=RequisitionDecisionSerializer)

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        return self._act(request, pk, lambda r, u, reason='': svc.reject_requisition(r, u, reason),
                         ser_cls=RequisitionRejectSerializer)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        return self._act(request, pk, lambda r, u, reason='': svc.cancel_requisition(r, u, reason),
                         ser_cls=RequisitionRejectSerializer)

    @action(detail=False, methods=['get'])
    def queue(self, request):
        """Requisitions awaiting THIS user's approval decision."""
        qs = self.filter_queryset(self.get_queryset())
        pending = []
        for req in qs.filter(status__in=[
            AssetRequisition.Status.PENDING_FM_APPROVAL,
            AssetRequisition.Status.PENDING_CFO_APPROVAL,
        ]):
            if req.status == AssetRequisition.Status.PENDING_FM_APPROVAL and svc.can_approve_as_fm(request.user):
                pending.append(req)
            elif req.status == AssetRequisition.Status.PENDING_CFO_APPROVAL and svc.can_approve_as_cfo(request.user):
                pending.append(req)
        return Response({'requisitions': AssetRequisitionSerializer(pending, many=True).data})


class AssetHandoverViewSet(CompanyScopedViewSetMixin, viewsets.ModelViewSet):
    queryset = AssetHandover.objects.select_related(
        'requisition', 'asset', 'asset__category', 'recipient',
        'it_released_by', 'finance_recorded_by', 'employee_accepted_by',
    ).all()
    serializer_class = AssetHandoverSerializer
    permission_classes = [IsAuthenticated]
    company_lookup_field = 'requisition__company_id'
    http_method_names = ['get', 'post', 'head', 'options']

    def create(self, request, *args, **kwargs):
        s = HandoverCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        req = get_object_or_404(AssetRequisition, pk=d['requisition'])
        asset = get_object_or_404(Asset, pk=d['asset'])
        if not _company_allowed(request, req.company_id) or not _company_allowed(request, asset.company_id):
            return Response({'error': 'That requisition or asset is in another entity.'},
                            status=drf_status.HTTP_403_FORBIDDEN)
        if req.company_id != asset.company_id:
            return Response({'error': 'The asset and the requisition are in different entities.'},
                            status=drf_status.HTTP_403_FORBIDDEN)
        try:
            ho = svc.create_handover(
                req, asset=asset, user=request.user,
                condition_on_issue=d.get('condition_on_issue', ''),
                accessories=d.get('accessories', ''),
            )
        except (DjangoValidationError, IntegrityError) as exc:
            return _err(exc)
        return Response(AssetHandoverSerializer(ho).data, status=drf_status.HTTP_201_CREATED)

    def _sign(self, request, fn):
        ho = self.get_object()
        s = HandoverSignSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            fn(ho, request.user, s.validated_data.get('signature', ''))
        except (DjangoValidationError, IntegrityError) as exc:
            return _err(exc)
        ho.refresh_from_db()
        return Response(AssetHandoverSerializer(ho).data)

    @action(detail=True, methods=['post'], url_path='it-release')
    def it_release(self, request, pk=None):
        return self._sign(request, svc.handover_it_release)

    @action(detail=True, methods=['post'], url_path='finance-record')
    def finance_record(self, request, pk=None):
        return self._sign(request, svc.handover_finance_record)

    @action(detail=True, methods=['post'])
    def accept(self, request, pk=None):
        return self._sign(request, svc.handover_employee_accept)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        ho = self.get_object()
        reason = request.data.get('reason', '')
        try:
            svc.cancel_handover(ho, request.user, reason)
        except (DjangoValidationError, IntegrityError) as exc:
            return _err(exc)
        ho.refresh_from_db()
        return Response(AssetHandoverSerializer(ho).data)

    @action(detail=True, methods=['get'])
    def pdf(self, request, pk=None):
        from .control_pdf import render_handover_pdf
        ho = self.get_object()
        data = render_handover_pdf(ho)
        resp = HttpResponse(data, content_type='application/pdf')
        resp['Content-Disposition'] = f'inline; filename="{ho.handover_number}.pdf"'
        return resp


class AssetControlPolicyViewSet(viewsets.ViewSet):
    """§11 approval-tiering threshold. Read by any authenticated user; only the
    CFO may change it."""
    permission_classes = [IsAuthenticated]

    def list(self, request):
        return Response(AssetControlPolicySerializer(AssetControlPolicy.current()).data)

    def partial_update(self, request, pk=None):
        if not svc.can_approve_as_cfo(request.user):
            return Response({'error': 'Only the CFO can change the approval threshold.'},
                            status=drf_status.HTTP_403_FORBIDDEN)
        policy = AssetControlPolicy.current()
        s = AssetControlPolicySerializer(policy, data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        s.save()
        return Response(s.data)


class SparePoolView(APIView):
    """Returned/spare assets available for reissue — only via a new requisition."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = Asset.objects.filter(custody_status=Asset.CustodyStatus.RETURNED_SPARE)
        qs = _scope_company_qs(request, qs).select_related('category', 'company').order_by('tag_number')
        return Response({'assets': AssetListSerializer(qs, many=True).data})


class SparePoolUploadView(APIView):
    """CSV/Excel bulk upload of spare assets into the pool."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        import csv
        import io
        f = request.FILES.get('file')
        if not f:
            return Response({'error': 'No file uploaded.'}, status=drf_status.HTTP_400_BAD_REQUEST)

        name = (f.name or '').lower()
        if name.endswith(('.xlsx', '.xls')):
            try:
                import openpyxl
                wb = openpyxl.load_workbook(f, read_only=True, data_only=True)
                ws = wb.active
                rows = list(ws.iter_rows(values_only=True))
                if not rows:
                    return Response({'created': 0, 'errors': ['Empty file.']})
                header = [str(c or '').strip().lower() for c in rows[0]]
                data_rows = [dict(zip(header, r)) for r in rows[1:]]
            except Exception as exc:
                return Response({'error': f'Could not read Excel file: {exc}'},
                                status=drf_status.HTTP_400_BAD_REQUEST)
        else:
            text = f.read().decode('utf-8-sig')
            reader = csv.DictReader(io.StringIO(text))
            data_rows = list(reader)
            header = [h.strip().lower() for h in (reader.fieldnames or [])]

        cid = _resolve_company_id(request)
        company = Company.objects.filter(pk=cid).first() if cid else Company.objects.first()
        if not company:
            return Response({'error': 'No company resolved.'},
                            status=drf_status.HTTP_400_BAD_REQUEST)

        created = 0
        errors = []
        for i, row in enumerate(data_rows, start=2):
            row = {str(k).strip().lower(): str(v or '').strip() for k, v in row.items() if k}
            tag = row.get('tag') or row.get('tag_number') or row.get('asset tag') or ''
            asset_name = row.get('name') or row.get('asset name') or ''
            cat_name = row.get('category') or ''
            location = row.get('location') or ''
            condition = (row.get('condition') or 'unknown').lower()
            if condition not in ('functional', 'broken', 'unknown'):
                condition = 'unknown'

            if not tag or not asset_name:
                errors.append(f'Row {i}: missing tag or name.')
                continue

            if Asset.objects.filter(tag_number=tag).exists():
                errors.append(f'Row {i}: tag {tag} already exists.')
                continue

            cat = AssetCategory.objects.filter(name__iexact=cat_name).first()
            if not cat:
                cat = AssetCategory.objects.first()
            if not cat:
                errors.append(f'Row {i}: no asset category found.')
                continue

            from decimal import Decimal
            Asset.objects.create(
                tag_number=tag, name=asset_name, company=company, category=cat,
                location=location, condition=condition,
                custody_status=Asset.CustodyStatus.RETURNED_SPARE,
                cost=Decimal('0'), salvage_value=Decimal('0'),
                useful_life_months=60,
                purchase_date='2026-01-01', in_service_date='2026-01-01',
            )
            created += 1

        return Response({'created': created, 'errors': errors})




class MyAssetsView(APIView):
    """The assets the signed-in user currently holds + signs for."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        emp = getattr(request.user, 'employee_record', None)
        if emp is None:
            return Response({'assets': [], 'employee': None})
        qs = (Asset.objects
              .filter(custodian_employee_id=emp.id,
                      custody_status__in=[Asset.CustodyStatus.ISSUED, Asset.CustodyStatus.IN_USE])
              .select_related('category', 'company').order_by('tag_number'))
        return Response({
            'employee': {'id': str(emp.id), 'full_name': emp.full_name},
            'assets': AssetListSerializer(qs, many=True).data,
        })


class AssetCountReconciliationView(APIView):
    """AC7 — the register export ties out to the quarterly physical count.

    Compares the live register (assets expected to be present) against the most
    recent completed physical count (assets.AssetSignOff, semi-annual count).
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        reg = _scope_company_qs(
            request, Asset.objects.exclude(custody_status=Asset.CustodyStatus.RETIRED))
        register_count = reg.count()

        counts = _scope_company_qs(request, AssetSignOff.objects.filter(
            kind=AssetSignOff.Kind.SEMI_ANNUAL_COUNT,
            status=AssetSignOff.Status.COMPLETED,
        ))
        latest = counts.order_by('-due_date', '-created_at').first()

        counted = latest.counted_assets if latest else None
        variance = (register_count - counted) if counted is not None else None
        LIMIT = 2000
        return Response({
            'register_count': register_count,
            'counted_assets': counted,
            'variance': variance,
            'ties_out': (variance == 0) if variance is not None else None,
            'count_period': latest.period_label if latest else None,
            'count_completed_at': latest.second_signed_at if latest else None,
            'discrepancies': latest.discrepancies_text if latest else '',
            'truncated': register_count > LIMIT,
            'register': AssetListSerializer(
                reg.select_related('category', 'company').order_by('tag_number')[:LIMIT],
                many=True,
            ).data,
        })
