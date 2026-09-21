from django.db import transaction
from rest_framework import serializers

from . import service
from .models import (
    CommissionAgent,
    CommissionAmendment,
    CommissionBankAccount,
    CommissionGroup,
    CommissionSubmission,
    CommissionSubmissionLine,
)


class CommissionGroupSerializer(serializers.ModelSerializer):
    class Meta:
        model = CommissionGroup
        fields = ['id', 'key', 'name', 'withholding_rate', 'pays_via', 'owner_name', 'is_active']


class CommissionBankAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = CommissionBankAccount
        fields = ['bank_name', 'account_name', 'account_number', 'branch_code', 'updated_at']


class CommissionAgentSerializer(serializers.ModelSerializer):
    group_name = serializers.CharField(source='group.name', read_only=True)
    has_bank = serializers.SerializerMethodField()

    class Meta:
        model = CommissionAgent
        fields = ['id', 'name', 'agent_code', 'group', 'group_name', 'email',
                  'works_via_company', 'is_active', 'has_bank']

    def get_has_bank(self, obj):
        return hasattr(obj, 'bank')


class CommissionSubmissionLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = CommissionSubmissionLine
        fields = ['id', 'policy_number', 'client_name', 'transaction_type', 'frequency',
                  'amount_collected', 'annualised_premium', 'commission_rate', 'amount_applicable',
                  'collection_date', 'is_policy_closed', 'commission_amount']


class CommissionAmendmentSerializer(serializers.ModelSerializer):
    """Read-only view of one amendment on the trail."""
    actor_name = serializers.SerializerMethodField()

    class Meta:
        model = CommissionAmendment
        fields = ['id', 'actor_name', 'created_at', 'old_gross', 'new_gross', 'note']

    def get_actor_name(self, obj):
        u = obj.actor
        return ((u.get_full_name() or u.username or '').strip()) if u else 'Staff'


class CommissionSubmissionSerializer(serializers.ModelSerializer):
    lines = CommissionSubmissionLineSerializer(many=True, required=False)
    amendments = CommissionAmendmentSerializer(many=True, read_only=True)
    # Optional on input — the view binds the agent (own agent, or a manager's
    # chosen one). The client never has to send it.
    agent = serializers.PrimaryKeyRelatedField(
        queryset=CommissionAgent.objects.all(), required=False)
    agent_name = serializers.CharField(source='agent.name', read_only=True)
    group_name = serializers.CharField(source='group.name', read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    first_reviewed_by_name = serializers.SerializerMethodField()
    second_reviewed_by_name = serializers.SerializerMethodField()
    final_by_name = serializers.SerializerMethodField()
    paid_by_name = serializers.SerializerMethodField()
    has_statement_file = serializers.SerializerMethodField()
    review_flags = serializers.SerializerMethodField()

    class Meta:
        model = CommissionSubmission
        fields = ['id', 'agent', 'agent_name', 'group', 'group_name', 'period_label',
                  'status', 'status_label', 'submitted_at', 'review_note',
                  'first_reviewed_at', 'first_reviewed_by_name',
                  'second_reviewed_at', 'second_reviewed_by_name',
                  'final_at', 'final_by_name', 'paid_at', 'paid_by_name', 'notified_at',
                  'gross_commission', 'withholding_rate', 'withholding_amount', 'net_payable',
                  'lines', 'amendments', 'has_statement_file', 'review_flags', 'created_at']
        # group is derived from the agent; status + stamps move only through the
        # submit/review actions and the recompute — never set directly by a client.
        read_only_fields = ['group', 'status', 'submitted_at', 'review_note',
                            'first_reviewed_at', 'second_reviewed_at', 'final_at',
                            'gross_commission', 'withholding_rate', 'withholding_amount', 'net_payable']
        # Drop the auto UniqueTogetherValidator(agent, period_label): it would force
        # `agent` to be required, but the view binds the agent server-side (the
        # client never sends it). The DB unique constraint still holds — a duplicate
        # is caught in the view and returned as a clean 400.
        validators = []

    def _name(self, u):
        return ((u.get_full_name() or u.username or '').strip()) if u else ''

    def get_has_statement_file(self, obj):
        return bool(obj.statement_file)

    def get_review_flags(self, obj):
        # Only worth computing while a submission is actually being reviewed —
        # drafts/paid rows don't need the sanity badge, and it keeps the "My
        # commission" list cheap (the month-on-month check is one extra query).
        review_states = {CommissionSubmission.Status.SUBMITTED,
                         CommissionSubmission.Status.SECOND_REVIEW,
                         CommissionSubmission.Status.FINAL_REVIEW}
        if obj.status not in review_states:
            # not under review → checks not run; 'unknown' shows no badge (never a
            # false "clean"). Lines are already prefetched by the viewset, so only
            # the one small month-on-month lookup per review row is extra.
            return {'level': 'unknown', 'items': []}
        from .review_flags import review_flags
        return review_flags(obj)

    def get_first_reviewed_by_name(self, obj):
        return self._name(obj.first_reviewed_by)

    def get_second_reviewed_by_name(self, obj):
        return self._name(obj.second_reviewed_by)

    def get_final_by_name(self, obj):
        return self._name(obj.final_by)

    def get_paid_by_name(self, obj):
        return self._name(obj.paid_by)

    @transaction.atomic
    def create(self, validated):
        lines = validated.pop('lines', [])
        validated['group'] = validated['agent'].group
        sub = CommissionSubmission.objects.create(**validated)
        for ln in lines:
            CommissionSubmissionLine.objects.create(submission=sub, **ln)
        service.recompute_submission(sub)
        return sub

    @transaction.atomic
    def update(self, instance, validated):
        lines = validated.pop('lines', None)
        for k, v in validated.items():
            setattr(instance, k, v)
        if 'agent' in validated:
            instance.group = validated['agent'].group
        instance.save()
        if lines is not None:
            instance.lines.all().delete()
            for ln in lines:
                CommissionSubmissionLine.objects.create(submission=instance, **ln)
        service.recompute_submission(instance)
        return instance
