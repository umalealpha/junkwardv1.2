"""
payroll/importer.py

CSV / XLSX parser for payroll uploads (Odoo previous-period export).

Header expectations match the CFO's exact 28-column layout:

    Employee, Department Name, Basic Salary, Commission, Incentive,
    Principal Officer Allowance, Allowance, Housing Allowance,
    Loans Deduction, Leave Pay, Housing Tax Deduction,
    Medical aid allowance, Vehicle Allowance, Severence,
    Sales Allowance, Health Insurance Allowance, Fuel Allowance,
    Mobile Allowance, Internet Allowance, Non-Cash Benefits,
    Medical Aid Employee Contribution, Medical Aid Company Contribution,
    Pension Employee Contribution, Pension Company Contribution,
    Provident Fund Employee Contribution, Provident Fund Company Contribution,
    Gross, PAYE, Net Salary, CTC

Imports are append-only by (employee × period). Existing payslips are NEVER
overwritten — conflicts are reported as skipped duplicates.
"""

from __future__ import annotations

import csv
import logging
import io
import re
import uuid
from decimal import Decimal, InvalidOperation

from django.contrib.auth.models import User
from django.db import transaction

from core.models import Company

from .models import (
    Employee, PayrollImportBatch, PayrollPeriod, Payslip, PayslipComponent, PayslipLine,
)


ZERO = Decimal('0.00')


CFO_HEADERS = [
    'Employee', 'Department Name', 'Basic Salary', 'Commission', 'Incentive',
    'Principal Officer Allowance', 'Allowance', 'Housing Allowance',
    'Loans Deduction', 'Leave Pay', 'Housing Tax Deduction',
    'Medical aid allowance', 'Vehicle Allowance', 'Severence',
    'Sales Allowance', 'Health Insurance Allowance', 'Fuel Allowance',
    'Mobile Allowance', 'Internet Allowance',
    # 'Non-Cash Benefits' was mapped in HEADER_TO_COMPONENT (and in
    # payroll/register_backfill.py, which proves it is a real file header) but
    # was missing HERE. parse_payroll_file builds every row dict from this list
    # ONLY, so the value never reached the row: the earning became ZERO, no
    # line was written, and the file's Gross — which INCLUDES the benefit — was
    # still trusted verbatim. Header said 12,500 while its lines summed to
    # 10,000 (2026-09-20).
    'Non-Cash Benefits',
    'Medical Aid Employee Contribution', 'Medical Aid Company Contribution',
    'Pension Employee Contribution', 'Pension Company Contribution',
    'Provident Fund Employee Contribution', 'Provident Fund Company Contribution',
    'Gross', 'PAYE', 'Net Salary', 'CTC',
]


def _to_decimal(v):
    if v is None:
        return ZERO
    s = str(v).strip()
    if not s:
        return ZERO
    for ch in [',', ' ', 'P', 'BWP']:
        s = s.replace(ch, '')
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return ZERO


def _bwp_per_unit(currency):
    """BWP per 1 unit of `currency` from the most-recent ExchangeRate
    (prefer an approved one). Returns Decimal or None when no rate is on file.
    Used to convert a foreign payroll (e.g. ADRisk INR) into the BWP
    reporting figures while keeping the source-currency amounts intact."""
    if currency is None:
        return None
    from core.models import Currency, ExchangeRate
    bwp = Currency.objects.filter(code='BWP').first()
    if bwp is None:
        return None
    qs = ExchangeRate.objects.filter(from_currency=currency, to_currency=bwp)
    rate = (qs.filter(approved_at__isnull=False).order_by('-effective_date').first()
            or qs.order_by('-effective_date').first())
    return rate.rate if rate else None


def _name_key(s: str) -> str:
    """Normalised employee-name key for dedup: casefold + collapse whitespace,
    so 'John Doe', 'JOHN DOE' and 'John  Doe' all resolve to ONE payslip per
    (employee, period). Stops the duplicate-in-register bug (Legakwa, RSA June
    2026-06-25) when the file lists a name twice with different case/spacing."""
    import re as _re
    return _re.sub(r'\s+', ' ', (s or '').strip()).casefold()


def _looks_like_xlsx(raw: bytes) -> bool:
    return isinstance(raw, (bytes, bytearray)) and len(raw) >= 4 and bytes(raw[:4]) == b'PK\x03\x04'


