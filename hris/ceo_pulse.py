from __future__ import annotations

import datetime
import logging
from django.core.mail import EmailMultiAlternatives
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Count
from django.utils import timezone
from django.utils.html import escape

from core.models import OmniTask
from hris import exceptions_report
from hris.models import WorkdayJustification
from payroll.models import Employee, Payslip

log = logging.getLogger(__name__)

PULSE_TO = ('aiyer@alphadirect.co.bw',)
PULSE_CC = (
    'pganesharajah@alphadirect.co.bw',
    'arjuniyer@alphadirect.co.bw',
    'ubutale@alphadirect.co.bw',
)
PREVIEW_TO = ('pganesharajah@alphadirect.co.bw',)
# Share of the roster that must have Time Doctor data before the CEO gets it.
MIN_TD_COVERAGE = 0.7
# Below this many people a salary 'aggregate' is really one person's pay.
MIN_SALARY_GROUP = 3

NAVY = '#0D1B2A'
ORANGE = '#F4A623'
INK = '#1F2937'
MUT = '#6B7280'
GREEN = '#059669'
RED = '#B91C1C'

RESOLVED_JUSTIFICATION_STATUSES = {
    WorkdayJustification.Status.MET,
    WorkdayJustification.Status.NOT_REQUIRED,
    WorkdayJustification.Status.JUSTIFIED,
    WorkdayJustification.Status.UNJUSTIFIED,
}
PENDING_JUSTIFICATION_STATUSES = {
    WorkdayJustification.Status.PENDING,
    WorkdayJustification.Status.EXPLAINED,
}
OPEN_TASK_STATUSES = [
    OmniTask.Status.PENDING,
    OmniTask.Status.IN_PROGRESS,
    OmniTask.Status.PARTIAL,
    OmniTask.Status.BLOCKED,
]


def week_window(today: datetime.date) -> tuple[datetime.date, datetime.date]:
    """Return the Mon-Sat week that ENDS on the Saturday before `today`
    when today is Sunday; otherwise the most recent completed Mon-Sat."""
    if today.weekday() == 6:  # Sunday
        saturday = today - datetime.timedelta(days=1)
    else:
        # Monday -> 2 days back, ..., Saturday -> 7 days back
        offset = today.weekday() + 2
        saturday = today - datetime.timedelta(days=offset)
    monday = saturday - datetime.timedelta(days=5)
    return monday, saturday


def _is_zero_tracked(summary: dict) -> bool:
    """Return True when TD summary tracked is missing or zero-ish."""
    raw = summary.get('tracked')
    if raw is None or raw == '':
        return True
    try:
        if isinstance(raw, str):
            # Accept only numeric-ish strings like '0', '0.0'
            raw = raw.replace(',', '').strip()
            if not raw:
                return True
        return float(raw) <= 0
    except (TypeError, ValueError):
        return True


def _collect_td(monday: datetime.date, saturday: datetime.date, td_client) -> dict:
    """Pull the weekly Time Doctor exceptions and keep only aggregate data."""
    if td_client is None:
        return {
            'available': False,
            'reason': 'Time Doctor client is not configured.',
            'summary': {},
            'leaderboard': [],
            'shortfall': [],
            'late': [],
        }

    try:
        data = exceptions_report.compute_weekly(td_client, monday, persist_map=False)
    except Exception as exc:  # noqa: BLE001 - fail safe for the CEO brief
        return {
            'available': False,
            'reason': f'Time Doctor unavailable: {type(exc).__name__}: {exc}',
            'summary': {},
            'leaderboard': [],
            'shortfall': [],
            'late': [],
        }

    summary = data.get('summary') or {}
    # summary['tracked'] is a COUNT of people who tracked, not hours. A partial
    # pull (3 of 120) must hold the CEO send too (Opus judge).
    try:
        coverage = float(summary.get('tracked') or 0) / float(summary.get('roster') or 0)
    except (TypeError, ValueError, ZeroDivisionError):
        coverage = 0.0
    if _is_zero_tracked(summary) or summary.get('prod_h') is None or coverage < MIN_TD_COVERAGE:
        return {
            'available': False,
            'reason': (f'Time Doctor covers only {coverage:.0%} of the roster this week '
                       f'(need {MIN_TD_COVERAGE:.0%}).'),
            'summary': summary,
            'leaderboard': data.get('leaderboard', []),
            'shortfall': data.get('shortfall', []),
            'late': data.get('late', []),
        }

    return {
        'available': True,
        'reason': '',
        'summary': summary,
        'leaderboard': data.get('leaderboard', []),
        'shortfall': data.get('shortfall', []),
        'late': data.get('late', []),
    }


