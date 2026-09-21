"""
hris/amendment_views.py — REST endpoints for the HRIS dual-approval workflow.

  POST /api/hris/amendments/                 submit a proposed change (maker)
  GET  /api/hris/amendments/pending/          list amendments awaiting approval
       ?status=approved                       ... or the last 50 applied ones
  POST /api/hris/amendments/<id>/approve/     approve & apply (approver)
  POST /api/hris/amendments/<id>/reject/      reject (approver)
  POST /api/hris/amendments/<id>/reverse/     propose a compensating reversal

All gated behind the HRIS whitelist + unlock (see _deny_if_not_whitelisted).
"""
from __future__ import annotations

from django.core.exceptions import ValidationError
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.hris_access import user_can_amend_hris, can_view_compensation
from .amendment_models import HRISAmendment
from .amendment_service import (
    approve_amendment, reject_amendment, reverse_amendment, submit_amendment,
)
from .api_views import _deny_if_not_whitelisted


def _serialize(a: HRISAmendment, own_email: str = '', redact_comp: bool = False) -> dict:
    # is_own: the viewer is the maker. The backend already BLOCKS a maker from
    # approving their own amendment (amendment_service._can_approve), so this is
    # purely so the UI can hide the Approve/Reject buttons from the submitter
    # instead of showing buttons that error on click (BUG 6f53096f, Oprah 2026-06-26).
    is_own = bool(a.maker_email and own_email
                  and a.maker_email.strip().lower() == own_email.strip().lower())
    return {
        'id': str(a.pk),
        'target_kind': a.target_kind,
        'target_id': a.target_id,
        'target_label': a.target_label,
        # A pending change may carry a new/old salary or grade. A viewer without
        # compensation rights (read-only hr_viewer) sees which fields changed but
        # never the pay values themselves.
        'changes': (a.changes if not redact_comp
                    else {k: '•••' for k in (a.changes or {})}),
        'reason': a.reason,
        'status': a.status,
        'maker_email': a.maker_email,
        'approver_email': a.approver_email,
        'is_own': is_own,
        'created_at': a.created_at.isoformat() if a.created_at else None,
        'decided_at': a.decided_at.isoformat() if a.decided_at else None,
        'decision_notes': a.decision_notes,
        # Reversal pairing (2026-08-25): `reversal_of` is set on a compensating
        # amendment; `has_reversal` tells the UI not to offer Reverse twice.
        'reversal_of': str(a.reversal_of_id) if a.reversal_of_id else None,
        'has_reversal': a.reversals.exclude(
            status=HRISAmendment.Status.REJECTED).exists(),
    }


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def submit(request):
    denied = _deny_if_not_whitelisted(request)
    if denied:
        return denied
    if not user_can_amend_hris(request.user):
        return Response({'detail': 'You do not have HRIS amendment rights.'},
                        status=status.HTTP_403_FORBIDDEN)
    data = request.data or {}
    try:
        amendment = submit_amendment(
            maker=request.user,
            target_kind=data.get('target_kind', ''),
            target_id=str(data.get('target_id', '')),
            proposed=data.get('changes') or {},
            reason=data.get('reason', '') or '',
        )
    except ValidationError as exc:
        return Response({'detail': '; '.join(exc.messages)},
                        status=status.HTTP_400_BAD_REQUEST)
    return Response(_serialize(amendment), status=status.HTTP_201_CREATED)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def pending(request):
    denied = _deny_if_not_whitelisted(request)
    if denied:
        return denied
    redact = not can_view_compensation(request.user)
    # Default stays PENDING — the maker-checker queue, unchanged. `?status=approved`
    # lists APPLIED amendments so the Reverse action (2026-08-25) has something to
    # act on; without it the reversal endpoint would exist with no way to reach it.
    # Any other value falls back to pending rather than widening by accident.
    wanted = (request.query_params.get('status') or '').strip().lower()
    state = (HRISAmendment.Status.APPROVED if wanted == 'approved'
             else HRISAmendment.Status.PENDING)
    qs = HRISAmendment.objects.filter(status=state)
    if state == HRISAmendment.Status.APPROVED:
        # Newest first, and bounded — this list is for "undo the one I just did",
        # not a full audit history (the AuditLog is that).
        qs = qs.order_by('-decided_at')[:50]
    # Entity scope (CFO 2026-06-16): a scoped user only sees amendments whose
    # target (employee / profile) belongs to one of their granted entities.
    # Unrestricted users (CFO/admin/super) see the whole maker-checker queue.
    from core.mixins import scoped_company_ids
    ids = scoped_company_ids(request)
    if ids is not None:
        if not ids:
            return Response({'amendments': []})
        from payroll.models import Employee
        from hris.models import HRISProfile
        emp_ids = set(str(x) for x in Employee.objects
                      .filter(company_id__in=ids).values_list('id', flat=True))
        prof_ids = set(str(x) for x in HRISProfile.objects
                       .filter(employee__company_id__in=ids).values_list('id', flat=True))
        allowed_targets = emp_ids | prof_ids
        rows = [a for a in qs if str(a.target_id) in allowed_targets]
        return Response({'amendments': [_serialize(a, own_email=getattr(request.user, 'email', ''), redact_comp=redact) for a in rows]})
    return Response({'amendments': [_serialize(a, own_email=getattr(request.user, 'email', ''), redact_comp=redact) for a in qs]})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def approve(request, amendment_id):
    denied = _deny_if_not_whitelisted(request)
    if denied:
        return denied
    try:
        amendment = HRISAmendment.objects.get(pk=amendment_id)
    except HRISAmendment.DoesNotExist:
        return Response({'detail': 'Amendment not found.'}, status=status.HTTP_404_NOT_FOUND)
    try:
        approve_amendment(amendment, request.user)
    except ValidationError as exc:
        return Response({'detail': '; '.join(exc.messages)},
                        status=status.HTTP_400_BAD_REQUEST)
    return Response(_serialize(amendment))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def reject(request, amendment_id):
    denied = _deny_if_not_whitelisted(request)
    if denied:
        return denied
    try:
        amendment = HRISAmendment.objects.get(pk=amendment_id)
    except HRISAmendment.DoesNotExist:
        return Response({'detail': 'Amendment not found.'}, status=status.HTTP_404_NOT_FOUND)
    try:
        reject_amendment(amendment, request.user, notes=(request.data or {}).get('notes', ''))
    except ValidationError as exc:
        return Response({'detail': '; '.join(exc.messages)},
                        status=status.HTTP_400_BAD_REQUEST)
    return Response(_serialize(amendment))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def reverse(request, amendment_id):
    """Propose a compensating amendment that puts an applied change back.

    Returns the NEW (pending) amendment, not the original — the original is
    untouched history. See amendment_service.reverse_amendment for why this is a
    normal maker-checker amendment rather than an undo button.
    """
    denied = _deny_if_not_whitelisted(request)
    if denied:
        return denied
    try:
        amendment = HRISAmendment.objects.get(pk=amendment_id)
    except HRISAmendment.DoesNotExist:
        return Response({'detail': 'Amendment not found.'}, status=status.HTTP_404_NOT_FOUND)
    try:
        reversal = reverse_amendment(amendment, request.user)
    except ValidationError as exc:
        return Response({'detail': '; '.join(exc.messages)},
                        status=status.HTTP_400_BAD_REQUEST)
    own = (getattr(request.user, 'email', '') or '')
    return Response(
        _serialize(reversal, own_email=own,
                   redact_comp=not can_view_compensation(request.user)),
        status=status.HTTP_201_CREATED,
    )
