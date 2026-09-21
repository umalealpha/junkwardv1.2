"""
records/request_views.py — endpoints for staff file requests.

Thin views over request_service. Any authenticated staff member may request or
list their own; approve/deny authority + SoD + entity scope are enforced in the
service, not here.
"""
from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404
from rest_framework import status as http
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import RecordItem
from .request_models import RecordFileRequest
from .request_service import (
    approve_request, can_approve_requests, create_request, deny_request,
    my_requests, pending_for_approver,
)


def _uname(u) -> str:
    return (u.get_full_name() or u.username or '').strip() if u else ''


def _ser(r) -> dict:
    return {
        'id': str(r.id),
        'status': r.status,
        'status_display': r.get_status_display(),
        'record': {
            'id': str(r.record_id),
            'reference': r.record.reference,
            'title': r.record.title,
            'company': r.record.company.name if r.record.company_id else '',
        },
        'reason': r.reason,
        'requested_by': _uname(r.requested_by),
        'requested_at': r.requested_at.isoformat() if r.requested_at else None,
        'decided_by': _uname(r.decided_by),
        'decided_at': r.decided_at.isoformat() if r.decided_at else None,
        'decision_note': r.decision_note,
    }


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def request_list_create(request):
    if request.method == 'POST':
        d = request.data
        try:
            rec = get_object_or_404(RecordItem, pk=d.get('record_id'))
            req = create_request(user=request.user, record=rec, reason=d.get('reason'))
        except PermissionDenied as e:
            return Response({'detail': str(e)}, status=http.HTTP_403_FORBIDDEN)
        except ValidationError as e:
            return Response({'detail': '; '.join(e.messages)}, status=http.HTTP_400_BAD_REQUEST)
        return Response({'request': _ser(req)}, status=http.HTTP_201_CREATED)

    # GET — ?scope=mine (default, the requester's trail) or ?scope=to_approve
    scope = request.query_params.get('scope', 'mine')
    qs = pending_for_approver(request.user) if scope == 'to_approve' else my_requests(request.user)
    qs = qs.select_related('record', 'record__company', 'requested_by', 'decided_by')[:200]
    return Response({
        'results': [_ser(r) for r in qs],
        'can_approve': can_approve_requests(request.user),
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def request_approve(request, pk):
    req = get_object_or_404(RecordFileRequest, pk=pk)
    try:
        approve_request(req, request.user)
    except PermissionDenied as e:
        return Response({'detail': str(e)}, status=http.HTTP_403_FORBIDDEN)
    except ValidationError as e:
        return Response({'detail': '; '.join(e.messages)}, status=http.HTTP_400_BAD_REQUEST)
    return Response({'request': _ser(req)})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def request_deny(request, pk):
    req = get_object_or_404(RecordFileRequest, pk=pk)
    try:
        deny_request(req, request.user, request.data.get('note', ''))
    except PermissionDenied as e:
        return Response({'detail': str(e)}, status=http.HTTP_403_FORBIDDEN)
    except ValidationError as e:
        return Response({'detail': '; '.join(e.messages)}, status=http.HTTP_400_BAD_REQUEST)
    return Response({'request': _ser(req)})