def _user_display_name(user) -> str:
    """Best non-PII display name for a Django user.

    Staff reports are allowed to show internal employee names. The salary
    section never shows a name beside a BWP figure."""
    name = (user.get_full_name() or '').strip()
    if not name and hasattr(user, 'employee_record'):
        emp = getattr(user, 'employee_record', None)
        if emp and getattr(emp, 'full_name', '').strip():
            name = emp.full_name.strip()
    if not name:
        name = user.username or ''
    return name or 'Unknown'


def _salary_at_risk(high_names: list[str], flight_error: str | None) -> dict:
    """Aggregate latest Payslip gross for the HIGH flight-risk band.

    NEVER per person: only the sum, headcount and the fixed basis note are
    returned."""
    if flight_error:
        return {
            'bwp': None,
            'people': 0,
            'basis': 'unavailable — flight-risk radar could not compute',
            'error': flight_error,
        }

    if not high_names:
        return {
            'bwp': Decimal('0'),
            'people': 0,
            'basis': 'latest monthly gross of high-risk staff',
        }

    # Match on identity, never on full name (duplicate names exist). A flight-risk
    # row's profile_id is the HRIS profile pk, or the employee pk if none.
    from hris.models import HRISProfile
    ids = [str(i) for i in high_names if i]
    employee_ids = list(
        Employee.objects.filter(status=Employee.Status.ACTIVE)
        .exclude(is_test_record=True)
        .filter(pk__in=[str(e) for e in HRISProfile.objects.filter(pk__in=ids)
                        .values_list('employee_id', flat=True)] + ids)
        .values_list('id', flat=True))
    if not employee_ids:
        return {
            'bwp': Decimal('0'),
            'people': 0,
            'basis': 'latest monthly gross of high-risk staff',
        }

    slips = (
        Payslip.objects
        .filter(employee_id__in=employee_ids)
        # Every Omni payslip is 'draft' (the pay run happens outside Omni), so
        # approved/paid-only found nothing live (18-Sep); use the latest
        # non-cancelled slip, as the Command Center does.
        .exclude(status=Payslip.Status.CANCELLED)
        .order_by('employee_id', '-period__start_date')
    )

    seen_employee_ids: set[int] = set()
    total = Decimal('0')
    for slip in slips:
        if slip.employee_id in seen_employee_ids:
            continue
        seen_employee_ids.add(slip.employee_id)
        total += slip.gross_amount or Decimal('0')

    if len(seen_employee_ids) < MIN_SALARY_GROUP:
        # One or two people: the 'aggregate' would be their pay, next to their
        # names in the flight-risk list — never show it (Opus judge).
        return {
            'bwp': None,
            'people': len(seen_employee_ids),
            'basis': f'fewer than {MIN_SALARY_GROUP} people — not shown',
        }
    return {
        'bwp': total,
        'people': len(seen_employee_ids),
        'basis': 'latest monthly gross of high-risk staff',
    }


