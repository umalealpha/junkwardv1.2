from django.apps import AppConfig


class CiMonitorConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'ci_monitor'
    verbose_name = 'Gate monitor'
