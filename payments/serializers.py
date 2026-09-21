"""payments/serializers.py"""
import logging
from decimal import Decimal

from rest_framework import serializers

from ledger.models import Account
from .models import (
    Payment, PaymentAllocation, PaymentApproval, WithholdingTaxRecord,
    resolve_fx_rate,
)


class WithholdingTaxRecordSerializer(serializers.ModelSerializer):
    contact_name = serializers.CharField(source='contact.name', read_only=True)

    class Meta:
        model  = WithholdingTaxRecord
        fields = ['id', 'contact', 'contact_name', 'gross_amount', 'wht_rate',
                  'wht_amount', 'net_amount', 'tax_year_start', 'cumulative_paid_ytd',
                  'remitted_to_burs', 'remittance_date', 'burs_reference', 'created_at']
        read_only_fields = ['id', 'gross_amount', 'wht_rate', 'wht_amount',
                            'net_amount', 'tax_year_start', 'cumulative_paid_ytd',
                            'created_at']


class PaymentAllocationSerializer(serializers.ModelSerializer):
    invoice_number = serializers.CharField(source='invoice.invoice_number', read_only=True)
    invoice_total  = serializers.DecimalField(
        source='invoice.total_amount', max_digits=18, decimal_places=2, read_only=True
    )
    invoice_status = serializers.CharField(source='invoice.status', read_only=True)

    class Meta:
        model  = PaymentAllocation
        fields = ['id', 'payment', 'invoice', 'invoice_number',
                  'invoice_total', 'invoice_status', 'amount_allocated', 'created_at']
        read_only_fields = ['id', 'created_at']


class PaymentApprovalSerializer(serializers.ModelSerializer):
    """Quorum progress — who has signed so far (Fable audit 2026-07-07:
    approvers previously had no way to see 1-of-3 vs 2-of-3)."""
    approver_name     = serializers.CharField(source='approver.get_full_name', read_only=True)
    approver_username = serializers.CharField(source='approver.username', read_only=True)

    class Meta:
        model  = PaymentApproval
        fields = ['id', 'approver', 'approver_name', 'approver_username',
                  'role', 'comment', 'created_at']
        read_only_fields = fields


class PaymentListSerializer(serializers.ModelSerializer):
    contact_name    = serializers.CharField(source='contact.name', read_only=True)
    bank_account_code = serializers.CharField(source='bank_account.code', read_only=True)
    currency        = serializers.CharField(source='currency_code_id', read_only=True)
    company_code    = serializers.CharField(source='company.code', read_only=True, default=None)
    company_name    = serializers.CharField(source='company.name', read_only=True, default=None)
    je_number       = serializers.CharField(
        source='journal_entry.entry_number', read_only=True, default=None
    )

    class Meta:
        model  = Payment
        fields = ['id', 'payment_number', 'payment_type', 'contact', 'contact_name',
                  'company', 'company_code', 'company_name',
                  'bank_account', 'bank_account_code', 'payment_date', 'currency',
                  'amount', 'amount_bwp', 'payment_method', 'reference',
                  'is_once_off', 'payee_name',
                  'status', 'approval_status', 'approval_tier', 'je_number', 'created_at']
        read_only_fields = ['id', 'payment_number', 'amount_bwp',
                            'is_once_off', 'payee_name',
                            'status', 'approval_status', 'approval_tier',
                            'je_number', 'created_at']


class PayingBankField(serializers.PrimaryKeyRelatedField):
    """Accept a ledger.Account id OR a banking.BankAccount id.

    The FE bank pickers are populated from /bank-accounts/ (banking.BankAccount)
    while Payment.bank_account FKs ledger.Account — normalise here so both work.
    """

    def to_internal_value(self, data):
        from .models import resolve_paying_bank
        acc = resolve_paying_bank(data)
        if acc is not None:
            return acc
        return super().to_internal_value(data)


