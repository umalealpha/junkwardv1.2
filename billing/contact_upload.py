"""
billing/contact_upload.py — bulk vendor / customer upload, scoped to one
company at a time.

CFO directive 2026-05-18: every legal entity has its own supplier list
(ADIC, UNI, GCX, RSA, ADSA, VCM, …). Upload one xlsx/csv per entity; the
endpoint stamps every row with the company picked in the topbar (or
passed explicitly).

  POST /api/v1/admin/cfo-upload-vendors/
       file               .xlsx or .csv
       company            Company.code (e.g. ADIC, UNI, GCX). Required.
       contact_type       'vendor' | 'customer' (default: 'vendor')
       commit             'true' to actually insert; absent = dry-run
  GET  /api/v1/admin/cfo-upload-vendors/template/  — blank CSV template

Required columns (case-insensitive header row):
    name                  *required
    registration_number
    tax_id
    email
    phone
    address
    currency_code         default 'BWP'
    payment_terms_days    default 30
    is_resident           default 'true' for BW, 'false' otherwise
    notes                 (goes into Contact.address suffix if no address)

Idempotency: rows are matched on (company, contact_type, lower(name)).
A re-run patches missing fields onto existing rows instead of inserting
duplicates.
"""
from __future__ import annotations

import csv
import io
from typing import Any

from django.contrib.auth.models import User
from django.db import transaction
from django.http import HttpResponse
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response

from core.models import AuditLog, Company, Currency, user_can_write_company
from core.permissions import CanBulkUploadMasterData
from .models import Contact


REQUIRED_COLUMNS = [
    'name', 'registration_number', 'tax_id', 'email', 'phone',
    'address', 'currency_code', 'payment_terms_days', 'is_resident',
    'notes',
]


def _read_workbook(file_obj, filename: str) -> list[dict]:
    """Return a list of {column: value} dicts from xlsx or csv."""
    name = (filename or '').lower()
    if name.endswith('.csv'):
        text = file_obj.read().decode('utf-8-sig', errors='replace')
        reader = csv.DictReader(io.StringIO(text))
        return [_norm_keys(row) for row in reader]

    if not (name.endswith('.xlsx') or name.endswith('.xls')):
        raise ValueError('Only .xlsx and .csv are accepted.')

    import openpyxl  # noqa: WPS433
    wb = openpyxl.load_workbook(file_obj, data_only=True, read_only=True)
    sheet = wb.active
    rows_iter = sheet.iter_rows(values_only=True)
    try:
        header = next(rows_iter)
    except StopIteration:
        return []
    header_clean = [(_clean(h) or '').lower() for h in header]
    out: list[dict] = []
    for row in rows_iter:
        if not row or all(v in (None, '') for v in row):
            continue
        d = {header_clean[i]: row[i] for i in range(min(len(header_clean), len(row)))}
        out.append(_norm_keys(d))
    return out


def _clean(v: Any) -> str:
    if v is None:
        return ''
    return str(v).strip()


def _norm_keys(d: dict) -> dict:
    """Lowercase + trim keys."""
    return {(k or '').strip().lower(): v for k, v in d.items()}


def _bool(v: Any, default: bool = True) -> bool:
    if v in (None, ''):
        return default
    return str(v).strip().lower() in ('true', '1', 'yes', 'y', 't')


def _int(v: Any, default: int) -> int:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


