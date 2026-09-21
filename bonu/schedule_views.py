"""bonu/schedule_views.py — the editable BONU schedule API.

Unlike the read-only ledger views, this is where staff CAPTURE the schedule in
Omni instead of Excel: list sheets, page through rows, add a row, edit a row's
cells, delete a row. Same finance/management access gate as the rest of BONU.
"""
from __future__ import annotations

from decimal import Decimal

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import BonuScheduleRow, BonuScheduleSheet
from .schedule import _money, reconcile, sheet_total, stated_total
from .schedule_kpis import compute as compute_kpis
from .schedule_validate import run_all as validate_all
from .views import _deny

ROW_PAGE = 100


# Columns whose values are a small, closed set — 'Paid' / 'paid' / 'Paid ' were
# three separate values in the live data and split every group-by. Deliberately
# narrow: 'Case Matter' is more free-text than enumerated (Divorce, Debt
# Recovery, Estate Administration, … open list) and normalising it would corrupt
# the reader's own words.
_ENUMERATED_COLUMNS = ('status', 'payment status', 'kyc status',
                       'category', 'payment method')


def _normalise_cell(column: str, raw) -> str:
    """Trim every cell on save; title-case the small, fixed enumerations so
    'Paid' / 'paid' / 'Paid ' become one value. All other cells round-trip."""
    s = str(raw or '').strip()
    if not s:
        return s
    col_key = column.strip().lower()
    if any(col_key == e or col_key.endswith(' ' + e) for e in _ENUMERATED_COLUMNS):
        # `w.capitalize()` lowercases every non-first letter, so a live 'KYC'
        # became 'Kyc'. Skip words that are already all upper-case, so acronyms
        # round-trip intact.
        words = s.split()
        if any(w and w[0].islower() for w in words):
            return ' '.join(w if w.isupper() else w.capitalize() for w in words)
        return s
    return s


def _firm_column(sheet):
    """The column that names the law firm, if the sheet has one (for the filter
    dropdown). Matches 'Law Firm Name' / 'Firm' / 'Claim Expenses' (the unpaid
    sheet's firm column)."""
    for c in sheet.columns:
        cl = c.lower()
        if 'law firm' in cl or 'firm name' in cl or cl == 'firm' or cl == 'claim expenses':
            return c
    return ''


def _sheet_brief(s):
    detail, stated = sheet_total(s), stated_total(s)
    return {'key': s.key, 'title': s.title, 'columns': s.columns,
            'amount_column': s.amount_column, 'rows': s.rows.count(),
            'total': str(detail),
            # The author's own 'Totals' line, shown beside ours so a disagreement
            # is visible rather than us picking a winner.
            'stated_total': None if stated is None else str(stated),
            'foots': None if stated is None else (detail == stated),
            'source_note': s.source_note}


def _row(r):
    return {'id': str(r.id), 'position': r.position, 'cells': r.cells,
            'note': r.note, 'updated_by': r.updated_by_email,
            'updated_at': r.updated_at.isoformat() if r.updated_at else None}


