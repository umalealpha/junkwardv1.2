"""
payroll/addition_views.py — endpoints for "Add Employee to Payroll".

Thin views over addition_service. Create/list are open to payroll viewers;
approve/reject authority (Finance leg) and segregation of duties are enforced
inside the service, not here.
"""
from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404
from rest_framework import status as http
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.models import Company, allowed_company_ids

from .addition_models import PayrollAdditionRequest
from .addition_service import (
    approve_addition, create_addition, pending_for_approver, reject_addition,
)
from .api_views import CanViewPayroll
from .models import Employee, PayrollPeriod, PayslipComponent
from .signoff_service import can_sign_finance


def _uname(u) -> str:
    if not u:
        return ''
    return (u.get_full_name() or u.username or u.email or '').strip()


def _can_touch(user, company_id) -> bool:
    """True if the user's entity grant covers this company ('*' = unrestricted).
    Hand-rolled views miss CompanyScopedViewSetMixin, so every entity-stamped
    read/write must clamp here (SEC-02 / H33)."""
    allowed = allowed_company_ids(user)
    return allowed == {'*'} or str(company_id) in allowed


def _serialize(req, user=None) -> dict:
    from .signoff_service import is_signed_off
    _pending = req.status == PayrollAdditionRequest.Status.PENDING
    # A pending addition on a period that has since been signed off cannot be
    # approved until the period is reopened (approve_addition re-guards it). Don't
    # offer an Approve button that would just error — mark it blocked and say why.
    _signed_off = bool(_pending and is_signed_off(req.period, req.company))
    return {
        'id': str(req.id),
        'status': req.status,
        'status_display': req.get_status_display(),
        'full_name': req.full_name,
        'employee_number': req.employee_number,
        'department': req.department,
        'company': {'id': str(req.company_id), 'name': req.company.name, 'code': req.company.code},
        'period': {'id': str(req.period_id), 'period_name': req.period.period_name},
        'basic': str(req.basic),
        'commission': str(req.commission),
        'incentive': str(req.incentive),
        'allowances': req.allowances or [],
        'gross_estimate': str(req.gross_estimate),
        'requested_by': _uname(req.requested_by),
        'requested_at': req.requested_at.isoformat() if req.requested_at else None,
        'decided_by': _uname(req.decided_by),
        'decided_at': req.decided_at.isoformat() if req.decided_at else None,
        'rejection_comment': req.rejection_comment,
        'linked_existing': req.linked_existing,
        'created_payslip_id': str(req.created_payslip_id) if req.created_payslip_id else None,
        # One predicate for "this viewer may Approve/Reject this row" — a Finance
        # signer who did NOT submit it (SoD). The dashboard button and the
        # My-Approvals count must agree, so both derive from this (H50).
        'can_action': bool(user and _pending
                           and can_sign_finance(user)
                           and req.requested_by_id != getattr(user, 'id', None)
                           and not _signed_off),
        # Why an otherwise-actionable request can't be approved right now.
        'blocked_reason': (
            f'{req.company.name} {req.period.period_name} is signed off — reopen the '
            f'period on Payroll Sign-off before this addition can be approved.'
            if _signed_off and user and can_sign_finance(user) else ''),
    }


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated, CanViewPayroll])
def addition_list_create(request):
    if request.method == 'POST':
        d = request.data
        try:
            period = get_object_or_404(PayrollPeriod, pk=d.get('period_id'))
            company = get_object_or_404(Company, pk=d.get('company_id'))
            if not _can_touch(request.user, company.id):
                return Response({'detail': 'You do not have access to that entity.'},
                                status=http.HTTP_403_FORBIDDEN)
            req, warnings = create_addition(
                period=period, company=company, user=request.user,
                full_name=d.get('full_name'), employee_number=d.get('employee_number', ''),
                department=d.get('department', ''), basic=d.get('basic'),
                commission=d.get('commission'), incentive=d.get('incentive'),
                allowances=d.get('allowances') or [],
            )
        except ValidationError as e:
            return Response({'detail': '; '.join(e.messages)}, status=http.HTTP_400_BAD_REQUEST)
        return Response({'request': _serialize(req), 'warnings': warnings},
                        status=http.HTTP_201_CREATED)

    qs = (PayrollAdditionRequest.objects
          .select_related('period', 'company', 'requested_by', 'decided_by'))
    status_f = request.query_params.get('status')
    if status_f:
        qs = qs.filter(status=status_f)
    period_id = request.query_params.get('period_id')
    if period_id:
        qs = qs.filter(period_id=period_id)
    company_id = request.query_params.get('company_id')
    if company_id:
        qs = qs.filter(company_id=company_id)
    # Entity isolation: never list additions for an entity the caller has no
    # grant to (H33 — the cross-entity salary read-leak).
    allowed = allowed_company_ids(request.user)
    if allowed != {'*'}:
        qs = qs.filter(company_id__in=allowed)
    return Response({
        'results': [_serialize(r, request.user) for r in qs[:200]],
        'can_approve': can_sign_finance(request.user),
        'pending_for_me': pending_for_approver(request.user).count(),
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated, CanViewPayroll])
def addition_approve(request, pk):
    req = get_object_or_404(PayrollAdditionRequest, pk=pk)
    if not _can_touch(request.user, req.company_id):
        return Response({'detail': 'You do not have access to that entity.'}, status=http.HTTP_403_FORBIDDEN)
    try:
        approve_addition(req, request.user)
    except PermissionDenied as e:
        return Response({'detail': str(e)}, status=http.HTTP_403_FORBIDDEN)
    except ValidationError as e:
        return Response({'detail': '; '.join(e.messages)}, status=http.HTTP_400_BAD_REQUEST)
    return Response({'request': _serialize(req, request.user)})


