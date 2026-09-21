"""
hris/auto_feedback.py — the facts behind a month's feedback, and the draft.

CFO 2026-08-26: managers are not giving feedback because it is not compulsory.
So on the 5th every manager gets ONE email covering everyone who reports to
them, with the month's facts already written up. They confirm, they edit, or
they say nothing — and if they say nothing it posts as feedback from Omni.

"Don't consider this as a penalty." So this module states FACTS and never
judges: hours against requirement, late or short days the person never
explained, work still outstanding. It deliberately produces NO rating —
see NOT_RATED in performance_feedback_models. A machine that rates people
feeds PIPs and warnings off data errors, and this company has already had two
wrong escalations that way (#523).

Sources, all existing:
  * WorkdayJustification — required vs tracked hours per day, PLUS whether the
    person explained a shortfall and whether the manager accepted it. Using the
    justification record rather than raw Time Doctor is deliberate: someone who
    explained their day must not be written up for it.
  * overdue_gate.overdue_summary — the same long-overdue work that already caps
    a manager's rating.
"""
from __future__ import annotations

import calendar
import datetime as dt
import logging
from decimal import Decimal

from django.utils import timezone

log = logging.getLogger(__name__)

ZERO = Decimal('0')

# Four buckets, not two. Lumping these together is how a manager's decision gets
# quietly overruled — the first cut filed UNJUSTIFIED (the manager reviewed the
# excuse and REJECTED it) under "explained and accepted", and EXPLAINED (still
# awaiting review) as though it were already settled. Fable caught both.
# CFO 2026-08-27: a rejected excuse is stated plainly as not accepted — it is NOT
# treated the same as never answering, because someone who tried and was overruled
# is not someone who ignored the question.
UNEXPLAINED_STATUSES = ('pending',)        # asked, never answered
REJECTED_STATUSES    = ('unjustified',)    # answered, manager did not accept
AWAITING_STATUSES    = ('explained',)      # answered, manager has not looked yet
ACCEPTED_STATUSES    = ('justified', 'on_leave', 'external_meeting', 'client_visit')

# Below this, a shortfall is noise, not a pattern.
MIN_SHORTFALL_HOURS = Decimal('1.00')


def period_bounds(year: int, month: int) -> tuple[dt.date, dt.date]:
    last = calendar.monthrange(year, month)[1]
    return dt.date(year, month, 1), dt.date(year, month, last)


def facts_for(profile, year: int, month: int) -> dict:
    """Everything the month can say about one person, without an opinion."""
    from hris.models import WorkdayJustification

    start, end = period_bounds(year, month)
    days = list(WorkdayJustification.objects.filter(
        profile=profile, work_date__gte=start, work_date__lte=end))

    required = sum((d.required_hours or ZERO for d in days), ZERO)
    tracked = sum((d.tracked_hours or ZERO for d in days), ZERO)

    short_days = [d for d in days if d.shortfall >= MIN_SHORTFALL_HOURS]

    def _in(bucket):
        return [d for d in short_days if (d.status or '').lower() in bucket]

    unexplained = _in(UNEXPLAINED_STATUSES)
    rejected    = _in(REJECTED_STATUSES)
    awaiting    = _in(AWAITING_STATUSES)
    accepted    = _in(ACCEPTED_STATUSES)

    # The gap the person is actually answerable for. Net off ONLY hours on days in
    # the ACCEPTED bucket: workforce_views sets justified_hours at EXPLANATION
    # time, while the row still sits awaiting a manager's review, so summing over
    # every day credited un-reviewed hours and the narrative then called them
    # "accepted hours" — two lines below a sentence saying awaiting days conclude
    # nothing (Fable round 2, 2026-08-27).
    justified = sum((d.justified_hours or ZERO for d in accepted), ZERO)
    gross_gap = (required - tracked) if required > tracked else ZERO
    net_gap = gross_gap - justified
    if net_gap < ZERO:
        net_gap = ZERO

    overdue = _overdue(profile)
    off_window = _off_window_loads(profile, start, end)

    return {
        'period': f'{year}-{month:02d}',
        'days_on_record': len(days),
        'required_hours': required,
        'tracked_hours': tracked,
        'hours_gap': net_gap,                 # after accepted explanations
        'hours_gap_gross': gross_gap,
        'justified_hours': justified,
        'short_days': len(short_days),
        'short_days_unexplained': len(unexplained),
        'short_days_rejected': len(rejected),
        'short_days_awaiting': len(awaiting),
        'short_days_explained': len(accepted),
        'unexplained_dates': [d.work_date for d in unexplained][:12],
        'rejected_dates': [d.work_date for d in rejected][:12],
        'overdue_count': overdue.get('count', 0),
        'overdue_titles': overdue.get('titles', [])[:6],
        # Payments raised outside the old morning loading window (PAY-WIN-02
        # abolished 2026-09-02): no longer blocked, but a planning concern the
        # manager sees at feedback time.
        'off_window_loads': off_window.get('count', 0),
        'off_window_dates': off_window.get('dates', []),
        # Nothing here is a verdict. The manager supplies that, or nobody does.
        'has_anything_to_say': bool(unexplained or rejected or overdue.get('count')
                                    or off_window.get('count') or net_gap > ZERO),
    }