def _find_header_row(all_rows, scan: int = 20) -> int:
    """Odoo/RealPay payroll exports carry a title/preamble block above the real
    column header (logo, period, blank rows). Find the first row that looks like
    the header by locating the 'Employee' (or 'Name') column. Falls back to 0."""
    for i, r in enumerate(all_rows[:scan]):
        cells = [str(c).strip().lower() if c is not None else '' for c in r]
        if 'employee' in cells or 'employee name' in cells or 'name' in cells:
            return i
        if any('employee' in c for c in cells) and any('gross' in c or 'net' in c for c in cells):
            return i
    return 0


def _read_rows(raw: bytes, file_name: str = ''):
    if _looks_like_xlsx(raw) or file_name.lower().endswith(('.xlsx', '.xlsm')):
        from openpyxl import load_workbook
        from io import BytesIO
        wb = load_workbook(filename=BytesIO(raw), read_only=True, data_only=True)
        ws = wb.active
        if ws is None:
            return [], []
        all_rows = [list(r) for r in ws.iter_rows(values_only=True)]
        if not all_rows:
            return [], []
        hi = _find_header_row(all_rows)
        headers = ['' if c is None else str(c) for c in all_rows[hi]]
        data = []
        for r in all_rows[hi + 1:]:
            if all(c is None or (isinstance(c, str) and not c.strip()) for c in r):
                continue
            data.append(['' if c is None else c for c in r])
        return headers, data

    # CSV
    text = ''
    for enc in ('utf-8-sig', 'utf-8', 'cp1252', 'latin-1'):
        try:
            text = raw.decode(enc); break
        except UnicodeDecodeError:
            continue
    sample = text[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=',;\t|')
    except csv.Error:
        dialect = csv.excel
    all_rows = list(csv.reader(io.StringIO(text), dialect=dialect))
    if not all_rows:
        return [], []
    hi = _find_header_row(all_rows)
    headers = all_rows[hi]
    return headers, all_rows[hi + 1:]


def parse_payroll_file(file_obj, file_name: str = ''):
    """Returns (rows, errors) where rows is a list of row dicts keyed by canonical CFO header."""
    raw = file_obj.read() if hasattr(file_obj, 'read') else file_obj
    raw_bytes = raw if isinstance(raw, (bytes, bytearray)) else raw.encode('utf-8')
    errors: list[dict] = []

    headers, data_rows = _read_rows(bytes(raw_bytes), file_name)
    if not headers:
        errors.append({'row_index': -1, 'field': 'file', 'message': 'Empty file.'})
        return [], errors

    # Map each canonical CFO header to a column index (case-insensitive trim match)
    norm_headers = [(h or '').strip().lower() for h in headers]
    col_for: dict[str, int] = {}
    for h in CFO_HEADERS:
        target = h.strip().lower()
        if target in norm_headers:
            col_for[h] = norm_headers.index(target)

    if 'Employee' not in col_for:
        errors.append({
            'row_index': -1, 'field': 'headers',
            'message': f'Missing required Employee column. Headers seen: {headers}',
        })
        return [], errors

    rows = []
    for idx, raw_row in enumerate(data_rows, start=1):
        def get(h):
            i = col_for.get(h)
            if i is None or i >= len(raw_row):
                return ''
            v = raw_row[i]
            if v is None:
                return ''
            return str(v).strip() if isinstance(v, str) else v

        d = {'row_index': idx}
        for h in CFO_HEADERS:
            d[h] = get(h)
        rows.append(d)

    return rows, errors


# Spreadsheet summary rows ("Total", "Grand Total", "Subtotal", "Sum") are NOT
# employees. The Smart Upload was ingesting them as a person named "Total",
# creating a phantom payslip that DOUBLES the GL if the period is posted
# (Veritas VCM 2026-06; CFO directive 2026-06-25). Skip them everywhere.
# 2026-07-24 (ADIC final run): the label "TOTAL (72 payslips)" slipped through
# because the pattern was anchored to the exact word — a total row with any
# trailing text (a count, a note, punctuation) was ingested as an employee and
# DOUBLED Basic. Now matches a leading total/sum keyword followed by anything.
# 2026-09-20: four more realistic labels were still being INGESTED AS AN
# EMPLOYEE — "Total Payroll", "TOTAL STAFF", "Totals ADIC", "Grand Total
# Gross" — each minting an Employee row, a payslip, and doubling the period's
# Basic. Widened by ALLOW-LIST, not by "anything after the word Total": the
# pattern has to stay narrow because 'Sumaya Khan', 'Summertime Ltd',
# 'Sunday Phiri' and 'Total Quality Mgmt' are all pinned as PEOPLE by the
# importer tests. So a "Total …" label is a summary row only when its tail is
# a payroll-summary word, punctuation/a count, or an upper-case entity code.
_TOTAL_TAIL_WORDS = (
    r'payroll|staff|employees?|payslips?|headcount|gross|net|pay|salary|'
    r'salaries|basic|paye|ctc|cost|amounts?|earnings|deductions?|all|rows?|'
    r'lines?|for|of|per'
)

