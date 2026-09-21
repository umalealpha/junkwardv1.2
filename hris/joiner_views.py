from datetime import timedelta

from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.hris_access import user_can_access_hris
from core.models import AuditLog
from hris.joiner_pack import joiner_row, start_joiner_pack
from hris.models import EmployeeAcknowledgement, HRISProfile
from payroll.models import Employee


def _get_manager(employee):
    try:
        return employee.hris_profile.manager
    except HRISProfile.DoesNotExist:
        return None


def _ack_dict(ack):
    return {
        'id': ack.pk,
        'kind': ack.kind,
        'title': ack.title,
        'due_date': ack.due_date.isoformat() if ack.due_date else None,
        'employee_signed_at': ack.employee_signed_at.isoformat() if ack.employee_signed_at else None,
        'manager_signed_at': ack.manager_signed_at.isoformat() if ack.manager_signed_at else None,
        'manager_signed_by': ack.manager_signed_by_id,
        'needs_manager': ack.needs_manager,
        'employee_id': str(ack.employee_id),
        'employee_name': ack.employee.full_name,
        'period': ack.period,
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def joiners_list(request):
    if not user_can_access_hris(request.user):
        return Response({'detail': 'HR access required.'}, status=403)

    today = timezone.localdate()
    cutoff = today - timedelta(days=120)
    employees = Employee.objects.filter(
        status='active',
        hire_date__gte=cutoff,
        hire_date__lte=today,
    ).select_related('company', 'hris_profile__manager__user').order_by('hire_date')

    return Response({'rows': [joiner_row(employee) for employee in employees]})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_signoffs(request):
    ack_map = {}

    own_employee = Employee.objects.filter(user=request.user).first()
    if own_employee:
        for ack in EmployeeAcknowledgement.objects.filter(employee=own_employee):
            ack_map[ack.pk] = ack

    manager_acks = EmployeeAcknowledgement.objects.filter(
        employee__hris_profile__manager__user=request.user,
        manager_signed_at__isnull=True,
        needs_manager=True,
    )
    for ack in manager_acks:
        ack_map[ack.pk] = ack

    acks = sorted(
        ack_map.values(),
        key=lambda ack: (ack.due_date is None, ack.due_date or timezone.localdate(), ack.title or ''),
    )
    return Response({'acks': [_ack_dict(ack) for ack in acks]})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sign_acknowledgement(request, ack_id):
    try:
        ack = EmployeeAcknowledgement.objects.select_related('employee').get(pk=ack_id)
    except EmployeeAcknowledgement.DoesNotExist:
        return Response({'detail': 'Not found.'}, status=404)

    employee = ack.employee

    if request.user.pk == employee.user_id:
        if ack.employee_signed_at is not None:
            return Response({'detail': 'Already signed by employee.'}, status=400)
        old_value = ack.employee_signed_at
        ack.employee_signed_at = timezone.now()
        ack.save()
        AuditLog.objects.create(
            table_name='hris_employeeacknowledgement',
            record_id=str(ack.pk),
            action='update',
            old_values={'employee_signed_at': old_value.isoformat() if old_value else None},
            new_values={'employee_signed_at': ack.employee_signed_at.isoformat()},
            user=request.user,
            description=f'{request.user.email} signed {ack.kind} for {employee.full_name}',
        )
        return Response({'ack': _ack_dict(ack)})

    manager = _get_manager(employee)
    if manager is not None and request.user.pk == manager.user_id and ack.needs_manager:
        if ack.manager_signed_at is not None:
            return Response({'detail': 'Already signed by manager.'}, status=400)
        old_value = ack.manager_signed_at
        ack.manager_signed_at = timezone.now()
        ack.manager_signed_by = request.user
        ack.save()
        AuditLog.objects.create(
            table_name='hris_employeeacknowledgement',
            record_id=str(ack.pk),
            action='update',
            old_values={
                'manager_signed_at': old_value.isoformat() if old_value else None,
                'manager_signed_by': ack.manager_signed_by_id,
            },
            new_values={
                'manager_signed_at': ack.manager_signed_at.isoformat(),
                'manager_signed_by': request.user.pk,
            },
            user=request.user,
            description=f'{request.user.email} signed {ack.kind} for {employee.full_name} as manager',
        )
        return Response({'ack': _ack_dict(ack)})

    return Response({'detail': 'You are not allowed to sign this acknowledgement.'}, status=403)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def start_joiner(request, employee_id):
    if not user_can_access_hris(request.user):
        return Response({'detail': 'HR access required.'}, status=403)

    try:
        employee = Employee.objects.get(pk=employee_id)
    except Employee.DoesNotExist:
        return Response({'detail': 'Not found.'}, status=404)

    start_joiner_pack(employee, request.user)
    return Response({'row': joiner_row(employee)})
