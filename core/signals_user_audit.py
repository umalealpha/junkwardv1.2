# Register this receiver by importing this module from core/apps.py ready(), e.g.:
# from . import signals_user_audit  # noqa: F401
import logging

from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from core.models import AuditLog

logger = logging.getLogger(__name__)


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def audit_user_created(sender, instance, created, **kwargs):
    if not created:
        return

    try:
        AuditLog.objects.create(
            table_name='auth_user',
            record_id=str(instance.pk),
            action='create',
            new_values={
                'username': instance.get_username(),
                'email': instance.email,
                'is_superuser': instance.is_superuser,
            },
            description='Omni login created',
        )
    except Exception:
        logger.exception('User created audit logging failed for pk=%s', instance.pk)
