"""
hris/leave_encash_views.py — API for leave-encashment self-service application,
the 4-step approval chain, and the leave-pay provision register
(CFO directive 2026-07-21).

Endpoints (all under /hris/api/):
  GET  leave-encashment/                 my quote + my requests + (privileged)
                                         the whole queue + my caller capabilities
  POST leave-encashment/                 apply (self-service, own leave)
  GET  leave-encashment/provision/       provision register (salary data — CFO/
                                         HR/Finance only)
  POST leave-encashment/<id>/approve/    approve at the current stage
  POST leave-encashment/<id>/reject/     reject at the current stage
  POST leave-encashment/<id>/mark-paid/  Finance marks it paid
"""
from __future__ import annotations

from django.core.exceptions import ValidationError
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from . import leave_encash_service as svc
from .leave_encash_models import LeaveEncashment


def _sig(user, at):
    if not at:
        return {'signed': False, 'by': '', 'at': None}
    return {'signed': True,
            'by': (getattr(user, 'email', '') or getattr(user, 'username', '')) if user else '',
            'at': at.isoformat()}


def _serialize(enc: LeaveEncashment, viewer=None) -> dict:
    return {
        'id':            str(enc.id),
        'employee_id':   str(enc.employee_id),
        'employee_name': enc.employee.full_name,
        'kind':          enc.kind,
        'kind_label':    enc.get_kind_display(),
        'last_day':      enc.last_day.isoformat() if enc.last_day else None,
        'department':    enc.employee.department or '',
        'company':       getattr(enc.company, 'code', '') or getattr(enc.company, 'name', '') or '',
        'days':          str(enc.days),
        'basic_salary':  str(enc.basic_salary),
        'basic_source':  enc.basic_source,
        'daily_rate':    str(enc.daily_rate),
        'amount':        str(enc.amount),
        'tax_base':      str(enc.tax_base),
        'tax_amount':    str(enc.tax_amount),
        'net_amount':    str(enc.net_amount),
        'balance_at_request': str(enc.balance_at_request),
        'reason':        enc.reason,
        'status':        enc.status,
        'status_label':  enc.get_status_display(),
        'applicant_email': enc.applicant_email,
        'is_own':        bool(viewer is not None and enc.applicant_id == viewer.pk),
        'can_act':       bool(viewer is not None and svc.can_approve_now(enc, viewer)),
        'can_pay':       bool(viewer is not None and enc.status == LeaveEncashment.Status.APPROVED
                              and not enc.payroll_processed and svc.can_pay(viewer)),
        'created_at':    enc.created_at.isoformat() if enc.created_at else None,
        'signatures': {
            'cfo':     _sig(enc.cfo_approver, enc.cfo_approved_at),
            'hr':      _sig(enc.hr_approver, enc.hr_approved_at),
            'finance': _sig(enc.finance_approver, enc.finance_approved_at),
        },
        'rejected':      ({'at': enc.rejected_at.isoformat() if enc.rejected_at else None,
                           'stage': enc.rejected_stage, 'notes': enc.decision_notes}
                          if enc.status == LeaveEncashment.Status.REJECTED else None),
        'paid':          {'is_paid': enc.payroll_processed,
                          'at': enc.payroll_processed_at.isoformat()
                                if enc.payroll_processed_at else None},
    }


