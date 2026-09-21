"""Supplier Payables Reconciliation — API.

Mounted at ``/api/v1/supplier-recon/`` (see alpha_finance/api_router.py):

    GET    runs/                          list runs (entity-scoped)
    POST   runs/build/                    build/refresh  {company, period_label}
    GET    runs/{id}/                     run header
    GET    runs/{id}/dashboard/           KPI tiles, categories, ageing, blockers
    GET    runs/{id}/blockers/            what stands between here and sign-off
    GET    runs/{id}/export/              the month as an Excel workbook
    POST   runs/{id}/finalise/            lock the month  (reviewer)
    POST   runs/{id}/reopen/              unlock          (reviewer, needs reason)
    GET    lines/?run=                    per-supplier positions
    GET    items/?run=&line=&…            bill items (filters below)
    GET    items/{id}/                    one bill + its decision trail
    POST   items/{id}/action/             record a decision
    POST   items/{id}/escalate/           raise an escalation
    POST   escalations/{id}/resolve/      close out          (reviewer)
    GET    reason-codes/
    GET/POST/PATCH supplier-profiles/     classify suppliers

Every queryset is filtered to the entities the caller is permitted in, so a run
cannot be read across legal entities by guessing its id.
"""

import uuid

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Count, Q, Sum
from rest_framework import status as http, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from core.models import Company
from reporting.views import _parse_company

from . import services
from .export import workbook_response
from .constants import (
    AGEING_BUCKETS,
    CLAIM_BACKED_CATEGORIES,
    EscalationStatus,
    LedgerStage,
    NOT_FULLY_PAID,
    OVERDUE_ESCALATION_DAYS,
    PaymentStatus,
    ZERO,
)
from .models import (
    Escalation,
    InvoiceReconItem,
    ReasonCode,
    ReconSupplierProfile,
    SupplierReconLine,
    SupplierReconRun,
)
from .permissions import (
    CanPrepareRecon,
    CanReviewRecon,
    CanViewRecon,
    can_access_company,
    scope_to_allowed_companies,
)
from .serializers import (
    EscalationSerializer,
    InvoiceReconItemDetailSerializer,
    InvoiceReconItemSerializer,
    ReasonCodeSerializer,
    ReconSupplierProfileSerializer,
    SupplierReconLineSerializer,
    SupplierReconRunSerializer,
)


def _reraise(exc):
    """Turn a Django ValidationError into a clean DRF 400."""
    if hasattr(exc, 'message_dict'):
        raise ValidationError(exc.message_dict)
    raise ValidationError(exc.messages if hasattr(exc, 'messages') else str(exc))


class SupplierReconRunViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = SupplierReconRunSerializer
    permission_classes = [CanPrepareRecon]

    def get_queryset(self):
        qs = (SupplierReconRun.objects
              .select_related('company', 'prepared_by', 'reviewed_by'))
        qs = scope_to_allowed_companies(qs, self.request.user)
        params = self.request.query_params
        if company := _parse_company(self.request):
            qs = qs.filter(company_id=company)
        if run_status := params.get('status'):
            qs = qs.filter(status=run_status)
        if period := params.get('period'):
            qs = qs.filter(period_label=period)
        return qs

    @action(detail=False, methods=['post'])
    def build(self, request):
        company_id = (request.data.get('company')
                      or request.data.get('company_id') or '')
        period_label = (request.data.get('period_label') or '').strip()
        if not company_id or not period_label:
            raise ValidationError('company and period_label are required.')

        # Company.id is a UUID. Passing a CODE (e.g. "ADIC") makes the pk
        # lookup raise at queryset evaluation, which no exception handler
        # catches - a 500 - and the code fallback below was unreachable.
        company = None
        try:
            uuid.UUID(str(company_id))
        except (ValueError, AttributeError, TypeError):
            pass
        else:
            company = Company.objects.filter(pk=company_id).first()
        if company is None:
            company = Company.objects.filter(code__iexact=str(company_id)).first()
        if company is None:
            raise ValidationError(f"Unknown company '{company_id}'.")
        if not can_access_company(request.user, company.id):
            raise PermissionDenied('You are not permitted in that entity.')

        try:
            run = services.build_recon_run(company, period_label,
                                          prepared_by=request.user,
                                          audit_user=request.user)
        except DjangoValidationError as exc:
            _reraise(exc)
        return Response(SupplierReconRunSerializer(run).data,
                        status=http.HTTP_201_CREATED)

    @action(detail=True, methods=['get'])
    def dashboard(self, request, pk=None):
        # group (redesign S3): 'all' | 'claims' | 'operational' — scopes the
        # "Still owed, by age" cards to claims-related or operational spend.
        group = (request.query_params.get('group') or 'all').lower()
        return Response(_dashboard_payload(self.get_object(), group=group))

    @action(detail=True, methods=['get'])
    def export(self, request, pk=None):
        """Stream the month as an Excel workbook for the month-end pack."""
        return workbook_response(self.get_object())

    @action(detail=True, methods=['get'])
    def blockers(self, request, pk=None):
        run = self.get_object()
        problems = services.blocking_exceptions(run)
        return Response({'count': len(problems), 'items': problems})

    @action(detail=True, methods=['post'], permission_classes=[CanReviewRecon])
    def finalise(self, request, pk=None):
        run = self.get_object()
        try:
            run = services.finalise_run(run, request.user)
        except DjangoValidationError as exc:
            _reraise(exc)
        return Response(SupplierReconRunSerializer(run).data)

    @action(detail=True, methods=['post'], permission_classes=[CanReviewRecon])
    def reopen(self, request, pk=None):
        run = self.get_object()
        try:
            run = services.reopen_run(run, request.user,
                                     reason=request.data.get('reason', ''))
        except DjangoValidationError as exc:
            _reraise(exc)
        return Response(SupplierReconRunSerializer(run).data)


class SupplierReconLineViewSet(viewsets.ModelViewSet):
    """Read-only apart from reassigning the accountable clerk."""

    serializer_class = SupplierReconLineSerializer
    permission_classes = [CanPrepareRecon]
    http_method_names = ['get', 'patch', 'head', 'options']

    def get_queryset(self):
        qs = (SupplierReconLine.objects
              .select_related('supplier', 'run', 'run__company', 'assigned_to'))
        qs = scope_to_allowed_companies(qs, self.request.user, field='run__company_id')
        params = self.request.query_params
        if run_id := params.get('run'):
            qs = qs.filter(run_id=run_id)
        if line_status := params.get('status'):
            qs = qs.filter(status=line_status)
        if category := params.get('category'):
            qs = qs.filter(category=category)
        return qs


class InvoiceReconItemViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [CanPrepareRecon]

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return InvoiceReconItemDetailSerializer
        return InvoiceReconItemSerializer

    def get_queryset(self):
        qs = (InvoiceReconItem.objects
              .select_related('invoice', 'invoice__currency_code', 'reason_code',
                              'line', 'line__supplier', 'line__run',
                              'purchase_order', 'goods_receipt',
                              'actioned_by', 'actioned_by__profile')
              .prefetch_related('escalations'))
        qs = scope_to_allowed_companies(qs, self.request.user,
                                       field='line__run__company_id')
        params = self.request.query_params
        if line_id := params.get('line'):
            qs = qs.filter(line_id=line_id)
        if run_id := params.get('run'):
            qs = qs.filter(line__run_id=run_id)
        if ps := params.get('payment_status'):
            qs = qs.filter(payment_status=ps)
        if ms := params.get('match_status'):
            qs = qs.filter(match_status=ms)
        if params.get('open') == 'true':
            qs = qs.filter(payment_status__in=list(NOT_FULLY_PAID))
        if params.get('overdue') == 'true':
            from django.utils import timezone
            qs = (qs.exclude(payment_status=PaymentStatus.PAID)
                  .filter(due_date__lt=timezone.localdate()))
        if params.get('unactioned') == 'true':
            qs = qs.filter(actioned=False)
        # "Invoices actioned" (redesign S1): a bill counts as actioned when it
        # has an invoice number captured AND no reason is still outstanding —
        # i.e. it has been actioned or is fully paid. Both conditions, per spec.
        if params.get('invoiced_actioned') == 'true':
            qs = (qs.exclude(invoice__invoice_number='')
                    .exclude(invoice__invoice_number__isnull=True)
                    .filter(Q(actioned=True) | Q(payment_status=PaymentStatus.PAID)))
        if params.get('escalated') == 'true':
            qs = qs.filter(escalations__status__in=[EscalationStatus.OPEN,
                                                   EscalationStatus.ACKNOWLEDGED]
                           ).distinct()
        return qs

    # url_path keeps the endpoint at items/{id}/action/ while the method name
    # stays clear of the imported @action decorator.
    @action(detail=True, methods=['post'], url_path='action')
    def record_action(self, request, pk=None):
        item = self.get_object()
        reason_code = None
        if rc := request.data.get('reason_code'):
            reason_code = ReasonCode.objects.filter(pk=rc, active=True).first()
            if reason_code is None:
                raise ValidationError({'reason_code': 'Unknown or inactive reason code.'})
        hold = request.data.get('hold')
        if isinstance(hold, str):
            hold = {'true': True, 'false': False}.get(hold.lower())
        try:
            item = services.action_item(
                item, request.user,
                reason_code=reason_code,
                justification=request.data.get('justification', ''),
                hold=hold,
            )
        except DjangoValidationError as exc:
            _reraise(exc)
        return Response(InvoiceReconItemDetailSerializer(item).data)

    @action(detail=True, methods=['post'])
    def escalate(self, request, pk=None):
        item = self.get_object()
        try:
            esc = services.raise_escalation(
                item, request.user, request.data.get('justification', ''),
            )
        except DjangoValidationError as exc:
            _reraise(exc)
        return Response(EscalationSerializer(esc).data,
                        status=http.HTTP_201_CREATED)


class EscalationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = EscalationSerializer
    permission_classes = [CanPrepareRecon]

    def get_queryset(self):
        qs = Escalation.objects.select_related(
            'item__line__run', 'item__invoice', 'raised_by')
        qs = scope_to_allowed_companies(qs, self.request.user,
                                       field='item__line__run__company_id')
        params = self.request.query_params
        if run_id := params.get('run'):
            qs = qs.filter(item__line__run_id=run_id)
        if esc_status := params.get('status'):
            qs = qs.filter(status=esc_status)
        return qs

    @action(detail=True, methods=['post'], permission_classes=[CanReviewRecon])
    def resolve(self, request, pk=None):
        esc = self.get_object()
        try:
            esc = services.resolve_escalation(
                esc, request.user,
                status=(request.data.get('status') or '').strip(),
                note=request.data.get('note', ''),
            )
        except DjangoValidationError as exc:
            _reraise(exc)
        return Response(EscalationSerializer(esc).data)


class ReasonCodeViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ReasonCode.objects.filter(active=True)
    serializer_class = ReasonCodeSerializer
    permission_classes = [CanViewRecon]
    pagination_class = None


class ReconSupplierProfileViewSet(viewsets.ModelViewSet):
    serializer_class = ReconSupplierProfileSerializer
    permission_classes = [CanPrepareRecon]

    def get_queryset(self):
        qs = ReconSupplierProfile.objects.select_related('contact')
        qs = scope_to_allowed_companies(qs, self.request.user,
                                       field='contact__company_id')
        params = self.request.query_params
        if category := params.get('category'):
            qs = qs.filter(category=category)
        if (scope := params.get('in_scope')) is not None:
            qs = qs.filter(in_scope=str(scope).lower() == 'true')
        return qs

    def perform_create(self, serializer):
        contact = serializer.validated_data.get('contact')
        if contact is not None and contact.company_id and \
                not can_access_company(self.request.user, contact.company_id):
            raise PermissionDenied('You are not permitted in that entity.')
        serializer.save(audit_user=self.request.user)

    def perform_update(self, serializer):
        # Same entity gate as create - a PATCH could otherwise re-point a
        # profile at a contact in an entity the caller cannot access.
        contact = serializer.validated_data.get('contact')
        if contact is not None and contact.company_id and \
                not can_access_company(self.request.user, contact.company_id):
            raise PermissionDenied('You are not permitted in that entity.')
        serializer.save(audit_user=self.request.user)


