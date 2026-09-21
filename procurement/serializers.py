"""procurement/serializers.py — DRF serializers."""

from decimal import Decimal

from django.db import transaction
from rest_framework import serializers

from .models import (
    BillAIVerification,
    GoodsReceiptNote,
    GoodsReceiptNoteLine,
    POBillMatch,
    PurchaseOrder,
    PurchaseOrderLine,
    VariancePolicy,
    VendorBankAccount,
)


# ---------------------------------------------------------------------------
# Purchase Order
# ---------------------------------------------------------------------------

class PurchaseOrderLineSerializer(serializers.ModelSerializer):
    account_code = serializers.CharField(source='account.code', read_only=True)
    account_name = serializers.CharField(source='account.name', read_only=True)
    quantity_outstanding = serializers.DecimalField(
        max_digits=12, decimal_places=4, read_only=True,
    )
    is_fully_received = serializers.BooleanField(read_only=True)
    is_fully_billed   = serializers.BooleanField(read_only=True)

    class Meta:
        model  = PurchaseOrderLine
        fields = [
            'id', 'sequence', 'description', 'account', 'account_code', 'account_name',
            'quantity', 'unit_price', 'tax_code', 'tax_rate', 'tax_amount',
            'discount_amount', 'line_total',
            'quantity_received', 'quantity_billed',
            'quantity_outstanding', 'is_fully_received', 'is_fully_billed',
        ]
        read_only_fields = (
            'tax_rate', 'tax_amount', 'discount_amount', 'line_total',
            'quantity_received', 'quantity_billed',
        )


class PurchaseOrderListSerializer(serializers.ModelSerializer):
    supplier_name = serializers.CharField(source='supplier.name', read_only=True)
    department_display = serializers.CharField(source='get_department_display', read_only=True)
    status_display = serializers.CharField(source='status_display_label', read_only=True)

    class Meta:
        model  = PurchaseOrder
        fields = [
            'id', 'po_number', 'department', 'department_display',
            'supplier', 'supplier_name',
            'issue_date', 'expected_delivery_date',
            'currency_code', 'total_amount', 'total_bwp',
            'status', 'status_display',
            'related_claim_reference',
            'last_emailed_at', 'last_emailed_to',
        ]
        read_only_fields = ('last_emailed_at', 'last_emailed_to')