@api_view(['POST'])
@parser_classes([MultiPartParser, FormParser])
# SECURITY FIX (2026-08-25, Manus nine-area retest P1): this was
# @permission_classes([IsAuthenticated]) — any signed-in staffer could upload a
# workbook and create/patch vendor master rows in ANY entity, because the company
# comes from a request parameter rather than from the caller's access. A vendor
# row is the first half of paying someone.
@permission_classes([CanBulkUploadMasterData])
def cfo_upload_vendors(request):
    f = request.FILES.get('file')
    if not f:
        return Response({'detail': 'file is required.'}, status=400)
    if f.size > 10 * 1024 * 1024:
        return Response({'detail': 'File too large (10 MB limit).'}, status=413)

    company_code = (request.data.get('company') or '').strip()
    if not company_code:
        return Response({'detail': 'company is required (e.g. ADIC, UNI, GCX).'}, status=400)
    company = Company.objects.filter(code__iexact=company_code).first()
    if not company:
        return Response({'detail': f'Company code {company_code!r} not found.'}, status=400)

    # Entity scope is a SEPARATE gate from the role: holding `cfo-upload` says
    # you may bulk-upload, not that you may write to every legal entity. The
    # company arrives as a parameter, so without this an authorised uploader for
    # ADSA could load suppliers straight into ADIC. Superusers / administrators /
    # the CFO pass automatically (see core.models.allowed_company_ids).
    if not user_can_write_company(request.user, company.id):
        return Response(
            {'detail': f'You do not have write access to {company.code}.'},
            status=403,
        )

    contact_type = (request.data.get('contact_type') or 'vendor').strip().lower()
    if contact_type not in {ct.value for ct in Contact.ContactType}:
        return Response(
            {'detail': f'contact_type must be one of '
                       f'{[ct.value for ct in Contact.ContactType]}.'},
            status=400,
        )

    commit = (request.data.get('commit') or '').lower() in ('true', '1', 'yes')

    try:
        rows = _read_workbook(f, f.name or '')
    except Exception as e:  # noqa: BLE001
        return Response({'detail': f'Could not read workbook: {e}'}, status=400)

    if not rows:
        return Response({'detail': 'No data rows found.'}, status=400)

    created  = 0
    updated  = 0
    skipped  = 0
    errors:  list[dict] = []

    with transaction.atomic():
        sp = transaction.savepoint()

        for idx, row in enumerate(rows, start=2):  # row 1 is header
            try:
                name = _clean(row.get('name'))
                if not name:
                    skipped += 1
                    errors.append({'row': idx, 'error': 'name is required.'})
                    continue

                currency_code = (_clean(row.get('currency_code')) or 'BWP').upper()
                currency, _ = Currency.objects.get_or_create(
                    code=currency_code,
                    defaults={'name': currency_code, 'symbol': currency_code},
                )

                # Idempotency key: same company + contact_type + name (case-insensitive)
                existing = Contact.objects.filter(
                    company=company,
                    contact_type=contact_type,
                    name__iexact=name,
                ).first()

                defaults = {
                    'name':                name[:300],
                    'registration_number': _clean(row.get('registration_number'))[:50] or None,
                    'tax_id':              _clean(row.get('tax_id'))[:50] or None,
                    'email':               _clean(row.get('email'))[:254] or None,
                    'phone':               _clean(row.get('phone'))[:50] or None,
                    'address':             _clean(row.get('address')) or None,
                    'currency_code':       currency,
                    'payment_terms_days':  _int(row.get('payment_terms_days'), 30),
                    'is_resident':         _bool(row.get('is_resident'), True),
                    'is_active':           True,
                    'company':             company,
                    'contact_type':        contact_type,
                }

                if existing:
                    # Only patch fields that are currently blank — never overwrite
                    # values an operator has typed in by hand on the UI.
                    patched = False
                    for k, v in defaults.items():
                        if k in ('company', 'contact_type'):
                            continue
                        cur = getattr(existing, k, None)
                        if (cur in (None, '', 0, 30)) and v not in (None, ''):
                            setattr(existing, k, v)
                            patched = True
                    if patched and commit:
                        existing.save()
                    updated += 1
                    continue

                if commit:
                    Contact.objects.create(**defaults)
                created += 1

            except Exception as e:  # noqa: BLE001
                errors.append({'row': idx, 'error': str(e)})

        if commit:
            transaction.savepoint_commit(sp)
        else:
            transaction.savepoint_rollback(sp)

    # ONE audit row per committed upload (2026-08-25). A dry-run writes nothing,
    # so it leaves no trail — deliberate: the trail records changes to master
    # data, not previews of them.
    if commit:
        AuditLog.objects.create(
            table_name='billing.Contact',
            record_id=str(company.id),
            action=AuditLog.Action.CREATE,
            new_values={
                'company':      company.code,
                'contact_type': contact_type,
                'file_name':    (f.name or '')[:200],
                'rows_seen':    len(rows),
                'created':      created,
                'updated':      updated,
                'skipped':      skipped,
                'error_count':  len(errors),
            },
            user=request.user,
            description=(f'Bulk {contact_type} upload into {company.code}: '
                         f'{created} created, {updated} updated, {skipped} skipped'),
        )

    return Response({
        'company':      company.code,
        'contact_type': contact_type,
        'commit':       commit,
        'rows_seen':    len(rows),
        'created':      created,
        'updated':      updated,
        'skipped':      skipped,
        'errors':       errors[:50],   # cap response size
    })


@api_view(['GET'])
@permission_classes([CanBulkUploadMasterData])
def cfo_upload_vendors_template(request):
    """Blank CSV template with the canonical column order."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(REQUIRED_COLUMNS)
    # Two sample rows so people can see what to fill in
    w.writerow([
        'Example Vendor (Pty) Ltd', 'BW00012345', 'BV00098765',
        'accounts@example.com', '+267 312 1234',
        'Plot 1234, Gaborone', 'BWP', '30', 'true', '',
    ])
    w.writerow([
        'Another Supplier Co', '', '', '', '+27 11 555 0000',
        '15 Sandton Drive, Johannesburg', 'ZAR', '60', 'false', '',
    ])
    resp = HttpResponse(buf.getvalue(), content_type='text/csv')
    resp['Content-Disposition'] = (
        'attachment; filename="vendors-template.csv"'
    )
    return resp
