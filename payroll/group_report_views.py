import re

from django.http import HttpResponse
from openpyxl import Workbook
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.models import AuditLog
from payroll.group_report import (
    can_regenerate_group_report,
    can_view_group_report,
    generate,
    notify,
    snapshot_dict,
)
from payroll.models import GroupPayrollSnapshot, PayrollPeriod


def _audit(user, action, record_id, description):
    AuditLog.objects.create(
        table_name='payroll_grouppayrollsnapshot',
        record_id=str(record_id),
        action=action,
        old_values={},
        new_values={},
        user=user,
        description=description,
    )


def _version_or_error(request):
    raw = request.query_params.get('version')
    if raw in (None, ''):
        return None, None
    try:
        return int(raw), None
    except ValueError:
        return None, Response({'detail': 'version must be an integer'}, status=400)


def _resolve_snapshot(period_name, version):
    period = None

    if period_name:
        period = PayrollPeriod.objects.filter(period_name=period_name).first()
        if not period:
            return None, period_name, {
                'detail': 'No report yet for this month',
                'period': period_name,
            }
    else:
        # Latest MONTH, not the latest-generated row (a back-fill of May must not
        # make May the default over August).
        last_snapshot = GroupPayrollSnapshot.objects.order_by('-period__start_date', '-version').first()
        if last_snapshot:
            period = last_snapshot.period
        else:
            period = PayrollPeriod.objects.order_by('-start_date').first()
            if not period:
                return None, None, {'detail': 'No payroll period exists yet'}

    period_name = period.period_name
    qs = GroupPayrollSnapshot.objects.filter(period=period)

    if not qs.exists():
        return None, period_name, {
            'detail': 'No report yet for this month',
            'period': period_name,
        }

    if version is not None:
        snap = qs.filter(version=version).first()
        if not snap:
            return None, period_name, {
                'detail': 'No report for this version',
                'period': period_name,
            }
    else:
        snap = qs.order_by('-version').first()

    return snap, period_name, None


def _safe_sheet_name(value):
    value = re.sub(r'[\\/*?:\[\]]', '', str(value)).strip() or 'Company'
    return value[:31]


def _build_workbook(snap):
    wb = Workbook()
    ws = wb.active
    ws.title = 'Group'

    totals = snap.totals or {}
    ws.append(['Alpha Direct · Omni', 'Group payroll report'])
    ws.append(['Period', snap.period.period_name])
    ws.append(['Version', snap.version])
    ws.append(['Trigger', snap.trigger])
    ws.append(['Generated at', totals.get('generated_at', '')])
    ws.append([])
    ws.append(['Cost to company', totals.get('ctc', '0')])
    ws.append(['Basic', totals.get('basic', '0')])
    ws.append(['Incentives', totals.get('incentive', '0')])
    ws.append(['Commissions', totals.get('commission', '0')])
    ws.append(['Headcount', totals.get('headcount', '0')])
    ws.append([])
    ws.append([
        'Company', 'Status', 'Headcount', 'Basic', 'Incentive', 'Commission',
        'Employer contributions', 'Gross', 'PAYE', 'Net', 'CTC',
        'Source currency', 'Source gross', 'FX rate', 'Tie-out OK', 'Tie-out note',
    ])

    lines = list(snap.lines.select_related('company').order_by('company__name'))
    used_titles = set()

    for line in lines:
        ws.append([
            line.company.name,
            line.status,
            line.headcount,
            str(line.basic),
            str(line.incentive),
            str(line.commission),
            str(line.employer_contrib),
            str(line.gross),
            str(line.paye),
            str(line.net),
            str(line.ctc),
            line.source_currency or '',
            str(line.source_gross) if line.source_gross is not None else '',
            str(line.fx_rate) if line.fx_rate is not None else '',
            'Yes' if line.tieout_ok else 'No',
            line.tieout_note or '',
        ])

        title = _safe_sheet_name(line.company.code or line.company.name)
        if title in used_titles:
            title = f"{title[:28]}-{line.id}"
        used_titles.add(title)

        ws2 = wb.create_sheet(title)
        ws2.append(['Department', 'Headcount', 'CTC'])
        departments = line.by_department or {}
        if not departments:
            ws2.append(['No payslips', 0, '0'])
        else:
            for department, values in sorted(departments.items()):
                ws2.append([
                    department,
                    values.get('headcount', 0),
                    values.get('ctc', '0'),
                ])

    return wb


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def group_report(request):
    if not can_view_group_report(request.user):
        return Response({'detail': 'You do not have permission to view the group payroll report.'}, status=403)

    version, error = _version_or_error(request)
    if error:
        return error

    snap, period_name, error = _resolve_snapshot(request.query_params.get('period'), version)
    can_regenerate = can_regenerate_group_report(request.user)

    if error:
        error['can_regenerate'] = can_regenerate
        error.setdefault('period', period_name)
        return Response(error, status=404)

    _audit(request.user, 'read', snap.id, 'Group payroll report viewed')

    data = snapshot_dict(snap)
    period_ids = GroupPayrollSnapshot.objects.values_list('period_id', flat=True).distinct()
    periods = list(
        PayrollPeriod.objects.filter(id__in=list(period_ids))
        .order_by('-start_date')
        .values_list('period_name', flat=True)
    )
    data['periods'] = periods
    data['can_regenerate'] = can_regenerate
    return Response(data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def group_report_regenerate(request):
    if not can_regenerate_group_report(request.user):
        return Response({'detail': 'You do not have permission to regenerate the group payroll report.'}, status=403)

    period_name = request.data.get('period')
    if not period_name:
        return Response({'detail': 'period is required'}, status=400)

    try:
        period = PayrollPeriod.objects.get(period_name=period_name)
    except PayrollPeriod.DoesNotExist:
        return Response({'detail': 'Payroll period does not exist.'}, status=404)

    snap = generate(period, trigger='manual', user=request.user)
    data = snapshot_dict(snap)
    data['can_regenerate'] = True

    if request.data.get('notify'):
        sent = notify(snap)
        data['emails_sent'] = sent

    return Response(data, status=201)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def group_report_export(request):
    if not can_view_group_report(request.user):
        return Response({'detail': 'You do not have permission to view the group payroll report.'}, status=403)

    version, error = _version_or_error(request)
    if error:
        return error

    snap, period_name, error = _resolve_snapshot(request.query_params.get('period'), version)
    if error:
        error['can_regenerate'] = can_regenerate_group_report(request.user)
        error.setdefault('period', period_name)
        return Response(error, status=404)

    _audit(request.user, 'download', snap.id, 'Group payroll report downloaded')

    wb = _build_workbook(snap)
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = (
        f'attachment; filename="group-payroll-{snap.period.period_name}-v{snap.version}.xlsx"'
    )
    wb.save(response)
    return response
