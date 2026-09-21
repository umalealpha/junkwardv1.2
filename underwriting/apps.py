"""underwriting/apps.py"""
from django.apps import AppConfig


class UnderwritingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'underwriting'
    verbose_name = 'Underwriting Documents'
