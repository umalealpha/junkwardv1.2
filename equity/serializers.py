"""equity/serializers.py — DRF serializers for the editable register."""
from __future__ import annotations

from rest_framework import serializers

from .models import EsopGrant, ShareHolding, Stakeholder, VestingTranche


class ShareHoldingSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShareHolding
        fields = ['id', 'stakeholder', 'klass', 'shares', 'usd_invested', 'note']


class VestingTrancheSerializer(serializers.ModelSerializer):
    class Meta:
        model = VestingTranche
        fields = ['id', 'grant', 'vest_date', 'units', 'note']


class EsopGrantSerializer(serializers.ModelSerializer):
    grantee = serializers.CharField(source='stakeholder.name', read_only=True)
    tranches = VestingTrancheSerializer(many=True, read_only=True)

    class Meta:
        model = EsopGrant
        fields = [
            'id', 'stakeholder', 'grantee', 'units', 'grant_date', 'expiry_date',
            'exercise_price_usd', 'pct_pool', 'pct_fd', 'status', 'letter_ref',
            'note', 'tranches',
        ]


class StakeholderSerializer(serializers.ModelSerializer):
    holdings = ShareHoldingSerializer(many=True, read_only=True)
    grants = EsopGrantSerializer(many=True, read_only=True)
    employee_name = serializers.CharField(source='employee.full_name', read_only=True, default='')

    class Meta:
        model = Stakeholder
        fields = [
            'id', 'name', 'kind', 'employee', 'employee_name', 'email',
            'is_current', 'note', 'holdings', 'grants',
        ]
