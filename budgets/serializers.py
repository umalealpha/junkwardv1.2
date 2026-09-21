"""
budgets/serializers.py

DRF serializers for Budget and BudgetLine.
"""

from rest_framework import serializers

from .models import Budget, BudgetLine


class BudgetLineSerializer(serializers.ModelSerializer):
    account_code = serializers.CharField(source='account.code', read_only=True)
    account_name = serializers.CharField(source='account.name', read_only=True)
    account_type = serializers.CharField(source='account.account_type', read_only=True)
    sub_type = serializers.CharField(source='account.sub_type', read_only=True)

    class Meta:
        model = BudgetLine
        fields = [
            'id', 'account', 'account_code', 'account_name',
            'account_type', 'sub_type', 'amount', 'notes',
        ]
        extra_kwargs = {
            'account': {'write_only': True},
        }


class BudgetListSerializer(serializers.ModelSerializer):
    period_name = serializers.CharField(source='fiscal_period.period_name', read_only=True)
    period_start = serializers.DateField(source='fiscal_period.start_date', read_only=True)
    period_end = serializers.DateField(source='fiscal_period.end_date', read_only=True)
    department_display = serializers.CharField(source='get_department_display', read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)
    line_count = serializers.IntegerField(source='lines.count', read_only=True)
    total_revenue = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    total_expenses = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)

    class Meta:
        model = Budget
        fields = [
            'id', 'fiscal_period', 'period_name', 'period_start', 'period_end',
            'department', 'department_display', 'status', 'description',
            'created_by_name', 'line_count', 'total_revenue', 'total_expenses',
            'created_at', 'updated_at',
        ]


class BudgetDetailSerializer(serializers.ModelSerializer):
    period_name = serializers.CharField(source='fiscal_period.period_name', read_only=True)
    period_start = serializers.DateField(source='fiscal_period.start_date', read_only=True)
    period_end = serializers.DateField(source='fiscal_period.end_date', read_only=True)
    department_display = serializers.CharField(source='get_department_display', read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)
    approved_by_name = serializers.SerializerMethodField()
    lines = BudgetLineSerializer(many=True, read_only=True)

    class Meta:
        model = Budget
        fields = [
            'id', 'fiscal_period', 'period_name', 'period_start', 'period_end',
            'department', 'department_display', 'status', 'description',
            'created_by_name', 'approved_by_name', 'approved_at',
            'lines', 'created_at', 'updated_at',
        ]

    def get_approved_by_name(self, obj):
        return obj.approved_by.username if obj.approved_by else None


class BudgetCreateSerializer(serializers.ModelSerializer):
    lines = BudgetLineSerializer(many=True, required=False)

    class Meta:
        model = Budget
        fields = [
            'id',   # create must identify the new row (2026-09-10 sweep)
            'fiscal_period', 'department', 'description', 'lines',
        ]
        read_only_fields = ['id']

    def create(self, validated_data):
        lines_data = validated_data.pop('lines', [])
        validated_data['created_by'] = self.context['request'].user
        budget = Budget.objects.create(**validated_data)
        for line_data in lines_data:
            BudgetLine.objects.create(budget=budget, **line_data)
        return budget
