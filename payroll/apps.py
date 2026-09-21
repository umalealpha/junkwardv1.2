from django.apps import AppConfig


class PayrollConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'payroll'
    verbose_name = 'Payroll'

    def ready(self):
        # Wire the paid-period lock signals (Unami wishlist 2026-06-02).
        from . import lock_signals  # noqa: F401
