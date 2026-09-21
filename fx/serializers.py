"""fx/serializers.py — DRF serializers for FX revaluation."""

from rest_framework import serializers

from .models import FXRevaluation, FXRevaluationLine


class FXRevaluationLineSerializer(serializers.ModelSerializer):
    account_code = serializers.CharField(source='account.code', read_only=True)
    account_name = serializers.CharField(source='account.name', read_only=True)

    class Meta:
        model  = FXRevaluationLine
        fields = [
            'id', 'account', 'account_code', 'account_name',
            'currency_code', 'balance_foreign', 'closing_rate',
            'book_bwp', 'revalued_bwp', 'revaluation_amount',
        ]


class FXRevaluationListSerializer(serializers.ModelSerializer):
    period_name    = serializers.CharField(source='period.period_name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model  = FXRevaluation
        fields = [
            'id', 'period', 'period_name', 'company',
            'run_date', 'status', 'status_display',
            'total_gain_bwp', 'total_loss_bwp', 'net_bwp',
        ]


class FXRevaluationDetailSerializer(serializers.ModelSerializer):
    period_name    = serializers.CharField(source='period.period_name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    journal_entry_number = serializers.CharField(source='journal_entry.entry_number', read_only=True)
    created_by_username  = serializers.CharField(source='created_by.username', read_only=True)
    lines = FXRevaluationLineSerializer(many=True, read_only=True)

    class Meta:
        model  = FXRevaluation
        fields = [
            'id', 'period', 'period_name', 'company',
            'run_date', 'status', 'status_display',
            'journal_entry', 'journal_entry_number',
            'notes',
            'total_gain_bwp', 'total_loss_bwp', 'net_bwp',
            'created_by', 'created_by_username',
            'created_at', 'updated_at',
            'lines',
        ]
