from django.apps import AppConfig


class ExceptionsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name               = 'exceptions'
    verbose_name       = 'Exceptions Engine'

    def ready(self):
        # Register the post_save signal handlers. Wrapped in try/except so the
        # app loads cleanly even if the models a handler is interested in
        # haven't been added yet (e.g. before PR #20 / PR #23 land on main).
        try:
            from . import signals  # noqa: F401
            signals.register_signals()
        except Exception:  # noqa: BLE001
            pass