class PurchaseOrderDetailSerializer(serializers.ModelSerializer):
    supplier_name = serializers.CharField(source='supplier.name', read_only=True)
    # Supplier's email on file — prefills the "email to supplier" forms.
    # Contact.email is nullable, so coerce None to ''.
    supplier_email = serializers.SerializerMethodField()
    company_name  = serializers.CharField(source='company.name', read_only=True)
    department_display = serializers.CharField(source='get_department_display', read_only=True)
    status_display = serializers.CharField(source='status_display_label', read_only=True)
    submitted_by_username  = serializers.CharField(source='submitted_by.username',  read_only=True)
    fm_approved_by_username  = serializers.CharField(source='fm_approved_by.username',  read_only=True)
    cfo_approved_by_username = serializers.CharField(source='cfo_approved_by.username', read_only=True)
    created_by_username    = serializers.CharField(source='created_by.username',    read_only=True)
    fiscal_period_name = serializers.CharField(source='fiscal_period.period_name', read_only=True)
    lines = PurchaseOrderLineSerializer(many=True, read_only=True)
    # True only when the CURRENT user may actually cancel this PO (CFO-only
    # control, and the PO is in a cancellable state with no goods received).
    # The frontend uses this to hide the "Cancel PO" button from users who
    # cannot use it. (Kao "Cancel shows but can't cancel", 2026-07-09.)
    can_cancel = serializers.SerializerMethodField()
    # Server truth for "may this user approve the PO's current leg" — the FE uses
    # it to show the approve button to claims seniors (not just the FM). Without
    # it the approve action read as FM-only. (Kao "approval tab is back to the
    # FM", 2026-07-11.)
    can_approve = serializers.SerializerMethodField()
    # Server truth for "may this user reject the PO's current leg". Reject shares
    # the approval AUTHORITY (services.reject uses _can_approve_po) but carries NO
    # segregation-of-duties bar — the raiser may withdraw their own PO. Kept
    # separate from can_approve so a screen can hide a dead-end Approve without
    # also hiding a working Reject. (SoD dead-end-button fix, 2026-09-06.)
    can_reject = serializers.SerializerMethodField()

    class Meta:
        model  = PurchaseOrder
        fields = [
            'id', 'po_number', 'department', 'department_display',
            'supplier', 'supplier_name', 'supplier_email',
            'company', 'company_name',
            'issue_date', 'expected_delivery_date',
            'currency_code', 'exchange_rate',
            'discount_percent', 'discount_total',
            'subtotal', 'tax_total', 'total_amount', 'total_bwp',
            'status', 'status_display',
            'related_claim_reference', 'related_claim_recovery',
            'justification',
            'fiscal_period', 'fiscal_period_name',
            'submitted_by', 'submitted_by_username', 'submitted_at',
            'fm_approved_by', 'fm_approved_by_username', 'fm_approved_at',
            'cfo_approved_by', 'cfo_approved_by_username', 'cfo_approved_at',
            'rejection_reason',
            'cancelled_by', 'cancelled_at', 'cancellation_reason',
            'created_by', 'created_by_username',
            'created_at', 'updated_at',
            'last_emailed_at', 'last_emailed_to',
            'lines', 'can_cancel', 'can_approve', 'can_reject',
        ]
        read_only_fields = ('last_emailed_at', 'last_emailed_to')

    def get_supplier_email(self, obj) -> str:
        return obj.supplier.email or ''

    def get_can_cancel(self, obj) -> bool:
        # Cancelling an approved PO reverses its commitment. Mirror the
        # services.cancel guard exactly (claims PO -> Claims Manager; other
        # depts -> CFO; superuser backstop) so the button only appears for a
        # user who can actually complete the action, on a PO in a cancellable
        # state with no goods received.
        from procurement.services import _can_cancel_po
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        return bool(
            user and _can_cancel_po(user, obj)
            and obj.status in (PurchaseOrder.Status.APPROVED,
                               PurchaseOrder.Status.PARTIALLY_RECEIVED)
            and not obj.is_partially_received
        )

    def get_can_approve(self, obj) -> bool:
        # Mirror the approval guard so the FE shows the approve button to whoever
        # may approve the CURRENT leg: claims seniors on a claims PO at the
        # operational (FM) leg, FM/FC on other depts, CFO on the CFO leg.
        # Segregation of duties is PART of that guard: a two-step operational PO
        # (>= CFO_APPROVAL_THRESHOLD_BWP) may not be approved by the person who
        # created or submitted it (nor, at the CFO leg, by the FM-approver). So
        # the raiser of a P15,000 operational PO must NOT see an Approve button
        # that only 400s "Segregation of duties" — a dead-end button. Claims and
        # single-step (< threshold) POs carry no SoD gate, so they are unchanged.
        from procurement.services import (
            _can_approve_po, _can_approve_as_cfo,
            _fm_leg_sod_ok, _cfo_leg_sod_ok,
        )
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        if not user:
            return False
        if obj.status == PurchaseOrder.Status.PENDING_FM_APPROVAL:
            return _can_approve_po(user, obj) and _fm_leg_sod_ok(obj, user)
        if obj.status == PurchaseOrder.Status.PENDING_CFO_APPROVAL:
            return _can_approve_as_cfo(user) and _cfo_leg_sod_ok(obj, user)
        return False

    def get_can_reject(self, obj) -> bool:
        # Mirror services.reject exactly: any authorised approver of the PO's
        # current leg may reject it, at either pending stage, with NO SoD bar
        # (the raiser may reject their own PO). Kept distinct from can_approve so
        # tightening the approve gate never hides a legitimate Reject button.
        from procurement.services import _can_approve_po
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        if not user:
            return False
        return (obj.status in (PurchaseOrder.Status.PENDING_FM_APPROVAL,
                               PurchaseOrder.Status.PENDING_CFO_APPROVAL)
                and _can_approve_po(user, obj))


