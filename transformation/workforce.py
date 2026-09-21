"""Where the salary money actually goes, by department.

CFO, 2026-09-20: "link time doctor also into this so this dashboard has some
meaning, link leave days, excuse dashboard also so we can find which department
is ineffective and burning our salary money and the late coming database also".

Everything here reads records Omni already keeps:

  WorkdayJustification   the canonical per-day record — required hours against
                         tracked hours, with the person's explanation and where
                         the manager got to. 5,500+ rows. This is the engine.
  TimeDoctorDailySnapshot  the stored daily roll-up per person (hours tracked,
                         productive hours). Used for the live picture.
  LeaveRequest           approved leave, so a day off is never counted as a
                         shortfall and leave days show as what they are.

An honesty note that belongs on the screen, not buried here: Omni does NOT
store clock-in times. There is no late-arrival clock. What it does store is
the shape of each working day — required against tracked, and whether the
shortfall was explained. So "late coming" on this board means SHORT DAYS and
ABSENCE DAYS from that record. Naming it anything else would be inventing a
number, and a board that invents one number cannot be trusted on the others.
"""
from __future__ import annotations

import datetime as _dt
import logging
from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import FieldError
from django.db import DatabaseError
from django.db.models import Count, Q, Sum
from django.utils import timezone

# A required day with almost no tracked time is an absence, not a short day.
# Same threshold the staff-loan attendance gate already uses, so the two
# screens can never tell a person two different stories about the same day.
ABSENCE_HOURS = 2.0
# Statuses that mean nobody has accounted for the day yet.
UNEXPLAINED = ('pending', 'unjustified')
# The day the EMPLOYEE has explained and the MANAGER has not yet ruled on.
# This — not 'pending' — is what "awaiting manager" means. `responded_at` is
# the employee's reply; the manager's decision is `reviewed_at`.
AWAITING_MANAGER = 'explained'
# Working hours in a month, for turning hours into money.
MONTH_HOURS = Decimal('176')


log = logging.getLogger(__name__)


def _d(value) -> Decimal:
    return Decimal(str(value or 0))


def _window(days: int = 30) -> tuple[_dt.date, _dt.date]:
    end = timezone.localdate()
    return end - _dt.timedelta(days=days), end


def _department_of_profiles() -> dict:
    """HRIS profile id -> department, via the payroll record."""
    from hris.models import HRISProfile

    out = {}
    rows = HRISProfile.objects.select_related('employee').values(
        'id', 'employee__department')
    for row in rows:
        out[row['id']] = (row['employee__department'] or 'Unassigned').strip()
    return out


