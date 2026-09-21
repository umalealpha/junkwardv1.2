"""hris/leave_backfill.py — approving leave clears the days it covers.

Why this exists (CFO 2026-08-03, Oratile Ria Tlhomelang).

The daily brief job stamps every WorkdayJustification row ONCE, on the morning it
runs, and only then asks whether approved leave covers that day
(send_daily_brief._approved_leave_for). Leave approved LATER never went back and
cleared the days already stamped — so an employee who took leave before the Omni
leave screen existed, and captured it on return, stayed `unjustified` for those
days. The register said "approved leave", the attendance record said
"unexplained absence", and manager_accountability walks the attendance record.

Oratile is the live case and the second time she has been hit by it: she was
escalated to management on 2026-07-28 for an absence she HAD taken (see
manager_accountability.leave_register_reliable), then on 2026-08-03 she captured
27–31 Jul, Kakale Botana approved it inside four minutes — and all five days
were still sitting `unjustified`. The manager-accountability shield window is
only MIN_DARK_DAYS wide, so once the leave scrolls out of that window the same
five days are evidence of "gone dark" again.

So: approval back-fills. Update-only, never create — a day Omni holds no record
for is not an accusation and must not become one (same principle as
manager_accountability.dark_working_streak skipping unknown days).
"""
from __future__ import annotations

import datetime
import logging
from decimal import Decimal

from hris import workforce
from hris.models import LeaveRequest, WorkdayJustification

log = logging.getLogger(__name__)

_STATUS_FOR = {
    'met':          WorkdayJustification.Status.MET,
    'not_required': WorkdayJustification.Status.NOT_REQUIRED,
    'justified':    WorkdayJustification.Status.JUSTIFIED,
    'unjustified':  WorkdayJustification.Status.UNJUSTIFIED,
}

# Days we never rewrite:
#   MET          — they actually worked it; approved leave does not erase hours.
#   NOT_REQUIRED — an off day, there was nothing to justify in the first place.
#   JUSTIFIED    — already clear; rewriting would churn the audit log.
_KEEP = {
    WorkdayJustification.Status.MET,
    WorkdayJustification.Status.NOT_REQUIRED,
    WorkdayJustification.Status.JUSTIFIED,
}


def _covered_hours(lr: LeaveRequest, day: datetime.date, required: Decimal) -> Decimal:
    """Hours of `day` the leave actually covers.

    A full day covers the whole requirement. A half-day boundary (start_day_type
    / end_day_type AM or PM) covers half of it — so a half-day of leave cannot
    silently excuse a whole day of missing hours.
    """
    required = Decimal(required or 0)
    half = (
        (day == lr.start_date and lr.start_day_type != LeaveRequest.DayType.FULL)
        or (day == lr.end_date and lr.end_day_type != LeaveRequest.DayType.FULL)
    )
    return (required / 2) if half else required


def backfill_workdays_for_leave(lr: LeaveRequest) -> int:
    """Mark the days an APPROVED leave covers as justified-on-leave.

    Returns how many WorkdayJustification rows were changed. Never raises — a
    failure here must not undo or block a manager's approval, so the caller can
    treat it as best-effort (the day-of path in send_daily_brief still works).
    """
    try:
        if lr.status != LeaveRequest.Status.APPROVED:
            return 0
        if not (lr.start_date and lr.end_date) or lr.end_date < lr.start_date:
            return 0

        rows = WorkdayJustification.objects.filter(
            profile_id=lr.profile_id,
            work_date__gte=lr.start_date,
            work_date__lte=lr.end_date,
        )
        changed = 0
        for row in rows:
            if row.status in _KEEP:
                continue
            required = Decimal(row.required_hours or 0)
            if required <= 0:                      # nothing was owed that day
                continue
            gap = required - Decimal(row.tracked_hours or 0)
            if gap <= 0:                           # hours are already there
                continue
            covered = _covered_hours(lr, row.work_date, required)
            justified = covered if covered < gap else gap

            row.justified_hours = justified
            row.reason = WorkdayJustification.Reason.ON_LEAVE
            row.linked_leave = lr
            # Same classifier the day-of path and the manual "I was on leave"
            # route use (workforce_views.justify_day), so a back-filled day is
            # indistinguishable from one cleared on the day. A half-day of leave
            # against a whole day of missing hours stays UNJUSTIFIED.
            row.status = _STATUS_FOR[
                workforce.classify_day(required, row.tracked_hours, justified)
            ]
            row.save(update_fields=['justified_hours', 'reason', 'linked_leave',
                                    'status', 'updated_at'])
            changed += 1
        return changed
    except Exception:    # noqa: BLE001 — must never break an approval
        log.exception('backfill_workdays_for_leave failed for LeaveRequest %s', lr.pk)
        return 0


def unbackfill_workdays_for_leave(lr: LeaveRequest) -> int:
    """Undo what backfill_workdays_for_leave wrote, when approved leave is cancelled.

    Without this, cancelling approved leave leaves the attendance record still
    saying "on leave" for days the person is now expected to work. Two things
    break: the workforce brief stops chasing genuinely missing hours, and the
    manager-accountability report reads those days as covered.

    Only rows this leave actually stamped are touched — `linked_leave` names
    them exactly, so there is no guessing and no other reason's justification is
    disturbed. A day someone justified another way carries a different reason
    and is left alone.

    Returns how many rows were reset. Never raises: a failure here must not
    block the cancellation itself (CFO queue item 3, 2026-08-08).
    """
    try:
        rows = WorkdayJustification.objects.filter(
            profile_id=lr.profile_id,
            linked_leave=lr,
            reason=WorkdayJustification.Reason.ON_LEAVE,
        )
        changed = 0
        for row in rows:
            required = Decimal(row.required_hours or 0)
            row.justified_hours = Decimal('0')
            row.reason = ''
            row.linked_leave = None
            # Re-run the same classifier, now with nothing justified — a day
            # with missing hours goes back to UNJUSTIFIED and is chased again.
            row.status = _STATUS_FOR[
                workforce.classify_day(required, row.tracked_hours, Decimal('0'))
            ]
            row.save(update_fields=['justified_hours', 'reason', 'linked_leave',
                                    'status', 'updated_at'])
            changed += 1
        return changed
    except Exception:    # noqa: BLE001 — must never break a cancellation
        log.exception('unbackfill_workdays_for_leave failed for LeaveRequest %s', lr.pk)
        return 0
