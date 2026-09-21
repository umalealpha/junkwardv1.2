from django.contrib import admin

from .models import Engagement, Finding, FollowUp, ManagementResponse


@admin.register(Engagement)
class EngagementAdmin(admin.ModelAdmin):
    list_display = ('reference', 'title', 'engagement_type', 'status', 'lead_auditor')
    list_filter = ('engagement_type', 'status')
    search_fields = ('reference', 'title')


@admin.register(Finding)
class FindingAdmin(admin.ModelAdmin):
    list_display = ('reference', 'title', 'engagement', 'rating', 'status', 'fraud_flag')
    list_filter = ('rating', 'status', 'fraud_flag', 'regulatory_tag', 'root_cause')
    search_fields = ('reference', 'title', 'criteria', 'condition')


@admin.register(ManagementResponse)
class ManagementResponseAdmin(admin.ModelAdmin):
    list_display = ('finding', 'owner', 'target_date', 'agreed')
    list_filter = ('agreed',)


@admin.register(FollowUp)
class FollowUpAdmin(admin.ModelAdmin):
    list_display = ('finding', 'status', 'next_retest_date')
    list_filter = ('status',)
