"""
hris/leave_upload_views.py — HR bulk-xlsx uploads for the HRIS, mirroring the
payroll-amendments upload (CFO directive 2026-06-16).

  POST /hris/api/leave-opening-balances/upload/   (bug 9c0aa7d3 — HR)
      Set corrected leave entitlement / opening balance / accrued figures AS AT
      a cutoff date for all employees at once. Columns (case-insensitive):
        employee | leave_type | as_at_date | entitlement | opening_balance | accrued
      Creates LeaveOpeningBalance rows; leave_balances() then uses the latest
      row <= today per (employee, type) as the baseline (additive — no
      regression for anyone not uploaded).

  POST /hris/api/leave-approvers/upload/          (bug 2cdd6333)
      Bulk-set the leave approver / "reports to" (HRISProfile.manager).
      Columns: employee | approver

Both are gated to the privileged HRIS tier (whitelist + unlock) — HR only.
"""
from __future__ import annotations

import datetime as _dt
import logging
import re
from decimal import Decimal, InvalidOperation
from io import BytesIO

from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from payroll.models import Employee
from hris.models import HRISProfile, LeaveOpeningBalance
from hris.feature_views import _gate, get_leave_rules

log = logging.getLogger(__name__)


# ───────────────────────── helpers ─────────────────────────

def _read_xlsx(file_obj):
    """Return (headers_lower, [dict-rows]) from an .xlsx OR .csv. Empty on failure.

    Accepts both so HR can upload the spreadsheet they already keep, or the
    one-click CSV template offered by the Leave Report page. .xlsx files are
    ZIP archives (magic bytes ``PK\\x03\\x04``); anything else is treated as
    CSV text.
    """
    raw = file_obj.read()
    name = (getattr(file_obj, 'name', '') or '').lower()
    is_xlsx = raw[:4] == b'PK\x03\x04' or name.endswith(('.xlsx', '.xlsm'))

    if is_xlsx:
        from openpyxl import load_workbook
        wb = load_workbook(filename=BytesIO(raw), read_only=True, data_only=True)
        ws = wb.active
        table = list(ws.iter_rows(values_only=True))
    else:
        import csv as _csv
        text = raw.decode('utf-8-sig', errors='replace')
        table = list(_csv.reader(text.splitlines()))

    it = iter(table)
    header_row = next(it, None)
    if not header_row:
        return [], []
    headers = [('' if c is None else str(c)).strip().lower() for c in header_row]
    rows = []
    for r in it:
        if r is None or all(c is None or str(c).strip() == '' for c in r):
            continue
        rows.append({headers[i]: (r[i] if i < len(r) else None) for i in range(len(headers))})
    return headers, rows


def _dec(v) -> Decimal:
    if v is None or str(v).strip() == '':
        return Decimal('0.00')
    try:
        return Decimal(str(v).replace(',', '').strip())
    except (InvalidOperation, ValueError):
        return Decimal('0.00')


def _date(v):
    if isinstance(v, (_dt.datetime, _dt.date)):
        return v.date() if isinstance(v, _dt.datetime) else v
    s = str(v or '').strip()
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%Y/%m/%d'):
        try:
            return _dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


# ── Name matching (HRIS-004, HR 2026-06-18) ──────────────────────────────
# The upload file carries names with middle initials/dots ("Dimpho A.
# Motseothata") that don't string-equal the stored "Dimpho Motseothata", so the
# old `full_name__icontains(ref)` rejected valid rows (the ref is LONGER than
# the stored name, so "stored contains ref" is false). Match on a normalised
# first+last token instead — and when a name is ambiguous or unknown, return a
# SPECIFIC named reason rather than silently dropping the row.

def _norm_name(s) -> str:
    """Lowercase, drop dots, collapse whitespace."""
    return re.sub(r'\s+', ' ', str(s or '').replace('.', ' ')).strip().lower()


def _first_last(s):
    """(first, last) tokens of a normalised name — middle names/initials dropped."""
    parts = _norm_name(s).split()
    return (parts[0], parts[-1]) if parts else None


def _build_employee_index():
    """One pass over employees → lookup maps for fast, tolerant matching."""
    by_norm, by_fl, by_email, by_num = {}, {}, {}, {}
    for e in Employee.objects.all().only('id', 'full_name', 'email', 'employee_number'):
        by_norm.setdefault(_norm_name(e.full_name), []).append(e)
        fl = _first_last(e.full_name)
        if fl:
            by_fl.setdefault(fl, []).append(e)
        if e.email:
            by_email[e.email.strip().lower()] = e
        if e.employee_number:
            by_num[str(e.employee_number).strip().lower()] = e
    return by_norm, by_fl, by_email, by_num


