"""salvage/parts_register.py — the write half of Veritas · Parts & Savings.

CFO directive 2026-09-10 (third pass): *"this is a register and I want people
to actually enter data into this going forward, especially the Veritas staff.
Bharath is the person who is going to manage this... he should be able to play
around with it, add a new supplier, add a new line of savings, whatever he
wants... This module is a memorandum module. We'll never get wired to a GL."*

So: full create / edit / delete on all three registers, for Veritas and ADIC
staff, with Bharath explicitly held as the module's manager.

Memorandum only — nothing here touches the ledger, and the module posts no
journal and moves no money. What it does keep is a name against every row
(`entered_by`) and a source marker, so an uploaded workbook never overwrites a
line somebody typed.

Totals are derived in `AssessmentSaving.recompute()`, never accepted from the
client — a typed row cannot disagree with its own parts / labour / paint the
way the workbook's dragged formulas do.
"""
from __future__ import annotations

from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .parts_models import (
    AssessmentSaving, EntrySource, PartsContractPricing, PartsSpend,
)
from .permissions import IsPartsRegisterEditor


class AssessmentSavingSerializer(serializers.ModelSerializer):
    entered_by_name = serializers.SerializerMethodField()
    source_display  = serializers.CharField(source='get_source_display', read_only=True)

    class Meta:
        model  = AssessmentSaving
        fields = [
            'id', 'period', 'assessment_id', 'reg_no', 'vehicle', 'repairer',
            'req_auth_date',
            'quote_parts', 'quote_labour', 'quote_paint', 'quote_total',
            'report_parts', 'report_labour', 'report_paint', 'report_total',
            'saving_parts', 'saving_labour', 'saving_paint', 'saving_total',
            'file_saving_total', 'savings_variance',
            'source', 'source_display', 'entered_by_name', 'notes',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'quote_total', 'report_total',
            'saving_parts', 'saving_labour', 'saving_paint', 'saving_total',
            'savings_variance', 'source', 'created_at', 'updated_at',
        ]
        extra_kwargs = {'period': {'required': False}}

    def get_entered_by_name(self, obj) -> str:
        user = obj.entered_by
        if not user:
            return 'Workbook'
        return user.get_full_name() or user.username

    def validate(self, attrs):
        period = attrs.get('period') or getattr(self.instance, 'period', None)
        auth   = attrs.get('req_auth_date') or getattr(self.instance, 'req_auth_date', None)
        if not period and not auth:
            raise serializers.ValidationError(
                {'req_auth_date': 'Give the authorisation date, or the month it belongs to.'})
        return attrs


class PartsSpendSerializer(serializers.ModelSerializer):
    entered_by_name  = serializers.SerializerMethodField()
    category_display = serializers.CharField(source='get_category_display', read_only=True)
    source_display   = serializers.CharField(source='get_source_display', read_only=True)

    class Meta:
        model  = PartsSpend
        fields = ['id', 'category', 'category_display', 'supplier', 'month', 'amount',
                  'source', 'source_display', 'entered_by_name', 'notes',
                  'created_at', 'updated_at']
        read_only_fields = ['source', 'created_at', 'updated_at']

    def get_entered_by_name(self, obj) -> str:
        user = obj.entered_by
        return (user.get_full_name() or user.username) if user else 'Workbook'

    def validate_supplier(self, value: str) -> str:
        cleaned = ' '.join((value or '').split())
        if not cleaned:
            raise serializers.ValidationError('Give the supplier a name.')
        return cleaned

    def validate_month(self, value):
        # Every figure in this register belongs to a month, not a day.
        return value.replace(day=1)


class PartsContractPricingSerializer(serializers.ModelSerializer):
    entered_by_name = serializers.SerializerMethodField()
    source_display  = serializers.CharField(source='get_source_display', read_only=True)

    class Meta:
        model  = PartsContractPricing
        fields = ['id', 'month', 'amount', 'source', 'source_display',
                  'entered_by_name', 'notes', 'created_at', 'updated_at']
        read_only_fields = ['source', 'created_at', 'updated_at']

    def get_entered_by_name(self, obj) -> str:
        user = obj.entered_by
        return (user.get_full_name() or user.username) if user else 'Workbook'

    def validate_month(self, value):
        return value.replace(day=1)


class RegisterViewSetMixin:
    """Shared write behaviour: stamp the typist, mark the row as typed."""

    permission_classes = [IsAuthenticated, IsPartsRegisterEditor]

    def perform_create(self, serializer):
        serializer.save(source=EntrySource.MANUAL, entered_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(entered_by=self.request.user)


class AssessmentSavingViewSet(RegisterViewSetMixin, viewsets.ModelViewSet):
    """A line of savings — one assessed job."""

    serializer_class = AssessmentSavingSerializer
    queryset         = AssessmentSaving.objects.select_related('entered_by').all()
    filterset_fields = []

    def get_queryset(self):
        rows = super().get_queryset()
        month = self.request.query_params.get('month')
        if month:
            rows = rows.filter(period=month)
        search = (self.request.query_params.get('q') or '').strip()
        if search:
            from django.db.models import Q
            rows = rows.filter(Q(assessment_id__icontains=search)
                               | Q(repairer__icontains=search)
                               | Q(reg_no__icontains=search)
                               | Q(vehicle__icontains=search))
        return rows

    # AssessmentSaving.save() recomputes the month and every total, so the
    # mixin's plain save is enough here.


class PartsSpendViewSet(RegisterViewSetMixin, viewsets.ModelViewSet):
    """Parts bought from one supplier in one month."""

    serializer_class = PartsSpendSerializer
    queryset         = PartsSpend.objects.select_related('entered_by').all()

    def get_queryset(self):
        rows = super().get_queryset()
        month = self.request.query_params.get('month')
        if month:
            rows = rows.filter(month=month)
        category = self.request.query_params.get('category')
        if category:
            rows = rows.filter(category=category)
        return rows

    @action(detail=False, methods=['get'])
    def suppliers(self, request):
        """Names already in the register, so a new line can reuse one instead
        of inventing a third spelling of the same shop."""
        names = (PartsSpend.objects.values_list('supplier', flat=True).distinct())
        seen: dict[str, str] = {}
        for name in names:
            key = ' '.join((name or '').split()).upper()
            if key and key not in seen:
                seen[key] = name
        return Response({'suppliers': sorted(seen.values(), key=str.upper)})


class PartsContractPricingViewSet(RegisterViewSetMixin, viewsets.ModelViewSet):
    """The month's contract-pricing figure."""

    serializer_class = PartsContractPricingSerializer
    queryset         = PartsContractPricing.objects.select_related('entered_by').all()

    def get_queryset(self):
        rows = super().get_queryset()
        month = self.request.query_params.get('month')
        return rows.filter(month=month) if month else rows