# ---------------------------------------------------------------------------
# Dashboard payload
# ---------------------------------------------------------------------------

def _dashboard_payload(run: SupplierReconRun, group: str = 'all') -> dict:
    """KPI tiles + per-category split + ageing of what is still owed.

    ``group`` ('all' | 'claims' | 'operational') scopes ONLY the ageing cards
    (redesign S3) — the tiles, Exceptions and category breakdown stay whole-run,
    so toggling the group recalculates the age cards without silently moving the
    headline figures.
    """
    items = (InvoiceReconItem.objects
             .filter(line__run=run)
             .select_related('reason_code'))

    by_category = list(
        run.lines.values('category')
        .annotate(invoiced=Sum('invoiced'), paid=Sum('paid'),
                  unpaid=Sum('unpaid'), held=Sum('held'),
                  suppliers=Count('id'))
        .order_by('-invoiced')
    )

    # Ageing is of the OUTSTANDING balance, bucketed on the same day-ranges the
    # AP Aging report uses, so the two surfaces agree.
    open_items = items.exclude(payment_status=PaymentStatus.PAID)

    # Overdue counts feed the Exceptions panel — always whole-run, never scoped.
    overdue_count = 0
    overdue_30_count = 0
    for item in open_items:
        if item.is_overdue:
            overdue_count += 1
            if item.days_past_due >= OVERDUE_ESCALATION_DAYS:
                overdue_30_count += 1

    # Ageing is scoped to the selected group (S3): claims-related vs operational.
    ageing_items = open_items
    if group == 'claims':
        ageing_items = open_items.filter(line__category__in=CLAIM_BACKED_CATEGORIES)
    elif group == 'operational':
        ageing_items = open_items.exclude(line__category__in=CLAIM_BACKED_CATEGORIES)
    ageing = {key: ZERO for key, *_ in AGEING_BUCKETS}
    for item in ageing_items:
        ageing[item.ageing_bucket] = ageing[item.ageing_bucket] + item.amount_outstanding

    by_reason = list(
        items.filter(payment_status__in=list(NOT_FULLY_PAID),
                     reason_code__isnull=False)
        .values('reason_code__code', 'reason_code__label')
        .annotate(bills=Count('id'), amount=Sum('amount'))
        .order_by('-amount')
    )

    open_escalations = Escalation.objects.filter(
        item__line__run=run,
        status__in=[EscalationStatus.OPEN, EscalationStatus.ACKNOWLEDGED],
    ).count()

    unclassified = list(services.unclassified_vendors(
        run.company, run.period_start, run.period_end)[:25])

    return {
        'run': SupplierReconRunSerializer(run).data,
        'kpis': {
            'total_invoiced': run.total_invoiced,
            'total_paid': run.total_paid,
            'total_unpaid': run.total_unpaid,
            'total_held': run.total_held,
            'total_escalated': run.total_escalated,
            'total_not_posted': run.total_not_posted,
            'pct_paid': run.pct_paid,
            'invoice_count': items.count(),
            'supplier_count': run.lines.count(),
            'unactioned_count': items.filter(actioned=False).count(),
            'overdue_count': overdue_count,
            'overdue_30_count': overdue_30_count,
            'open_escalations': open_escalations,
            'unmatched_count': items.exclude(match_status='matched').count(),
            'no_claim_count': items.filter(match_status='no_claim').count(),
            'no_po_count': items.filter(match_status='no_po').count(),
            'not_posted_count': items.filter(
                ledger_stage=LedgerStage.DRAFT).count(),
        },
        'by_category': by_category,
        'by_reason': by_reason,
        'ageing': ageing,
        'ageing_group': group,
        # Single source of truth for the claims/operational split — the UI reads
        # this instead of hard-copying the set, so the client cannot drift from
        # the server the day a category is added (H96).
        'claim_categories': sorted(CLAIM_BACKED_CATEGORIES),
        'unclassified_vendors': unclassified,
    }
