"""petty_cash/serializers.py — DRF serializers for the petty cash module."""

from decimal import Decimal

from rest_framework import serializers

from .models import (
    DEFAULT_FLOAT,
    PettyCashLocation,
    PettyCashReimbursement,
    PettyCashVoucher,
    PettyCashVoucherReceipt,
)


class PettyCashVoucherReceiptSerializer(serializers.ModelSerializer):
    download_url = serializers.SerializerMethodField()
    uploaded_by_username = serializers.CharField(source='uploaded_by.username', read_only=True)

    def get_download_url(self, obj):
        """The authenticated API route, NOT the raw media path.

        This used to return `obj.file.url` — /media/... — which is not served in production, so
        every receipt download 404'd (Keetile, 5 Aug 2026). It was also the wrong thing to hand
        out on principle: a media path is not permission-checked, so anyone with the link could
        have read a till slip. The API route below is scoped to the voucher's company.
        """
        if not obj.file:
            return None
        path = f'/api/v1/petty-cash-vouchers/{obj.voucher_id}/receipts/{obj.pk}/download/'
        request = self.context.get('request')
        return request.build_absolute_uri(path) if request is not None else path

    class Meta:
        model = PettyCashVoucherReceipt
        fields = [
            'id', 'voucher', 'filename', 'file_size_bytes', 'content_type',
            'uploaded_by_username', 'download_url', 'created_at',
        ]
        read_only_fields = fields


# ---------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------

