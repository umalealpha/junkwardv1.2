"""regulatory/serializers.py"""
from rest_framework import serializers

from .models import CapitalRequirementParameter, RegulatoryCapitalSnapshot


class CapitalRequirementParameterSerializer(serializers.ModelSerializer):
    class Meta:
        model = CapitalRequirementParameter
        fields = ['id', 'code', 'label', 'value', 'notes',
                  'effective_from', 'is_active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


class RegulatoryCapitalSnapshotSerializer(serializers.ModelSerializer):
    status_display    = serializers.CharField(source='get_status_display', read_only=True)
    car_pct           = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    prepared_by_username = serializers.CharField(source='prepared_by.username', read_only=True, default=None)
    approved_by_username = serializers.CharField(source='approved_by.username', read_only=True, default=None)
    method_current    = serializers.SerializerMethodField()
    method_note       = serializers.SerializerMethodField()

    def _method(self, obj):
        from .services import snapshot_method_state
        return snapshot_method_state(obj)

    def get_method_current(self, obj):
        return self._method(obj)['method_current']

    def get_method_note(self, obj):
        return self._method(obj)['method_note']

    class Meta:
        model  = RegulatoryCapitalSnapshot
        fields = [
            'id', 'as_of_date', 'company',
            'available_capital', 'required_capital',
            'capital_adequacy_ratio', 'car_pct',
            'status', 'status_display',
            'method_current', 'method_note',
            'components', 'notes',
            'prepared_by_username', 'approved_by_username', 'approved_at',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'available_capital', 'required_capital',
                            'capital_adequacy_ratio', 'car_pct', 'status',
                            'status_display', 'components',
                            'prepared_by_username', 'approved_by_username', 'approved_at',
                            'created_at', 'updated_at']