_TOTAL_ROW_RE = re.compile(
    r'^(?:grand\s+|sub\s*-?\s*)?totals?\b'
    r'\s*(?:$|[\(\):,;#\d-]|(?:' + _TOTAL_TAIL_WORDS + r')\b)',
    re.IGNORECASE,
)

# "Totals ADIC" — a summary row labelled with the ENTITY it totals. Deliberately
# CASE-SENSITIVE: the tail must be a short upper-case code (ADIC, RSA, VCM), so
# 'Total Quality Mgmt' stays a name.
_TOTAL_ENTITY_RE = re.compile(
    r'^(?:(?:[Gg]rand|GRAND)\s+|(?:[Ss]ub|SUB)\s*-?\s*)?'
    r'(?:Totals?|TOTALS?)\s+[A-Z0-9]{2,6}\s*$'
)

# 'sum' stays exactly as narrow as it was — that branch is the one guarding
# Sumaya / Summertime / Sunday.
_SUM_ROW_RE = re.compile(
    r'^sum\s*(?:$|[\(\):,;#\d-]|(?:for|of)\b|payslips?\b)',
    re.IGNORECASE,
)


def _is_total_row(name) -> bool:
    s = (name or '').strip()
    return bool(_TOTAL_ROW_RE.match(s)
                or _TOTAL_ENTITY_RE.match(s)
                or _SUM_ROW_RE.match(s))


def validate_payroll_rows(rows):
    errors = []
    seen = set()
    for r in rows:
        emp = (r.get('Employee') or '').strip()
        if _is_total_row(emp):
            continue  # spreadsheet totals/summary row, not an employee
        if not emp:
            errors.append({'row_index': r['row_index'], 'field': 'Employee', 'message': 'Missing.'})
            continue
        if emp in seen:
            errors.append({'row_index': r['row_index'], 'field': 'Employee',
                           'message': f'Duplicate employee in file: {emp}'})
        seen.add(emp)
    return errors


# Mapping CFO header -> canonical PayslipComponent code (set up by setup_payroll_components)
HEADER_TO_COMPONENT = {
    'Basic Salary':                              'BASIC',
    'Commission':                                'COMMISSION',
    'Incentive':                                 'INCENTIVE',
    'Principal Officer Allowance':               'PO_ALLOWANCE',
    'Allowance':                                 'ALLOWANCE',
    'Housing Allowance':                         'HOUSING_ALLOWANCE',
    'Loans Deduction':                           'LOANS_DEDUCTION',
    'Leave Pay':                                 'LEAVE_PAY',
    'Housing Tax Deduction':                     'HOUSING_TAX',
    'Medical aid allowance':                     'MEDICAL_AID_ALLOWANCE',
    'Vehicle Allowance':                         'VEHICLE_ALLOWANCE',
    'Severence':                                 'SEVERANCE',
    'Sales Allowance':                           'SALES_ALLOWANCE',
    'Health Insurance Allowance':                'HEALTH_INS_ALLOWANCE',
    'Fuel Allowance':                            'FUEL_ALLOWANCE',
    'Mobile Allowance':                          'MOBILE_ALLOWANCE',
    'Internet Allowance':                        'INTERNET_ALLOWANCE',
    'Non-Cash Benefits':                         'NON_CASH_BENEFIT',
    'Medical Aid Employee Contribution':         'MEDICAL_AID_EE',
    'Medical Aid Company Contribution':          'MEDICAL_AID_ER',
    'Pension Employee Contribution':             'PENSION_EE',
    'Pension Company Contribution':              'PENSION_ER',
    'Provident Fund Employee Contribution':      'PROVIDENT_EE',
    'Provident Fund Company Contribution':       'PROVIDENT_ER',
    'PAYE':                                      'PAYE',
}


