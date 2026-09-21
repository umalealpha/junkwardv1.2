"""staff_rewards/apps.py — app config for the Alpha Staff Rewards module."""
from django.apps import AppConfig


class StaffRewardsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'staff_rewards'
    verbose_name = 'Alpha Staff Rewards'