def collect(monday: datetime.date, saturday: datetime.date, *, td_client=None) -> dict:
    """Collect every section of the Sunday CEO workforce pulse.

    No per-person salary information is returned anywhere in this payload.
    """
    td = _collect_td(monday, saturday, td_client)

    justifications = WorkdayJustification.objects.filter(
        work_date__gte=monday,
        work_date__lte=saturday,
    )

    # ---- Excuses ---------------------------------------------------------
    total_justifications = justifications.count()
    by_reason_raw = (
        justifications
        .values('reason')
        .annotate(cnt=Count('id'))
        .order_by('-cnt')[:5]
    )
    reason_label_map = dict(WorkdayJustification.Reason.choices)
    by_reason = [
        {
            'reason': reason_label_map.get(row['reason'], row['reason']),
            'count': row['cnt'],
        }
        for row in by_reason_raw
    ]

    unresolved = (
        justifications
        .exclude(status__in=RESOLVED_JUSTIFICATION_STATUSES)
        .count()
    )
    repeated = (
        justifications
        .values('profile_id')
        .annotate(cnt=Count('id'))
        .filter(cnt__gte=3)
        .count()
    )

    excuses = {
        'total': total_justifications,
        'by_reason': by_reason,
        'unresolved': unresolved,
        'repeated': repeated,
    }

    # ---- Managers to chase ----------------------------------------------
    pending_justifications = (
        justifications
        .filter(status__in=PENDING_JUSTIFICATION_STATUSES)
        .select_related('profile__manager')
    )
    manager_counts: dict[str, int] = {}
    for wj in pending_justifications:
        profile = getattr(wj, 'profile', None)
        manager = getattr(profile, 'manager', None) if profile else None
        if manager is None:
            # No line manager on file — skip gracefully rather than inventing one.
            continue
        name = (getattr(manager, 'full_name', '') or '').strip()
        if not name:
            continue
        manager_counts[name] = manager_counts.get(name, 0) + 1

    managers = [
        {'name': name, 'count': count}
        for name, count in sorted(
            manager_counts.items(),
            key=lambda item: item[1],
            reverse=True,
        )[:5]
    ]

    # ---- Tasks -----------------------------------------------------------
    open_tasks = OmniTask.objects.filter(
        status__in=OPEN_TASK_STATUSES,
        due_at__isnull=False,
        due_at__lt=saturday + datetime.timedelta(days=1),
    )
    overdue_tasks = open_tasks.count()
    done_tasks = OmniTask.objects.filter(
        status=OmniTask.Status.DONE,
        completed_at__date__gte=monday,
        completed_at__date__lte=saturday,
    ).count()

    overdue_by_assignee = (
        open_tasks
        .values('assignee_id')
        .annotate(cnt=Count('id'))
        .order_by('-cnt')[:5]
    )
    User = get_user_model()
    top_assignee_users = User.objects.filter(
        pk__in=[row['assignee_id'] for row in overdue_by_assignee if row.get('assignee_id')]
    )
    name_by_user_id = {
        user.pk: _user_display_name(user)
        for user in top_assignee_users
    }
    top_task_assignees = [
        {'name': name_by_user_id.get(row['assignee_id'], 'Unknown'),
         'overdue': row['cnt']}
        for row in overdue_by_assignee
    ]

    tasks = {
        'overdue': overdue_tasks,
        'done': done_tasks,
        'top_assignees': top_task_assignees,
    }

    # ---- Flight risk -----------------------------------------------------
    try:
        from hris.flight_risk_views import compute_flight_risk, _band_for_score

        flight_results = compute_flight_risk()
        if not isinstance(flight_results, list):
            raise TypeError('compute_flight_risk did not return a list')

        counts = {'low': 0, 'med': 0, 'high': 0}
        for row in flight_results:
            band = row.get('band') or _band_for_score(row.get('score', 0))
            counts[band] = counts.get(band, 0) + 1

        top_flight = [
            {
                'name': row.get('name', ''),
                'band': row.get('band') or _band_for_score(row.get('score', 0)),
                'score': row.get('score'),
            }
            for row in flight_results[:5]
        ]
        high_names = [row.get('profile_id') for row in flight_results if (row.get('band') or _band_for_score(row.get('score', 0))) == 'high']

        flight_risk = {
            'counts': counts,
            'top': top_flight,
            'error': None,
        }
        flight_error = None
    except Exception as exc:  # noqa: BLE001 - brief must still build
        flight_risk = {
            'counts': {'low': 0, 'med': 0, 'high': 0},
            'top': [],
            'error': f'{type(exc).__name__}: {exc}',
        }
        flight_error = flight_risk['error']
        high_names = []

    salary_at_risk = _salary_at_risk(high_names, flight_error)

    # ---- Actions ----------------------------------------------------------
    responded_in_window = justifications.filter(
        responded_at__date__gte=monday,
        responded_at__date__lte=saturday,
    ).count()
    actions = responded_in_window + done_tasks

    missing: list[str] = []
    if not td['available']:
        missing.append('Time Doctor data for the week')
    if flight_error:
        missing.append('Flight-risk radar could not compute')

    complete = not missing

    return {
        'td': td,
        'excuses': excuses,
        'managers': managers,
        'tasks': tasks,
        'flight_risk': flight_risk,
        'salary_at_risk': salary_at_risk,
        'actions': actions,
        'complete': complete,
        'missing': missing,
    }