def add_schedule_row(sheet, cells, user, *, note='', audit=''):
    """Append one well-formed row to a schedule sheet.

    Shared by the grid's add-row button and the guided Capture forms so both go
    through the same normalisation, column-cleaning and audit trail. Only known
    columns survive; everything else on the sheet is blanked so the row is
    well-formed and never carries a stray key the sheet total cannot read.
    """
    clean = {col: _normalise_cell(col, (cells or {}).get(col, '')) for col in sheet.columns}
    last = sheet.rows.order_by('-position').first()
    row = BonuScheduleRow(sheet=sheet, position=(last.position + 1 if last else 0),
                          cells=clean, note=str(note or ''),
                          updated_by_email=getattr(user, 'email', '') or '')
    row.save(audit_user=user,
             audit_description=audit or f'Added a row to BONU schedule {sheet.key}')
    return row


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def schedule_sheets(request):
    """Every sheet with its columns + row count + total, plus the Excel-vs-Omni
    claims reconciliation."""
    denied = _deny(request)
    if denied:
        return denied
    sheets = [_sheet_brief(s) for s in BonuScheduleSheet.objects.all()]
    return Response({'sheets': sheets, 'reconciliation': reconcile(),
                     'validation': validate_all()})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def schedule_kpis(request):
    """Every ratio Omni derives from the schedule, alongside the author's own
    figure for the same thing. Same 'our figure beside the author's' pattern the
    sheet totals use."""
    denied = _deny(request)
    if denied:
        return denied
    return Response(compute_kpis())


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def schedule_validate(request):
    """Run every pre-flight check against the CURRENTLY loaded schedule and
    return findings sorted most-severe first. Fresh run — nothing cached, so a
    fix reflects the moment the row is saved."""
    denied = _deny(request)
    if denied:
        return denied
    return Response(validate_all())


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def schedule_rows(request, key):
    """GET: one page of rows for a sheet (?offset=&limit=). POST: add a row."""
    denied = _deny(request)
    if denied:
        return denied
    try:
        sheet = BonuScheduleSheet.objects.get(key=key)
    except BonuScheduleSheet.DoesNotExist:
        return Response({'detail': 'No such schedule sheet.'}, status=404)

    if request.method == 'GET':
        try:
            offset = max(0, int(request.query_params.get('offset', 0)))
            limit = min(500, max(1, int(request.query_params.get('limit', ROW_PAGE))))
        except (TypeError, ValueError):
            offset, limit = 0, ROW_PAGE

        # Smart search — applied across the WHOLE sheet server-side (not just the
        # page): free-text over any cell, an exact law-firm match, and an amount
        # range on the money column. The count + total reflect the filter.
        q = (request.query_params.get('q') or '').strip().lower()
        firm = (request.query_params.get('firm') or '').strip()
        firm_col = _firm_column(sheet)

        def _f(name):
            v = request.query_params.get(name)
            if v in (None, ''):
                return None
            try:
                return _money(v)
            except Exception:
                return None
        amin, amax = _f('amount_min'), _f('amount_max')
        acol = sheet.amount_column

        def keep(r):
            cells = r.cells
            if q and not any(q in str(v).lower() for v in cells.values()):
                return False
            if firm and firm_col and str(cells.get(firm_col, '')).strip() != firm:
                return False
            if acol and (amin is not None or amax is not None):
                a = _money(cells.get(acol))
                if amin is not None and a < amin:
                    return False
                if amax is not None and a > amax:
                    return False
            return True

        all_rows = list(sheet.rows.all())
        firms = sorted({str(r.cells.get(firm_col, '')).strip()
                        for r in all_rows if firm_col and str(r.cells.get(firm_col, '')).strip()})
        matched = [r for r in all_rows if keep(r)]
        total = len(matched)
        filtered_total = (sum((_money(r.cells.get(acol)) for r in matched), Decimal('0')).quantize(Decimal('0.01'))
                          if acol else Decimal('0.00'))
        rows = [_row(r) for r in matched[offset:offset + limit]]
        return Response({'sheet': _sheet_brief(sheet), 'rows': rows,
                         'offset': offset, 'limit': limit, 'total': total,
                         'firm_column': firm_col, 'firms': firms,
                         'filtered_total': str(filtered_total)})

    # POST — add a blank/prefilled row at the end
    cells = request.data.get('cells') or {}
    if not isinstance(cells, dict):
        return Response({'detail': 'cells must be an object keyed by column label.'}, status=400)

    # The claims sheet IS the supplier ledger — so the grid (one tab away, the path
    # Kutlo used before Capture) must run the same two guards, or the control has an
    # open side door. No override here: an over-cap bill goes through the Capture
    # form, which records the reason. Bulk workbook upload stays unguarded — it
    # loads the historical book (Fable H29 creation-point sweep, 17 Aug 2026).
    if sheet.key == 'claims':
        from bonu.capture import (LEGAL_BENEFIT_CAP, _CLAIMS_AMOUNT_COL,
                                  _CLAIMS_INVOICE_COL, _CLAIMS_MEMBER_COL,
                                  _duplicate_invoice, _member_billed_to_date)
        if _duplicate_invoice(sheet, cells.get(_CLAIMS_INVOICE_COL)) is not None:
            return Response({'code': 'DUPLICATE_INVOICE',
                'detail': f'Invoice {cells.get(_CLAIMS_INVOICE_COL)} is already recorded — '
                          f'if it is genuinely a different bill, give it its own reference.'},
                status=409)
        member = cells.get(_CLAIMS_MEMBER_COL)
        if str(member or '').strip():
            would_be = _member_billed_to_date(sheet, member) + _money(cells.get(_CLAIMS_AMOUNT_COL))
            if would_be > LEGAL_BENEFIT_CAP:
                return Response({'code': 'CAP_EXCEEDED',
                    'detail': f'{member} would pass the P{LEGAL_BENEFIT_CAP:,.0f} legal-benefit '
                              f'cap (P{would_be:,.2f}). Record it through the Capture form, which '
                              f'takes a reason.'},
                    status=409)

    row = add_schedule_row(sheet, cells, request.user)
    return Response(_row(row), status=201)


