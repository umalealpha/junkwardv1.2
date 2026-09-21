from django.apps import AppConfig


class InternalAuditConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'internal_audit'
    verbose_name = 'Internal Audit'
