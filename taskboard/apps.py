from django.apps import AppConfig


class TaskboardConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "taskboard"
    verbose_name = "Task Board & Reminders"

    def ready(self):
        from . import signals  # noqa: F401  (registers the assign-day post_save hook)
