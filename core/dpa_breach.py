"""
core/dpa_breach.py — data-breach / incident register + 72-hour IDPC clock (DPA
audit S-6). Logged by the DPO / C-suite (same access as the dashboard). CFO 2026-07-19.
"""
from __future__ import annotations

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.dpa_dashboard import can_view_dpa_dashboard


def _row(b):
    return {
        'id': b.id, 'title': b.title, 'description': b.description,
        'discovered_at': b.discovered_at.isoformat() if b.discovered_at else None,
        'severity': b.severity, 'reportable': b.reportable, 'status': b.status,
        'idpc_notified': b.idpc_notified,
        'idpc_notified_at': b.idpc_notified_at.isoformat() if b.idpc_notified_at else None,
        'subjects_notified': b.subjects_notified, 'remedial': b.remedial,
        'notify_deadline': b.notify_deadline.isoformat() if b.notify_deadline else None,
        'hours_left': b.hours_left, 'overdue': b.overdue,
    }


def breach_summary() -> dict:
    from core.models import BreachIncident
    qs = BreachIncident.objects.all()
    open_qs = list(qs.exclude(status=BreachIncident.Status.CLOSED))
    return {'total': qs.count(), 'open': len(open_qs),
            'overdue': sum(1 for b in open_qs if b.overdue)}


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def breaches(request):
    if not can_view_dpa_dashboard(request.user):
        return Response({'detail': 'Restricted.'}, status=403)
    from core.models import BreachIncident
    from django.utils import timezone
    from django.utils.dateparse import parse_datetime
    if request.method == 'POST':
        d = request.data
        title = (d.get('title') or '').strip()
        if not title:
            return Response({'detail': 'A short title is required.'}, status=400)
        disc = parse_datetime(d.get('discovered_at') or '') if d.get('discovered_at') else None
        sev = d.get('severity') if d.get('severity') in dict(BreachIncident.Severity.choices) else 'medium'
        b = BreachIncident.objects.create(
            title=title[:200], description=str(d.get('description', ''))[:5000],
            discovered_at=disc or timezone.now(), severity=sev,
            reportable=bool(d.get('reportable', True)), created_by=request.user)
        return Response(_row(b), status=201)
    return Response({'incidents': [_row(b) for b in BreachIncident.objects.all()[:100]],
                     'summary': breach_summary()})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def breach_update(request, pk):
    if not can_view_dpa_dashboard(request.user):
        return Response({'detail': 'Restricted.'}, status=403)
    from core.models import BreachIncident
    from django.utils import timezone
    b = BreachIncident.objects.filter(pk=pk).first()
    if not b:
        return Response({'detail': 'Not found.'}, status=404)
    d = request.data
    if d.get('status') in dict(BreachIncident.Status.choices):
        b.status = d['status']
    if d.get('idpc_notified') and not b.idpc_notified:
        b.idpc_notified = True
        b.idpc_notified_at = timezone.now()
    if 'subjects_notified' in d:
        b.subjects_notified = bool(d['subjects_notified'])
    if 'remedial' in d:
        b.remedial = str(d['remedial'])[:5000]
    b.save()
    return Response(_row(b))