@api_view(['POST'])
@permission_classes([IsAuthenticated, CanViewPayroll])
def addition_reject(request, pk):
    req = get_object_or_404(PayrollAdditionRequest, pk=pk)
    if not _can_touch(request.user, req.company_id):
        return Response({'detail': 'You do not have access to that entity.'}, status=http.HTTP_403_FORBIDDEN)
    try:
        reject_addition(req, request.user, request.data.get('comment', ''))
    except PermissionDenied as e:
        return Response({'detail': str(e)}, status=http.HTTP_403_FORBIDDEN)
    except ValidationError as e:
        return Response({'detail': '; '.join(e.messages)}, status=http.HTTP_400_BAD_REQUEST)
    return Response({'request': _serialize(req, request.user)})


@api_view(['GET'])
@permission_classes([IsAuthenticated, CanViewPayroll])
def addition_meta(request):
    """Dropdown data for the Add-Employee modal."""
    periods = (PayrollPeriod.objects
               .filter(status__in=[PayrollPeriod.Status.OPEN, PayrollPeriod.Status.LOCKED])
               .order_by('-start_date'))
    allowed = allowed_company_ids(request.user)
    companies = Company.objects.all().order_by('name')
    if allowed != {'*'}:
        companies = companies.filter(id__in=allowed)
    depts = sorted({(e.department or '').strip()
                    for e in Employee.objects.only('department').all()
                    if (e.department or '').strip()})
    allowance_kinds = [PayslipComponent.Kind.EARNING, PayslipComponent.Kind.EARNING_NON_TAXABLE]
    skip = {'BASIC', 'COMMISSION', 'INCENTIVE'}
    allowance_types = [
        {'code': c.code, 'name': c.name}
        for c in PayslipComponent.objects.filter(is_active=True, kind__in=allowance_kinds)
                                         .order_by('sort_order', 'name')
        if c.code not in skip
    ]
    return Response({
        'periods': [{'id': str(p.id), 'period_name': p.period_name} for p in periods],
        'companies': [{'id': str(c.id), 'name': c.name, 'code': c.code} for c in companies],
        'departments': depts,
        'allowance_types': allowance_types,
        'can_approve': can_sign_finance(request.user),
    })
