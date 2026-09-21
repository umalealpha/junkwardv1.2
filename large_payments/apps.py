from django.apps import AppConfig


class LargePaymentsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'large_payments'
    verbose_name = 'Large Payment Authorisation'

    def ready(self):
        """Register the CEO's no-login buttons.

        Done HERE rather than by editing core/magic_action.py, so the whole
        feature lives in its own app: nothing preexisting is edited to add it,
        and removing the app removes the actions with it. ACTIONS is that
        module's documented extension point.
        """
        from .magic import register
        register()
