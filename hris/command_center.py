from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from statistics import median
from zoneinfo import ZoneInfo

from django.db.models import Q
from django.utils import timezone

from core.models import OmniTask
from hris.flight_risk_views import compute_flight_risk
from hris.models import HRISProfile, Recognition, WorkdayJustification
from integrations.models import TimeDoctorDailySnapshot
from payroll.models import Employee, Payslip


GABORONE_TZ = ZoneInfo('Africa/Gaborone')

OPEN_TASK_STATUSES = {
    OmniTask.Status.PENDING,
    OmniTask.Status.IN_PROGRESS,
    OmniTask.Status.PARTIAL,
    OmniTask.Status.BLOCKED,
}

SHORT_STATUSES = {
    WorkdayJustification.Status.UNJUSTIFIED,
    WorkdayJustification.Status.PENDING,
    WorkdayJustification.Status.EXPLAINED,
    WorkdayJustification.Status.JUSTIFIED,
}

EXCUSE_STATUSES = {
    WorkdayJustification.Status.JUSTIFIED,
    WorkdayJustification.Status.EXPLAINED,
    WorkdayJustification.Status.UNJUSTIFIED,
}


def _money_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(value.quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def _month_window(year: int, month: int, today: date) -> tuple[date, date]:
    first = date(year, month, 1)
    if year == today.year and month == today.month:
        return first, today
    next_month = first.replace(day=28) + timedelta(days=4)
    last = next_month - timedelta(days=next_month.day)
    return first, last


def _prev_month(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


# Below this many people a salary 'aggregate' is one person's pay: hidden
# unless the viewer is authorised for named detail anyway.
MIN_SALARY_GROUP = 3


def _get_ctx(employees_qs):
    employees = list(employees_qs)
    emp_ids = [e.pk for e in employees]
    emp_by_id = {e.pk: e for e in employees}
    user_ids = [e.user_id for e in employees if e.user_id]
    emp_by_user_id = {e.user_id: e for e in employees if e.user_id}
    email_set = {e.email.strip().lower() for e in employees if e.email and e.email.strip()}
    emp_by_name = {e.full_name: e for e in employees}

    profile_ids = []
    if emp_ids:
        profile_ids = list(
            HRISProfile.objects.filter(employee_id__in=emp_ids).values_list('pk', flat=True)
        )

    return {
        'employees': employees,
        'emp_ids': emp_ids,
        'emp_by_id': emp_by_id,
        'user_ids': user_ids,
        'emp_by_user_id': emp_by_user_id,
        'email_set': email_set,
        'emp_by_name': emp_by_name,
        'profile_ids': profile_ids,
    }


def _usable_snapshots(qs):
    """Split snapshot rows into (usable, excluded). A row claiming more hours
    than 24 per person on it is physically impossible — e.g. the 16-Jun-2026
    one-off backfill (29,102 h in one 'day') — so it is left out and reported,
    never summed."""
    usable, excluded = [], []
    for snap in qs:
        payload = snap.payload or []
        secs = sum((e.get('tracked_seconds') or 0) for e in payload)
        if payload and secs > len(payload) * 24 * 3600:
            excluded.append(snap.as_of.isoformat())
        else:
            usable.append(snap)
    return usable, excluded


def _tracked_hours(snapshots, email_set) -> Decimal:
    """Tracked hours of the people in scope only (company scope applies)."""
    total = Decimal('0')
    for snap in snapshots:
        for entry in snap.payload or []:
            if (entry.get('email') or '').strip().lower() in email_set:
                total += Decimal(str(entry.get('tracked_seconds') or 0)) / Decimal('3600')
    return total


def _headline(ctx: dict, year: int, month: int, today: date) -> dict:
    first, last = _month_window(year, month, today)

    snapshots, _ = _usable_snapshots(
        TimeDoctorDailySnapshot.objects.filter(as_of__range=(first, last)))
    tracked = _tracked_hours(snapshots, ctx['email_set'])

    justifications = WorkdayJustification.objects.filter(
        profile_id__in=ctx['profile_ids'],
        work_date__range=(first, last),
    )
    person_days_short = justifications.filter(status__in=SHORT_STATUSES).count()

    open_overdue_tasks = OmniTask.objects.filter(
        assignee_id__in=ctx['user_ids'],
        status__in=OPEN_TASK_STATUSES,
        due_at__lte=last,
    ).count()

    return {
        'tracked_hours': float(tracked),
        'person_days_short': person_days_short,
        'open_overdue_tasks': open_overdue_tasks,
    }


def compute_month(
    year: int,
    month: int,
    *,
    employees_qs,
    now=None,
    named: bool = False,
) -> dict:
    now_dt = now or timezone.now()
    today = now_dt.astimezone(GABORONE_TZ).date()
    first, last = _month_window(year, month, today)

    ctx = _get_ctx(employees_qs)

    # ── Time Doctor ────────────────────────────────────────────────────────
    snapshots, excluded_days = _usable_snapshots(
        TimeDoctorDailySnapshot.objects.filter(as_of__range=(first, last)))
    snapshot_rows = len(snapshots)
    total_tracked_hours = _tracked_hours(snapshots, ctx['email_set'])
    productive_pct_sum = Decimal('0')
    productive_pct_count = 0
    # A weekday holding under 30% of the month's typical weekday hours was only
    # partly pulled (e.g. 20-21 Jul 2026); it is kept but named so nobody reads
    # a pull gap as people not working.
    weekday_hours = sorted(sum((e.get('tracked_seconds') or 0) for e in (s.payload or [])) / 3600
                           for s in snapshots if s.as_of.weekday() < 5)
    median_weekday = weekday_hours[len(weekday_hours) // 2] if weekday_hours else 0
    partial_days = [s.as_of.isoformat() for s in snapshots if s.as_of.weekday() < 5 and median_weekday
                    and sum((e.get('tracked_seconds') or 0) for e in (s.payload or [])) / 3600
                    < 0.3 * median_weekday]

    for snap in snapshots:
        productive_pct = (snap.totals or {}).get('productive_pct')
        if productive_pct is not None:
            productive_pct_sum += Decimal(str(productive_pct))
            productive_pct_count += 1

    avg_productive_pct = None
    if productive_pct_count:
        avg_productive_pct = float(
            (productive_pct_sum / productive_pct_count).quantize(Decimal('0.01'))
        )

    no_tracking_person_days = 0
    for snap in snapshots:
        if snap.as_of.weekday() >= 5:
            continue
        for entry in snap.payload or []:
            if (entry.get('tracked_seconds') or 0) != 0:
                continue
            email = (entry.get('email') or '').strip().lower()
            if email in ctx['email_set']:
                no_tracking_person_days += 1

    latest_as_of = (
        TimeDoctorDailySnapshot.objects.order_by('-as_of')
        .values_list('as_of', flat=True)
        .first()
    )
    stale = latest_as_of is None or latest_as_of < last

    td = {
        'days_with_data': snapshot_rows,
        'excluded_days': excluded_days,
        'partial_days': partial_days,
        'latest_as_of': latest_as_of.isoformat() if latest_as_of else None,
        'total_tracked_hours': float(total_tracked_hours),
        'avg_productive_pct': avg_productive_pct,
        'no_tracking_person_days': no_tracking_person_days,
        'stale': stale,
    }

    # ── Shortfall ──────────────────────────────────────────────────────────
    justifications = list(
        WorkdayJustification.objects.filter(
            profile_id__in=ctx['profile_ids'],
            work_date__range=(first, last),
        )
    )
    justification_rows = len(justifications)

    short_rows = [j for j in justifications if j.status in SHORT_STATUSES]
    person_days_short = len(short_rows)

    hours_short = Decimal('0')
    for j in short_rows:
        required = j.required_hours or Decimal('0')
        tracked = j.tracked_hours or Decimal('0')
        gap = required - tracked
        if gap > 0:
            hours_short += gap

    status_counter = Counter(j.status for j in justifications)
    by_status = {value: status_counter.get(value, 0) for value, _ in WorkdayJustification.Status.choices}

    shortfall = {
        'person_days_short': person_days_short,
        'hours_short': str(hours_short.quantize(Decimal('0.01'))),
        'by_status': by_status,
    }

    # ── Excuses ─────────────────────────────────────────────────────────────
    # 'No shortfall' is not an excuse (the live run showed it as 'most used').
    excuse_rows = [j for j in justifications if j.status in EXCUSE_STATUSES
                   and j.reason != WorkdayJustification.Reason.NONE]
    reason_label_map = dict(WorkdayJustification.Reason.choices)

    reason_counter = Counter(j.reason for j in excuse_rows)
    by_reason = {
        reason_label_map.get(reason, reason): count
        for reason, count in reason_counter.items()
    }

    if by_reason:
        most_used = sorted(by_reason.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    else:
        most_used = None

    profile_counter = Counter(j.profile_id for j in excuse_rows)
    repeated_people = sum(1 for count in profile_counter.values() if count >= 3)

    unresolved = WorkdayJustification.objects.filter(
        profile_id__in=ctx['profile_ids'],
        work_date__range=(first, last),
        status__in=[
            WorkdayJustification.Status.PENDING,
            WorkdayJustification.Status.EXPLAINED,
        ],
    ).count()

    excuses = {
        'by_reason': by_reason,
        'most_used': most_used,
        'repeated_people': repeated_people,
        'unresolved': unresolved,
    }

    # ── Manager SLA ────────────────────────────────────────────────────────
    if not hasattr(WorkdayJustification, 'reviewed_at'):
        manager_sla = {
            'available': False,
            'reason': 'no decision timestamp stored',
        }
    else:
        decision_durations = []
        for j in justifications:
            if j.reviewed_at and j.responded_at:
                seconds = (j.reviewed_at - j.responded_at).total_seconds()
                decision_durations.append(seconds / 3600)

        cutoff = now_dt - timedelta(hours=48)
        waiting_over_48h = WorkdayJustification.objects.filter(
            profile_id__in=ctx['profile_ids'],
            work_date__range=(first, last),
            status=WorkdayJustification.Status.EXPLAINED,
            responded_at__isnull=False,
            reviewed_at__isnull=True,
            responded_at__lt=cutoff,
        ).count()

        manager_sla = {
            'available': True,
            'median_hours': median(decision_durations) if decision_durations else None,
            'waiting_over_48h': waiting_over_48h,
        }

    # ── Tasks ──────────────────────────────────────────────────────────────
    open_tasks = OmniTask.objects.filter(
        assignee_id__in=ctx['user_ids'],
        status__in=OPEN_TASK_STATUSES,
        due_at__lte=last,
    )
    open_overdue = open_tasks.count()

    completed_in_window = OmniTask.objects.filter(
        assignee_id__in=ctx['user_ids'],
        status=OmniTask.Status.DONE,
        completed_at__date__range=(first, last),
    ).count()

    by_assignee_counter = Counter(task.assignee_id for task in open_tasks)
    top_assignees = []
    for user_id, overdue_count in sorted(
        by_assignee_counter.items(),
        key=lambda kv: (-kv[1], ctx['emp_by_user_id'].get(kv[0]).full_name if kv[0] in ctx['emp_by_user_id'] else ''),
    ):
        emp = ctx['emp_by_user_id'].get(user_id)
        name = emp.full_name if emp else f'User {user_id}'
        top_assignees.append({'name': name, 'overdue_count': overdue_count})

    tasks = {
        'open_overdue': open_overdue,
        'completed_in_window': completed_in_window,
        'by_assignee': top_assignees[:10],
    }

    # ── Flight risk ────────────────────────────────────────────────────────
    flight_rows = compute_flight_risk(ctx['employees'])
    band_counter = Counter(r['band'] for r in flight_rows)

    flight_risk = {
        'counts': {
            'high': band_counter.get('high', 0),
            'med': band_counter.get('med', 0),
            'low': band_counter.get('low', 0),
        }
    }
    if named:
        flight_risk['results'] = [
            {
                'name': r['name'],
                'department': r['department'],
                'band': r['band'],
                'score': r['score'],
            }
            for r in flight_rows
        ]

    # ── Salary at risk ─────────────────────────────────────────────────────
    # Match on identity, never full name (duplicate names exist; Opus judge).
    # A flight-risk row's profile_id is the HRIS profile pk, or the employee pk.
    high_ids = {str(r.get('profile_id')) for r in flight_rows if r['band'] == 'high' and r.get('profile_id')}
    emp_id_set = high_ids | {str(e) for e in HRISProfile.objects.filter(pk__in=list(high_ids))
                             .values_list('employee_id', flat=True)}
    high_emps = [e for e in ctx['employees'] if str(e.pk) in emp_id_set]
    people = len(high_emps)

    if people == 0:
        salary_at_risk = {
            'bwp': None,
            'people': 0,
            'basis': 'No high flight-risk employees in scope',
        }
    else:
        high_emp_ids = [e.pk for e in high_emps]
        payslips = Payslip.objects.filter(
            employee_id__in=high_emp_ids,
        # Omni holds every payslip as 'draft' (the pay run itself happens
        # outside Omni), so approved/paid-only found nothing live; use the
        # latest non-cancelled slip as the estimate basis.
        ).exclude(status=Payslip.Status.CANCELLED).order_by('-period__start_date', '-created_at')

        latest_by_emp = {}
        for payslip in payslips:
            if payslip.employee_id not in latest_by_emp:
                latest_by_emp[payslip.employee_id] = payslip

        if not latest_by_emp:
            salary_at_risk = {
                'bwp': None,
                'people': people,
                'basis': 'No payslip on record for high flight-risk employees',
            }
        else:
            total_gross = sum(
                (p.gross_amount or Decimal('0')) for p in latest_by_emp.values()
            )
            salary_at_risk = {} if len(latest_by_emp) >= MIN_SALARY_GROUP or named else {
                'bwp': None,
                'people': people,
                'basis': f'fewer than {MIN_SALARY_GROUP} people — not shown',
            }
        if not salary_at_risk and latest_by_emp:
            salary_at_risk = {
                'bwp': _money_str(total_gross),
                'people': people,
                'basis': 'Latest payslip gross (monthly, BWP) of high flight-risk staff — an estimate',
            }

        if named:
            breakdown = []
            for emp in high_emps:
                payslip = latest_by_emp.get(emp.pk)
                breakdown.append({
                    'name': emp.full_name,
                    'department': emp.department or '',
                    'gross': _money_str(payslip.gross_amount) if payslip else None,
                })
            salary_at_risk['breakdown'] = breakdown

    # ── Recognition ────────────────────────────────────────────────────────
    recognitions = list(
        Recognition.objects.filter(
            receiver__employee_id__in=ctx['emp_ids'],
            created_at__date__range=(first, last),
        ).select_related('receiver__employee')
    )
    recognition_count = len(recognitions)

    recognition_counter = Counter(
        rec.receiver.employee.full_name for rec in recognitions
    )
    top_recipients = [
        {'name': name, 'count': count}
        for name, count in sorted(
            recognition_counter.items(), key=lambda kv: (-kv[1], kv[0])
        )[:5]
    ]

    recognition = {
        'count': recognition_count,
        'top_recipients': top_recipients,
    }

    # ── Trend ──────────────────────────────────────────────────────────────
    trend = []
    y, m = year, month
    for _ in range(3):
        py, pm = _prev_month(y, m)
        trend.append({
            'month': f'{py:04d}-{pm:02d}',
            **_headline(ctx, py, pm, today),
        })
        y, m = py, pm

    trend.reverse()
    trend.append({
        'month': f'{year:04d}-{month:02d}',
        'tracked_hours': td['total_tracked_hours'],
        'person_days_short': shortfall['person_days_short'],
        'open_overdue_tasks': tasks['open_overdue'],
    })

    # ── Cross-check ────────────────────────────────────────────────────────
    considered_tasks = OmniTask.objects.filter(
        assignee_id__in=ctx['user_ids'],
    ).filter(
        Q(status__in=OPEN_TASK_STATUSES, due_at__lte=last)
        | Q(status=OmniTask.Status.DONE, completed_at__date__range=(first, last))
    )
    task_rows = considered_tasks.count()

    cross_check = {
        'snapshot_rows': snapshot_rows,
        'justification_rows': justification_rows,
        'task_rows': task_rows,
        'employees_in_scope': len(ctx['emp_ids']),
    }

    return {
        'month': f'{year:04d}-{month:02d}',
        'window': {
            'first': first.isoformat(),
            'last': last.isoformat(),
        },
        'td': td,
        'shortfall': shortfall,
        'excuses': excuses,
        'manager_sla': manager_sla,
        'tasks': tasks,
        'flight_risk': flight_risk,
        'salary_at_risk': salary_at_risk,
        'recognition': recognition,
        'trend': trend,
        'cross_check': cross_check,
    }
