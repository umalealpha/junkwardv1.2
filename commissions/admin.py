from django.contrib import admin

from .models import (
    Broker,
    BrokerCommissionRate,
    CommissionAgent,
    CommissionAmendment,
    CommissionBankAccount,
    CommissionGroup,
    CommissionSubmission,
    CommissionSubmissionLine,
)


@admin.register(CommissionGroup)
class CommissionGroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'key', 'withholding_rate', 'pays_via', 'owner_name', 'is_active')


@admin.register(CommissionAgent)
class CommissionAgentAdmin(admin.ModelAdmin):
    list_display = ('name', 'agent_code', 'group', 'is_active')
    list_filter = ('group', 'is_active')
    search_fields = ('name', 'agent_code', 'email')


class CommissionSubmissionLineInline(admin.TabularInline):
    model = CommissionSubmissionLine
    extra = 0


@admin.register(CommissionSubmission)
class CommissionSubmissionAdmin(admin.ModelAdmin):
    list_display = ('agent', 'period_label', 'group', 'status',
                    'gross_commission', 'withholding_amount', 'net_payable')
    list_filter = ('status', 'group', 'period_label')
    search_fields = ('agent__name',)
    inlines = [CommissionSubmissionLineInline]


@admin.register(CommissionBankAccount)
class CommissionBankAccountAdmin(admin.ModelAdmin):
    list_display = ('agent', 'bank_name', 'branch_code')


@admin.register(CommissionAmendment)
class CommissionAmendmentAdmin(admin.ModelAdmin):
    """Append-only amendment trail — read-only in admin."""
    list_display = ('submission', 'actor', 'created_at', 'old_gross', 'new_gross')
    list_filter = ('created_at',)
    search_fields = ('submission__agent__name', 'note')
    readonly_fields = ('submission', 'actor', 'old_lines', 'new_lines',
                       'old_gross', 'new_gross', 'note', 'created_at', 'updated_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(BrokerCommissionRate)
class BrokerCommissionRateAdmin(admin.ModelAdmin):
    """Effective-dated rates. Adding a row is a rate CHANGE, not an edit.

    Editing an existing row silently restates every payable already worked out
    at that rate, so the intended way to change a rate is a NEW row dated from
    the day it takes effect.
    """
    list_display = ('effective_from', 'motor_pct', 'non_motor_pct', 'vat_pct',
                    'admin_pct', 'wht_pct', 'note')
    ordering = ('-effective_from',)


@admin.register(Broker)
class BrokerAdmin(admin.ModelAdmin):
    """Where Finance sets each broker's withholding-tax flag.

    The motor / non-motor split is NOT a broker setting: it is decided per
    policy from whether the policy has a vehicle attached in Graphite, so there
    is nothing to configure here.
    """
    list_display = ('name', 'short_name', 'withholding_tax', 'is_active')
    list_filter = ('is_active', 'withholding_tax')
    list_editable = ('withholding_tax',)
    search_fields = ('name', 'short_name')