@transaction.atomic
def commit_payroll_import(batch: PayrollImportBatch, user: User):
    """
    Append-only commit:
      - Creates Employee rows for unknown names (basic placeholder records).
      - Creates a Payslip per row only if (employee, period) doesn't already exist.
      - Skips duplicates (employee × period); records skip count.
      - Recomputes payslip totals from line items, ignoring imported Gross/Net/CTC
        which are kept only as cross-checks (validation, not source-of-truth).
    """
    if batch.period is None:
        raise ValueError('PayrollImportBatch.period must be set before commit.')

    components = {c.code: c for c in PayslipComponent.objects.filter(is_active=True)}
    # Dedup key: normalised name, scoped to THIS company + period (was period-
    # only + raw name — which let case/space variants AND cross-entity names slip
    # through as duplicates). Updated live as rows are created (see below) so a
    # name listed twice in the same file is only imported once.
    existing_payslip_keys = set(
        _name_key(n) for n in
        Payslip.objects.filter(period=batch.period, company=batch.company)
        .values_list('employee__full_name', flat=True)
    )

    # Currency awareness (CFO 2026-06-20): a non-BWP company (e.g. ADRisk = INR)
    # uploads payroll already computed in its local currency. We store the
    # source amounts + a BWP conversion, and DO NOT recompute Botswana PAYE.
    base_ccy = getattr(batch.company, 'base_currency', None)
    is_foreign = bool(base_ccy and base_ccy.code != 'BWP')
    src_ccy = 'BWP'
    fx_rate = Decimal('1')
    if is_foreign:
        src_ccy = base_ccy.code
        _r = _bwp_per_unit(base_ccy)
        if _r is None:
            raise ValueError(
                f'No {src_ccy}->BWP exchange rate on file; cannot import {src_ccy} '
                f'payroll for {batch.company.code}. Load + approve the rate first.'
            )
        fx_rate = _r

    created = 0
    skipped = 0
    errors: list[dict] = []

    for r in batch.parsed_rows:
        full_name = (r.get('Employee') or '').strip()
        if not full_name or _is_total_row(full_name):
            continue
        name_key = _name_key(full_name)
        if name_key in existing_payslip_keys:
            skipped += 1
            continue

        try:
            # employee_number is UNIQUE; a blank '' collides the moment a 2nd
            # new employee is created. Generate a stable per-company number for
            # NEW employees only (existing ones keep theirs).
            # Look up existing employee (filter().first(), NOT get_or_create —
            # the Employee table has pre-existing duplicate full_names that make
            # get() raise MultipleObjectsReturned). Prefer a same-company match.
            employee = (Employee.objects.filter(full_name=full_name, company=batch.company).first()
                        or Employee.objects.filter(full_name=full_name).first())
            # Terminated Employee Archive (2026-08-13): an archived leaver must
            # never be seeded a fresh payslip via import — that is exactly the
            # "excluded from all pay runs" guarantee HR archived them for.
            # Unarchive first if this row is genuinely a rehire.
            if employee is not None and employee.is_archived:
                skipped += 1
                continue
            if employee is None:
                base_no = f"{batch.company.code}-{re.sub(r'[^A-Za-z0-9]+', '', full_name)[:12].upper()}"
                empno = base_no or f"EMP-{uuid.uuid4().hex[:6].upper()}"
                while Employee.objects.filter(employee_number=empno).exists():
                    empno = f"{base_no}-{uuid.uuid4().hex[:4].upper()}"
                employee = Employee.objects.create(
                    full_name=full_name,
                    department=(r.get('Department Name') or '').strip(),
                    company=batch.company,
                    employee_number=empno,
                )
                # A staff record with no HRIS row is invisible to self-service —
                # the person cannot apply for leave, open a payslip or see their
                # own profile, and is told their record is "not linked" when it
                # is. Two developers sat in that state for weeks (2026-08-08).
                _ensure_hris_profile(employee)
            payslip = Payslip.objects.create(
                employee=employee,
                period=batch.period,
                company=batch.company,
                status=Payslip.Status.DRAFT,
            )
            payslip.save(audit_user=user, audit_description=f'Imported via batch {batch.id}')

            if is_foreign:
                # Foreign payroll: amounts are in the company's local currency.
                payslip.source_currency = src_ccy
                payslip.fx_rate_to_bwp  = fx_rate
                payslip.source_gross = _to_decimal(r.get('Gross'))
                payslip.source_paye  = _to_decimal(r.get('PAYE'))
                _sn = _to_decimal(r.get('Net Salary'))
                payslip.source_net = _sn if _sn != ZERO else None
                # Optional breakdown lines (kept in the SOURCE currency).
                for header, code in HEADER_TO_COMPONENT.items():
                    amount = _to_decimal(r.get(header))
                    comp = components.get(code)
                    if comp is None or amount == ZERO:
                        continue
                    PayslipLine.objects.create(payslip=payslip, component=comp, amount=amount)
                # Foreign branch: no BURS PAYE, converts source → BWP.
                payslip.recompute_totals()
                payslip.save(audit_user=user,
                             audit_description=f'{src_ccy} payroll imported (source + BWP @ {fx_rate})')
            else:
                # Odoo/RealPay exports store deductions, PAYE, pretax and
                # company contributions as NEGATIVE numbers. PayslipLine +
                # recompute_totals expect positive magnitudes keyed by kind, so
                # normalise every imported component to abs(). Earnings are
                # already positive.
                for header, code in HEADER_TO_COMPONENT.items():
                    amount = abs(_to_decimal(r.get(header)))
                    comp = components.get(code)
                    if comp is None or amount == ZERO:
                        continue
                    PayslipLine.objects.create(payslip=payslip, component=comp, amount=amount)

                gross_in = abs(_to_decimal(r.get('Gross')))
                net_in   = abs(_to_decimal(r.get('Net Salary')))
                if gross_in != ZERO and net_in != ZERO:
                    # Completed external payroll (Odoo computes, Omni records).
                    # Trust the file's totals exactly — do NOT recompute BURS
                    # PAYE (CFO 2026-06-20: June salaries run via Odoo + Omni).
                    company_contrib = ZERO
                    for ln in payslip.lines.select_related('component').all():
                        if ln.component.kind == PayslipComponent.Kind.COMPANY_CONTRIBUTION:
                            company_contrib += (ln.amount or ZERO)
                    payslip.gross_amount = gross_in
                    payslip.paye_amount  = abs(_to_decimal(r.get('PAYE')))
                    payslip.net_amount   = net_in
                    # CTC is always DERIVED (gross + employer contributions), never
                    # trusted from the file's CTC column. A stale sheet CTC column
                    # shipped a wrong CTC into the ADIC register (Aug 2026 — the file's
                    # CTC did not refresh after amendments). Matches recompute_totals().
                    payslip.ctc_amount   = gross_in + company_contrib
                    payslip.save(audit_user=user,
                                 audit_description='Imported actuals (trusted Gross/PAYE/Net)')
                else:
                    # Fresh omni-native run with no totals → compute from lines.
                    payslip.recompute_totals()
                    # Persist recomputed PAYE onto the TAX line so the GL posts
                    # the right tax even when the upload omitted a PAYE column
                    # (HRIS audit 2026-06-09).
                    _rp = getattr(payslip, '_recomputed_paye', None)
                    if _rp is not None:
                        payslip.overwrite_paye_line(_rp, user=user)
                        payslip.recompute_totals(recompute_paye=False)
                    payslip.save(audit_user=user, audit_description='Totals recomputed after import')

            created += 1
            existing_payslip_keys.add(name_key)
        except Exception as exc:  # noqa: BLE001
            errors.append({'row_index': r['row_index'], 'field': '__row__', 'message': str(exc)})

    batch.rows_imported = created
    batch.rows_skipped_dup = skipped
    batch.status = PayrollImportBatch.Status.COMMITTED
    from django.utils import timezone
    batch.committed_at = timezone.now()
    batch.save(audit_user=user, audit_description=f'Committed: {created} new, {skipped} skipped')

    return created, skipped, errors


log = logging.getLogger(__name__)

def _ensure_hris_profile(employee):
    """Give a newly created staff record the HRIS row self-service needs.

    Imported here rather than at module level to keep payroll free of a hard
    import on hris, and best-effort because a payroll import must never fall
    over on a talent-management row. `_profile_for()` self-heals on read as the
    backstop for anything that still slips through.
    """
    try:
        from hris.models import HRISProfile
        HRISProfile.objects.get_or_create(employee=employee)
    except Exception as exc:                               # noqa: BLE001
        # Exception, not BaseException: the latter also swallows Ctrl-C and
        # SystemExit. Log the reason, not just the fact — without it this is
        # the same silent swallow the block exists to prevent.
        log.warning('Could not create the HRIS profile for employee id %s: %s',
                    getattr(employee, 'pk', None), exc)
