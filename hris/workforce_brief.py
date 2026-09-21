"""
hris/workforce_brief.py

Assembles each employee's Daily Brief and renders it as house-style HTML.

The brief shows, for one employee as of a given day:
  - Time Doctor hours tracked vs the required hours (6.5 wk / 3 Sat)
  - leave taken (this month, approved) + pending leave
  - pending tasks
  - active company announcements / HR matters / meetings for them
  - if short of required hours: the shortfall + the justification prompt

Aggregates only — no raw Time Doctor window/app titles (AD-POL-AI-GOV-001).
Tracked hours are passed in by the caller (the send_daily_brief command sources
them from the latest Time Doctor snapshot); the assembly stays DB-light and
testable.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Optional

from django.utils import timezone

from hris import workforce
from hris.leave_balance import days_out
from hris.models import Announcement, LeaveRequest, PublicHoliday

ZERO = Decimal('0.00')

NAVY, ORANGE, INK, MUT, RED, GREEN = '#0D1B2A', '#F4A623', '#1F2937', '#6B7280', '#DC2626', '#059669'


def holiday_off_dates(country_code: str = 'BW') -> set:
    """Active public holidays on which staff do NOT work (not a working day)."""
    return set(
        PublicHoliday.objects
        .filter(country_code=country_code, is_active=True, is_working_day=False)
        .values_list('holiday_date', flat=True)
    )


def is_holiday_off(d: 'datetime.date | None' = None, country_code: str = 'BW') -> bool:
    """True when `d` (default: today in the project timezone) is a public
    holiday on which staff do NOT work — i.e. a day to stay QUIET with staff
    reminders. Shared by the reminder crons so they pause on public holidays and
    resume, catching up, on the next working day (CFO directive 2026-07-19). The
    workforce briefs already skip these days via holiday_off_dates(); this gives
    the other reminder crons the same single source of truth."""
    try:
        from django.utils import timezone
        if d is None:
            d = timezone.localdate()
        return d in holiday_off_dates(country_code)
    except Exception:  # noqa: BLE001 — fail SAFE: on any error do NOT suppress reminders
        return False


def is_first_working_day_after_holiday(d: 'datetime.date | None' = None,
                                       country_code: str = 'BW') -> bool:
    """True when `d` (default today) is a WORKING day whose immediately preceding
    run of days-off included at least one public holiday — i.e. the first day back
    after a holiday break. Drives the ONE 'welcome back' catch-up instead of a burst
    of separate reminders (CFO directive 2026-07-19). A plain Monday after an
    ordinary weekend returns False (no holiday in the break)."""
    try:
        from django.utils import timezone
        if d is None:
            d = timezone.localdate()
        off = holiday_off_dates(country_code)
    except Exception:  # noqa: BLE001 — fail SAFE: never crash the reminder cron
        return False

    def _is_off(day):
        # Only SUNDAY is the weekly off-day — Alpha staff work Saturdays (3h,
        # hris.workforce.base_required_hours). So the Saturday after a Friday
        # holiday IS the first day back (M2, Fable review 2026-07-19).
        return day.weekday() == 6 or day in off      # Sunday, or a non-working holiday

    if _is_off(d):
        return False                                 # today itself is not a working day
    had_holiday = False
    probe = d - datetime.timedelta(days=1)
    for _ in range(7):                               # walk back over the contiguous break
        if not _is_off(probe):
            break                                    # reached the last working day before d
        if probe in off:
            had_holiday = True
        probe -= datetime.timedelta(days=1)
    return had_holiday


def _month_bounds(d: datetime.date):
    first = d.replace(day=1)
    return first, d


def leave_summary(profile, as_of: datetime.date) -> dict:
    """Approved leave taken this month + pending, PLUS the employee's real
    ANNUAL leave balance as at today (CFO 2026-07-14: 'pull the actual leave as
    at the date and send it to them so they are clear about their leave days').
    Uses the one canonical balance engine so the brief matches the self-service
    balances page exactly (annual_available = bookable now, accrued − used)."""
    first, _ = _month_bounds(as_of)
    taken = pending = ZERO
    pending_count = 0
    try:
        # Count approved leave by the month it STARTED in, so leave spanning a
        # month boundary is not double-counted in both months (Fable review).
        rows = LeaveRequest.objects.filter(
            profile=profile, status=LeaveRequest.Status.APPROVED,
            start_date__gte=first, start_date__lte=as_of,
        )
        for r in rows:
            taken += (r.days or ZERO)
        for r in LeaveRequest.objects.filter(profile=profile,
                                              status=LeaveRequest.Status.PENDING):
            pending += (r.days or ZERO)
            pending_count += 1
    except Exception:    # noqa: BLE001 — brief must never crash on a data edge
        pass

    annual_available = annual_entitlement = annual_used = None
    try:
        from hris.leave_balance import balances_for_profile
        for b in balances_for_profile(profile):
            if b.get('code') == 'annual':
                annual_available = days_out(float(b.get('available') or 0))
                annual_entitlement = days_out(float(b.get('days') or 0))
                annual_used = days_out(float(b.get('used') or 0))
                break
    except Exception:    # noqa: BLE001
        pass

    return {'taken_month': taken, 'pending_days': pending, 'pending_count': pending_count,
            'annual_available': annual_available, 'annual_entitlement': annual_entitlement,
            'annual_used': annual_used}


def pending_tasks(profile) -> list:
    """Open OmniTasks assigned to this employee's user (best-effort match by
    email). Returns a list of {title, priority}."""
    out = []
    try:
        from django.contrib.auth.models import User
        from core.models import OmniTask
        email = (getattr(profile.employee, 'email', '') or '').strip().lower()
        if not email:
            return out
        user = User.objects.filter(email__iexact=email).first()
        if not user:
            return out
        qs = (OmniTask.objects
              .filter(assignee=user,
                      status__in=[OmniTask.Status.PENDING, OmniTask.Status.IN_PROGRESS])
              .order_by('-priority', 'created_at')[:10])
        out = [{'title': t.title, 'priority': t.get_priority_display()} for t in qs]
    except Exception:    # noqa: BLE001
        pass
    return out


def outstanding_authorities_brief(profile) -> list:
    """Recruit/regrade authorities awaiting THIS person's signature, folded into
    the morning brief so there is no separate 'needs your signature' email to the
    CFO (CFO 2026-08-12). Matched by the person's email against EACH authority's
    own signatory chain (tier-driven since 2026-09-02, so the Finance Manager and
    hiring managers are covered too); empty for everyone else, so nearly every
    brief is unchanged. Best-effort — any error returns [] and never breaks the brief."""
    out = []
    try:
        from recruitment.models import AuthorityToRecruit
        email = (getattr(profile.employee, 'email', '') or '').strip().lower()
        if not email:
            return out
        pending = (AuthorityToRecruit.objects
                   .filter(status=AuthorityToRecruit.Status.PENDING)
                   .select_related('tier')
                   .order_by('-created_at')[:25])
        # Resolve this signatory's user so each row can carry a login-free
        # Approve/Decline link (CFO 2026-08-18) — the token binds to this user.
        from django.contrib.auth.models import User
        from recruitment.authority_actions import action_url
        user = User.objects.filter(email__iexact=email, is_active=True).first()
        for a in pending:
            # The person's slot depends on THIS authority's chain, not a fixed
            # five — a tiered paper puts the FM / hiring manager on the chain.
            slug = next((s for s, _l, addr in a.signatory_chain()
                         if addr and addr == email), None)
            if not slug or slug not in a.outstanding_signatories():
                continue
            row = {'reference': a.reference, 'person_name': a.person_name,
                   'position': a.position, 'action_url': ''}
            if user is not None:
                try:
                    row['action_url'] = action_url(a, user)
                except Exception:    # noqa: BLE001
                    pass
            out.append(row)
    except Exception:    # noqa: BLE001
        pass
    return out


def announcements_for(profile, as_of: datetime.date) -> list:
    """Active announcements visible to this employee as of `as_of`."""
    out = []
    try:
        from django.db.models import Q
        qs = Announcement.objects.filter(is_active=True, starts_on__lte=as_of).filter(
            Q(audience__isnull=True) | Q(audience=profile)
        ).filter(Q(ends_on__isnull=True, starts_on=as_of) | Q(ends_on__gte=as_of))
        out = [{'category': a.get_category_display(), 'title': a.title, 'body': a.body}
               for a in qs.order_by('category', '-starts_on')]
    except Exception:    # noqa: BLE001
        pass
    return out


def build_employee_brief(profile, as_of: datetime.date, tracked_hours: Decimal,
                         off_dates: Optional[set] = None,
                         justified_hours: Optional[Decimal] = None) -> dict:
    """Assemble one employee's brief dict for `as_of`. `justified_hours` is any
    shortfall already justified up front (e.g. approved leave covering the day)."""
    if off_dates is None:
        off_dates = holiday_off_dates()
    from hris.workforce_roles import is_manager_hours_profile
    is_manager = is_manager_hours_profile(profile)
    required = workforce.required_hours_for_date(as_of, off_dates, is_manager=is_manager)
    if tracked_hours is None:
        # No Time Doctor data for this day yet — do NOT flag a shortfall.
        tracked, status, needs, shortfall = ZERO, 'no_data', False, ZERO
    else:
        tracked   = Decimal(tracked_hours or 0)
        status    = workforce.classify_day(required, tracked, Decimal(justified_hours or 0))
        needs     = status == 'unjustified'
        shortfall = workforce.shortfall_hours(required, tracked)
    return {
        'as_of':        as_of.isoformat(),
        'name':         getattr(profile.employee, 'full_name', ''),
        'required':     required,
        'tracked':      tracked,
        'shortfall':    shortfall,
        'status':       status,               # met | not_required | justified | unjustified | no_data
        'is_manager':   is_manager,            # on the 4.5h manager weekday rate?
        'needs_justification': needs,
        'leave':        leave_summary(profile, as_of),
        'tasks':        pending_tasks(profile),
        'approvals':    _approvals_for(profile),
        'announcements': announcements_for(profile, as_of),
    }


def _approvals_for(profile) -> list:
    """Approval items waiting on this person, folded into the ONE Daily Brief so
    there is no separate 'approvals waiting on you' email (CFO 2026-07-22).
    Empty for non-approvers, so the vast majority of briefs are unchanged."""
    try:
        from core.approvals_views import pending_approvals_for
        emp = getattr(profile, 'employee', None)
        user = getattr(emp, 'user', None)
        if user is None:
            return []
        return pending_approvals_for(user) or []
    except Exception:  # noqa: BLE001
        return []


def build_brief_html(brief: dict) -> str:
    """Render the brief dict as house-style HTML for the daily email."""
    from django.utils.html import escape
    def h(v):
        # Values are user-derived (names, task titles, announcement bodies) —
        # escape everything interpolated into the HTML.
        return '—' if v is None else escape(str(v))
    req, trk = brief['required'], brief['tracked']
    st = brief['status']
    badge = {'met': (GREEN, 'On target'),
             'not_required': (MUT, 'Off day'),
             'justified': (ORANGE, 'Justified'),
             'no_data': (MUT, 'Awaiting Time Doctor data'),
             'unjustified': (RED, 'Below target — please justify')}.get(st, (MUT, h(st)))
    lv = brief['leave']
    task_rows = ''.join(
        f'<li style="margin:2px 0">{h(t["title"])} <span style="color:{MUT};font-size:12px">({h(t["priority"])})</span></li>'
        for t in brief['tasks']
    ) or f'<li style="color:{MUT}">No pending tasks.</li>'
    # Approvals waiting on this person — folded in so there is no separate
    # "approvals waiting on you" email (CFO 2026-07-22). Empty for non-approvers.
    appr_rows = ''.join(
        f'<li style="margin:2px 0">{h(a.get("label"))} — <b>{h(a.get("count"))}</b>'
        + (f' <span style="color:{MUT};font-size:12px">(oldest {h(a.get("oldest_days"))}d)</span>'
           if a.get('oldest_days') else '')
        + '</li>'
        for a in (brief.get('approvals') or [])
    )
    approvals_block = (
        f'<h3 style="color:{NAVY};font-size:14px;margin:18px 0 6px">Waiting for your approval</h3>'
        f'<ul style="margin:0;padding-left:20px;color:{INK};font-size:13px">{appr_rows}</ul>'
        f'<div style="margin:6px 0 0"><a href="https://omni.alphadirect.co.bw/my-approvals" '
        f'style="color:{ORANGE};font-size:12px;font-weight:600;text-decoration:none">Open My Approvals →</a></div>'
    ) if appr_rows else ''
    ann_rows = ''.join(
        f'<div style="margin:8px 0;padding:8px 12px;background:#F8F9FB;border-left:3px solid {ORANGE};border-radius:4px">'
        f'<div style="font-size:11px;text-transform:uppercase;color:{MUT}">{h(a["category"])}</div>'
        f'<div style="font-weight:600;color:{INK}">{h(a["title"])}</div>'
        f'<div style="color:{INK};font-size:13px">{h(a["body"])}</div></div>'
        for a in brief['announcements']
    )
    justify_block = ''
    if brief['needs_justification']:
        from django.conf import settings
        base = (getattr(settings, 'PUBLIC_BASE_URL', '') or 'https://omni.alphadirect.co.bw').rstrip('/')
        justify_block = (
            f'<div style="margin:14px 0;padding:12px 16px;background:#FEF2F2;border:1px solid {RED};border-radius:8px">'
            f'<div style="font-weight:700;color:{RED}">Action needed</div>'
            f'<div style="color:{INK};font-size:13px;margin-top:4px">You are <b>{h(brief["shortfall"])}h</b> under the '
            f'required <b>{h(req)}h</b> for {h(brief["as_of"])}. Please explain why — were you on leave, in an '
            f'external meeting (max 1.5h), or on a client visit?</div>'
            f'<a href="{base}/hris/my-brief" style="display:inline-block;margin-top:10px;padding:8px 16px;'
            f'background:{NAVY};color:{ORANGE};text-decoration:none;border-radius:6px;font-weight:600;font-size:13px">'
            f'Explain in Omni →</a></div>'
        )
    # Policy explainer — folded into the daily brief so one email covers the whole
    # rule (CFO 2026-07-22: no separate mass-mail). Wording flips at 1 Sep 2026.
    from hris import leave_accountability as _la
    tgt = '4.5' if brief.get('is_manager') else '6.5'   # manager weekday rate (CFO 2026-07-23)
    if _la.enforcement_active(timezone.localdate()):
        policy_block = (
            f'<div style="margin:14px 0;padding:12px 16px;background:#FFF7E6;border:1px solid {ORANGE};border-radius:8px">'
            f'<div style="font-weight:700;color:{INK}">How this works</div>'
            f'<div style="color:{INK};font-size:13px;margin-top:4px">Omni tracks your productive hours each day — '
            f'weekday target <b>{tgt}h</b>, Saturday <b>3h</b>. A day you are short with no accepted reason is applied as '
            f'leave. If you disagree, you can <b>appeal</b> it for review. Explain a short day under '
            f'<b>My Daily Brief</b>.</div></div>'
        )
    else:
        policy_block = (
            f'<div style="margin:14px 0;padding:12px 16px;background:#FFF7E6;border:1px solid {ORANGE};border-radius:8px">'
            f'<div style="font-weight:700;color:{INK}">How this works</div>'
            f'<div style="color:{INK};font-size:13px;margin-top:4px">Omni tracks your productive hours each day — '
            f'weekday target <b>{tgt}h</b>, Saturday <b>3h</b>. <b>Right now is a warm-up: nothing is deducted.</b> From '
            f'<b>1 September 2026</b>, a day you are short with no accepted reason is applied as leave — and you can '
            f'<b>appeal</b> it before it is finalised. If you miss a day, explain it under <b>My Daily Brief</b>.</div></div>'
        )
    tile = lambda label, val, color=NAVY: (
        f'<td style="padding:14px 16px;background:#F8F9FB;border:1px solid #EEF0F3;border-radius:10px">'
        f'<div style="font-size:11px;letter-spacing:.04em;text-transform:uppercase;color:{MUT}">{label}</div>'
        f'<div style="font-size:22px;font-weight:700;color:{color};margin-top:2px">{val}</div></td>'
    )
    return f"""<!doctype html><html><body style="margin:0;background:#EEF0F3;font-family:'Book Antiqua',Georgia,serif">