def ceo_question(data: dict) -> str | None:
    """Ask the AI for one short CEO question using aggregate figures only.

    No person names are passed to the AI. The pulse must work without it.
    """
    try:
        from core.ai_assist import reasoning_complete
    except Exception:  # noqa: BLE001
        return None

    td = data.get('td') or {}
    summary = td.get('summary') or {}
    flight_risk = data.get('flight_risk') or {}
    flight_counts = flight_risk.get('counts') or {}
    salary_at_risk = data.get('salary_at_risk') or {}
    excuses = data.get('excuses') or {}
    tasks = data.get('tasks') or {}
    managers = data.get('managers') or []

    prompt_lines = [
        'You are preparing a very short Sunday workforce pulse for the CEO.',
        'Use ONLY the aggregate numbers below. Do not mention any person by name.',
        'Return a single short strategic question for the CEO to put to the executive team on Monday.',
        '',
        f"Tracked productive hours: {summary.get('prod_h', 'n/a')}",
        f"Did not track: {summary.get('did_not_track', 'n/a')}",
        f"Matched roster: {summary.get('roster', 'n/a')}",
        f"Overdue tasks: {tasks.get('overdue', 'n/a')}",
        f"Tasks done this week: {tasks.get('done', 'n/a')}",
        f"High flight-risk staff count: {flight_counts.get('high', 'n/a')}",
        f"Unresolved justifications: {excuses.get('unresolved', 'n/a')}",
        f"Staff with 3+ justifications this week: {excuses.get('repeated', 'n/a')}",
        f"Managers with pending justifications: {len(managers)}",
        f"Aggregate salary at risk (BWP): {salary_at_risk.get('bwp', 'n/a')}",
        f"Actions completed this week: {data.get('actions', 'n/a')}",
    ]

    try:
        answer = reasoning_complete('\n'.join(prompt_lines))
    except Exception:  # noqa: BLE001
        return None

    if not answer:
        return None

    question = ' '.join(answer.strip().split())
    return question or None


def _escape(value) -> str:
    return escape(str(value))


def _bwp(amount: Decimal | None) -> str:
    if amount is None:
        return 'Unavailable'
    try:
        value = Decimal(str(amount)).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
        return f'BWP {value:,}'
    except Exception:  # noqa: BLE001
        return f'BWP {amount}'


def _chip(label: str, value) -> str:
    return (
        '<td style="width:50%; padding:6px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="border-collapse:collapse;background:#F8FAFC;border:1px solid #EAEEF3;border-radius:10px;">'
        '<tr><td style="padding:12px 14px;">'
        f'<div style="font-size:11px;color:{MUT};text-transform:uppercase;'
        f'font-family:\'Segoe UI\',Arial,sans-serif;">{_escape(label)}</div>'
        f'<div style="font-size:20px;font-weight:800;color:{NAVY};'
        f'font-family:\'Segoe UI\',Arial,sans-serif;">{_escape(value)}</div>'
        '</td></tr></table></td>'
    )


def _section(emoji: str, title: str, body_html: str) -> str:
    return (
        '<tr><td style="padding:16px 22px 0;">'
        f'<div style="background:{NAVY};color:#fff;font-size:13px;font-weight:700;'
        f'padding:8px 12px;border-radius:8px 8px 0 0;'
        f'font-family:\'Segoe UI\',Arial,sans-serif;">{_escape(emoji)} {_escape(title)}</div>'
        f'<div style="border:1px solid #EEF0F3;border-top:none;border-radius:0 0 8px 8px;'
        f'padding:12px;color:{INK};font-size:13px;line-height:1.55;'
        f'font-family:\'Segoe UI\',Arial,sans-serif;">{body_html}</div>'
        '</td></tr>'
    )


def _td_unavailable_note(reason: str) -> str:
    return (
        f'<div style="background:#F3F4F6;border:1px solid #E5E7EB;border-radius:8px;'
        f'padding:12px;color:{MUT};font-size:13px;">'
        f'Time Doctor data unavailable for this week: {_escape(reason)}</div>'
    )


