from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.aria.qc_tools import CFO_EMAILS
from core.mixins import apply_company_scope
from hris.api_views import _deny_if_not_whitelisted
from hris.command_center import compute_month
from payroll.models import Employee


GABORONE_TZ = ZoneInfo('Africa/Gaborone')


def _last_full_month(today: date) -> tuple[int, int]:
    first_current = date(today.year, today.month, 1)
    last_full = first_current - __import__('datetime').timedelta(days=1)
    return last_full.year, last_full.month


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def command_center(request):
    """GET /api/v1/hris/command-center/?month=YYYY-MM

    Opens on the last full calendar month by default. `named=True` is
    required for per-person salary-at-risk and the named flight-risk list.
    """
    deny = _deny_if_not_whitelisted(request)
    if deny:
        return deny

    now_dt = timezone.now()
    today = now_dt.astimezone(GABORONE_TZ).date()

    month_param = (request.query_params.get('month') or '').strip()
    if month_param:
        try:
            year_s, month_s = month_param.split('-')
            year = int(year_s)
            month = int(month_s)
            if not (1 <= month <= 12) or not (2000 <= year <= 2100):
                raise ValueError
        except Exception:
            return Response({'detail': 'month must be YYYY-MM'}, status=400)
    else:
        year, month = _last_full_month(today)

    named = bool(
        request.user.is_superuser
        or (request.user.email and request.user.email.lower() in CFO_EMAILS)
        or request.user.has_perm('payroll.view_employee')
    )

    qs = Employee.objects.filter(status=Employee.Status.ACTIVE)
    qs = apply_company_scope(request, qs, 'company_id')

    data = compute_month(year, month, employees_qs=qs, now=now_dt, named=named)
    data['month'] = f'{year:04d}-{month:02d}'
    data['named'] = named
    return Response(data)
