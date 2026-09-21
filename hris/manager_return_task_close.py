"""
hris/manager_return_task_close.py

Close the "Monthly manager return" OmniTask once the manager has actually filed
the return.

Same bug class as hris/feedback_task_close.py, same day (9-Sep-2026), the other
half of the monthly performance cycle. manager_return_cycle CREATES a
`manager_return:<year>-<month>` task for every people-manager, and NOTHING ever
closed it: manager_return_service.submit() flips the return to SUBMITTED and
saves the row, but never touches the task that tracks the work. So
taskboard.services.sweep_due_and_overdue re-raises an OVERDUE notification every
day and email_open_reminders emails a nudge every day, for ever, to managers who
filed on time.

The completion signal here is NOT a set of check-in rows (that is the feedback
half) — it is the ManagerMonthlyReturn row for this manager + period reaching
SUBMITTED or CLEARED. Read straight off manager_return_service's own workflow:

    DRAFT     -> the manager still owes it        -> task stays OPEN
    RETURNED  -> reviewer sent it back for more   -> task stays OPEN (owed again)
    SUBMITTED -> filed, waiting on the reviewer   -> manager's task is DONE
    CLEARED   -> reviewer signed it off           -> manager's task is DONE
    (no row)  -> nothing filed at all             -> task stays OPEN

This is deliberately the SAME test manager_return_cycle uses to chase late
filers (`status__in=[DRAFT, RETURNED]`), so chase and close cannot drift.

RETURNED is the trap. The task belongs to the FILER, and a send-back puts the
work back on the filer's desk — so a returned return must never read as done,
and a task closed at submit has to be re-opened when the reviewer sends it back
(reopen_if_outstanding, called from manager_return_service.send_back). Without
that, closing on submit would leave a sent-back manager with no task at all,
because manager_return_cycle skips any manager who already HAS a task for the
period whatever its status.

Called from taskboard.services.sweep_due_and_overdue (clears the tasks already
stuck in prod, and keeps them clear) and from manager_return_service.submit()
(so the nag stops the moment they file, not the next morning).
"""
from __future__ import annotations

import logging
import re

from django.db import transaction
from django.utils import timezone

log = logging.getLogger(__name__)

SOURCE_PREFIX = 'manager_return:'
_TAG = re.compile(r'^manager_return:(\d{4})-(\d{2})$')

# Only tasks still awaiting action; a done/cancelled task needs no closing and a
# blocked one is a deliberate human state we must not silently overwrite.
_CLOSEABLE = ('pending', 'in_progress', 'partial')


def _period_from_source(source: str):
    """(year, month) out of a `manager_return:2026-08` tag, else None."""
    m = _TAG.match((source or '').strip())
    if not m:
        return None
    year, month = int(m.group(1)), int(m.group(2))
    return (year, month) if 1 <= month <= 12 else None


def _manager_for(user):
    """The payroll Employee behind a task's assignee, or None.

    manager_return_cycle._user_for resolves manager -> user as `emp.user` first
    and the email only as a fallback, so walk it back the same way round.
    `Employee.user` is a OneToOne, so the primary path is exact.

    The email fallback is where this gets dangerous. Omni has duplicate employee
    rows sharing one address (re-hires, leaver copies). A TERMINATED duplicate
    has no return row, so `is_outstanding()` would be answering about the WRONG
    person — here that happens to fail safe (the task stays open) rather than
    closing a live manager's task, but the identity would still be wrong. So:
    leavers are excluded, and a non-unique hit returns None rather than guessing.
    """
    from payroll.models import Employee
    emp = Employee.objects.filter(user=user).first()
    if emp is not None:
        return emp
    email = (getattr(user, 'email', '') or '').strip()
    if not email:
        return None
    matches = list(Employee.objects
                   .filter(email__iexact=email)
                   .exclude(status=Employee.Status.TERMINATED)[:2])
    if len(matches) != 1:
        if matches:
            # The login id, never the address. An app log is a wider audience
            # than the screen, and the id is the stable identifier anyway —
            # the same principle close_if_complete already states below.
            log.warning('manager_return_task_close: login %s matches more than '
                        'one active employee row - refusing to guess which '
                        'manager it is.', getattr(user, 'pk', '?'))
        return None
    return matches[0]


def is_outstanding(manager, year: int, month: int) -> bool:
    """True while this manager still owes the return for the period.

    Deliberately the same test manager_return_cycle uses for its late-filer
    chase: DRAFT or RETURNED (or no row at all) means the work is still theirs.
    """
    from hris.manager_return_models import ManagerMonthlyReturn, ReturnStatus
    return not ManagerMonthlyReturn.objects.filter(
        manager=manager, period_year=year, period_month=month,
        status__in=[ReturnStatus.SUBMITTED, ReturnStatus.CLEARED],
    ).exists()


