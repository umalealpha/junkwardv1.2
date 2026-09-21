"""
hris/perf_panel.py — the manager's decision panel + fact-based feedback draft.

For one employee and one month, assemble the facts a manager needs to give
fair monthly feedback (all from persisted omni data — nightly-safe, no live TD
API): tracked hours, absence, leave, sick leave, and task on-time record; plus
their standing targets with the month's actual, and a 3-month trend. Then
pre-draft the three feedback boxes from those facts so the manager edits rather
than writes from scratch.
"""
from __future__ import annotations

import calendar
import datetime as dt
from decimal import Decimal

from django.utils import timezone
from django.db.models import Sum

from hris.perf_target_source import pull_actual, is_achieved


def month_bounds(year: int, month: int):
    first = dt.date(year, month, 1)
    last  = dt.date(year, month, calendar.monthrange(year, month)[1])
    start_dt = timezone.make_aware(dt.datetime(year, month, 1))
    nxt = (dt.date(year, month, 28) + dt.timedelta(days=7)).replace(day=1)
    next_dt = timezone.make_aware(dt.datetime(nxt.year, nxt.month, 1))
    return first, last, start_dt, next_dt


def _decimal(v) -> Decimal:
    return v if isinstance(v, Decimal) else Decimal(str(v or 0))


def employee_month_panel(emp, year: int, month: int) -> dict:
    """The 5 decision metrics for one employee+month, from persisted stores."""
    from hris.models import WorkdayJustification, LeaveRequest
    from django.contrib.auth.models import User
    from core.models import OmniTask

    first, last, start_dt, next_dt = month_bounds(year, month)

    wj = WorkdayJustification.objects.filter(
        profile__employee=emp, work_date__gte=first, work_date__lte=last)
    tracked_hours = _decimal(wj.aggregate(h=Sum('tracked_hours'))['h'])
    absent_days = wj.filter(status=WorkdayJustification.Status.UNJUSTIFIED).count()

    leave_qs = LeaveRequest.objects.filter(
        profile__employee=emp, status=LeaveRequest.Status.APPROVED,
        start_date__gte=first, start_date__lte=last)
    leave_days = _decimal(leave_qs.aggregate(d=Sum('days'))['d'])
    sick_days = _decimal(leave_qs.filter(leave_type__code__iexact='sick')
                         .aggregate(d=Sum('days'))['d'])

    user = getattr(emp, 'user', None) or User.objects.filter(
        email__iexact=(emp.email or '')).first() if getattr(emp, 'email', '') else getattr(emp, 'user', None)

    assigned = completed = on_time = 0
    if user is not None:
        aq = (OmniTask.objects
              .filter(assignee=user, created_at__gte=start_dt, created_at__lt=next_dt)
              .exclude(status=OmniTask.Status.CANCELLED))
        assigned = aq.count()
        done = aq.filter(status=OmniTask.Status.DONE, completed_at__isnull=False)
        completed = aq.filter(status=OmniTask.Status.DONE).count()
        TZ = timezone.get_current_timezone()
        for t in done:
            if not t.due_at:
                on_time += 1
                continue
            deadline = dt.datetime.combine(t.due_at, t.due_time or dt.time(23, 59, 59), tzinfo=TZ)
            if t.completed_at <= deadline:
                on_time += 1

    # Long-overdue work still open right now (CFO 2026-08-07). This is the same
    # definition the leave / loan / incentive gate uses — one meaning of "late"
    # across the whole system — and it carries a negative weight into the
    # feedback below, automatically.
    from hris import overdue_gate
    overdue = overdue_gate.overdue_summary(user)

    on_time_pct = round(100 * on_time / completed) if completed else None
    return {
        'tracked_hours': float(tracked_hours),
        'absent_days': absent_days,
        'leave_days': float(leave_days),
        'sick_days': float(sick_days),
        'tasks_assigned': assigned,
        'tasks_completed': completed,
        'tasks_on_time': on_time,
        'tasks_on_time_pct': on_time_pct,
        'tasks_overdue_long': overdue['count'],
        'overdue_days_threshold': overdue['days_threshold'],
        'overdue_tasks': overdue['tasks'],
    }


def target_lines(profile, year: int, month: int) -> list[dict]:
    """Active targets for this person + the month's actual (cached result wins,
    else a live auto-pull) + achieved flag."""
    from hris.performance_target_models import PerformanceTarget, PerformanceTargetResult

    out = []
    # v1 acts on MONTHLY targets only — quarterly/annual would read "missed" every
    # month against the full-period figure (Fable M-4). Kept for a later pro-rate.
    for tgt in PerformanceTarget.objects.filter(
            profile=profile, active=True, cadence=PerformanceTarget.Cadence.MONTHLY):
        res = PerformanceTargetResult.objects.filter(
            target=tgt, period_year=year, period_month=month).first()
        if res and res.actual_value is not None:
            excl = res.actual_excl if res.actual_excl is not None else res.actual_value
            vat, incl = res.actual_vat, res.actual_incl
            achieved = res.achieved
        else:
            bd, _src = pull_actual(tgt, year, month)
            excl = bd['excl'] if bd else None
            vat = bd['vat'] if bd else None
            incl = bd['incl'] if bd else None
            achieved = is_achieved(tgt, excl)
        out.append({
            'target_id': str(tgt.id),
            'metric': tgt.metric,
            'target_value': float(tgt.target_value),   # target is on the VAT-exclusive basis
            'unit': tgt.unit,
            'source': tgt.source,
            'actual_value': None if excl is None else float(excl),   # achievement basis (excl)
            'actual_excl': None if excl is None else float(excl),
            'actual_vat': None if vat is None else float(vat),
            'actual_incl': None if incl is None else float(incl),
            'achieved': achieved,
            'confirmed': bool(res and res.achieved is not None),
            'note': tgt.note,
        })
    return out