def _match_employee(ref, idx):
    """Resolve a name/email/employee-number cell → (Employee|None, reason|None).

    Order: email → employee-number → exact normalised name → first+last token.
    Ambiguous (>1 match) and not-found each return a specific, named reason so
    the upload never silently rejects a row.
    """
    by_norm, by_fl, by_email, by_num = idx
    s = str(ref or '').strip()
    if not s:
        return None, 'cell is blank'
    low = s.lower()
    if low in by_email:
        return by_email[low], None
    if low in by_num:
        return by_num[low], None
    cands = by_norm.get(_norm_name(s))
    if cands:
        if len(cands) == 1:
            return cands[0], None
        return None, f"{s!r} matches {len(cands)} employees — use the email or employee number to disambiguate"
    fl = _first_last(s)
    if fl:
        cands = by_fl.get(fl)
        if cands and len(cands) == 1:
            return cands[0], None
        if cands:
            return None, f"{s!r} matches {len(cands)} employees ({fl[0].title()} … {fl[1].title()}) — use the email or employee number"
    return None, f"{s!r} not found — check the spelling against HRIS records"


def _resolve_profile(ref, idx):
    """Resolve an 'employee' cell → (HRISProfile|None, reason|None).

    Creates a blank HRISProfile when the Employee exists but has none yet
    (bug 6ff261ed: 39/134 employees had no profile, so every row was skipped).
    """
    emp, reason = _match_employee(ref, idx)
    if emp is None:
        return None, reason
    prof, _ = HRISProfile.objects.get_or_create(employee=emp)
    return prof, None


def _first(row: dict, *keys):
    for k in keys:
        if k in row and row[k] is not None and str(row[k]).strip() != '':
            return row[k]
    return None


# ───────────────────────── opening balances (9c0aa7d3) ─────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def upload_leave_opening_balances(request):
    # HR-tier only: whitelist + unlock + manage_leave_admin (hr/hris/admin/
    # superadmin). Keeps mgr/ceo out of a bulk leave-balance rewrite.
    denied = _gate(request, capability='manage_leave_admin')
    if denied is not None:
        return denied
    f = request.FILES.get('file')
    if f is None:
        return Response({'detail': 'Attach an .xlsx file in the "file" field.'},
                        status=status.HTTP_400_BAD_REQUEST)
    try:
        headers, rows = _read_xlsx(f)
    except Exception as exc:    # noqa: BLE001
        return Response({'detail': f'Could not read the spreadsheet: {exc}'},
                        status=status.HTTP_400_BAD_REQUEST)
    if not rows:
        return Response({'detail': 'No data rows found.'}, status=status.HTTP_400_BAD_REQUEST)

    idx = _build_employee_index()
    valid_codes = set(get_leave_rules().keys())
    batch = (request.data.get('batch') or 'leave-opening-upload').strip()[:60]
    created, replaced, errors = 0, 0, []
    for i, row in enumerate(rows, start=2):   # row 1 = header
        emp_ref = _first(row, 'employee', 'name', 'full name', 'full_name')
        code = (str(_first(row, 'leave_type', 'leave type', 'type', 'leave') or '')).lower().strip()
        as_at = _date(_first(row, 'as_at_date', 'as at date', 'as_at', 'as at', 'date'))
        prof, perr = _resolve_profile(emp_ref, idx)
        if prof is None:
            errors.append({'row': i, 'error': f'Employee {perr}'}); continue
        if code not in valid_codes:
            errors.append({'row': i, 'error': f'Unknown leave type {code!r} (use one of {sorted(valid_codes)})'}); continue
        if as_at is None:
            errors.append({'row': i, 'error': 'Missing/invalid as_at_date (use YYYY-MM-DD)'}); continue
        # REPLACE, don't pile up. Re-uploading a corrected file for the same employee, leave
        # type and as-at date means "this figure was wrong" — it does not mean "keep both and
        # pick one at random", which is what created 63 colliding rows and bug c2888ba7. One
        # row per (employee, type, as-at date), so a correction cannot lose to its predecessor.
        # One row's problem must not abandon the file half-written. Before this, a key that
        # already held two rows (63 did) raised MultipleObjectsReturned and 500'd the upload
        # mid-way through, with some rows saved and some not — worse than the bug being fixed.
        try:
            _, was_created = LeaveOpeningBalance.objects.update_or_create(
                profile=prof, leave_type_code=code, as_at_date=as_at,
                defaults={
                    'entitlement_days': _dec(_first(row, 'entitlement', 'entitlement_days', 'entitlement days')),
                    'opening_balance_days': _dec(_first(row, 'opening_balance', 'opening balance', 'opening', 'balance')),
                    'accrued_days': _dec(_first(row, 'accrued', 'accrued_days', 'accrued days')),
                    'batch': batch,
                    'uploaded_by': request.user if request.user.is_authenticated else None,
                })
        except LeaveOpeningBalance.MultipleObjectsReturned:
            # Legacy duplicates from before the unique constraint: keep the newest, correct it,
            # drop the rest. The upload carries on.
            dupes = list(LeaveOpeningBalance.objects
                         .filter(profile=prof, leave_type_code=code, as_at_date=as_at)
                         .order_by('-created_at'))
            keep = dupes[0]
            LeaveOpeningBalance.objects.filter(
                id__in=[d.id for d in dupes[1:]]).delete()
            keep.entitlement_days = _dec(_first(row, 'entitlement', 'entitlement_days', 'entitlement days'))
            keep.opening_balance_days = _dec(_first(row, 'opening_balance', 'opening balance', 'opening', 'balance'))
            keep.accrued_days = _dec(_first(row, 'accrued', 'accrued_days', 'accrued days'))
            keep.batch = batch
            keep.uploaded_by = request.user if request.user.is_authenticated else None
            keep.save()
            was_created = False
        except Exception as exc:      # noqa: BLE001 — one row must not abandon the file
            # Deliberately broad, and NOT silent: the row is reported back to HR in `errors`
            # and logged with the traceback so it can be diagnosed. The alternative is a 500
            # part-way through a file, with some balances written and some not.
            log.warning('leave opening upload: row %s failed', i, exc_info=True)
            errors.append({'row': i, 'error': f'Could not save: {type(exc).__name__}'})
            continue
        created += 1
        if not was_created:
            replaced += 1
    payload = {'processed': len(rows), 'created': created, 'replaced': replaced,
               'failed': len(errors), 'errors': errors,
               'message': f'{created} of {len(rows)} opening-balance row(s) loaded'
                          + (f' · {replaced} corrected an earlier figure' if replaced else '')
                          + (f' · {len(errors)} need attention (see detail below)' if errors else '') + '.'}
    if created == 0 and errors:
        payload['detail'] = ('No rows loaded — every row failed validation. '
                             + '; '.join(e['error'] for e in errors[:5])
                             + (' …' if len(errors) > 5 else ''))
        return Response(payload, status=status.HTTP_400_BAD_REQUEST)
    return Response(payload)