class PettyCashLocationSerializer(serializers.ModelSerializer):
    custodian_username = serializers.CharField(source='custodian.username', read_only=True)
    company_name = serializers.CharField(source='company.name', read_only=True, default=None)
    petty_cash_account_code = serializers.CharField(
        source='petty_cash_account.code', read_only=True,
    )
    petty_cash_account_name = serializers.CharField(
        source='petty_cash_account.name', read_only=True,
    )
    reimbursing_bank_account_code = serializers.CharField(
        source='reimbursing_bank_account.code', read_only=True,
    )
    reimbursing_bank_account_name = serializers.CharField(
        source='reimbursing_bank_account.name', read_only=True,
    )

    # Derived imprest figures
    cash_on_hand = serializers.SerializerMethodField()
    available_for_voucher = serializers.SerializerMethodField()
    total_unreimbursed = serializers.SerializerMethodField()
    # Ring-fence notice for a tin with a per-entity access override (e.g.
    # Unicoin). Server-owned so the red banner and the enforced rule can never
    # drift apart; None for an ordinary tin. (CFO directive 2026-08-31.)
    access_notice = serializers.SerializerMethodField()

    class Meta:
        model = PettyCashLocation
        fields = [
            'id', 'name', 'address', 'float_amount',
            'company', 'company_name',
            'custodian', 'custodian_username',
            'petty_cash_account', 'petty_cash_account_code',
            'petty_cash_account_name',
            'reimbursing_bank_account', 'reimbursing_bank_account_code',
            'reimbursing_bank_account_name',
            'is_active',
            'cash_on_hand', 'available_for_voucher', 'total_unreimbursed',
            'access_notice',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_access_notice(self, obj):
        from .services import access_notice_for_location
        return access_notice_for_location(obj)

    def get_cash_on_hand(self, obj):
        return str(obj.cash_on_hand())

    def get_available_for_voucher(self, obj):
        return str(obj.available_for_voucher())

    def get_total_unreimbursed(self, obj):
        return str(obj.total_unreimbursed())


# ---------------------------------------------------------------------------
# Voucher
# ---------------------------------------------------------------------------

class PettyCashVoucherListSerializer(serializers.ModelSerializer):
    amended_by_username = serializers.CharField(source='amended_by.username', read_only=True,
                                               default=None)
    location_name = serializers.CharField(source='location.name', read_only=True)
    expense_account_code = serializers.CharField(source='expense_account.code', read_only=True)
    expense_account_name = serializers.CharField(source='expense_account.name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    submitted_by_username = serializers.CharField(source='submitted_by.username', read_only=True)
    first_approved_by_username = serializers.CharField(source='first_approved_by.username', read_only=True, default=None)
    approved_by_username = serializers.CharField(source='approved_by.username', read_only=True)
    je_number = serializers.CharField(source='journal_entry.entry_number', read_only=True, default=None)
    reimbursement_number = serializers.CharField(
        source='reimbursement.reimbursement_number', read_only=True, default=None,
    )
    receipt_count = serializers.SerializerMethodField()
    # Whether the person LOOKING at this voucher is one of the petty-cash coders
    # (Keetile / Pako / Legakwa / Tlamelo, or a superuser). Drives the UI: only a
    # coder sees "Amend amount / GL", "Sign" and "Reject". The requester who
    # raised the voucher (e.g. an EA) never sees a coding step — coding the GL is
    # Finance's job (CFO directive 2026-08-14). Server-computed, never trusted
    # from the client. The backend still enforces the same rule on every action,
    # so this is UX only, not the security boundary.
    viewer_can_code = serializers.SerializerMethodField()
    # Ring-fence notice inherited from this voucher's tin (Unicoin) so the
    # detail screen can show the red theft-warning banner. None otherwise.
    location_access_notice = serializers.SerializerMethodField()

    def get_location_access_notice(self, obj):
        from .services import access_notice_for_location
        return access_notice_for_location(obj.location)

    def get_receipt_count(self, obj):
        try:
            return obj.receipts.count()
        except Exception:  # noqa: BLE001 — unsaved / prefetch edge
            return 0

    def get_viewer_can_code(self, obj):
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        if user is None:
            return False
        # Voucher-aware so a ring-fenced tin (Unicoin) shows the Sign / Amend
        # controls to its named approvers only — and hides them from the group
        # finance pool who may not touch that tin. (CFO directive 2026-08-31.)
        from .services import _can_approve_voucher
        return bool(_can_approve_voucher(user, obj))

    class Meta:
        model = PettyCashVoucher
        fields = [
            'id', 'voucher_number', 'voucher_date',
            'location', 'location_name',
            'payee', 'amount',
            'expense_account', 'expense_account_code', 'expense_account_name',
            'description', 'receipt_reference', 'receipt_attached', 'receipt_count',
            'status', 'status_display', 'rejection_reason',
            'submitted_by_username', 'submitted_at',
            'first_approved_by_username', 'first_approved_at',
            'approved_by_username', 'approved_at',
            'je_number', 'reimbursement_number',
            'created_at', 'viewer_can_code', 'location_access_notice',
            # The amendment trail, so a screen can show what was originally asked for.
            'original_amount', 'amend_reason', 'amended_at', 'amended_by_username',
        ]
        read_only_fields = ['voucher_number', 'created_at']


class PettyCashVoucherCreateSerializer(serializers.ModelSerializer):
    """Used for both create and update of DRAFT vouchers."""

    class Meta:
        model = PettyCashVoucher
        fields = [
            # 'id' is read back off the 201 by petty-cash/new/page.tsx, which
            # then uploads each receipt against it and routes to the voucher.
            # Without it (found 2026-09-10) v.id was undefined: EVERY receipt
            # upload failed and the user landed on /petty-cash/undefined,
            # having just typed a real voucher.
            'id',
            'location', 'voucher_date', 'payee', 'amount',
            'expense_account', 'description',
            'receipt_reference', 'receipt_attached',
        ]
        read_only_fields = ['id']


class RejectVoucherSerializer(serializers.Serializer):
    reason = serializers.CharField(min_length=3, max_length=500)


# ---------------------------------------------------------------------------
# Reimbursement
# ---------------------------------------------------------------------------

class PettyCashReimbursementListSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source='location.name', read_only=True)
    period_name = serializers.CharField(source='period.period_name', read_only=True, default=None)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    je_number = serializers.CharField(source='journal_entry.entry_number', read_only=True, default=None)
    created_by_username = serializers.CharField(source='created_by.username', read_only=True, default=None)
    submitted_by_username = serializers.CharField(source='submitted_by.username', read_only=True, default=None)
    fm_reviewed_by_username = serializers.CharField(source='fm_reviewed_by.username', read_only=True, default=None)
    posted_by_username = serializers.CharField(source='posted_by.username', read_only=True, default=None)

    class Meta:
        model = PettyCashReimbursement
        fields = [
            'id', 'reimbursement_number',
            'location', 'location_name',
            'period', 'period_name',
            'period_start', 'period_end', 'reimbursement_date',
            'total_amount', 'voucher_count',
            'status', 'status_display', 'notes', 'rejection_reason',
            'created_by_username',
            'submitted_by_username', 'submitted_at',
            'fm_reviewed_by_username', 'fm_reviewed_at',
            'posted_by_username', 'posted_at',
            'je_number',
            'created_at',
        ]
        read_only_fields = [
            'reimbursement_number', 'total_amount', 'voucher_count',
            'created_at',
        ]


class PettyCashReimbursementDetailSerializer(PettyCashReimbursementListSerializer):
    # Vouchers + GL preview shown on the review screens. Before posting, the
    # sweep hasn't run so the reverse FK is empty — show the ELIGIBLE vouchers
    # (what WILL be swept); after posting, show the ones actually swept.
    vouchers = serializers.SerializerMethodField()
    gl_preview = serializers.SerializerMethodField()

    class Meta(PettyCashReimbursementListSerializer.Meta):
        fields = PettyCashReimbursementListSerializer.Meta.fields + ['vouchers', 'gl_preview']

    def _line_vouchers(self, obj):
        if obj.status == obj.Status.POSTED:
            return list(obj.vouchers.all())
        from .services import _eligible_vouchers
        return list(_eligible_vouchers(obj.location, obj.period_start, obj.period_end))

    def get_vouchers(self, obj):
        return PettyCashVoucherListSerializer(self._line_vouchers(obj), many=True).data

    def get_gl_preview(self, obj):
        """The exact double entry the CFO is about to post: DR petty cash /
        CR the reimbursing bank, for the reimbursement total."""
        if obj.status == obj.Status.POSTED:
            total = obj.total_amount
        else:
            total = sum((v.amount for v in self._line_vouchers(obj)), Decimal('0.00'))
        loc = obj.location
        return {
            'debit': {
                'code': loc.petty_cash_account.code,
                'name': loc.petty_cash_account.name,
                'amount': str(total),
            },
            'credit': {
                'code': loc.reimbursing_bank_account.code,
                'name': loc.reimbursing_bank_account.name,
                'amount': str(total),
            },
            'total': str(total),
        }


class CreateReimbursementSerializer(serializers.Serializer):
    location = serializers.UUIDField()
    period = serializers.UUIDField(required=False, allow_null=True)
    period_start = serializers.DateField()
    period_end = serializers.DateField()
    notes = serializers.CharField(required=False, allow_blank=True, max_length=500)


class PreviewReimbursementSerializer(serializers.Serializer):
    location = serializers.UUIDField()
    period_start = serializers.DateField()
    period_end = serializers.DateField()