def trend(profile, year: int, month: int, months: int = 3) -> list[dict]:
    """Last N months up to (year, month): rating + share of targets hit — sparkline."""
    from hris.performance_feedback_models import MonthlyCheckIn
    from hris.performance_target_models import PerformanceTargetResult

    out = []
    y, m = year, month
    for _ in range(months):
        ci = MonthlyCheckIn.objects.filter(
            profile=profile, period_year=y, period_month=m).first()
        results = PerformanceTargetResult.objects.filter(
            profile=profile, period_year=y, period_month=m, achieved__isnull=False)
        hit = results.filter(achieved=True).count()
        total = results.count()
        out.append({
            'year': y, 'month': m,
            'rating': ci.overall_rating if ci else None,
            'targets_hit': hit, 'targets_total': total,
            # auto_posted excluded: a month Omni wrote is not the manager
            # having given feedback (Fable round 2, 2026-08-27).
            'feedback_given': ci is not None and not ci.auto_posted,
        })
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return list(reversed(out))


def draft_feedback(panel: dict, targets: list[dict]) -> dict:
    """Pre-draft the three boxes from the facts. Manager edits before saving."""
    well, notwell, improve = [], [], []

    otp = panel.get('tasks_on_time_pct')
    if panel.get('tasks_completed'):
        if otp is not None and otp >= 80:
            well.append(f"Delivered {panel['tasks_on_time']}/{panel['tasks_completed']} tasks on time ({otp}%).")
        elif otp is not None:
            notwell.append(f"Only {panel['tasks_on_time']}/{panel['tasks_completed']} tasks on time ({otp}%).")
            improve.append("Agree realistic due dates and flag blockers early.")
    # Long-overdue work is an automatic negative on the record (CFO 2026-08-07).
    # It goes in first because it is the hardest fact in the panel, and it also
    # caps the rating — see hris.perf_panel.rating_cap.
    n_over = panel.get('tasks_overdue_long') or 0
    if n_over:
        thr = panel.get('overdue_days_threshold', 2)
        titles = ', '.join(t['title'] for t in (panel.get('overdue_tasks') or [])[:3])
        tail = f': {titles}' if titles else ''
        notwell.append(f"{n_over} task(s) still open more than {thr} days past the due date{tail}.")
        improve.append("Close the overdue tasks, or agree a new date with the person who set them.")

    if panel.get('absent_days'):
        notwell.append(f"{panel['absent_days']} working day(s) with no tracked time or reason.")
        improve.append("Log time daily or record a reason the same day.")

    for t in targets:
        label = f"{t['metric']} ({_fmt(t['target_value'], t['unit'])})"
        if t['achieved'] is True:
            well.append(f"Met target: {label}"
                        + (f" — actual {_fmt(t['actual_value'], t['unit'])}." if t['actual_value'] is not None else "."))
        elif t['achieved'] is False:
            notwell.append(f"Missed target: {label}"
                           + (f" — actual {_fmt(t['actual_value'], t['unit'])}." if t['actual_value'] is not None else "."))
            improve.append(f"Plan to reach {label} next month.")

    return {
        'strengths': ' '.join(well),
        'concerns': ' '.join(notwell),
        'support_provided': ' '.join(improve),
    }


def _fmt(value, unit) -> str:
    if value is None:
        return '—'
    if unit == 'BWP':
        return f"BWP {value:,.2f}"
    if unit == 'percent':
        return f"{value:g}%"
    return f"{value:g}"


# ── rating cap from long-overdue work (CFO 2026-08-07) ──────────────────────
# "The overdue task should be added as a negative point automatically in the
# performance feedback." A written line alone is easy to ignore, so it also
# limits the rating: nobody carrying work more than 2 days past due is rated
# "Meets expectations" or better for that month.

CAP_RATING = 'PA'                       # Partially meets
BLOCKED_WHEN_OVERDUE = frozenset({'EX', 'ME'})


def rating_cap(panel: dict) -> str | None:
    """The best rating allowed given this panel, or None for no cap."""
    return CAP_RATING if (panel.get('tasks_overdue_long') or 0) else None


def rating_cap_message(panel: dict) -> str:
    n = panel.get('tasks_overdue_long') or 0
    thr = panel.get('overdue_days_threshold', 2)
    return (f'This person has {n} task(s) more than {thr} days overdue, so the rating '
            f'cannot be higher than "Partially meets" this month. Close the tasks, '
            f'or agree a new due date, and the cap lifts.')


def rating_blocked(panel: dict, rating: str) -> bool:
    """True when `rating` is not allowed because of long-overdue work."""
    if not (panel.get('tasks_overdue_long') or 0):
        return False
    return (rating or '').strip().upper() in BLOCKED_WHEN_OVERDUE
