"""
billing/vendor_address.py — focused, gated supplier-address editor.

CFO directive 2026-07-10 (Kao / Claims): give staff a simple way to add or fix
a supplier's ADDRESS — one at a time, or by Excel/CSV upload — without handing
them the full vendor-admin surface. Address only.

Endpoints (all gated to superusers + members of the `vendor_editor` group):
  GET   /api/v1/vendor-address/access/     -> {allowed, is_superuser}
  GET   /api/v1/vendor-address/list/?q=&company=&missing_only=&limit=
  PATCH /api/v1/vendor-address/<uuid>/     {address}
  POST  /api/v1/vendor-address/upload/     file, company, commit
  GET   /api/v1/vendor-address/template/   -> CSV template

Least privilege — only Contact.address is ever written. Vendor creation and
name/tax/bank edits stay in the vendor admin / cfo-upload. A Finance Manager
(approver-only) is blocked from editing vendor data, same rule as ContactViewSet.
"""
from __future__ import annotations

import csv
import io
import logging

from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponse
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.models import Company, user_is_approver_only
from .models import Contact
from .contact_upload import _read_workbook, _clean

logger = logging.getLogger(__name__)

VENDOR_EDITOR_GROUP = 'vendor_editor'
LIST_CAP = 200


def _resolve_company(val: str):
    """Accept a Company.code (e.g. ADIC) OR a UUID — the TopBar auto-injects
    the id on GETs, while forms send the code."""
    val = (val or '').strip()
    if not val:
        return None
    c = Company.objects.filter(code__iexact=val).first()
    if c:
        return c
    try:
        return Company.objects.filter(pk=val).first()
    except (ValueError, ValidationError):
        return None


def _can_edit(user) -> bool:
    return bool(
        user and user.is_authenticated
        and (user.is_superuser
             or user.groups.filter(name=VENDOR_EDITOR_GROUP).exists())
    )


def _require(user):
    if not _can_edit(user):
        raise PermissionDenied(
            'You need supplier-address access. Ask the CFO / HR to add you to '
            'the vendor-address editors.')
    # Same control as ContactViewSet: a Finance Manager approves vendor data,
    # never edits it.
    if user_is_approver_only(user):
        raise PermissionDenied(
            'A Finance Manager approves vendor records but does not edit them.')


def _row(c: Contact) -> dict:
    return {
        'id':         str(c.id),
        'name':       c.name,
        'company':    (c.company.code if c.company_id and c.company else ''),
        'address':    c.address or '',
        'has_address': bool((c.address or '').strip()),
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def vendor_address_access(request):
    return Response({
        'allowed':      _can_edit(request.user),
        'is_superuser': bool(request.user.is_superuser),
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def vendor_address_list(request):
    _require(request.user)
    qs = (Contact.objects
          .filter(contact_type=Contact.ContactType.VENDOR)
          .select_related('company'))
    company = _resolve_company(request.query_params.get('company'))
    if company:
        qs = qs.filter(company=company)
    q = (request.query_params.get('q') or '').strip()
    if q:
        qs = qs.filter(name__icontains=q)
    if (request.query_params.get('missing_only') or '').lower() in ('1', 'true', 'yes'):
        qs = qs.filter(address__isnull=True) | qs.filter(address='')
    qs = qs.order_by('name')
    total = qs.count()
    try:
        limit = min(int(request.query_params.get('limit') or LIST_CAP), LIST_CAP)
    except ValueError:
        limit = LIST_CAP
    return Response({
        'count':   total,
        'limit':   limit,
        'results': [_row(c) for c in qs[:limit]],
    })


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def vendor_address_update(request, pk):
    _require(request.user)
    c = Contact.objects.filter(pk=pk, contact_type=Contact.ContactType.VENDOR).first()
    if not c:
        return Response({'detail': 'Vendor not found.'}, status=404)
    if 'address' not in request.data:
        return Response({'detail': 'address is required.'}, status=400)
    new = (_clean(request.data.get('address')) or None)
    old = c.address
    c.address = new
    c.save(update_fields=['address', 'updated_at'])
    logger.info('vendor-address edit by %s on %s (%s): %r -> %r',
                request.user.username, c.id, c.name, old, new)
    return Response(_row(c))


@api_view(['POST'])
@parser_classes([MultiPartParser, FormParser])
@permission_classes([IsAuthenticated])
def vendor_address_upload(request):
    _require(request.user)
    f = request.FILES.get('file')
    if not f:
        return Response({'detail': 'file is required.'}, status=400)
    if f.size > 10 * 1024 * 1024:
        return Response({'detail': 'File too large (10 MB limit).'}, status=413)

    code = (request.data.get('company') or '').strip()
    if not code:
        return Response({'detail': 'company is required (e.g. ADIC).'}, status=400)
    company = _resolve_company(code)
    if not company:
        return Response({'detail': f'Company {code!r} not found.'}, status=400)

    commit = (request.data.get('commit') or '').lower() in ('true', '1', 'yes')

    try:
        rows = _read_workbook(f, f.name or '')
    except Exception as e:  # noqa: BLE001
        return Response({'detail': f'Could not read file: {e}'}, status=400)
    if not rows:
        return Response({'detail': 'No data rows found.'}, status=400)

    matched = updated = 0
    not_found: list[str] = []
    errors: list[dict] = []
    with transaction.atomic():
        sp = transaction.savepoint()
        for idx, row in enumerate(rows, start=2):   # row 1 is the header
            try:
                name = _clean(row.get('name'))
                address = _clean(row.get('address'))
                if not name:
                    errors.append({'row': idx, 'error': 'name is blank.'})
                    continue
                if not address:
                    errors.append({'row': idx, 'error': f'no address for {name!r}.'})
                    continue
                c = Contact.objects.filter(
                    company=company,
                    contact_type=Contact.ContactType.VENDOR,
                    name__iexact=name,
                ).first()
                if not c:
                    not_found.append(name)
                    continue
                matched += 1
                if c.address != address:
                    c.address = address
                    if commit:
                        c.save(update_fields=['address', 'updated_at'])
                    updated += 1
            except Exception as e:  # noqa: BLE001
                errors.append({'row': idx, 'error': str(e)})
        if commit:
            transaction.savepoint_commit(sp)
        else:
            transaction.savepoint_rollback(sp)

    logger.info('vendor-address upload by %s company=%s commit=%s matched=%s updated=%s not_found=%s',
                request.user.username, company.code, commit, matched, updated, len(not_found))
    return Response({
        'company':        company.code,
        'commit':         commit,
        'rows_seen':      len(rows),
        'matched':        matched,
        'updated':        updated,
        'not_found':      len(not_found),
        'not_found_names': not_found[:50],
        'errors':         errors[:50],
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def vendor_address_template(request):
    _require(request.user)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(['name', 'address'])
    w.writerow(['MANCON PTY LTD', 'P O Box 202464, Gaborone'])
    w.writerow(['Example Supplier (Pty) Ltd', 'Plot 1234, Gaborone'])
    resp = HttpResponse(buf.getvalue(), content_type='text/csv')
    resp['Content-Disposition'] = 'attachment; filename="supplier-addresses-template.csv"'
    return resp
