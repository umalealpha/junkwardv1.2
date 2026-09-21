"""
records/request_service.py — rules behind staff file requests (maker-checker).

Kept out of the views so tests exercise the rules directly. Any authenticated
staff member may REQUEST a file; only the Records / Human Capital register team
(records.api_views._may_open) may approve or deny; the requester may not decide
their own request (SoD); every read/write is clamped to the caller's entities.
"""
from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import allowed_company_ids

from .api_views import _may_open          # the Records / Human Capital register gate
from .request_models import RecordFileRequest


def can_approve_requests(user) -> bool:
    """Human Capital / Records — whoever may open the register decides requests."""
    return _may_open(user)


def _in_scope(user, company_id) -> bool:
    allowed = allowed_company_ids(user)
    return allowed == {'*'} or str(company_id) in allowed


@transaction.atomic
def create_request(*, user, record, reason):
    if record is None:
        raise ValidationError('Pick the file you are requesting.')
    reason = (reason or '').strip()
    if len(reason) < 5:
        raise ValidationError('Please give a short reason for the request.')
    if not _in_scope(user, record.company_id):
        raise PermissionDenied('You do not have access to that entity.')
    req = RecordFileRequest(record=record, reason=reason, requested_by=user,
                            status=RecordFileRequest.Status.PENDING)
    req.save(audit_user=user, audit_description=f'Requested file {record.reference}')
    return req


@transaction.atomic
def approve_request(req, user):
    if req.status != RecordFileRequest.Status.PENDING:
        raise ValidationError(f'This request is already {req.get_status_display().lower()}.')
    if not can_approve_requests(user):
        raise PermissionDenied('Only Human Capital / Records may decide file requests.')
    if req.requested_by_id and req.requested_by_id == getattr(user, 'id', None):
        raise ValidationError('You cannot approve your own request (segregation of duties).')
    if not _in_scope(user, req.record.company_id):
        raise PermissionDenied('You do not have access to that entity.')
    req.status = RecordFileRequest.Status.APPROVED
    req.decided_by = user
    req.decided_at = timezone.now()
    req.save(audit_user=user, audit_description=f'Approved file request {req.record.reference}')
    return req


@transaction.atomic
def deny_request(req, user, note):
    if req.status != RecordFileRequest.Status.PENDING:
        raise ValidationError(f'This request is already {req.get_status_display().lower()}.')
    if not can_approve_requests(user):
        raise PermissionDenied('Only Human Capital / Records may decide file requests.')
    note = (note or '').strip()
    if not note:
        raise ValidationError('A reason is required to deny a request.')
    if not _in_scope(user, req.record.company_id):
        raise PermissionDenied('You do not have access to that entity.')
    req.status = RecordFileRequest.Status.DENIED
    req.decided_by = user
    req.decided_at = timezone.now()
    req.decision_note = note
    req.save(audit_user=user, audit_description=f'Denied file request {req.record.reference}')
    return req


def pending_for_approver(user):
    """PENDING requests this HC/Records approver may action (SoD: not their own,
    clamped to their entities)."""
    if not can_approve_requests(user):
        return RecordFileRequest.objects.none()
    qs = RecordFileRequest.objects.filter(status=RecordFileRequest.Status.PENDING)
    allowed = allowed_company_ids(user)
    if allowed != {'*'}:
        qs = qs.filter(record__company_id__in=allowed)
    uid = getattr(user, 'id', None)
    if uid:
        qs = qs.exclude(requested_by_id=uid)
    return qs


def my_requests(user):
    """The requester's own trail (every request they made, any status)."""
    return RecordFileRequest.objects.filter(requested_by=user)