@api_view(['PATCH', 'DELETE'])
@permission_classes([IsAuthenticated])
def schedule_row_detail(request, row_id):
    """PATCH: edit a row's cells (and note). DELETE: remove the row."""
    denied = _deny(request)
    if denied:
        return denied
    try:
        row = BonuScheduleRow.objects.select_related('sheet').get(id=row_id)
    except BonuScheduleRow.DoesNotExist:
        return Response({'detail': 'No such row.'}, status=404)

    if request.method == 'DELETE':
        key = row.sheet.key
        row.delete()
        return Response({'ok': True, 'sheet': key}, status=status.HTTP_200_OK)

    cells = request.data.get('cells')
    if cells is not None:
        if not isinstance(cells, dict):
            return Response({'detail': 'cells must be an object keyed by column label.'}, status=400)
        # only accept known columns; edits round-trip as strings
        row.cells = {col: _normalise_cell(col, cells.get(col, row.cells.get(col, '')))
                     for col in row.sheet.columns}
    if 'note' in request.data:
        row.note = str(request.data.get('note') or '')
    row.updated_by_email = getattr(request.user, 'email', '') or ''
    row.save(audit_user=request.user, audit_description=f'Edited BONU schedule row {row.id}')
    return Response(_row(row))


# ---------------------------------------------------------------------------
# Insights + export + history + upload-diff (CFO 2026-08-12, nine upgrades)
# ---------------------------------------------------------------------------
import csv as _csv
import io as _io

from django.http import HttpResponse


def _csv_safe(v):
    """Neutralise CSV/Excel formula injection: a cell of third-party text (firm
    refs, client names) that opens with = + @ or a non-numeric - executes as a
    formula when finance opens the export. Prefix it with a single quote. Plain
    numbers (incl. negatives) are left untouched."""
    s = '' if v is None else str(v)
    if s[:1] in ('=', '+', '@', '\t', '\r'):
        return "'" + s
    if s[:1] == '-':
        try:
            float(s.replace(',', ''))
        except ValueError:
            return "'" + s
    return s


def _safe_row(values):
    return [_csv_safe(v) for v in values]

