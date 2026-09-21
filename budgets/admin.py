from django.contrib import admin
from .models import Budget, BudgetLine


class BudgetLineInline(admin.TabularInline):
    model = BudgetLine
    extra = 1
    raw_id_fields = ['account']


@admin.register(Budget)
class BudgetAdmin(admin.ModelAdmin):
    list_display = ['fiscal_period', 'department', 'status', 'created_by', 'created_at']
    list_filter = ['department', 'status']
    inlines = [BudgetLineInline]
    raw_id_fields = ['fiscal_period', 'created_by', 'approved_by']