def workday_record(days: int = 30) -> dict:
    """Per department: short days, absences, unexplained hours, manager SLA."""
    from hris.models import WorkdayJustification

    start, end = _window(days)
    by_profile = _department_of_profiles()

    stats: dict[str, dict] = defaultdict(lambda: {
        'days_recorded': 0, 'short_days': 0, 'absence_days': 0,
        'unexplained_days': 0, 'explained_days': 0, 'justified_days': 0,
        'required_hours': Decimal('0'), 'tracked_hours': Decimal('0'),
        'covered_hours': Decimal('0'), 'leave_days_recorded': 0,
        'unexplained_hours': Decimal('0'), 'people': set(),
        'manager_responses': 0, 'manager_response_days': 0, 'awaiting_manager': 0,
    })

    rows = (WorkdayJustification.objects
            .filter(work_date__gte=start, work_date__lte=end)
            .values('profile_id', 'work_date', 'required_hours', 'tracked_hours',
                    'justified_hours', 'status', 'created_at', 'responded_at',
                    'reviewed_at'))

    for row in rows:
        dept = by_profile.get(row['profile_id'], 'Unassigned')
        bucket = stats[dept]
        required = _d(row['required_hours'])
        tracked = _d(row['tracked_hours'])
        status = (row['status'] or '').lower()

        if required <= 0:
            continue  # not a working day for that person

        justified = _d(row['justified_hours'])
        # A day covered by approved leave is accounted for. The board prints
        # "approved leave is never counted as a shortfall" — this is where
        # that promise is either kept or quietly broken. Leave days arrive as
        # required_hours > 0, tracked_hours ~ 0, justified_hours = the day.
        covered = tracked + justified
        on_leave = status == 'justified' and justified > 0

        bucket['days_recorded'] += 1
        bucket['people'].add(row['profile_id'])
        bucket['required_hours'] += required
        bucket['tracked_hours'] += tracked
        bucket['covered_hours'] += min(covered, required)

        gap = required - covered
        if not on_leave:
            if gap > 0:
                bucket['short_days'] += 1
            if covered < _d(ABSENCE_HOURS):
                bucket['absence_days'] += 1
        else:
            bucket['leave_days_recorded'] += 1

        if status in UNEXPLAINED:
            bucket['unexplained_days'] += 1
            if gap > 0:
                bucket['unexplained_hours'] += gap
        elif status == 'justified':
            bucket['justified_days'] += 1
        else:
            bucket['explained_days'] += 1

        # Manager SLA = from the employee's explanation to the manager's
        # ruling. Measuring from created_at to responded_at would time the
        # EMPLOYEE and print it as the manager's number.
        responded, reviewed = row['responded_at'], row['reviewed_at']
        if reviewed and responded:
            bucket['manager_responses'] += 1
            bucket['manager_response_days'] += max(0, (reviewed - responded).days)
        elif status == AWAITING_MANAGER:
            bucket['awaiting_manager'] += 1

    out = {}
    for dept, bucket in stats.items():
        recorded = bucket['days_recorded'] or 1
        out[dept] = {
            'people_with_records': len(bucket['people']),
            'days_recorded': bucket['days_recorded'],
            'short_days': bucket['short_days'],
            'absence_days': bucket['absence_days'],
            'unexplained_days': bucket['unexplained_days'],
            'explained_days': bucket['explained_days'],
            'justified_days': bucket['justified_days'],
            'awaiting_manager': bucket['awaiting_manager'],
            'required_hours': float(bucket['required_hours']),
            'tracked_hours': float(bucket['tracked_hours']),
            'unexplained_hours': float(bucket['unexplained_hours']),
            'covered_hours': float(bucket['covered_hours']),
            'leave_days_recorded': bucket['leave_days_recorded'],
            # Hours ACCOUNTED FOR (worked or on approved leave) against hours
            # owed. Using tracked alone would mark a department down for its
            # people taking the leave they are entitled to.
            'attendance_percent': round(
                float(bucket['covered_hours']) * 100 / float(bucket['required_hours'])
            ) if bucket['required_hours'] > 0 else None,
            'short_day_rate': round(bucket['short_days'] * 100 / recorded),
            'unexplained_rate': round(bucket['unexplained_days'] * 100 / recorded),
            'manager_sla_days': round(
                bucket['manager_response_days'] / bucket['manager_responses'], 1
            ) if bucket['manager_responses'] else None,
        }
    return out


def time_doctor_by_department() -> dict:
    """Latest stored Time Doctor day, rolled up per department.

    Uses the STORED snapshot, never the live API — Time Doctor has refused Omni
    before (17-Sep) and a board that goes blank when a vendor is down is worse
    than one that says "as at 18-Sep".
    """
    from django.apps import apps

    from integrations.models import TimeDoctorDailySnapshot

    snapshot = TimeDoctorDailySnapshot.objects.order_by('-as_of').first()
    if not snapshot:
        return {'as_of': None, 'stale': True, 'departments': {}}

    Employee = apps.get_model('payroll', 'Employee')
    dept_by_email = {
        (e or '').strip().lower(): (d or 'Unassigned').strip()
        for e, d in Employee.objects.filter(status__in=('active', 'on_leave'))
                                    .values_list('email', 'department')
        if e
    }

    stats: dict[str, dict] = defaultdict(
        lambda: {'people': 0, 'hours_tracked': 0.0, 'productive_hours': 0.0})
    unmatched = 0
    for person in (snapshot.payload or []):
        email = (person.get('email') or '').strip().lower()
        dept = dept_by_email.get(email)
        if not dept:
            unmatched += 1
            continue
        bucket = stats[dept]
        bucket['people'] += 1
        bucket['hours_tracked'] += float(person.get('hours_tracked') or 0)
        bucket['productive_hours'] += float(person.get('productive_hours') or 0)

    for bucket in stats.values():
        tracked = bucket['hours_tracked']
        bucket['productive_percent'] = (
            round(bucket['productive_hours'] * 100 / tracked) if tracked else None)
        bucket['hours_tracked'] = round(tracked, 1)
        bucket['productive_hours'] = round(bucket['productive_hours'], 1)

    age = (timezone.localdate() - snapshot.as_of).days
    return {
        'as_of': snapshot.as_of.isoformat(),
        'days_old': age,
        # Time Doctor has gone quiet on Omni before. Say so on the screen
        # rather than showing an old number as if it were this morning's.
        'stale': age > 2,
        'unmatched_people': unmatched,
        'company_totals': snapshot.totals or {},
        'departments': dict(stats),
    }


