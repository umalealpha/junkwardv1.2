"""Compute the Transformation Board from what Omni already knows.

Every number on the board is a rule over live data. Nothing here asks a model
what it thinks, and nothing here is typed in by hand — that is deliberate. The
board names people (who is pushing automation, who is not), so each score has
to be defensible line by line when that person asks "why am I on this list?".

The four signals, and why each one is fair:

  delivery     the steps this person owns, and how far along they are.
  adoption     the Build Log knows what we shipped FOR a department and whether
               anyone confirmed using it. A feature shipped and never confirmed
               is the clearest evidence of automation not being taken up.
  response     tasks assigned to them that are past their due date.
  blocking     steps stopped because they have not done their part, in days.

Guards that keep it honest:
  * Someone with nothing assigned is NOT scored — an empty plate is not a
    refusal (a score of zero for a person nobody gave work to is a smear).
  * Someone on approved leave today is never called a laggard (notebook rule:
    a person on approved leave is never flagged).
  * Every score carries the counts it was built from, so the screen can show
    the evidence next to the name.
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

log = logging.getLogger(__name__)

from transformation.assign import assignments_for
from transformation.evolution import evolution
from transformation.models import (PROGRAMME_END, PROGRAMME_START,
                                   DepartmentPlan, Initiative)

# A person needs at least this much work on their plate before the board is
# willing to call their score meaningful.
MIN_WORKLOAD_TO_SCORE = 2

# Weights. Delivery dominates: doing the work beats talking about it.
WEIGHTS = {'delivery': 0.40, 'adoption': 0.25, 'response': 0.12,
           'blocking': 0.08, 'effectiveness': 0.15}


def _today() -> _dt.date:
    """Botswana date. Never date.today() — the server clock is UTC."""
    return timezone.localdate()


def _d(value) -> Decimal:
    return Decimal(str(value or 0))


def _expected_percent(init) -> int:
    """How far through this step's own window we are, 0-100.

    A step in month 3 is not late in month 1. Each step is measured against
    the time it has actually had, not against the whole programme — the same
    rule `build_board` uses for the "behind" list, so one number never has two
    computations.
    """
    start = _dt.date.fromisoformat(PROGRAMME_START)
    target = init.target_date
    if not target or target <= start:
        return 100
    total = (target - start).days or 1
    elapsed = (_today() - start).days
    if elapsed <= 0:
        return 0
    return max(0, min(100, round(elapsed * 100 / total)))


# How far behind a step may drift before it costs its owner anything. The
# same 10 points the "behind" list uses, so one number has one meaning.
GRACE_POINTS = 10


def _credit(percent: int, due: int) -> float:
    """Credit for a step, by POINTS BEHIND — never a ratio.

    A ratio has a cliff: on day two of a four-month programme `due` is 3%, and
    0/3 scores zero, so an owner who has had two days publishes as lagging.
    Points behind degrades gently and matches how a person actually reads it.
    """
    behind = max(0, due - percent - GRACE_POINTS)
    return max(0.0, 1.0 - behind / 100)


def programme_clock() -> dict:
    start = _dt.date.fromisoformat(PROGRAMME_START)
    end = _dt.date.fromisoformat(PROGRAMME_END)
    today = _today()
    total = (end - start).days or 1
    elapsed = max(0, min(total, (today - start).days))
    return {
        'start': start.isoformat(),
        'end': end.isoformat(),
        'today': today.isoformat(),
        'days_total': total,
        'days_elapsed': elapsed,
        'days_remaining': (end - today).days,
        'time_percent': round(elapsed * 100 / total),
    }


# --------------------------------------------------------------------------
# Live payroll shape. Read from payroll, never stored twice.
# --------------------------------------------------------------------------

def live_payroll() -> dict:
    """Active headcount and the latest month's cost, per department.

    Cost comes from the most recent period that actually has payslips, so a
    freshly opened month with nothing in it never reports the company as free.
    """
    from payroll.models import Payslip, PayrollPeriod

    from django.apps import apps
    Employee = apps.get_model('payroll', 'Employee')

    # Headcount and cost must come from the SAME population, or cost per head
    # is inflated by anyone who is paid but not flagged 'active' (on leave,
    # suspended) — and that figure feeds both the cost of unused automation
    # and salary at risk.
    PAID_STATUSES = ('active', 'on_leave')
    heads: dict[str, int] = defaultdict(int)
    for dept, n in (Employee.objects.filter(status__in=PAID_STATUSES)
                    .values_list('department')
                    .annotate(n=Count('id'))):
        heads[(dept or 'Unassigned').strip()] += n

    period = (PayrollPeriod.objects
              .filter(payslips__isnull=False)
              .order_by('-start_date')
              .distinct()
              .first())
    costs: dict[str, Decimal] = defaultdict(Decimal)
    total_cost = Decimal('0')
    if period:
        rows = (Payslip.objects.filter(period=period)
                .values('employee__department')
                .annotate(ctc=Sum('ctc_amount')))
        for row in rows:
            dept = (row['employee__department'] or 'Unassigned').strip()
            costs[dept] += _d(row['ctc'])
            total_cost += _d(row['ctc'])

    return {
        'period_name': getattr(period, 'period_name', ''),
        'headcount_total': sum(heads.values()),
        'cost_total': total_cost,
        'heads': dict(heads),
        'costs': {k: v for k, v in costs.items()},
    }


# --------------------------------------------------------------------------
# Adoption — the Build Log tells us what was shipped and whether it was used.
# --------------------------------------------------------------------------

def adoption_by_area() -> dict[str, dict]:
    """Per Build Log area: what is live, and what is still in the dev queue.

    IMPORTANT — what this can and cannot tell us. `DevItem.WAITING` is
    documented as "Built, waiting to go live": it is the DEVELOPMENT team's
    undeployed backlog, not a feature sitting in front of a department that
    nobody confirmed using. Omni has no "the requester confirmed they use
    this" field at all (`confirm_declined_at` is the CFO's own looks-done
    answer, not a department's).

    So `adoption_percent` is deliberately None. An earlier version computed
    live/(live+waiting) and called it adoption — which measured Software
    Development's deploy queue and then billed Claims and Finance in Pula for
    code that had never been deployed to them. A number with no evidence
    behind it is worse than an empty column.
    """
    from devlog.models import DevItem

    out: dict[str, dict] = {}
    rows = (DevItem.objects.exclude(area='')
            .values('area')
            .annotate(
                total=Count('id'),
                live=Count('id', filter=Q(status='live')),
                waiting=Count('id', filter=Q(status='waiting')),
                building=Count('id', filter=Q(status='building')),
                declined=Count('id', filter=Q(confirm_declined_at__isnull=False)),
            ))
    for row in rows:
        area = (row['area'] or '').strip().lower()
        if not area:
            continue
        bucket = out.setdefault(area, {'total': 0, 'live': 0, 'waiting': 0,
                                       'building': 0, 'declined': 0})
        for key in ('total', 'live', 'waiting', 'building', 'declined'):
            bucket[key] += row[key]

    for bucket in out.values():
        # No source of truth for adoption exists yet. None means "not
        # measured" everywhere downstream: no score, no ranking, no Pula.
        bucket['adoption_percent'] = None
    return out


# --------------------------------------------------------------------------
# People signals
# --------------------------------------------------------------------------

def _on_leave_today() -> tuple[set[str], bool]:
    """Everyone on approved leave right now, and whether we could actually look.

    Returns (emails, ok). **ok=False means we do not know who is on leave.**

    This signature exists because the obvious version — swallow the error and
    return an empty set — reads as "nobody is on leave", and the caller then
    happily brands a person on approved leave a laggard. A failed sub-query
    must never produce a conclusion; the honest answer is "I could not check",
    and the leaderboard refuses to name anyone until it can.
    """
    # hris is a LOCAL_APP in this same repo — it is always installed, so
    # guarding its import would be error handling for a case that cannot
    # happen. What CAN happen is the query failing, and that is caught below.
    from hris.models import LeaveRequest

    today = _today()
    try:
        rows = list(LeaveRequest.objects
                    .filter(status='approved', start_date__lte=today, end_date__gte=today)
                    .values_list('profile__employee__email', flat=True))
    except (FieldError, DatabaseError) as exc:
        # Narrow on purpose. The first version caught bare Exception, which
        # swallowed a wrong field name for as long as it existed — the guard
        # looked like it worked and never ran. Anything outside these two is a
        # bug that should surface, not be absorbed here.
        log.error('transformation: leave lookup failed (%s)', exc)
        return set(), False
    return {(e or '').strip().lower() for e in rows if e}, True


def _overdue_tasks() -> dict[str, dict]:
    """Open TRANSFORMATION tasks past their due date, by assignee email.

    Scoped to tasks this board raised — not every task in Omni.

    Found by running the board against live data on the day it shipped: the
    first person it published as "not pushing automation" had NO transformation
    step due yet. She had two overdue tasks from unrelated work, which scored
    her response at 0% and pushed her under the line. The board would have
    imported the company's general task debt and presented it to the CEO as a
    judgement about automation.

    A board about this programme judges work on this programme. Other overdue
    tasks are somebody else's screen.
    """
    from core.models import OmniTask

    today = _today()
    out: dict[str, dict] = defaultdict(lambda: {'open': 0, 'overdue': 0})
    rows = (OmniTask.objects
            .filter(status__in=['pending', 'in_progress', 'blocked'])
            .filter(transformation_assignments__isnull=False)
            .distinct()
            .values('assignee__email', 'due_at'))
    for row in rows:
        email = (row['assignee__email'] or '').strip().lower()
        if not email:
            continue
        out[email]['open'] += 1
        due = row['due_at']
        if due and due < today:
            out[email]['overdue'] += 1
    return dict(out)


def _score_from(delivery, adoption, response, blocking, effectiveness=None) -> int:
    """Blend the signals. Any signal that has no evidence is skipped, and the
    remaining weights are re-normalised — a person is never marked down for a
    signal we could not measure."""
    parts = {'delivery': delivery, 'adoption': adoption, 'response': response,
             'blocking': blocking, 'effectiveness': effectiveness}
    usable = {k: v for k, v in parts.items() if v is not None}
    if not usable:
        return 0
    weight = sum(WEIGHTS[k] for k in usable)
    return round(sum(WEIGHTS[k] * v for k, v in usable.items()) / weight)


def department_scores(workforce_rows: list[dict] | None = None) -> list[dict]:
    """One row per department: are they taking up what we built, and the cost."""
    payroll = live_payroll()
    adoption = adoption_by_area()
    plans = {p.department: p for p in DepartmentPlan.objects.all()}
    effect = {r['department']: r for r in (workforce_rows or [])}

    # One query for every step, grouped in Python. The per-department filter
    # this replaces ran four queries per department on every page load.
    by_department: dict[str, list] = defaultdict(list)
    for init in Initiative.objects.all():
        by_department[init.department].append(init)

    # Map a department to the Build Log areas that serve it.
    from transformation.seed import DEPARTMENT_AREAS

    rows: list[dict] = []
    departments = set(payroll['heads']) | set(plans) | set(by_department)
    for dept in sorted(departments):
        plan = plans.get(dept)
        heads = payroll['heads'].get(dept, 0)
        cost = payroll['costs'].get(dept, Decimal('0'))

        inits = by_department.get(dept, [])
        # Only steps far enough into their own window to carry information.
        # Below GRACE_POINTS `_credit` returns 1.0 whatever the work, so such a
        # step cannot distinguish anybody — gating on > 0 fixed only day ONE:
        # the next morning a month-4 step is 1% due, scores a free 100, and the
        # whole table goes green with attendance as the only real variable.
        open_inits = [i for i in inits if _expected_percent(i) > GRACE_POINTS]
        total_weight = sum(float(i.fte_released or 0) + 1 for i in open_inits)
        delivery = None
        if open_inits:
            done = sum((float(i.fte_released or 0) + 1)
                       * _credit(i.percent, _expected_percent(i)) for i in open_inits)
            delivery = round(done * 100 / total_weight) if total_weight else 0

        areas = DEPARTMENT_AREAS.get(dept, [])
        shipped = confirmed = waiting = 0
        for area in areas:
            bucket = adoption.get(area)
            if not bucket:
                continue
            # Live only. "Waiting" has not reached the department at all.
            shipped += bucket['live']
            confirmed += bucket['live']
            waiting += bucket['waiting']
        adoption_pct = None

        # A step waiting on Pramod or MotoLink is not the department's
        # failing. Vendor blocks are shown on the board; they never score.
        blocked = sum(1 for i in inits
                      if i.status == Initiative.Status.BLOCKED and not i.is_vendor)
        # No steps means nothing to block — that is an absence of evidence, not
        # a clean record. Scoring it 100 handed a perfect mark to any
        # department nobody had given work to, and pushed the departments
        # actually doing the work down the ranking.
        blocking_score = None if not inits else max(0, 100 - blocked * 25)

        # Effectiveness: hours actually worked against hours owed, from the
        # stored per-day record. Capped at 100 so a department cannot buy a
        # good automation score with overtime.
        work = effect.get(dept, {})
        attendance = work.get('attendance_percent')
        effectiveness = min(100, attendance) if attendance is not None else None

        # A column headed "automation score" must rest on an AUTOMATION signal:
        # steps delivered, or evidence the department uses what we shipped.
        # Without one, the only thing left is attendance — and publishing a
        # department's timesheet under that heading tells the CEO something
        # the number does not mean. Attendance still shows in its own column.
        has_automation_signal = delivery is not None or adoption_pct is not None
        score = (_score_from(delivery, adoption_pct, None, blocking_score, effectiveness)
                 if has_automation_signal else None)

        # What the unused automation is costing. Only steps that are FINISHED
        # count: we cannot bill a department for not using something we have
        # not delivered yet.
        released = sum((_d(i.fte_released) for i in inits
                        if i.status == Initiative.Status.DONE), Decimal('0'))
        cost_per_head = (cost / heads) if heads else Decimal('0')
        # Unknown adoption is NOT zero adoption. `(100 - (pct or 0))` would
        # turn "we have no evidence either way" into "they used none of it"
        # and bill the department the full amount in Pula.
        if adoption_pct is None:
            unused_fte = Decimal('0')
        else:
            unused_fte = _d(released) * (Decimal(100 - adoption_pct) / 100)
        monthly_waste = (cost_per_head * unused_fte).quantize(Decimal('0.01'))

        rows.append({
            'department': dept,
            'manager_name': plan.manager_name if plan else '',
            'manager_email': plan.manager_email if plan else '',
            'headcount_now': heads,
            'headcount_target': plan.headcount_target if plan else heads,
            'cost_now': float(cost),
            'cost_per_head': float(cost_per_head.quantize(Decimal('0.01'))),
            'reallocate_to_acquisition': plan.reallocate_to_acquisition if plan else 0,
            'initiatives': len(inits),
            'initiatives_done': sum(1 for i in inits
                                    if i.status == Initiative.Status.DONE),
            'initiatives_blocked': blocked,
            'delivery_percent': delivery,
            'shipped_for_them': shipped,
            'confirmed_used': confirmed,
            'awaiting_confirmation': waiting,
            'adoption_percent': adoption_pct,
            'automation_score': score,
            'scored': score is not None,
            'why_unscored': ('' if score is not None else
                             'Nothing due yet and nothing shipped for them — '
                             'attendance alone is not an automation score'),
            'monthly_cost_of_not_using': float(monthly_waste),
            'cost_measurable': adoption_pct is not None,
            'automation_note': plan.automation_note if plan else '',
            'manual_work_note': plan.manual_work_note if plan else '',
            # Workforce side — how the department actually spends its month.
            'attendance_percent': attendance,
            'short_day_rate': work.get('short_day_rate'),
            'unexplained_days': work.get('unexplained_days'),
            'awaiting_manager': work.get('awaiting_manager'),
            'manager_sla_days': work.get('manager_sla_days'),
            'leave_days': work.get('leave_days'),
            'salary_at_risk': work.get('salary_at_risk'),
            'time_doctor': work.get('time_doctor', {}),
        })

    # Unscored departments sort last, out of the ranking entirely — they are
    # neither the best nor the worst, they are simply not measured.
    rows.sort(key=lambda r: (r['automation_score'] is None,
                             r['automation_score'] if r['automation_score'] is not None else 0,
                             -r['monthly_cost_of_not_using']))
    return rows


def manager_leaderboard() -> dict:
    """Who is pushing automation and who is not — with the evidence attached."""
    adoption = adoption_by_area()
    overdue = _overdue_tasks()
    on_leave, leave_known = _on_leave_today()

    people: dict[str, dict] = {}
    for init in Initiative.objects.exclude(manager_email=''):
        email = init.manager_email.strip().lower()
        row = people.setdefault(email, {
            'name': init.manager_name or email,
            'email': email,
            'department': init.department,
            'owned': 0, 'done': 0, 'blocked': 0, 'weighted': 0.0, 'progress': 0.0,
            'blocked_days': 0, 'open_steps': 0,
        })
        weight = float(init.fte_released or 0) + 1
        row['owned'] += 1

        # Only steps whose own window has OPENED count towards delivery.
        # Scoring absolute completion instead would brand every owner a
        # laggard on day one of a four-month programme — including people
        # whose work is not due until month 4.
        due = _expected_percent(init)
        # Same grace-band rule as departments: a step that cannot cost you
        # anything yet must not earn you anything either.
        if due <= GRACE_POINTS:
            continue
        row['open_steps'] += 1
        row['weighted'] += weight
        # Credit is progress against what is due by now, capped at 1 — being
        # ahead on one step does not buy forgiveness on another.
        row['progress'] += weight * _credit(init.percent, due)
        if init.status == Initiative.Status.DONE:
            row['done'] += 1
        if init.status == Initiative.Status.BLOCKED:
            row['blocked'] += 1
            # Only a block THIS person can clear counts against them.
            if init.blocked_since and not init.is_vendor:
                row['blocked_days'] += max(0, (_today() - init.blocked_since).days)

    pushing, lagging, unscored = [], [], []
    for email, row in people.items():
        tasks = overdue.get(email, {'open': 0, 'overdue': 0})
        # Work that has not started yet is not workload you can be judged on.
        workload = row['open_steps'] + tasks['open']

        delivery = round(row['progress'] * 100 / row['weighted']) if row['weighted'] else None
        # Adoption is NOT measurable today — see adoption_by_area(). This line
        # previously rebuilt live/(live+waiting) from the raw counts, which
        # meant the DEVELOPMENT team's undeployed backlog moved a named
        # colleague's score by ~17 points and could push them across the line
        # into "Lagging". Fixing the department path alone left this one live.
        adoption_pct = None
        response = None
        if tasks['open']:
            response = round((tasks['open'] - tasks['overdue']) * 100 / tasks['open'])
        blocking = max(0, 100 - row['blocked_days'] * 5) if row['owned'] else None

        entry = {
            **row,
            'open_tasks': tasks['open'],
            'overdue_tasks': tasks['overdue'],
            'delivery_percent': delivery,
            'adoption_percent': adoption_pct,
            'response_percent': response,
            'automation_score': _score_from(delivery, adoption_pct, response, blocking),
            'on_leave': email in on_leave,
            'evidence': (
                f"{row['done']}/{row['owned']} steps done · "
                f"{tasks['overdue']} overdue task(s) · "
                f"{row['blocked_days']} day(s) holding someone up"
            ),
        }

        if row['open_steps'] == 0 and tasks['open'] == 0:
            entry['why_unscored'] = 'Nothing due yet — their steps open later'
            unscored.append(entry)
            continue
        if workload < MIN_WORKLOAD_TO_SCORE:
            entry['why_unscored'] = 'Not enough assigned work to judge'
            unscored.append(entry)
        elif entry['on_leave']:
            entry['why_unscored'] = 'On approved leave — never listed as a laggard'
            unscored.append(entry)
        elif not leave_known and entry['automation_score'] < 60:
            # We could not read the leave register, so we cannot rule out that
            # this person is on approved leave. Nobody is named until we can.
            entry['why_unscored'] = ('Leave register could not be read — nobody is '
                                     'listed as a laggard until it can')
            unscored.append(entry)
        elif entry['automation_score'] >= 60:
            pushing.append(entry)
        else:
            lagging.append(entry)

    pushing.sort(key=lambda r: -r['automation_score'])
    lagging.sort(key=lambda r: r['automation_score'])
    return {'pushing': pushing, 'lagging': lagging, 'unscored': unscored,
            'leave_data_available': leave_known}


# --------------------------------------------------------------------------
# The board
# --------------------------------------------------------------------------

def build_board() -> dict:
    """Everything the screen draws, computed fresh."""
    from transformation import workforce as wf

    clock = programme_clock()
    payroll = live_payroll()
    workforce_block = wf.workforce_block(payroll['costs'], payroll['heads'])
    depts = department_scores(workforce_block['departments'])
    leaders = manager_leaderboard()

    inits = list(Initiative.objects.prefetch_related('assignments__task'))
    weight = sum(float(i.fte_released or 0) + 1 for i in inits) or 1
    overall = round(sum((float(i.fte_released or 0) + 1) * (i.percent / 100)
                        for i in inits) * 100 / weight)

    by_month = []
    for month in (1, 2, 3, 4):
        rows = [i for i in inits if i.month == month]
        w = sum(float(i.fte_released or 0) + 1 for i in rows) or 1
        by_month.append({
            'month': month,
            'count': len(rows),
            'done': sum(1 for i in rows if i.status == Initiative.Status.DONE),
            'percent': round(sum((float(i.fte_released or 0) + 1) * (i.percent / 100)
                                 for i in rows) * 100 / w),
            'saving_bwp': float(sum(_d(i.annual_saving_bwp) for i in rows)),
            'fte_released': float(sum(_d(i.fte_released) for i in rows)),
        })

    target_heads = sum(d['headcount_target'] for d in depts)
    # Cost target scales each department by the shape it is heading for, at
    # today's cost per head. It is an estimate, and the board says so.
    target_cost = sum((_d(d['cost_per_head']) * d['headcount_target'] for d in depts),
                      Decimal('0'))

    blocked = [{
        'code': i.code, 'title': i.title, 'blocked_on': i.blocked_on,
        'days': (_today() - i.blocked_since).days if i.blocked_since else 0,
        'is_vendor': i.is_vendor, 'manager_name': i.manager_name,
        'annual_saving_bwp': float(i.annual_saving_bwp),
    } for i in inits if i.status == Initiative.Status.BLOCKED]
    blocked.sort(key=lambda r: -r['days'])

    # Behind = the clock has run further than the work has.
    behind = [{
        'code': i.code, 'title': i.title, 'manager_name': i.manager_name,
        'department': i.department, 'percent': i.percent, 'month': i.month,
        'target_date': i.target_date.isoformat() if i.target_date else None,
    } for i in inits
        if i.status != Initiative.Status.DONE
        and i.percent < _expected_percent(i) - GRACE_POINTS]
    behind.sort(key=lambda r: r['percent'])

    monthly_waste = sum((_d(d['monthly_cost_of_not_using']) for d in depts),
                        Decimal('0'))

    return {
        'clock': clock,
        'headline': 'First AI Insurance Company in Botswana',
        'overall_percent': overall,
        # The March of Progress strip. Reads the SAME weighted percent as
        # everything else — one number, one computation, so the creature can
        # never flatter us past what the work actually says.
        'evolution': evolution(overall),
        'time_percent': clock['time_percent'],
        'months': by_month,
        'departments': depts,
        'leaderboard': leaders,
        'blocked': blocked,
        'behind': behind,
        'staff_cost': {
            'period': payroll['period_name'],
            'now': float(payroll['cost_total']),
            'target': float(target_cost.quantize(Decimal('0.01'))),
            'saving': float((payroll['cost_total'] - target_cost).quantize(Decimal('0.01'))),
            'headcount_now': payroll['headcount_total'],
            'headcount_target': target_heads,
            'note': 'Cost target is today’s cost per head applied to the target shape — an estimate.',
        },
        'cost_of_delay': {
            'monthly': float(monthly_waste),
            'annual': float(monthly_waste * 12),
            'basis': 'Finished automation that a department has not confirmed using, '
                     'valued at that department’s own cost per head.',
        },
        'workforce': workforce_block,
        'initiatives': [{
            'code': i.code, 'title': i.title, 'plain_summary': i.plain_summary,
            'track': i.track, 'track_label': i.get_track_display(),
            'month': i.month, 'department': i.department,
            'manager_name': i.manager_name, 'manager_email': i.manager_email,
            'status': i.status, 'status_label': i.get_status_display(),
            'percent': i.percent,
            'target_date': i.target_date.isoformat() if i.target_date else None,
            'annual_saving_bwp': float(i.annual_saving_bwp),
            'fte_released': float(i.fte_released),
            'blocked_on': i.blocked_on, 'is_vendor': i.is_vendor,
            'improves_customer_service': i.improves_customer_service,
            'assignments': assignments_for(i),
        } for i in inits],
    }
