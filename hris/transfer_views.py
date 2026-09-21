"""
hris/transfer_views.py — REST endpoints for inter-entity employee transfers.

  POST /hris/api/transfers/                  submit a transfer (PENDING_OUT)
  GET  /hris/api/transfers/                  list transfers (in-progress + recent)
  POST /hris/api/transfers/<id>/approve-out/ source-entity sign-off
  POST /hris/api/transfers/<id>/approve-in/  destination sign-off → applies
  POST /hris/api/transfers/<id>/reject/      reject

Gated behind the HRIS whitelist + unlock + amend right, same as amendments.
"""
from __future__ import annotations

from django.core.exceptions import ValidationError
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.hris_access import user_can_amend_hris
from .api_views import _deny_if_not_whitelisted
from .transfer_models import EmployeeTransfer
from .transfer_service import (
    _can_approve,
    approve_in, approve_out, reject_transfer, submit_transfer,
)


def _serialize(t: EmployeeTransfer) -> dict:
    return {
        'id': str(t.pk),
        'employee_id': str(t.employee_id),
        'employee_name': t.employee.full_name,
        'source_company': t.source_company.code,
        'dest_company': t.dest_company.code,
        'effective_date': t.effective_date.isoformat() if t.effective_date else None,
        'reason': t.reason,
        'mode': t.mode,
        'mode_label': t.get_mode_display(),
        'leave_treatment': t.leave_treatment,
        'new_email': t.new_email,
        'new_employee_id': str(t.new_employee_id) if t.new_employee_id else None,
        'new_employee_number': t.new_employee.employee_number if t.new_employee_id else None,
        'settlement': ({'id': str(t.settlement_id), 'days': str(t.settlement.days),
                        'net_amount': str(t.settlement.net_amount),
                        'status_label': t.settlement.get_status_display()}
                       if t.settlement_id else None),
        'status': t.status,
        'status_label': t.get_status_display(),
        'out_approver_email': t.out_approver_email,
        'out_approved_at': t.out_approved_at.isoformat() if t.out_approved_at else None,
        'in_approver_email': t.in_approver_email,
        'in_approved_at': t.in_approved_at.isoformat() if t.in_approved_at else None,
        'applied_at': t.applied_at.isoformat() if t.applied_at else None,
        'created_at': t.created_at.isoformat() if t.created_at else None,
    }


def _guard(request, approver_ok: bool = False):
    """HRIS amendment rights, unless this is one of the two things an approver
    must be able to do.

    `approver_ok=True` is passed ONLY by the decide endpoints (approve / reject)
    and by the GET list they need in order to find the transfer. The CFO named
    Pako Kago (Financial Controller) an approver on 2026-09-18 and his HRIS role
    is 'hr', which `user_can_amend_hris` refuses — so without this he would hold
    the right to approve and meet a 403 on the only screen where approving
    happens.

    It is a flag rather than a blanket bypass because this same guard also
    fronts SUBMITTING a transfer — which in `rehire` mode terminates someone's
    record and raises a leaver settlement — and `settlement_preview`, which
    returns any employee's basic salary and net pay with NO entity scope. The
    CFO's words were "access to approve". Approving is what this grants.
    """
    denied = _deny_if_not_whitelisted(request)
    if denied:
        return denied
    if approver_ok and _can_approve(request.user):
        return None
    if not user_can_amend_hris(request.user):
        return Response({'detail': 'You do not have HRIS amendment rights.'},
                        status=status.HTTP_403_FORBIDDEN)
    return None


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def transfers(request):
    # Reading the list is how an approver finds the transfer waiting on them.
    # Submitting one is not theirs — that keeps full HRIS amendment rights.
    guard = _guard(request, approver_ok=(request.method == 'GET'))
    if guard:
        return guard
    if request.method == 'GET':
        qs = (EmployeeTransfer.objects
              .select_related('employee', 'source_company', 'dest_company',
                              'new_employee', 'settlement')
              .order_by('-created_at'))
        # Entity scope (CFO 2026-06-16): a scoped user sees a transfer only if
        # it touches one of their entities (as source OR destination).
        from core.mixins import scoped_company_ids
        from django.db.models import Q
        ids = scoped_company_ids(request)
        if ids is not None:
            qs = (qs.filter(Q(source_company_id__in=ids) | Q(dest_company_id__in=ids))
                  if ids else qs.none())
        return Response({'transfers': [_serialize(t) for t in qs[:200]]})
    data = request.data or {}
    try:
        t = submit_transfer(
            submitter=request.user,
            employee_id=str(data.get('employee_id', '')),
            dest_company_id=str(data.get('dest_company_id', '')),
            effective_date=data.get('effective_date'),
            reason=data.get('reason', '') or '',
            mode=(data.get('mode') or EmployeeTransfer.Mode.CARRY),
            leave_treatment=(data.get('leave_treatment') or EmployeeTransfer.LeaveTreatment.PAYOUT),
            new_email=data.get('new_email', '') or '',
        )
    except ValidationError as e:
        return Response({'detail': '; '.join(e.messages)}, status=400)
    return Response(_serialize(t), status=status.HTTP_201_CREATED)


def _decide(request, transfer_id, fn):
    guard = _guard(request, approver_ok=True)
    if guard:
        return guard
    qs = EmployeeTransfer.objects.filter(pk=transfer_id)
    # Same entity scope as the list: three more approvers now reach this, so a
    # transfer outside their entities must not be decidable by guessing its id.
    from core.mixins import scoped_company_ids
    from django.db.models import Q
    ids = scoped_company_ids(request)
    if ids is not None:
        qs = (qs.filter(Q(source_company_id__in=ids) | Q(dest_company_id__in=ids))
              if ids else qs.none())
    t = qs.first()
    if t is None:
        return Response({'detail': 'Transfer not found.'}, status=404)
    try:
        fn(t, request.user, **({'notes': (request.data or {}).get('notes', '')}
                               if fn is reject_transfer else {}))
    except ValidationError as e:
        return Response({'detail': '; '.join(e.messages)}, status=400)
    return Response(_serialize(t))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def approve_out_view(request, transfer_id):
    return _decide(request, transfer_id, approve_out)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def approve_in_view(request, transfer_id):
    return _decide(request, transfer_id, approve_in)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def reject_view(request, transfer_id):
    return _decide(request, transfer_id, reject_transfer)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def settlement_preview(request):
    """GET /hris/api/transfers/settlement-preview/?employee_id=&last_day=YYYY-MM-DD
    What the leaver's final leave pay would be, before anything is saved."""
    import datetime as _dt
    from payroll.models import Employee
    from .leave_encash_service import settlement_quote
    guard = _guard(request)
    if guard:
        return guard
    emp = Employee.objects.filter(pk=request.query_params.get('employee_id', '')).first()
    if emp is None:
        return Response({'detail': 'Employee not found.'}, status=404)
    raw = (request.query_params.get('last_day') or '').strip()
    try:
        last_day = _dt.date.fromisoformat(raw) if raw else None
    except ValueError:
        return Response({'detail': 'last_day must be YYYY-MM-DD.'}, status=400)
    return Response(settlement_quote(emp, last_day))
