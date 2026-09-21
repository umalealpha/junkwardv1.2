"""investments/serializers.py"""

from rest_framework import serializers

from .models import Investment, InvestmentTransaction


class InvestmentSerializer(serializers.ModelSerializer):
    instrument_type_display = serializers.CharField(source='get_instrument_type_display', read_only=True)
    classification_display = serializers.CharField(source='get_classification_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    investment_account_code = serializers.CharField(
        source='investment_account.code', read_only=True,
    )
    investment_account_name = serializers.CharField(
        source='investment_account.name', read_only=True,
    )
    transaction_count = serializers.SerializerMethodField()
    unrealised_pl = serializers.SerializerMethodField()

    class Meta:
        model = Investment
        fields = [
            'id', 'investment_number', 'name', 'isin_or_ref',
            'instrument_type', 'instrument_type_display',
            'classification', 'classification_display',
            'issuer', 'custodian',
            'currency_code',
            'face_value', 'cost', 'current_fair_value',
            'coupon_rate_percent',
            'purchase_date', 'maturity_date',
            'investment_account', 'investment_account_code', 'investment_account_name',
            'status', 'status_display', 'notes',
            'transaction_count', 'unrealised_pl',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'investment_number', 'created_at', 'updated_at', 'current_fair_value',
        ]

    def get_transaction_count(self, obj):
        return obj.transactions.count()

    def get_unrealised_pl(self, obj):
        try:
            diff = (obj.current_fair_value or 0) - (obj.cost or 0)
            return str(diff)
        except Exception:
            return '0.00'


class InvestmentTransactionSerializer(serializers.ModelSerializer):
    investment_number = serializers.CharField(source='investment.investment_number', read_only=True)
    investment_name = serializers.CharField(source='investment.name', read_only=True)
    transaction_type_display = serializers.CharField(source='get_transaction_type_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    je_number = serializers.CharField(
        source='journal_entry.entry_number', read_only=True, default=None,
    )
    posted_by_username = serializers.CharField(
        source='posted_by.username', read_only=True, default=None,
    )

    class Meta:
        model = InvestmentTransaction
        fields = [
            'id', 'transaction_number',
            'investment', 'investment_number', 'investment_name',
            'transaction_type', 'transaction_type_display',
            'transaction_date', 'amount',
            'cash_account', 'description',
            'status', 'status_display',
            'je_number',
            'posted_by_username', 'posted_at',
            'created_at',
        ]
        read_only_fields = [
            'transaction_number', 'status', 'created_at',
            'posted_by_username', 'posted_at', 'je_number',
        ]