def build_html(data: dict, *, monday: datetime.date, saturday: datetime.date,
               question: str | None, preview: bool) -> str:
    esc = _escape
    td = data.get('td') or {}
    td_available = bool(td.get('available'))
    td_reason = td.get('reason') or ''
    td_summary = td.get('summary') or {}
    td_leaderboard = td.get('leaderboard') or []
    td_shortfall = td.get('shortfall') or []
    td_late = td.get('late') or []

    excuses = data.get('excuses') or {}
    managers = data.get('managers') or []
    tasks = data.get('tasks') or {}
    flight_risk = data.get('flight_risk') or {}
    flight_counts = flight_risk.get('counts') or {}
    flight_top = flight_risk.get('top') or []
    salary_at_risk = data.get('salary_at_risk') or {}
    actions = data.get('actions', 0)
    missing = data.get('missing') or []
    complete = bool(data.get('complete'))

    # ---- KPI cards -------------------------------------------------------
    if td_available:
        tracked_hours = td_summary.get('total_h', '—')
        # The weekly Time Doctor report does not compute late starts; never
        # show an invented 0 (Opus judge).
        late_value = 'n/a'
    else:
        tracked_hours = 'Unavailable'
        late_value = 'Unavailable'

    overdue_tasks = tasks.get('overdue', 0) or 0
    high_flight_risk = flight_counts.get('high', 0) or 0

    kpi_html = (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="border-collapse:separate;border-spacing:6px;">'
        f'<tr>{_chip("Tracked hours", tracked_hours)}{_chip("Late starts", late_value)}</tr>'
        f'<tr>{_chip("Overdue tasks", overdue_tasks)}{_chip("High flight risk", high_flight_risk)}</tr>'
        '</table>'
    )
    if not td_available:
        kpi_html += _td_unavailable_note(td_reason)

    # ---- Salary at risk ---------------------------------------------------
    bwp_value = salary_at_risk.get('bwp')
    salary_people = salary_at_risk.get('people', 0)
    salary_basis = salary_at_risk.get('basis', 'latest monthly gross of high-risk staff')
    salary_html = (
        f'<div style="font-size:24px;font-weight:800;color:{NAVY};margin-bottom:6px;">'
        f'{esc(_bwp(bwp_value))}</div>'
        f'<div style="color:{MUT};font-size:12px;">Aggregate {esc(salary_basis)} '
        f'({esc(salary_people)} people).</div>'
    )

    # ---- Team league table ------------------------------------------------
    if td_available:
        if td_leaderboard:
            league_rows = []
            for row in td_leaderboard[:10]:
                # Row keys from hris.workforce_pulse.team_leaderboard.
                name = row.get('dept') or 'Unknown'
                hours = row.get('avg_prod_h') if row.get('avg_prod_h') is not None else '—'
                league_rows.append(
                    '<tr>'
                    '<td style="padding:5px 0;font-size:13px;color:#1F2937;'
                    f'font-family:\'Segoe UI\',Arial,sans-serif;">{esc(name)}</td>'
                    '<td style="padding:5px 0;font-size:13px;color:#1F2937;text-align:right;'
                    f'font-family:\'Segoe UI\',Arial,sans-serif;">{esc(hours)}</td>'
                    '</tr>'
                )
            league_html = (
                '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
                'style="border-collapse:collapse;">'
                + ''.join(league_rows) + '</table>'
            )
        else:
            league_html = f'<div style="font-size:13px;color:{GREEN};">None — all clear. ✅</div>'
    else:
        league_html = _td_unavailable_note(td_reason)

    # ---- Excuses ----------------------------------------------------------
    exc_total = excuses.get('total', 0) or 0
    exc_unresolved = excuses.get('unresolved', 0) or 0
    exc_repeated = excuses.get('repeated', 0) or 0
    exc_by_reason = excuses.get('by_reason') or []
    excuses_html = (
        f'<div style="margin-bottom:8px;">{esc(exc_total)} justifications · '
        f'{esc(exc_unresolved)} unresolved · {esc(exc_repeated)} staff with 3+ days.</div>'
    )
    if exc_by_reason:
        reason_rows = []
        for item in exc_by_reason:
            reason = item.get('reason') or item.get('label') or 'Unknown'
            count = item.get('count', 0)
            reason_rows.append(
                '<tr>'
                '<td style="padding:4px 0;font-size:13px;color:#1F2937;">'
                f'{esc(reason)}</td>'
                '<td style="padding:4px 0;font-size:13px;color:#1F2937;text-align:right;">'
                f'{esc(count)}</td></tr>'
            )
        excuses_html += (
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            'style="border-collapse:collapse;">' + ''.join(reason_rows) + '</table>'
        )

    # ---- Managers ---------------------------------------------------------
    managers_html = ''
    if managers:
        manager_rows = []
        for item in managers:
            name = item.get('name') or 'Unknown'
            count = item.get('count', 0)
            manager_rows.append(
                '<tr>'
                '<td style="padding:4px 0;font-size:13px;color:#1F2937;">'
                f'{esc(name)}</td>'
                '<td style="padding:4px 0;font-size:13px;color:#1F2937;text-align:right;">'
                f'{esc(count)} pending</td></tr>'
            )
        managers_html = (
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            'style="border-collapse:collapse;">' + ''.join(manager_rows) + '</table>'
        )
    else:
        managers_html = f'<div style="font-size:13px;color:{GREEN};">None — all clear. ✅</div>'

    # ---- Tasks ------------------------------------------------------------
    tasks_overdue = tasks.get('overdue', 0) or 0
    tasks_done = tasks.get('done', 0) or 0
    tasks_top = tasks.get('top_assignees') or []
    tasks_html = (
        f'<div style="margin-bottom:8px;">{esc(tasks_overdue)} overdue · '
        f'{esc(tasks_done)} done this week.</div>'
    )
    if tasks_top:
        task_rows = []
        for item in tasks_top:
            name = item.get('name') or 'Unknown'
            overdue = item.get('overdue', 0)
            task_rows.append(
                '<tr>'
                '<td style="padding:4px 0;font-size:13px;color:#1F2937;">'
                f'{esc(name)}</td>'
                '<td style="padding:4px 0;font-size:13px;color:#1F2937;text-align:right;">'
                f'{esc(overdue)} overdue</td></tr>'
            )
        tasks_html += (
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            'style="border-collapse:collapse;">' + ''.join(task_rows) + '</table>'
        )

    # ---- Flight risk -------------------------------------------------------
    flight_html = (
        f'<div style="margin-bottom:8px;">'
        f'High {esc(flight_counts.get("high", 0))} · '
        f'Medium {esc(flight_counts.get("med", 0))} · '
        f'Low {esc(flight_counts.get("low", 0))}</div>'
    )
    if flight_top:
        top_rows = []
        for row in flight_top:
            name = row.get('name') or 'Unknown'
            band = (row.get('band') or '').upper()
            top_rows.append(
                '<tr>'
                '<td style="padding:4px 0;font-size:13px;color:#1F2937;">'
                f'{esc(name)}</td>'
                '<td style="padding:4px 0;font-size:13px;color:#1F2937;text-align:right;">'
                f'{esc(band)}</td></tr>'
            )
        flight_html += (
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            'style="border-collapse:collapse;">' + ''.join(top_rows) + '</table>'
        )
    if flight_risk.get('error'):
        flight_html += (
            f'<div style="color:#B45309;font-size:12px;margin-top:8px;">'
            f'Radar degraded: {esc(flight_risk.get("error"))}</div>'
        )

    # ---- Actions -----------------------------------------------------------
    actions_html = f'<div style="font-size:13px;color:#1F2937;">{esc(actions)} this week.</div>'

    # ---- Complete raw body ------------------------------------------------
    body_rows = [
        _section('📊', 'KPI cards', kpi_html),
        _section('💷', 'Salary at risk', salary_html),
        _section('🏆', 'Team league table', league_html),
        _section('🧾', 'Excuses', excuses_html),
        _section('👔', 'Managers to chase', managers_html),
        _section('✅', 'Tasks', tasks_html),
        _section('🕊️', 'Flight risk', flight_html),
        _section('⚡', 'Actions this week', actions_html),
    ]

    if question:
        body_rows.append(_section('❓', 'One question for Monday',
                                  f'<div style="font-size:14px;font-style:italic;color:#1F2937;">'
                                  f'{esc(question)}</div>'))

    incomplete_strip_html = ''
    if not complete:
        missing_display = esc(', '.join(missing) if missing else '')
        incomplete_strip_html = (
            '<tr><td style="background:#B91C1C;color:#fff;font-size:13px;'
            'padding:10px 16px;font-family:\'Segoe UI\',Arial,sans-serif;">'
            'INCOMPLETE — this will not go to the CEO'
            f'{(" — Missing: " + missing_display) if missing_display else ""}'
            '</td></tr>'
        )

    monday_label = monday.strftime('%a %d %b')
    saturday_label = saturday.strftime('%a %d %b')
    subtitle = f'Week {monday_label} – {saturday_label}'

    html = (
        '<!doctype html><html><head><meta charset="utf-8"></head>'
        '<body style="margin:0;background:#EEF2F7;font-family:\'Book Antiqua\',Palatino,Georgia,serif;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="border-collapse:collapse;background:#EEF2F7;">'
        '<tr><td align="center" style="padding:16px 12px;">'
        '<table role="presentation" width="600" cellpadding="0" cellspacing="0" '
        'style="width:600px;max-width:100%;border-collapse:collapse;background:#fff;border-radius:14px;overflow:hidden;">'
        f'{incomplete_strip_html}'
        '<tr><td style="background:#0D1B2A;padding:18px 22px;">'
        '<div style="font-size:11px;color:#FFD98A;letter-spacing:.06em;text-transform:uppercase;'
        'font-family:\'Segoe UI\',Arial,sans-serif;">Alpha Direct &middot; CEOs Briefing</div>'
        '<div style="font-size:18px;font-weight:800;color:#F4A623;margin-top:4px;'
        'font-family:\'Segoe UI\',Arial,sans-serif;">Sunday Workforce Pulse</div>'
        f'<div style="color:#E5E7EB;font-size:13px;margin-top:6px;'
        f'font-family:\'Segoe UI\',Arial,sans-serif;">{esc(subtitle)}</div>'
        '</td></tr>'
        + ''.join(body_rows) +
        '</table></td></tr></table></body></html>'
    )
    return html


