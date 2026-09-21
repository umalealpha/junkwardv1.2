from django.contrib import admin

from .models import (Application, Candidate, JobRequisition, JobTitleTier,
                     PositionTier)


@admin.register(PositionTier)
class PositionTierAdmin(admin.ModelAdmin):
    list_display = ("tier", "name", "basic_salary_min", "basic_salary_max", "is_active")
    ordering = ("tier",)


@admin.register(JobTitleTier)
class JobTitleTierAdmin(admin.ModelAdmin):
    list_display = ("title", "tier", "updated_at")
    search_fields = ("title",)
    list_filter = ("tier",)


@admin.register(JobRequisition)
class JobRequisitionAdmin(admin.ModelAdmin):
    list_display = ("title", "department", "status", "headcount", "created_at")
    list_filter = ("status", "employment_type", "department")
    search_fields = ("title", "department")


@admin.register(Candidate)
class CandidateAdmin(admin.ModelAdmin):
    list_display = ("full_name", "email", "created_at")
    search_fields = ("full_name", "email")


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = ("candidate", "requisition", "stage", "match_score", "human_reviewed")
    list_filter = ("stage", "human_reviewed")