<div style="max-width:680px;margin:0 auto;background:#fff">
  <div style="background:{NAVY};padding:18px 24px">
    <div style="color:{ORANGE};font-size:20px;font-weight:700">Alpha Direct — Daily Brief</div>
    <div style="color:#AEB6C2;font-size:13px;margin-top:2px">{h(brief['name'])} · {h(brief['as_of'])}</div>
  </div>
  <div style="padding:20px 24px">
    <div style="display:inline-block;padding:4px 10px;border-radius:12px;background:{badge[0]};color:#fff;font-size:12px;font-weight:600">{badge[1]}</div>
    <table cellspacing="8" style="border-collapse:separate;width:100%;margin-top:12px"><tr>
      {tile('Hours tracked', h(trk))}
      {tile('Required', h(req))}
      {tile('Leave taken (mo)', h(lv['taken_month']))}
      {tile('Pending leave', f"{lv['pending_count']} req")}
    </tr></table>
    {policy_block}
    {justify_block}
    <h3 style="color:{NAVY};font-size:14px;margin:18px 0 6px">Pending tasks</h3>
    <ul style="margin:0;padding-left:20px;color:{INK};font-size:13px">{task_rows}</ul>
    {approvals_block}
    {('<h3 style="color:%s;font-size:14px;margin:18px 0 6px">Announcements &amp; HR</h3>%s' % (NAVY, ann_rows)) if ann_rows else ''}
    <p style="color:{MUT};font-size:11px;margin-top:18px">Hours from Time Doctor (aggregated — no activity detail).</p>
  </div>
</div></body></html>"""
