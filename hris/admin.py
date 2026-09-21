from django.contrib import admin

from .co_review_models import AdditionalReviewer
from .models import (
    CompetencyArea, Grade, HRISProfile, LeaveRequest, LeaveType,
    LetterRequest, OKR, PerformanceReview, TrackingDirective,
    PerformanceTarget, PerformanceTargetResult,
)


class AdditionalReviewerInline(admin.TabularInline):
    """Operations reviewers assigned to this employee (feedback oversight only —
    no leave approval, not in the co-review blend)."""
    model = AdditionalReviewer
    extra = 0
    autocomplete_fields = ('reviewer',)
    fields = ('reviewer', 'reason')


@admin.register(AdditionalReviewer)
class AdditionalReviewerAdmin(admin.ModelAdmin):
    list_display  = ('reviewer', 'profile', 'reason', 'created_at')
    search_fields = ('reviewer__full_name', 'profile__employee__full_name')
    autocomplete_fields = ('profile', 'reviewer')


@admin.register(PerformanceTarget)
class PerformanceTargetAdmin(admin.ModelAdmin):
    list_display = ('profile', 'metric', 'target_value', 'unit', 'cadence', 'source', 'active')
    list_filter = ('active', 'cadence', 'source', 'unit')
    search_fields = ('profile__employee__full_name', 'metric')
    autocomplete_fields = ('profile',)


@admin.register(PerformanceTargetResult)
class PerformanceTargetResultAdmin(admin.ModelAdmin):
    list_display = ('profile', 'target', 'period_year', 'period_month', 'actual_value', 'achieved', 'source_used')
    list_filter = ('period_year', 'period_month', 'achieved')
    search_fields = ('profile__employee__full_name', 'target__metric')


@admin.register(LetterRequest)
class LetterRequestAdmin(admin.ModelAdmin):
    list_display  = ('employee', 'letter_type', 'status', 'reference',
                     'signatory', 'decided_at', 'created_at')
    list_filter   = ('status', 'letter_type')
    search_fields = ('employee__full_name', 'reference', 'purpose')
    readonly_fields = ('issued_snapshot', 'reference', 'decided_at')


@admin.register(TrackingDirective)
class TrackingDirectiveAdmin(admin.ModelAdmin):
    """CFO/HR override of who is expected to track time (wins over the guess)."""
    list_display  = ['employee', 'expected_to_track', 'note', 'updated_by', 'updated_at']
    list_filter   = ['expected_to_track']
    search_fields = ['employee__full_name', 'employee__employee_number']
    autocomplete_fields = ['employee']
    readonly_fields = ['id', 'created_at', 'updated_at']

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(Grade)
class GradeAdmin(admin.ModelAdmin):
    list_display  = ('code', 'name', 'level', 'midpoint', 'spread', 'is_active')
    search_fields = ('code', 'name')
    list_filter   = ('level', 'is_active')


@admin.register(HRISProfile)
class HRISProfileAdmin(admin.ModelAdmin):
    list_display  = ('employee', 'grade', 'manager', 'location', 'gender', 'talent_segment')
    list_filter   = ('grade', 'gender', 'location', 'talent_segment')
    search_fields = ('employee__full_name', 'employee__email')
    autocomplete_fields = ('employee', 'manager', 'co_manager', 'grade')
    inlines = (AdditionalReviewerInline,)


@admin.register(CompetencyArea)
class CompetencyAreaAdmin(admin.ModelAdmin):
    list_display  = ('code', 'name', 'sort_order')
    search_fields = ('code', 'name')


@admin.register(PerformanceReview)
class PerformanceReviewAdmin(admin.ModelAdmin):
    list_display  = ('profile', 'period', 'overall_rating', 'status', 'review_date')
    list_filter   = ('period', 'status')
    search_fields = ('profile__employee__full_name',)
    autocomplete_fields = ('profile', 'reviewer')


@admin.register(OKR)
class OKRAdmin(admin.ModelAdmin):
    list_display  = ('name', 'profile', 'period', 'weight_pct', 'score_h1', 'score_h2')
    list_filter   = ('period',)
    search_fields = ('name', 'profile__employee__full_name')


@admin.register(LeaveType)
class LeaveTypeAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'default_annual_days', 'is_paid', 'is_active')


@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    list_display  = ('profile', 'leave_type', 'start_date', 'end_date', 'days', 'status')
    list_filter   = ('status', 'leave_type')
    search_fields = ('profile__employee__full_name',)
    autocomplete_fields = ('profile', 'approver')


# ── Manager objectives (CFO 2026-09-09) ──────────────────────────────────────
# The definitions are editable; the RUNS are not. A run is the evidence of what
# was asked and what happened — if a verdict can be typed over, the whole board
# becomes an opinion again.
from .weekly_objective_models import WeeklyObjective, WeeklyObjectiveRun  # noqa: E402


@admin.register(WeeklyObjective)
class WeeklyObjectiveAdmin(admin.ModelAdmin):
    list_display = ('profile', 'key', 'title', 'cadence', 'direction', 'target', 'active')
    list_filter = ('cadence', 'direction', 'active', 'counter')
    search_fields = ('key', 'title', 'profile__employee__full_name')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(WeeklyObjectiveRun)
class WeeklyObjectiveRunAdmin(admin.ModelAdmin):
    list_display = ('objective', 'period_start', 'cadence', 'baseline', 'target',
                    'actual', 'met', 'settled_at')
    list_filter = ('cadence', 'met', 'period_start')
    search_fields = ('objective__key', 'objective__profile__employee__full_name')
    readonly_fields = ('objective', 'period_start', 'cadence', 'baseline', 'target',
                       'direction', 'actual', 'met', 'settled_at', 'settle_error',
                       'task', 'created_at', 'updated_at')

    def has_add_permission(self, request):
        return False        # runs are raised by the cycle, never by hand

    def has_delete_permission(self, request, obj=None):
        return False        # the record of what was asked must survive a bad week
