"""
core/frozen_views.py — API for the frozen-component governance control.

  GET  /api/v1/frozen/components/            list frozen components + open-request counts
  GET  /api/v1/frozen/requests/              list change requests (newest first)
  POST /api/v1/frozen/requests/              file a request (Finance team + admins)
  POST /api/v1/frozen/requests/<id>/decide/  CFO approve/reject + mandatory comment

Access:
  * file    — Finance team / administrators (mirrors Payables/Smart Entry makers)
  * decide  — CFO only (superuser or title 'cfo')
Every action is audited inside core.frozen_controls.
"""
from __future__ import annotations

from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import FrozenComponent, FrozenChangeRequest, get_user_profile
from .frozen_controls import (
    FROZEN_MESSAGE, file_change_request, decide_change_request,
)


def _is_admin(user) -> bool:
    if getattr(user, 'is_superuser', False):
        return True
    p = get_user_profile(user)
    return bool(p and getattr(p, 'is_administrator', False))


def _can_file(user) -> bool:
    """Finance team + admins may file a request."""
    if _is_admin(user):
        return True
    p = get_user_profile(user)
    if p is None:
        return False
    title = (getattr(p, 'title', '') or '').lower()
    dept  = (getattr(p, 'department', '') or '').lower()
    return title in {'cfo', 'finance_manager', 'financial_controller'} or 'financ' in dept


def _can_decide(user) -> bool:
    """Only the CFO decides frozen-change requests."""
    if getattr(user, 'is_superuser', False):
        return True
    p = get_user_profile(user)
    return bool(p and (getattr(p, 'title', '') or '').lower() == 'cfo')


def _req_dict(r: FrozenChangeRequest) -> dict:
    return {
        'id':               str(r.id),
        'component_key':    r.component.key,
        'component_label':  r.component.label,
        'summary':          r.summary,
        'reason':           r.reason,
        'board_impact':     r.board_impact,
        'status':           r.status,
        'status_display':   r.get_status_display(),
        'requested_by':     (r.requested_by.get_full_name() or r.requested_by.username) if r.requested_by else '—',
        'decided_by':       (r.decided_by.get_full_name() or r.decided_by.username) if r.decided_by else None,
        'decision_comment': r.decision_comment or None,
        'consumed':         r.consumed,
        'created_at':       r.created_at.isoformat(),
        'decided_at':       r.decided_at.isoformat() if r.decided_at else None,
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def frozen_components(request):
    rows = []
    for c in FrozenComponent.objects.all():
        rows.append({
            'key':          c.key,
            'label':        c.label,
            'description':  c.description,
            'is_frozen':    c.is_frozen,
            'pending':      c.change_requests.filter(status=FrozenChangeRequest.Status.PENDING).count(),
        })
    return Response({
        'components': rows,
        'message':    FROZEN_MESSAGE,
        'can_file':   _can_file(request.user),
        'can_decide': _can_decide(request.user),
    })


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def frozen_requests(request):
    if request.method == 'GET':
        qs = (FrozenChangeRequest.objects.select_related('component', 'requested_by', 'decided_by')
              .all()[:200])
        return Response({'requests': [_req_dict(r) for r in qs]})

    # POST — file a new request
    if not _can_file(request.user):
        raise PermissionDenied('Filing a frozen-change request is restricted to Finance team and administrators.')
    d = request.data or {}
    req = file_change_request(
        component_key=(d.get('component_key') or '').strip(),
        user=request.user,
        summary=d.get('summary', ''),
        reason=d.get('reason', ''),
        board_impact=d.get('board_impact', ''),
    )
    return Response(_req_dict(req), status=201)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def frozen_request_decide(request, request_id):
    if not _can_decide(request.user):
        raise PermissionDenied('Only the CFO can approve or reject a frozen-change request.')
    from django.shortcuts import get_object_or_404
    req = get_object_or_404(FrozenChangeRequest, pk=request_id)
    d = request.data or {}
    decision = (d.get('decision') or '').strip().lower()
    if decision not in ('approve', 'reject'):
        return Response({'detail': "decision must be 'approve' or 'reject'."}, status=400)
    req = decide_change_request(
        request_obj=req, user=request.user,
        approve=(decision == 'approve'),
        comment=d.get('comment', ''),
    )
    return Response(_req_dict(req))
