from django.apps import AppConfig


class WatchdogConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "watchdog"
    verbose_name = "Omni Watchdog"

    def ready(self):
        # Import the check modules so their @register decorators populate the
        # REGISTRY. Kept inside ready() so importing the app never has import-time
        # side effects on the models layer.
        from watchdog import checks  # noqa: F401
