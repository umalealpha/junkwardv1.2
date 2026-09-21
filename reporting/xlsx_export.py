"""
reporting/xlsx_export.py — generic XLSX export for every CFO report.

CFO directive 2026-05-20: every report page must offer an "Export Excel"
button alongside the existing CSV. Rather than hand-write 15 different
exporters, this module reuses the existing ``build_*`` functions and
converts their JSON shape into a single-sheet openpyxl workbook.

Usage from a DRF view::

    from reporting.xlsx_export import build_report_xlsx_response
    return build_report_xlsx_response('general_ledger', params)

Supported report keys are listed in ``REPORT_REGISTRY``. Each entry
declares (a) the builder callable, (b) the arg-extractor that pulls
the right values from the query-param dict, (c) a *render* callable
that converts the report's return-dict into ``(filename, [Sheet, ...])``.

A ``Sheet`` is a small dataclass with title + headers + rows so the
single dispatcher can write multi-sheet workbooks where reports
naturally have multiple sections (e.g. BS with assets / liab / equity).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
from typing import Any, Callable

from django.http import HttpResponse
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from django.utils import timezone


# ---------------------------------------------------------------------------
# Styling
# ---------------------------------------------------------------------------

_NAVY  = 'FF0D1B2A'         # AD brand navy
_ORANGE = 'FFF4A623'        # AD brand orange
_LIGHT = 'FFF3F4F6'         # neutral row stripe
_GREY  = 'FF6B7280'         # secondary text

_HEADER_FILL  = PatternFill('solid', fgColor=_NAVY)
_HEADER_FONT  = Font(bold=True, color='FFFFFFFF', size=11)
_TOTAL_FILL   = PatternFill('solid', fgColor=_ORANGE)
_TOTAL_FONT   = Font(bold=True, color=_NAVY, size=11)
_SUB_FONT     = Font(bold=True, color=_NAVY)
_BORDER_THIN  = Border(
    left=Side(style='thin', color='FFCBD5E1'),
    right=Side(style='thin', color='FFCBD5E1'),
    top=Side(style='thin', color='FFCBD5E1'),
    bottom=Side(style='thin', color='FFCBD5E1'),
)


# ---------------------------------------------------------------------------
# Sheet data container
# ---------------------------------------------------------------------------

@dataclass
class Sheet:
    title: str
    headers: list[str]
    rows: list[list[Any]]
    # Indices in `rows` that should be rendered as bold subtotal / total
    bold_row_indices: list[int] = field(default_factory=list)
    total_row_index: int | None = None
    # Optional meta lines printed above the header row
    meta: list[str] = field(default_factory=list)
    # Optional currency for number formatting on amount columns (default BWP)
    currency: str = 'BWP'
    # Numeric column indices (0-based) — get number formatting
    numeric_cols: list[int] = field(default_factory=list)
    # Date column indices (0-based) — cells carry real date values and render as
    # yyyy-mm-dd so Excel can sort / filter-by-range / pivot them (Kago 2026-08-31).
    date_cols: list[int] = field(default_factory=list)
    # Integer column indices (0-based) — whole-number format, no decimals
    # (e.g. a day count, which must not read as "1.00").
    int_cols: list[int] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _coerce(v: Any) -> Any:
    """Coerce values for openpyxl. Strings/numbers pass through;
    Decimal converts to float; date/datetime pass through; None -> ''."""
    if v is None:
        return ''
    if isinstance(v, Decimal):
        # Excel can store Decimal as text only; convert to float for math
        try:
            return float(v)
        except Exception:        # noqa: BLE001
            return str(v)
    return v


def _is_numeric(v: Any) -> bool:
    return isinstance(v, (int, float, Decimal))


def _autosize(ws):
    for col_idx in range(1, ws.max_column + 1):
        max_len = 0
        col_letter = get_column_letter(col_idx)
        for cell in ws[col_letter]:
            try:
                length = len(str(cell.value)) if cell.value is not None else 0
            except Exception:    # noqa: BLE001
                length = 0
            if length > max_len:
                max_len = length
        ws.column_dimensions[col_letter].width = min(max(max_len + 2, 10), 60)


def _write_sheet(wb: Workbook, sheet: Sheet, first: bool):
    if first:
        ws = wb.active
        ws.title = sheet.title[:31]
    else:
        ws = wb.create_sheet(title=sheet.title[:31])

    row_cursor = 1
    # Meta lines above header
    for m in sheet.meta:
        ws.cell(row=row_cursor, column=1, value=m).font = Font(italic=True, color=_GREY)
        ws.merge_cells(start_row=row_cursor, start_column=1,
                       end_row=row_cursor, end_column=max(1, len(sheet.headers)))
        row_cursor += 1
    if sheet.meta:
        row_cursor += 1   # blank spacer

    # Header row
    header_row_idx = row_cursor
    for c, h in enumerate(sheet.headers, start=1):
        cell = ws.cell(row=row_cursor, column=c, value=h)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(horizontal='left', vertical='center')
        cell.border = _BORDER_THIN
    ws.row_dimensions[row_cursor].height = 22
    row_cursor += 1

    # Data rows
    for r_idx, row in enumerate(sheet.rows):
        for c, v in enumerate(row, start=1):
            cell = ws.cell(row=row_cursor, column=c, value=_coerce(v))
            cell.border = _BORDER_THIN
            if (c - 1) in sheet.date_cols:
                # Real Excel date value → sortable / filterable / pivotable.
                cell.number_format = 'yyyy-mm-dd'
                cell.alignment = Alignment(horizontal='left', vertical='center')
            elif (c - 1) in sheet.int_cols:
                cell.number_format = '#,##0'
                cell.alignment = Alignment(horizontal='right')
            elif (c - 1) in sheet.numeric_cols or _is_numeric(v):
                cell.number_format = '#,##0.00;[Red](#,##0.00)'
                cell.alignment = Alignment(horizontal='right')
            else:
                cell.alignment = Alignment(horizontal='left', vertical='center', wrap_text=False)
            if r_idx in sheet.bold_row_indices:
                cell.font = _SUB_FONT
            if sheet.total_row_index is not None and r_idx == sheet.total_row_index:
                cell.fill = _TOTAL_FILL
                cell.font = _TOTAL_FONT
        row_cursor += 1

    ws.freeze_panes = ws.cell(row=header_row_idx + 1, column=1)
    _autosize(ws)


# ---------------------------------------------------------------------------
# Per-report renderers
# ---------------------------------------------------------------------------

def _render_trial_balance(rep: dict) -> tuple[str, list[Sheet]]:
    lines = rep.get('lines') or rep.get('rows') or []
    headers = ['Code', 'Name', 'Account Type', 'Debit', 'Credit']
    rows = []
    for ln in lines:
        rows.append([
            ln.get('account_code') or ln.get('code'),
            ln.get('account_name') or ln.get('name'),
            ln.get('account_type', ''),
            float(ln.get('debit_balance', ln.get('debit', 0)) or 0),
            float(ln.get('credit_balance', ln.get('credit', 0)) or 0),
        ])
    totals = rep.get('totals') or {}
    if totals:
        rows.append([
            '', 'TOTAL', '',
            float(totals.get('total_debits', 0) or 0),
            float(totals.get('total_credits', 0) or 0),
        ])
    return (
        f"trial_balance_{rep.get('as_of','')}.xlsx",
        [Sheet(
            title='Trial Balance',
            headers=headers, rows=rows,
            meta=[f"As of {rep.get('as_of','')}",
                  f"Currency: {rep.get('currency_code','BWP')}"],
            numeric_cols=[3, 4],
            total_row_index=(len(rows) - 1) if totals else None,
        )],
    )


def _render_profit_loss(rep: dict) -> tuple[str, list[Sheet]]:
    sections = rep.get('sections') or rep.get('rows') or []
    headers = ['Section', 'Account', 'Amount']
    rows: list[list[Any]] = []
    bold_idx: list[int] = []
    for sec in sections:
        if isinstance(sec, dict) and sec.get('lines'):
            for ln in sec['lines']:
                rows.append([sec.get('label', ''), ln.get('label', ''),
                             float(ln.get('amount', 0) or 0)])
            rows.append([sec.get('subtotal_label', sec.get('label','')+' Total'),
                         '', float(sec.get('subtotal', 0) or 0)])
            bold_idx.append(len(rows) - 1)
        elif isinstance(sec, dict):
            rows.append([sec.get('label',''), '', float(sec.get('amount', 0) or 0)])
    totals = rep.get('totals') or {}
    if 'net_profit' in totals or 'pat' in totals:
        rows.append(['Net Profit (PAT)', '', float(totals.get('net_profit', totals.get('pat', 0)) or 0)])
        bold_idx.append(len(rows) - 1)
    return (
        f"profit_loss_{rep.get('from_date','')}_{rep.get('to_date','')}.xlsx",
        [Sheet(
            title='Profit & Loss',
            headers=headers, rows=rows,
            meta=[f"Period {rep.get('from_date','')} to {rep.get('to_date','')}",
                  f"Currency: {rep.get('currency_code','BWP')}"],
            numeric_cols=[2],
            bold_row_indices=bold_idx,
        )],
    )


def _render_balance_sheet(rep: dict) -> tuple[str, list[Sheet]]:
    sections = rep.get('sections') or []
    headers = ['Section', 'Side', 'Line', 'Amount']
    rows: list[list[Any]] = []
    bold_idx: list[int] = []
    for sec in sections:
        for ln in sec.get('lines', []):
            rows.append([sec.get('label',''), sec.get('side',''),
                         ln.get('label',''), float(ln.get('amount', 0) or 0)])
        rows.append([sec.get('subtotal_label','Subtotal'), sec.get('side',''),
                     '', float(sec.get('subtotal', 0) or 0)])
        bold_idx.append(len(rows) - 1)
    totals = rep.get('totals') or {}
    rows.append(['TOTAL ASSETS', 'asset', '', float(totals.get('total_assets', 0) or 0)])
    rows.append(['TOTAL LIABILITIES', 'liability', '', float(totals.get('total_liabilities', 0) or 0)])
    rows.append(['TOTAL EQUITY', 'equity', '', float(totals.get('total_equity', 0) or 0)])
    rows.append(['LIABILITIES + EQUITY', '', '', float(totals.get('liabilities_and_equity', 0) or 0)])
    rows.append(['BALANCED', '', '', 'YES' if totals.get('balanced') else 'NO'])
    return (
        f"balance_sheet_{rep.get('as_of','')}.xlsx",
        [Sheet(
            title='Balance Sheet',
            headers=headers, rows=rows,
            meta=[f"As of {rep.get('as_of','')}",
                  f"Currency: {rep.get('currency_code','BWP')}"],
            numeric_cols=[3],
            bold_row_indices=bold_idx,
            total_row_index=len(rows) - 2,
        )],
    )


def _render_general_ledger(rep: dict) -> tuple[str, list[Sheet]]:
    acc = rep.get('account') or {}
    lines = rep.get('lines') or []
    headers = ['Date', 'Entry #', 'Description', 'Journal Type',
               'Debit', 'Credit', 'Running Balance', 'Counterparty']
    rows: list[list[Any]] = []
    for ln in lines:
        rows.append([
            ln.get('date',''), ln.get('entry_number',''),
            ln.get('description',''), ln.get('journal_type',''),
            float(ln.get('debit', 0) or 0),
            float(ln.get('credit', 0) or 0),
            float(ln.get('running_balance', 0) or 0),
            ln.get('contact_name','') or '',
        ])
    totals = rep.get('totals') or {}
    rows.append([
        '', '', 'TOTALS', '',
        float(totals.get('total_debits', 0) or 0),
        float(totals.get('total_credits', 0) or 0),
        float(totals.get('closing_balance', 0) or 0),
        '',
    ])
    meta = [
        f"Account {acc.get('code','')} — {acc.get('name','')}",
        f"Period {rep.get('from_date','')} to {rep.get('to_date','')}",
        f"Currency: {rep.get('currency_code','BWP')}",
        f"Opening balance: {rep.get('opening_balance', 0)}",
    ]
    return (
        f"general_ledger_{acc.get('code','')}_{rep.get('from_date','')}_{rep.get('to_date','')}.xlsx",
        [Sheet(
            title=f"GL {acc.get('code','')}"[:31],
            headers=headers, rows=rows, meta=meta,
            numeric_cols=[4, 5, 6],
            total_row_index=len(rows) - 1,
        )],
    )


def _render_ar_aging(rep: dict) -> tuple[str, list[Sheet]]:
    rows_in = rep.get('rows') or rep.get('lines') or []
    bucket_keys = ['current', '1-30', '31-60', '61-90', '90_plus', 'total']
    headers = ['Contact', 'Current', '1-30', '31-60', '61-90', '90+', 'Total']
    out = []
    for r in rows_in:
        out.append([
            r.get('contact_name') or r.get('contact','') or r.get('name',''),
            *[float(r.get(k, 0) or 0) for k in bucket_keys],
        ])
    totals = rep.get('totals') or {}
    out.append(['TOTAL', *[float(totals.get(k, 0) or 0) for k in bucket_keys]])
    return (
        f"ar_aging_{rep.get('as_of','')}.xlsx",
        [Sheet(
            title='AR Aging',
            headers=headers, rows=out,
            meta=[f"As of {rep.get('as_of','')}",
                  f"Currency: {rep.get('currency_code','BWP')}"],
            numeric_cols=[1, 2, 3, 4, 5, 6],
            total_row_index=len(out) - 1,
        )],
    )


def _render_ap_aging(rep: dict) -> tuple[str, list[Sheet]]:
    name, sheets = _render_ar_aging(rep)
    sheets[0].title = 'AP Aging'
    return name.replace('ar_aging', 'ap_aging'), sheets


def _render_cash_position(rep: dict) -> tuple[str, list[Sheet]]:
    accts = rep.get('accounts') or rep.get('rows') or []
    headers = ['Account', 'Code', 'Currency', 'Balance (BWP)', 'As of']
    rows: list[list[Any]] = []
    for a in accts:
        rows.append([
            a.get('name','') or a.get('account_name',''),
            a.get('code','') or a.get('account_code',''),
            a.get('currency','BWP'),
            float(a.get('balance_bwp', a.get('balance', 0)) or 0),
            rep.get('as_of',''),
        ])
    total = rep.get('total_balance') or rep.get('total') or sum(float(a.get('balance_bwp', a.get('balance', 0)) or 0) for a in accts)
    rows.append(['TOTAL', '', '', float(total or 0), ''])
    return (
        f"cash_position_{rep.get('as_of','')}.xlsx",
        [Sheet(
            title='Cash Position',
            headers=headers, rows=rows,
            meta=[f"As of {rep.get('as_of','')}",
                  f"Currency: {rep.get('currency_code','BWP')}"],
            numeric_cols=[3],
            total_row_index=len(rows) - 1,
        )],
    )


def _render_generic(rep: dict, key: str) -> tuple[str, list[Sheet]]:
    """Fallback — flatten the top-level dict keys with list values into sheets."""
    sheets: list[Sheet] = []
    for k, v in rep.items():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            keys = list(v[0].keys())
            sheets.append(Sheet(
                title=k[:31],
                headers=[h.replace('_', ' ').title() for h in keys],
                rows=[[row.get(h) for h in keys] for row in v],
            ))
    if not sheets:
        # Single key-value sheet
        sheets = [Sheet(
            title='Report',
            headers=['Key', 'Value'],
            rows=[[k, str(v)] for k, v in rep.items()],
        )]
    return (f'{key}.xlsx', sheets)


def _render_cash_flow(rep: dict) -> tuple[str, list[Sheet]]:
    headers = ['Section', 'Line', 'Amount']
    rows: list[list[Any]] = []
    bold_idx: list[int] = []
    for sec_key in ('operating', 'investing', 'financing'):
        sec = rep.get(sec_key) or {}
        if not sec:
            continue
        for ln in sec.get('lines', []):
            rows.append([sec_key.title(), ln.get('label',''), float(ln.get('amount', 0) or 0)])
        rows.append([sec_key.title() + ' Subtotal', '', float(sec.get('subtotal', 0) or 0)])
        bold_idx.append(len(rows) - 1)
    rows.append(['NET CHANGE IN CASH', '', float(rep.get('net_change_in_cash', 0) or 0)])
    rows.append(['OPENING CASH', '', float(rep.get('opening_cash', 0) or 0)])
    rows.append(['CLOSING CASH', '', float(rep.get('closing_cash', 0) or 0)])
    bold_idx.extend([len(rows) - 3, len(rows) - 2, len(rows) - 1])
    return (
        f"cash_flow_{rep.get('from_date','')}_{rep.get('to_date','')}.xlsx",
        [Sheet(
            title='Cash Flow',
            headers=headers, rows=rows,
            meta=[f"Period {rep.get('from_date','')} to {rep.get('to_date','')}",
                  f"Currency: {rep.get('currency_code','BWP')}"],
            numeric_cols=[2],
            bold_row_indices=bold_idx,
        )],
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def _parse_company(params: dict) -> Any:
    """Match _parse_company in views — accepts UUID or 'all'."""
    v = params.get('company') or params.get('company_id')
    if not v or v == 'all':
        return None
    return v


def _date(params: dict, key: str, default: Any = None):
    v = params.get(key)
    if not v:
        return default
    try:
        return date.fromisoformat(v)
    except Exception:    # noqa: BLE001
        return default


def _kwargs_trial_balance(params):
    return dict(as_of=_date(params, 'as_of', timezone.localdate()),
                company_id=_parse_company(params))


def _kwargs_profit_loss(params):
    return dict(from_date=_date(params, 'from', _date(params, 'from_date')),
                to_date=_date(params, 'to', _date(params, 'to_date', timezone.localdate())),
                company_id=_parse_company(params))


def _kwargs_balance_sheet(params):
    return dict(as_of=_date(params, 'as_of', timezone.localdate()),
                company_id=_parse_company(params))


def _kwargs_general_ledger(params):
    return dict(
        account_code=params.get('account') or params.get('account_code'),
        from_date=_date(params, 'from', _date(params, 'from_date')),
        to_date=_date(params, 'to', _date(params, 'to_date', timezone.localdate())),
        company_id=_parse_company(params),
    )


def _kwargs_aging(params):
    return dict(as_of=_date(params, 'as_of', timezone.localdate()),
                company_id=_parse_company(params))


def _kwargs_cash_position(params):
    return dict(company_id=_parse_company(params),
                as_of=_date(params, 'as_of', timezone.localdate()))


def _kwargs_cash_flow(params):
    return dict(from_date=_date(params, 'from', _date(params, 'from_date')),
                to_date=_date(params, 'to', _date(params, 'to_date', timezone.localdate())),
                company_id=_parse_company(params))


@dataclass
class ReportEntry:
    builder: Callable
    kwargs_extractor: Callable[[dict], dict]
    renderer: Callable[[dict], tuple[str, list[Sheet]]]


def _registry():
    from reporting.reports import (
        build_trial_balance, build_profit_loss, build_balance_sheet,
        build_ma_balance_sheet, build_general_ledger,
        build_ar_aging, build_ap_aging, build_cash_position,
        build_cash_flow,
    )
    return {
        'trial_balance':    ReportEntry(build_trial_balance,    _kwargs_trial_balance,  _render_trial_balance),
        'profit_loss':      ReportEntry(build_profit_loss,      _kwargs_profit_loss,    _render_profit_loss),
        'balance_sheet':    ReportEntry(build_balance_sheet,    _kwargs_balance_sheet,  _render_balance_sheet),
        'ma_balance_sheet': ReportEntry(build_ma_balance_sheet, _kwargs_balance_sheet,  _render_balance_sheet),
        'general_ledger':   ReportEntry(build_general_ledger,   _kwargs_general_ledger, _render_general_ledger),
        'ar_aging':         ReportEntry(build_ar_aging,         _kwargs_aging,          _render_ar_aging),
        'ap_aging':         ReportEntry(build_ap_aging,         _kwargs_aging,          _render_ap_aging),
        'cash_position':    ReportEntry(build_cash_position,    _kwargs_cash_position,  _render_cash_position),
        'cash_flow':        ReportEntry(build_cash_flow,        _kwargs_cash_flow,      _render_cash_flow),
    }


SUPPORTED_REPORTS = (
    'trial_balance', 'profit_loss', 'balance_sheet', 'ma_balance_sheet',
    'general_ledger', 'ar_aging', 'ap_aging', 'cash_position', 'cash_flow',
)


# ---------------------------------------------------------------------------
# Top-level dispatcher
# ---------------------------------------------------------------------------

def build_sheets_xlsx_response(filename: str, sheets: list[Sheet]) -> HttpResponse:
    """Write a ready-made list of Sheets to an .xlsx HTTP response.

    The same styled writer the report registry uses, exposed for callers that
    build their own Sheets (e.g. the payment register export) instead of going
    through a report builder. Keeps one workbook-styling path across the app.
    """
    wb = Workbook()
    for i, sheet in enumerate(sheets):
        _write_sheet(wb, sheet, first=(i == 0))
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    resp = HttpResponse(
        buf.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'
    return resp


def build_report_xlsx_response(report_key: str, params: dict) -> HttpResponse:
    """
    Build an Excel-format HTTP response for a supported report.

    Returns 400 if the key is unknown, or 404 if the underlying builder
    short-circuits (e.g. GL with an unknown account).
    """
    reg = _registry()
    entry = reg.get(report_key)
    if entry is None:
        return HttpResponse(
            f'{{"error":"Unknown report {report_key!r}. Supported: {sorted(reg)}"}}',
            content_type='application/json',
            status=400,
        )
    kwargs = entry.kwargs_extractor(params)
    try:
        result = entry.builder(**kwargs)
    except Exception as e:        # noqa: BLE001
        return HttpResponse(
            f'{{"error":"{type(e).__name__}: {str(e)[:200]}"}}',
            content_type='application/json',
            status=500,
        )
    if isinstance(result, dict) and 'error' in result:
        return HttpResponse(
            f'{{"error":"{result["error"]}"}}',
            content_type='application/json',
            status=404,
        )
    filename, sheets = entry.renderer(result)
    wb = Workbook()
    for i, sheet in enumerate(sheets):
        _write_sheet(wb, sheet, first=(i == 0))
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    resp = HttpResponse(
        buf.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'
    return resp
