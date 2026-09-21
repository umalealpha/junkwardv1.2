from rest_framework import serializers

from .models import (
    RewardPartner, RewardProgram, RewardMember, PointsTransaction, DrivingScore,
)


class RewardPartnerSerializer(serializers.ModelSerializer):
    kind_display = serializers.CharField(source='get_kind_display', read_only=True)

    class Meta:
        model = RewardPartner
        fields = ['id', 'name', 'kind', 'kind_display', 'status', 'notes']


class RewardProgramSerializer(serializers.ModelSerializer):
    class Meta:
        model = RewardProgram
        fields = ['id', 'code', 'name', 'description', 'is_active', 'config']


class RewardMemberSerializer(serializers.ModelSerializer):
    tier_display = serializers.CharField(source='get_tier_display', read_only=True)

    class Meta:
        model = RewardMember
        fields = ['id', 'company', 'policy_number', 'customer_name', 'tier',
                  'tier_display', 'points_balance', 'claims_free_months',
                  'enrolled_at', 'is_active']


class PointsTransactionSerializer(serializers.ModelSerializer):
    member_name  = serializers.CharField(source='member.customer_name', read_only=True)
    program_name = serializers.CharField(source='program.name', read_only=True, default=None)
    partner_name = serializers.CharField(source='partner.name', read_only=True, default=None)
    kind_display = serializers.CharField(source='get_kind_display', read_only=True)

    class Meta:
        model = PointsTransaction
        fields = ['id', 'member', 'member_name', 'program', 'program_name',
                  'partner', 'partner_name', 'kind', 'kind_display', 'points',
                  'detail', 'occurred_at']


class DrivingScoreSerializer(serializers.ModelSerializer):
    member_name = serializers.CharField(source='member.customer_name', read_only=True)

    class Meta:
        model = DrivingScore
        fields = ['id', 'member', 'member_name', 'period', 'vehicle_reg',
                  'distance_km', 'idle_minutes', 'harsh_events', 'score',
                  'points_awarded', 'source']