def send_pulse(*, preview: bool, dry_run: bool, today: datetime.date | None = None,
               td_client=None) -> dict:
    if today is None:
        today = timezone.localdate()

    monday, saturday = week_window(today)
    data = collect(monday, saturday, td_client=td_client)
    question = ceo_question(data)
    html = build_html(data, monday=monday, saturday=saturday, question=question,
                      preview=preview)

    # Prepend the do-not-reply banner if it is exposed by core.notifications.
    try:
        from core.notifications import no_reply_banner
        html = no_reply_banner() + html
    except Exception:  # noqa: BLE001
        pass

    subject = f"Sunday Workforce Pulse — week to {saturday:%d %b}"
    if preview:
        subject += " [PREVIEW]"

    from_email = (
        getattr(settings, 'OMNI_FROM_EMAIL', '')
        or getattr(settings, 'DEFAULT_FROM_EMAIL', 'Omni ERP <omni@alphadirect.co.bw>')
    )

    plain = f'Sunday Workforce Pulse — week to {saturday:%d %b}\n\n'
    if not data.get('complete'):
        plain += 'INCOMPLETE — this will not go to the CEO. Missing: '
        plain += ', '.join(data.get('missing', [])) + '\n'
    plain += '(See the HTML version.)'

    if preview:
        recipients = list(PREVIEW_TO)
        cc: list[str] = []
        if dry_run:
            return {
                'sent': False,
                'prepared': True,
                'preview': True,
                'dry_run': True,
                'html_len': len(html),
                'to': recipients,
                'cc': cc,
                'complete': data.get('complete'),
                'missing': data.get('missing', []),
            }
        msg = EmailMultiAlternatives(
            subject=subject,
            body=plain,
            from_email=from_email,
            to=recipients,
            cc=cc,
        )
        msg.attach_alternative(html, 'text/html')
        msg.send()
        return {
            'sent': True,
            'preview': True,
            'html_len': len(html),
            'to': recipients,
            'cc': cc,
            'complete': data.get('complete'),
            'missing': data.get('missing', []),
        }

    if not data.get('complete'):
        return {
            'sent': False,
            'held': True,
            'html_len': len(html),
            'to': list(PULSE_TO),
            'cc': list(PULSE_CC),
            'missing': data.get('missing', []),
        }

    recipients = list(PULSE_TO)
    cc = list(PULSE_CC)


    if dry_run:
        return {
            'sent': False,
            'prepared': True,
            'dry_run': True,
            'html_len': len(html),
            'to': recipients,
            'cc': cc,
            'complete': True,
        }

    msg = EmailMultiAlternatives(
        subject=subject,
        body=plain,
        from_email=from_email,
        to=recipients,
        cc=cc,
    )
    msg.attach_alternative(html, 'text/html')
    # Recipient safeguard on the message itself, not an assert (asserts vanish
    # under python -O): the CEO send goes to exactly these people or not at all.
    if (tuple(msg.to) != PULSE_TO or tuple(msg.cc) != PULSE_CC or msg.bcc):
        return {'sent': False, 'held': True, 'missing': ['recipient safeguard tripped']}
    msg.send()
    return {
        'sent': True,
        'preview': False,
        'html_len': len(html),
        'to': recipients,
        'cc': cc,
        'complete': True,
    }

