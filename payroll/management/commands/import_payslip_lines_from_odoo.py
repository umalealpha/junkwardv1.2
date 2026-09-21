"""
import_payslip_lines_from_odoo — backfill per-component payslip LINES from the
Odoo payroll register (CFO / Pako 2026-07-14).

Root cause of "Payroll Report: component fields not populating": the ADIC (and
ADRG/UNI/RSA/QIH) payslips for a period were loaded TOTALS-ONLY — header
Gross/PAYE/Net/CTC exist but there are no PayslipLine component rows, so the
report honestly shows blanks + "totals only". The export code is correct; the
line data was never captured. This loads it from the Odoo register.

SAFETY: this NEVER changes an approved total. For each employee it recomputes
the Odoo Gross and only writes lines if that ties the payslip's stored
gross_amount within --tolerance. Non-tying / unmatched employees are skipped and
reported. Dry-run unless --commit. Earnings stored positive, deductions negative
(as in the register), employer contributions positive.

  manage.py import_payslip_lines_from_odoo <file.xlsx> --period 2026-05 --company ADIC
  manage.py import_payslip_lines_from_odoo <file.xlsx> --period 2026-05 --company ADIC --commit
"""
from __future__ import annotations

from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from payroll.models import Payslip, PayslipComponent, PayslipLine, PayrollPeriod

# Odoo register column header (lower-cased) -> Omni PayslipComponent.code
COL_TO_CODE = {
    'basic salary': 'BASIC', 'commission': 'COMMISSION', 'incentive': 'INCENTIVE',
    'principal officer allowance': 'PO_ALLOWANCE', 'allowance': 'ALLOWANCE',
    'housing allowance': 'HOUSING_ALLOWANCE', 'loans deduction': 'LOANS_DEDUCTION',
    'housing tax deduction': 'HOUSING_TAX', 'medical aid allowance': 'MEDICAL_AID_ALLOWANCE',
    'vehicle allowance': 'VEHICLE_ALLOWANCE', 'health insurance allowance': 'HEALTH_INS_ALLOWANCE',
    'fuel allowance': 'FUEL_ALLOWANCE', 'internet allowance': 'INTERNET_ALLOWANCE',
    'medical aid employee contribution': 'MEDICAL_AID_EE',
    'medical aid company contribution': 'MEDICAL_AID_ER',
    'pension employee contribution': 'PENSION_EE', 'pension company contribution': 'PENSION_ER',
    'provident fund employee contribution': 'PROVIDENT_EE',
    'provident fund company contribution': 'PROVIDENT_ER',
    'non-cash benefits': 'NON_CASH_BENEFIT',
}
GROSS_HDR = 'gross'


def _toks(n):
    return [t for t in (n or '').replace('.', ' ').split() if len(t) > 1]


def _norm(n):
    return ' '.join(_toks(n)).lower()


def _firstlast(n):
    t = _toks(n)
    return f'{t[0]} {t[-1]}'.lower() if len(t) >= 2 else (t[0].lower() if t else '')


def _dec(v):
    try:
        return Decimal(str(round(float(v), 2)))
    except (TypeError, ValueError):
        return Decimal('0')


class Command(BaseCommand):
    help = 'Backfill payslip component lines from the Odoo payroll register (reconciliation-guarded).'

    def add_arguments(self, parser):
        parser.add_argument('xlsx_path')
        parser.add_argument('--period', required=True, help='Period name, e.g. 2026-05')
        parser.add_argument('--company', required=True, help='Company code, e.g. ADIC')
        parser.add_argument('--commit', action='store_true')
        parser.add_argument('--tolerance', type=float, default=1.0,
                            help='Max |Odoo gross − stored gross| to accept (default 1.00).')

    def handle(self, *args, **o):
        import openpyxl
        w = self.stdout.write
        period = PayrollPeriod.objects.filter(period_name=o['period']).first()
        if not period:
            raise CommandError(f'Period {o["period"]!r} not found.')
        code = o['company'].strip().upper()
        payslips = list(Payslip.objects.filter(period=period, company__code=code)
                        .select_related('employee', 'company').prefetch_related('lines'))
        if not payslips:
            raise CommandError(f'No payslips for {o["period"]} / {code}.')
        by_norm, by_fl = {}, {}
        for ps in payslips:
            nm = ps.employee.full_name if ps.employee else ''
            by_norm.setdefault(_norm(nm), []).append(ps)
            by_fl.setdefault(_firstlast(nm), []).append(ps)

        wb = openpyxl.load_workbook(o['xlsx_path'], read_only=True, data_only=True)
        ws = wb[wb.sheetnames[0]]
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        hidx = next((i for i, r in enumerate(rows[:20])
                     if any(str(c or '').strip().lower() == 'employee' for c in r)), -1)
        if hidx < 0:
            raise CommandError('Could not find the "Employee" header row.')
        hdr = [str(c or '').strip().lower() for c in rows[hidx]]
        colmap = {hdr[i]: i for i in range(len(hdr))}
        comp_col = {code_: colmap[h] for h, code_ in COL_TO_CODE.items() if h in colmap}
        gross_col = colmap.get(GROSS_HDR)
        comps = {c.code: c for c in PayslipComponent.objects.filter(code__in=comp_col.keys())}
        missing = [c for c in comp_col if c not in comps]
        if missing:
            w(f'WARN: components not in catalogue (skipped): {missing}')

        matched = tied = written = skipped_recon = unmatched = 0
        report = []
        for r in rows[hidx + 1:]:
            if not r or not str(r[0] or '').strip():
                continue
            name = str(r[0]).strip()
            cand = by_norm.get(_norm(name)) or by_fl.get(_firstlast(name))
            if not cand or len(cand) != 1:
                unmatched += 1
                report.append(f'  UNMATCHED{"/AMBIG" if cand else ""}: {name}')
                continue
            ps = cand[0]; matched += 1
            odoo_gross = _dec(r[gross_col]) if gross_col is not None and gross_col < len(r) else None
            stored = ps.gross_amount or Decimal('0')
            if odoo_gross is None or abs(odoo_gross - stored) > Decimal(str(o['tolerance'])):
                skipped_recon += 1
                report.append(f'  RECON-FAIL: {name} odoo_gross={odoo_gross} stored_gross={stored}')
                continue
            tied += 1
            lines = []
            for code_, ci in comp_col.items():
                if code_ not in comps or ci >= len(r):
                    continue
                comp = comps[code_]
                amt = _dec(r[ci])
                if amt == 0:
                    continue
                # Employer (company) contributions are a positive employer COST
                # (CTC = gross + employer). The register sometimes stores them
                # negative, so normalise to positive. Earnings stay +, employee
                # deductions stay − (as in the register).
                if comp.kind == 'company_contribution':
                    amt = abs(amt)
                lines.append(PayslipLine(payslip=ps, component=comp, amount=amt))
            if o['commit']:
                with transaction.atomic():
                    ps.lines.all().delete()
                    PayslipLine.objects.bulk_create(lines)
                written += 1

        w(f'\n=== {o["period"]} / {code} — {"COMMIT" if o["commit"] else "DRY-RUN"} ===')
        w(f'payslips in period/company: {len(payslips)}')
        w(f'odoo rows matched: {matched} | reconciled(gross ties): {tied} | '
          f'recon-fail: {skipped_recon} | unmatched: {unmatched}')
        w(f'payslips written with lines: {written}')
        for line in report[:40]:
            w(line)
        if not o['commit']:
            w('\nDRY-RUN — nothing written. Re-run with --commit once the reconciliation looks right.')