class PurchaseOrderCreateLineSerializer(serializers.ModelSerializer):
    # CFO directive 2026-05-21 — POs no longer require a GL account.
    account = serializers.PrimaryKeyRelatedField(
        queryset=PurchaseOrderLine._meta.get_field('account').related_model.objects.all(),
        required=False, allow_null=True,
    )

    class Meta:
        model  = PurchaseOrderLine
        fields = [
            'description', 'account',
            'quantity', 'unit_price', 'tax_code',
        ]


class PurchaseOrderCreateSerializer(serializers.ModelSerializer):
    lines = PurchaseOrderCreateLineSerializer(many=True)

    class Meta:
        model  = PurchaseOrder
        fields = [
            # 'id' and 'po_number' are what the New PO page reads back off the
            # 201 (Motlatsi, 2026-09-10): "Create and submit" did
            # POST /purchase-orders/undefined/submit/ and then routed to
            # /purchase-orders/undefined, because this serializer answers the
            # create call and never returned the key. The PO was saved as a
            # DRAFT, was never submitted for approval, and the raiser landed on
            # a dead page believing the system was down.
            'id', 'po_number',
            'department', 'supplier', 'company',
            'issue_date', 'expected_delivery_date',
            'currency_code', 'exchange_rate',
            'discount_percent',
            'related_claim_reference', 'related_claim_recovery',
            'justification',
            'lines',
        ]
        read_only_fields = ['id', 'po_number']

    def validate_discount_percent(self, value):
        # A discount, not a surcharge or a giveaway. Keep it sane: 0-100%.
        if value is None:
            return Decimal('0')
        if value < 0 or value > 100:
            raise serializers.ValidationError('Discount must be between 0 and 100 percent.')
        return value

    def _check_company_writable(self, request, company):
        # Entity isolation (Fable audit 2026-07-09): a restricted user must not
        # create — or move — a PO into a company OUTSIDE their allowed set. The
        # viewset mixin scoped READS only; the write path was open. Uses the
        # same allowed-companies gate as the read scope (NOT can_write, which
        # would wrongly block view-scoped PO raisers). '*' = unrestricted.
        from core.models import allowed_company_ids
        user = getattr(request, 'user', None)
        allowed = allowed_company_ids(user)
        if (company is not None and allowed != {'*'}
                and str(company.pk) not in allowed):
            raise serializers.ValidationError(
                {'company': 'You do not have access to raise a PO in that company.'})

    @transaction.atomic
    def create(self, validated_data):
        lines_data = validated_data.pop('lines')
        request = self.context.get('request')
        self._check_company_writable(request, validated_data.get('company'))
        po = PurchaseOrder.objects.create(
            created_by=request.user,
            **validated_data,
        )
        for i, ln in enumerate(lines_data):
            ln.pop('sequence', None)   # order comes from row position, not client value
            PurchaseOrderLine.objects.create(purchase_order=po, sequence=i, **ln)
        po.recalculate_totals()
        po.save(audit_user=request.user, audit_description=f"Created {po.po_number}")
        return po

    @transaction.atomic
    def update(self, instance, validated_data):
        # Amend a PO (CFO directive 2026-07-08). The viewset has already
        # verified the PO is editable (DRAFT / pending) and reset a submitted
        # PO back to DRAFT. Replace the header fields + rebuild the lines, then
        # recompute the totals so the commitment is always on current figures.
        # @transaction.atomic (Fable audit): the delete-then-recreate must be
        # all-or-nothing — a bad line must never leave the PO with half its
        # lines destroyed.
        request = self.context.get('request')
        lines_data = validated_data.pop('lines', None)
        if 'company' in validated_data:
            self._check_company_writable(request, validated_data.get('company'))
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if lines_data is not None:
            instance.lines.all().delete()
            for i, ln in enumerate(lines_data):
                ln.pop('sequence', None)   # order = row position on save
                PurchaseOrderLine.objects.create(
                    purchase_order=instance, sequence=i, **ln)
        instance.recalculate_totals()
        instance._allow_status_transition = True
        instance.save(audit_user=request.user if request else None,
                      audit_description=f"Amended {instance.po_number}")
        return instance