def leave_by_department(days: int = 30) -> tuple[dict, bool]:
    """Approved leave days per department, and whether we could read them.

    Returns (rows, ok). **ok=False means the leave register could not be read.**

    The swallow-and-return-{} version of this reads downstream as "this
    department took no leave at all", which makes its attendance look worse
    than it is — an accusation manufactured out of a failed query. When we
    cannot look, the board says "not available", not "zero".
    """
    # Same-repo app — see the note in pulse._on_leave_today.
    from hris.models import LeaveRequest

    start, end = _window(days)
    out: dict[str, dict] = defaultdict(lambda: {'requests': 0, 'days': 0.0})
    try:
        rows = list(LeaveRequest.objects
                    .filter(status='approved', start_date__lte=end, end_date__gte=start)
                    .values('profile__employee__department', 'start_date', 'end_date'))
    except (FieldError, DatabaseError) as exc:
        # Narrow on purpose — see the note in pulse._on_leave_today.
        log.error('transformation: leave lookup failed (%s)', exc)
        return {}, False

    for row in rows:
        dept = (row['profile__employee__department'] or 'Unassigned').strip()
        first = max(row['start_date'], start)
        last = min(row['end_date'], end)
        out[dept]['requests'] += 1
        out[dept]['days'] += max(0, (last - first).days + 1)
    for bucket in out.values():
        bucket['days'] = round(bucket['days'], 1)
    return dict(out), True


def effectiveness(costs: dict, heads: dict, days: int = 30) -> tuple[list[dict], bool]:
    """The answer to "which department is burning salary money", in Pula.

    Salary at risk = unexplained shortfall hours valued at that department's
    OWN cost per head per hour. Explained and justified days cost nothing here
    — a person who said where they were is not the problem this measures.
    """
    work = workday_record(days)
    td = time_doctor_by_department()
    leave, leave_known = leave_by_department(days)

    rows = []
    for dept in sorted(set(work) | set(heads) | set(td.get('departments', {}))):
        w = work.get(dept, {})
        head = heads.get(dept, 0)
        cost = _d(costs.get(dept, 0))
        hourly = (cost / head / MONTH_HOURS) if head else Decimal('0')
        at_risk = (hourly * _d(w.get('unexplained_hours', 0))).quantize(Decimal('0.01'))

        rows.append({
            'department': dept,
            'headcount': head,
            'cost_now': float(cost),
            'cost_per_hour': float(hourly.quantize(Decimal('0.01'))),
            'attendance_percent': w.get('attendance_percent'),
            'short_days': w.get('short_days', 0),
            'short_day_rate': w.get('short_day_rate', 0),
            'absence_days': w.get('absence_days', 0),
            'unexplained_days': w.get('unexplained_days', 0),
            'unexplained_rate': w.get('unexplained_rate', 0),
            'unexplained_hours': w.get('unexplained_hours', 0),
            'awaiting_manager': w.get('awaiting_manager', 0),
            'manager_sla_days': w.get('manager_sla_days'),
            # None (shown as "—") when the register could not be read;
            # never 0, which would read as "they took no leave".
            'leave_days': (leave.get(dept, {}).get('days', 0)
                           if leave_known else None),
            'time_doctor': td.get('departments', {}).get(dept, {}),
            'salary_at_risk': float(at_risk),
        })

    rows.sort(key=lambda r: -r['salary_at_risk'])
    return rows, leave_known


def workforce_block(costs: dict, heads: dict, days: int = 30) -> dict:
    """Everything the workforce half of the board draws."""
    rows, leave_known = effectiveness(costs, heads, days)
    td = time_doctor_by_department()
    total_at_risk = sum((_d(r['salary_at_risk']) for r in rows), Decimal('0'))
    return {
        'window_days': days,
        'time_doctor': {
            'as_of': td.get('as_of'),
            'days_old': td.get('days_old'),
            'stale': td.get('stale'),
            'company_totals': td.get('company_totals'),
            'unmatched_people': td.get('unmatched_people', 0),
        },
        'departments': rows,
        'leave_data_available': leave_known,
        'salary_at_risk_month': float(total_at_risk),
        'salary_at_risk_year': float(total_at_risk * 12),
        'worst': rows[0]['department'] if rows else None,
        'late_coming_note': (
            'Omni does not store clock-in times, so there is no true late-arrival '
            'clock. "Short days" and "absences" come from the stored per-day record '
            '(required hours against tracked hours) — the same record the staff-loan '
            'attendance gate uses.'
        ),
        'fairness_note': (
            'Approved leave is never counted as a shortfall. Only days nobody has '
            'explained are valued as salary at risk.'
        ),
    }
