"""payroll/archive_service.py — Terminated Employee Archive.

Feature request from Oprah Mogomotsi (Omni bug report d0f05edc-06f5-4084-
94ce-b613e54e7665, 13-Aug-2026): archiving a terminated employee must be a
manual, reason-carrying HR action — never a delete. Archived records keep
every field (profile, payroll history, payslips, documents); they are only
hidden from active lists/pay runs and made read-only. Retention default is 7
years from termination (Botswana DPA Act No. 18 of 2024 + BURS payroll
retention rules), configurable per record. No auto-purge — expiring records
are only ever flagged for HR review.

Every archive/unarchive writes one core.AuditLog row (reason, user, time) —
the trail exportable for NBFIRA inspection via the existing audit-log viewer.
"""
from __future__ import annotations

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import AuditLog

from .models import Employee


TERMINATION_REASONS = {
    'resignation', 'dismissal', 'retirement', 'redundancy',
    'end_of_contract', 'death', 'other',
}


def terminate_employee(employee: Employee, *, actor, termination_date, reason: str,
                       notes: str = '', archive: bool = False) -> Employee:
    """Record an employee's exit: set the termination date + status TERMINATED.

    Feature request from D. Ikgopoleng (Omni bug report dfc0b768-8ded-4610-
    8109-c142fb374bde, 10-Sep-2026), authorised by the CFO 10-Sep-2026. HR had
    no way to close a leaver's profile — the capability existed only as a raw
    status edit, with no exit reason and no single audit row.

    History is never deleted. `archive=True` additionally hides the record from
    active lists via the existing archive path (7-year retention).

    Raises ValidationError when the reason or date is missing or invalid, when
    the employee is already terminated, when the date is in the future, or when
    the leaver still physically holds an asset (the same Asset Control gate the
    archive path enforces — CFO 2026-09-02).
    """
    reason = (reason or '').strip().lower()
    if reason not in TERMINATION_REASONS:
        raise ValidationError(
            f'Termination reason must be one of: {", ".join(sorted(TERMINATION_REASONS))}.'
        )
    if not termination_date:
        raise ValidationError('Termination date is required.')
    if employee.status == Employee.Status.TERMINATED:
        raise ValidationError(f'{employee.full_name} is already terminated.')
    if termination_date > timezone.localdate():
        raise ValidationError('Termination date cannot be in the future.')
    if employee.hire_date and termination_date < employee.hire_date:
        raise ValidationError('Termination date cannot be before the hire date.')

    # Same Asset Control gate as archiving — a laptop must not leave with the
    # person unrecorded. Lazy import avoids a payroll<->assets circular import.
    from assets.control_services import assets_blocking_offboarding
    outstanding = list(assets_blocking_offboarding(employee))
    if outstanding:
        tags = ', '.join(a.tag_number for a in outstanding[:10])
        more = '' if len(outstanding) <= 10 else f' (+{len(outstanding) - 10} more)'
        raise ValidationError(
            f'{employee.full_name} still holds {len(outstanding)} asset(s): {tags}{more}. '
            'Return or write off every asset before terminating the leaver.'
        )

    employee.status = Employee.Status.TERMINATED
    employee.termination_date = termination_date
    employee.save(update_fields=['status', 'termination_date', 'updated_at'], skip_audit=True)

    AuditLog.objects.create(
        table_name='payroll.Employee',
        record_id=str(employee.pk),
        action=AuditLog.Action.UPDATE,
        user=actor if getattr(actor, 'is_authenticated', False) else None,
        new_values={'status': Employee.Status.TERMINATED,
                    'termination_date': str(termination_date),
                    'termination_reason': reason,
                    'notes': notes},
        description=(f'Terminated {employee.full_name} on {termination_date} '
                     f'— reason: {reason}' + (f' ({notes})' if notes else '')),
    )

    if archive:
        archive_employee(
            employee, actor=actor,
            reason=f'Termination ({reason})' + (f' — {notes}' if notes else ''),
        )

    # Close the Omni login (CFO 2026-09-18). Only fires when the last working
    # day has ALREADY passed — a termination recorded on the day itself is left
    # to the nightly sweep so nobody is locked out mid-handover. See
    # payroll.offboard_access for why closing the account is the only thing
    # that ends a live phone or browser session.
    from .offboard_access import close_omni_access, should_close
    if should_close(employee):
        close_omni_access(employee, actor=actor)

    # Dorothy Ikgopoleng, 2026-09-11: IT and Payroll must get "a prompt so that
    # they are able to ensure all offboarding checks are done on their end".
    # Best-effort — a notification failure must never undo a recorded exit.
    from core.notifications import notify_offboarding_started
    notify_offboarding_started(
        employee, termination_date=termination_date, reason=reason, actor=actor,
    )
    return employee


