"""procurement/api_views.py — DRF endpoints for POs, GRNs, and matches."""

from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.mixins import CompanyScopedViewSetMixin
from . import services
from .models import (
    GoodsReceiptNote,
    POBillMatch,
    PurchaseOrder,
    PurchaseOrderLine,
    VendorBankAccount,
)
from .serializers import (
    GoodsReceiptNoteCreateSerializer,
    GoodsReceiptNoteDetailSerializer,
    GoodsReceiptNoteListSerializer,
    POBillMatchSerializer,
    PurchaseOrderCreateSerializer,
    PurchaseOrderDetailSerializer,
    PurchaseOrderListSerializer,
    VendorBankAccountCreateSerializer,
    VendorBankAccountDetailSerializer,
    VendorBankAccountListSerializer,
)


def _err(detail, code=status.HTTP_400_BAD_REQUEST):
    return Response({'detail': detail}, status=code)


# ?status= aliases that split the shared PENDING_FM_APPROVAL leg by who
# actually approves it. True = the claims-senior side, False = the FM side.
_PENDING_FM_SPLIT = {
    'pending_fm_finance':      False,
    'pending_claims_approval': True,
}


# ---------------------------------------------------------------------------
# Purchase Orders
# ---------------------------------------------------------------------------

class PurchaseOrderViewSet(CompanyScopedViewSetMixin, viewsets.ModelViewSet):
    queryset = (
        PurchaseOrder.objects
        .select_related('supplier', 'company', 'currency_code', 'fiscal_period')
        .prefetch_related('lines__account')
    )
    permission_classes = [IsAuthenticated]
    lookup_field = 'pk'

    def get_serializer_class(self):
        if self.action == 'list':
            return PurchaseOrderListSerializer
        if self.action in ('create', 'update', 'partial_update'):
            return PurchaseOrderCreateSerializer
        return PurchaseOrderDetailSerializer

    def get_queryset(self):
        # Start from the mixin chain: CompanyScopedViewSetMixin resolves
        # ?company= (UUID *or* code like 'ADIC') and applies the
        # allowed-companies gate. The previous version started from
        # self.queryset, which (a) bypassed that security gate entirely and
        # (b) re-filtered company_id=<raw param>, 500ing on code values.
        qs = super().get_queryset()
        params = self.request.query_params
        if params.get('status'):
            # Kago (FM) 2026-07-25: the operational leg of BOTH a finance PO and
            # a claims PO sits in the single PENDING_FM_APPROVAL status, so
            # ?status=pending_fm_approval returned the FM's queue AND the Claims
            # Manager's queue mixed together — unusable with 5 000+ claims POs.
            # Two split aliases scope it to the right approver's side. The raw
            # value still means "both legs" for existing callers.
            status_param = params['status']
            if status_param in _PENDING_FM_SPLIT:
                qs = qs.filter(status=PurchaseOrder.Status.PENDING_FM_APPROVAL)
                if _PENDING_FM_SPLIT[status_param]:
                    qs = qs.filter(department=PurchaseOrder.Department.CLAIMS)
                else:
                    qs = qs.exclude(department=PurchaseOrder.Department.CLAIMS)
            else:
                qs = qs.filter(status=status_param)
        if params.get('department'):
            qs = qs.filter(department=params['department'])
        if params.get('supplier'):
            qs = qs.filter(supplier_id=params['supplier'])
        if params.get('search'):
            term = params['search']
            qs = qs.filter(po_number__icontains=term) | qs.filter(supplier__name__icontains=term)
        # Issue-date range filter (CFO directive 2026-05-17 — quarterly reviews
        # and FY-end cuts need the list scoped to a window). Accepts ISO dates.
        if params.get('issue_date_from'):
            qs = qs.filter(issue_date__gte=params['issue_date_from'])
        if params.get('issue_date_to'):
            qs = qs.filter(issue_date__lte=params['issue_date_to'])
        return qs.order_by('-issue_date', '-po_number')

    def perform_destroy(self, instance):
        # Fable audit 2026-07-09: destroy was ungated — any user could delete an
        # APPROVED PO (orphaning its commitment JE) or a REJECTED PO (destroying
        # the control record). Only a DRAFT PO with no commitment JE may be
        # deleted; everything else is an audit record — cancel/reject instead.
        from rest_framework.exceptions import ValidationError as DRFValidationError
        if (instance.status != PurchaseOrder.Status.DRAFT
                or instance.commitment_journal_entry_id):
            raise DRFValidationError(
                "Only a draft PO can be deleted. Approved or processed POs are "
                "locked as audit records — cancel or reject them instead.")
        instance.delete()

    def update(self, request, *args, **kwargs):
        # CFO directive 2026-07-08 (Lemogang): people make mistakes raising a PO
        # and must be able to fix them BEFORE approval. Editable while DRAFT or
        # still in the approval queue; locked once APPROVED (or any terminal
        # state) as the control — corrections then go through the amendment flow.
        #
        # Safety the old DRAFT-only lock protected: no approval must ever be
        # retained on figures an approver did not see, and no commitment JE
        # exists until approval. So amending a SUBMITTED PO resets it to DRAFT
        # and voids the in-flight approval stamps — it must be re-submitted and
        # re-approved on the corrected figures.
        po = self.get_object()
        EDITABLE = {
            PurchaseOrder.Status.DRAFT,
            PurchaseOrder.Status.PENDING_FM_APPROVAL,
            PurchaseOrder.Status.PENDING_CFO_APPROVAL,
        }
        if po.status not in EDITABLE:
            return _err(
                f"This PO is {po.status} and can no longer be edited. Approved "
                "POs are locked as a control — raise a PO amendment instead.",
                code=status.HTTP_409_CONFLICT,
            )
        # Fable audit 2026-07-08: the status reset + the amend must be ONE
        # transaction. Otherwise a validation failure inside super().update()
        # (e.g. a bad exchange rate) 400s AFTER the de-approval already
        # committed — the PO silently drops out of the approval queue with no
        # successful edit. Wrapping in atomic rolls the reset back on any raise.
        with transaction.atomic():
            if po.status != PurchaseOrder.Status.DRAFT:
                po.status = PurchaseOrder.Status.DRAFT
                po.submitted_by = None
                po.submitted_at = None
                po.fm_approved_by = None
                po.fm_approved_at = None
                po.cfo_approved_by = None
                po.cfo_approved_at = None
                po.save(
                    audit_user=request.user,
                    audit_description=(
                        f"PO {po.po_number} reopened to DRAFT for amendment "
                        "(in-flight approval voided; must be re-submitted)"
                    ),
                )
            return super().update(request, *args, **kwargs)

    @action(detail=False, methods=['get'], url_path='supplier-history')
    def supplier_history(self, request):
        """Recent line items bought from a supplier — powers the new-PO
        'reuse a past item' quick-fill (CFO 2026-07-14). Company-scoped through
        the same allowed-companies gate as the list (super().get_queryset()), so
        a user only ever sees history for entities they may see. Deduped by
        description, most-recent price wins; no GL account is returned (POs carry
        no GL line — CFO 2026-05-21)."""
        supplier_id = (request.query_params.get('supplier') or '').strip()
        if not supplier_id:
            return _err('supplier is required.')
        try:
            pos = (
                self.get_queryset()
                .filter(supplier_id=supplier_id)
                .order_by('-issue_date', '-created_at')[:25]
            )
            pos = list(pos)
        except (DjangoValidationError, ValueError, TypeError):
            # A malformed supplier id (bad UUID) must not 500 the form.
            return Response({'supplier': supplier_id, 'items': []})

        items, seen = [], set()
        for po in pos:
            for ln in po.lines.all():
                desc = (ln.description or '').strip()
                key = desc.lower()
                # Skip blanks and negative/excess lines (a client's excess
                # contribution is claim-specific, never a reusable catalogue item).
                if not desc or key in seen or (ln.unit_price or 0) < 0:
                    continue
                seen.add(key)
                items.append({
                    'description':    desc,
                    'unit_price':     str(ln.unit_price),
                    'tax_code':       str(ln.tax_code_id) if ln.tax_code_id else '',
                    'last_po_number': po.po_number,
                    'last_issue_date': po.issue_date.isoformat() if po.issue_date else None,
                })
                if len(items) >= 15:
                    break
            if len(items) >= 15:
                break
        return Response({'supplier': supplier_id, 'items': items})

    @action(detail=True, methods=['post'])
    def submit(self, request, pk=None):
        po = self.get_object()
        try:
            services.submit_for_approval(po, request.user)
        except DjangoValidationError as e:
            return _err(e.messages if hasattr(e, 'messages') else str(e))
        return Response(PurchaseOrderDetailSerializer(po, context=self.get_serializer_context()).data)

    @action(detail=True, methods=['post'], url_path='fm-approve')
    def fm_approve(self, request, pk=None):
        po = self.get_object()
        try:
            services.fm_approve(po, request.user)
        except DjangoValidationError as e:
            return _err(e.messages if hasattr(e, 'messages') else str(e))
        return Response(PurchaseOrderDetailSerializer(po, context=self.get_serializer_context()).data)

    @action(detail=True, methods=['post'], url_path='cfo-approve')
    def cfo_approve(self, request, pk=None):
        po = self.get_object()
        try:
            services.cfo_approve(po, request.user)
        except DjangoValidationError as e:
            return _err(e.messages if hasattr(e, 'messages') else str(e))
        return Response(PurchaseOrderDetailSerializer(po, context=self.get_serializer_context()).data)

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        po     = self.get_object()
        reason = request.data.get('reason', '')
        try:
            services.reject(po, request.user, reason)
        except DjangoValidationError as e:
            return _err(e.messages if hasattr(e, 'messages') else str(e))
        return Response(PurchaseOrderDetailSerializer(po, context=self.get_serializer_context()).data)

    @action(detail=False, methods=['get'], url_path='export-csv')
    def export_csv(self, request):
        """GET /api/v1/purchase-orders/export-csv/?status=approved&...
        Streams a CSV of the same query the list view honours.
        CFO directive 2026-05-21 (Outlook 'downlod purchase orders'):
        the PO list page had no bulk download, users were stuck."""
        import csv
        from django.http import StreamingHttpResponse
        qs = self.filter_queryset(self.get_queryset())

        class _Echo:
            def write(self, value):
                return value
        writer = csv.writer(_Echo())

        def rows():
            yield writer.writerow([
                'PO Number', 'Status', 'Department', 'Supplier',
                'Issue Date', 'Expected Delivery', 'Currency',
                'Total', 'Total (BWP)', 'Company',
                'FM Approved By', 'CFO Approved By',
            ])
            for po in qs.iterator(chunk_size=200):
                yield writer.writerow([
                    po.po_number, po.status, po.department,
                    po.supplier.name if po.supplier_id else '',
                    po.issue_date.isoformat() if po.issue_date else '',
                    po.expected_delivery_date.isoformat() if po.expected_delivery_date else '',
                    po.currency_code_id or '',
                    str(po.total_amount or 0),
                    str(po.total_bwp or 0),
                    po.company.code if po.company_id else '',
                    po.fm_approved_by.username if po.fm_approved_by_id else '',
                    po.cfo_approved_by.username if po.cfo_approved_by_id else '',
                ])
        resp = StreamingHttpResponse(rows(), content_type='text/csv')
        status_label = request.query_params.get('status', 'all')
        resp['Content-Disposition'] = f'attachment; filename="purchase_orders_{status_label}.csv"'
        return resp

    @action(detail=False, methods=['get'], url_path='export-zip')
    def export_zip(self, request):
        """GET /api/v1/purchase-orders/export-zip/?status=approved&...
        Zips PO PDFs for the filtered list. Caps at 200 rows to keep memory bounded
        and the response under ~50 MB; the CFO uses the list filters to narrow first."""
        import io, zipfile
        from django.http import HttpResponse
        from .pdf import generate_po_pdf

        qs = self.filter_queryset(self.get_queryset())[:200]
        buf = io.BytesIO()
        included = []
        errors = []
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for po in qs:
                try:
                    pdf = generate_po_pdf(po)
                    zf.writestr(f'{po.po_number}.pdf', pdf)
                    included.append(po.po_number)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f'{po.po_number}: {exc}')
            manifest = (
                f'Exported {len(included)} PO(s).\n\n'
                + '\n'.join(f'OK  {n}' for n in included)
                + ('\n\nErrors:\n' + '\n'.join(errors) if errors else '')
            )
            zf.writestr('_manifest.txt', manifest)
        resp = HttpResponse(buf.getvalue(), content_type='application/zip')
        status_label = request.query_params.get('status', 'all')
        resp['Content-Disposition'] = f'attachment; filename="purchase_orders_{status_label}.zip"'
        return resp

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        po     = self.get_object()
        reason = request.data.get('reason', '')
        try:
            services.cancel(po, request.user, reason)
        except DjangoValidationError as e:
            return _err(e.messages if hasattr(e, 'messages') else str(e))
        return Response(PurchaseOrderDetailSerializer(po, context=self.get_serializer_context()).data)


