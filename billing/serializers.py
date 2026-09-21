"""billing/serializers.py"""
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from rest_framework import serializers

from core.models import TaxRate
from ledger.models import Account
from .models import Contact, Invoice, InvoiceLine, ReverseChargeEntry
from .reverse_charge_models import TWO_PLACES


class ContactSerializer(serializers.ModelSerializer):
    currency_name = serializers.CharField(source='currency_code.name', read_only=True)
    company_code  = serializers.CharField(source='company.code', read_only=True)

    class Meta:
        model  = Contact
        fields = ['id', 'contact_type', 'name', 'registration_number', 'tax_id',
                  'email', 'phone', 'address', 'currency_code', 'currency_name',
                  'payment_terms_days', 'is_resident', 'wht_exempt', 'is_active',
                  'graphite_id', 'company', 'company_code',
                  'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_name(self, value):
        # BUG-016 (Oprah QA, 2026-06-05): the New Vendor form accepted a blank
        # or single-character name (e.g. "."). Block it at the API layer — a
        # real vendor/contact name is at least 2 non-space characters.
        v = (value or '').strip()
        if len(v) < 2:
            raise serializers.ValidationError(
                'Name must be at least 2 characters — blank or single-character '
                'names (e.g. ".") are not allowed.'
            )
        return v

    def create(self, validated_data):
        user = self.context['request'].user
        obj  = Contact(**validated_data)
        obj.save(audit_user=user)
        return obj

    def update(self, instance, validated_data):
        user = self.context['request'].user
        for attr, val in validated_data.items():
            setattr(instance, attr, val)
        instance.save(audit_user=user)
        return instance


class InvoiceLineSerializer(serializers.ModelSerializer):
    account      = serializers.SlugRelatedField(
        slug_field='code', queryset=Account.objects.filter(is_active=True)
    )
    account_name = serializers.CharField(source='account.name', read_only=True)
    tax_code     = serializers.SlugRelatedField(
        slug_field='tax_code', queryset=TaxRate.objects.filter(is_active=True)
    )
    tax_code_name = serializers.CharField(source='tax_code.name', read_only=True)

    class Meta:
        model  = InvoiceLine
        fields = ['id', 'account', 'account_name', 'description',
                  'quantity', 'unit_price', 'tax_code', 'tax_code_name',
                  'tax_rate', 'tax_amount', 'line_total']
        read_only_fields = ['id', 'tax_rate', 'tax_amount', 'line_total']


class InvoiceListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for list views."""
    contact_name  = serializers.CharField(source='contact.name', read_only=True)
    currency      = serializers.CharField(source='currency_code_id', read_only=True)
    company_code  = serializers.CharField(source='company.code', read_only=True, default=None)
    company_name  = serializers.CharField(source='company.name', read_only=True, default=None)
    je_number     = serializers.CharField(
        source='journal_entry.entry_number', read_only=True, default=None
    )

    class Meta:
        model  = Invoice
        fields = ['id', 'invoice_number', 'invoice_type', 'contact', 'contact_name',
                  'company', 'company_code', 'company_name',
                  'issue_date', 'due_date', 'currency', 'exchange_rate',
                  'subtotal', 'tax_total', 'total_amount', 'amount_paid',
                  'balance_due', 'status', 'je_number', 'created_at']
        read_only_fields = ['id', 'invoice_number', 'subtotal', 'tax_total',
                            'total_amount', 'amount_paid', 'balance_due',
                            'status', 'je_number', 'created_at']


class InvoiceDetailSerializer(serializers.ModelSerializer):
    """Full serializer with nested lines."""
    contact_name  = serializers.CharField(source='contact.name', read_only=True)
    currency      = serializers.CharField(source='currency_code_id', read_only=True)
    company_code  = serializers.CharField(source='company.code', read_only=True, default=None)
    company_name  = serializers.CharField(source='company.name', read_only=True, default=None)
    je_number     = serializers.CharField(
        source='journal_entry.entry_number', read_only=True, default=None
    )
    submitted_for_approval_by_username = serializers.CharField(
        source='submitted_for_approval_by.username', read_only=True, default=None,
    )
    approved_by_username = serializers.CharField(
        source='approved_by.username', read_only=True, default=None,
    )
    lines = InvoiceLineSerializer(many=True)

    class Meta:
        model  = Invoice
        fields = ['id', 'invoice_number', 'invoice_type', 'contact', 'contact_name',
                  'company', 'company_code', 'company_name',
                  'issue_date', 'due_date', 'currency_code', 'currency', 'exchange_rate',
                  'subtotal', 'tax_total', 'total_amount', 'amount_paid', 'balance_due',
                  'status', 'journal_entry', 'je_number', 'description',
                  'submitted_for_approval_at', 'submitted_for_approval_by',
                  'submitted_for_approval_by_username',
                  'approval_tier', 'approved_at', 'approved_by',
                  'approved_by_username', 'rejection_reason',
                  'lines', 'created_at', 'updated_at']
        read_only_fields = ['id', 'invoice_number', 'subtotal', 'tax_total',
                            'total_amount', 'amount_paid', 'balance_due',
                            'status', 'journal_entry', 'je_number',
                            'submitted_for_approval_at', 'submitted_for_approval_by',
                            'approval_tier', 'approved_at', 'approved_by',
                            'rejection_reason',
                            'created_at', 'updated_at']

    def create(self, validated_data):
        from django.db import transaction
        from core.models import Company
        lines_data = validated_data.pop('lines', [])
        user       = self.context['request'].user

        # Default to the system's default company if none was supplied
        if not validated_data.get('company'):
            validated_data['company'] = Company.get_default()

        with transaction.atomic():
            invoice = Invoice(**validated_data, created_by=user)
            invoice.save(audit_user=user)

            for line_data in lines_data:
                InvoiceLine.objects.create(invoice=invoice, **line_data)

            invoice.recalculate_totals()
            invoice.save(audit_user=user)

        return invoice

    def update(self, instance, validated_data):
        """
        Update a draft invoice and (optionally) replace its line items.
        Posted/cancelled invoices are blocked by Invoice.save's immutability guard.
        """
        from django.db import transaction
        from rest_framework.exceptions import ValidationError as DRFValidationError

        if instance.status != Invoice.Status.DRAFT:
            raise DRFValidationError(
                f"Only draft invoices can be edited (current status: {instance.status})."
            )

        lines_data = validated_data.pop('lines', None)
        user = self.context['request'].user

        with transaction.atomic():
            for attr, val in validated_data.items():
                setattr(instance, attr, val)
            instance.save(audit_user=user)

            if lines_data is not None:
                # Replace strategy: simpler than per-line diffing and safer for
                # drafts that haven't been referenced anywhere else yet.
                instance.lines.all().delete()
                for line_data in lines_data:
                    InvoiceLine.objects.create(invoice=instance, **line_data)
                instance.recalculate_totals()
                instance.save(audit_user=user)

        return instance


# ---------------------------------------------------------------------------
# ReverseChargeEntry — reverse-charge VAT on imported remote services
# (VAT Amendment Act No.16 of 2025, effective 1 June 2026)
# ---------------------------------------------------------------------------

class ReverseChargeEntrySerializer(serializers.ModelSerializer):
    category_label = serializers.CharField(source='get_category_display', read_only=True)
    company_code    = serializers.CharField(source='company.code', read_only=True, default=None)
    company_name    = serializers.CharField(source='company.name', read_only=True, default=None)
    created_by_username = serializers.CharField(
        source='created_by.username', read_only=True, default=None,
    )

    class Meta:
        model  = ReverseChargeEntry
        fields = ['id', 'vendor', 'category', 'category_label', 'invoice_date',
                  'foreign_currency', 'foreign_amount', 'bwp_amount',
                  'reverse_charge_applies',
                  'output_vat', 'input_vat_recoverable', 'input_recovery_overridden',
                  'net_vat_cost',
                  'note', 'company', 'company_code', 'company_name',
                  'created_by_username', 'created_at', 'updated_at']
        # output_vat / net_vat_cost are ALWAYS server-computed (model.save()
        # overwrites whatever is passed in) — read-only here too so a
        # client-supplied value never even reaches validated_data.
        # input_recovery_overridden is also server-only (C-1 fix): create()/
        # update() below are the ONLY places that set it, based on whether
        # the client actually supplied input_vat_recoverable — a client can
        # never flip it directly.
        read_only_fields = ['id', 'output_vat', 'net_vat_cost',
                             'input_recovery_overridden', 'created_at', 'updated_at']

    def validate_vendor(self, value):
        v = (value or '').strip()
        if len(v) < 2:
            raise serializers.ValidationError(
                'Vendor name must be at least 2 characters.'
            )
        return v

    def validate_foreign_currency(self, value):
        v = (value or '').strip().upper()
        if len(v) != 3 or not v.isalpha():
            raise serializers.ValidationError(
                'Foreign currency must be a 3-letter ISO code, e.g. USD.'
            )
        return v

    def validate(self, attrs):
        # "Other" needs a note — enforced here (the actual enforcement path,
        # since nothing calls model.full_clean() on the API write path) AND
        # in ReverseChargeEntry.clean() (defence in depth for admin/ORM use).
        category = attrs.get(
            'category', getattr(self.instance, 'category', None),
        )
        if category == ReverseChargeEntry.Category.OTHER:
            note = attrs.get('note', getattr(self.instance, 'note', '') if self.instance else '')
            if not (note or '').strip():
                raise serializers.ValidationError({
                    'note': 'A note is required when category is "Other imported remote service".',
                })

        # H-1 (Fable-5 review): range validation — over-claims and negative
        # amounts were previously accepted outright. Mirrored in
        # ReverseChargeEntry.clean() as defence in depth for admin/ORM use.
        bwp_amount = attrs.get('bwp_amount', getattr(self.instance, 'bwp_amount', None))
        if bwp_amount is not None and bwp_amount <= 0:
            raise serializers.ValidationError({
                'bwp_amount': 'BWP amount paid must be greater than zero.',
            })

        foreign_amount = attrs.get('foreign_amount', getattr(self.instance, 'foreign_amount', None))
        if foreign_amount is not None and foreign_amount <= 0:
            raise serializers.ValidationError({
                'foreign_amount': 'Foreign amount must be greater than zero.',
            })

        # Absent-vs-explicit-None both read as None via .get() — both mean
        # "nothing to bound-check here", not "check against zero".
        input_vat_recoverable = attrs.get('input_vat_recoverable')
        if input_vat_recoverable is not None and bwp_amount is not None:
            bound = (bwp_amount * settings.RC_VAT_RATE).quantize(
                TWO_PLACES, rounding=ROUND_HALF_UP)
            if input_vat_recoverable < 0:
                raise serializers.ValidationError({
                    'input_vat_recoverable': 'Input VAT recoverable cannot be negative.',
                })
            if input_vat_recoverable > bound:
                raise serializers.ValidationError({
                    'input_vat_recoverable': (
                        f'Input VAT recoverable cannot exceed output VAT ({bound}) — '
                        f'that would over-claim input VAT.'
                    ),
                })

        # M-3 (Fable-5 review): reverse charge only applies from
        # RC_VAT_EFFECTIVE_DATE (VAT Amendment Act No.16 of 2025, effective
        # 1 June 2026) — not retroactively.
        invoice_date = attrs.get('invoice_date', getattr(self.instance, 'invoice_date', None))
        if invoice_date is not None and invoice_date < settings.RC_VAT_EFFECTIVE_DATE:
            raise serializers.ValidationError({
                'invoice_date': (
                    f'Reverse-charge VAT applies to invoices dated on or after '
                    f'{settings.RC_VAT_EFFECTIVE_DATE} (VAT Amendment Act No.16 of 2025).'
                ),
            })

        return attrs

    def create(self, validated_data):
        from core.models import Company
        user = self.context['request'].user
        if not validated_data.get('company'):
            validated_data['company'] = Company.get_default()
        # C-1 fix: input_recovery_overridden is set HERE ONLY — True iff the
        # client actually supplied a real (non-null) input_vat_recoverable.
        # An explicit null is treated the same as "not supplied" (revert to
        # full recovery); an explicit 0 counts as supplied and sticks.
        overridden = validated_data.get('input_vat_recoverable') is not None
        obj = ReverseChargeEntry(**validated_data, input_recovery_overridden=overridden)
        obj.save(audit_user=user)
        return obj

    def update(self, instance, validated_data):
        user = self.context['request'].user
        # C-1 fix: only flip the flag on when THIS request actually supplies
        # a real value — an update that doesn't touch input_vat_recoverable
        # at all must leave whatever was previously persisted untouched (an
        # existing override keeps sticking; an existing non-override keeps
        # re-pinning to full recovery in save()).
        if validated_data.get('input_vat_recoverable') is not None:
            instance.input_recovery_overridden = True
        for attr, val in validated_data.items():
            setattr(instance, attr, val)
        instance.save(audit_user=user)
        return instance