# ---------------------------------------------------------------------------
# GRN
# ---------------------------------------------------------------------------

class GoodsReceiptNoteLineSerializer(serializers.ModelSerializer):
    po_line_description = serializers.CharField(source='po_line.description', read_only=True)
    po_line_quantity    = serializers.DecimalField(
        source='po_line.quantity', max_digits=12, decimal_places=4, read_only=True,
    )

    class Meta:
        model  = GoodsReceiptNoteLine
        fields = [
            'id', 'po_line', 'po_line_description', 'po_line_quantity',
            'quantity_received', 'condition', 'notes',
        ]


class GoodsReceiptNoteListSerializer(serializers.ModelSerializer):
    po_number = serializers.CharField(source='purchase_order.po_number', read_only=True)
    supplier_name = serializers.CharField(source='purchase_order.supplier.name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model  = GoodsReceiptNote
        fields = [
            'id', 'grn_number', 'purchase_order', 'po_number', 'supplier_name',
            'receipt_date', 'status', 'status_display',
        ]


class GoodsReceiptNoteDetailSerializer(serializers.ModelSerializer):
    po_number = serializers.CharField(source='purchase_order.po_number', read_only=True)
    supplier_name = serializers.CharField(source='purchase_order.supplier.name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    received_by_username = serializers.CharField(source='received_by.username', read_only=True)
    created_by_username = serializers.CharField(source='created_by.username', read_only=True)
    journal_entry_number = serializers.CharField(source='journal_entry.entry_number', read_only=True)
    lines = GoodsReceiptNoteLineSerializer(many=True, read_only=True)

    class Meta:
        model  = GoodsReceiptNote
        fields = [
            'id', 'grn_number',
            'purchase_order', 'po_number', 'supplier_name',
            'receipt_date', 'delivery_note_reference', 'notes',
            'received_by', 'received_by_username',
            'created_by', 'created_by_username',
            'status', 'status_display',
            'journal_entry', 'journal_entry_number',
            'created_at', 'updated_at',
            'lines',
        ]


class GoodsReceiptNoteCreateLineSerializer(serializers.ModelSerializer):
    class Meta:
        model  = GoodsReceiptNoteLine
        fields = ['po_line', 'quantity_received', 'condition', 'notes']


class GoodsReceiptNoteCreateSerializer(serializers.ModelSerializer):
    lines = GoodsReceiptNoteCreateLineSerializer(many=True)

    class Meta:
        model  = GoodsReceiptNote
        # 'id' and 'grn_number' are read-only OUTPUT fields: DRF re-serialises the
        # created GRN through this serializer for the 201 response body, and the
        # frontend needs the new id to call .../{id}/post_grn/. Omitting 'id' sent
        # `undefined` into that URL, so the GRN could be created but never posted
        # (stuck in draft). Reported by Oprah 2026-08-29 (PO-CLM-2026-000350 et al).
        fields = [
            'id', 'grn_number',
            'purchase_order', 'receipt_date', 'delivery_note_reference',
            'received_by', 'notes', 'lines',
        ]
        read_only_fields = ['id', 'grn_number']

    def validate(self, attrs):
        # Fable audit 2026-07-08: a GRN may only be raised against an APPROVED
        # (or partially-received) PO. The UI only offers Receive on those, but
        # the API had no guard — a draft GRN on a pending PO armed a
        # ProtectedError that broke amendment. Close it at the serializer.
        po = attrs.get('purchase_order') or getattr(self.instance, 'purchase_order', None)
        allowed = {PurchaseOrder.Status.APPROVED,
                   PurchaseOrder.Status.PARTIALLY_RECEIVED}
        if po is not None and po.status not in allowed:
            raise serializers.ValidationError(
                f"Goods can only be received against an approved PO "
                f"(this PO is {po.get_status_display()}).")
        # Every receipt line must belong to THIS GRN's PO (Fable audit
        # 2026-07-09: po_line was an unscoped PK over ALL PO lines, so a GRN on
        # PO-A could receive against PO-B's line — cross-PO GL + receipt
        # corruption, and it permanently blocked PO-B's cancellation).
        if po is not None:
            for ln in (attrs.get('lines') or []):
                pol = ln.get('po_line')
                if pol is not None and getattr(pol, 'purchase_order_id', None) != po.pk:
                    raise serializers.ValidationError(
                        "Every receipt line must belong to this GRN's purchase order.")
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        lines_data = validated_data.pop('lines')
        request = self.context.get('request')
        grn = GoodsReceiptNote.objects.create(
            created_by=request.user,
            **validated_data,
        )
        for ln in lines_data:
            GoodsReceiptNoteLine.objects.create(grn=grn, **ln)
        return grn


# ---------------------------------------------------------------------------
# 3-way match
# ---------------------------------------------------------------------------

class POBillMatchSerializer(serializers.ModelSerializer):
    po_number      = serializers.CharField(source='purchase_order.po_number', read_only=True)
    bill_number    = serializers.CharField(source='bill.invoice_number', read_only=True)
    bill_total     = serializers.DecimalField(
        source='bill.total_amount', max_digits=18, decimal_places=2, read_only=True,
    )
    match_status_display = serializers.CharField(source='get_match_status_display', read_only=True)
    matched_by_username  = serializers.CharField(source='matched_by.username', read_only=True)

    class Meta:
        model  = POBillMatch
        fields = [
            'id', 'purchase_order', 'po_number',
            'bill', 'bill_number', 'bill_total',
            'match_status', 'match_status_display',
            'quantity_variance', 'price_variance',
            'override_reason',
            'matched_by', 'matched_by_username', 'matched_at',
        ]


# ---------------------------------------------------------------------------
# Vendor Bank Account
# ---------------------------------------------------------------------------

class VendorBankAccountListSerializer(serializers.ModelSerializer):
    contact_name   = serializers.CharField(source='contact.name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    name_mismatch  = serializers.BooleanField(read_only=True)
    masked_account = serializers.SerializerMethodField()

    class Meta:
        model  = VendorBankAccount
        fields = [
            'id', 'contact', 'contact_name',
            'bank_name', 'account_holder_name', 'masked_account',
            'currency_code', 'is_default', 'email',
            'status', 'status_display', 'name_mismatch',
            'created_at',
        ]

    def get_masked_account(self, obj):
        n = obj.account_number or ''
        if len(n) <= 4:
            return n
        return '*' * (len(n) - 4) + n[-4:]


class VendorBankAccountDetailSerializer(serializers.ModelSerializer):
    contact_name           = serializers.CharField(source='contact.name', read_only=True)
    status_display         = serializers.CharField(source='get_status_display', read_only=True)
    name_mismatch          = serializers.BooleanField(read_only=True)
    submitted_by_username  = serializers.CharField(source='submitted_by.username', read_only=True)
    approved_by_username   = serializers.CharField(source='approved_by.username',  read_only=True)
    rejected_by_username   = serializers.CharField(source='rejected_by.username',  read_only=True)
    retired_by_username    = serializers.CharField(source='retired_by.username',   read_only=True)
    created_by_username    = serializers.CharField(source='created_by.username',   read_only=True)

    class Meta:
        model  = VendorBankAccount
        fields = [
            'id', 'contact', 'contact_name',
            'bank_name', 'account_holder_name', 'account_number',
            'branch_code', 'branch_name', 'swift_bic', 'iban',
            'currency_code', 'is_default', 'email',
            'proof_document',
            'status', 'status_display', 'name_mismatch', 'notes',
            'submitted_by', 'submitted_by_username', 'submitted_at',
            'approved_by',  'approved_by_username',  'approved_at',
            'rejected_by',  'rejected_by_username',  'rejected_at',
            'rejection_reason',
            'retired_by',   'retired_by_username',   'retired_at',
            'retirement_reason',
            'replaces',
            'created_by',   'created_by_username',
            'created_at', 'updated_at',
        ]


class VendorBankAccountCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model  = VendorBankAccount
        fields = [
            'id',   # create must identify the new row (2026-09-10 sweep)
            'contact',
            'bank_name', 'account_holder_name', 'account_number',
            'branch_code', 'branch_name', 'swift_bic', 'iban',
            'currency_code', 'is_default', 'email',
            'proof_document', 'notes',
            'replaces',
        ]
        read_only_fields = ['id']

    def create(self, validated_data):
        request = self.context.get('request')
        return VendorBankAccount.objects.create(
            created_by=request.user, **validated_data,
        )


# ---------------------------------------------------------------------------
# Match — extended to expose tier-1/tier-2 fields + AI verdict + policy
# ---------------------------------------------------------------------------

class BillAIVerificationSerializer(serializers.ModelSerializer):
    verdict_display = serializers.CharField(source='get_verdict_display', read_only=True)

    class Meta:
        model  = BillAIVerification
        fields = [
            'id', 'verdict', 'verdict_display',
            'vendor_match', 'currency_match', 'total_match',
            'flags', 'notes', 'confidence',
            'model_used', 'elapsed_seconds', 'error_message',
            'created_at',
        ]


class POBillMatchExtendedSerializer(serializers.ModelSerializer):
    """Replaces the original POBillMatchSerializer for the new tiered flow."""

    po_number      = serializers.CharField(source='purchase_order.po_number', read_only=True)
    po_department  = serializers.CharField(source='purchase_order.get_department_display', read_only=True)
    po_total       = serializers.DecimalField(
        source='purchase_order.total_amount', max_digits=18, decimal_places=2, read_only=True,
    )
    bill_number    = serializers.CharField(source='bill.invoice_number', read_only=True)
    bill_total     = serializers.DecimalField(
        source='bill.total_amount', max_digits=18, decimal_places=2, read_only=True,
    )
    match_status_display = serializers.CharField(source='get_match_status_display', read_only=True)
    matched_by_username  = serializers.CharField(source='matched_by.username', read_only=True)
    tier1_approved_by_username = serializers.CharField(
        source='tier1_approved_by.username', read_only=True,
    )
    tier2_approved_by_username = serializers.CharField(
        source='tier2_approved_by.username', read_only=True,
    )
    is_payable = serializers.BooleanField(read_only=True)
    ai_verifications = BillAIVerificationSerializer(many=True, read_only=True)

    class Meta:
        model  = POBillMatch
        fields = [
            'id',
            'purchase_order', 'po_number', 'po_department', 'po_total',
            'bill', 'bill_number', 'bill_total',
            'match_status', 'match_status_display',
            'quantity_variance', 'price_variance', 'variance_pct',
            'tier1_approved_by', 'tier1_approved_by_username', 'tier1_approved_at',
            'tier2_approved_by', 'tier2_approved_by_username', 'tier2_approved_at',
            'rejection_reason', 'override_reason',
            'matched_by', 'matched_by_username', 'matched_at',
            'is_payable',
            'ai_verifications',
        ]


# ---------------------------------------------------------------------------
# Variance policy
# ---------------------------------------------------------------------------

class VariancePolicySerializer(serializers.ModelSerializer):
    updated_by_username = serializers.CharField(source='updated_by.username', read_only=True)

    class Meta:
        model  = VariancePolicy
        fields = ['id', 'tier1_ceiling_pct', 'notes',
                  'updated_by', 'updated_by_username',
                  'created_at', 'updated_at']
        read_only_fields = ['id', 'updated_by', 'updated_by_username',
                            'created_at', 'updated_at']
