"""staff_rewards/admin.py — light admin registration."""
from django.contrib import admin

from .models import StaffPointsAccount, StaffPointsTransaction, StaffSubmission


@admin.register(StaffPointsAccount)
class StaffPointsAccountAdmin(admin.ModelAdmin):
    list_display = ('employee', 'points_balance', 'innovation_points',
                    'business_impact_points', 'health_wellness_points')
    search_fields = ('employee__full_name', 'employee__employee_number')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(StaffSubmission)
class StaffSubmissionAdmin(admin.ModelAdmin):
    list_display = ('employee', 'feature_code', 'pillar', 'status',
                    'points_awarded', 'created_at', 'decided_at')
    list_filter = ('status', 'pillar', 'feature_code')
    search_fields = ('employee__full_name', 'maker_email', 'approver_email')
    readonly_fields = ('created_at', 'updated_at', 'decided_at')


@admin.register(StaffPointsTransaction)
class StaffPointsTransactionAdmin(admin.ModelAdmin):
    list_display = ('account', 'kind', 'points', 'detail', 'occurred_at')
    list_filter = ('kind',)
    search_fields = ('account__employee__full_name', 'detail')
    readonly_fields = ('created_at', 'updated_at')
