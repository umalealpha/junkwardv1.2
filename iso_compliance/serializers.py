from rest_framework import serializers

from .models import (
    AuditFinding, AuditRun, Commandment,
    SoAControl, Risk, CAPA, Policy, Evidence,
    InternalAudit, ManagementReview,
)


class AuditFindingSerializer(serializers.ModelSerializer):
    severity_label = serializers.CharField(source='get_severity_display', read_only=True)
    state_label = serializers.CharField(source='get_state_display', read_only=True)

    class Meta:
        model = AuditFinding
        fields = (
            'id', 'severity', 'severity_label', 'state', 'state_label',
            'title', 'detail', 'fix_hint', 'evidence',
            'detected_at', 'resolved_at',
        )


class CommandmentSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    findings = AuditFindingSerializer(many=True, read_only=True)
    open_count = serializers.SerializerMethodField()
    critical_count = serializers.SerializerMethodField()
    high_count = serializers.SerializerMethodField()
    good_count = serializers.SerializerMethodField()
    soa_clauses = serializers.SerializerMethodField()

    class Meta:
        model = Commandment
        fields = (
            'number', 'title', 'summary', 'iso_clauses', 'why_it_matters',
            'status', 'status_label', 'owner', 'last_audited_at',
            'findings', 'open_count', 'critical_count', 'high_count', 'good_count',
            'soa_clauses',
        )

    def _by_sev(self, obj, sev):
        return obj.findings.filter(state=AuditFinding.STATE_OPEN, severity=sev).count()

    def get_open_count(self, obj):
        return obj.findings.filter(state=AuditFinding.STATE_OPEN).count()

    def get_critical_count(self, obj):
        return self._by_sev(obj, AuditFinding.SEVERITY_CRIT)

    def get_high_count(self, obj):
        return self._by_sev(obj, AuditFinding.SEVERITY_HIGH)

    def get_good_count(self, obj):
        return self._by_sev(obj, AuditFinding.SEVERITY_GOOD)

    def get_soa_clauses(self, obj):
        return list(obj.soa_controls.values_list('clause', flat=True))


class AuditRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditRun
        fields = ('id', 'started_at', 'finished_at', 'actor',
                  'findings_created', 'score_pct', 'notes')


