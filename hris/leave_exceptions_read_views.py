"""
hris/leave_exceptions_read_views.py

READ-ONLY view over the Time Doctor / productive-hours guard state (CFO
2026-09-18, Easy PR E: "Time Doctor visibility").

The problem: the guard that turns a missed-hours day into an excuse chase or a
leave deduction is already built (see hris.leave_excuse_service.build_day and
hris.management.commands.process_leave_excuses). But its state was only visible
to CFO / exec / HR via /hris/leave-excuse. A line manager could not see whether
their own report had tripped, been asked for an explanation, or had a day
converted to leave — so they could not act early and only found out after the
fact.

This module adds one endpoint: a line manager sees their direct + co-managed
reports; HR / exec / superuser see everyone; a plain employee sees ONLY their
own row. It calls build_day for today plus the previous 7 days and returns the
existing row dicts verbatim (with the row's existing `status` also copied to
`guard_state`, so a UI can key off a stable field name). Threshold values are
NOT read or written here — build_day pulls them from env exactly as before.

Read-only:
  * Only GET is exposed.
  * build_day is a pure read of the Time Doctor snapshot / live feed and the
    WorkdayJustification join; it writes nothing, and this view does not touch
    LeaveRequest / WorkdayJustification / TrackingDirective at all.
  * No side calls into process_leave_excuses, the deduction path, or any writer.

  GET /hris/api/leave-exceptions/?days=N       (default 7, max 30)
     → {me, scope, low_hours, days: [{date, rows: [...]}]}
"""
from __future__ import annotations

import datetime as _dt

from django.db.models import Q
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from hris.performance_views import _resolve_employee
from hris.workforce_views import _can_see_excuses


_MAX_DAYS = 30
_DEFAULT_DAYS = 7


def _days_param(request) -> int:
    raw = (request.query_params.get('days') or '').strip()
    if not raw:
        return _DEFAULT_DAYS
    try:
        n = int(raw)
    except ValueError:
        return _DEFAULT_DAYS
    return max(1, min(_MAX_DAYS, n))


def _visible_profile_ids(user):
    """(scope, profile_id set or None). None means "all rows visible" (HR/exec).

    HR/exec/superuser -> ('hr', None) — every row.
    Manager           -> ('manager', {profile_ids of direct + co-managed reports})
    Own row only      -> ('self', {own profile_id}) if the caller has a profile.
    Nothing           -> ('none', set()) — the endpoint returns 403.
    """
    from hris.models import HRISProfile

    if _can_see_excuses(user):
        return 'hr', None
    me = _resolve_employee(user)
    if me is None:
        return 'none', set()
    reports = set(
        HRISProfile.objects
        .filter(Q(manager_id=me.id) | Q(co_manager_id=me.id))
        .values_list('id', flat=True)
    )
    own = set(
        HRISProfile.objects.filter(employee_id=me.id).values_list('id', flat=True)
    )
    if reports:
        return 'manager', reports | own
    if own:
        return 'self', own
    return 'none', set()


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def leave_exceptions_read(request):
    """Read-only preview of the productive-hours guard state.

    Uses hris.leave_excuse_service.build_day (pure read) for today + the previous
    N days, filters each day's rows to the caller's authorised scope, and passes
    the existing `status` field through as `guard_state` (verbatim — the values
    are the ones the service already emits: explained / no explanation / on
    leave / not accepted / appealed / accepted / rejected / hours still
    arriving). Thresholds are unchanged; no deduction runs from viewing.
    """
    from django.utils import timezone
    from hris.leave_excuse_service import build_day, low_hours_threshold

    scope, allowed = _visible_profile_ids(request.user)
    if scope == 'none':
        return Response(
            {'detail': 'No Time Doctor visibility for this account.'},
            status=status.HTTP_403_FORBIDDEN,
        )

    n = _days_param(request)
    today = timezone.localdate()
    days_out = []
    for i in range(n):
        d = today - _dt.timedelta(days=i)
        day = build_day(d)   # pure read — see module docstring.
        rows = day.get('rows') or []
        if allowed is not None:
            rows = [r for r in rows if r.get('profile_id') in {str(pid) for pid in allowed}]
        # Mirror the row's existing `status` onto `guard_state` under a stable
        # name for the UI. Do NOT invent new states.
        for r in rows:
            r['guard_state'] = r.get('status', '')
        days_out.append({
            'date': day.get('date') or d.isoformat(),
            'snapshot': bool(day.get('snapshot')),
            'rows': rows,
        })

    user = request.user
    me = {
        'name': (user.get_full_name() or user.get_username() or '').strip(),
        'email': (getattr(user, 'email', '') or '').strip(),
    }
    return Response({
        'me': me,
        'scope': scope,           # 'hr' | 'manager' | 'self'
        'low_hours': low_hours_threshold(),
        'read_only': True,
        'thresholds_unchanged': True,
        'days': days_out,
    })
