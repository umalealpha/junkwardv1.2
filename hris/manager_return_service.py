"""
hris/manager_return_service.py — assemble the facts half of the Monthly Manager
Return, and drive its workflow (CFO 2026-07-26).

Every number here comes from omni's OWN persisted stores — the same ones the
morning brief and the exceptions report already use — so the return cannot
disagree with the daily emails staff already receive. No live Time Doctor call.

Reuses hris.perf_panel.employee_month_panel verbatim for the per-person metrics
rather than re-deriving them; required-vs-tracked hours come from
WorkdayJustification, which the daily brief already writes per person per day.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from hris.manager_return_models import (
    ManagerMonthlyReturn, ReturnStatus, ReviewVerdict,
)
from hris.perf_panel import employee_month_panel, month_bounds


def _f(v) -> float:
    return float(v or 0)


def prev_period(today: dt.date | None = None) -> tuple[int, int]:
    """The month a return filed today reports on — the one that just ended."""
    today = today or timezone.localdate()
    first = today.replace(day=1)
    last_prev = first - dt.timedelta(days=1)
    return last_prev.year, last_prev.month


def team_profiles(manager, raiser_user=None):
    """Active direct reports of `manager`.

    LINE reports only — this drives the return's own facts table and the
    "do I have a team at all" check. People the manager merely CO-REVIEWS are
    not their team for hours/leave purposes; see co_review_profiles().
    """
    from hris.models import HRISProfile
    from payroll.models import Employee
    # CFO 2026-07-26: a flagged person does NOT drop off the roster. They stay
    # visible, marked as waiting on Unami's decision. `raiser_user` is accepted so
    # callers can pass it, but it deliberately no longer filters anything.
    return (HRISProfile.objects
            .filter(manager=manager)
            .exclude(employee=None)
            .exclude(employee__status=Employee.Status.TERMINATED)
            .select_related('employee')
            .order_by('employee__full_name'))


def co_review_profiles(manager):
    """People this manager CO-REVIEWS but does not line-manage (CFO 2026-07-26).

    Kago asked why the senior accountants were missing from his team. They report
    to the CFO, so they are correctly absent from his LINE roster — but he works
    with them daily, so he is now their co-reviewer and they appear here, clearly
    separated. Their hours and leave stay the line manager's business.
    """
    from hris.models import HRISProfile
    from payroll.models import Employee
    return (HRISProfile.objects
            .filter(co_manager=manager)
            .exclude(employee=None)
            .exclude(manager=manager)          # never list someone twice
            .exclude(employee__status=Employee.Status.TERMINATED)
            .select_related('employee', 'manager')
            .order_by('employee__full_name'))


def additional_review_profiles(manager):
    """People this manager reviews as an OPERATIONS reviewer (CFO 2026-08-12).

    Bharath runs operations and must be able to review + give feedback on people who
    line-report elsewhere (Finance, HR, IT, Claims). They are assigned via
    `AdditionalReviewer`. Their hours, leave and org chart stay with their real
    manager; this is feedback oversight only. Kept separate from the line roster and
    from the co-review blend.
    """
    from hris.models import HRISProfile
    from payroll.models import Employee
    return (HRISProfile.objects
            .filter(additional_reviewers__reviewer=manager)
            .exclude(employee=None)
            .exclude(manager=manager)          # if they later line-manage them,
            .exclude(co_manager=manager)       # that relationship wins — never both
            .exclude(employee__status=Employee.Status.TERMINATED)
            .select_related('employee', 'manager', 'co_manager')
            .distinct()
            .order_by('employee__full_name'))


def _hours_row(emp, year: int, month: int) -> dict:
    """Required vs tracked hours + the unexplained-day count for one person.

    `unexplained_days` is the CFO's "not putting leave and not explaining lost
    hours" — a shortfall day that is neither covered by approved leave (those
    auto-justify) nor carries a manager-approved explanation.
    """
    from hris.models import WorkdayJustification

    first, last, _s, _n = month_bounds(year, month)
    wj = WorkdayJustification.objects.filter(
        profile__employee=emp, work_date__gte=first, work_date__lte=last)
    agg = wj.aggregate(req=Sum('required_hours'), got=Sum('tracked_hours'))
    required, tracked = _f(agg['req']), _f(agg['got'])
    unexplained = wj.filter(status=WorkdayJustification.Status.UNJUSTIFIED).count()
    # PENDING = asked, never answered. That is still "not explaining".
    unanswered = wj.filter(status=WorkdayJustification.Status.PENDING).count()
    return {
        'required_hours': round(required, 1),
        'tracked_hours': round(tracked, 1),
        'shortfall_hours': round(max(0.0, required - tracked), 1),
        'hours_pct': round(100 * tracked / required) if required else None,
        'unexplained_days': unexplained,
        'unanswered_days': unanswered,
    }


def build_team_pack(manager, year: int, month: int, raiser_user=None) -> dict:
    """The facts half: one row per team member + the capacity signal."""
    from hris.roster_flag_models import open_flags_for
    rows = []
    total_required = total_tracked = 0.0
    profiles = list(team_profiles(manager, raiser_user=raiser_user))
    flags = open_flags_for(raiser_user, [p.id for p in profiles])
    for prof in profiles:
        emp = prof.employee
        panel = employee_month_panel(emp, year, month)
        hours = _hours_row(emp, year, month)
        total_required += hours['required_hours']
        total_tracked += hours['tracked_hours']
        rows.append({
            'employee_id': str(emp.id),
            'name': emp.full_name,
            'job_title': emp.job_title or '',
            **hours,
            'leave_days': panel['leave_days'],
            'sick_days': panel['sick_days'],
            'tasks_assigned': panel['tasks_assigned'],
            'tasks_completed': panel['tasks_completed'],
            'tasks_on_time_pct': panel['tasks_on_time_pct'],
            # "I've flagged this person" marker — they STAY on the roster.
            'flag': flags.get(str(prof.id)),
        })

    # Capacity signal: if the team delivered N people's worth of hours against a
    # headcount of M, the gap is the overstaffing question answered in numbers
    # instead of asked as a yes/no. Guard against a zero-required month (nobody
    # tracked, or a brand-new team) — a 0/0 must read "unknown", never "0 people".
    per_head = (total_required / len(rows)) if rows else 0.0
    headcount_equiv = round(total_tracked / per_head, 2) if per_head else None

    return {
        'rows': rows,
        'headcount': len(rows),
        'total_required_hours': round(total_required, 1),
        'total_tracked_hours': round(total_tracked, 1),
        'headcount_equivalent': headcount_equiv,
        # The people the manager must account for in `leave_action`.
        'offenders': [r['name'] for r in rows
                      if r['unexplained_days'] or r['unanswered_days']],
    }


def build_own_pack(manager, year: int, month: int) -> dict:
    """The manager's OWN month — the CFO asked for their own hours too."""
    panel = employee_month_panel(manager, year, month)
    return {'name': manager.full_name, **_hours_row(manager, year, month),
            'tasks_assigned': panel['tasks_assigned'],
            'tasks_completed': panel['tasks_completed'],
            'tasks_on_time_pct': panel['tasks_on_time_pct']}