def _me(user) -> dict:
    return {
        'can_approve_cfo':     svc.is_cfo(user),
        'can_approve_hr':      svc.is_hr(user),
        'can_approve_finance': svc.is_finance_approver(user),
        'can_view_all':        svc.can_view_all(user),
        'can_pay':             svc.can_pay(user),
    }


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def encashments(request):
    me = _me(request.user)
    if request.method == 'POST':
        # Self-service: any employee applies for THEIR OWN leave.
        try:
            enc = svc.apply_encashment(
                applicant=request.user,
                days=request.data.get('days'),
                reason=request.data.get('reason') or '',
            )
        except ValidationError as exc:
            return Response({'detail': '; '.join(exc.messages)}, status=400)
        return Response(_serialize(enc, request.user),
                        status=status.HTTP_201_CREATED)

    # GET — everyone sees their own quote + their own applications; privileged
    # approvers additionally see the whole queue.
    qs = (LeaveEncashment.objects
          .select_related('employee', 'company', 'applicant',
                          'cfo_approver', 'hr_approver', 'finance_approver')
          .order_by('-created_at'))
    if not me['can_view_all']:
        qs = qs.filter(applicant=request.user)
    return Response({
        'me':       me,
        'quote':    svc.my_quote(request.user),
        'requests': [_serialize(e, request.user) for e in qs[:300]],
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def provision(request):
    """The leave-pay provision register. Salary-bearing — CFO/HR/Finance only."""
    if not svc.can_view_all(request.user):
        return Response({'detail': 'The provision register is restricted to the '
                                   'CFO, HR and Finance.'}, status=403)
    from core.mixins import apply_company_scope
    from payroll.models import Employee
    qs = apply_company_scope(request, Employee.objects.all(), 'company_id')
    return Response(svc.provision_rows(qs))


def _get_or_404(encashment_id):
    return (LeaveEncashment.objects
            .select_related('employee', 'company', 'applicant',
                            'cfo_approver', 'hr_approver', 'finance_approver')
            .filter(pk=encashment_id).first())


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def approve(request, encashment_id):
    enc = _get_or_404(encashment_id)
    if enc is None:
        return Response({'detail': 'Not found.'}, status=404)
    try:
        enc = svc.approve(enc, request.user)
    except ValidationError as exc:
        return Response({'detail': '; '.join(exc.messages)}, status=400)
    return Response(_serialize(enc, request.user))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def reject(request, encashment_id):
    enc = _get_or_404(encashment_id)
    if enc is None:
        return Response({'detail': 'Not found.'}, status=404)
    try:
        enc = svc.reject(enc, request.user, notes=request.data.get('notes') or '')
    except ValidationError as exc:
        return Response({'detail': '; '.join(exc.messages)}, status=400)
    return Response(_serialize(enc, request.user))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def mark_paid(request, encashment_id):
    enc = _get_or_404(encashment_id)
    if enc is None:
        return Response({'detail': 'Not found.'}, status=404)
    try:
        enc = svc.mark_paid(enc, request.user)
    except ValidationError as exc:
        return Response({'detail': '; '.join(exc.messages)}, status=400)
    return Response(_serialize(enc, request.user))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def leaver_settlement(request):
    """HR raises a leaver's FINAL leave settlement (feature request 66b1e7a3,
    Internal Audit — standalone offboarding). Until now the leaver settlement was
    only raised automatically by the inter-entity transfer flow; there was no way
    for HR to raise one for an employee terminated directly. This reuses the same
    raise_leaver_settlement path: full balance to the last working day, BASIC/24
    valuation, and the SAME CFO -> HR -> Finance approval chain (the initiator is
    barred from approving), so it appears on every existing approval screen and
    posts nothing until dual-approved. The asset handover must be clear first —
    the same gate as marking the person terminated."""
    if not svc.can_view_all(request.user):
        return Response({'detail': 'Only the CFO, HR or Finance can raise a leaver settlement.'},
                        status=403)
    import uuid as _uuid
    raw = (request.data.get('employee_id') or '').strip()
    valid_id = None
    try:
        valid_id = str(_uuid.UUID(raw))
    except (ValueError, AttributeError, TypeError):
        return Response({'detail': 'Employee not found.'}, status=400)
    from payroll.models import Employee
    emp = Employee.objects.filter(pk=valid_id).first()
    if emp is None:
        return Response({'detail': 'Employee not found.'}, status=400)
    from assets.control_services import assets_blocking_offboarding
    held = list(assets_blocking_offboarding(emp))
    if held:
        tags = ', '.join(a.tag_number for a in held[:10])
        more = '' if len(held) <= 10 else f' (+{len(held) - 10} more)'
        return Response(
            {'detail': f'{emp.full_name} still holds {len(held)} asset(s): {tags}{more}. '
                       'Return or write off every asset before raising the final settlement.'},
            status=400)
    from django.db import transaction
    try:
        # raise_leaver_settlement takes a select_for_update lock, so it must run
        # inside a transaction (this project does not use ATOMIC_REQUESTS).
        with transaction.atomic():
            enc = svc.raise_leaver_settlement(
                initiator=request.user,
                employee=emp,
                last_day=((request.data.get('last_day') or '').strip() or None),
                reason=(request.data.get('reason') or '').strip(),
                actor=request.user,
            )
    except ValidationError as exc:
        return Response({'detail': '; '.join(exc.messages)}, status=400)
    return Response(_serialize(enc, request.user), status=201)
