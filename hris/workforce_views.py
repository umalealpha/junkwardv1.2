"""
hris/workforce_views.py

On/off switch for the Workforce Daily Brief, shown as a button on the omni
Time Doctor page. Read is allowed to any authenticated user (so the page can
render current state); TOGGLING is restricted server-side to the CFO, Arun and
Arjun (settings.WORKFORCE_BRIEF_ADMINS) — the button is a convenience, this
gate is the real control.

  GET  /api/v1/timedoctor/brief-toggle/   → {enabled, can_toggle}
  POST /api/v1/timedoctor/brief-toggle/   body {enabled: bool} → {enabled, can_toggle}
"""
from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from hris.models import WorkforceBriefSetting

DEFAULT_BRIEF_ADMINS = [
    'pganesharajah@alphadirect.co.bw',   # CFO
    'aiyer@alphadirect.co.bw',           # Arun
    'arjuniyer@alphadirect.co.bw',       # Arjun
]


def _can_toggle(user) -> bool:
    if getattr(user, 'is_superuser', False):
        return True
    admins = {a.strip().lower()
              for a in getattr(settings, 'WORKFORCE_BRIEF_ADMINS', DEFAULT_BRIEF_ADMINS) if a}
    return (getattr(user, 'email', '') or '').strip().lower() in admins


def _can_link(user) -> bool:
    """Who may look at the who-tracks panel and confirm a Time Doctor ↔ staff
    link: the switch admins above, PLUS the HR team (HRIS tier — Unami, Dorothy,
    Thapelo). CFO 2026-09-05: HR must be able to re-link a person who moved
    entity without waiting for the CFO. The track / don't-track switch and the
    daily-brief switch stay with the CFO, Arun and Arjun."""
    if _can_toggle(user):
        return True
    try:
        from core.hris_access import hris_role
        return hris_role(user) in {'hris', 'admin', 'superadmin'}
    except Exception:          # noqa: BLE001
        return False


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def workforce_brief_toggle(request):
    can = _can_toggle(request.user)
    if request.method == 'GET':
        return Response({'enabled': WorkforceBriefSetting.is_enabled(), 'can_toggle': can})

    if not can:
        return Response({'detail': 'Only the CFO, Arun or Arjun can switch the daily brief.'},
                        status=status.HTTP_403_FORBIDDEN)
    body = request.data if isinstance(request.data, dict) else {}
    obj = WorkforceBriefSetting.solo()
    obj.enabled = bool(body.get('enabled'))
    obj.updated_by = request.user
    obj.save(update_fields=['enabled', 'updated_by', 'updated_at'])
    return Response({'enabled': obj.enabled, 'can_toggle': True})


# ---------------------------------------------------------------------------
# Employee self-service: the days I need to justify + submitting a reason.
# Each employee only ever sees / edits their OWN days (resolved by email).
# ---------------------------------------------------------------------------