# ---------------------------------------------------------------------------
# Goods Receipt Notes
# ---------------------------------------------------------------------------

class GoodsReceiptNoteViewSet(CompanyScopedViewSetMixin, viewsets.ModelViewSet):
    # Scope every read/write to the PO's owning company (Fable audit 2026-07-09:
    # the GRN viewset had NO entity isolation — any user saw/created any
    # entity's receipts).
    company_lookup_field = 'purchase_order__company_id'
    queryset = (
        GoodsReceiptNote.objects
        .select_related('purchase_order__supplier', 'received_by', 'created_by', 'journal_entry')
        .prefetch_related('lines__po_line')
    )
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action == 'list':
            return GoodsReceiptNoteListSerializer
        if self.action == 'create':
            return GoodsReceiptNoteCreateSerializer
        return GoodsReceiptNoteDetailSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        if params.get('purchase_order'):
            qs = qs.filter(purchase_order_id=params['purchase_order'])
        if params.get('status'):
            qs = qs.filter(status=params['status'])
        return qs.order_by('-receipt_date', '-grn_number')

    def perform_destroy(self, instance):
        # A posted GRN has a GL journal entry + inflated received quantities;
        # deleting it orphans both. Only an unposted GRN may be deleted.
        from rest_framework.exceptions import ValidationError as DRFValidationError
        if instance.journal_entry_id:
            raise DRFValidationError(
                "A posted goods-receipt note cannot be deleted (it has a GL "
                "entry and updated PO receipts) — reverse it instead.")
        instance.delete()

    @action(detail=True, methods=['post'])
    def post_grn(self, request, pk=None):
        grn = self.get_object()
        try:
            je = services.post_grn(grn, request.user)
        except DjangoValidationError as e:
            return _err(e.messages if hasattr(e, 'messages') else str(e))
        return Response({
            'grn':  GoodsReceiptNoteDetailSerializer(grn).data,
            'journal_entry_id':     str(je.pk),
            'journal_entry_number': je.entry_number,
        })


