"""healthcare app — quick-quote from PDF/file → DeepSeek → rate card."""
from django.apps import AppConfig


class HealthcareConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'healthcare'
    verbose_name = 'Health Care'