from . import schedule as _sched
from . import schedule_insights as _ins


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def schedule_insights(request):
    """Everything for the Insights tab in one call: the headline summary plus the
    member-limit, duplicate, premium, cost and bill-match sections."""
    denied = _deny(request)
    if denied:
        return denied
    return Response({
        'summary': _ins.insights_summary(),
        'members': _ins.member_limits(),
        'duplicates': _ins.duplicate_claims(),
        'premium': _ins.premium_gaps(),
        'cost_by_firm': _ins.cost_by_firm(),
        'cost_by_case': _ins.cost_by_case(),
        'bills': _ins.bill_matches(),
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def schedule_gap(request):
    """The gap-to-ledger to-do list. ?format=csv streams it as a file."""
    denied = _deny(request)
    if denied:
        return denied
    data = _ins.gap_to_ledger()
    # NB: not '?format=' — DRF reserves that for content negotiation and 404s on an
    # unknown renderer. Use '?download=csv'.
    if request.query_params.get('download') in ('csv', '1', 'true') and data.get('available'):
        buf = _io.StringIO()
        w = _csv.writer(buf)
        w.writerow(['Law Firm', 'Invoice Ref', 'Client', 'Month', 'Amount (P)'])
        for r in data['unmatched']:
            w.writerow(_safe_row([r['firm'], r['ref'], r['client'], r['month'], r['amount']]))
        resp = HttpResponse(buf.getvalue(), content_type='text/csv')
        resp['Content-Disposition'] = 'attachment; filename="bonu_claims_not_in_ledger.csv"'
        return resp
    return Response(data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def schedule_export(request, key):
    """One-click CSV of a whole sheet — the round-trip that lets people leave Excel."""
    denied = _deny(request)
    if denied:
        return denied
    try:
        sheet = BonuScheduleSheet.objects.get(key=key)
    except BonuScheduleSheet.DoesNotExist:
        return Response({'detail': 'No such schedule sheet.'}, status=404)
    buf = _io.StringIO()
    w = _csv.writer(buf)
    w.writerow(_safe_row(sheet.columns))
    for r in sheet.rows.all():
        w.writerow(_safe_row([r.cells.get(c, '') for c in sheet.columns]))
    resp = HttpResponse(buf.getvalue(), content_type='text/csv')
    resp['Content-Disposition'] = f'attachment; filename="bonu_{sheet.key}.csv"'
    return resp


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def schedule_row_history(request, row_id):
    """Who changed this row, and when — straight from the immutable audit log."""
    denied = _deny(request)
    if denied:
        return denied
    from core.models import AuditLog
    try:
        row = BonuScheduleRow.objects.get(id=row_id)
    except BonuScheduleRow.DoesNotExist:
        return Response({'detail': 'No such row.'}, status=404)
    logs = AuditLog.objects.filter(table_name='BonuScheduleRow', record_id=str(row.id))[:50]
    out = [{'action': l.action,
            'user': (l.user.get_full_name() or l.user.email) if l.user else 'system',
            'when': l.created_at.isoformat(),
            'description': l.description or '',
            'old': l.old_values, 'new': l.new_values} for l in logs]
    return Response({'row_id': str(row.id), 'history': out})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def schedule_upload(request):
    """Upload next month's workbook. ?preview=1 returns the DIFF only (added /
    removed per sheet, no write); without it, the workbook is imported (sheets
    replaced). Parsed server-side and the temp file deleted — data never leaves."""
    denied = _deny(request)
    if denied:
        return denied
    import os
    import tempfile
    f = request.FILES.get('file')
    if not f:
        return Response({'detail': 'No file uploaded.'}, status=400)
    if os.path.splitext(f.name)[1].lower() != '.xlsx':
        return Response({'detail': 'Upload an Excel .xlsx file.'}, status=400)
    if getattr(f, 'size', 0) > 60 * 1024 * 1024:
        return Response({'detail': 'File too large (max 60 MB).'}, status=400)
    preview = str(request.query_params.get('preview', '')).lower() in ('1', 'true', 'yes')
    tmp = tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False)
    try:
        for chunk in f.chunks():
            tmp.write(chunk)
        tmp.close()
        if preview:
            return Response({'preview': True, 'sheets': _sched.diff_workbook(tmp.name)})
        summary = _sched.import_workbook(tmp.name, source_note=f.name, commit=True,
                                         user=request.user)
        return Response({'imported': True, 'sheets': summary})
    except Exception:
        log = __import__('logging').getLogger('bonu')
        log.exception('BONU schedule upload failed for %r', f.name)
        return Response({'detail': "Couldn't read that workbook. Re-save it as .xlsx and try "
                         "again, or enter the rows by hand."}, status=400)
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
