"""claims/serializers.py"""
from rest_framework import serializers

from .models import (
    RecoveryImportBatch, Salvage, Subrogation,
    SubrogationPanel, SubrogationReceipt,
)


class SubrogationPanelSerializer(serializers.ModelSerializer):
    kind_display      = serializers.CharField(source='get_kind_display', read_only=True)
    open_case_count   = serializers.SerializerMethodField()

    class Meta:
        model  = SubrogationPanel
        fields = [
            'id', 'name', 'kind', 'kind_display',
            'contact_name', 'contact_email', 'contact_phone',
            'is_active', 'notes', 'open_case_count',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_open_case_count(self, obj):
        # Annotated in the viewset when listing; falls back to a live count.
        n = getattr(obj, 'open_case_count', None)
        return n if n is not None else obj.subrogations.count()


class SubrogationReceiptSerializer(serializers.ModelSerializer):
    method_display        = serializers.CharField(source='get_method_display', read_only=True)
    claim_reference       = serializers.CharField(source='subrogation.claim_reference', read_only=True)
    created_by_username   = serializers.CharField(source='created_by.username', read_only=True, default=None)

    class Meta:
        model  = SubrogationReceipt
        fields = [
            'id', 'subrogation', 'claim_reference',
            'amount', 'received_date', 'method', 'method_display',
            'reference', 'realpay_txn_id', 'notes',
            'created_by_username', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']


class SubrogationSerializer(serializers.ModelSerializer):
    company_code        = serializers.CharField(source='company.code', read_only=True, default=None)
    third_party_contact_name = serializers.CharField(source='third_party_contact.name', read_only=True, default=None)
    status_display      = serializers.CharField(source='get_status_display', read_only=True)
    claim_type_display  = serializers.CharField(source='get_claim_type_display', read_only=True)
    appointed_to_name   = serializers.CharField(source='appointed_to.name', read_only=True, default=None)

    # Computed off the cost build-up and the receipt records (see the model).
    total_recoverable   = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    amount_recovered    = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    outstanding_balance = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    outstanding         = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    recovery_pct        = serializers.DecimalField(max_digits=10, decimal_places=1, read_only=True)
    age_days            = serializers.IntegerField(read_only=True)
    age_bucket_label    = serializers.CharField(read_only=True)
    prescription        = serializers.SerializerMethodField()
    created_by_username = serializers.CharField(source='created_by.username', read_only=True, default=None)

    class Meta:
        model  = Subrogation
        fields = [
            'id', 'claim_reference', 'incident_date',
            'company', 'company_code',
            'claim_type', 'claim_type_display',
            'third_party_name', 'third_party_insurer',
            'third_party_contact', 'third_party_contact_name',
            'appointed_to', 'appointed_to_name', 'date_appointed',
            'assessor_fees', 'repair_costs', 'client_excess',
            'towing_fees', 'legal_fees', 'salvage_amount',
            'claim_paid_amount', 'expected_recovery', 'actual_recovery',
            'total_recoverable', 'amount_recovered', 'outstanding_balance',
            'outstanding', 'recovery_pct',
            'age_days', 'age_bucket_label', 'prescription',
            'last_recovery_date', 'status', 'status_display',
            'notes', 'graphite_id',
            'recovery_flagged_at', 'recovery_flag_source',
            'demand_letter_sent_at', 'demand_letter_sent_to',
            'escalated_at',
            'created_by_username', 'created_at', 'updated_at',
        ]
        # B9: the stamps are set by the flag ingest, the send-letter action
        # and the escalation command — never typed by a user. A writable
        # recovery_flagged_at would let anyone reset the 48-hour clock from
        # the edit form, which is the whole thing this guards.
        read_only_fields = ['id', 'created_at', 'updated_at',
                            'recovery_flagged_at', 'recovery_flag_source',
                            'demand_letter_sent_at', 'demand_letter_sent_to',
                            'escalated_at']

    def get_prescription(self, obj):
        p = obj.prescription
        return {
            'risk':           p.risk.name,
            'expires_on':     p.expires_on.isoformat() if p.expires_on else None,
            'days_remaining': p.days_remaining,
            'expired':        p.expired,
            'basis':          p.basis,
            'needs_attention': p.needs_attention,
        }


class SalvageSerializer(serializers.ModelSerializer):
    company_code        = serializers.CharField(source='company.code', read_only=True, default=None)
    buyer_contact_name  = serializers.CharField(source='buyer_contact.name', read_only=True, default=None)
    status_display      = serializers.CharField(source='get_status_display', read_only=True)
    gain_loss           = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    created_by_username = serializers.CharField(source='created_by.username', read_only=True, default=None)

    class Meta:
        model  = Salvage
        fields = [
            'id', 'claim_reference', 'incident_date',
            'company', 'company_code',
            'asset_description',
            'estimated_value', 'sale_proceeds', 'gain_loss',
            'sale_date', 'buyer_name', 'buyer_contact', 'buyer_contact_name',
            'status', 'status_display',
            'notes', 'graphite_id',
            'created_by_username', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class RecoveryImportBatchSerializer(serializers.ModelSerializer):
    kind_display    = serializers.CharField(source='get_kind_display', read_only=True)
    status_display  = serializers.CharField(source='get_status_display', read_only=True)
    company_code    = serializers.CharField(source='company.code', read_only=True, default=None)
    created_by_username       = serializers.CharField(source='created_by.username', read_only=True, default=None)
    first_approver_username   = serializers.CharField(source='first_approved_by.username', read_only=True, default=None)
    second_approver_username  = serializers.CharField(source='second_approved_by.username', read_only=True, default=None)
    rejected_by_username      = serializers.CharField(source='rejected_by.username', read_only=True, default=None)
    is_fully_approved         = serializers.BooleanField(read_only=True)

    class Meta:
        model  = RecoveryImportBatch
        fields = [
            'id', 'kind', 'kind_display', 'file_name',
            'rows_total', 'rows_valid', 'rows_invalid',
            'rows_skipped_dup', 'rows_imported',
            'parsed_rows', 'validation_errors',
            'status', 'status_display',
            'company', 'company_code',
            'created_by_username',
            'first_approver_username', 'first_approved_at',
            'second_approver_username', 'second_approved_at',
            'rejected_by_username', 'rejected_at', 'rejection_reason',
            'is_fully_approved',
            'created_at', 'committed_at',
        ]
        read_only_fields = [
            'id', 'rows_total', 'rows_valid', 'rows_invalid',
            'rows_skipped_dup', 'rows_imported',
            'parsed_rows', 'validation_errors',
            'status', 'status_display', 'company_code',
            'created_by_username', 'first_approver_username', 'first_approved_at',
            'second_approver_username', 'second_approved_at',
            'rejected_by_username', 'rejected_at', 'rejection_reason',
            'is_fully_approved', 'created_at', 'committed_at',
        ]
