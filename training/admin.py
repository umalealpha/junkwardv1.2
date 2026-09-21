from django.contrib import admin
from .models import TrainingModule, TrainingQuestion, TrainingAttempt


class QuestionInline(admin.StackedInline):
    model  = TrainingQuestion
    extra  = 1
    fields = ("order", "prompt",
              "option_a", "option_b", "option_c", "option_d",
              "correct_letter")


@admin.register(TrainingModule)
class TrainingModuleAdmin(admin.ModelAdmin):
    list_display  = ("title", "slug", "open_from", "open_until",
                     "pass_mark_pct", "is_published")
    list_filter   = ("is_published", "mandatory_for_all")
    search_fields = ("title", "slug")
    inlines       = [QuestionInline]
    fieldsets = (
        ("Basics",  {"fields": ("slug", "title", "subtitle", "is_published")}),
        ("Content", {"fields": ("body_html", "video_url", "pdf")}),
        ("Rules",   {"fields": ("pass_mark_pct", "open_from", "open_until",
                                "mandatory_for_all")}),
    )
    readonly_fields = ()


@admin.register(TrainingAttempt)
class TrainingAttemptAdmin(admin.ModelAdmin):
    list_display  = ("user", "module", "score_pct", "passed",
                     "certificate_number", "completed_at")
    list_filter   = ("module", "passed")
    search_fields = ("user__username", "user__email", "certificate_number")
    readonly_fields = ("answers", "score_pct", "passed", "completed_at",
                       "certificate_number")