# ---------------------------------------------------------------------------
# 3-way match
# ---------------------------------------------------------------------------

class POBillMatchViewSet(CompanyScopedViewSetMixin, viewsets.ReadOnlyModelViewSet):
    # Scope to the PO's owning company (Fable audit 2026-07-09: matches were
    # readable across every entity).
    company_lookup_field = 'purchase_order__company_id'
    queryset = POBillMatch.objects.select_related(
        'purchase_order', 'purchase_order__supplier',
        'bill', 'matched_by',
        'tier1_approved_by', 'tier2_approved_by',
    ).prefetch_related('ai_verifications').order_by('-matched_at')
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        # Prefer the extended serializer (tier1/2 fields + AI). Falls back to
        # the legacy one for callers that still expect the old shape.
        from .serializers import POBillMatchExtendedSerializer
        return POBillMatchExtendedSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        if params.get('status'):
            qs = qs.filter(match_status=params['status'])
        if params.get('purchase_order'):
            qs = qs.filter(purchase_order_id=params['purchase_order'])
        if params.get('bill'):
            qs = qs.filter(bill_id=params['bill'])
        if params.get('pending_only') in ('1', 'true', 'yes'):
            qs = qs.filter(match_status__in=[
                POBillMatch.MatchStatus.NEEDS_TIER1_APPROVAL,
                POBillMatch.MatchStatus.NEEDS_TIER2_APPROVAL,
                POBillMatch.MatchStatus.TIER1_APPROVED,
            ])
        return qs

    @action(detail=False, methods=['post'], url_path='create-match')
    def create_match(self, request):
        po_id   = request.data.get('purchase_order')
        bill_id = request.data.get('bill')
        override_reason = request.data.get('override_reason', '')

        if not po_id or not bill_id:
            return _err('purchase_order and bill are required.')

        from billing.models import Invoice
        from .serializers import POBillMatchExtendedSerializer

        po   = get_object_or_404(PurchaseOrder, pk=po_id)
        bill = get_object_or_404(Invoice, pk=bill_id)
        # Entity isolation (Fable audit 2026-07-09): only act on a PO in a
        # company within the caller's allowed set (same gate as read scope; the
        # override AUTHORITY itself is enforced in services.match_bill_to_po).
        from core.models import allowed_company_ids
        from rest_framework.exceptions import PermissionDenied
        _allowed = allowed_company_ids(request.user)
        if (po.company_id and _allowed != {'*'}
                and str(po.company_id) not in _allowed):
            raise PermissionDenied("You cannot match bills against that company's PO.")

        try:
            match = services.match_bill_to_po(bill, po, request.user, override_reason)
        except DjangoValidationError as e:
            return _err(e.messages if hasattr(e, 'messages') else str(e))
        return Response(POBillMatchExtendedSerializer(match).data)

    @action(detail=True, methods=['post'], url_path='tier1-approve')
    def tier1_approve(self, request, pk=None):
        from .serializers import POBillMatchExtendedSerializer
        match = self.get_object()
        try:
            services.tier1_approve_match(match, request.user)
        except DjangoValidationError as e:
            return _err(e.messages if hasattr(e, 'messages') else str(e))
        return Response(POBillMatchExtendedSerializer(match).data)

    @action(detail=True, methods=['post'], url_path='tier2-approve')
    def tier2_approve(self, request, pk=None):
        from .serializers import POBillMatchExtendedSerializer
        match = self.get_object()
        try:
            services.tier2_approve_match(match, request.user)
        except DjangoValidationError as e:
            return _err(e.messages if hasattr(e, 'messages') else str(e))
        return Response(POBillMatchExtendedSerializer(match).data)

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        from .serializers import POBillMatchExtendedSerializer
        match  = self.get_object()
        reason = request.data.get('reason', '')
        try:
            services.reject_match(match, request.user, reason)
        except DjangoValidationError as e:
            return _err(e.messages if hasattr(e, 'messages') else str(e))
        return Response(POBillMatchExtendedSerializer(match).data)

    @action(detail=True, methods=['post'], url_path='retry-ai')
    def retry_ai(self, request, pk=None):
        """Re-run the DeepSeek second-pass verification on demand."""
        from .serializers import POBillMatchExtendedSerializer
        match = self.get_object()
        try:
            services.verify_bill_with_ai(
                match.bill, match.purchase_order, match,
                request.user, timeout=15.0,
            )
        except Exception as e:  # noqa: BLE001
            return _err(str(e))
        return Response(POBillMatchExtendedSerializer(match).data)