def _users_for(manager):
    """The login(s) the task could have been assigned to for this manager.

    Mirrors manager_return_cycle._user_for: the linked login first, the email
    only as a fallback.
    """
    user = getattr(manager, 'user', None)
    if user is not None:
        return [user]
    from django.contrib.auth.models import User
    email = (getattr(manager, 'email', '') or '').strip()
    return list(User.objects.filter(email__iexact=email)) if email else []


def close_if_complete(manager, year: int, month: int) -> int:
    """Close this manager's return task for the period if nothing is owed.

    Returns the number of tasks closed (0 or 1 in practice). Never raises: this
    is housekeeping hung off a save path, and a failure here must not lose the
    return the manager just filed.

    The inner savepoint is what actually makes that promise true. submit() is
    @transaction.atomic, so a swallowed DatabaseError from here would leave the
    OUTER transaction marked for rollback: the exception would be caught, submit()
    would return happily, and Django would then roll back the whole block — the
    manager sees "filed", the return is a draft again, and the only trace is a
    warning log. The savepoint confines the damage to this closer (Fable review,
    2026-09-09).
    """
    from core.models import OmniTask
    try:
        with transaction.atomic():
            if manager is None or is_outstanding(manager, year, month):
                return 0
            users = _users_for(manager)
            if not users:
                return 0
            return _close(OmniTask.objects.filter(
                source=f'{SOURCE_PREFIX}{year}-{month:02d}',
                assignee__in=users, status__in=_CLOSEABLE))
    except Exception:    # noqa: BLE001 - housekeeping, never break the caller
        # Employee NUMBER, never a name: an app log is a wider audience than the
        # screen, and the number is the stable identifier for debugging anyway.
        log.warning('manager_return_task_close: could not close the %s-%02d task '
                    'for employee %s', year, month,
                    getattr(manager, 'employee_number', None) or getattr(manager, 'pk', '?'),
                    exc_info=True)
        return 0


def reopen_if_outstanding(manager, year: int, month: int) -> int:
    """Re-open a task we closed at submit, when the reviewer sends it back.

    close_if_complete fires on submit, so by the time send_back runs the filer's
    task is DONE. A RETURNED return is outstanding work with the filer again, so
    without this they would be left with nothing on their dashboard and nothing
    would ever re-raise it.

    Only ever re-opens a DONE task - one a human CANCELLED stays cancelled, and
    one already open is left exactly as it is.

    Savepointed for the same reason as close_if_complete: send_back() is
    @transaction.atomic, so a swallowed DatabaseError without it would roll back
    the reviewer's send-back itself.
    """
    from core.models import OmniTask
    try:
        with transaction.atomic():
            if manager is None or not is_outstanding(manager, year, month):
                return 0
            users = _users_for(manager)
            if not users:
                return 0
            return (OmniTask.objects
                    .filter(source=f'{SOURCE_PREFIX}{year}-{month:02d}',
                            assignee__in=users, status='done')
                    .update(status='pending', completed_at=None,
                            updated_at=timezone.now()))
    except Exception:    # noqa: BLE001 - housekeeping, never break the caller
        log.warning('manager_return_task_close: could not re-open the %s-%02d '
                    'task for employee %s', year, month,
                    getattr(manager, 'employee_number', None) or getattr(manager, 'pk', '?'),
                    exc_info=True)
        return 0


def close_completed_manager_return_tasks() -> int:
    """Sweep every open manager-return task and close the ones already filed.

    The catch-up path: it clears the tasks that are ALREADY stuck in prod (the
    reported bug) and covers every write path - the form, the admin, a shell
    fix, an import - without a hook on each of them.
    """
    from core.models import OmniTask
    closed = 0
    tasks = (OmniTask.objects
             .filter(source__startswith=SOURCE_PREFIX, status__in=_CLOSEABLE)
             .select_related('assignee'))
    for task in tasks:
        period = _period_from_source(task.source)
        if period is None:
            continue                      # not a period tag - leave it alone
        try:
            manager = _manager_for(task.assignee)
            if manager is None or is_outstanding(manager, *period):
                continue
            closed += _close(OmniTask.objects.filter(pk=task.pk, status__in=_CLOSEABLE))
        except Exception:    # noqa: BLE001 - one bad row must not stop the sweep
            log.warning('manager_return_task_close: skipped task %s (%s)',
                        task.pk, task.source, exc_info=True)
            continue
    return closed


def _close(qs) -> int:
    """Mark the queryset done + stamp completed_at. Status-filtered by the
    caller, so a task completed by a human between read and write is not
    re-stamped."""
    return qs.update(status='done', completed_at=timezone.now(),
                     updated_at=timezone.now())
