from django.contrib import admin

from .models import (
    Employee, PayrollImportBatch, PayrollPeriod, PayrollSetting, Payslip,
    PayslipComponent, PayslipLine, TaxBracket,
)


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'employee_number', 'department', 'job_title', 'status')
    list_filter  = ('department', 'status', 'company')
    search_fields = ('full_name', 'employee_number', 'department', 'email')


from .contract_models import EmploymentContract  # noqa: E402


@admin.register(EmploymentContract)
class EmploymentContractAdmin(admin.ModelAdmin):
    list_display  = ('employee', 'contract_type', 'start_date', 'end_date',
                     'probation_end_date', 'status', 'basic')
    list_filter   = ('contract_type', 'status')
    search_fields = ('employee__full_name', 'employee__employee_number')


@admin.register(TaxBracket)
class TaxBracketAdmin(admin.ModelAdmin):
    list_display = ('name', 'effective_from', 'lower_bound', 'upper_bound', 'base_amount', 'rate_pct', 'is_active')
    list_filter  = ('is_active', 'effective_from')


@admin.register(PayslipComponent)
class PayslipComponentAdmin(admin.ModelAdmin):
    list_display = ('sort_order', 'code', 'name', 'kind', 'is_taxable', 'is_active')
    list_filter  = ('kind', 'is_active', 'is_taxable')
    search_fields = ('code', 'name')
    ordering = ('sort_order',)


@admin.register(PayrollPeriod)
class PayrollPeriodAdmin(admin.ModelAdmin):
    list_display = ('period_name', 'start_date', 'end_date', 'pay_date', 'status')
    list_filter  = ('status',)


class PayslipLineInline(admin.TabularInline):
    model = PayslipLine
    extra = 0
    autocomplete_fields = ('component',)


@admin.register(Payslip)
class PayslipAdmin(admin.ModelAdmin):
    list_display = ('employee', 'period', 'gross_amount', 'paye_amount', 'net_amount', 'ctc_amount', 'status')
    list_filter  = ('status', 'period', 'company')
    search_fields = ('employee__full_name',)
    inlines = [PayslipLineInline]


@admin.register(PayrollImportBatch)
class PayrollImportBatchAdmin(admin.ModelAdmin):
    list_display = ('file_name', 'period', 'status', 'rows_total', 'rows_imported', 'rows_skipped_dup', 'created_at')
    list_filter  = ('status',)
    readonly_fields = ('parsed_rows', 'validation_errors', 'created_at', 'committed_at')


@admin.register(PayrollSetting)
class PayrollSettingAdmin(admin.ModelAdmin):
    """Registered so payroll/config.py's "Finance/admin edits via the admin,
    no deploy needed" claim is true (coordinator review, 2026-09-12 — the
    model existed with no screen to edit it on)."""
    list_display  = ('key', 'value', 'description', 'updated_at')
    search_fields = ('key', 'description')
