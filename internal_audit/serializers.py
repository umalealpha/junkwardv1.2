from __future__ import annotations

from rest_framework import serializers

from .models import Engagement, Finding, FollowUp, ManagementResponse


class ManagementResponseSerializer(serializers.ModelSerializer):
    class Meta:
        model = ManagementResponse
        fields = ('id', 'finding', 'response_text', 'action', 'owner',
                  'target_date', 'agreed', 'created_at', 'updated_at')
        read_only_fields = ('id', 'created_at', 'updated_at')


class FollowUpSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)

    class Meta:
        model = FollowUp
        fields = ('id', 'finding', 'status', 'status_label', 'evidence_reference',
                  'next_retest_date', 'notes', 'is_overdue', 'created_at', 'updated_at')
        read_only_fields = ('id', 'is_overdue', 'created_at', 'updated_at')


class FindingSerializer(serializers.ModelSerializer):
    root_cause_label = serializers.CharField(source='get_root_cause_display', read_only=True)
    effect_category_label = serializers.CharField(source='get_effect_category_display', read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    rating_label = serializers.SerializerMethodField()
    response = ManagementResponseSerializer(read_only=True)
    followups = FollowUpSerializer(many=True, read_only=True)
    engagement_ref = serializers.CharField(source='engagement.reference', read_only=True)

    class Meta:
        model = Finding
        fields = ('id', 'engagement', 'engagement_ref', 'reference', 'title',
                  'criteria', 'condition', 'evidence_reference',
                  'root_cause', 'root_cause_label', 'root_cause_justification',
                  'effect_category', 'effect_category_label', 'effect_detail',
                  'likelihood', 'impact', 'rating', 'rating_label', 'rating_justification',
                  'fraud_flag', 'regulatory_tag', 'nbfira_reference',
                  'status', 'status_label', 'response', 'followups',
                  'created_at', 'updated_at')
        # rating is server-computed — never accept it from the client.
        read_only_fields = ('id', 'rating', 'created_at', 'updated_at')

    def get_rating_label(self, obj):
        return (obj.rating or '').title()

    def validate(self, attrs):
        # Run the model's clean() so the five-element / root-cause / rating rules
        # are enforced identically on the API as in the admin.
        instance = Finding(**{**self._instance_dict(), **attrs})
        instance.full_clean(exclude=['rating'], validate_unique=False)
        return attrs

    def _instance_dict(self):
        if self.instance is None:
            return {}
        return {f.name: getattr(self.instance, f.name)
                for f in Finding._meta.concrete_fields if f.name != 'id'}


class EngagementSerializer(serializers.ModelSerializer):
    engagement_type_label = serializers.CharField(source='get_engagement_type_display', read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    finding_count = serializers.SerializerMethodField()

    class Meta:
        model = Engagement
        fields = ('id', 'reference', 'title', 'engagement_type', 'engagement_type_label',
                  'status', 'status_label', 'lead_auditor', 'period_start', 'period_end',
                  'finding_count', 'created_at', 'updated_at')
        read_only_fields = ('id', 'created_at', 'updated_at')

    def get_finding_count(self, obj):
        return obj.findings.count()
