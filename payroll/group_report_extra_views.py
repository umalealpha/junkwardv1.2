from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.models import AuditLog, Company
from payroll.group_report import can_view_group_report
from payroll.group_report_extra import (
    agent_commissions as agent_commissions_data,
    departments as departments_data,
    latest_snapshot,
    people as people_data,
    trend as trend_data,
)
from payroll.models import PayrollPeriod


def _get_period_or_response(request):
    period_name = request.query_params.get('period')
    if not period_name:
        return None, Response({'detail': 'period query parameter is required'}, status=400)

    period = PayrollPeriod.objects.filter(period_name=period_name).first()
    if period is None:
        return None, Response({'detail': 'Period not found'}, status=404)

    return period, None


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def trend_view(request):
    if not can_view_group_report(request.user):
        return Response({'detail': 'You do not have permission to view this report.'}, status=403)

    return Response({'points': trend_data()})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def departments_view(request):
    if not can_view_group_report(request.user):
        return Response({'detail': 'You do not have permission to view this report.'}, status=403)

    period, error = _get_period_or_response(request)
    if error:
        return error

    snap = latest_snapshot(period)
    if snap is None:
        return Response({'detail': 'No snapshot found for this period'}, status=404)

    return Response({'rows': departments_data(snap)})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def people_view(request):
    if not can_view_group_report(request.user):
        return Response({'detail': 'You do not have permission to view this report.'}, status=403)

    period, error = _get_period_or_response(request)
    if error:
        return error

    company_id = request.query_params.get('company')
    company = Company.objects.filter(pk=company_id).first() if company_id else None
    if company is None:
        return Response({'detail': 'Company not found'}, status=404)

    snap = latest_snapshot(period)
    if snap is None:
        return Response({'detail': 'No snapshot found for this period'}, status=404)

    rows = people_data(snap, company)

    AuditLog.objects.create(
        table_name='payroll_payslip',
        record_id=f'{period.period_name}:{company_id}',
        action='read',
        user=request.user,
        description='Group payroll person-level view',
    )

    return Response({'rows': rows})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def agent_commissions_view(request):
    if not can_view_group_report(request.user):
        return Response({'detail': 'You do not have permission to view this report.'}, status=403)

    period, error = _get_period_or_response(request)
    if error:
        return error

    return Response(agent_commissions_data(period))
