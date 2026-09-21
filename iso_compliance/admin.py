from django.contrib import admin

from .models import (
    Commandment, AuditFinding, AuditRun,
    SoAControl, Risk, CAPA, Policy, Evidence,
    InternalAudit, ManagementReview,
)


@admin.register(Commandment)
class CommandmentAdmin(admin.ModelAdmin):
    list_display = ('number', 'title', 'status', 'iso_clauses', 'last_audited_at')
    list_editable = ('status',)
    search_fields = ('title', 'iso_clauses')


@admin.register(AuditFinding)
class AuditFindingAdmin(admin.ModelAdmin):
    list_display = ('commandment', 'severity', 'state', 'title', 'detected_at')
    list_filter = ('severity', 'state', 'commandment')
    search_fields = ('title', 'detail')
    readonly_fields = ('detected_at',)


@admin.register(AuditRun)
class AuditRunAdmin(admin.ModelAdmin):
    list_display = ('started_at', 'finished_at', 'score_pct', 'findings_created', 'actor')
    readonly_fields = ('started_at', 'finished_at', 'findings_created', 'score_pct')


@admin.register(SoAControl)
class SoAControlAdmin(admin.ModelAdmin):
    list_display = ('clause', 'title', 'domain', 'applicable', 'status', 'owner')
    list_filter = ('domain', 'applicable', 'status', 'control_type')
    search_fields = ('clause', 'title', 'description')
    list_editable = ('applicable', 'status')


@admin.register(Risk)
class RiskAdmin(admin.ModelAdmin):
    list_display = ('ref', 'title', 'likelihood', 'impact', 'treatment', 'status', 'owner')
    list_filter = ('treatment', 'status')
    search_fields = ('ref', 'title', 'asset', 'threat')


@admin.register(CAPA)
class CAPAAdmin(admin.ModelAdmin):
    list_display = ('ref', 'title', 'status', 'owner', 'due_date', 'closed_at')
    list_filter = ('status',)
    search_fields = ('ref', 'title', 'nonconformity', 'root_cause')


@admin.register(Policy)
class PolicyAdmin(admin.ModelAdmin):
    list_display = ('code', 'title', 'version', 'status', 'owner', 'review_due')
    list_filter = ('status',)
    search_fields = ('code', 'title')


@admin.register(Evidence)
class EvidenceAdmin(admin.ModelAdmin):
    list_display = ('label', 'control', 'commandment', 'captured_by', 'captured_at')
    search_fields = ('label', 'description', 'url')


@admin.register(InternalAudit)
class InternalAuditAdmin(admin.ModelAdmin):
    list_display = ('ref', 'lead_auditor', 'scheduled_for', 'status', 'findings_count')
    list_filter = ('status',)


@admin.register(ManagementReview)
class ManagementReviewAdmin(admin.ModelAdmin):
    list_display = ('review_date', 'chair', 'next_review_due')


# ── AML/CFT + market-conduct registers (CFO 2026-09-09) ──────────────────────
# Registered so the registers are USABLE from day one. Without somewhere to
# enter a screening, a complaint or a breach, the weekly objectives that count
# them would be unachievable — which is worse than not setting them.
# This grants nobody new access: it only surfaces the models to users who
# already hold Django admin.
from .aml_models import (                                          # noqa: E402
    AMLTrainingRecord, ComplianceReport, CustomerComplaint, RegulatoryBreach,
    SanctionsScreening, SuspiciousTransactionReport,
)


@admin.register(ComplianceReport)
class ComplianceReportAdmin(admin.ModelAdmin):
    list_display = ('period_year', 'period_quarter', 'status', 'due_on',
                    'is_discharged', 'sent_at')
    list_filter = ('status', 'period_year', 'period_quarter')
    search_fields = ('title', 'summary', 'recipients')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(SanctionsScreening)
class SanctionsScreeningAdmin(admin.ModelAdmin):
    list_display = ('subject_name', 'subject_type', 'result', 'pep_status',
                    'screened_at', 'reported_to_fia_at')
    list_filter = ('result', 'pep_status', 'subject_type', 'list_source')
    search_fields = ('subject_name', 'subject_ref', 'notes')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(SuspiciousTransactionReport)
class SuspiciousTransactionReportAdmin(admin.ModelAdmin):
    list_display = ('subject_name', 'detected_on', 'status', 'filed_at', 'fia_reference')
    list_filter = ('status',)
    search_fields = ('subject_name', 'subject_ref', 'description', 'fia_reference')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(RegulatoryBreach)
class RegulatoryBreachAdmin(admin.ModelAdmin):
    list_display = ('title', 'regulator', 'severity', 'status', 'discovered_on',
                    'reported_to_regulator_at')
    list_filter = ('status', 'severity', 'regulator')
    search_fields = ('title', 'rule', 'description', 'remediation')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(AMLTrainingRecord)
class AMLTrainingRecordAdmin(admin.ModelAdmin):
    list_display = ('employee', 'course', 'completed_on', 'valid_until', 'score')
    list_filter = ('course',)
    search_fields = ('employee__full_name', 'course')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(CustomerComplaint)
class CustomerComplaintAdmin(admin.ModelAdmin):
    list_display = ('complainant', 'received_on', 'channel', 'status',
                    'is_overdue', 'resolved_on')
    list_filter = ('status', 'channel', 'category')
    search_fields = ('complainant', 'policy_ref', 'summary', 'outcome')
    readonly_fields = ('created_at', 'updated_at')
