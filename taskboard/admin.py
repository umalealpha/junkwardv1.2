from django.contrib import admin

from .models import CompletionNote, Notification, TaskboardSetting


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("recipient", "task", "type", "acknowledged", "seen_at", "created_at")
    list_filter = ("type", "acknowledged")
    raw_id_fields = ("recipient", "task")


@admin.register(CompletionNote)
class CompletionNoteAdmin(admin.ModelAdmin):
    list_display = ("task", "author", "interaction_seconds", "created_at")
    raw_id_fields = ("task", "author")


@admin.register(TaskboardSetting)
class TaskboardSettingAdmin(admin.ModelAdmin):
    """Registered so Finance can actually edit the payment ageing-escalation
    threshold on screen (CFO 2026-09-14) — a setting model with no screen to
    edit it on is not a setting Finance can change."""
    list_display  = ('key', 'value', 'description', 'updated_at')
    search_fields = ('key', 'description')
