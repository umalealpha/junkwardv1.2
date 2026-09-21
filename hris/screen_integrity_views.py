"""
hris/screen_integrity_views.py

The CFO's Screen-Integrity monitor (2026-09-06): pull the frozen-screen /
weight-on-a-key exceptions (heavy typing on a screen that never changes) from
one Omni screen instead of re-running the Time Doctor sweep by hand.

Reads the stored daily sweep (integrations.ScreenIntegrityScan/Flag, written by
the nightly cron) so the page loads instantly; a "re-scan (live)" button pulls
one day straight from Time Doctor and re-persists it.

Gate: HR / admin / CEO / superadmin only (the CFO resolves to 'hr'). Managers
and ordinary staff cannot see it. Privacy (AD-POL-AI-GOV-001): names + counts
only — no window titles, no images.
"""
from __future__ import annotations

import datetime

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.hris_access import hris_role
from integrations.models import ScreenIntegrityScan
from django.utils import timezone

_VIEW_ROLES = {'hr', 'admin', 'ceo', 'superadmin'}
_MAX_DAYS = 120


def _can_view(user) -> bool:
    return hris_role(user) in _VIEW_ROLES


def _flag_json(f) -> dict:
    return {
        'name':                f.name or '—',
        'suspicion':           f.suspicion,
        'shots':               f.shots,
        'frozen_typing_pct':   float(f.frozen_typing_pct),
        'frozen_typing_hours': float(f.frozen_typing_hours),
        'mouse_dead_pct':      float(f.mouse_dead_pct),
        'identical_pct':       float(f.identical_pct),
        'reasons':             list(f.reasons or []),
    }


def _scan_json(scan) -> dict:
    return {
        'day':            scan.day.isoformat(),
        'people_checked': scan.people_checked,
        'suspicious':     scan.suspicious,
        'watch':          scan.watch,
        'status':         scan.status,
        'note':           scan.note,
        'ran_at':         scan.ran_at.isoformat() if scan.ran_at else None,
        'flags':          [_flag_json(f) for f in scan.flags.all()],
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def screen_integrity_list(request):
    """Stored daily sweeps, newest first. ?days=N (default 14) or ?from&to."""
    if not _can_view(request.user):
        return Response({'detail': 'Not permitted.'}, status=403)

    qs = ScreenIntegrityScan.objects.prefetch_related('flags').all()
    d_from = (request.GET.get('from') or '').strip()
    d_to = (request.GET.get('to') or '').strip()
    try:
        if d_from:
            qs = qs.filter(day__gte=datetime.date.fromisoformat(d_from))
        if d_to:
            qs = qs.filter(day__lte=datetime.date.fromisoformat(d_to))
    except ValueError:
        return Response({'detail': 'Bad date. Use YYYY-MM-DD.'}, status=400)

    if not d_from and not d_to:
        try:
            days = min(max(int(request.GET.get('days', 14)), 1), _MAX_DAYS)
        except (TypeError, ValueError):
            days = 14
        qs = qs[:days]

    scans = list(qs)
    totals = {
        'days':       len(scans),
        'suspicious': sum(s.suspicious for s in scans),
        'watch':      sum(s.watch for s in scans),
    }
    return Response({'scans': [_scan_json(s) for s in scans], 'totals': totals})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def screen_integrity_rescan(request):
    """Pull ONE day live from Time Doctor, re-persist it, return that day."""
    if not _can_view(request.user):
        return Response({'detail': 'Not permitted.'}, status=403)

    raw = (request.data.get('date') or '').strip()
    try:
        day = datetime.date.fromisoformat(raw)
    except (TypeError, ValueError):
        return Response({'detail': 'Bad date. Use YYYY-MM-DD.'}, status=400)
    if day > timezone.localdate():
        return Response({'detail': 'That day is in the future.'}, status=400)

    from integrations.timedoctor import TimeDoctorClient, TimeDoctorError
    from integrations.td_screenshot_integrity import analyze_day
    from integrations.screen_integrity_store import persist_day
    from integrations.management.commands.detect_frozen_screen import (
        _pull_files, _sast_day_bounds,
    )

    client = TimeDoctorClient.from_settings()
    if not client.configured:
        return Response({'detail': 'Time Doctor is not configured.'}, status=503)

    users = client.users()
    ids = [u.get('id') for u in users if u.get('id')]
    d_from, d_to = _sast_day_bounds(day)
    try:
        files = _pull_files(client, d_from, d_to, ids)
    except TimeDoctorError as exc:
        persist_day(day, [], status=ScreenIntegrityScan.Status.FAILED, note=str(exc)[:180])
        return Response({'detail': f'Time Doctor pull failed: {exc}'}, status=502)

    sigs = analyze_day(files, users)
    status = (ScreenIntegrityScan.Status.OK if sigs
              else ScreenIntegrityScan.Status.NO_DATA)
    scan = persist_day(day, sigs, status=status)
    scan = ScreenIntegrityScan.objects.prefetch_related('flags').get(pk=scan.pk)
    return Response(_scan_json(scan))
