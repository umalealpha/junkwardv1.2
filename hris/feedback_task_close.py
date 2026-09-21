"""
hris/feedback_task_close.py

Close the "Monthly performance feedback" OmniTask once the manager has actually
given the feedback.

Bug 13869f41 (Kakale Botana, 9-Sep-2026): monthly_feedback_cycle CREATES a
`monthly_feedback:<year>-<month>` task for every manager who still owes a
MonthlyCheckIn, but nothing ever CLOSED it. The manager records the check-ins
(in-app, or through the sign-in-free one-click page in
hris/manager_feedback_actions.py) and those write MonthlyCheckIn rows only —
the task stays PENDING for ever. So sweep_due_and_overdue keeps re-raising an
OVERDUE reminder and email_open_reminders keeps emailing a daily nudge, while
the one-click page correctly tells her there is nothing outstanding. She filed
all five of her team's August check-ins on 1-Sep and was still being chased on
6, 7, 8 and 9-Sep: "I have re-done the task twice ... it takes me to a page
that confirms that there is nothing outstanding."

The completion signal is the DATA, not a button: a manager owes nothing for a
period once every non-terminated report has a MonthlyCheckIn for it. That is
the same test monthly_feedback_cycle uses to decide whether to raise the task,
so raise and close now agree and cannot drift.

Called from taskboard.services.sweep_due_and_overdue (clears tasks already
stuck in prod, and keeps them clear) and from the one-click submit page (so the
nag stops the moment the last person is done, not the next morning).
"""
from __future__ import annotations

import logging
import re

from django.utils import timezone

log = logging.getLogger(__name__)

SOURCE_PREFIX = 'monthly_feedback:'
_TAG = re.compile(r'^monthly_feedback:(\d{4})-(\d{2})$')

# Only tasks still awaiting action; a done/cancelled task needs no closing and a
# blocked one is a deliberate human state we must not silently overwrite.
_CLOSEABLE = ('pending', 'in_progress', 'partial')


def _period_from_source(source: str):
    """(year, month) out of a `monthly_feedback:2026-08` tag, else None."""
    m = _TAG.match((source or '').strip())
    if not m:
        return None
    year, month = int(m.group(1)), int(m.group(2))
    return (year, month) if 1 <= month <= 12 else None


def _manager_for(user):
    """The payroll Employee behind a task's assignee, or None.

    monthly_feedback_cycle resolves manager -> user as `mgr.user` first and the
    email only as a fallback, so walk it back the same way round. `Employee.user`
    is a OneToOne, so the primary path is exact.

    The email fallback is where this gets dangerous. Omni has duplicate employee
    rows sharing one address (re-hires, leaver copies). A TERMINATED duplicate
    has no reports, so `owed_reports()` would return [] and this would close a
    LIVE manager's task while their team feedback was still outstanding. So:
    leavers are excluded, and a non-unique hit returns None rather than guessing
    — never resolve an identity on a many-hit (Fable review, 2026-09-09).
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
            log.warning('feedback_task_close: %s matches more than one active '
                        'employee row — refusing to guess which manager it is.',
                        email)
        return None
    return matches[0]


def owed_reports(manager, year: int, month: int):
    """The manager's active reports with NO check-in for the period.

    Deliberately the same query as monthly_feedback_cycle's `owed`.
    """
    from hris.models import HRISProfile
    from hris.performance_feedback_models import MonthlyCheckIn
    from payroll.models import Employee
    reports = list(HRISProfile.objects.filter(manager=manager)
                   .exclude(employee__status=Employee.Status.TERMINATED)
                   .select_related('employee'))
    if not reports:
        return []
    # ONE query for the whole team, not one EXISTS per report. Same result as
    # monthly_feedback_cycle's per-report check, so raise and close stay in
    # parity; the daily sweep just stops doing tasks x reports round-trips.
    done = set(MonthlyCheckIn.objects
               .filter(profile__in=reports, period_year=year, period_month=month)
               .values_list('profile_id', flat=True))
    return [p for p in reports if p.pk not in done]


def close_if_complete(manager, year: int, month: int) -> int:
    """Close this manager's feedback task for the period if nothing is owed.

    Returns the number of tasks closed (0 or 1 in practice). Never raises: this
    is housekeeping hung off a save path, and a failure here must not lose the
    feedback the manager just typed.
    """
    from core.models import OmniTask
    try:
        if manager is None or owed_reports(manager, year, month):
            return 0
        tag = f'{SOURCE_PREFIX}{year}-{month:02d}'
        users = [u for u in (getattr(manager, 'user', None),) if u is not None]
        if not users:
            from core.models import User
            email = (getattr(manager, 'email', '') or '').strip()
            users = list(User.objects.filter(email__iexact=email)) if email else []
        if not users:
            return 0
        return _close(OmniTask.objects.filter(
            source=tag, assignee__in=users, status__in=_CLOSEABLE))
    except Exception:    # noqa: BLE001 — housekeeping, never break the caller
        # Employee NUMBER, never a name: an app log is a wider audience than the
        # screen, and the number is the stable identifier for debugging anyway.
        log.warning('feedback_task_close: could not close the %s-%02d task for '
                    'employee %s', year, month,
                    getattr(manager, 'employee_number', None) or getattr(manager, 'pk', '?'),
                    exc_info=True)
        return 0


def close_completed_feedback_tasks() -> int:
    """Sweep every open monthly-feedback task and close the ones already done.

    The catch-up path: it clears the tasks that are ALREADY stuck in prod (the
    reported bug) and covers every write path — in-app, one-click, admin, an
    import — without a hook on each of them.
    """
    from core.models import OmniTask
    closed = 0
    tasks = (OmniTask.objects
             .filter(source__startswith=SOURCE_PREFIX, status__in=_CLOSEABLE)
             .select_related('assignee'))
    for task in tasks:
        period = _period_from_source(task.source)
        if period is None:
            continue                      # not a period tag — leave it alone
        try:
            manager = _manager_for(task.assignee)
            if manager is None or owed_reports(manager, *period):
                continue
            closed += _close(OmniTask.objects.filter(pk=task.pk, status__in=_CLOSEABLE))
        except Exception:    # noqa: BLE001 — one bad row must not stop the sweep
            log.warning('feedback_task_close: skipped task %s (%s)',
                        task.pk, task.source, exc_info=True)
            continue
    return closed


def _close(qs) -> int:
    """Mark the queryset done + stamp completed_at. Status-filtered by the
    caller, so a task completed by a human between read and write is not
    re-stamped."""
    return qs.update(status='done', completed_at=timezone.now(),
                     updated_at=timezone.now())
