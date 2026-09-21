from django.contrib import admin

from .models import (
    RewardPartner, RewardProgram, RewardMember, PointsTransaction, DrivingScore,
)


@admin.register(RewardProgram)
class RewardProgramAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'is_active')
    list_filter = ('is_active',)


@admin.register(RewardPartner)
class RewardPartnerAdmin(admin.ModelAdmin):
    list_display = ('name', 'kind', 'status')
    list_filter = ('kind', 'status')


@admin.register(RewardMember)
class RewardMemberAdmin(admin.ModelAdmin):
    list_display = ('customer_name', 'tier', 'points_balance', 'claims_free_months', 'company', 'is_active')
    list_filter = ('tier', 'is_active', 'company')
    search_fields = ('customer_name', 'policy_number')


@admin.register(PointsTransaction)
class PointsTransactionAdmin(admin.ModelAdmin):
    list_display = ('member', 'kind', 'points', 'program', 'partner', 'occurred_at')
    list_filter = ('kind', 'program')


@admin.register(DrivingScore)
class DrivingScoreAdmin(admin.ModelAdmin):
    list_display = ('member', 'period', 'vehicle_reg', 'score', 'points_awarded', 'source')
    list_filter = ('period', 'source')
