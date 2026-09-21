"""supplier_recon/statement_views.py — supplier-statement upload, matching and
review API. Mounted under ``/api/v1/supplier-recon/`` (see api_router.py):

    POST   lines/{line_id}/statements/upload/   upload + parse + match a statement
    GET    lines/{line_id}/statements/          list statements on a supplier line
    GET    statements/{id}/                      header + parsed lines + matches
    POST   statements/{id}/match/                re-run the match (idempotent)
    DELETE statements/{id}/                       remove a mis-uploaded statement

This module moves no money. Matching writes a Pay-now / Hold / Do-not-pay /
Investigate recommendation the CFO acts on in FNB. Every queryset is scoped to
the entities the caller is permitted in, through the run behind the line.
"""
from __future__ import annotations

import hashlib

from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404
from rest_framework import status as http
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from .constants import StatementStatus
from .models import SupplierReconLine
from .permissions import CanPrepareRecon, CanViewRecon, can_access_company
from .statement_matching import match_statement, summarise
from .statement_models import SupplierStatement
from .statement_parser import StatementParseError, parse_statement

MAX_UPLOAD_BYTES = 10 * 1024 * 1024   # 10 MB — a statement is a small file


def _company_currency(company) -> str:
    cur = getattr(company, 'base_currency', None)
    code = getattr(cur, 'code', None) or (str(cur) if cur else None)
    return (code or 'BWP').upper()[:3]


def _get_line(line_id, user) -> SupplierReconLine:
    line = get_object_or_404(
        SupplierReconLine.objects.select_related('run', 'run__company', 'supplier'),
        pk=line_id)
    if not can_access_company(user, line.run.company_id):
        raise PermissionDenied('This supplier belongs to another entity.')
    return line


def _get_statement(statement_id, user) -> SupplierStatement:
    stmt = get_object_or_404(
        SupplierStatement.objects.select_related('line', 'line__run', 'line__supplier'),
        pk=statement_id)
    if not can_access_company(user, stmt.line.run.company_id):
        raise PermissionDenied('This statement belongs to another entity.')
    return stmt


def _line_dict(sl):
    return {
        'id': str(sl.id), 'row_number': sl.row_number, 'line_type': sl.line_type,
        'reference': sl.reference, 'doc_date': sl.doc_date,
        'description': sl.description, 'amount': str(sl.amount),
        'running_balance': (str(sl.running_balance)
                            if sl.running_balance is not None else None),
    }


def _match_dict(mm):
    inv = mm.recon_item.invoice.invoice_number if mm.recon_item else None
    return {
        'id': str(mm.id),
        'match_type': mm.match_type,
        'proposal': mm.proposal,
        'variance': str(mm.variance),
        'proposed_amount': str(mm.proposed_amount),
        'reason': mm.reason,
        'statement_reference': mm.statement_line.reference if mm.statement_line else None,
        'omni_invoice': inv,
        'omni_invoice_item': str(mm.recon_item_id) if mm.recon_item_id else None,
    }


def _statement_detail(stmt) -> dict:
    matches = list(stmt.matches.select_related(
        'statement_line', 'recon_item', 'recon_item__invoice').all())
    return {
        'id': str(stmt.id),
        'line_id': str(stmt.line_id),
        'supplier': stmt.line.supplier.name,
        'period': stmt.line.run.period_label,
        'file_name': stmt.file_name,
        'currency': stmt.currency,
        'statement_date': stmt.statement_date,
        'opening_balance': (str(stmt.opening_balance)
                            if stmt.opening_balance is not None else None),
        'closing_balance': (str(stmt.closing_balance)
                            if stmt.closing_balance is not None else None),
        'status': stmt.status,
        'parse_error': stmt.parse_error,
        'uploaded_by': (stmt.uploaded_by.get_full_name() or stmt.uploaded_by.username)
                       if stmt.uploaded_by else None,
        'uploaded_at': stmt.created_at,
        'matched_at': stmt.matched_at,
        'lines': [_line_dict(sl) for sl in stmt.lines.all()],
        'matches': [_match_dict(mm) for mm in matches],
        'summary': summarise(matches),
    }


