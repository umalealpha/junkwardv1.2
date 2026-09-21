"""
core/my_omni_views.py

Two net-new feeds for the "My Omni — Personal Employee Home" page. Everything
else on that home reuses existing endpoints (tasks, requests, payslips, leave,
kudos, approvals, what's-new); only these did not exist yet:

  GET  /api/v1/timedoctor/my-hours/   → the logged-in employee's OWN tracked
                                        hours by window (today / yesterday /
                                        this & last week / this & last month)
                                        + a 14-day trend. Hours resolve through
                                        the CONFIRMED Time Doctor map (stable
                                        td_user_id) — never a name match (L19).
  GET  /api/v1/announcements/          → live company announcements for the reader
  POST /api/v1/announcements/          → create (HR / IT / C-suite only)
  PATCH/DELETE /api/v1/announcements/<id>/ → edit / retire (author-tier only)

The announcement model is the EXISTING hris.Announcement (already shown in the
daily brief) — we add the create/list API and a dashboard-appropriate window,
no new table.
"""
from __future__ import annotations

import datetime

from django.conf import settings
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from hris.models import Announcement


# ---------------------------------------------------------------------------
# Personal Time Doctor hours
# ---------------------------------------------------------------------------

def _my_employee(user):
    """The Employee row linked to this login by email, if any and not terminated."""
    from payroll.models import Employee
    email = (getattr(user, 'email', '') or '').strip()
    if not email:
        return None
    return (Employee.objects
            .exclude(status=Employee.Status.TERMINATED)
            .filter(email__iexact=email)
            .first())


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_hours(request):
    """The caller's own tracked hours, by window, from the stored daily
    snapshots. Own-scoped: an employee only ever sees their own figure.

    Hours resolve via the CONFIRMED TimeDoctorUserMap (stable td_user_id) — the
    id is looked up ONCE, then read straight from each snapshot payload, so the
    page never rebuilds the matcher per day. Accounts with no confirmed map fall
    back to the guarded TDMatcher resolver (never a bespoke name match — L19)."""
    from integrations.models import TimeDoctorDailySnapshot, TimeDoctorUserMap
    from integrations.td_matching import fold_uid, hours_by_employee
    from hris import workforce

    emp = _my_employee(request.user)
    today = timezone.localtime().date()
    first_this_month = today.replace(day=1)
    last_month_end = first_this_month - datetime.timedelta(days=1)
    first_last_month = last_month_end.replace(day=1)

    if emp is None:
        return Response({'linked': False, 'windows': {}, 'trend': []})

    # Resolve the confirmed stable id ONCE (fast path); None → guarded fallback.
    tmap = (TimeDoctorUserMap.objects
            .filter(employee=emp, confirmed=True)
            .exclude(td_user_id='')
            .first())
    my_uid = str(fold_uid(tmap.td_user_id)) if tmap else None

    snaps = (TimeDoctorDailySnapshot.objects
             .filter(as_of__gte=first_last_month, as_of__lte=today)
             .order_by('as_of'))

    hours_by_date: dict[datetime.date, float] = {}
    for s in snaps:
        payload = s.payload or []
        h = None
        if my_uid is not None:
            for m in payload:
                uid = m.get('user_id')
                if uid and str(fold_uid(uid)) == my_uid:
                    val = float(m.get('hours_tracked') or 0)
                    h = val if h is None else max(h, val)
        else:
            matched = hours_by_employee(payload, [emp], field='hours_tracked')
            h = matched.get(emp.id)
        if h is None:
            continue
        # A person is in one company's snapshot; if two rows ever carry them,
        # keep the larger (never sum — matches the busiest-machine rule).
        hours_by_date[s.as_of] = max(hours_by_date.get(s.as_of, 0.0), float(h))

    # The snapshot is frozen at the 06:30 pull and never receives a late upload,
    # so on its own this tile can show LESS than the permanent record — which is
    # exactly the complaint: "the morning brief indicates 3.24 hours, whereas the
    # Omni portal reflects only 1.7 hours" (bug c82def7f, reported from
    # /my-omni). WorkdayJustification is corrected upward by
    # reconcile_workday_records, so take whichever is higher and this tile can
    # never read below the figure the rest of Omni holds.
    from hris import hours_for_day
    for _d, _rec in hours_for_day.record_hours_by_date(
            emp, first_last_month, today).items():
        if _rec > hours_by_date.get(_d, 0.0):
            hours_by_date[_d] = _rec

    def _sum(d0: datetime.date, d1: datetime.date) -> float:
        return round(sum(v for dd, v in hours_by_date.items() if d0 <= dd <= d1), 1)

    yesterday = today - datetime.timedelta(days=1)
    monday = today - datetime.timedelta(days=today.weekday())
    last_monday = monday - datetime.timedelta(days=7)
    last_sunday = monday - datetime.timedelta(days=1)

    windows = {
        'today':      round(hours_by_date.get(today, 0.0), 1),
        'yesterday':  round(hours_by_date.get(yesterday, 0.0), 1),
        'this_week':  _sum(monday, today),
        'last_week':  _sum(last_monday, last_sunday),
        'this_month': _sum(first_this_month, today),
        'last_month': _sum(first_last_month, last_month_end),
    }

    trend = []
    for i in range(13, -1, -1):
        dd = today - datetime.timedelta(days=i)
        trend.append({'date': dd.isoformat(), 'hours': round(hours_by_date.get(dd, 0.0), 1)})

    # Required hours must account for public holidays, or the "explain today's
    # hours" banner falsely accuses people on a day off (Fable review).
    try:
        from hris.workforce_brief import holiday_off_dates
        off = holiday_off_dates()
    except Exception:   # noqa: BLE001
        off = set()
    try:
        # Managers are on the lighter daily requirement — pass is_manager or the
        # "explain today's hours" banner falsely accuses them (Fable review).
        from hris.workforce_roles import is_manager_hours_employee
        mgr = is_manager_hours_employee(emp)
    except Exception:   # noqa: BLE001
        mgr = False
    try:
        req_today = float(workforce.required_hours_for_date(today, off, is_manager=mgr))
    except Exception:   # noqa: BLE001
        req_today = 0.0
    windows['required_today'] = round(req_today, 1)
    windows['short_today'] = bool(req_today > 0 and windows['today'] < req_today)

    # Month-to-date EXPECTED vs ACHIEVED (CFO 2026-08-30, My Omni box). Expected =
    # sum of the same per-day requirement the "explain today" banner uses (so it
    # honours weekends, public holidays and the lighter manager requirement);
    # achieved = this_month tracked. A plain, honest "are you on target" figure.
    month_required = 0.0
    d = first_this_month
    while d <= today:
        try:
            month_required += float(workforce.required_hours_for_date(d, off, is_manager=mgr))
        except Exception:   # noqa: BLE001
            pass
        d += datetime.timedelta(days=1)
    windows['month_required'] = round(month_required, 1)
    windows['month_pct'] = (round(100 * windows['this_month'] / month_required)
                            if month_required else None)

    return Response({
        'linked': True,
        'employee': getattr(emp, 'full_name', ''),
        'windows': windows,
        'trend': trend,
    })


