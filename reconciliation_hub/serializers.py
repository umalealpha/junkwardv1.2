from rest_framework import serializers

from .models import (
    MetricSourceMap,
    SourceFigure,
    ReconciliationRun,
    ReconciliationLine,
    AgeingTieOut,
)


class ReconciliationLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReconciliationLine
        fields = [
            'id', 'metric_key', 'label', 'unit',
            'source_total', 'source_system', 'omni_posted',
            'variance', 'variance_pct', 'status', 'note', 'reconciliation',
        ]


class AgeingTieOutSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgeingTieOut
        fields = [
            'id', 'bucket', 'graphite_total', 'omni_total', 'portal_total',
            'variance_graphite_vs_omni', 'variance_pct', 'status',
        ]


class ReconciliationRunSerializer(serializers.ModelSerializer):
    company_code = serializers.CharField(source='company.code', read_only=True, default=None)
    run_by_name = serializers.CharField(source='run_by.get_full_name', read_only=True, default=None)
    lines = ReconciliationLineSerializer(many=True, read_only=True)
    ageing = AgeingTieOutSerializer(source='ageing_tieouts', many=True, read_only=True)

    class Meta:
        model = ReconciliationRun
        fields = [
            'id', 'company', 'company_code', 'period_label',
            'period_start', 'period_end', 'tolerance_pct', 'status',
            'run_at', 'run_by', 'run_by_name', 'notes', 'lines', 'ageing',
        ]


class ReconciliationRunListSerializer(serializers.ModelSerializer):
    """Lightweight — no nested lines/ageing (for the runs history list)."""
    company_code = serializers.CharField(source='company.code', read_only=True, default=None)

    class Meta:
        model = ReconciliationRun
        fields = [
            'id', 'company_code', 'period_label', 'period_start', 'period_end',
            'tolerance_pct', 'status', 'run_at',
        ]


class SourceFigureSerializer(serializers.ModelSerializer):
    class Meta:
        model = SourceFigure
        fields = [
            'id', 'company', 'period_label', 'period_start', 'period_end',
            'metric_key', 'source_system', 'source_value', 'row_count',
            'source_ref', 'notes', 'captured_at', 'captured_by',
        ]
        read_only_fields = ['id', 'captured_at', 'captured_by']


class MetricSourceMapSerializer(serializers.ModelSerializer):
    account_codes = serializers.SerializerMethodField()

    class Meta:
        model = MetricSourceMap
        fields = [
            'id', 'metric_key', 'label', 'unit', 'flow_type',
            'accounts', 'account_codes', 'include_all_receivable',
            'source_system', 'source_ref', 'is_active', 'sort_order',
        ]

    def get_account_codes(self, obj):
        return list(obj.accounts.values_list('code', flat=True))
