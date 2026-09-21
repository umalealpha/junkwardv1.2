"""
payroll/burs_itw8.py

CFO directive 2026-05-24 — Payroll + HR upgrades pass, item #5.

Two pure functions that emit BURS statutory-format CSVs:

  render_itw8_csv(employee, tax_year_start, tax_year_end) -> str
      Per-employee certificate (the ITW8 layout BURS publishes for the
      "PAYE Certificate" download).

  render_paye_recon_csv(tax_year_start, tax_year_end) -> str
      The PAYE Reconciliation File — one row per employee for the whole
      tax year (used at year-end to file the consolidated return).

Columns are derived from Payslip + PayslipLine totals in the window
(start <= period.end_date <= end). No PDF generation in this pass —
BURS accept the CSV as the upload format.

Bible-check guard: payroll/ only. No ledger / reporting / billing.
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal

from django.db.models import Sum


ZERO = Decimal('0.00')

# Employer-level static metadata — pulled from settings at runtime so the
# CFO can flip the TIN without redeploys.
def _employer_tin() -> str:
    from django.conf import settings
    return str(getattr(settings, 'BURS_EMPLOYER_TIN', '') or '')


def _employer_name() -> str:
    from django.conf import settings
    return str(getattr(settings, 'BURS_EMPLOYER_NAME', 'Alpha Direct Insurance Company') or '')


# ---------------------------------------------------------------------------
# Aggregation helper
# ---------------------------------------------------------------------------

def _aggregate_for_employee(employee, tax_year_start, tax_year_end):
    """Return (gross_taxable, paye_total, net_total) for the window."""
    from .models import Payslip, PayslipComponent, PayslipLine

    payslips = Payslip.objects.filter(
        employee=employee,
        period__end_date__gte=tax_year_start,
        period__end_date__lte=tax_year_end,
    )
    if not payslips.exists():
        return (ZERO, ZERO, ZERO)

    gross_taxable_total = ZERO
    for ps in payslips:
        # Taxable portion = EARNING kind - EMPLOYEE_PRETAX kind on this payslip.
        lines = (
            PayslipLine.objects
            .filter(payslip=ps)
            .select_related('component')
        )
        for ln in lines:
            kind = ln.component.kind
            if kind == PayslipComponent.Kind.EARNING:
                gross_taxable_total += (ln.amount or ZERO)
            elif kind == PayslipComponent.Kind.EMPLOYEE_PRETAX:
                gross_taxable_total -= (ln.amount or ZERO)

    paye_total = payslips.aggregate(s=Sum('paye_amount'))['s'] or ZERO
    net_total  = payslips.aggregate(s=Sum('net_amount'))['s']  or ZERO
    return (Decimal(gross_taxable_total), Decimal(paye_total), Decimal(net_total))


# ---------------------------------------------------------------------------
# Per-employee ITW8
# ---------------------------------------------------------------------------

def render_itw8_csv(employee, tax_year_start, tax_year_end) -> str:
    """Emit one CSV blob for one employee covering the given window.

    Columns (header included):
        Omang, FullName, EmployerTIN, EmployerName, PeriodFrom, PeriodTo,
        GrossTaxable, PAYETotal, NetPayTotal
    """
    gross, paye, net = _aggregate_for_employee(employee, tax_year_start, tax_year_end)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        'Omang', 'FullName', 'EmployerTIN', 'EmployerName',
        'PeriodFrom', 'PeriodTo',
        'GrossTaxable', 'PAYETotal', 'NetPayTotal',
    ])
    writer.writerow([
        employee.national_id or '',
        employee.full_name,
        _employer_tin(),
        _employer_name(),
        tax_year_start.isoformat(),
        tax_year_end.isoformat(),
        f'{gross:.2f}',
        f'{paye:.2f}',
        f'{net:.2f}',
    ])
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Consolidated PAYE recon
# ---------------------------------------------------------------------------

def render_paye_recon_csv(tax_year_start, tax_year_end) -> str:
    """Emit one CSV with one row per employee that had any payslip in the
    window. Used for the BURS PAYE Reconciliation File at year-end.

    Columns: Omang, FullName, EmployeeNumber, EmployerTIN, EmployerName,
             PeriodFrom, PeriodTo, GrossTaxable, PAYETotal, NetPayTotal.
    """
    from .models import Employee, Payslip

    emp_ids = (
        Payslip.objects
        .filter(period__end_date__gte=tax_year_start,
                period__end_date__lte=tax_year_end)
        .values_list('employee_id', flat=True)
        .distinct()
    )
    employees = Employee.objects.filter(id__in=list(emp_ids)).order_by('full_name')

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        'Omang', 'FullName', 'EmployeeNumber',
        'EmployerTIN', 'EmployerName',
        'PeriodFrom', 'PeriodTo',
        'GrossTaxable', 'PAYETotal', 'NetPayTotal',
    ])
    for emp in employees:
        gross, paye, net = _aggregate_for_employee(emp, tax_year_start, tax_year_end)
        writer.writerow([
            emp.national_id or '',
            emp.full_name,
            emp.employee_number or '',
            _employer_tin(),
            _employer_name(),
            tax_year_start.isoformat(),
            tax_year_end.isoformat(),
            f'{gross:.2f}',
            f'{paye:.2f}',
            f'{net:.2f}',
        ])
    return buf.getvalue()
