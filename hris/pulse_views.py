"""
hris/pulse_views.py — weekly anonymous staff mood / "pulse" check API.

Three endpoints:
  POST /api/v1/hris/pulse/submit/     — caller submits/updates THIS week's
                                         mood (1-5) + optional comment.
                                         IsAuthenticated only — NO HRIS
                                         whitelist; every member of staff
                                         may record their own pulse.
  GET  /api/v1/hris/pulse/me/         — has the caller already submitted
                                         this week, and with what score.
  GET  /api/v1/hris/pulse/dashboard/  — HR/exec-only aggregate view: mood
                                         trend over the last 12 weeks,
                                         average by department, and this
                                         week's participation %.

ANONYMOUS by design: the dashboard returns aggregates only, and never an
average built from fewer than MIN_GROUP responses — a department that never
reaches that floor in the window is dropped entirely; a low-turnout week on
the trend line keeps its response count (a headcount carries no sentiment)
but has its average suppressed.
"""
from __future__ import annotations

import datetime

from django.db.models import Avg, Count
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from hris.api_views import _deny_if_not_whitelisted
from hris.pulse_models import PulseResponse

MIN_GROUP    = 3     # never surface an aggregate built from fewer people than this
WINDOW_WEEKS = 12
MAX_COMMENT  = 1000


def _resolve_employee(user):
    """Map a Django auth user to their payroll.Employee (by user link, then email).

    Same resolver as hris.performance_views._resolve_employee.
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return None
    from payroll.models import Employee
    emp = Employee.objects.filter(user=user).first()
    if emp:
        return emp
    email = (getattr(user, 'email', '') or '').strip()
    if email:
        return Employee.objects.filter(email__iexact=email).first()
    return None


def _resolve_profile(user):
    """Map a Django auth user to their HRISProfile, or None."""
    emp = _resolve_employee(user)
    if emp is None:
        return None
    return getattr(emp, 'hris_profile', None)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def submit_pulse(request):
    """Upsert the caller's own mood check-in for the current week."""
    profile = _resolve_profile(request.user)
    if profile is None:
        return Response(
            {'detail': 'No HRIS profile linked to your account.'},
            status=status.HTTP_404_NOT_FOUND,
        )

    try:
        score = int(request.data.get('score'))
    except (TypeError, ValueError):
        return Response({'detail': 'score must be an integer 1-5.'},
                         status=status.HTTP_400_BAD_REQUEST)
    if score < 1 or score > 5:
        return Response({'detail': 'score must be between 1 and 5.'},
                         status=status.HTTP_400_BAD_REQUEST)
    comment = str(request.data.get('comment') or '').strip()[:MAX_COMMENT]

    week = PulseResponse.current_week_start()
    PulseResponse.objects.update_or_create(
        profile=profile, week_start=week,
        defaults={'score': score, 'comment': comment},
    )
    return Response({'ok': True, 'week_start': week.isoformat()})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_pulse(request):
    """Has the caller already submitted this week's check-in, and with what score."""
    profile = _resolve_profile(request.user)
    if profile is None:
        return Response({'submitted_this_week': False, 'score': None})
    week = PulseResponse.current_week_start()
    resp = PulseResponse.objects.filter(profile=profile, week_start=week).first()
    return Response({
        'submitted_this_week': resp is not None,
        'score': resp.score if resp else None,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def pulse_dashboard(request):
    """HR/exec aggregate view: mood trend, department averages, participation."""
    deny = _deny_if_not_whitelisted(request)
    if deny:
        return deny

    from hris.models import HRISProfile
    from payroll.models import Employee

    current   = PulseResponse.current_week_start()
    earliest  = current - datetime.timedelta(weeks=WINDOW_WEEKS - 1)
    week_list = [earliest + datetime.timedelta(weeks=i) for i in range(WINDOW_WEEKS)]

    in_window = PulseResponse.objects.filter(week_start__gte=earliest, week_start__lte=current)

    by_week = {
        row['week_start']: row
        for row in in_window.values('week_start').annotate(avg=Avg('score'), n=Count('id'))
    }
    weeks = []
    for wk in week_list:
        row = by_week.get(wk)
        n = row['n'] if row else 0
        avg = round(float(row['avg']), 1) if row and n >= MIN_GROUP else None
        weeks.append({'week_start': wk.isoformat(), 'avg_score': avg, 'responses': n})

    dept_rows = (
        in_window
        .exclude(profile__employee__department='')
        .values('profile__employee__department')
        .annotate(avg=Avg('score'), n=Count('id'))
        .filter(n__gte=MIN_GROUP)
        .order_by('-avg')
    )
    by_department = [
        {
            'department': row['profile__employee__department'],
            'avg_score':  round(float(row['avg']), 1),
            'responses':  row['n'],
        }
        for row in dept_rows
    ]

    responded    = PulseResponse.objects.filter(week_start=current).count()
    total_active = HRISProfile.objects.filter(employee__status=Employee.Status.ACTIVE).count()
    pct          = round(responded / total_active * 100, 1) if total_active else 0.0

    return Response({
        'weeks': weeks,
        'by_department': by_department,
        'participation': {
            'responded': responded,
            'total_active': total_active,
            'pct': pct,
        },
    })
