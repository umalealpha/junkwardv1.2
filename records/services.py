"""
records/services.py — the one place a movement is recorded.

A movement and the item's current position must never disagree: the register's
whole job is answering "who has this file right now?". Both writes happen here,
in one transaction, so a caller cannot log a movement and forget to move the item.
"""

from django.db import transaction

from .models import RecordItem, RecordMovement


class RecordMovementError(Exception):
    """A movement that must not be recorded."""


@transaction.atomic
def move_record(record: RecordItem, *, kind: str, moved_at, recorded_by,
                to_employee=None, to_custodian: str = '', to_location: str = '',
                reason: str = '', due_back_on=None) -> RecordMovement:
    """Record one leg of custody and move the item to match it."""

    # Fable review: the guards below used to run on the STALE instance and only
    # then take the lock, so a concurrent destroy and issue could race past them.
    # Lock first, validate second.
    record = RecordItem.objects.select_for_update().get(pk=record.pk)

    if kind in (RecordMovement.Kind.ISSUE, RecordMovement.Kind.TRANSFER) \
            and not (to_employee or to_custodian):
        raise RecordMovementError(
            'Say who is taking it — a record cannot be issued to nobody.')

    if record.status == RecordItem.Status.DESTROYED:
        raise RecordMovementError(
            f'{record.reference} is recorded as destroyed and cannot be moved.')

    if kind == RecordMovement.Kind.DESTROY:
        # Legal hold beats retention, always. This is the check the whole
        # legal_hold flag exists for.
        if record.legal_hold:
            raise RecordMovementError(
                f'{record.reference} is under legal hold and must not be '
                f'destroyed. {record.legal_hold_note or ""}'.strip())
        if not record.may_be_destroyed:
            raise RecordMovementError(
                f'{record.reference} has not reached its retention date '
                f'({record.retention_until or "none set"}).')

    mv = RecordMovement.objects.create(
        record=record, kind=kind, moved_at=moved_at, recorded_by=recorded_by,
        from_employee=record.current_holder,
        from_custodian=record.current_custodian,
        from_location=record.current_location,
        to_employee=to_employee, to_custodian=to_custodian,
        to_location=to_location, reason=reason, due_back_on=due_back_on,
    )

    record.current_holder    = to_employee
    record.current_custodian = to_custodian
    record.current_location  = to_location
    # Only a live issue carries a due-back date; returning or archiving clears it.
    record.due_back_on = due_back_on if kind in (
        RecordMovement.Kind.ISSUE, RecordMovement.Kind.TRANSFER) else None
    record.status = {
        RecordMovement.Kind.ISSUE:    RecordItem.Status.ISSUED,
        RecordMovement.Kind.RETURN:   RecordItem.Status.IN_STORE,
        RecordMovement.Kind.TRANSFER: RecordItem.Status.ISSUED,
        RecordMovement.Kind.ARCHIVE:  RecordItem.Status.ARCHIVED,
        RecordMovement.Kind.DESTROY:  RecordItem.Status.DESTROYED,
    }[kind]

    # The day counter's clock. An ISSUE starts it. A TRANSFER hands the file to
    # someone else WITHOUT it coming back, so the clock keeps running from the
    # original issue — that is the number Admin needs ("how long has this been
    # out of the repository", not "how long with the current person"). Anything
    # that ends the file being out clears it.
    if kind == RecordMovement.Kind.ISSUE:
        record.out_since = moved_at
    elif kind == RecordMovement.Kind.TRANSFER:
        record.out_since = record.out_since or moved_at
    else:
        record.out_since = None

    record.save(update_fields=['current_holder', 'current_custodian',
                               'current_location', 'status', 'due_back_on',
                               'out_since', 'updated_at'])
    return mv
