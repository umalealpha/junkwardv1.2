"""ledger/serializers.py"""
from decimal import Decimal

from rest_framework import serializers

from .models import (
    Account, FiscalPeriod, JournalEntry, JournalEntryAttachment, JournalEntryLine,
    RecurringJournalEntry, RecurringJournalEntryLine,
)

TWO = Decimal('0.01')
ZERO = Decimal('0.00')


class AccountSerializer(serializers.ModelSerializer):
    parent_code  = serializers.CharField(source='parent.code', read_only=True, default=None)
    parent_name  = serializers.CharField(source='parent.name', read_only=True, default=None)
    currency     = serializers.CharField(source='currency_code_id', read_only=True)
    balance      = serializers.SerializerMethodField()
    # Surface company code so the FE can request company-aware fs-line-options
    # without a second round-trip (CFO directive 2026-06-09 Legakwa UX pass).
    owner_company_code = serializers.CharField(source='owner_company.code', read_only=True, default=None)

    def get_balance(self, obj):
        # CFO directive 2026-05-25 (COA-002): distinguish "no activity"
        # (return empty string -> UI renders "—") from "nets to zero".
        balances = self.context.get('balances', {})
        if obj.id not in balances:
            return ''
        dr, cr = balances[obj.id]
        if obj.account_type in ('asset', 'expense'):
            return str((dr - cr).quantize(TWO))
        return str((cr - dr).quantize(TWO))

    class Meta:
        model  = Account
        fields = ['id', 'code', 'name', 'account_type', 'sub_type',
                  'is_bank_account', 'parent', 'parent_code', 'parent_name',
                  'currency', 'is_active', 'balance',
                  # CFO-controlled classification (CFO directive 2026-05-17)
                  'statement_class', 'fs_line_item', 'normal_balance_dc',
                  'owner_company', 'owner_company_code', 'is_archived']
        read_only_fields = ['id']


class FiscalPeriodSerializer(serializers.ModelSerializer):
    # Period Management page (CFO directive 2026-05-28, Pako workflow):
    # surface the dual-sign signatures + entity context so the UI can render
    # the lock state without a second round-trip.
    company_code      = serializers.CharField(source='company.code', read_only=True, default=None)
    company_name      = serializers.CharField(source='company.name', read_only=True, default=None)
    fiscal_year_label = serializers.CharField(source='fiscal_year.label', read_only=True, default=None)
    locked_by_cfo_name = serializers.CharField(
        source='locked_by_cfo.get_full_name', read_only=True, default=None)
    locked_by_fm_name  = serializers.CharField(
        source='locked_by_fm.get_full_name', read_only=True, default=None)
    has_full_lock_signoff = serializers.BooleanField(read_only=True)
    # Deferred auto-lock (CFO 2026-06-09): a TB import schedules
    # auto_lock_at = now + 5 days. While in the future the period is
    # still effectively OPEN with a "locks in N days" banner; after
    # the timestamp passes, is_effectively_locked goes True and the
    # period behaves as LOCKED for posting gates.
    is_effectively_locked = serializers.BooleanField(source='is_locked', read_only=True)
    auto_lock_pending     = serializers.BooleanField(read_only=True)

    class Meta:
        model  = FiscalPeriod
        fields = ['id', 'period_name', 'start_date', 'end_date', 'status',
                  'closed_at',
                  'company', 'company_code', 'company_name',
                  'fiscal_year', 'fiscal_year_label',
                  'locked_by_cfo', 'locked_by_cfo_name', 'locked_by_cfo_at',
                  'locked_by_fm',  'locked_by_fm_name',  'locked_by_fm_at',
                  'lock_reason', 'has_full_lock_signoff',
                  'auto_lock_at', 'auto_lock_pending', 'is_effectively_locked',
                  'updated_at']
        read_only_fields = ['id', 'closed_at',
                            'company_code', 'company_name', 'fiscal_year_label',
                            'locked_by_cfo', 'locked_by_cfo_name', 'locked_by_cfo_at',
                            'locked_by_fm', 'locked_by_fm_name', 'locked_by_fm_at',
                            'lock_reason', 'has_full_lock_signoff',
                            'auto_lock_at', 'auto_lock_pending', 'is_effectively_locked',
                            'updated_at']


