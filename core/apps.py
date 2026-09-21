from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = 'core'
    verbose_name = 'Core — Users & Authentication'

    def ready(self):
        # Audit every Omni login creation, whatever path made it (CFO HR plan #04, 19-Sep-2026).
        from core import signals_user_audit  # noqa: F401
