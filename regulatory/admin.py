from django.contrib import admin

from .models import CapitalRequirementParameter, RegulatoryCapitalSnapshot


@admin.register(CapitalRequirementParameter)
class CapitalRequirementParameterAdmin(admin.ModelAdmin):
    list_display = ('code', 'label', 'value', 'is_active', 'effective_from')
    list_filter  = ('is_active',)
    search_fields = ('code', 'label')


@admin.register(RegulatoryCapitalSnapshot)
class RegulatoryCapitalSnapshotAdmin(admin.ModelAdmin):
    list_display = ('as_of_date', 'available_capital', 'required_capital',
                    'capital_adequacy_ratio', 'status', 'prepared_by', 'approved_by')
    list_filter  = ('status',)
    readonly_fields = ('available_capital', 'required_capital',
                       'capital_adequacy_ratio', 'components',
                       'created_at', 'updated_at')
    date_hierarchy = 'as_of_date'