def _overdue(profile) -> dict:
    """Long-overdue work, via the same gate that caps a manager's rating."""
    try:
        from hris import overdue_gate
        user = getattr(getattr(profile, 'employee', None), 'user', None)
        if not user:
            return {'count': 0, 'titles': []}
        s = overdue_gate.overdue_summary(user) or {}
        items = s.get('items') or s.get('tasks') or []
        titles = []
        for it in items:
            t = it.get('title') if isinstance(it, dict) else getattr(it, 'title', '')
            if t:
                titles.append(str(t))
        return {'count': s.get('count', len(titles)), 'titles': titles}
    except Exception:                                            # noqa: BLE001
        # A missing gate must never stop the cycle — no facts is better than
        # a crashed month.
        return {'count': 0, 'titles': []}


def _off_window_loads(profile, start, end) -> dict:
    """Payments this person RAISED outside the old morning loading window in the
    month. PAY-WIN-02 was abolished 2026-09-02 — raising off-window is no longer
    blocked, but the count is a planning concern the manager sees at feedback
    time (CFO: 'a negative review point that they did not plan the load in time').
    Never a verdict; the manager still supplies the rating."""
    try:
        from taskboard.models import PaymentRequest
        user = getattr(getattr(profile, 'employee', None), 'user', None)
        if not user:
            return {'count': 0, 'dates': []}
        # One query: pull the timestamps, then count + dedupe dates in Python.
        stamps = list(PaymentRequest.objects.filter(
            created_by=user, loaded_off_window=True,
            created_at__date__gte=start, created_at__date__lte=end,
        ).values_list('created_at', flat=True))
        # Display in local time (Africa/Gaborone) to match the __date filter,
        # so a 00:00-02:00 raise on the 1st is not shown under last month's date.
        dates = sorted({timezone.localtime(s).date() for s in stamps})[:12]
        return {'count': len(stamps), 'dates': dates}
    except Exception:                                            # noqa: BLE001
        # Mirror _overdue: a bad lookup must never crash the monthly cycle — but
        # log the traceback, because a silent zero would hide a real off-window
        # record (it switches the CFO's control off, e.g. after a bad deploy).
        log.exception('off-window load count failed for profile %s',
                      getattr(profile, 'id', '?'))
        return {'count': 0, 'dates': []}


def draft_narrative(employee_name: str, facts: dict) -> str:
    """The month in plain sentences. Facts only — no rating, no adjectives."""
    lines: list[str] = []
    req, trk = facts['required_hours'], facts['tracked_hours']

    if facts['days_on_record']:
        if facts['hours_gap'] > ZERO:
            note = (f' after {facts["justified_hours"]:,.1f} accepted hours'
                    if facts.get('justified_hours') else '')
            lines.append(
                f'Tracked {trk:,.1f} hours against {req:,.1f} expected for the '
                f'month — {facts["hours_gap"]:,.1f} hours short{note}.')
        elif req > ZERO:
            lines.append(f'Tracked {trk:,.1f} hours against {req:,.1f} expected '
                         f'for the month, with no shortfall.')
    else:
        lines.append('No working-time record for the month.')

    if facts['short_days_unexplained']:
        dates = ', '.join(d.strftime('%d %b') for d in facts['unexplained_dates'])
        lines.append(
            f'{facts["short_days_unexplained"]} short day(s) were never explained '
            f'after being asked: {dates}.')
    if facts.get('short_days_rejected'):
        dates = ', '.join(d.strftime('%d %b') for d in facts['rejected_dates'])
        lines.append(
            f'{facts["short_days_rejected"]} short day(s) were explained, and the '
            f'explanation was not accepted by the manager: {dates}.')
    if facts.get('short_days_awaiting'):
        lines.append(f'{facts["short_days_awaiting"]} short day(s) were explained and '
                     f'are still awaiting a manager\'s review — nothing is concluded '
                     f'about these.')
    if facts['short_days_explained']:
        lines.append(f'{facts["short_days_explained"]} short day(s) were explained '
                     f'and accepted.')

    if facts['overdue_count']:
        titles = '; '.join(facts['overdue_titles'])
        lines.append(f'{facts["overdue_count"]} task(s) still outstanding past their '
                     f'due date: {titles}.')

    if facts.get('off_window_loads'):
        dts = ', '.join(d.strftime('%d %b') for d in facts.get('off_window_dates', []))
        lines.append(
            f'{facts["off_window_loads"]} payment(s) were loaded outside the morning '
            f'window{f" ({dts})" if dts else ""}. Payments can be raised at any time '
            f'now, so this is not a breach — but loading them in the morning is the '
            f'expected planning.')

    if not lines:
        lines.append('Nothing on record needing attention this month.')

    lines.append('')
    lines.append('These are the facts on record. No rating has been given — '
                 'a rating is the manager\'s judgement, not the system\'s.')
    return '\n'.join(lines)


def team_facts(manager_employee, year: int, month: int) -> list[dict]:
    """Facts for everyone reporting to this manager, worst first.

    Reuses the SAME reports resolution as the one-click page (line-managed and
    co-managed), so the email and the page can never disagree about who is on
    a manager's team.
    """
    from hris.manager_feedback_actions import _reports
    from hris.performance_feedback_models import MonthlyCheckIn

    out = []
    for profile in _reports(manager_employee):
        existing = MonthlyCheckIn.objects.filter(
            profile=profile, period_month=month, period_year=year).first()
        f = facts_for(profile, year, month)
        out.append({
            'profile': profile,
            'name': profile.employee.full_name,
            'facts': f,
            'draft': draft_narrative(profile.employee.full_name, f),
            'already_done': existing is not None,
            'checkin': existing,
        })
    out.sort(key=lambda r: (r['already_done'],
                            -r['facts']['overdue_count'],
                            -r['facts']['short_days_unexplained']))
    return out
