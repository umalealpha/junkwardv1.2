"""core/team_glance_views.py — one glance at my direct reports, for the phone
Team tab (CFO 2026-09-03: "who's in, who's dark, who's on leave, what's overdue").

GET /api/v1/team/glance/  (IsAuthenticated)

    { as_of, day_name, is_workday, reports: [{ employee_id, user_id, name,
                         job_title, on_leave_today, leave_type, online,
                         dark_days_7, short_days_7, awaiting_review_7,
                         open_tasks, overdue_tasks }],
      counts: { in, on_leave, dark, overdue, overdue_people, awaiting_me } }

`dark_days_7` is unchanged in meaning: it is the sum of `short_days_7`
(UNJUSTIFIED or PENDING) and `awaiting_review_7` (EXPLAINED).

Nothing here is computed fresh from Time Doctor. Every figure is read from the
stores the rest of Omni already writes:
  * on_leave_today  — an APPROVED hris.LeaveRequest spanning today.
  * online          — core.OnlinePresence heartbeat within the same 5-minute
                      window /presence/online/ uses.
  * dark_days_7     — hris.WorkdayJustification rows in the last 7 calendar days
                      whose stored status (the product of hris.workforce
                      .classify_day, written by the daily brief) is not one of
                      the "accounted for" states. The shortfall rule itself is
                      NOT re-implemented here.
  * open/overdue    — core.OmniTask via taskboard.services.is_overdue, approval
                      items excluded exactly as the task dashboard excludes them.

No PII beyond name + job title + status. A user with no reports gets an empty
200, never a 403 — the app uses this as its "do I manage people" probe.
"""
from __future__ import annotations

from datetime import timedelta

from django.db.models import Q
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

PRESENCE_WINDOW = timedelta(minutes=5)      # same window as presence_online_users
DARK_LOOKBACK_DAYS = 7


def _zero_counts() -> dict:
    return {'in': 0, 'on_leave': 0, 'dark': 0, 'overdue': 0,
            'overdue_people': 0, 'awaiting_me': 0}


def _is_workday(today) -> bool:
    """Monday..Saturday are working days unless they are an active BW public
    holiday. Sunday is always False. Saturday counts (3h rule)."""
    if today.weekday() == 6:  # Sunday
        return False
    from hris.models import PublicHoliday
    return not PublicHoliday.objects.filter(
        country_code='BW', holiday_date=today, is_active=True
    ).exists()


def _team_profiles(me):
    """Active, real (non-test) people who line-report to `me` or whom `me`
    co-reviews — the same set the monthly-feedback worklist uses."""
    from hris.models import HRISProfile
    from payroll.models import Employee
    return (HRISProfile.objects
            .filter(manager=me)   # line manager ONLY — a co-manager is a second rater, not a second boss (notebook)
            .exclude(employee=None)
            .exclude(employee__status=Employee.Status.TERMINATED)
            .exclude(employee__is_test_record=True)
            .select_related('employee', 'employee__user')
            .distinct()
            .order_by('employee__full_name'))


def _leave_today(profile_ids, today) -> dict:
    """{profile_id: leave type name} for APPROVED leave that spans today."""
    from hris.models import LeaveRequest
    rows = (LeaveRequest.objects
            .filter(profile_id__in=profile_ids, status=LeaveRequest.Status.APPROVED,
                    start_date__lte=today, end_date__gte=today)
            .select_related('leave_type'))
    return {r.profile_id: (r.leave_type.name if r.leave_type_id else 'Leave') for r in rows}


def _online_user_ids(user_ids) -> set:
    from core.models import OnlinePresence
    cutoff = timezone.now() - PRESENCE_WINDOW
    return set(OnlinePresence.objects
               .filter(user_id__in=user_ids, last_seen__gte=cutoff)
               .values_list('user_id', flat=True))