def _my_profile(user):
    from hris.models import HRISProfile
    from payroll.models import Employee
    email = (getattr(user, 'email', '') or '').strip()
    if not email:
        return None
    # HRISProfile has no is_active flag — active = not terminated in payroll.
    return (HRISProfile.objects.select_related('employee')
            .filter(employee__email__iexact=email)
            .exclude(employee__status=Employee.Status.TERMINATED).first())


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_brief(request):
    """Days in the last 14 that the logged-in employee is short on and hasn't
    justified yet — powers the pop-up."""
    import datetime
    from django.utils import timezone as _tz
    from hris.models import WorkdayJustification
    prof = _my_profile(request.user)
    if prof is None:
        return Response({'employee': None, 'days': []})
    since = _tz.localtime().date() - datetime.timedelta(days=14)
    rows = (WorkdayJustification.objects
            .filter(profile=prof, work_date__gte=since,
                    status=WorkdayJustification.Status.UNJUSTIFIED,
                    responded_at__isnull=True)   # don't re-nag a day they already explained
            .order_by('-work_date'))
    days = [{
        'work_date': r.work_date.isoformat(),
        'required':  str(r.required_hours),
        'tracked':   str(r.tracked_hours),
        'shortfall': str(r.shortfall),
    } for r in rows]
    return Response({'employee': getattr(prof.employee, 'full_name', ''), 'days': days})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def justify_day(request):
    """Employee submits a reason for a short day. Validates the 1.5h meeting cap
    and records a ClientVisit when it's a client meeting.

    Control model (Fable review 2026-07-14 — a self-report is not proof):
      - "On leave" requires an APPROVED leave request covering the day; it then
        auto-justifies (the leave system is the evidence). Pending leave is not
        accepted — apply / get it approved first.
      - Meeting / client visit / other explanations park the day as
        EXPLAINED — pending manager review; a manager approves or rejects via
        review_justification. Nothing self-clears any more.
    """
    import datetime
    from decimal import Decimal, InvalidOperation
    from django.utils import timezone as _tz
    from hris import workforce
    from hris.models import WorkdayJustification, ClientVisit

    prof = _my_profile(request.user)
    if prof is None:
        return Response({'detail': 'No employee record is linked to your login.'},
                        status=status.HTTP_404_NOT_FOUND)
    body = request.data if isinstance(request.data, dict) else {}
    try:
        work_date = datetime.datetime.strptime((body.get('work_date') or '').strip(), '%Y-%m-%d').date()
    except ValueError:
        return Response({'detail': 'work_date must be YYYY-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST)

    row = WorkdayJustification.objects.filter(profile=prof, work_date=work_date).first()
    if row is None:
        return Response({'detail': 'No recorded short day for that date.'}, status=status.HTTP_404_NOT_FOUND)

    reason = (body.get('reason') or '').strip()
    valid = {WorkdayJustification.Reason.ON_LEAVE, WorkdayJustification.Reason.EXTERNAL_MEETING,
             WorkdayJustification.Reason.CLIENT_VISIT, WorkdayJustification.Reason.OTHER}
    if reason not in valid:
        return Response({'detail': 'Choose a valid reason.'}, status=status.HTTP_400_BAD_REQUEST)

    justification = (body.get('justification') or '').strip()
    meeting_minutes = body.get('meeting_minutes')

    if reason == WorkdayJustification.Reason.EXTERNAL_MEETING:
        if not workforce.meeting_minutes_valid(meeting_minutes):
            return Response({'detail': 'Meeting length must be between 1 and 90 minutes (max 1.5h).'},
                            status=status.HTTP_400_BAD_REQUEST)
    if reason in {WorkdayJustification.Reason.OTHER, WorkdayJustification.Reason.EXTERNAL_MEETING} and not justification:
        return Response({'detail': 'Please add a short explanation.'}, status=status.HTTP_400_BAD_REQUEST)

    if reason == WorkdayJustification.Reason.CLIENT_VISIT:
        client_name = (body.get('client_name') or '').strip()
        if not client_name:
            return Response({'detail': 'Client name is required for a client visit.'},
                            status=status.HTTP_400_BAD_REQUEST)
        amount = body.get('amount')
        if amount in (None, ''):
            amount = None
        else:
            try:
                amount = Decimal(str(amount))
            except (InvalidOperation, ValueError):
                return Response({'detail': 'Potential premium must be a number.'},
                                status=status.HTTP_400_BAD_REQUEST)
        ClientVisit.objects.create(
            profile=prof, justification=row, visit_date=work_date, client_name=client_name,
            reason=(body.get('client_reason') or '').strip(),
            outcome=(body.get('client_outcome') or '').strip(),
            amount=amount,
        )

    if reason == WorkdayJustification.Reason.ON_LEAVE:
        # Only APPROVED leave is evidence — pending applications used to slip
        # through and self-clear the day (Fable review 2026-07-14).
        from hris.models import LeaveRequest
        approved = (LeaveRequest.objects
                    .filter(profile=prof, start_date__lte=work_date, end_date__gte=work_date,
                            status=LeaveRequest.Status.APPROVED)
                    .first())
        if approved is None:
            return Response(
                {'detail': 'No APPROVED leave covers that day. Please apply for leave '
                           '(or chase the approval) first — a pending request is not enough.'},
                status=status.HTTP_400_BAD_REQUEST)
        row.linked_leave = approved

    row.reason = reason
    row.justification = justification
    row.location = (body.get('location') or '').strip()
    if reason == WorkdayJustification.Reason.EXTERNAL_MEETING:
        # The 1.5h cap is real: a meeting justifies at most its own length,
        # never the whole shortfall. 90 min against a 4h gap ≠ a justified day.
        row.meeting_minutes = int(meeting_minutes)
        row.justified_hours = min(row.shortfall,
                                  (Decimal(row.meeting_minutes) / Decimal('60')).quantize(Decimal('0.01')))
    else:
        row.meeting_minutes = None
        row.justified_hours = row.shortfall

    new_status = workforce.classify_day(row.required_hours, row.tracked_hours, row.justified_hours)
    if reason == WorkdayJustification.Reason.ON_LEAVE:
        # Approved leave = system evidence → classify directly (auto-justify).
        row.status = {'met': WorkdayJustification.Status.MET,
                      'not_required': WorkdayJustification.Status.NOT_REQUIRED,
                      'justified': WorkdayJustification.Status.JUSTIFIED,
                      'unjustified': WorkdayJustification.Status.UNJUSTIFIED}[new_status]
    elif new_status == 'unjustified':
        # Even full acceptance wouldn't clear the day — no review needed.
        row.status = WorkdayJustification.Status.UNJUSTIFIED
    else:
        # Self-reported explanation that WOULD clear the day → a manager must
        # sign it off. Parked, not cleared.
        row.status = WorkdayJustification.Status.EXPLAINED
    row.responded_at = _tz.now()
    row.save()
    return Response({'work_date': work_date.isoformat(), 'status': row.status,
                     'justified_hours': str(row.justified_hours),
                     'pending_review': row.status == WorkdayJustification.Status.EXPLAINED})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def plan_absence(request):
    """Employee tells us IN ADVANCE they'll be out or short on an upcoming day
    (CFO 2026-07-15). justify_day only explains a day already flagged short;
    this pre-files the reason for today or a future date, so when that day's
    exceptions report runs the person shows under "told us in advance" instead
    of an unexplained no-show. A self-report is still not proof: it parks as
    EXPLAINED for the Monday manager review — approved leave auto-justifies,
    the same rule justify_day uses.
    """
    import datetime
    from django.utils import timezone as _tz
    from hris import workforce, workforce_brief
    from hris.models import WorkdayJustification, ClientVisit, LeaveRequest

    prof = _my_profile(request.user)
    if prof is None:
        return Response({'detail': 'No employee record is linked to your login.'},
                        status=status.HTTP_404_NOT_FOUND)
    body = request.data if isinstance(request.data, dict) else {}
    try:
        work_date = datetime.datetime.strptime(str(body.get('work_date') or '').strip(), '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return Response({'detail': 'work_date must be YYYY-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST)

    today = _tz.localtime().date()
    if work_date < today:
        return Response({'detail': 'Use this to plan today or an upcoming day. For a past day, '
                                   'open it in My Daily Brief and explain it there.'},
                        status=status.HTTP_400_BAD_REQUEST)
    if (work_date - today).days > 60:
        return Response({'detail': 'You can plan up to 60 days ahead.'}, status=status.HTTP_400_BAD_REQUEST)

    # Planned reasons are WHOLE-DAY absences. External meeting is deliberately
    # NOT offered here: a short off-site is justified reactively in justify_day
    # under the 1.5h cap, so letting it pre-file a whole day would bypass that cap.
    reason = (body.get('reason') or '').strip()
    valid = {WorkdayJustification.Reason.ON_LEAVE, WorkdayJustification.Reason.CLIENT_VISIT,
             WorkdayJustification.Reason.OTHER}
    if reason not in valid:
        return Response({'detail': 'Choose a valid reason.'}, status=status.HTTP_400_BAD_REQUEST)

    justification = (body.get('justification') or '').strip()
    if reason == WorkdayJustification.Reason.OTHER and not justification:
        return Response({'detail': 'Please add a short reason (e.g. "Baker Tilly golf day").'},
                        status=status.HTTP_400_BAD_REQUEST)
    client_name = (body.get('client_name') or '').strip()
    if reason == WorkdayJustification.Reason.CLIENT_VISIT and not client_name:
        return Response({'detail': 'Client name is required for a client visit.'},
                        status=status.HTTP_400_BAD_REQUEST)

    # Honour public holidays — a non-working holiday needs no plan. Managers /
    # EXCO / FM carry the lighter 4.5h weekday requirement (CFO 2026-07-23).
    from hris.workforce_roles import is_manager_hours_profile
    required = workforce.required_hours_for_date(
        work_date, workforce_brief.holiday_off_dates(),
        is_manager=is_manager_hours_profile(prof))
    if required <= 0:
        return Response({'detail': 'That day is already a non-working day — nothing to plan.'},
                        status=status.HTTP_400_BAD_REQUEST)

    # Approved leave is evidence (auto-justify); anything else parks as EXPLAINED
    # for the manager review. Resolve leave BEFORE writing any row so a failed
    # check never leaves a phantom justification behind.
    linked_leave = None
    new_status = WorkdayJustification.Status.EXPLAINED
    if reason == WorkdayJustification.Reason.ON_LEAVE:
        linked_leave = (LeaveRequest.objects
                        .filter(profile=prof, start_date__lte=work_date, end_date__gte=work_date,
                                status=LeaveRequest.Status.APPROVED)
                        .first())
        if linked_leave is None:
            return Response(
                {'detail': 'No APPROVED leave covers that day yet. Please apply for leave first — '
                           'once it is approved the day is covered automatically.'},
                status=status.HTTP_400_BAD_REQUEST)
        new_status = WorkdayJustification.Status.JUSTIFIED

    # All checks passed — write the row now.
    row, _created = WorkdayJustification.objects.get_or_create(
        profile=prof, work_date=work_date, defaults={'required_hours': required})
    row.required_hours = required
    row.reason = reason
    row.justification = justification
    row.location = (body.get('location') or '').strip()
    row.meeting_minutes = None
    row.justified_hours = required
    row.linked_leave = linked_leave
    row.status = new_status

    if reason == WorkdayJustification.Reason.CLIENT_VISIT:
        ClientVisit.objects.update_or_create(
            profile=prof, justification=row, visit_date=work_date,
            defaults={'client_name': client_name, 'reason': (body.get('client_reason') or '').strip()})

    row.responded_at = _tz.now()
    row.save()
    return Response({'work_date': work_date.isoformat(), 'status': row.status, 'planned': True})


# ---------------------------------------------------------------------------
# Manager review of self-reported explanations (EXPLAINED → JUSTIFIED /
# UNJUSTIFIED). Gated like the rest of the manager Time Doctor surface.
# ---------------------------------------------------------------------------

def _aria_week_summary(items) -> str:
    """A short Aria summary of the week's explanations for the manager Monday
    review (CFO 2026-07-14). Counts by reason only — NO names to the AI."""
    if not items:
        return ''
    from collections import Counter
    from core.ai_assist import reasoning_complete, is_safe_for_ai
    by_reason = Counter(i['reason'] for i in items)
    ctx = (f"{len(items)} short workdays await review this week. "
           f"By reason: " + ', '.join(f'{k}={v}' for k, v in by_reason.items()) + ".")
    system = ("You are Aria, Alpha Direct's workplace assistant. In 1-2 short sentences, "
              "summarise this week's staff hour-shortfall explanations for a manager's Monday "
              "review and suggest what to focus on. Plain English, no names, no lists.")
    try:
        rep = is_safe_for_ai(ctx)
        if not getattr(rep, 'safe', True):
            return ''
        return (reasoning_complete(ctx, system_prompt=system, timeout=12.0, max_tokens=90) or '').strip()
    except Exception:    # noqa: BLE001
        return ''


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def pending_justifications(request):
    """Self-reported explanations awaiting manager sign-off (newest first),
    with an Aria weekly summary for the Monday review."""
    from integrations.timedoctor_views import _can_view
    from hris.models import WorkdayJustification
    if not _can_view(request.user):
        return Response({'detail': 'Restricted to managers / HR.'}, status=status.HTTP_403_FORBIDDEN)
    rows = (WorkdayJustification.objects
            .filter(status=WorkdayJustification.Status.EXPLAINED)
            .select_related('profile__employee')
            .order_by('-work_date')[:200])
    items = [{
        'id': str(r.id),
        'employee': getattr(r.profile.employee, 'full_name', ''),
        'work_date': r.work_date.isoformat(),
        'required': str(r.required_hours),
        'tracked': str(r.tracked_hours),
        'justified_hours': str(r.justified_hours),
        'reason': r.reason,
        'justification': r.justification,
        'location': r.location,
        'meeting_minutes': r.meeting_minutes,
        'responded_at': r.responded_at.isoformat() if r.responded_at else None,
    } for r in rows]
    aria = _aria_week_summary(items) if not request.query_params.get('no_ai') else ''
    return Response({'items': items, 'aria_summary': aria})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def review_justification(request):
    """Approve or reject one EXPLAINED day. body {id, decision: approve|reject,
    note?}. Approve → classify with the claimed hours; reject → UNJUSTIFIED."""
    from django.utils import timezone as _tz
    from decimal import Decimal
    from integrations.timedoctor_views import _can_view
    from hris import workforce
    from hris.models import WorkdayJustification
    if not _can_view(request.user):
        return Response({'detail': 'Restricted to managers / HR.'}, status=status.HTTP_403_FORBIDDEN)
    body = request.data if isinstance(request.data, dict) else {}
    decision = (body.get('decision') or '').strip().lower()
    if decision not in ('approve', 'reject'):
        return Response({'detail': "decision must be 'approve' or 'reject'."},
                        status=status.HTTP_400_BAD_REQUEST)
    try:
        row = (WorkdayJustification.objects
               .filter(id=body.get('id'), status=WorkdayJustification.Status.EXPLAINED)
               .select_related('profile__employee').first())
    except (ValueError, ValidationError):    # malformed UUID → 400, not 500
        return Response({'detail': 'Invalid id.'}, status=status.HTTP_400_BAD_REQUEST)
    if row is None:
        return Response({'detail': 'No explanation awaiting review with that id.'},
                        status=status.HTTP_404_NOT_FOUND)
    # Separation of duties: you cannot sign off your OWN explanation (Fable
    # review 2026-07-14) — that would defeat the manager-review control.
    my_email = (getattr(request.user, 'email', '') or '').strip().lower()
    subj_email = (getattr(row.profile.employee, 'email', '') or '').strip().lower()
    if my_email and subj_email and my_email == subj_email:
        return Response({'detail': 'You cannot review your own explanation — it needs another manager.'},
                        status=status.HTTP_403_FORBIDDEN)
    if decision == 'approve':
        verdict = workforce.classify_day(row.required_hours, row.tracked_hours, row.justified_hours)
        row.status = (WorkdayJustification.Status.JUSTIFIED if verdict == 'justified'
                      else WorkdayJustification.Status.MET if verdict == 'met'
                      else WorkdayJustification.Status.UNJUSTIFIED)
    else:
        row.justified_hours = Decimal('0')
        row.status = WorkdayJustification.Status.UNJUSTIFIED
    row.reviewed_by = request.user
    row.reviewed_at = _tz.now()
    row.review_note = (body.get('note') or '').strip()
    row.save(update_fields=['status', 'justified_hours', 'reviewed_by',
                            'reviewed_at', 'review_note', 'updated_at'])
    return Response({'id': str(row.id), 'status': row.status})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def timedoctor_leaderboard(request):
    """Gamified leaderboard for the Time Doctor dashboard — ranked hours for a
    period (day / week / month-to-date), with the top performer and the lowest.
    Manager/HR gated; cached 2h (the pull is a few seconds). Aggregates only.
    Pool = MATCHED payroll staff only (shared matcher) — a contractor or role
    account can never top the board (Fable review 2026-07-14)."""
    import datetime
    from django.core.cache import cache
    from django.utils import timezone as _tz
    from integrations.timedoctor_views import _can_view
    from integrations.timedoctor import TimeDoctorClient, TimeDoctorError
    from integrations.td_matching import TDMatcher, active_td_users
    from hris import eligibility, exceptions_report

    if not _can_view(request.user):
        return Response({'detail': 'Restricted to managers / HR.'}, status=status.HTTP_403_FORBIDDEN)
    period = (request.query_params.get('period') or 'week').lower()
    if period not in ('day', 'week', 'month'):
        period = 'week'
    ck = f'td_leaderboard_{period}'
    hit = cache.get(ck)
    if hit and not request.query_params.get('refresh'):
        return Response(hit)

    client = TimeDoctorClient.from_settings()
    if not client.configured:
        return Response({'period': period, 'configured': False, 'top': [], 'count': 0})
    today = _tz.localtime().date()
    if period == 'day':
        w_from, w_to = exceptions_report.day_window_utc(today - datetime.timedelta(days=1))
    elif period == 'month':
        # Calendar month-to-date ("month" used to mean a rolling 28 days).
        w_from = exceptions_report.day_window_utc(today.replace(day=1))[0]
        w_to = exceptions_report.day_window_utc(today)[1]
    else:
        w_from = exceptions_report.day_window_utc(today - datetime.timedelta(days=7))[0]
        w_to = exceptions_report.day_window_utc(today)[1]

    try:
        users = active_td_users(client.users())
        ids = [u.get('id') for u in users if u.get('id')]
        matcher = TDMatcher(users, [p.employee for p in eligibility.tracking_profiles()])
        totals: dict = {}
        for bucket in client.worklog(w_from, w_to, user_ids=ids):
            rows = bucket if isinstance(bucket, list) else [bucket]
            for r in rows:
                if isinstance(r, dict) and r.get('userId') in matcher.employee_for_uid:
                    totals[r['userId']] = totals.get(r['userId'], 0) + int(r.get('time') or 0)
    except TimeDoctorError as exc:
        return Response({'detail': f'Time Doctor pull failed: {exc}'}, status=status.HTTP_502_BAD_GATEWAY)

    ranked = sorted(
        [{'name': (getattr(matcher.employee_for_uid[uid], 'full_name', '') or '').strip() or str(uid),
          'hours': round(sec / 3600, 1)}
         for uid, sec in totals.items() if sec > 0],
        key=lambda x: x['hours'], reverse=True)
    data = {
        'period': period, 'configured': True, 'count': len(ranked),
        'total_hours': round(sum(totals.values()) / 3600, 1),
        'winner': ranked[0] if ranked else None,
        'runner_up': ranked[1] if len(ranked) > 1 else None,
        'lowest': ranked[-1] if ranked else None,
        'top': ranked[:15],
    }
    cache.set(ck, data, 60 * 120)
    return Response(data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def timedoctor_latecomers(request):
    """Punctuality dashboard (CFO 2026-07-14): frequent latecomers over the last
    ~month — how many days each matched staff member started after 09:00, plus
    the company total. Manager/HR gated; cached 2h. Aggregates only."""
    import datetime
    from django.core.cache import cache
    from django.utils import timezone as _tz
    from integrations.timedoctor_views import _can_view
    from integrations.timedoctor import TimeDoctorClient, TimeDoctorError
    from integrations.td_matching import TDMatcher, active_td_users
    from hris import eligibility, exceptions_report

    if not _can_view(request.user):
        return Response({'detail': 'Restricted to managers / HR.'}, status=status.HTTP_403_FORBIDDEN)
    ck = 'td_latecomers_month'
    hit = cache.get(ck)
    if hit and not request.query_params.get('refresh'):
        return Response(hit)

    client = TimeDoctorClient.from_settings()
    if not client.configured:
        return Response({'configured': False, 'latecomers': [], 'window_days': 30})
    today = _tz.localtime().date()
    w_from = exceptions_report.day_window_utc(today - datetime.timedelta(days=30))[0]
    w_to = exceptions_report.day_window_utc(today)[1]
    try:
        users = active_td_users(client.users())
        ids = [u.get('id') for u in users if u.get('id')]
        matcher = TDMatcher(users, [p.employee for p in eligibility.tracking_profiles()])
        wl = client.worklog(w_from, w_to, user_ids=ids)
        late = exceptions_report.late_days_by_user(wl)
    except TimeDoctorError as exc:
        return Response({'detail': f'Time Doctor pull failed: {exc}'}, status=status.HTTP_502_BAD_GATEWAY)

    rows = sorted(
        [{'name': (getattr(matcher.employee_for_uid[uid], 'full_name', '') or '').strip() or str(uid),
          'late_days': n}
         for uid, n in late.items() if uid in matcher.employee_for_uid and n > 0],
        key=lambda x: x['late_days'], reverse=True)
    data = {'configured': True, 'window_days': 30, 'count': len(rows),
            'total_late_days': sum(r['late_days'] for r in rows), 'latecomers': rows[:20]}
    cache.set(ck, data, 60 * 120)
    return Response(data)


# ---------------------------------------------------------------------------
# Who-tracks setup panel (CFO 2026-07-14): confirm the TD↔staff match once and
# click track / don't-track per person. CFO / Arun / Arjun only (same gate as
# the switch) — this decides who the whole feature reports on.
# ---------------------------------------------------------------------------

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def tracking_setup(request):
    """Every active employee with effective track/don't-track + their matched
    Time Doctor account (auto or confirmed). Powers the setup dashboard."""
    if not _can_link(request.user):
        return Response({'detail': 'Restricted to the CFO, Arun, Arjun and the HR team.'}, status=status.HTTP_403_FORBIDDEN)
    from hris import eligibility
    from integrations.td_matching import TDMatcher, active_td_users
    from integrations.timedoctor import TimeDoctorClient
    from integrations.models import TimeDoctorUserMap
    from payroll.models import Employee

    roster = eligibility.tracking_roster()
    # One read of the active pool, used by BOTH the matcher and the off-roster
    # pass below. Declared out here so the second pass cannot depend on whether
    # the Time Doctor call succeeded.
    emps = list(Employee.objects.filter(status='active'))
    td_by_emp, confirmed_emp, unmatched_td = {}, set(), []
    client = TimeDoctorClient.from_settings()
    configured = client.configured
    if configured:
        try:
            users = active_td_users(client.users())
            # Match against EVERY active employee, not only the tracking roster
            # (CFO 2026-09-15). tracking_roster() is built from HRISProfile rows,
            # so an active employee without a profile was never offered to the
            # matcher at all and could never be linked from this screen. Measured
            # on prod 15-Sep-2026: 160 active staff, 156 roster rows, so 4 sat
            # outside it — none of them matching a TD account that day, which is
            # why this closes a structural hole rather than fixing a live backlog.
            # This view PERSISTS NOTHING: every hours consumer builds its own
            # matcher from its own pool, so proposing a match here changes no
            # attribution. The tick via confirm_match is what makes a link stable.
            # (Do NOT restate this as "hours resolve through the confirmed map
            # only" — hours_by_employee falls through to the guarded email/name
            # passes for accounts nobody has confirmed. See K6.)
            m = TDMatcher(users, emps)
            name_by_uid = {str(u.get('id')): (u.get('name') or '') for u in users}
            for uid, emp in m.employee_for_uid.items():
                td_by_emp[emp.id] = {'td_user_id': str(uid), 'td_name': name_by_uid.get(str(uid), '')}
            confirmed_emp = set(TimeDoctorUserMap.objects
                                .filter(confirmed=True, employee__isnull=False)
                                .values_list('employee_id', flat=True))
            unmatched_td = [{'td_user_id': str(x.get('id')), 'td_name': x.get('name') or ''}
                            for x in m.unmatched_td]
        except Exception:    # noqa: BLE001
            configured = False

    items = []
    for r in roster:
        td = td_by_emp.get(r['employee_id'])
        items.append({**r,
                      'matched_td': td['td_name'] if td else None,
                      'td_user_id': td['td_user_id'] if td else None,
                      'confirmed': r['employee_id'] in confirmed_emp})
    # Active staff missing from the roster (no HRISProfile) who nonetheless have
    # a proposed Time Doctor account — show them so HR can tick the link. They
    # carry expected=False/source='no-profile' so nothing about who is REPORTED
    # on changes here; this screen only offers the link.
    on_roster = {r['employee_id'] for r in roster}
    # td_by_emp is empty whenever Time Doctor is unreachable or unconfigured, and
    # then there is nothing to propose — skip the query entirely.
    extra = sorted(
        (e for e in emps if e.id not in on_roster and td_by_emp.get(e.id)),
        key=lambda e: (e.full_name or '')) if td_by_emp else []
    for e in extra:
        td = td_by_emp[e.id]
        items.append({'employee_id': e.id, 'name': (e.full_name or '').strip(),
                      'department': e.department or '', 'job_title': e.job_title or '',
                      'expected': False, 'source': 'no-profile',
                      'matched_td': td['td_name'], 'td_user_id': td['td_user_id'],
                      'confirmed': e.id in confirmed_emp})
    return Response({'configured': configured, 'items': items,
                     'unmatched_td': unmatched_td, 'can_edit': True,
                     'can_toggle': _can_toggle(request.user)})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def set_tracking(request):
    """Set track / don't-track for one employee. body {employee_id, expected_to_track}."""
    if not _can_toggle(request.user):
        return Response({'detail': 'Restricted to the CFO, Arun or Arjun.'}, status=status.HTTP_403_FORBIDDEN)
    from django.core.cache import cache
    from hris.models import TrackingDirective
    from payroll.models import Employee
    body = request.data if isinstance(request.data, dict) else {}
    emp = Employee.objects.filter(id=body.get('employee_id')).first()
    if emp is None:
        return Response({'detail': 'Unknown employee.'}, status=status.HTTP_404_NOT_FOUND)
    expected = bool(body.get('expected_to_track'))
    TrackingDirective.objects.update_or_create(
        employee=emp, defaults={'expected_to_track': expected,
                                'note': (body.get('note') or '').strip(),
                                'updated_by': request.user})
    for p in ('day', 'week', 'month'):
        cache.delete(f'td_leaderboard_{p}')
    cache.delete('td_latecomers_month')
    return Response({'employee_id': str(emp.id), 'expected_to_track': expected})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def confirm_match(request):
    """Confirm (or correct) the TD↔employee link for good. body {employee_id,
    td_user_id, td_name?}. Writes a confirmed TimeDoctorUserMap the matcher
    trusts above all name matching."""
    if not _can_link(request.user):
        return Response({'detail': 'Restricted to the CFO, Arun, Arjun and the HR team.'}, status=status.HTTP_403_FORBIDDEN)
    from integrations.models import TimeDoctorUserMap
    from payroll.models import Employee
    body = request.data if isinstance(request.data, dict) else {}
    td_user_id = (body.get('td_user_id') or '').strip()
    if not td_user_id:
        return Response({'detail': 'td_user_id is required.'}, status=status.HTTP_400_BAD_REQUEST)
    emp = None
    if body.get('employee_id'):
        emp = Employee.objects.filter(id=body.get('employee_id')).first()
        if emp is None:
            return Response({'detail': 'Unknown employee.'}, status=status.HTTP_404_NOT_FOUND)
    # A TD account maps to at most one employee — clear any other row that
    # currently claims this employee so the confirmed link is unique.
    if emp is not None:
        TimeDoctorUserMap.objects.filter(employee=emp).exclude(td_user_id=td_user_id).update(
            employee=None, confirmed=False)
    TimeDoctorUserMap.objects.update_or_create(
        td_user_id=td_user_id,
        defaults={'employee': emp, 'confirmed': emp is not None,
                  'td_name': (body.get('td_name') or '').strip()[:191],
                  'source': TimeDoctorUserMap.Source.MANUAL, 'updated_by': request.user})
    return Response({'td_user_id': td_user_id,
                     'employee_id': str(emp.id) if emp else None,
                     'confirmed': emp is not None})


# ---------------------------------------------------------------------------
# CFO "Excuses feed" (Leave & Productive-Hours Accountability, CFO 2026-07-21).
# Read-only view over the explanations staff give for missed hours, for the CFO
# + HR (Unami) + exec. Surfaces each person's reason in their own words, auto-
# flags the watch-list ("power cut at home"), and a most-used-words panel so
# common excuses surface across the company. Filter by person to read one
# employee's whole run before a conversation. This is a REPORT — it deducts and
# changes nothing. See hris/leave_accountability.py for the flag/word rules.
# ---------------------------------------------------------------------------

def _can_see_excuses(user) -> bool:
    """CFO + Arun + Arjun + HR (Unami / HR-role holders) + superuser. Deliberately
    tighter than the manager Time Doctor surface — this is the whole company's
    excuses in their own words, so it stays with exec/HR, not every line manager."""
    if getattr(user, 'is_superuser', False):
        return True
    email = (getattr(user, 'email', '') or '').strip().lower()
    exec_hr = {
        'pganesharajah@alphadirect.co.bw',   # CFO
        'aiyer@alphadirect.co.bw',           # Arun (CEO)
        'arjuniyer@alphadirect.co.bw',       # Arjun (COO)
        'ubutale@alphadirect.co.bw',         # Unami (HR)
        'dikgopoleng@alphadirect.co.bw',     # Dorothy (HR Manager)
    }
    if email in exec_hr:
        return True
    try:
        from core.hris_access import hris_role
        return hris_role(user) in {'hr', 'hris', 'admin', 'superadmin', 'ceo'}
    except Exception:    # noqa: BLE001
        return False


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def excuses_feed(request):
    """The CFO Excuses feed. Query params:
        days?   window in days (default 30, max 120)
        person? profile UUID — filter to one employee's whole run
    Returns the explanations (newest first) + a watch-list flag per row + a
    most-used-words panel + counts by reason. Read-only; deducts nothing."""
    from datetime import timedelta
    from django.utils import timezone as _tz
    from collections import Counter
    from hris.models import WorkdayJustification
    from hris import leave_accountability as la
    from hris import workforce

    if not _can_see_excuses(request.user):
        return Response({'detail': 'Restricted to the CFO, exec and HR.'},
                        status=status.HTTP_403_FORBIDDEN)

    try:
        days = int(request.query_params.get('days', 30))
    except (TypeError, ValueError):
        days = 30
    days = max(1, min(days, 120))
    since = (_tz.localdate() - timedelta(days=days))

    # Rows where the person actually said something, OR a short day still stands
    # unexplained — both belong in the CFO's view of "who missed and why".
    qs = (WorkdayJustification.objects
          .filter(work_date__gte=since)
          .filter(status__in=[WorkdayJustification.Status.EXPLAINED,
                              WorkdayJustification.Status.JUSTIFIED,
                              WorkdayJustification.Status.UNJUSTIFIED])
          .select_related('profile__employee')
          .order_by('-work_date'))

    person = (request.query_params.get('person') or '').strip()
    if person:
        try:
            qs = qs.filter(profile_id=person)
        except (ValueError, ValidationError):
            return Response({'detail': 'Bad person id.'}, status=status.HTTP_400_BAD_REQUEST)

    rows = list(qs[:500])
    items, explanations = [], []
    by_reason: Counter = Counter()
    by_person: Counter = Counter()
    flagged_count = 0
    for r in rows:
        text = r.justification or ''
        flags = la.flag_excuse(text)
        if flags:
            flagged_count += 1
        if text:
            explanations.append(text)
        shortfall = workforce.shortfall_hours(r.required_hours or 0, r.tracked_hours or 0)
        emp_name = getattr(getattr(r.profile, 'employee', None), 'full_name', '') or ''
        by_reason[r.reason] += 1
        if emp_name:
            by_person[emp_name] += 1
        items.append({
            'id': str(r.id),
            'profile_id': str(r.profile_id),
            'employee': emp_name,
            'work_date': r.work_date.isoformat(),
            'reason': r.reason,
            'explanation': text,
            'required': str(r.required_hours),
            'tracked': str(r.tracked_hours),
            'shortfall_hours': f'{shortfall:.2f}',
            'would_be_leave_hours': str(la.leave_hours_for_shortfall(shortfall)),
            'status': r.status,
            'flagged': bool(flags),
            'flag_terms': flags,
            'responded_at': r.responded_at.isoformat() if r.responded_at else None,
        })

    return Response({
        'window_days': days,
        'enforcement_active': la.enforcement_active(_tz.localdate()),
        'strict_from': la.STRICT_ENFORCEMENT_START.isoformat(),
        'total': len(items),
        'flagged_count': flagged_count,
        'by_reason': dict(by_reason),
        'top_people': by_person.most_common(15),
        'top_words': la.word_frequency(explanations, top_n=25),
        'items': items,
    })
