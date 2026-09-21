"""Propose and approve a controlled override of a locked maternity date
(bug f4464440, requirement 3).

Two steps, two different people:
  * propose_override — HR uploads new evidence, picks a reason code and the new
    end date. Nothing changes yet.
  * decide_override — a SECOND approver (not the proposer, not the original
    applicant, not the original approver) approves; only then does the locked
    date move. Everything is logged (requirement 5).
"""
from __future__ import annotations

import datetime as _dt
import os

from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.hris_access import user_can_amend_hris
from core.models import AuditLog
from hris.leave_admin import CERT_CONTENT_TYPES
from hris.maternity_override_models import MaternityDateOverride
from hris.models import LeaveRequest


def _owner_user_id(lr: LeaveRequest):
    emp = getattr(lr.profile, 'employee', None)
    return getattr(emp, 'user_id', None)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def propose_override(request, leave_id):
    if not user_can_amend_hris(request.user):
        return Response({'detail': 'Only HR with amendment rights can change a locked '
                                   'maternity date.'}, status=status.HTTP_403_FORBIDDEN)
    lr = LeaveRequest.objects.filter(pk=leave_id).select_related('profile', 'leave_type').first()
    if lr is None:
        return Response({'detail': 'Leave request not found.'}, status=status.HTTP_404_NOT_FOUND)
    if (lr.leave_type.code or '').lower() != 'maternity':
        return Response({'detail': 'Only a maternity leave date is locked and overridable.'},
                        status=status.HTTP_400_BAD_REQUEST)

    reason_code = (request.data.get('reason_code') or '').strip().lower()
    if reason_code not in {c[0] for c in MaternityDateOverride.ReasonCode.choices}:
        return Response(
            {'detail': 'reason_code must be one of '
                       f'{sorted(c[0] for c in MaternityDateOverride.ReasonCode.choices)}.'},
            status=status.HTTP_400_BAD_REQUEST)
    try:
        new_end = _dt.date.fromisoformat(str(request.data.get('new_end_date') or ''))
    except ValueError:
        return Response({'detail': 'new_end_date must be ISO format YYYY-MM-DD.'},
                        status=status.HTTP_400_BAD_REQUEST)
    if new_end < lr.start_date:
        return Response({'detail': 'new_end_date is before the leave start date.'},
                        status=status.HTTP_400_BAD_REQUEST)
    cert = request.FILES.get('certificate')
    if cert is None:
        return Response({'detail': 'New supporting medical evidence must be uploaded to '
                                   'change a locked maternity date.'},
                        status=status.HTTP_400_BAD_REQUEST)
    if os.path.splitext(cert.name)[1].lower() not in CERT_CONTENT_TYPES:
        return Response({'detail': 'The evidence must be a PDF, JPG or PNG file.'},
                        status=status.HTTP_400_BAD_REQUEST)
    if lr.date_overrides.filter(status=MaternityDateOverride.Status.PENDING).exists():
        return Response({'detail': 'An override is already awaiting approval for this leave.'},
                        status=status.HTTP_409_CONFLICT)

    ov = MaternityDateOverride(
        leave_request=lr, old_end_date=lr.end_date, new_end_date=new_end,
        reason_code=reason_code, proposed_by=request.user,
        notes=(request.data.get('notes') or '')[:2000])
    ov.certificate.save(cert.name, cert, save=False)
    ov.save()
    AuditLog.objects.create(
        table_name='MaternityDateOverride', record_id=str(ov.pk),
        action=AuditLog.Action.CREATE, user=request.user,
        description=(f'Proposed maternity date change {lr.end_date} → {new_end} '
                     f'({reason_code}) on leave {lr.pk}; awaiting a second approver.'))
    return Response({'id': str(ov.pk), 'status': ov.status,
                     'old_end_date': ov.old_end_date.isoformat(),
                     'new_end_date': ov.new_end_date.isoformat()},
                    status=status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def decide_override(request, override_id):
    if not user_can_amend_hris(request.user):
        return Response({'detail': 'Only HR with amendment rights can approve a maternity '
                                   'date change.'}, status=status.HTTP_403_FORBIDDEN)
    ov = (MaternityDateOverride.objects
          .filter(pk=override_id)
          .select_related('leave_request', 'leave_request__profile', 'leave_request__requested_approver')
          .first())
    if ov is None:
        return Response({'detail': 'Override not found.'}, status=status.HTTP_404_NOT_FOUND)
    if ov.status != MaternityDateOverride.Status.PENDING:
        return Response({'detail': f'This override is already {ov.status}.'},
                        status=status.HTTP_409_CONFLICT)

    action = (request.data.get('action') or '').strip().lower()
    if action not in {'approve', 'reject'}:
        return Response({'detail': 'action must be approve or reject.'},
                        status=status.HTTP_400_BAD_REQUEST)

    lr = ov.leave_request
    # Dual control: the second approver must not be the proposer, the original
    # applicant, or the original approver of the leave.
    forbidden = {ov.proposed_by_id, _owner_user_id(lr), lr.requested_approver_id}
    forbidden.discard(None)
    if request.user.pk in forbidden:
        return Response(
            {'detail': 'A maternity date change needs a SECOND approver — not the person '
                       'who proposed it, the employee on leave, or the original approver.'},
            status=status.HTTP_403_FORBIDDEN)

    if action == 'reject':
        ov.status = MaternityDateOverride.Status.REJECTED
        ov.approved_by = request.user
        ov.decided_at = timezone.now()
        ov.save(update_fields=['status', 'approved_by', 'decided_at', 'updated_at'])
        AuditLog.objects.create(
            table_name='MaternityDateOverride', record_id=str(ov.pk),
            action=AuditLog.Action.UPDATE, user=request.user,
            description=f'Rejected maternity date change on leave {lr.pk}.')
        return Response({'id': str(ov.pk), 'status': ov.status})

    old_end = lr.end_date
    lr.end_date = ov.new_end_date
    lr.save()                                   # recomputes days via compute_days()
    ov.status = MaternityDateOverride.Status.APPROVED
    ov.approved_by = request.user
    ov.decided_at = timezone.now()
    ov.save(update_fields=['status', 'approved_by', 'decided_at', 'updated_at'])
    AuditLog.objects.create(
        table_name='MaternityDateOverride', record_id=str(ov.pk),
        action=AuditLog.Action.APPROVE, user=request.user,
        old_values={'end_date': old_end.isoformat()},
        new_values={'end_date': ov.new_end_date.isoformat()},
        description=(f'Approved maternity date change {old_end} → {ov.new_end_date} '
                     f'({ov.reason_code}) on leave {lr.pk}. Proposed by user '
                     f'{ov.proposed_by_id}, second-approved by {request.user.pk}.'))
    return Response({'id': str(ov.pk), 'status': ov.status,
                     'leave_end_date': lr.end_date.isoformat()})
