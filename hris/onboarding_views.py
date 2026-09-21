"""HTTP endpoints for the controlled onboarding maker-checker workflow."""
from __future__ import annotations

from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.hris_access import hris_role, user_can_access_hris
from core.hris_unlock import is_hris_unlocked
from core.models import Company, allowed_company_ids
from hris.departments import DEPARTMENTS
from hris.onboarding_models import OnboardingRequest
from hris.onboarding_service import (
    approve_onboarding_request,
    reject_onboarding_request,
    request_dict,
    submit_onboarding_request,
)
from payroll.models import Employee

_MANAGE_ROLES = {'hr', 'hris', 'admin', 'superadmin'}


def _gate(request):
    if not user_can_access_hris(request.user):
        return Response(
            {'detail': 'HRIS access is restricted to authorised personnel only.'},
            status=status.HTTP_403_FORBIDDEN,
        )
    if not is_hris_unlocked(request.user):
        return Response(
            {'detail': 'HRIS is locked. Enter the HRIS password to continue.',
             'requires_unlock': True},
            status=status.HTTP_401_UNAUTHORIZED,
        )
    if hris_role(request.user) not in _MANAGE_ROLES:
        return Response(
            {'detail': 'Only authorised HR personnel can manage onboarding.'},
            status=status.HTTP_403_FORBIDDEN,
        )
    return None


def _validation_response(exc: ValidationError):
    if hasattr(exc, 'message_dict'):
        detail = exc.message_dict
    elif hasattr(exc, 'messages'):
        detail = exc.messages[0] if len(exc.messages) == 1 else exc.messages
    else:
        detail = str(exc)
    return Response({'detail': detail}, status=status.HTTP_400_BAD_REQUEST)


def _scoped_company_ids(user):
    allowed = allowed_company_ids(user)
    if allowed == {'*'}:
        return None
    if allowed:
        return allowed
    own = getattr(user, 'employee_record', None)
    return {str(own.company_id)} if own and own.company_id else set()


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def onboard_employee(request):
    """Submit an onboarding request; no live employee is activated yet."""
    denied = _gate(request)
    if denied is not None:
        return denied
    try:
        onboarding, idempotent = submit_onboarding_request(
            maker=request.user, data=request.data or {})
    except ValidationError as exc:
        return _validation_response(exc)
    return Response(
        {
            **request_dict(onboarding, user=request.user),
            'created': False,
            'completed_existing': False,
            'pending_approval': True,
            'idempotent_retry': idempotent,
        },
        status=status.HTTP_200_OK if idempotent else status.HTTP_202_ACCEPTED,
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def onboarding_queue(request):
    """Return onboarding requests visible to the caller's entity scope."""
    denied = _gate(request)
    if denied is not None:
        return denied
    state = (request.query_params.get('status') or 'pending').strip().lower()
    if state not in {choice for choice, _ in OnboardingRequest.Status.choices}:
        return Response({'detail': 'status must be pending, approved or rejected.'},
                        status=status.HTTP_400_BAD_REQUEST)
    qs = (OnboardingRequest.objects.filter(status=state)
          .select_related('company', 'manager', 'default_leave_approver',
                          'default_leave_approver__employee_record',
                          'maker', 'approver', 'employee'))
    company_ids = _scoped_company_ids(request.user)
    if company_ids is not None:
        qs = qs.filter(company_id__in=company_ids)
    rows = [request_dict(row, user=request.user) for row in qs[:200]]
    return Response({'count': len(rows), 'requests': rows, 'status': state})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def onboarding_options(request):
    """Return entity-scoped companies and active employees for assignments."""
    denied = _gate(request)
    if denied is not None:
        return denied
    company_ids = _scoped_company_ids(request.user)
    companies = Company.objects.filter(is_active=True).order_by('code')
    employees = (Employee.objects.filter(is_archived=False)
                 .exclude(status=Employee.Status.TERMINATED)
                 .select_related('company', 'user').order_by('full_name'))
    if company_ids is not None:
        companies = companies.filter(id__in=company_ids)
        employees = employees.filter(company_id__in=company_ids)
    manager_rows = [
        {
            'id': str(emp.pk),
            'name': emp.full_name,
            'department': emp.department or '',
            'company_id': str(emp.company_id) if emp.company_id else None,
            'company_code': emp.company.code if emp.company_id else '',
            'user_id': emp.user_id,
            'can_receive_leave': bool(emp.user_id and emp.user and emp.user.is_active),
        }
        for emp in employees[:1000]
    ]
    return Response({
        'companies': [
            {'id': str(company.pk), 'code': company.code, 'name': company.name}
            for company in companies
        ],
        'managers': manager_rows,
        'departments': list(DEPARTMENTS),
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def decide_onboarding(request, request_id):
    """Approve or reject one pending onboarding request."""
    denied = _gate(request)
    if denied is not None:
        return denied
    action = (request.data.get('action') or '').strip().lower()
    notes = (request.data.get('notes') or '').strip()
    get_object_or_404(OnboardingRequest, pk=request_id)
    try:
        if action == 'approve':
            onboarding, replay = approve_onboarding_request(
                request_id=request_id, approver=request.user, notes=notes)
            code = status.HTTP_200_OK
            payload = {**request_dict(onboarding, user=request.user),
                       'idempotent_replay': replay}
        elif action == 'reject':
            onboarding = reject_onboarding_request(
                request_id=request_id, approver=request.user, notes=notes)
            code = status.HTTP_200_OK
            payload = request_dict(onboarding, user=request.user)
        else:
            return Response({'detail': 'action must be approve or reject.'},
                            status=status.HTTP_400_BAD_REQUEST)
    except OnboardingRequest.DoesNotExist:
        return Response({'detail': 'Onboarding request not found.'},
                        status=status.HTTP_404_NOT_FOUND)
    except ValidationError as exc:
        return _validation_response(exc)
    return Response(payload, status=code)