# ---------------------------------------------------------------------------
# Variance Policy (single-row settings)
# ---------------------------------------------------------------------------

class VariancePolicyViewSet(viewsets.ModelViewSet):
    """Read or update the active variance policy. Only ADMINs may write.

    Behaves like a singleton list endpoint: GET /variance-policy/ returns
    the only row; PATCH /variance-policy/{id}/ updates thresholds.
    """
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        from .models import VariancePolicy
        return VariancePolicy.objects.all().order_by('created_at')

    def get_serializer_class(self):
        from .serializers import VariancePolicySerializer
        return VariancePolicySerializer

    def _require_admin(self):
        # Fable audit 2026-07-09: only perform_update was gated — POST (create)
        # and DELETE were open, so any user could drop the policy row and insert
        # their own ceiling (VariancePolicy.current() = oldest row), routing all
        # over-billing to tier-1-only approval. Gate every write.
        from core.models import get_user_profile
        from rest_framework.exceptions import PermissionDenied
        profile = get_user_profile(self.request.user)
        if not (self.request.user.is_superuser
                or (profile and getattr(profile, 'can_administer_users', False))):
            raise PermissionDenied('Only administrators can change the variance policy.')

    def perform_create(self, serializer):
        self._require_admin()
        serializer.save(updated_by=self.request.user)

    def perform_update(self, serializer):
        self._require_admin()
        serializer.save(updated_by=self.request.user)

    def perform_destroy(self, instance):
        self._require_admin()
        instance.delete()


