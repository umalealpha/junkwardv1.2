from rest_framework import serializers

from .models import (Agent, AgentBankAccount, AgentPayslip, CommissionCycle,
                     CommissionLine, PayoutBatch, ReportSubmission, SourceReport)


class AgentBankAccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = AgentBankAccount
        fields = ['bank_name', 'account_name', 'account_number', 'branch_code', 'updated_at']


class AgentSerializer(serializers.ModelSerializer):
    has_bank = serializers.SerializerMethodField()

    class Meta:
        model = Agent
        fields = ['id', 'name', 'is_active', 'streams', 'has_bank']

    def get_has_bank(self, obj):
        return hasattr(obj, 'bank')


class CommissionCycleSerializer(serializers.ModelSerializer):
    class Meta:
        model = CommissionCycle
        fields = ['id', 'label', 'start_date', 'end_date', 'status', 'approved_at', 'created_at']


class CommissionLineSerializer(serializers.ModelSerializer):
    agent_name = serializers.CharField(source='agent.name', read_only=True)

    class Meta:
        model = CommissionLine
        fields = ['id', 'agent_name', 'stream', 'policy_ref', 'basis', 'commission', 'payable', 'reason',
                  'graphite_status', 'graphite_note', 'graphite_checked_at']


class ReportSubmissionSerializer(serializers.ModelSerializer):
    submitted_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ReportSubmission
        fields = ['id', 'stream', 'agent_name', 'report_date', 'source',
                  'rows_count', 'approved_count', 'rejected_count', 'payable_bwp',
                  'submitted_by_name', 'created_at']

    def get_submitted_by_name(self, obj):
        u = obj.submitted_by
        if not u:
            return ''
        return (u.get_full_name() or u.username or '').strip()


class SourceReportSerializer(serializers.ModelSerializer):
    uploaded_by_name = serializers.SerializerMethodField()
    kind_display = serializers.CharField(source='get_kind_display', read_only=True)

    class Meta:
        model = SourceReport
        fields = ['id', 'kind', 'kind_display', 'rows_count', 'uploaded_by_name', 'created_at']

    def get_uploaded_by_name(self, obj):
        u = obj.uploaded_by
        return ((u.get_full_name() or u.username).strip() if u else '')


class PayoutBatchSerializer(serializers.ModelSerializer):
    class Meta:
        model = PayoutBatch
        fields = ['id', 'agent_count', 'total_bwp', 'held_count', 'fmt', 'created_at']


class AgentPayslipSerializer(serializers.ModelSerializer):
    agent_name = serializers.CharField(source='agent.name', read_only=True)
    agent_ref = serializers.CharField(source='agent.ref_id', read_only=True)
    has_email = serializers.SerializerMethodField()

    class Meta:
        model = AgentPayslip
        fields = ['id', 'number', 'agent_name', 'agent_ref', 'gross', 'ex_vat',
                  'annual', 'tax', 'net', 'breakdown', 'not_paid',
                  'emailed_at', 'emailed_to', 'has_email']

    def get_has_email(self, obj):
        return bool((obj.agent.email or '').strip())
