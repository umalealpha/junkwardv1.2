from decimal import Decimal

from rest_framework import serializers

from .models import (
    ForexPaymentHistory, ForexPaymentImport, PlannedForexPayment,
    RecurringForexPayee,
)


class RecurringForexPayeeSerializer(serializers.ModelSerializer):
    confirmed = serializers.SerializerMethodField()
    amount_varies = serializers.SerializerMethodField()

    class Meta:
        model = RecurringForexPayee
        fields = [
            'id', 'display_name', 'beneficiary_key', 'currency', 'typical_amount',
            'amount_min', 'amount_max', 'amount_varies',
            'cadence', 'typical_day', 'source_account', 'occurrences',
            'months_active', 'confidence', 'last_seen', 'active', 'watch',
            'confirmed', 'notes',
        ]
        read_only_fields = [
            'beneficiary_key', 'currency', 'amount_min', 'amount_max',
            'occurrences', 'months_active', 'confidence', 'last_seen', 'watch',
        ]

    def get_confirmed(self, obj):
        return bool(obj.confirmed_by_id)

    def get_amount_varies(self, obj):
        """True when the payments swing enough to warrant a range, not one figure
        (widest ≥ 25% above the smallest)."""
        lo, hi = obj.amount_min, obj.amount_max
        if not lo or not hi or lo <= 0:
            return False
        return (hi / lo) >= Decimal('1.25')


class PlannedForexPaymentSerializer(serializers.ModelSerializer):
    payment_request_ref = serializers.SerializerMethodField()

    class Meta:
        model = PlannedForexPayment
        fields = [
            'id', 'beneficiary', 'currency', 'expected_amount',
            'expected_value_date', 'source_account', 'driver', 'status',
            'estimated_rate', 'estimated_bwp', 'rate_is_estimate',
            'payment_request', 'payment_request_ref', 'notes',
        ]
        read_only_fields = ['estimated_rate', 'estimated_bwp', 'rate_is_estimate',
                            'payment_request']

    def get_payment_request_ref(self, obj):
        return obj.payment_request.ref if obj.payment_request_id else None


class ForexPaymentHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ForexPaymentHistory
        fields = [
            'id', 'reference', 'beneficiary', 'currency', 'amount',
            'value_date', 'capture_date', 'source_account', 'status',
            'payment_type',
        ]


class ForexPaymentImportSerializer(serializers.ModelSerializer):
    class Meta:
        model = ForexPaymentImport
        fields = ['id', 'filename', 'row_count', 'imported_count', 'date_from',
                  'date_to', 'created_at']