def _prior_commitment(manager, year: int, month: int) -> str:
    """Last month's promise, so this month can ask what happened to it."""
    y, m = (year, month - 1) if month > 1 else (year - 1, 12)
    prev = ManagerMonthlyReturn.objects.filter(
        manager=manager, period_year=y, period_month=m).first()
    if not prev:
        return ''
    return (prev.next_month_commitment or prev.fy27_actions or '').strip()


@transaction.atomic
def get_or_create_draft(manager, year: int, month: int) -> ManagerMonthlyReturn:
    """The manager's return for the period, creating an empty draft if needed.

    The role-specific question set is decided and stored HERE, at draft time —
    not at submit. Two reasons: the completeness gate needs to know which extra
    questions were shown before it can insist they are answered, and Aria is
    then called once per manager per month rather than on every page load.
    """
    ret = ManagerMonthlyReturn.objects.filter(
        manager=manager, period_year=year, period_month=month).first()
    if ret:
        if not ret.question_spec and not ret.is_locked:
            # Row predates the role-specific questions — backfill without AI so
            # an existing draft never stalls on a slow model call.
            ret.question_spec = spec_for(manager, use_ai=False)
            ret.save(update_fields=['question_spec', 'updated_at'])
        return ret
    return ManagerMonthlyReturn.objects.create(
        manager=manager, period_year=year, period_month=month,
        status=ReturnStatus.DRAFT,
        question_spec=spec_for(manager, use_ai=True),
        prior_commitments=_prior_commitment(manager, year, month))


