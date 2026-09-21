"""
payroll/lock_signals.py — refuse edits to a paid payroll period.

Per Unami's wishlist (Fw: Omni, 2026-06-02): "Lock paid batch — so it cannot
be altered."

Once a PayrollPeriod transitions to status='paid', subsequent saves that
change any field other than `notes` are refused.  Same rule applies to the
Payslip rows attached to a paid period.

Bypass: pass `instance._allow_lock_bypass = True` on the in-memory object
before save(); used by reversal flows.
"""
from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db.models.signals import pre_save, pre_delete
from django.dispatch import receiver


def _is_paid(instance) -> bool:
    return getattr(instance, 'status', '') == 'paid'


@receiver(pre_save, sender='payroll.PayrollPeriod')
def _guard_period_save(sender, instance, **kwargs):
    if getattr(instance, '_allow_lock_bypass', False):
        return
    if instance.pk is None:
        return
    try:
        prev = sender.objects.only('status', 'notes').get(pk=instance.pk)
    except sender.DoesNotExist:
        return
    if prev.status != 'paid':
        return
    # Only `notes` may be edited on a paid period.
    if instance.status != 'paid':
        raise ValidationError(
            'Payroll period is paid and locked. Cannot change status. '
            'Create a reversal entry instead.'
        )
    # If notes is the only change, allow.  Otherwise refuse.
    for field in instance._meta.fields:
        n = field.name
        if n in ('notes', 'status', 'updated_at'):
            continue
        if getattr(instance, n, None) != getattr(prev, n, None):
            raise ValidationError(
                f'Payroll period is paid and locked. Field {n!r} cannot be modified.'
            )


@receiver(pre_save, sender='payroll.Payslip')
def _guard_payslip_save(sender, instance, **kwargs):
    if getattr(instance, '_allow_lock_bypass', False):
        return
    period = getattr(instance, 'period', None)
    if not period or period.status != 'paid':
        return
    if instance.pk is None:
        raise ValidationError(
            'Cannot create a payslip in a paid (locked) period.'
        )
    raise ValidationError(
        'Payslip is in a paid (locked) period. Cannot be modified.'
    )


@receiver(pre_delete, sender='payroll.PayrollPeriod')
def _guard_period_delete(sender, instance, **kwargs):
    if instance.status == 'paid' and not getattr(instance, '_allow_lock_bypass', False):
        raise ValidationError('Paid payroll periods cannot be deleted.')