# ───────────────────────── leave approvers (2cdd6333) ─────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def upload_leave_approvers(request):
    denied = _gate(request, capability='manage_leave_admin')
    if denied is not None:
        return denied
    f = request.FILES.get('file')
    if f is None:
        return Response({'detail': 'Attach an .xlsx file in the "file" field.'},
                        status=status.HTTP_400_BAD_REQUEST)
    try:
        headers, rows = _read_xlsx(f)
    except Exception as exc:    # noqa: BLE001
        return Response({'detail': f'Could not read the spreadsheet: {exc}'},
                        status=status.HTTP_400_BAD_REQUEST)
    if not rows:
        return Response({'detail': 'No data rows found.'}, status=status.HTTP_400_BAD_REQUEST)

    idx = _build_employee_index()
    updated, errors = 0, []
    for i, row in enumerate(rows, start=2):
        emp_ref = _first(row, 'employee', 'name', 'full name', 'full_name')
        appr_ref = _first(row, 'approver', 'reports_to', 'reports to', 'manager', 'leave_approver', 'leave approver')
        prof, perr = _resolve_profile(emp_ref, idx)
        if prof is None:
            errors.append({'row': i, 'error': f'Employee {perr}'}); continue
        appr_emp, aerr = _match_employee(appr_ref, idx)
        if appr_emp is None:
            errors.append({'row': i, 'error': f'Approver {aerr}'}); continue
        prof.manager = appr_emp
        prof.save(update_fields=['manager'])
        updated += 1
    payload = {'processed': len(rows), 'updated': updated, 'failed': len(errors), 'errors': errors,
               'message': f'{updated} of {len(rows)} approver assignment(s) updated'
                          + (f' · {len(errors)} need attention (see detail below)' if errors else '') + '.'}
    if updated == 0 and errors:
        payload['detail'] = ('No approvers assigned — every row failed. '
                             + '; '.join(f"Row {e['row']}: {e['error']}" for e in errors[:5])
                             + (' …' if len(errors) > 5 else ''))
        return Response(payload, status=status.HTTP_400_BAD_REQUEST)
    return Response(payload)