class SoAControlSerializer(serializers.ModelSerializer):
    domain_label = serializers.CharField(source='get_domain_display', read_only=True)
    control_type_label = serializers.CharField(source='get_control_type_display', read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    commandment_number = serializers.IntegerField(source='commandment.number',
                                                  read_only=True, default=None)

    class Meta:
        model = SoAControl
        fields = (
            'id', 'clause', 'title', 'domain', 'domain_label',
            'control_type', 'control_type_label', 'description',
            'applicable', 'justification', 'status', 'status_label',
            'owner', 'evidence_ref', 'commandment_number', 'last_reviewed_at',
        )
        read_only_fields = ('id', 'clause')


class RiskSerializer(serializers.ModelSerializer):
    treatment_label = serializers.CharField(source='get_treatment_display', read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    score = serializers.IntegerField(read_only=True)
    residual_score = serializers.IntegerField(read_only=True)
    control_clauses = serializers.SerializerMethodField()
    # Unopa Male, 2026-09-18: the Risk ID (formerly "Reference") is auto-
    # generated. Making it read-only stops the browser sending back an empty
    # `ref` on a new row and hitting the CharField unique-index at "".
    ref = serializers.CharField(read_only=True)

    class Meta:
        model = Risk
        fields = (
            'id', 'ref', 'title', 'description', 'asset', 'threat', 'vulnerability',
            'likelihood', 'impact', 'score',
            'residual_likelihood', 'residual_impact', 'residual_score',
            'treatment', 'treatment_label', 'treatment_plan',
            'owner', 'status', 'status_label',
            'control_clauses', 'created_at', 'reviewed_at',
        )
        read_only_fields = ('id', 'ref', 'score', 'residual_score', 'created_at')

    def create(self, validated_data):
        validated_data['ref'] = next_risk_ref()
        return super().create(validated_data)

    def get_control_clauses(self, obj):
        return list(obj.controls.values_list('clause', flat=True))


def next_risk_ref() -> str:
    """Return the next R-###/R-#### ref, continuing whatever the register
    already uses. Sits here (not on the model) so the importer can reuse it.
    """
    import re
    from .models import Risk as _R
    n = 0
    for ref in _R.objects.values_list('ref', flat=True):
        m = re.search(r'(\d+)$', ref or '')
        if m:
            n = max(n, int(m.group(1)))
    return f'R-{n + 1:03d}'


class CAPASerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    finding_title = serializers.CharField(source='finding.title', read_only=True, default=None)
    commandment_number = serializers.IntegerField(source='commandment.number',
                                                  read_only=True, default=None)

    class Meta:
        model = CAPA
        fields = (
            'id', 'ref', 'title', 'nonconformity', 'root_cause',
            'corrective_action', 'preventive_action',
            'owner', 'due_date', 'verifier', 'verified_at', 'closed_at',
            'status', 'status_label', 'created_at',
            'finding', 'finding_title', 'commandment', 'commandment_number',
        )
        read_only_fields = ('id', 'created_at')


class PolicySerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    control_clauses = serializers.SerializerMethodField()

    class Meta:
        model = Policy
        fields = (
            'id', 'code', 'title', 'version', 'summary', 'document_url',
            'owner', 'approver', 'approved_at', 'review_due',
            'status', 'status_label', 'control_clauses',
        )
        read_only_fields = ('id',)

    def get_control_clauses(self, obj):
        return list(obj.controls.values_list('clause', flat=True))


class EvidenceSerializer(serializers.ModelSerializer):
    control_clause = serializers.CharField(source='control.clause', read_only=True, default=None)
    commandment_number = serializers.IntegerField(source='commandment.number',
                                                  read_only=True, default=None)

    class Meta:
        model = Evidence
        fields = (
            'id', 'label', 'description', 'url',
            'captured_by', 'captured_at',
            'control', 'control_clause',
            'finding', 'commandment', 'commandment_number',
        )
        read_only_fields = ('id', 'captured_at')


class InternalAuditSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = InternalAudit
        fields = (
            'id', 'ref', 'scope', 'lead_auditor', 'scheduled_for',
            'completed_at', 'status', 'status_label', 'summary',
            'findings_count', 'report_url',
        )
        read_only_fields = ('id',)


class ManagementReviewSerializer(serializers.ModelSerializer):
    class Meta:
        model = ManagementReview
        fields = (
            'id', 'review_date', 'chair', 'attendees',
            'inputs_considered', 'decisions', 'action_items',
            'next_review_due', 'minutes_url',
        )
        read_only_fields = ('id',)


# ── DPO / DPIA register serializers (CFO 2026-08-13) ──────────────────
from .models import DPIA, DPIACondition, DPIARisk


class DPIARiskSerializer(serializers.ModelSerializer):
    score = serializers.IntegerField(read_only=True)

    class Meta:
        model = DPIARisk
        fields = (
            'id', 'dpia', 'order', 'title', 'what_could_go_wrong', 'controls',
            'likelihood', 'impact', 'score', 'mitigation',
            'residual_score', 'residual_band',
        )
        read_only_fields = ('id', 'score')


class DPIAConditionSerializer(serializers.ModelSerializer):
    phase_label = serializers.CharField(source='get_phase_display', read_only=True)

    class Meta:
        model = DPIACondition
        fields = (
            'id',
            'dpia',
            'phase',
            'phase_label',
            'text',
            'owner',
            'due_date',
            'done',
            'done_at',
            'order',
        )
        read_only_fields = ('id', 'done_at')


class DPIASerializer(serializers.ModelSerializer):
    risk_rating_label = serializers.CharField(source='get_risk_rating_display', read_only=True)
    residual_risk_label = serializers.CharField(source='get_residual_risk_display', read_only=True)
    status_label = serializers.CharField(source='get_status_display', read_only=True)
    lawful_basis_label = serializers.CharField(source='get_lawful_basis_display', read_only=True)
    decision_label = serializers.CharField(source='get_decision_display', read_only=True)
    conditions = DPIAConditionSerializer(many=True, read_only=True)
    risks = DPIARiskSerializer(many=True, read_only=True)
    pilot_total = serializers.SerializerMethodField()
    pilot_done = serializers.SerializerMethodField()
    rollout_total = serializers.SerializerMethodField()
    rollout_done = serializers.SerializerMethodField()

    class Meta:
        model = DPIA
        fields = (
            'id',
            'project',
            'description',
            'data_types',
            'special_category',
            'risk_rating',
            'risk_rating_label',
            'residual_risk',
            'residual_risk_label',
            'status',
            'status_label',
            'dpo_reviewer',
            'dpo_signed',
            'dpo_signed_at',
            'compliance_officer',
            'compliance_signed',
            'compliance_signed_at',
            'cfo_signed',
            'cfo_signed_at',
            'deadline',
            'created_at',
            'updated_at',
            # Full DPIA form
            'dpia_ref', 'date_opened', 'department', 'process_owner', 'system_used',
            'new_change_existing', 'planned_go_live', 'vendor_involved', 'cross_border',
            'purpose',
            'data_subjects', 'data_categories', 'data_other',
            'special_category_detail', 'vulnerable', 'vulnerable_note', 'volume',
            'triggers',
            'lawful_basis', 'lawful_basis_label', 'lawful_basis_note',
            'special_category_basis', 'how_informed', 'how_rights',
            'internal_sharing', 'external_vendors', 'cross_border_detail',
            'retention_period', 'retention_reason', 'disposal_method',
            'security_controls', 'security_note',
            'decision', 'decision_label', 'dpo_review_note', 'statement_of_alignment',
            'conditions',
            'risks',
            'pilot_total',
            'pilot_done',
            'rollout_total',
            'rollout_done',
        )
        read_only_fields = (
            'id', 'created_at', 'updated_at',
            'dpo_signed_at', 'compliance_signed_at', 'cfo_signed_at',
        )

    def get_pilot_total(self, obj):
        return sum(1 for condition in obj.conditions.all() if condition.phase == 'pilot')

    def get_pilot_done(self, obj):
        return sum(
            1
            for condition in obj.conditions.all()
            if condition.phase == 'pilot' and condition.done
        )

    def get_rollout_total(self, obj):
        return sum(1 for condition in obj.conditions.all() if condition.phase == 'rollout')

    def get_rollout_done(self, obj):
        return sum(
            1
            for condition in obj.conditions.all()
            if condition.phase == 'rollout' and condition.done
        )
