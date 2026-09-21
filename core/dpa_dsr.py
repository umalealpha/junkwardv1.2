"""
core/dpa_dsr.py — data-subject request register + statutory clock (DPA audit H-3).
Access / correct / delete / restrict / object / portability. Logged by DPO/HR
(same access as the dashboard). CFO directive 2026-07-20.
"""
from __future__ import annotations

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.dpa_dashboard import can_view_dpa_dashboard


def _row(r):
    return {
        'id': r.id, 'subject_name': r.subject_name, 'subject_email': r.subject_email,
        'subject_type': r.subject_type, 'kind': r.kind, 'details': r.details, 'status': r.status,
        'received_at': r.received_at.isoformat() if r.received_at else None,
        'due_date': r.due_date.isoformat() if r.due_date else None,
        'resolution': r.resolution, 'days_left': r.days_left, 'overdue': r.overdue,
    }


def dsr_summary() -> dict:
    from core.models import DataSubjectRequest as D
    qs = D.objects.all()
    openq = list(qs.filter(status__in=[D.Status.OPEN, D.Status.IN_PROGRESS]))
    return {'total': qs.count(), 'open': len(openq), 'overdue': sum(1 for r in openq if r.overdue)}


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def dsrs(request):
    if not can_view_dpa_dashboard(request.user):
        return Response({'detail': 'Restricted.'}, status=403)
    from core.models import DataSubjectRequest as D
    from django.utils import timezone
    from datetime import timedelta
    if request.method == 'POST':
        d = request.data
        name = (d.get('subject_name') or '').strip()
        if not name:
            return Response({'detail': 'Subject name is required.'}, status=400)
        kind = d.get('kind') if d.get('kind') in dict(D.Kind.choices) else 'access'
        now = timezone.now()
        r = D.objects.create(
            subject_name=name[:200], subject_email=str(d.get('subject_email', ''))[:254],
            subject_type=('staff' if d.get('subject_type') == 'staff' else 'customer'),
            kind=kind, details=str(d.get('details', ''))[:5000], received_at=now,
            due_date=(now + timedelta(days=D.STATUTORY_DAYS)).date(), handled_by=request.user)
        return Response(_row(r), status=201)
    return Response({'requests': [_row(r) for r in D.objects.all()[:100]], 'summary': dsr_summary()})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def dsr_update(request, pk):
    if not can_view_dpa_dashboard(request.user):
        return Response({'detail': 'Restricted.'}, status=403)
    from core.models import DataSubjectRequest as D
    r = D.objects.filter(pk=pk).first()
    if not r:
        return Response({'detail': 'Not found.'}, status=404)
    d = request.data
    if d.get('status') in dict(D.Status.choices):
        r.status = d['status']
    if 'resolution' in d:
        r.resolution = str(d['resolution'])[:5000]
    r.save()
    return Response(_row(r))