@api_view(['POST'])
@permission_classes([CanPrepareRecon])
@parser_classes([MultiPartParser, FormParser])
def upload_statement(request, line_id):
    line = _get_line(line_id, request.user)
    f = request.FILES.get('file')
    if f is None:
        return Response({'detail': 'Attach a statement file under "file".'},
                        status=http.HTTP_400_BAD_REQUEST)
    if f.size and f.size > MAX_UPLOAD_BYTES:
        return Response({'detail': 'File is larger than 10 MB.'},
                        status=http.HTTP_400_BAD_REQUEST)

    data = f.read()
    checksum = hashlib.sha256(data).hexdigest()

    # Currency must match the entity's base currency (priority-2 validation).
    currency = (request.data.get('currency') or _company_currency(line.run.company)).upper()[:3]
    base = _company_currency(line.run.company)
    if currency != base:
        return Response(
            {'detail': f'Statement currency {currency} does not match the '
                       f'entity currency {base}.'},
            status=http.HTTP_400_BAD_REQUEST)

    # Duplicate-file control: same bytes already loaded for this supplier month.
    if SupplierStatement.objects.filter(line=line, checksum=checksum).exists():
        return Response(
            {'detail': 'This exact file has already been uploaded for this '
                       'supplier and month.'},
            status=http.HTTP_409_CONFLICT)

    try:
        parsed = parse_statement(data, f.name)
    except StatementParseError as e:
        return Response({'detail': str(e)}, status=http.HTTP_400_BAD_REQUEST)

    try:
        with transaction.atomic():
            stmt = SupplierStatement(
                line=line, file_name=(f.name or '')[:400],
                content_type=(f.content_type or '')[:120], file_size=f.size or len(data),
                checksum=checksum, currency=currency,
                statement_date=parsed.statement_date,
                opening_balance=parsed.opening_balance,
                closing_balance=parsed.closing_balance,
                status=StatementStatus.PARSED, uploaded_by=request.user)
            stmt.original_file.save((f.name or 'statement')[:400],
                                    ContentFile(data), save=False)
            stmt.save()
            from .statement_models import SupplierStatementLine
            SupplierStatementLine.objects.bulk_create([
                SupplierStatementLine(
                    statement=stmt, row_number=pl.row_number, line_type=pl.line_type,
                    reference=pl.reference, doc_date=pl.doc_date,
                    description=pl.description, amount=pl.amount,
                    running_balance=pl.running_balance, raw=pl.raw)
                for pl in parsed.lines])
    except IntegrityError:
        return Response(
            {'detail': 'This exact file has already been uploaded for this '
                       'supplier and month.'},
            status=http.HTTP_409_CONFLICT)

    match_statement(stmt)
    data_out = _statement_detail(stmt)
    data_out['parse_warnings'] = parsed.errors
    return Response(data_out, status=http.HTTP_201_CREATED)


@api_view(['GET'])
@permission_classes([CanViewRecon])
def list_statements(request, line_id):
    line = _get_line(line_id, request.user)
    out = []
    qs = (line.statements.select_related('uploaded_by')
          .annotate(n_lines=Count('lines')).all())
    for stmt in qs:
        out.append({
            'id': str(stmt.id), 'file_name': stmt.file_name,
            'status': stmt.status, 'currency': stmt.currency,
            'statement_date': stmt.statement_date,
            'closing_balance': (str(stmt.closing_balance)
                                if stmt.closing_balance is not None else None),
            'uploaded_at': stmt.created_at,
            'uploaded_by': (stmt.uploaded_by.get_full_name() or stmt.uploaded_by.username)
                           if stmt.uploaded_by else None,
            'line_count': stmt.n_lines,
        })
    return Response({'line_id': str(line.id), 'statements': out})


@api_view(['GET', 'DELETE'])
@permission_classes([CanPrepareRecon])
def statement_detail(request, statement_id):
    stmt = _get_statement(statement_id, request.user)
    if request.method == 'DELETE':
        stmt.delete()
        return Response(status=http.HTTP_204_NO_CONTENT)
    return Response(_statement_detail(stmt))


@api_view(['POST'])
@permission_classes([CanPrepareRecon])
def rematch_statement(request, statement_id):
    stmt = _get_statement(statement_id, request.user)
    match_statement(stmt)
    return Response(_statement_detail(stmt))