# ---------------------------------------------------------------------------
# Announcements  (reuses the existing hris.Announcement model)
# ---------------------------------------------------------------------------

# Categories an ordinary staff member may see on the dashboard. 'disciplinary'
# is a private, per-person HR matter — never surfaced on the company home.
DASHBOARD_CATEGORIES = ('announcement', 'hr_matter', 'meeting')
# Categories the compose form offers (same, disciplinary excluded on purpose).
POSTABLE_CATEGORIES = DASHBOARD_CATEGORIES


def can_make_announcement(user) -> bool:
    """HR, IT and C-suite may post company announcements. The ROLE is the gate,
    never a screen label (admin@ etc. is not authority)."""
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    try:
        from core.hris_access import hris_role
        # 'hris' = the HR team (Unami/Dorothy/Thapelo); 'hr' = finance leadership
        # who admin HRIS; both, plus admin/ceo, are authorised to post.
        if hris_role(user) in {'hr', 'hris', 'admin', 'ceo', 'superadmin'}:
            return True
    except Exception:   # noqa: BLE001
        pass
    profile = getattr(user, 'profile', None)
    if profile and getattr(profile, 'is_administrator', False):
        return True
    # IT: a Django group named "it", or the named-authors setting (execs / IT).
    try:
        if user.groups.filter(name__iexact='it').exists():
            return True
    except Exception:   # noqa: BLE001
        pass
    extra = {e.strip().lower() for e in getattr(settings, 'ANNOUNCEMENT_AUTHORS', []) if e}
    if (getattr(user, 'email', '') or '').strip().lower() in extra:
        return True
    return False


