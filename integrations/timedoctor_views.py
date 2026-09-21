"""
integrations/timedoctor_views.py

Read API for the omni Time Doctor productivity report.

  GET /api/v1/timedoctor/daily/            → latest stored snapshot
  GET /api/v1/timedoctor/daily/?date=Y-M-D → that day's snapshot
  GET /api/v1/timedoctor/history/?days=30  → company totals time-series

Gated to managers / HR / admin / superuser — employee-monitoring data is not
for the whole company. Returns the privacy-safe aggregate only (no raw titles).
"""
from __future__ import annotations

from datetime import datetime

from django.conf import settings
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from integrations.models import TimeDoctorDailySnapshot


def _can_view(user) -> bool:
    if user.is_superuser or user.is_staff:
        return True
    try:
        from core.hris_access import hris_role
        return hris_role(user) in {'mgr', 'hr', 'hris', 'admin', 'superadmin', 'ceo'}
    except Exception:    # noqa: BLE001
        return False


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
def timedoctor_run(request):
    """Server-side full pull, given a fresh token (from n8n's login). Keeps the
    n8n workflow to 3 nodes (schedule → login → hand omni the token) and keeps
    raw activity titles on the box. Shared-secret auth (X-TD-Ingest-Key).

      POST /api/v1/timedoctor/run/
      Header: X-TD-Ingest-Key: <key>
      Body:   { token: '<jwt from /login>', company_id?: '', as_of?: 'YYYY-MM-DD',
                days?: 1, email?: true }
    """
    expected = getattr(settings, 'TIMEDOCTOR_INGEST_KEY', '') or ''
    if not expected or request.META.get('HTTP_X_TD_INGEST_KEY', '') != expected:
        return Response({'detail': 'Invalid or missing X-TD-Ingest-Key.'},
                        status=status.HTTP_401_UNAUTHORIZED)
    body = request.data if isinstance(request.data, dict) else {}
    token = (body.get('token') or '').strip()
    if not token:
        return Response({'detail': 'token (from Time Doctor /login) is required.'},
                        status=status.HTTP_400_BAD_REQUEST)

    import datetime as _dt
    from integrations.timedoctor import TimeDoctorClient, TimeDoctorError, aggregate, build_email_html
    company = (body.get('company_id') or getattr(settings, 'TIMEDOCTOR_COMPANY_ID', '') or '').strip()
    client = TimeDoctorClient(token=token, company_id=company,
                              base=getattr(settings, 'TIMEDOCTOR_API_BASE', 'https://api2.timedoctor.com'),
                              timeout=int(getattr(settings, 'TIMEDOCTOR_TIMEOUT_SECONDS', 45)))
    if not client.configured:
        return Response({'detail': 'company_id missing (set TIMEDOCTOR_COMPANY_ID or pass company_id).'},
                        status=status.HTTP_400_BAD_REQUEST)

    raw_as_of = (body.get('as_of') or '').strip()
    if raw_as_of:
        try:
            day = datetime.strptime(raw_as_of, '%Y-%m-%d').date()
        except ValueError:
            return Response({'detail': 'as_of must be YYYY-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST)
    else:
        day = timezone.localtime().date() - _dt.timedelta(days=1)
    span = max(int(body.get('days') or 1), 1)
    d_from = day - _dt.timedelta(days=span - 1)
    d_to = day + _dt.timedelta(days=1)

    try:
        users = client.users()
        ids = [u.get('id') for u in users if u.get('id')]
        agg = aggregate(users, client.worklog(d_from, d_to, user_ids=ids),
                        client.timeuse(d_from, d_to, user_ids=ids),
                        client.projects(), client.tasks(), as_of=day, td_user_ids=ids)
    except TimeDoctorError as exc:
        return Response({'detail': f'Time Doctor pull failed: {exc}'}, status=status.HTTP_502_BAD_GATEWAY)

    totals, members = agg['totals'], agg['members']
    snap, _ = TimeDoctorDailySnapshot.objects.update_or_create(
        company_id=company, as_of=day, defaults={'totals': totals, 'payload': members})
    emailed = False
    if body.get('email', True):
        try:
            from core.notifications import send_html_with_cfo_cc
            recips = list(getattr(settings, 'TIMEDOCTOR_EMAIL_TO', []) or []) or ['pganesharajah@alphadirect.co.bw']
            send_html_with_cfo_cc(subject=f'Daily Workforce Report — {day}',
                                  html=build_email_html(totals, members), to=recips)
            emailed = True
            snap.emailed = True
            snap.save(update_fields=['emailed', 'updated_at'])
        except Exception:    # noqa: BLE001
            pass
    return Response({'stored': True, 'as_of': day.isoformat(), 'totals': totals, 'emailed': emailed})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def timedoctor_daily(request):
    if not _can_view(request.user):
        return Response({'detail': 'Time Doctor reports are restricted to managers / HR.'},
                        status=status.HTTP_403_FORBIDDEN)
    qs = TimeDoctorDailySnapshot.objects.all()
    d = (request.query_params.get('date') or '').strip()
    if d:
        try:
            day = datetime.strptime(d, '%Y-%m-%d').date()
        except ValueError:
            return Response({'detail': 'date must be YYYY-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST)
        snap = qs.filter(as_of=day).first()
    else:
        snap = qs.first()   # ordering = -as_of
    if snap is None:
        return Response({'detail': 'No Time Doctor snapshot yet. The daily pull has not run '
                                   '(or TIMEDOCTOR_TOKEN is not set).',
                         'totals': None, 'members': []})
    # Rows raised to the permanent record before anyone reads them. The stored
    # snapshot freezes at the 06:30 pull and Time Doctor back-fills a machine
    # that was offline, so this report could show a manager fewer hours than
    # Omni itself holds for that person (checklist L27/L28).
    from hris import hours_for_day
    return Response({
        'as_of':   snap.as_of.isoformat(),
        'totals':  snap.totals,
        'members': hours_for_day.floored_rows(snap.as_of, snap.payload),
        'emailed': snap.emailed,
    })


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
def timedoctor_ingest(request):
    """Receive a raw Time Doctor pull from the n8n workflow, aggregate it
    server-side (privacy-safe — raw titles discarded), store the daily snapshot,
    and email the summary. Authenticated by a shared secret header, NOT a user
    session — n8n holds the Time Doctor login in its own encrypted vault and
    POSTs here.

      POST /api/v1/timedoctor/ingest/
      Header: X-TD-Ingest-Key: <TIMEDOCTOR_INGEST_KEY>
      Body:   { as_of?: 'YYYY-MM-DD', users:[], worklog:[], timeuse:[],
                projects:[], tasks:[], email?: true }
    """
    expected = getattr(settings, 'TIMEDOCTOR_INGEST_KEY', '') or ''
    supplied = request.META.get('HTTP_X_TD_INGEST_KEY', '')
    if not expected or supplied != expected:
        return Response({'detail': 'Invalid or missing X-TD-Ingest-Key.'},
                        status=status.HTTP_401_UNAUTHORIZED)

    from integrations.timedoctor import aggregate, build_email_html
    body = request.data if isinstance(request.data, dict) else {}
    raw_as_of = (body.get('as_of') or '').strip()
    if raw_as_of:
        try:
            as_of = datetime.strptime(raw_as_of, '%Y-%m-%d').date()
        except ValueError:
            return Response({'detail': 'as_of must be YYYY-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST)
    else:
        as_of = timezone.localtime().date()

    agg = aggregate(
        body.get('users') or [], body.get('worklog') or [], body.get('timeuse') or [],
        body.get('projects') or [], body.get('tasks') or [], as_of=as_of,
    )
    totals, members = agg['totals'], agg['members']
    company_id = (body.get('company_id') or getattr(settings, 'TIMEDOCTOR_COMPANY_ID', '') or 'default')

    snap, _ = TimeDoctorDailySnapshot.objects.update_or_create(
        company_id=company_id, as_of=as_of,
        defaults={'totals': totals, 'payload': members},
    )

    emailed = False
    if body.get('email', True):
        try:
            from core.notifications import send_html_with_cfo_cc
            recips = list(getattr(settings, 'TIMEDOCTOR_EMAIL_TO', []) or []) or ['pganesharajah@alphadirect.co.bw']
            send_html_with_cfo_cc(subject=f'Daily Workforce Report — {as_of}',
                                  html=build_email_html(totals, members), to=recips)
            emailed = True
            snap.emailed = True
            snap.save(update_fields=['emailed', 'updated_at'])
        except Exception:    # noqa: BLE001
            pass

    return Response({'stored': True, 'as_of': as_of.isoformat(),
                     'totals': totals, 'emailed': emailed})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def timedoctor_reconciliation(request):
    """Time Doctor tracked hours vs payroll + adoption gap, last N months.
    ?months=3 (default), ?insight=1 to include a DeepSeek/Gemini commentary
    (anonymised, cached 6h). Role-gated to managers / HR."""
    if not _can_view(request.user):
        return Response({'detail': 'Time Doctor reports are restricted to managers / HR.'},
                        status=status.HTTP_403_FORBIDDEN)
    from integrations.timedoctor_recon import (
        build_reconciliation, deepseek_insight, compute_exceptions,
        monthly_rollup, by_department, payroll_coverage,
    )
    try:
        months = min(max(int(request.query_params.get('months') or 3), 1), 12)
    except ValueError:
        months = 3
    recon = build_reconciliation(months=months)
    recon['exceptions'] = compute_exceptions(recon['rows'])
    recon['by_department'] = by_department(recon['rows'])
    recon['coverage'] = payroll_coverage(recon['rows'])
    recon['monthly'] = monthly_rollup(months=max(months, 6))

    # Feed health (CFO 2026-07-14): this dashboard silently rendered a single
    # 28-day-old snapshot as if it were "last 3 months" live data. Surface the
    # real freshness so nobody acts on stale numbers. live = pulled within 2 days.
    latest = TimeDoctorDailySnapshot.objects.order_by('-as_of').first()
    today = timezone.localtime().date()
    age = (today - latest.as_of).days if latest else None
    recon['feed'] = {
        'latest_as_of':   latest.as_of.isoformat() if latest else None,
        'data_age_days':  age,
        'snapshot_count': TimeDoctorDailySnapshot.objects.count(),
        'live':           bool(latest and age is not None and age <= 2),
    }

    insight = None
    if request.query_params.get('insight') in ('1', 'true', 'yes'):
        from django.core.cache import cache
        ck = f'td_insight_m{months}'
        insight = cache.get(ck)
        if insight is None:
            insight = deepseek_insight(recon)
            cache.set(ck, insight, 60 * 60 * 6)
    return Response({**recon, 'insight': insight})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def timedoctor_history(request):
    if not _can_view(request.user):
        return Response({'detail': 'Time Doctor reports are restricted to managers / HR.'},
                        status=status.HTTP_403_FORBIDDEN)
    try:
        days = min(int(request.query_params.get('days') or 30), 180)
    except ValueError:
        days = 30
    rows = (TimeDoctorDailySnapshot.objects.all()
            .order_by('-as_of')[:days])
    series = [{
        'as_of':            s.as_of.isoformat(),
        'total_hours':      (s.totals or {}).get('total_hours'),
        'active_users':     (s.totals or {}).get('active_users'),
        'productive_pct':   (s.totals or {}).get('productive_pct'),
    } for s in reversed(list(rows))]
    return Response({'days': len(series), 'series': series})