EDITABLE_BY_FILER = (
    'leave_action', 'sla_breaches', 'sla_explanation', 'sales_target_met',
    'new_sales_amount', 'work_finished_on_time', 'work_on_time_comment',
    'dashboard_cleared_on_time', 'tasks_comment', 'overstaffed',
    'overstaffed_comment', 'fy27_aligned', 'fy27_actions', 'innovation',
    'prior_outcome', 'next_month_commitment',
)


def jd_text_for(emp) -> str:
    """What this manager's job actually is, for the question generator.

    Three sources, best first (CFO 2026-07-26):
      1. a real job description, once HR (Dorothy) has uploaded them;
      2. failing that, the person's Development Dialogue — the CFO's point is
         that a DD and a JD say almost the same thing, so a DD on file is a
         perfectly good basis for asking role-specific questions;
      3. nothing — the curated department set still stands on its own.
    Never raises: a missing source just means less context, not a broken form.
    """
    for attr in ('job_description', 'jd_text'):
        val = getattr(emp, attr, '') or ''
        if val:
            return str(val)
    try:
        from hris.models import HRISProfile
        prof = HRISProfile.objects.filter(employee=emp).first()
        for attr in ('job_description', 'jd_text', 'key_responsibilities'):
            val = (getattr(prof, attr, '') or '') if prof else ''
            if val:
                return str(val)
    except Exception:
        pass
    return _jd_from_dialogue(emp)


# Free-text DD fields worth reading as a stand-in JD. Ratings and box-grid
# coordinates say nothing about the JOB, so they are deliberately not used.
_DD_TEXT_KEYS = (
    'key_responsibilities', 'responsibilities', 'role_summary', 'purpose',
    'objectives', 'kpis', 'key_result_areas', 'kras', 'competencies',
    'development_areas', 'strengths', 'goals',
)


def _jd_from_dialogue(emp) -> str:
    """Assemble a JD-ish summary from the person's Development Dialogue."""
    try:
        from hris.talent_cockpit_models import DevelopmentDialogue
    except Exception:
        return ''
    try:
        dd = (DevelopmentDialogue.objects
              .filter(employee=emp)
              .order_by('-is_current', '-created_at')
              .first())
        if dd is None and getattr(emp, 'email', ''):
            dd = (DevelopmentDialogue.objects
                  .filter(email__iexact=emp.email)
                  .order_by('-is_current', '-created_at')
                  .first())
        if dd is None:
            return ''
        parts = []
        if dd.position:
            parts.append(f'Position: {dd.position}')
        if dd.department:
            parts.append(f'Department: {dd.department}')
        payload = dd.payload if isinstance(dd.payload, dict) else {}
        for key in _DD_TEXT_KEYS:
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                parts.append(f'{key.replace("_", " ").title()}: {val.strip()}')
            elif isinstance(val, list):
                flat = [str(v).strip() for v in val
                        if isinstance(v, (str, int, float)) and str(v).strip()]
                if flat:
                    parts.append(f'{key.replace("_", " ").title()}: ' + '; '.join(flat))
        text = '\n'.join(parts).strip()
        return f'(from Development Dialogue, no JD on file)\n{text}' if text else ''
    except Exception:
        return ''


def spec_for(emp, use_ai: bool = True) -> dict:
    """The role-specific question set for this manager."""
    from hris.manager_return_questions import question_set
    return question_set(
        job_title=getattr(emp, 'job_title', '') or '',
        department=getattr(emp, 'department', '') or '',
        jd_text=jd_text_for(emp),
        use_ai=use_ai)


def save_draft(ret: ManagerMonthlyReturn, data: dict,
               extra_answers: dict | None = None,
               allowed_extra_keys: set[str] | None = None) -> ManagerMonthlyReturn:
    """Apply the filer's answers. Whitelisted fields only — the facts half and
    the workflow fields are never writable from the form.

    `extra_answers` are the department / Aria answers; only keys that were
    actually SHOWN to this manager are stored, so a crafted request cannot
    stuff arbitrary JSON onto the record.
    """
    if ret.is_locked:
        raise ValidationError('This return is cleared and locked — it cannot be changed.')
    if ret.status not in (ReturnStatus.DRAFT, ReturnStatus.RETURNED):
        raise ValidationError('This return has been submitted — it cannot be edited.')
    for field in EDITABLE_BY_FILER:
        if field in data:
            setattr(ret, field, data[field])
    if extra_answers:
        allowed = allowed_extra_keys or set()
        merged = dict(ret.dept_answers or {})
        for k, v in extra_answers.items():
            if k in allowed:
                merged[k] = v
        ret.dept_answers = merged
    ret.clean()          # field-level sanity (negative sales, silly breach counts)
    ret.save()
    return ret


