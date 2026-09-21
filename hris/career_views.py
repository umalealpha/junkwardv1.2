"""hris/career_views.py — Career Tracks (promotion-readiness records).

CFO directive 2026-07-13, triggered by M. Tlagae's development-path request
to HR: the HRIS must hold, per employee, the target role and the concrete
business milestones that must be achieved before the move is considered.

Endpoints (same three-gate model as the rest of the talent cluster):

  GET   /hris/api/talent/career-tracks/                      → list (view_talent)
  POST  /hris/api/talent/career-tracks/                      → create (HR tier)
  PATCH /hris/api/talent/career-tracks/milestones/<uuid:id>/ → update (HR tier)

Reads are company-scoped like every other HRIS surface. Writes are limited
to the HR tier (hr / hris / ceo / admin / superadmin).
"""
from __future__ import annotations

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.hris_access import hris_role
from hris.feature_views import _gate
from hris.models import CareerMilestone, CareerTrack
from payroll.models import Employee

MANAGE_ROLES = frozenset({'hr', 'hris', 'ceo', 'admin', 'superadmin'})


def _can_manage(user) -> bool:
    return hris_role(user) in MANAGE_ROLES


def _track_dict(t: CareerTrack) -> dict:
    return {
        'id': str(t.id),
        'employee': {
            'id': str(t.employee_id),
            'name': t.employee.full_name,
            'number': t.employee.employee_number,
            'title': t.employee.job_title,
            'department': t.employee.department,
        },
        'target_role': t.target_role,
        'status': t.status,
        'context': t.context,
        'created_by': t.created_by_name,
        'created_at': t.created_at.isoformat() if t.created_at else None,
        'milestones': [
            {
                'id': str(m.id),
                'order': m.order,
                'description': m.description,
                'status': m.status,
                'evidence': m.evidence,
            }
            for m in t.milestones.all()
        ],
    }


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def career_tracks(request):
    denied = _gate(request, capability='view_talent')
    if denied is not None:
        return denied
    from core.mixins import apply_company_scope

    if request.method == 'GET':
        qs = (CareerTrack.objects
              .select_related('employee')
              .prefetch_related('milestones'))
        qs = apply_company_scope(request, qs, 'employee__company_id')
        return Response({
            'can_manage': _can_manage(request.user),
            'tracks': [_track_dict(t) for t in qs],
        })

    # POST — create a track (HR tier only).
    if not _can_manage(request.user):
        return Response({'detail': 'Only HR can create career tracks.'},
                        status=status.HTTP_403_FORBIDDEN)
    data = request.data or {}
    target_role = str(data.get('target_role') or '').strip()
    if not target_role:
        return Response({'detail': 'target_role is required.'},
                        status=status.HTTP_400_BAD_REQUEST)

    emp_qs = apply_company_scope(request, Employee.objects.all(), 'company_id')
    emp = None
    if data.get('employee_id'):
        emp = emp_qs.filter(pk=data['employee_id']).first()
    elif data.get('employee_email'):
        emp = emp_qs.filter(
            email__iexact=str(data['employee_email']).strip()).first()
    if emp is None:
        return Response({'detail': 'Employee not found.'},
                        status=status.HTTP_400_BAD_REQUEST)

    full_name = ''
    try:
        full_name = request.user.get_full_name() or request.user.username
    except Exception:  # noqa: BLE001
        full_name = getattr(request.user, 'username', '')
    track = CareerTrack.objects.create(
        employee=emp,
        target_role=target_role,
        context=str(data.get('context') or '').strip(),
        created_by_name=(full_name or '')[:120],
    )
    descriptions = [str(d).strip() for d in (data.get('milestones') or [])
                    if str(d).strip()]
    for i, desc in enumerate(descriptions, start=1):
        CareerMilestone.objects.create(track=track, order=i, description=desc)
    return Response(_track_dict(track), status=status.HTTP_201_CREATED)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def update_milestone(request, milestone_id):
    denied = _gate(request, capability='view_talent')
    if denied is not None:
        return denied
    if not _can_manage(request.user):
        return Response({'detail': 'Only HR can update milestones.'},
                        status=status.HTTP_403_FORBIDDEN)
    from core.mixins import apply_company_scope

    qs = CareerMilestone.objects.select_related('track', 'track__employee')
    qs = apply_company_scope(request, qs, 'track__employee__company_id')
    ms = qs.filter(pk=milestone_id).first()
    if ms is None:
        return Response({'detail': 'Milestone not found.'},
                        status=status.HTTP_404_NOT_FOUND)

    data = request.data or {}
    new_status = data.get('status')
    if new_status:
        valid = {c[0] for c in CareerMilestone.Status.choices}
        if new_status not in valid:
            return Response(
                {'detail': f'status must be one of {sorted(valid)}.'},
                status=status.HTTP_400_BAD_REQUEST)
        ms.status = new_status
    if 'evidence' in data:
        ms.evidence = str(data.get('evidence') or '').strip()
    ms.save(audit_user=request.user)
    return Response({
        'ok': True,
        'milestone': {
            'id': str(ms.id),
            'order': ms.order,
            'description': ms.description,
            'status': ms.status,
            'evidence': ms.evidence,
        },
    })
