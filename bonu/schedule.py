"""bonu/schedule.py — read the BONU performance workbook into Omni verbatim.

The whole point: the schedule stops living in an emailed Excel file and lives in
Omni, editable, with NO column or row dropped. Each sheet becomes a
`BonuScheduleSheet` (its ordered column labels) and each data row a
`BonuScheduleRow` whose `cells` is keyed by those labels.

Runs server-side; the workbook holds member names, so nothing here is sent to an
external model — it only reads the file and writes rows.
"""
from __future__ import annotations

import datetime as _dt
import os
from decimal import Decimal, InvalidOperation

from django.db import transaction

from .models import BonuScheduleRow, BonuScheduleSheet

# Nice key/title + which column carries the money, per known sheet. A sheet not
# listed still imports (header auto-detected) — this only adds the reconciliation
# amount column and a stable key.
SHEET_SPECS = {
    'SUMMARY':             ('summary',       ''),
    'CLAIMS':              ('claims',        'Inv Amount (P)'),
    'ADMIN EXPENSES':      ('admin-expenses', 'Amount (BWP)'),
    'UNPAID FEES':         ('unpaid-fees',   'Totals'),
    'PREMIUMS':            ('premiums',      'Total Premium Amount'),
    'OWED PER FEE NOTE':   ('owed-per-fee-note', 'Inv Amount (P)'),
    'PREMIUM JULY2026-27': ('premium-2026-27', 'Total Premium Amount'),
}


def _slug(title: str) -> str:
    import re
    s = re.sub(r'[^a-z0-9]+', '-', (title or '').strip().lower()).strip('-')
    return s[:40] or 'sheet'


def norm_value(v) -> str:
    """A cell as a string that round-trips a hand-edit. Dates → ISO, whole
    floats → no trailing .0, everything else → str(). Blank → ''."""
    if v is None:
        return ''
    if isinstance(v, _dt.datetime):
        return v.date().isoformat() if (v.hour == v.minute == v.second == 0) else v.isoformat(sep=' ')
    if isinstance(v, _dt.date):
        return v.isoformat()
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else repr(v)
    if isinstance(v, (int, Decimal)):
        return str(v)
    return str(v).strip()


def _blank_first(row):
    return not row or row[0] in (None, '')


def _header_row(rows, scan=8):
    """Index of the row that looks most like a header — the row in the first
    `scan` with the most non-empty cells (ties → earliest).

    One exception, because a matrix sheet otherwise loses to its own first data
    row. A period header ('', Jan-25, Feb-25, …) has a BLANK corner cell, so it
    carries exactly one cell fewer than the row beneath it ('Total Revenue', 100,
    200) and gets read as data — which costs the month labels AND swallows the
    Total Revenue line. When the row directly above the best candidate is within
    one cell of it, starts blank, and the candidate does not, that row is the
    header."""
    best_i, best_n = 0, -1
    counts = []
    for i, r in enumerate(rows[:scan]):
        n = sum(1 for c in r if c not in (None, ''))
        counts.append(n)
        if n > best_n:
            best_i, best_n = i, n
    above = best_i - 1
    if (above >= 0 and counts[above] >= best_n - 1
            and _blank_first(rows[above]) and not _blank_first(rows[best_i])):
        return above
    return best_i


def _columns(header_row):
    """Ordered, de-duplicated, never-blank column labels for the header."""
    out, seen = [], {}
    for i, c in enumerate(header_row):
        label = norm_value(c) or f'Column {i + 1}'
        if label in seen:
            seen[label] += 1
            label = f'{label} ({seen[label]})'
        else:
            seen[label] = 1
        out.append(label)
    return out


def parse_workbook(path):
    """[{key,title,columns,amount_column,rows:[{position,cells}]}] for every sheet."""
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    sheets = []
    try:
        for order, ws in enumerate(wb.worksheets):
            rows = [list(r) for r in ws.iter_rows(values_only=True)]
            if not rows:
                continue
            hi = _header_row(rows)
            columns = _columns(rows[hi])
            spec_key, amount_col = SHEET_SPECS.get(ws.title.strip().upper(),
                                                   (_slug(ws.title), ''))
            data, pos = [], 0
            for r in rows[hi + 1:]:
                cells = {}
                any_val = False
                for ci, col in enumerate(columns):
                    val = norm_value(r[ci]) if ci < len(r) else ''
                    cells[col] = val
                    if val != '':
                        any_val = True
                if not any_val:
                    continue
                data.append({'position': pos, 'cells': cells})
                pos += 1
            sheets.append({
                'key': spec_key, 'title': ws.title.strip(), 'columns': columns,
                'amount_column': amount_col if amount_col in columns else '',
                'rows': data,
            })
    finally:
        wb.close()
    return sheets


@transaction.atomic
def import_workbook(path, *, source_note='', commit=True, user=None, wipe=False):
    """Upsert every sheet and REPLACE its rows (a re-import is the latest truth).
    With wipe=True, DELETE all existing schedule sheets first, so the load is a
    clean replacement (removes any sheet no longer in the file). Empty sheets
    (0 data rows, e.g. a stray 'Sheet1') are skipped — never create a blank tab.
    Returns a per-sheet summary — never a partial silent load."""
    parsed = [s for s in parse_workbook(path) if s['rows']]      # drop empty sheets
    if wipe and not parsed:
        # Fail-safe, never fail-open: a file that parses to zero data rows (wrong
        # file / all-blank tabs) must NOT wipe the live schedule and load nothing.
        raise ValueError('wipe refused: workbook has zero non-empty sheets — '
                         'nothing would replace the wiped data')
    if commit and wipe:
        BonuScheduleSheet.objects.all().delete()                 # cascade removes rows
    summary = []
    for order, s in enumerate(parsed):
        entry = {'key': s['key'], 'title': s['title'], 'columns': len(s['columns']),
                 'rows': len(s['rows']), 'amount_column': s['amount_column']}
        if commit:
            sheet, _ = BonuScheduleSheet.objects.update_or_create(
                key=s['key'],
                defaults={'title': s['title'], 'columns': s['columns'],
                          'amount_column': s['amount_column'],
                          'source_note': source_note, 'order': order})
            sheet.rows.all().delete()
            BonuScheduleRow.objects.bulk_create([
                BonuScheduleRow(sheet=sheet, position=r['position'], cells=r['cells'])
                for r in s['rows']])
        summary.append(entry)
    return summary


