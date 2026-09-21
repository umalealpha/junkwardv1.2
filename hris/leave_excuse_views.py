"""
hris/leave_excuse_views.py

Read-only API for the Leave Excuse Response dashboard (CFO 2026-07-22). Exec/HR
only — reuses hris.workforce_views._can_see_excuses verbatim (CFO + Arun + Arjun
+ Unami + Dorothy + HR-role holders + superuser). This GET NEVER sends an email
and deducts nothing; the auto-responder lives in the process_leave_excuses
command behind the LEAVE_EXCUSE_AUTOSEND gate.

  GET /hris/api/leave-excuse/?date=YYYY-MM-DD   → {me, date, rows, aria, low_hours, snapshot}
"""

from __future__ import annotations

import datetime

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

# Reuse the exec/HR gate verbatim — do not redefine it (CFO 2026-07-21).
from hris.workforce_views import _can_see_excuses


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def leave_excuse_dashboard(request):
    """Low/no productive-hours people for a day, their explanations, and Aria's
    anonymised read. Exec/HR gated; read-only."""
    if not _can_see_excuses(request.user):
        return Response({'detail': 'Restricted to the CFO, exec and HR.'},
                        status=status.HTTP_403_FORBIDDEN)

    from hris.leave_excuse_service import build_day
    from hris.leave_excuse_ai import aria_analysis

    d = None
    raw_date = (request.query_params.get('date') or '').strip()
    if raw_date:
        try:
            d = datetime.datetime.strptime(raw_date, '%Y-%m-%d').date()
        except ValueError:
            return Response({'detail': 'date must be YYYY-MM-DD.'},
                            status=status.HTTP_400_BAD_REQUEST)

    # NOT cached here. Caching the whole day froze the dashboard's own
    # decisions: the page reloads this endpoint straight after Accept/Reject, so
    # a cached day left the row reading "not accepted" for ten minutes while the
    # toast said it had been accepted (Fable review 2026-09-09). Only the Time
    # Doctor call inside build_day is slow, and that is cached at source in
    # hris.leave_excuse_service — everything else here is one database query.
    day = build_day(d)
    rows = day['rows']

    # Aria is best-effort and opt-out (?no_ai=1) — never blocks the table.
    if rows and not request.query_params.get('no_ai'):
        aria = aria_analysis(rows, date_str=day['date'] or '')
    else:
        aria = {'ok': False, 'reason': 'no rows' if not rows else 'ai skipped',
                'items': None, 'text': ''}

    user = request.user
    me = {
        'name': (user.get_full_name() or user.get_username() or '').strip(),
        'email': (getattr(user, 'email', '') or '').strip(),
    }
    return Response({
        'me': me,
        'date': day['date'],
        'snapshot': day['snapshot'],
        'low_hours': day['low_hours'],
        'rows': rows,
        'aria': aria,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def leave_excuse_decide(request):
    """The CFO/HR decides on one person's day from the dashboard (CFO 2026-07-23).

    Body: {profile_id, date: YYYY-MM-DD, action, note?}
      action='accept'       -> explanation accepted (WorkdayJustification JUSTIFIED,
                               signed by this user). No pay/leave impact.
      action='reject_more'  -> not good enough; re-open for the employee to explain
                               again (status PENDING) and record the note.
      action='reject_leave' -> reject and deduct the day as annual leave: raise a
                               PENDING annual LeaveRequest for that date (flows the
                               normal leave approval, never a silent balance raid)
                               and link it to the day.

    Same exec/HR gate as the dashboard. Writes a review note + reviewer stamp."""
    from django.utils import timezone
    if not _can_see_excuses(request.user):
        return Response({'detail': 'Restricted to the CFO, exec and HR.'},
                        status=status.HTTP_403_FORBIDDEN)

    profile_id = (request.data.get('profile_id') or '').strip()
    raw_date = (request.data.get('date') or '').strip()
    action = (request.data.get('action') or '').strip()
    note = (request.data.get('note') or '').strip()
    if action not in ('accept', 'reject_more', 'reject_leave'):
        return Response({'detail': 'Unknown action.'}, status=400)
    if action in ('reject_more', 'reject_leave') and not note:
        return Response({'detail': 'A short note is required for a rejection.'}, status=400)
    try:
        d = datetime.datetime.strptime(raw_date, '%Y-%m-%d').date()
    except ValueError:
        return Response({'detail': 'date must be YYYY-MM-DD.'}, status=400)

    from hris.models import HRISProfile, WorkdayJustification, LeaveRequest, LeaveType
    prof = HRISProfile.objects.filter(pk=profile_id).select_related('employee').first()
    if prof is None:
        return Response({'detail': 'Person not found.'}, status=404)

    # Required hours come from the POLICY function, never a literal — a hardcoded
    # 4 here kept stamping the old Saturday requirement after it moved to 3h, and
    # was plain wrong on a weekday (6.5) or a Sunday (0).
    from hris import workforce
    wj, _ = WorkdayJustification.objects.get_or_create(
        profile=prof, work_date=d,
        defaults={'required_hours': workforce.required_hours_for_date(d), 'tracked_hours': 0})
    wj.reviewed_by = request.user
    wj.reviewed_at = timezone.now()
    wj.review_note = note

    if action == 'accept':
        wj.status = WorkdayJustification.Status.JUSTIFIED
        wj.save()
        msg = 'Explanation accepted.'

    elif action == 'reject_more':
        # Re-open so the employee can explain again; keep the reviewer note.
        wj.status = WorkdayJustification.Status.PENDING
        wj.responded_at = None
        wj.save()
        msg = 'Sent back — the employee must explain again.'

    else:  # reject_leave
        leave_type, _ = LeaveType.objects.get_or_create(
            code='annual', defaults={'name': 'Annual Leave', 'default_annual_days': 21})
        clash = LeaveRequest.objects.filter(
            profile=prof, start_date__lte=d, end_date__gte=d,
            status__in=[LeaveRequest.Status.APPROVED, LeaveRequest.Status.PENDING]).first()
        if clash is None:
            lr = LeaveRequest.objects.create(
                profile=prof, leave_type=leave_type, start_date=d, end_date=d, days=1,
                reason=f'Unexplained absence on {d:%d %b %Y} — converted to annual '
                       f'leave by {request.user.get_username()}. {note}',
                status=LeaveRequest.Status.PENDING)
            wj.linked_leave = lr
        wj.reason = WorkdayJustification.Reason.ON_LEAVE
        wj.status = WorkdayJustification.Status.UNJUSTIFIED
        wj.save()
        msg = 'Rejected — a leave request for that day was raised for approval.'

    return Response({'ok': True, 'detail': msg})
