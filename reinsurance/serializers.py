"""reinsurance/serializers.py — DRF serializers."""

from rest_framework import serializers

from .models import (
    BordereauImport, Cession, Reinsurer, ReinsuranceRecovery,
    ReinsuranceTreaty,
)


class ReinsurerSerializer(serializers.ModelSerializer):
    treaty_count = serializers.SerializerMethodField()

    class Meta:
        model = Reinsurer
        fields = [
            'id', 'name', 'short_code', 'country', 'credit_rating',
            'is_active', 'notes', 'treaty_count', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_treaty_count(self, obj):
        return obj.treaties.count()


class ReinsuranceTreatySerializer(serializers.ModelSerializer):
    reinsurer_name = serializers.CharField(source='reinsurer.name', read_only=True)
    reinsurer_short_code = serializers.CharField(source='reinsurer.short_code', read_only=True)
    treaty_type_display = serializers.CharField(source='get_treaty_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = ReinsuranceTreaty
        fields = [
            'id', 'treaty_number', 'description',
            'reinsurer', 'reinsurer_name', 'reinsurer_short_code',
            'treaty_type', 'treaty_type_display',
            'line_of_business',
            'inception_date', 'expiry_date',
            'currency_code',
            'cession_share_percent', 'commission_percent',
            'retention_amount', 'limit_amount',
            'status', 'status_display', 'notes',
            'created_at',
        ]
        read_only_fields = ['id', 'created_at']


class CessionSerializer(serializers.ModelSerializer):
    treaty_number = serializers.CharField(source='treaty.treaty_number', read_only=True)
    reinsurer_short_code = serializers.CharField(
        source='treaty.reinsurer.short_code', read_only=True,
    )
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    je_number = serializers.CharField(
        source='journal_entry.entry_number', read_only=True, default=None,
    )
    posted_by_username = serializers.CharField(
        source='posted_by.username', read_only=True, default=None,
    )

    class Meta:
        model = Cession
        fields = [
            'id', 'cession_number', 'cession_date',
            'treaty', 'treaty_number', 'reinsurer_short_code',
            'policy_reference', 'risk_description',
            'gross_premium', 'ceded_premium', 'commission_amount',
            'bordereau',
            'status', 'status_display',
            'je_number',
            'posted_by_username', 'posted_at',
            'created_at',
        ]
        read_only_fields = [
            'cession_number', 'status', 'created_at',
            'posted_by_username', 'posted_at', 'je_number',
        ]


class ReinsuranceRecoverySerializer(serializers.ModelSerializer):
    treaty_number = serializers.CharField(source='treaty.treaty_number', read_only=True)
    reinsurer_short_code = serializers.CharField(
        source='treaty.reinsurer.short_code', read_only=True,
    )
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    je_number = serializers.CharField(
        source='journal_entry.entry_number', read_only=True, default=None,
    )
    posted_by_username = serializers.CharField(
        source='posted_by.username', read_only=True, default=None,
    )

    class Meta:
        model = ReinsuranceRecovery
        fields = [
            'id', 'recovery_number', 'recovery_date',
            'treaty', 'treaty_number', 'reinsurer_short_code',
            'claim_reference',
            'gross_loss', 'ceded_recovery',
            'notes',
            'status', 'status_display',
            'je_number',
            'posted_by_username', 'posted_at',
            'created_at',
        ]
        read_only_fields = [
            'recovery_number', 'status', 'created_at',
            'posted_by_username', 'posted_at', 'je_number',
        ]


class BordereauImportSerializer(serializers.ModelSerializer):
    treaty_number = serializers.CharField(source='treaty.treaty_number', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = BordereauImport
        fields = [
            'id', 'bordereau_number',
            'treaty', 'treaty_number',
            'period_start', 'period_end', 'received_date',
            'file_name', 'line_count',
            'total_gross_premium', 'total_ceded_premium', 'total_commission',
            'status', 'status_display', 'notes',
            'created_at',
        ]
        read_only_fields = ['bordereau_number', 'created_at']