# ---------------------------------------------------------------------------
# Vendor Bank Accounts (maker-checker)
# ---------------------------------------------------------------------------

class VendorBankAccountViewSet(viewsets.ModelViewSet):
    queryset = (
        VendorBankAccount.objects
        .select_related('contact', 'currency_code',
                        'created_by', 'submitted_by',
                        'approved_by', 'rejected_by', 'retired_by')
    )
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action == 'list':
            return VendorBankAccountListSerializer
        if self.action == 'create':
            return VendorBankAccountCreateSerializer
        return VendorBankAccountDetailSerializer

    def perform_create(self, serializer):
        # SoD #3: only a MAKER (Financial Controller / Senior Accountant /
        # Accountant) may originate a vendor bank account. Finance Manager is a
        # checker — it approves, it does not create. (submit is also gated in
        # services.submit_bank_for_approval as belt-and-braces.)
        from core.models import get_user_profile
        from rest_framework.exceptions import PermissionDenied
        profile = get_user_profile(self.request.user)
        if not (profile and profile.can_originate_controlled_txn):
            # DRF PermissionDenied -> 403 (Fable audit 2026-07-09: raising Django's
            # ValidationError here returned an opaque 500, not a clean 403).
            raise PermissionDenied(
                'Only a maker (Financial Controller / Senior Accountant / '
                'Accountant) may create a vendor bank account.')
        serializer.save()

    def get_queryset(self):
        qs = self.queryset
        params = self.request.query_params
        if params.get('status'):
            qs = qs.filter(status=params['status'])
        if params.get('contact'):
            qs = qs.filter(contact_id=params['contact'])
        if params.get('search'):
            term = params['search']
            qs = (qs.filter(contact__name__icontains=term)
                  | qs.filter(bank_name__icontains=term)
                  | qs.filter(account_holder_name__icontains=term))
        return qs.order_by('contact__name', '-created_at')

    # ── Loading a whole supplier list at once ────────────────────────────
    # CFO 2026-08-20: "Create a place where people can upload the supplier
    # names. We already have the list of suppliers and the bank accounts …  so
    # they don't need to really do hard work." Two steps on purpose: `preview`
    # writes nothing and says what will happen to every row, then `load` does
    # only what the preview showed. Nobody should find out what a spreadsheet
    # did to the supplier register after the fact.
    def _upload_gate(self, request):
        """Same maker rule as creating one by hand — an upload is not a way in.

        Returns (response_to_send, company_id). Exactly one of the two is set:
        a Response means stop, a company id means carry on.
        """
        from core.mixins import resolve_company_id_param, scoped_company_ids
        from core.models import get_user_profile
        profile = get_user_profile(request.user)
        if not (profile and profile.can_originate_controlled_txn):
            return Response(
                {'detail': 'Only a maker (Financial Controller / Senior '
                           'Accountant / Accountant) may load supplier bank '
                           'accounts. A Finance Manager approves them.'},
                status=403), None
        f = request.data.get('file')
        if f is None or not hasattr(f, 'read'):
            return Response(
                {'detail': 'Attach the supplier list as a file (Excel or CSV).'},
                status=400), None

        # A supplier record belongs to a company (CFO 2026-05-18: ADIC's
        # vendors must not appear when ADSA is selected). One without a company
        # is invisible in every picker, so loading a list into no company at all
        # would look like success and leave a register nobody can use.
        # Deliberately NO fallback to the person's default company. A request
        # that forgot to say which company must fail loudly: silently stamping
        # the default would put an ADSA supplier list onto ADIC and report it as
        # a success, which is worse than refusing.
        company_id = resolve_company_id_param(request) or ''
        if not company_id:
            return Response(
                {'detail': 'Choose the company at the top of the screen first '
                           '— a supplier has to belong to one, or nobody will '
                           'be able to find it afterwards.'}, status=400), None
        allowed = scoped_company_ids(request)
        if allowed is not None and str(company_id) not in {str(a) for a in allowed}:
            return Response(
                {'detail': 'You do not have access to that company.'},
                status=403), None
        return None, str(company_id)

    @action(detail=False, methods=['post'], url_path='upload/preview',
            parser_classes=[MultiPartParser, FormParser])
    def upload_preview(self, request):
        """What WOULD happen. Writes nothing."""
        from procurement.vendor_bank_upload import build_plan
        bad, company_id = self._upload_gate(request)
        if bad is not None:
            return bad
        f = request.data['file']
        try:
            plan = build_plan(f.read(), getattr(f, 'name', ''), company_id)
        except Exception as exc:                            # noqa: BLE001
            # A file we cannot read is the person's most likely mistake, so it
            # gets a readable answer instead of a 500.
            return Response(
                {'detail': f'That file could not be read ({exc}). Save it as '
                           f'.xlsx or .csv and try again.'}, status=400)
        if plan.public()['missing_columns']:
            missing = ', '.join(plan.public()['missing_columns']).replace('_', ' ')
            return Response({
                'detail': (f'The sheet needs a column for: {missing}. Any '
                           f'sensible heading works — it is matched by meaning, '
                           f'not by exact wording.'),
                **plan.public()}, status=400)
        return Response(plan.public())

    @action(detail=False, methods=['post'], url_path='upload/load',
            parser_classes=[MultiPartParser, FormParser])
    def upload_load(self, request):
        """Do what the preview showed. The file is read and judged AGAIN here —
        the browser's copy of the plan is never trusted to decide what gets
        written to a bank register."""
        from procurement.vendor_bank_upload import apply_plan, build_plan
        bad, company_id = self._upload_gate(request)
        if bad is not None:
            return bad
        f = request.data['file']
        try:
            plan = build_plan(f.read(), getattr(f, 'name', ''), company_id)
        except Exception as exc:                            # noqa: BLE001
            return Response(
                {'detail': f'That file could not be read ({exc}).'}, status=400)
        if plan.public()['missing_columns']:
            return Response({'detail': 'The sheet is missing a column we must '
                                       'have. Run the preview first.'}, status=400)
        result = apply_plan(plan, request.user, company_id)
        result['message'] = (
            f"{result['created']} account(s) added and waiting for approval on "
            f"this screen."
            + (f" {result['suppliers_created']} new supplier(s) created."
               if result['suppliers_created'] else '')
            + (f" {result['held']} row(s) held because they would move a "
               f"supplier to a different account — those need someone to say "
               f"why." if result['held'] else '')
            # A failure that does not appear in the one line the screen shows
            # is a failure nobody hears about.
            + (f" {len(result['problems'])} row(s) FAILED and were not added: "
               + '; '.join(result['problems'][:3])
               + (' …' if len(result['problems']) > 3 else '')
               if result['problems'] else '')
        )
        return Response(result)

    @action(detail=True, methods=['post'])
    def submit(self, request, pk=None):
        bank = self.get_object()
        try:
            services.submit_bank_for_approval(bank, request.user)
        except DjangoValidationError as e:
            return _err(e.messages if hasattr(e, 'messages') else str(e))
        return Response(VendorBankAccountDetailSerializer(bank).data)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        bank = self.get_object()
        try:
            services.approve_bank(bank, request.user)
        except DjangoValidationError as e:
            return _err(e.messages if hasattr(e, 'messages') else str(e))
        return Response(VendorBankAccountDetailSerializer(bank).data)

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        bank   = self.get_object()
        reason = request.data.get('reason', '')
        try:
            services.reject_bank(bank, request.user, reason)
        except DjangoValidationError as e:
            return _err(e.messages if hasattr(e, 'messages') else str(e))
        return Response(VendorBankAccountDetailSerializer(bank).data)

    @action(detail=True, methods=['post'])
    def retire(self, request, pk=None):
        bank   = self.get_object()
        reason = request.data.get('reason', '')
        try:
            services.retire_bank(bank, request.user, reason)
        except DjangoValidationError as e:
            return _err(e.messages if hasattr(e, 'messages') else str(e))
        return Response(VendorBankAccountDetailSerializer(bank).data)