def _workday_shortfalls(profile_ids, today):
    """Return (short, awaiting_review) dicts keyed by profile_id.

    short = WorkdayJustification rows in the last 7 calendar days with status
            UNJUSTIFIED or PENDING (the employee has not answered).
    awaiting_review = same window, status EXPLAINED (employee answered; the
            manager has not ruled).
    dark_days_7 at the call-site is short + awaiting_review — identical to the
    previous dark_days_7 meaning.
    """
    from hris.models import WorkdayJustification as WJ
    since = today - timedelta(days=DARK_LOOKBACK_DAYS)
    short: dict = {}
    awaiting: dict = {}
    rows = (WJ.objects
            .filter(profile_id__in=profile_ids, work_date__gte=since, work_date__lt=today)
            .exclude(status__in=(WJ.Status.MET, WJ.Status.NOT_REQUIRED, WJ.Status.JUSTIFIED))
            .values_list('profile_id', 'status'))
    for pid, status in rows:
        if status in (WJ.Status.UNJUSTIFIED, WJ.Status.PENDING):
            short[pid] = short.get(pid, 0) + 1
        elif status == WJ.Status.EXPLAINED:
            awaiting[pid] = awaiting.get(pid, 0) + 1
    return short, awaiting


def _task_counts(user_ids) -> dict:
    """{user_id: (open, overdue)} over real tasks (approval items excluded)."""
    from core.models import OmniTask
    from taskboard.cfo_views import _real_tasks
    from taskboard.services import is_overdue
    qs = _real_tasks(OmniTask.objects
                     .filter(assignee_id__in=user_ids)
                     .exclude(status__in=(OmniTask.Status.DONE, OmniTask.Status.CANCELLED)))
    out: dict = {}
    now = timezone.localtime()
    for t in qs.only('id', 'assignee_id', 'status', 'due_at', 'due_time'):
        o, od = out.get(t.assignee_id, (0, 0))
        out[t.assignee_id] = (o + 1, od + (1 if is_overdue(t, now) else 0))
    return out


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def team_glance(request):
    from hris.performance_views import _resolve_employee
    today = timezone.localdate()
    day_name = today.strftime('%A')
    is_workday = _is_workday(today)
    me = _resolve_employee(request.user)
    if me is None:
        return Response({'as_of': today, 'day_name': day_name,
                         'is_workday': is_workday,
                         'reports': [], 'counts': _zero_counts()})

    profiles = list(_team_profiles(me))
    if not profiles:
        return Response({'as_of': today, 'day_name': day_name,
                         'is_workday': is_workday,
                         'reports': [], 'counts': _zero_counts()})

    pids = [p.id for p in profiles]
    uids = [p.employee.user_id for p in profiles if p.employee.user_id]
    leave = _leave_today(pids, today)
    online = _online_user_ids(uids)
    short, awaiting = _workday_shortfalls(pids, today)
    tasks = _task_counts(uids)

    reports = []
    counts = _zero_counts()
    for p in profiles:
        emp = p.employee
        on_leave = p.id in leave
        is_online = bool(emp.user_id) and emp.user_id in online
        short_n = short.get(p.id, 0)
        awaiting_n = awaiting.get(p.id, 0)
        dark_n = short_n + awaiting_n
        open_n, overdue_n = tasks.get(emp.user_id, (0, 0)) if emp.user_id else (0, 0)
        reports.append({
            'employee_id': str(emp.id),
            # Django user pk (not PII) — the join key to /taskboard/overview/
            # rows, whose `assignee` is the same pk. Null for register-only staff.
            'user_id': emp.user_id,
            'name': emp.full_name,
            'job_title': emp.job_title or '',
            'on_leave_today': on_leave,
            'leave_type': leave.get(p.id),
            'online': is_online,
            'dark_days_7': dark_n,
            'short_days_7': short_n,
            'awaiting_review_7': awaiting_n,
            'open_tasks': open_n,
            'overdue_tasks': overdue_n,
        })
        counts['in'] += 1 if is_online else 0
        counts['on_leave'] += 1 if on_leave else 0
        counts['dark'] += 1 if dark_n else 0
        counts['overdue'] += overdue_n
        counts['overdue_people'] += 1 if overdue_n > 0 else 0
        counts['awaiting_me'] += 1 if awaiting_n > 0 else 0

    return Response({'as_of': today, 'day_name': day_name,
                     'is_workday': is_workday,
                     'reports': reports, 'counts': counts})
