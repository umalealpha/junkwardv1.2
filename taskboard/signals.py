"""taskboard/signals.py — raise the gentle assign-day toast when an OmniTask is created.

Wired in apps.ready(). Non-invasive (doesn't touch core's task-creation code): fires
only on create, and notify_on_assign is idempotent, so bulk creates / re-saves never
duplicate the toast.
"""
from django.db.models.signals import post_save
from django.dispatch import receiver

from core.models import OmniTask

from . import services


@receiver(post_save, sender=OmniTask, dispatch_uid="taskboard_assign_notice")
def create_assign_notification(sender, instance, created, **kwargs):
    if created:
        services.notify_on_assign(instance)