class PaymentDetailSerializer(serializers.ModelSerializer):
    # Fallback queryset restricted to active bank GLs so a non-bank ledger id
    # 400s cleanly instead of 500ing inside Payment.save (Fable audit).
    bank_account      = PayingBankField(
        queryset=Account.objects.filter(is_bank_account=True, is_active=True))
    contact_name      = serializers.CharField(source='contact.name', read_only=True)
    bank_account_code = serializers.CharField(source='bank_account.code', read_only=True)
    bank_account_name = serializers.CharField(source='bank_account.name', read_only=True)
    currency          = serializers.CharField(source='currency_code_id', read_only=True)
    company_code      = serializers.CharField(source='company.code', read_only=True, default=None)
    company_name      = serializers.CharField(source='company.name', read_only=True, default=None)
    je_number         = serializers.CharField(
        source='journal_entry.entry_number', read_only=True, default=None
    )
    vendor_bank_label = serializers.SerializerMethodField()
    # Once-off payee account: last-4 for the general finance readership, the
    # full number only for the people allowed to produce the bank file
    # (CanExportEftBatch). The vendor account below was already masked; this
    # one leaked in full to auditors and executives (Fable 5.1 audit
    # 2026-09-02, M7).
    payee_account_number = serializers.SerializerMethodField()
    allocations = PaymentAllocationSerializer(many=True, read_only=True)
    approvals   = PaymentApprovalSerializer(many=True, read_only=True)
    wht_record  = WithholdingTaxRecordSerializer(read_only=True)
    submitted_by_name = serializers.CharField(
        source='submitted_for_approval_by.get_full_name', read_only=True, default=None)
    approval_decided_by_name = serializers.CharField(
        source='approval_decided_by.get_full_name', read_only=True, default=None)

    class Meta:
        model  = Payment
        fields = ['id', 'payment_number', 'payment_type',
                  'contact', 'contact_name',
                  'company', 'company_code', 'company_name',
                  'bank_account', 'bank_account_code', 'bank_account_name',
                  'vendor_bank_account', 'vendor_bank_label',
                  'payment_date', 'currency_code', 'currency', 'exchange_rate',
                  'amount', 'amount_bwp', 'payment_method', 'reference', 'description',
                  'is_once_off', 'payee_name', 'payee_bank_name',
                  'bank_beneficiary_name', 'bank_our_reference', 'bank_narration',
                  'remittance_email',
                  'payee_account_number', 'payee_branch_code',
                  'status', 'bank_submitted_at', 'journal_entry', 'je_number',
                  'approval_status', 'approval_tier', 'approval_comment',
                  'submitted_for_approval_at', 'submitted_by_name',
                  'approval_decided_at', 'approval_decided_by_name',
                  'allocations', 'approvals', 'wht_record',
                  'created_at', 'updated_at']
        # is_once_off + payee_* are read-only here: only the dedicated
        # /payments/once-off/ action may set them. Writable on the generic
        # endpoint they'd let a caller flag a normal vendor payment once-off
        # and redirect the EFT to an arbitrary inline account (Fable audit).
        # bank_* are deliberately NOT read-only: the whole point is that
        # Finance can choose what the bank is told (CFO 2026-08-20). Unlike
        # payee_*, they cannot redirect money — they only change the words on
        # the statement — and validate() below refuses them once the payment
        # has gone to the bank.
        read_only_fields = ['id', 'payment_number', 'amount_bwp',
                            'is_once_off', 'payee_name', 'payee_bank_name',
                            'payee_account_number', 'payee_branch_code',
                            'status', 'bank_submitted_at', 'journal_entry', 'je_number',
                            'approval_status', 'approval_tier', 'approval_comment',
                            'submitted_for_approval_at', 'submitted_by_name',
                            'approval_decided_at', 'approval_decided_by_name',
                            'allocations', 'approvals', 'wht_record',
                            'vendor_bank_label',
                            'created_at', 'updated_at']

    def to_internal_value(self, data):
        # Older FE bundles send `currency` (the read-only display alias)
        # instead of `currency_code` — accept both so an FX payment is never
        # silently saved as BWP (Fable audit: USD 10k booked as BWP 10k).
        if hasattr(data, 'dict'):
            data = data.dict()
        elif isinstance(data, dict):
            data = dict(data)
        if 'currency_code' not in data and data.get('currency'):
            data['currency_code'] = data['currency']
        return super().to_internal_value(data)

    # Fields that describe what the BANK is told. Editable, but only while the
    # payment can still be changed — once it has gone, rewriting the words that
    # were sent would make Omni disagree with the bank statement.
    BANK_FACING_FIELDS = ('bank_beneficiary_name', 'bank_our_reference',
                          'bank_narration', 'remittance_email')

    def validate(self, attrs):
        # A payment already sent to the bank cannot have its narration
        # rewritten: FNB has the original wording and the audit trail must
        # match what was actually sent (CFO 2026-08-20).
        if self.instance is not None and self.instance.bank_submitted_at:
            touched = [f for f in self.BANK_FACING_FIELDS if f in attrs]
            if touched:
                raise serializers.ValidationError({
                    touched[0]: (
                        f'{self.instance.payment_number} has already been sent '
                        'to the bank, so what the bank was told cannot be '
                        'changed now.'
                    )})

        # FX: never default a non-BWP payment to rate 1.0. If the caller
        # didn't supply exchange_rate, resolve the latest APPROVED rate for
        # the payment date; block when none exists.
        currency = attrs.get('currency_code') or getattr(
            self.instance, 'currency_code', None)
        currency_id = getattr(currency, 'code', None) or (
            currency if isinstance(currency, str) else None) or 'BWP'
        sent_rate = attrs.get('exchange_rate')
        # Numeric compare (Fable audit): DRF quantises to 8dp so a client "1"
        # arrives as Decimal('1.00000000') — a string check ('1','1.0') missed
        # it and let a USD payment book at rate 1. Compare the actual value.
        def _is_one(r):
            if r is None:
                return True
            try:
                return Decimal(str(r)) == Decimal('1')
            except Exception:  # noqa: BLE001 — unparseable rate → force resolution
                return True
        # The resolution runs for EVERY non-BWP payment, not only when the
        # caller sent nothing or exactly 1. exchange_rate is writable, and any
        # other value used to be taken verbatim — so a USD 1,000,000 payment
        # posted with exchange_rate "0.5" booked as P500,000 instead of ~P13.6m.
        # amount_bwp is derived from this number, and the tier ladder
        # (assign_payment_tier), the OutboundPaymentPolicy dual-auth threshold
        # and the FNB batch ceiling all read amount_bwp, so a caller could pick
        # their own approval tier. The approved ExchangeRate row is the only
        # authority; a rate the caller sends is checked against it, never
        # trusted. No tolerance band — a band is only a smaller number to shade
        # the tier with, and a genuinely different dealt rate belongs on /fx
        # where somebody approves it.
        if currency_id != 'BWP':
            from django.utils import timezone as _tz
            on_date = attrs.get('payment_date') or getattr(
                self.instance, 'payment_date', None) or _tz.localdate()
            rate = resolve_fx_rate(currency_id, on_date)
            if rate is None:
                raise serializers.ValidationError(
                    {'exchange_rate': f'No approved {currency_id}→BWP exchange '
                     'rate is loaded for this date. Load and approve one on '
                     '/fx before recording this payment.'})
            if not _is_one(sent_rate) and Decimal(str(sent_rate)) != Decimal(str(rate)):
                raise serializers.ValidationError(
                    {'exchange_rate': f'{sent_rate} is not the approved '
                     f'{currency_id}→BWP rate for {on_date} ({rate}). The rate '
                     'a payment is measured in decides which approval tier it '
                     'needs, so it is taken from the approved rates only. Load '
                     'and approve the rate you want on /fx.'})
            attrs['exchange_rate'] = rate
        return attrs

    def get_vendor_bank_label(self, obj):
        b = obj.vendor_bank_account
        if not b:
            return None
        n = b.account_number or ''
        masked = ('*' * (len(n) - 4) + n[-4:]) if len(n) > 4 else n
        return f"{b.bank_name} — {masked} ({b.currency_code_id})"

    def get_payee_account_number(self, obj):
        n = (obj.payee_account_number or '').strip()
        if not n:
            return n
        request = (self.context or {}).get('request')
        try:
            from core.permissions import CanExportEftBatch
            if request is not None and CanExportEftBatch().has_permission(request, None):
                return n
        except Exception as exc:                           # noqa: BLE001
            # Fail closed (masked) but never silently: the gate check itself broke.
            logging.getLogger(__name__).warning(
                'payee_account_number: export-permission check failed, masking (%s)', exc)
        return ('*' * (len(n) - 4) + n[-4:]) if len(n) > 4 else n

    def create(self, validated_data):
        from core.models import Company
        from core.mixins import resolve_company_id_param
        if not validated_data.get('company'):
            # Prefer the caller's entity context (body/query param) over the
            # global default — booking to Company.get_default() regardless of
            # the topbar selection cross-posts entities (Fable audit).
            request = self.context.get('request')
            company_id = resolve_company_id_param(request) if request else None
            if company_id:
                validated_data['company'] = Company.objects.filter(pk=company_id).first()
        if not validated_data.get('company'):
            validated_data['company'] = Company.get_default()
        # Entity consistency: the paying bank must belong to the entity the
        # payment is booked under (mirror of the once-off guard).
        bank = validated_data.get('bank_account')
        company = validated_data.get('company')
        if (bank is not None and company is not None
                and bank.owner_company_id
                and str(bank.owner_company_id) != str(company.pk)):
            raise serializers.ValidationError(
                {'bank_account': 'That paying bank belongs to a different '
                 'entity than this payment is booked under — pick a bank in '
                 'the selected entity.'})
        # Remember-the-POP (CFO 2026-08-22): if the caller didn't set a POP email
        # but the chosen vendor bank account has one on file, use it — so the
        # vendor's remembered address wins over the blanket Accounts default.
        vba = validated_data.get('vendor_bank_account')
        # Default only when the caller OMITTED the field — an explicit blank means
        # "no POP email" and must win (model contract). API callers that omit it
        # still inherit the vendor's remembered address.
        if 'remittance_email' not in validated_data and getattr(vba, 'email', ''):
            validated_data['remittance_email'] = vba.email
        user = self.context['request'].user
        obj  = Payment(**validated_data, created_by=user)
        obj.save(audit_user=user)
        return obj

    def update(self, instance, validated_data):
        """
        Update a draft payment. Confirmed/reconciled/cancelled payments are
        blocked by Payment.save's immutability guard.
        """
        from rest_framework.exceptions import ValidationError as DRFValidationError

        if instance.status != Payment.Status.DRAFT:
            raise DRFValidationError(
                f"Only draft payments can be edited (current status: {instance.status})."
            )
        # Fable audit 2026-07-07: a payment awaiting quorum is frozen. Without
        # this, a maker could collect 2 FM signatures on P1,000 then edit the
        # amount to P900,000 before the CFO signs — the FMs' signatures would
        # count against a payment they never saw.
        if instance.approval_status == Payment.ApprovalStatus.PENDING:
            raise DRFValidationError(
                "This payment is awaiting approval signatures and cannot be "
                "edited. Reject it first (or ask an approver to), amend, and "
                "re-submit — re-submitting clears all previous signatures."
            )

        user = self.context['request'].user
        for attr, val in validated_data.items():
            setattr(instance, attr, val)
        instance.save(audit_user=user)
        return instance