class JournalEntryLineSerializer(serializers.ModelSerializer):
    account      = serializers.SlugRelatedField(
        slug_field='code', queryset=Account.objects.filter(is_active=True)
    )
    account_name = serializers.CharField(source='account.name', read_only=True)
    account_type = serializers.CharField(source='account.account_type', read_only=True)
    contact_name = serializers.CharField(source='contact.name', read_only=True, default=None)

    class Meta:
        model  = JournalEntryLine
        fields = ['id', 'account', 'account_name', 'account_type',
                  'debit_amount', 'credit_amount', 'debit_bwp', 'credit_bwp',
                  'description', 'contact', 'contact_name', 'is_related_party']
        read_only_fields = ['id', 'debit_bwp', 'credit_bwp']


class JournalEntryListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for list views — no nested lines."""
    currency       = serializers.CharField(source='currency_code_id', read_only=True)
    line_count     = serializers.IntegerField(source='lines.count', read_only=True)
    created_by_name = serializers.CharField(
        source='created_by.get_full_name', read_only=True, default=None
    )
    created_by_username = serializers.CharField(
        source='created_by.username', read_only=True, default=None
    )
    submitted_by_username = serializers.CharField(
        source='submitted_by.username', read_only=True, default=None
    )
    approved_by_username = serializers.CharField(
        source='approved_by.username', read_only=True, default=None
    )

    class Meta:
        model  = JournalEntry
        fields = ['id', 'entry_number', 'entry_date', 'description', 'journal_type',
                  'status', 'currency', 'exchange_rate', 'posted_date',
                  'source_type', 'line_count',
                  'created_by_name', 'created_by_username',
                  'submitted_by_username', 'submitted_at',
                  'approved_by_username', 'approved_at',
                  'rejection_reason', 'is_related_party', 'created_at']
        read_only_fields = ['id', 'entry_number', 'status', 'posted_date',
                            'source_type', 'line_count', 'created_at',
                            'submitted_at', 'approved_at', 'rejection_reason']


class JournalEntryDetailSerializer(serializers.ModelSerializer):
    """Full serializer for detail/create — includes nested lines."""
    currency       = serializers.CharField(source='currency_code_id', read_only=True)
    lines          = JournalEntryLineSerializer(many=True)
    created_by_name = serializers.CharField(
        source='created_by.get_full_name', read_only=True, default=None
    )
    created_by_username = serializers.CharField(
        source='created_by.username', read_only=True, default=None
    )
    created_by_id = serializers.CharField(
        source='created_by.id', read_only=True, default=None
    )
    submitted_by_username = serializers.CharField(
        source='submitted_by.username', read_only=True, default=None
    )
    approved_by_username = serializers.CharField(
        source='approved_by.username', read_only=True, default=None
    )
    reversal_of_number = serializers.CharField(
        source='reversal_of.entry_number', read_only=True, default=None
    )
    reversed_by_number = serializers.CharField(
        source='reversed_by.entry_number', read_only=True, default=None
    )

    class Meta:
        model  = JournalEntry
        fields = ['id', 'entry_number', 'entry_date', 'description', 'journal_type',
                  'company',
                  'status', 'currency_code', 'currency', 'exchange_rate',
                  'posted_date', 'source_type', 'source_id',
                  'reversal_of_number', 'reversed_by_number',
                  'lines',
                  'created_by_name', 'created_by_username', 'created_by_id',
                  'submitted_by_username', 'submitted_at',
                  'approved_by_username', 'approved_at',
                  'rejection_reason',
                  'is_related_party',
                  'created_at', 'updated_at']
        read_only_fields = ['id', 'entry_number', 'status', 'posted_date',
                            'source_type', 'source_id', 'created_at', 'updated_at',
                            'submitted_at', 'approved_at', 'rejection_reason']

    def validate_entry_date(self, value):
        # BUG-002 (Oprah QA, 2026-06-05): a future-dated JE (e.g. 01/01/2028)
        # was accepted without error. Block ANY entry date after today at the
        # API layer — a journal entry records something that has happened.
        # NOTE: this is stricter than the old >366-day posting guard and will
        # reject a future-dated FY-end TB; that is the directed behaviour.
        # "Today" = BOTSWANA's today (settings.TIME_ZONE), never the server
        # clock's — date.today() is the UTC date on a UTC box, which rejected
        # every today-dated entry between 00:00 and 02:00 Gaborone (18-Aug-2026).
        from django.utils import timezone as _tz
        today = _tz.localdate()
        if value and value > today:
            raise serializers.ValidationError(
                f"Entry date {value} is in the future. Journal entries cannot be "
                f"dated after today ({today})."
            )
        return value

    def create(self, validated_data):
        from django.db import transaction
        lines_data    = validated_data.pop('lines')
        user          = self.context['request'].user
        exchange_rate = validated_data.get('exchange_rate', Decimal('1.00000000'))

        with transaction.atomic():
            je = JournalEntry(**validated_data, created_by=user)
            je.save(audit_user=user)

            for line_data in lines_data:
                dr = line_data.get('debit_amount',  ZERO) or ZERO
                cr = line_data.get('credit_amount', ZERO) or ZERO
                JournalEntryLine.objects.create(
                    journal_entry=je,
                    debit_bwp=(dr * exchange_rate).quantize(TWO),
                    credit_bwp=(cr * exchange_rate).quantize(TWO),
                    **line_data,
                )
        return je


# ─── Journal Entry Attachments ───────────────────────────────────────────────

class JournalEntryAttachmentSerializer(serializers.ModelSerializer):
    download_url       = serializers.SerializerMethodField()
    uploaded_by_username = serializers.CharField(source='uploaded_by.username', read_only=True)

    def get_download_url(self, obj):
        request = self.context.get('request')
        if obj.file and request is not None:
            return request.build_absolute_uri(obj.file.url)
        if obj.file:
            return obj.file.url
        return None

    class Meta:
        model = JournalEntryAttachment
        fields = [
            'id', 'journal_entry', 'filename', 'file_size_bytes', 'content_type',
            'description', 'uploaded_by_username', 'download_url', 'created_at',
        ]
        read_only_fields = [
            'id', 'filename', 'file_size_bytes', 'content_type',
            'uploaded_by_username', 'download_url', 'created_at',
        ]


# ─── Recurring Journal Entries ───────────────────────────────────────────────

class RecurringJournalEntryLineSerializer(serializers.ModelSerializer):
    account_code = serializers.CharField(source='account.code', read_only=True)
    account_name = serializers.CharField(source='account.name', read_only=True)

    class Meta:
        model  = RecurringJournalEntryLine
        fields = [
            'id', 'account', 'account_code', 'account_name',
            'description', 'debit_amount', 'credit_amount', 'contact',
        ]


class RecurringJournalEntryListSerializer(serializers.ModelSerializer):
    line_count       = serializers.IntegerField(source='lines.count', read_only=True)
    company_code     = serializers.CharField(source='company.code', read_only=True, default=None)
    currency         = serializers.CharField(source='currency_code_id', read_only=True)
    frequency_display = serializers.CharField(source='get_frequency_display', read_only=True)

    class Meta:
        model  = RecurringJournalEntry
        fields = [
            'id', 'name', 'description', 'journal_type',
            'frequency', 'frequency_display', 'day_of_period',
            'company', 'company_code', 'currency_code', 'currency',
            'start_date', 'end_date', 'last_generated_for', 'is_active',
            'line_count', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'last_generated_for', 'created_at', 'updated_at']


class RecurringJournalEntryDetailSerializer(RecurringJournalEntryListSerializer):
    lines = RecurringJournalEntryLineSerializer(many=True, required=False)

    class Meta(RecurringJournalEntryListSerializer.Meta):
        fields = RecurringJournalEntryListSerializer.Meta.fields + ['lines']

    def create(self, validated_data):
        from django.db import transaction
        lines_data = validated_data.pop('lines', [])
        user = self.context['request'].user

        with transaction.atomic():
            template = RecurringJournalEntry.objects.create(created_by=user, **validated_data)
            template.save(audit_user=user, audit_description='Created recurring template')
            for line in lines_data:
                RecurringJournalEntryLine.objects.create(template=template, **line)
        return template

    def update(self, instance, validated_data):
        from django.db import transaction
        lines_data = validated_data.pop('lines', None)
        user = self.context['request'].user

        with transaction.atomic():
            for k, v in validated_data.items():
                setattr(instance, k, v)
            instance.save(audit_user=user, audit_description='Updated recurring template')

            if lines_data is not None:
                # Replace lines wholesale — simplest, matches the JE flow
                instance.lines.all().delete()
                for line in lines_data:
                    RecurringJournalEntryLine.objects.create(template=instance, **line)
        return instance
