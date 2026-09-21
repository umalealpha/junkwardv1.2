from decimal import Decimal

from django.db.models import Sum

from agent_portal.models import CommissionCycle
from payroll.models import (
    GroupPayrollLine,
    GroupPayrollSnapshot,
    Payslip,
    PayrollPeriod,
)


def _m(value):
    """Money as a 2-decimal string."""
    return str(Decimal(str(value or 0)).quantize(Decimal('0.01')))


def latest_snapshot(period):
    """Return the newest GroupPayrollSnapshot for a PayrollPeriod, or None."""
    return GroupPayrollSnapshot.objects.filter(period=period).order_by('-version').first()


def trend(limit=12):
    """Return oldest-first list of periods that have at least one snapshot."""
    period_ids = GroupPayrollSnapshot.objects.values_list('period_id', flat=True).distinct()
    # Newest `limit` months, then oldest-first for the chart.
    periods = reversed(PayrollPeriod.objects.filter(id__in=period_ids).order_by('-start_date')[:limit])

    points = []
    for period in periods:
        snap = latest_snapshot(period)
        if snap is None:
            continue

        totals = snap.totals or {}
        by_company = {}

        company_rows = (
            GroupPayrollLine.objects
            .filter(snapshot=snap, status__in=['final', 'draft'])
            .values('company__name')
            .annotate(total_ctc=Sum('ctc'))
        )
        for row in company_rows:
            name = row['company__name']
            by_company[name] = _m(row['total_ctc'] or Decimal('0'))

        points.append({
            'period': period.period_name,
            'ctc': totals.get('ctc', '0'),
            'headcount': int(totals.get('headcount', 0)),
            'by_company': by_company,
        })

    return points


def departments(snap):
    """Aggregate by_department JSON across final/draft group payroll lines."""
    rows = {}

    for line in snap.lines.filter(status__in=['final', 'draft']):
        data = line.by_department or {}
        if not isinstance(data, dict):
            continue

        for department, values in data.items():
            if not isinstance(values, dict):
                continue
            try:
                ctc = Decimal(str(values.get('ctc', '0')))
                headcount = int(values.get('headcount', 0))
            except (TypeError, ValueError):
                continue

            row = rows.setdefault(
                department,
                {'department': department, 'headcount': 0, 'ctc': Decimal('0')},
            )
            row['headcount'] += headcount
            row['ctc'] += ctc

    ordered = sorted(rows.values(), key=lambda row: row['ctc'], reverse=True)
    return [
        {
            'department': row['department'],
            'headcount': row['headcount'],
            'ctc': _m(row['ctc']),
        }
        for row in ordered
    ]


def people(snap, company):
    """Return person-level payslip summaries for a snapshot period and company."""
    payslips = (
        Payslip.objects
        .filter(period=snap.period, company=company)
        .exclude(status='cancelled')
        .select_related('employee')
        .prefetch_related('lines__component')
    )

    result = []
    for payslip in payslips:
        amounts = {
            'BASIC': Decimal('0'),
            'INCENTIVE': Decimal('0'),
            'COMMISSION': Decimal('0'),
        }

        for line in payslip.lines.all():
            component = getattr(line, 'component', None)
            if component is None:
                continue
            code = component.code
            if code in amounts:
                amounts[code] += line.amount

        employee = payslip.employee
        result.append({
            'name': employee.full_name,
            'department': employee.department,
            'job_title': employee.job_title,
            'basic': _m(amounts['BASIC']),
            'incentive': _m(amounts['INCENTIVE']),
            'commission': _m(amounts['COMMISSION']),
            'gross': _m(payslip.gross_amount or Decimal('0')),
            'ctc': _m(payslip.ctc_amount or Decimal('0')),
        })

    result.sort(key=lambda row: row['name'])
    return result


def agent_commissions(period):
    """Summarise payable agent portal commissions for cycles overlapping a payroll period."""
    cycles = CommissionCycle.objects.filter(
        start_date__lte=period.end_date,
        end_date__gte=period.start_date,
    ).order_by('start_date')

    total = Decimal('0')
    cycle_rows = []

    for cycle in cycles:
        cycle_total = (
            cycle.lines
            .filter(payable=True)
            .aggregate(total=Sum('commission'))['total']
        )
        cycle_total = Decimal(cycle_total) if cycle_total is not None else Decimal('0')
        total += cycle_total
        cycle_rows.append({
            'label': cycle.label,
            'status': cycle.status,
            'total': _m(cycle_total),
        })

    return {
        'total': _m(total),
        'cycles': cycle_rows,
        'note': 'UniCoin sales agents — paid outside payroll',
    }