def _money(s) -> Decimal:
    if s in (None, ''):
        return Decimal('0')
    t = str(s).replace(',', '').replace('P', '').replace(' ', '').replace('\xa0', '')
    neg = t.startswith('(') and t.endswith(')')
    if neg:
        t = t[1:-1]
    try:
        d = Decimal(t or '0')
        return -d if neg else d
    except InvalidOperation:
        return Decimal('0')


def is_total_row(cells, columns=None) -> bool:
    """True when the row is the workbook's OWN total line rather than a fee note.

    Keyed on the label a person actually types in the row's first filled cell
    ('Totals'), never on position — the real file has a blank row after it, and
    'Totals' also appears in free-text notes further along the row."""
    for col in (columns or list(cells.keys())):
        s = str(cells.get(col) or '').strip().lower()
        if s == '':
            continue
        return s in ('total', 'totals', 'grand total')
    return False


def sheet_total(sheet: BonuScheduleSheet) -> Decimal:
    """Sum of the sheet's amount column across its DATA rows (0 if it has none).

    The workbook's own 'Totals' line is a row like any other in the file, so it
    MUST be excluded here — counted as data it makes every figure read exactly
    double (claims showed P17.03m against a real P8.51m)."""
    if not sheet.amount_column:
        return Decimal('0.00')
    total = sum((_money(r.cells.get(sheet.amount_column))
                 for r in sheet.rows.all()
                 if not is_total_row(r.cells, sheet.columns)), Decimal('0'))
    return total.quantize(Decimal('0.01'))


def stated_total(sheet: BonuScheduleSheet):
    """What the workbook's own 'Totals' line says, or None if the sheet has none —
    so a screen can show whether our detail foots to the author's own figure
    instead of quietly presenting one of them as the truth."""
    if not sheet.amount_column:
        return None
    for r in sheet.rows.all():
        if is_total_row(r.cells, sheet.columns):
            return _money(r.cells.get(sheet.amount_column)).quantize(Decimal('0.01'))
    return None


def reconcile() -> dict:
    """Per-sheet totals, each beside the workbook's own stated total.

    When Omni ALSO holds ledger-fed BONU invoices, the claims comparison is added
    on top. When it does not, that key is omitted entirely and the screen drops
    the strip — CFO 13-Aug-2026: the union's own schedule is not comparable to the
    ledger, and subtracting one from the other invents a gap. A zero ledger is
    'nothing to compare with', never 'everything is missing'."""
    from .models import BonuInvoiceLine
    out = {'sheets': []}
    for sheet in BonuScheduleSheet.objects.all():
        detail, stated = sheet_total(sheet), stated_total(sheet)
        out['sheets'].append({'key': sheet.key, 'title': sheet.title,
                              'rows': sheet.rows.count(),
                              'amount_column': sheet.amount_column,
                              'total': str(detail),
                              'stated_total': None if stated is None else str(stated),
                              'foots': None if stated is None else (detail == stated)})
    if not BonuInvoiceLine.objects.exists():
        return out                                   # nothing to compare with
    omni_claims = sum((l.amount for l in BonuInvoiceLine.objects.all()), Decimal('0'))
    claims_sheet = BonuScheduleSheet.objects.filter(key='claims').first()
    schedule_claims = sheet_total(claims_sheet) if claims_sheet else Decimal('0')
    out['claims_reconciliation'] = {
        'schedule_total': str(schedule_claims),
        'omni_ledger_total': str(omni_claims),
        'difference': str(schedule_claims - omni_claims),
    }
    return out


def diff_workbook(path):
    """Compare an uploaded workbook to what is already stored, per sheet, WITHOUT
    writing — so staff see what next month's file changes before committing.

    Rows have no stable key, so the diff is set-based on the row's values: 'added'
    = rows in the file not already stored, 'removed' = stored rows not in the file.
    A changed amount shows as one removed + one added, which is the honest signal
    ('12 gone, 14 new') for a review-before-apply step."""
    parsed = parse_workbook(path)
    out = []
    for s in parsed:
        existing = BonuScheduleSheet.objects.filter(key=s['key']).first()
        cur = set()
        if existing:
            for r in existing.rows.all():
                cur.add(tuple(sorted((k, str(v)) for k, v in r.cells.items())))
        new = [tuple(sorted((k, str(v)) for k, v in r['cells'].items())) for r in s['rows']]
        new_set = set(new)
        added = [dict(t) for t in new_set - cur]
        removed = [dict(t) for t in cur - new_set]
        out.append({'key': s['key'], 'title': s['title'],
                    'current_rows': len(cur), 'file_rows': len(new),
                    'added': len(added), 'removed': len(removed),
                    'unchanged': len(new_set & cur),
                    'added_sample': added[:8], 'removed_sample': removed[:8]})
    return out
