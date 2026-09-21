"""
hris/attendance_gate.py — attendance pre-check for staff loans + leave encashment.

CFO directive 2026-07-28: a staff member may not apply for a staff loan (or leave
encashment) while they have working days in the last 20 with unexplained time on
Time Doctor. They must first either apply for leave for those days, or add a
comment explaining them. Both routes clear the day:

  * approved leave  -> the day is auto-justified (JUSTIFIED)
  * a comment        -> the day moves to EXPLAINED (their explanation on record)

So the block is simply: any WorkdayJustification in the window still sitting in
PENDING or UNJUSTIFIED (a shortfall that is neither justified nor explained).
This reuses the canonical per-day record the workforce brief already maintains —
no re-deriving hours here.
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

WINDOW_DAYS = 20
# A working-day shortfall that is neither justified/explained nor an off-day.
_BLOCKING_STATUSES = ('pending', 'unjustified')
# Only genuine ABSENCES gate an application — a day the person was effectively
# not at work. A short-but-present day (e.g. 5h of 8) is NOT an absence and must
# not block (CFO 2026-07-28: "not present at work"). Tracked below this = absent.
ABSENCE_HOURS = 2.0


def unaccounted_days(profile, days: int = WINDOW_DAYS):
    """[(work_date, tracked_hours, required_hours)] for unexplained ABSENCE days
    in the last `days`, oldest first. An absence = a real working day
    (required_hours > 0) with almost no tracked time (< ABSENCE_HOURS) that is
    neither justified/explained nor an off-day. Empty when nothing is outstanding."""
    if profile is None:
        return []
    from hris.models import WorkdayJustification
    since = timezone.localdate() - timedelta(days=days)
    rows = (WorkdayJustification.objects
            .filter(profile=profile, work_date__gte=since, status__in=_BLOCKING_STATUSES,
                    required_hours__gt=0, tracked_hours__lt=ABSENCE_HOURS)
            .order_by('work_date'))
    return [(r.work_date, float(r.tracked_hours or 0), float(r.required_hours or 0)) for r in rows]


def attendance_gate(profile, days: int = WINDOW_DAYS):
    """(ok, unaccounted_days, message). ok=True means the person may apply."""
    bad = unaccounted_days(profile, days)
    if not bad:
        return True, [], ''
    dates = ', '.join(d.strftime('%d %b') for d, _, _ in bad)
    msg = (
        f'You cannot apply yet. Your Time Doctor time is not fully accounted for on '
        f'{len(bad)} working day(s) in the last {days} days ({dates}). Please either apply '
        f'for leave for those days, or add a comment explaining each on your Workforce brief, '
        f'then try again.'
    )
    return False, bad, msg
