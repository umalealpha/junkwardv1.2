"""hris/incentive_import.py — read an uploaded incentive-list spreadsheet into
line dicts for the /hris/incentives request form (CFO / Bharath 2026-08-24).

PARSE ONLY. This never creates a request and never bypasses the earned-incentive
gate. The rows it returns drop into the SAME form a manager fills by hand, and
Submit still runs the identical server-side gate (incentive_service.submit_request
→ _validate_and_parse_line: beyond-duties / on-time / error-free / no-manager-fix
and the ≥50-word justification). So an upload is a typing shortcut for a long
list — not a second, weaker way in.

Reuses the house spreadsheet reader (commissions.importer._iter_sheets: .xlsx/
.xlsm via openpyxl, .xlsb via pyxlsb, .xls/.ods via python-calamine) plus a small
.csv path, so we do not hand-roll another parser. Runs server-side; the file is
deleted by the caller — the data never leaves the box.
"""
from __future__ import annotations

import os

from commissions.importer import _TOTALS_RE, _dec, _iter_sheets, _norm

# field -> predicate on the normalised header cell. Order = match priority
# (mirrors commissions.importer.map_headers). Justification is tested BEFORE
# basis so a plain "Reason" column becomes the substantive justification, not
# the short basis label.
_FIELD_TESTS = [
    ('name',          lambda h: h in {'name', 'employee', 'employee name',
                                      'full name', 'person', 'staff',
                                      'staff member', 'recipient'}
                                or h.startswith('name')
                                or 'employee name' in h),
    ('amount',        lambda h: 'amount' in h
                                or h in {'incentive', 'value', 'bwp', 'pula'}),
    ('justification', lambda h: 'justification' in h or 'motivation' in h
                                or h.startswith('why') or 'explanation' in h
                                or 'reason for' in h or h == 'reason'),
    ('basis',         lambda h: 'basis' in h or 'category' in h or h == 'type'),
    ('beyond_normal_duties', lambda h: 'beyond' in h or 'above' in h
                                or 'over and above' in h or 'extra dut' in h),
    ('on_time',       lambda h: 'on time' in h or 'on-time' in h
                                or 'ontime' in h),
    ('error_free',    lambda h: 'error' in h),   # error free / error-free / no errors
    ('needed_manager_fix', lambda h: 'fix' in h or 'rework' in h),
]


def _map_headers(header_row) -> dict:
    """{field: column_index} — first unused column matching each field."""
    norm = [_norm(c) for c in header_row]
    used, mapping = set(), {}
    for field, test in _FIELD_TESTS:
        for i, h in enumerate(norm):
            if i in used or not h:
                continue
            if test(h):
                mapping[field] = i
                used.add(i)
                break
    return mapping


def _find_header(rows, scan=30):
    """Row index + column map for a sheet that has at least a Name and an Amount
    column, or None. Requiring BOTH avoids matching a stray label row."""
    for i, row in enumerate(rows[:scan]):
        m = _map_headers(row)
        if 'name' in m and 'amount' in m:
            return i, m
    return None


def _truthy(v) -> bool:
    # Matches incentive_service._truthy, plus a bare "y".
    return v is True or _norm(v) in ('1', 'true', 'yes', 'y', 'on')


def _iter_incentive_sheets(path):
    """Like commissions.importer._iter_sheets but also reads .csv."""
    if os.path.splitext(path)[1].lower() == '.csv':
        import csv
        with open(path, newline='', encoding='utf-8-sig') as fh:
            yield 'csv', [list(r) for r in csv.reader(fh)], False
        return
    yield from _iter_sheets(path)


def parse_incentive_list(path) -> list[dict]:
    """Return one dict per person in the first sheet that carries a Name+Amount
    header. Shape matches the /hris/incentives form line (so it drops straight
    in): name, amount (str, '' when not a positive number so the form flags it),
    basis, justification, and the four qualification booleans (only true when the
    sheet actually says so — otherwise the manager still ticks them by hand and
    the gate holds).

    Raises ValueError with a plain-English message when no usable table is found.
    """
    for _sheet, rows, _xlsb in _iter_incentive_sheets(path):
        hdr = _find_header(rows)
        if hdr is None:
            continue
        hi, m = hdr

        def cell(row, field):
            idx = m.get(field)
            return row[idx] if idx is not None and idx < len(row) else None

        out: list[dict] = []
        for row in rows[hi + 1:]:
            name = str(cell(row, 'name') or '').strip()
            # Skip blank rows and a "Total"/"Grand Total" label in the name column.
            if not name or _TOTALS_RE.match(name):
                continue
            amt = _dec(cell(row, 'amount'))
            out.append({
                'name': name[:160],
                'amount': (f"{amt:.2f}" if amt > 0 else ''),
                'basis': str(cell(row, 'basis') or '').strip()[:200],
                'justification': str(cell(row, 'justification') or '').strip(),
                'beyond_normal_duties': _truthy(cell(row, 'beyond_normal_duties')),
                'on_time': _truthy(cell(row, 'on_time')),
                'error_free': _truthy(cell(row, 'error_free')),
                'needed_manager_fix': _truthy(cell(row, 'needed_manager_fix')),
            })
        if out:
            return out

    raise ValueError(
        "No incentive list found in that file. It needs a header row with at "
        "least a “Name” column and an “Amount” column. Download the template, "
        "fill it in, and upload it again.")
