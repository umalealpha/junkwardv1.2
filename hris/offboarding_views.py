from datetime import timedelta

from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.hris_access import user_can_access_hris
from core.models import AuditLog
from payroll.models import Employee

from .offboarding_service import case_dict, open_case, record_step
from hris.models import OffboardingCase


def _can_view_case(user, case):
    if user_can_access_hris(user):
        return True

    profile = getattr(case.employee, 'hris_profile', None)
    manager = getattr(profile, 'manager', None)
    return bool(manager and manager.user_id == user.id)


def _open_and_recent(case_qs):
    cutoff = timezone.now() - timedelta(days=90)
    return case_qs.filter(
        Q(status=OffboardingCase.Status.OPEN) |
        Q(status=OffboardingCase.Status.COMPLETE, completed_at__gte=cutoff)
    ).order_by('-status', '-created_at')


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def offboarding_list(request):
    user = request.user

    if user_can_access_hris(user):
        qs = _open_and_recent(OffboardingCase.objects.all())
        return Response([case_dict(case) for case in qs])

    if not Employee.objects.filter(hris_profile__manager__user=user).exists():
        return Response({'detail': 'You do not have access to offboarding.'}, status=403)

    qs = _open_and_recent(
        OffboardingCase.objects.filter(employee__hris_profile__manager__user=user)
    )
    return Response([case_dict(case) for case in qs])


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def offboarding_detail(request, case_id):
    case = OffboardingCase.objects.filter(pk=case_id).first()
    if case is None:
        return Response({'detail': 'Offboarding case not found.'}, status=404)

    if not _can_view_case(request.user, case):
        return Response({'detail': 'You do not have access to this offboarding case.'}, status=403)

    return Response(case_dict(case))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def offboarding_open(request):
    user = request.user
    if not user_can_access_hris(user):
        return Response({'detail': 'You do not have access to offboarding.'}, status=403)

    employee_id = request.data.get('employee_id')
    reason = request.data.get('reason', OffboardingCase.Reason.RESIGNATION)
    last_working_day_str = request.data.get('last_working_day')
    last_working_day = parse_date(last_working_day_str) if last_working_day_str else None

    if not employee_id or not last_working_day:
        return Response(
            {'detail': 'employee_id and last_working_day (YYYY-MM-DD) are required.'},
            status=400,
        )

    employee = Employee.objects.filter(pk=employee_id).first()
    if employee is None:
        return Response({'detail': 'Employee not found.'}, status=400)

    try:
        case = open_case(
            employee,
            reason=reason,
            last_working_day=last_working_day,
            user=user,
        )
    except ValueError as exc:
        return Response({'detail': str(exc)}, status=400)

    return Response(case_dict(case), status=201)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def offboarding_step(request, case_id):
    case = OffboardingCase.objects.filter(pk=case_id).first()
    if case is None:
        return Response({'detail': 'Offboarding case not found.'}, status=404)

    kind = request.data.get('kind')
    if not kind:
        return Response({'detail': 'kind is required.'}, status=400)

    upload = request.FILES.get('file')
    note = request.data.get('note', '')

    try:
        record_step(case, kind, request.user, upload=upload, note=note)
    except PermissionError as exc:
        return Response({'detail': str(exc)}, status=403)
    except ValueError as exc:
        return Response({'detail': str(exc)}, status=400)

    return Response(case_dict(case))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def offboarding_cancel(request, case_id):
    user = request.user
    if not user_can_access_hris(user):
        return Response({'detail': 'You do not have access to offboarding.'}, status=403)

    case = OffboardingCase.objects.filter(pk=case_id).first()
    if case is None:
        return Response({'detail': 'Offboarding case not found.'}, status=404)

    if case.status == OffboardingCase.Status.CANCELLED:
        return Response(case_dict(case))

    note = request.data.get('note', '')
    case.status = OffboardingCase.Status.CANCELLED
    case.note = note
    case.save(update_fields=['status', 'note'])

    AuditLog.objects.create(
        table_name='hris_offboardingcase',
        record_id=str(case.id),
        action='update',
        user=user,
        description=f'Offboarding case cancelled: {note}',
    )

    return Response(case_dict(case))


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def offboarding_employees(request):
    user = request.user
    if not user_can_access_hris(user):
        return Response({'detail': 'You do not have access to offboarding.'}, status=403)

    q = request.query_params.get('q', '').strip()
    qs = Employee.objects.filter(status='active')

    if q:
        qs = qs.filter(
            Q(full_name__icontains=q) |
            Q(email__icontains=q) |
            Q(department__icontains=q)
        )

    qs = qs[:50]

    data = [
        {
            'id': str(employee.id),
            'name': employee.full_name,
            'company': employee.company.name if employee.company else '',
            'department': employee.department,
        }
        for employee in qs
    ]
    return Response(data)