def resolve_reviewer(manager):
    """The filer's OWN manager — who the return goes up to."""
    from hris.models import HRISProfile
    prof = HRISProfile.objects.filter(employee=manager).select_related('manager').first()
    return prof.manager if prof else None


@transaction.atomic
def submit(ret: ManagerMonthlyReturn) -> ManagerMonthlyReturn:
    """Freeze the facts, resolve the reviewer, hand it up."""
    if ret.is_locked or ret.status == ReturnStatus.CLEARED:
        raise ValidationError('This return is already cleared.')
    if ret.status == ReturnStatus.SUBMITTED:
        raise ValidationError('This return has already been submitted.')
    gaps = ret.missing_for_submit()
    if gaps:
        raise ValidationError({'missing': gaps})

    pack = build_team_pack(ret.manager, ret.period_year, ret.period_month)
    ret.team_snapshot = pack['rows']
    ret.headcount_equivalent = (
        Decimal(str(pack['headcount_equivalent']))
        if pack['headcount_equivalent'] is not None else None)
    ret.own_snapshot = build_own_pack(ret.manager, ret.period_year, ret.period_month)
    ret.submitted_to = resolve_reviewer(ret.manager)
    ret.submitted_at = timezone.now()
    ret.status = ReturnStatus.SUBMITTED
    ret.save()
    # Stop the daily nag NOW, not on tomorrow's sweep. The dashboard task that
    # tracks this return is the thing being chased; filing the return is what
    # finishes it (9-Sep-2026 — nothing used to close it, so managers who filed
    # on time were emailed a reminder every morning for ever).
    from hris.manager_return_task_close import close_if_complete
    close_if_complete(ret.manager, ret.period_year, ret.period_month)
    return ret


@transaction.atomic
def clear(ret: ManagerMonthlyReturn, reviewer_user, verdict: str, notes: str = '') -> ManagerMonthlyReturn:
    """The filer's manager signs it off. Locks the record."""
    if ret.status != ReturnStatus.SUBMITTED:
        raise ValidationError('Only a submitted return can be cleared.')
    if verdict not in ReviewVerdict.values:
        raise ValidationError({'verdict': 'Choose On track, Watch or Intervene.'})
    # Separation of duties: nobody clears their own return, superuser included.
    filer_user = getattr(ret.manager, 'user', None)
    if filer_user and reviewer_user and filer_user.id == reviewer_user.id:
        raise ValidationError('You cannot clear your own return.')
    if verdict in (ReviewVerdict.WATCH, ReviewVerdict.INTERVENE) and not (notes or '').strip():
        raise ValidationError({'notes': 'Say why — a Watch or Intervene verdict needs a reason.'})
    ret.reviewer_verdict = verdict
    ret.reviewer_notes = (notes or '').strip()
    ret.cleared_by = reviewer_user
    ret.cleared_at = timezone.now()
    ret.status = ReturnStatus.CLEARED
    ret.is_locked = True
    ret.save()
    return ret


@transaction.atomic
def send_back(ret: ManagerMonthlyReturn, reviewer_user, notes: str) -> ManagerMonthlyReturn:
    """Reviewer wants more detail — reopens it for the filer."""
    if ret.status != ReturnStatus.SUBMITTED:
        raise ValidationError('Only a submitted return can be sent back.')
    if not (notes or '').strip():
        raise ValidationError({'notes': 'Say what is missing.'})
    ret.status = ReturnStatus.RETURNED
    ret.reviewer_notes = notes.strip()
    ret.submitted_at = None
    ret.save()
    # The work is back on the filer's desk, so give them their task back —
    # submit() closed it. Nothing else would re-raise it: manager_return_cycle
    # skips any manager who already HAS a task for the period, whatever state
    # it is in.
    from hris.manager_return_task_close import reopen_if_outstanding
    reopen_if_outstanding(ret.manager, ret.period_year, ret.period_month)
    return ret