def _serialize(a: Announcement) -> dict:
    return {
        'id': str(a.id),
        'title': a.title,
        'body': a.body,
        'category': a.category,
        'category_label': a.get_category_display(),
        'starts_on': a.starts_on.isoformat() if a.starts_on else None,
        'ends_on': a.ends_on.isoformat() if a.ends_on else None,
        'is_active': a.is_active,
        'created_at': a.created_at.isoformat(),
    }


def _live_company_announcements(today: datetime.date):
    """Company-wide (audience is null), active, in-window, non-private items —
    the dashboard window (show until ends_on, or indefinitely if blank)."""
    from django.db.models import Q
    return (Announcement.objects
            .filter(audience__isnull=True, is_active=True,
                    category__in=DASHBOARD_CATEGORIES,
                    starts_on__lte=today)
            .filter(Q(ends_on__isnull=True) | Q(ends_on__gte=today))
            .order_by('-starts_on', 'category'))


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def announcements_view(request):
    can_make = can_make_announcement(request.user)
    today = timezone.localtime().date()

    if request.method == 'GET':
        manage = request.query_params.get('manage') in ('1', 'true', 'yes')
        if manage and can_make:
            # Authors managing the board see every company-wide item (incl. retired
            # and future), so they can edit/retire — still never private HR items.
            rows = list(Announcement.objects.filter(
                audience__isnull=True, category__in=DASHBOARD_CATEGORIES
            ).order_by('-starts_on'))
        else:
            rows = list(_live_company_announcements(today))
        return Response({'announcements': [_serialize(a) for a in rows], 'can_make': can_make})

    # POST — create
    if not can_make:
        return Response({'detail': 'Only HR, IT or the executive team can post announcements.'},
                        status=status.HTTP_403_FORBIDDEN)
    body = request.data if isinstance(request.data, dict) else {}
    title = (body.get('title') or '').strip()
    text = (body.get('body') or '').strip()
    if not title:
        return Response({'detail': 'A title is required.'}, status=status.HTTP_400_BAD_REQUEST)
    category = (body.get('category') or 'announcement').strip()
    if category not in POSTABLE_CATEGORIES:
        category = 'announcement'

    ends_on = None
    raw_end = (body.get('ends_on') or '').strip()
    if raw_end:
        try:
            ends_on = datetime.datetime.strptime(raw_end, '%Y-%m-%d').date()
        except ValueError:
            return Response({'detail': 'ends_on must be YYYY-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST)
        if ends_on < today:
            return Response({'detail': 'ends_on cannot be in the past.'}, status=status.HTTP_400_BAD_REQUEST)

    a = Announcement.objects.create(
        title=title[:160],
        body=text,
        category=category,
        audience=None,          # company-wide
        starts_on=today,
        ends_on=ends_on,
        is_active=True,
    )
    return Response({'announcement': _serialize(a), 'can_make': True},
                    status=status.HTTP_201_CREATED)


@api_view(['PATCH', 'DELETE'])
@permission_classes([IsAuthenticated])
def announcement_detail(request, pk):
    if not can_make_announcement(request.user):
        return Response({'detail': 'Only HR, IT or the executive team can manage announcements.'},
                        status=status.HTTP_403_FORBIDDEN)
    # Only company-wide items are managed here — never a private HR/disciplinary row.
    a = Announcement.objects.filter(pk=pk, audience__isnull=True,
                                    category__in=DASHBOARD_CATEGORIES).first()
    if a is None:
        return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

    if request.method == 'DELETE':
        a.is_active = False
        a.save(update_fields=['is_active', 'updated_at'])
        return Response({'announcement': _serialize(a)})

    body = request.data if isinstance(request.data, dict) else {}
    fields = []
    if 'is_active' in body:
        a.is_active = bool(body.get('is_active')); fields.append('is_active')
    if 'title' in body and (body.get('title') or '').strip():
        a.title = (body.get('title') or '').strip()[:160]; fields.append('title')
    if 'body' in body:
        a.body = (body.get('body') or '').strip(); fields.append('body')
    if fields:
        fields.append('updated_at')
        a.save(update_fields=fields)
    return Response({'announcement': _serialize(a)})


# ---------------------------------------------------------------------------
# 5-day Gaborone weather (My Omni box, CFO 2026-08-30)
# ---------------------------------------------------------------------------

# Open-Meteo: free, no API key, no personal data (only Gaborone's coordinates
# leave). Cached ~3h and fails SOFT (a weather widget must never break the home
# page or hammer the service). WMO weather codes -> a plain label + emoji.
_WMO = {
    0: ('Clear', '☀️'), 1: ('Mainly clear', '\U0001f324️'),
    2: ('Partly cloudy', '⛅'), 3: ('Cloudy', '☁️'),
    45: ('Fog', '\U0001f32b️'), 48: ('Fog', '\U0001f32b️'),
    51: ('Light drizzle', '\U0001f327️'), 53: ('Drizzle', '\U0001f327️'),
    55: ('Drizzle', '\U0001f327️'), 61: ('Light rain', '\U0001f327️'),
    63: ('Rain', '\U0001f327️'), 65: ('Heavy rain', '⛈️'),
    80: ('Showers', '\U0001f326️'), 81: ('Showers', '\U0001f326️'),
    82: ('Heavy showers', '⛈️'), 95: ('Thunderstorm', '⛈️'),
    96: ('Thunderstorm', '⛈️'), 99: ('Thunderstorm', '⛈️'),
}


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def weather(request):
    """GET /api/v1/my-omni/weather/ — 5-day Gaborone forecast for the My Omni box.
    High/low, and an advance heads-up when a day is windy or likely to rain."""
    import json
    import urllib.request
    from django.core.cache import cache

    cached = cache.get('my_omni_weather_v2')
    if cached is not None:
        return Response(cached)

    url = ('https://api.open-meteo.com/v1/forecast'
           '?latitude=-24.6282&longitude=25.9231'
           '&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,'
           'wind_speed_10m_max,weather_code'
           '&timezone=Africa%2FGaborone&forecast_days=5')
    try:
        with urllib.request.urlopen(url, timeout=6) as r:  # noqa: S310 — fixed https host
            raw = json.loads(r.read().decode('utf-8'))
        d = raw.get('daily', {}) or {}
        times = d.get('time', []) or []
        days = []
        for i in range(len(times)):
            code = (d.get('weather_code') or [None] * len(times))[i]
            label, emoji = _WMO.get(code, ('—', '\U0001f321️'))
            rain = (d.get('precipitation_probability_max') or [0] * len(times))[i] or 0
            wind = (d.get('wind_speed_10m_max') or [0] * len(times))[i] or 0
            warns = []
            if rain >= 50:
                warns.append('rain')
            if wind >= 35:
                warns.append('windy')
            days.append({
                'date':    times[i],
                'high':    round((d.get('temperature_2m_max') or [0] * len(times))[i]),
                'low':     round((d.get('temperature_2m_min') or [0] * len(times))[i]),
                'rain_pct': int(rain),
                'wind_kmh': round(wind),
                'label':   label,
                'emoji':   emoji,
                'warn':    warns,
            })
        out = {'ok': True, 'place': 'Gaborone', 'days': days}
        cache.set('my_omni_weather_v2', out, 60 * 60 * 3)
        return Response(out)
    except Exception:   # noqa: BLE001 — weather must fail soft
        return Response({'ok': False, 'place': 'Gaborone', 'days': []})
