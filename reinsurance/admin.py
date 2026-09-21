from django.contrib import admin

from .models import (
    BordereauImport, Cession, Reinsurer,
    ReinsuranceRecovery, ReinsuranceTreaty,
)


@admin.register(Reinsurer)
class ReinsurerAdmin(admin.ModelAdmin):
    list_display = ('short_code', 'name', 'country', 'credit_rating',
                    'approval_status', 'is_active')
    list_filter = ('is_active', 'approval_status', 'country')
    search_fields = ('name', 'short_code')
    # The onboarding decision has exactly one write path: reinsurance.onboarding
    # .transition(), which checks the chain, the permission, the named person and
    # writes the audit row. Leaving these editable in admin would let a Super
    # Admin mark a counterparty approved with none of that — and Super Admin is
    # held outside the reinsurance function.
    readonly_fields = ('approval_status', 'submitted_by', 'submitted_at',
                       'approved_at')


@admin.register(ReinsuranceTreaty)
class ReinsuranceTreatyAdmin(admin.ModelAdmin):
    list_display = (
        'treaty_number', 'description', 'reinsurer',
        'treaty_type', 'inception_date', 'expiry_date', 'status',
    )
    list_filter = ('status', 'treaty_type', 'reinsurer')
    search_fields = ('treaty_number', 'description')


@admin.register(Cession)
class CessionAdmin(admin.ModelAdmin):
    list_display = (
        'cession_number', 'cession_date', 'treaty',
        'gross_premium', 'ceded_premium', 'commission_amount', 'status',
    )
    list_filter = ('status', 'cession_date')
    search_fields = ('cession_number', 'policy_reference', 'risk_description')
    readonly_fields = ('cession_number', 'journal_entry')


@admin.register(ReinsuranceRecovery)
class ReinsuranceRecoveryAdmin(admin.ModelAdmin):
    list_display = (
        'recovery_number', 'recovery_date', 'treaty',
        'claim_reference', 'gross_loss', 'ceded_recovery', 'status',
    )
    list_filter = ('status', 'recovery_date')
    search_fields = ('recovery_number', 'claim_reference')
    readonly_fields = ('recovery_number', 'journal_entry')


@admin.register(BordereauImport)
class BordereauImportAdmin(admin.ModelAdmin):
    list_display = (
        'bordereau_number', 'received_date', 'treaty',
        'period_start', 'period_end', 'line_count',
        'total_ceded_premium', 'status',
    )
    list_filter = ('status', 'received_date')
    search_fields = ('bordereau_number', 'file_name')
    readonly_fields = ('bordereau_number',)