def archive_employee(employee: Employee, *, actor, reason: str, retention_years: int | None = None) -> Employee:
    """Archive a terminated employee. Requires a termination date + a reason.

    Raises ValidationError if the employee has no termination date set, or
    if `reason` is blank — both are mandatory per the feature request.
    """
    reason = (reason or '').strip()
    if not reason:
        raise ValidationError('Archive reason is required.')
    if not employee.termination_date:
        raise ValidationError('Employee must have a termination date before archiving.')
    if employee.is_archived:
        raise ValidationError('Employee is already archived.')

    # Offboarding gate (Unami / CFO 19-Sep-2026): "offboarding should be done before Omni
    # archiving or removal" — resignation letter, IT + HC documents, HC/manager/Finance
    # sign-offs. Lazy import: hris imports payroll.
    from hris.offboarding_service import archive_block_reason
    blocked = archive_block_reason(employee)
    if blocked:
        raise ValidationError(blocked)

    # Asset Control & Handover gate (CFO 2026-09-02, spec §7 / AC6): an exit
    # cannot be finalised while the leaver still physically holds an asset
    # (issued or in use). They must return or write it off first — then the
    # asset drops into the Returned/Spare pool and stops blocking. Lazy import
    # avoids a payroll<->assets circular import (assets imports payroll.Employee).
    from assets.control_services import assets_blocking_offboarding
    outstanding = list(assets_blocking_offboarding(employee))
    if outstanding:
        tags = ', '.join(a.tag_number for a in outstanding[:10])
        more = '' if len(outstanding) <= 10 else f' (+{len(outstanding) - 10} more)'
        raise ValidationError(
            f'{employee.full_name} still holds {len(outstanding)} asset(s): {tags}{more}. '
            'Return or write off every asset before archiving the leaver.'
        )

    employee.is_archived = True
    employee.archived_at = timezone.now()
    employee.archived_by = actor if getattr(actor, 'is_authenticated', False) else None
    employee.archive_reason = reason
    if retention_years is not None:
        employee.retention_years = retention_years
    # Fable review (2026-08-13): archiving only checked termination_date, so an
    # employee whose status was never flipped to TERMINATED could be archived
    # yet still get picked up by the pay-run/amendment code that keys off
    # `status` alone. Archiving now always forces status=TERMINATED too — the
    # two flags stay in lock-step so every existing status check is also an
    # is_archived-safe check.
    employee.status = Employee.Status.TERMINATED
    # skip_audit: we write our own single, reason-carrying AuditLog row below
    # (the established pattern for AuditableMixin models — see core/models.py
    # BUG-003) instead of letting the mixin's generic auto-row double up.
    employee.save(update_fields=[
        'is_archived', 'archived_at', 'archived_by', 'archive_reason', 'retention_years',
        'status', 'updated_at',
    ], skip_audit=True)

    AuditLog.objects.create(
        table_name='payroll.Employee',
        record_id=str(employee.pk),
        action=AuditLog.Action.UPDATE,
        user=actor if getattr(actor, 'is_authenticated', False) else None,
        new_values={'is_archived': True, 'archive_reason': reason,
                    'retention_years': employee.retention_years},
        description=f'Archived {employee.full_name} — reason: {reason}',
    )
    return employee


def unarchive_employee(employee: Employee, *, actor, reason: str) -> Employee:
    """Unarchive an employee (rehire, audit, or legal hold). Requires a reason."""
    reason = (reason or '').strip()
    if not reason:
        raise ValidationError('Unarchive reason is required.')
    if not employee.is_archived:
        raise ValidationError('Employee is not archived.')

    employee.is_archived = False
    employee.save(update_fields=['is_archived', 'updated_at'], skip_audit=True)

    AuditLog.objects.create(
        table_name='payroll.Employee',
        record_id=str(employee.pk),
        action=AuditLog.Action.UPDATE,
        user=actor if getattr(actor, 'is_authenticated', False) else None,
        new_values={'is_archived': False},
        description=f'Unarchived {employee.full_name} — reason: {reason}',
    )
    return employee


def records_nearing_retention_expiry(*, within_days: int = 90):
    """Archived employees whose retention_expiry_date falls within `within_days`.

    Flags for HR review only — never auto-purges (per the feature request).
    """
    today = timezone.localdate()
    horizon = today + timedelta(days=within_days)
    due = []
    for emp in Employee.objects.filter(is_archived=True, termination_date__isnull=False):
        expiry = emp.retention_expiry_date
        if expiry and today <= expiry <= horizon:
            due.append(emp)
    return due
