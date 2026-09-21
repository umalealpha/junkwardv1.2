"""
banking/statement_files.py — turn an uploaded bank-statement file into CSV text,
and work out how to read its columns without needing a pre-configured format.

Why this exists (bug 713d6218, 2026-08-03)
------------------------------------------
Manual statement upload had never worked once in production. Two reasons:

1. `import_statement` defaulted to a `BankStatementFormat` named
   'FNB BWP Current Account'. There were **zero** BankStatementFormat rows on
   prod, so every upload died with "Statement format ... not found" — whatever
   file the user picked. All 238 statements in the system arrived through the
   FNB API sync, never through the upload button.
2. The file picker offered .csv/.ofx/.qfx/.xlsx but the importer only ever
   decoded text and parsed CSV. An .xlsx is a zip — decoding it raises before
   any parsing happens.

So instead of asking Finance to hand-configure a format per bank before they can
upload anything, we read the file's own header row and map it. A saved
BankStatementFormat still wins when one is named; this is the fallback.
"""
from __future__ import annotations

import csv
import io
import re
from typing import Dict, List, Optional, Tuple

# Header synonyms, lowercase. Longest-specific first within each group so that
# e.g. "transaction date" is preferred over a bare "date" when both exist.
_DATE_COLS = [
    'transaction date', 'trans date', 'value date', 'posting date',
    'date posted', 'effective date', 'date',
]
_DESC_COLS = [
    'description', 'narrative', 'transaction description', 'details',
    'detail', 'particulars', 'reference description', 'transaction',
]
_REF_COLS = [
    'reference', 'ref', 'transaction reference', 'cheque number',
    'cheque no', 'reference number',
]
_AMOUNT_COLS = ['amount', 'transaction amount', 'value']
_DEBIT_COLS = ['debit', 'debit amount', 'debits', 'withdrawal', 'money out', 'paid out']
_CREDIT_COLS = ['credit', 'credit amount', 'credits', 'deposit', 'money in', 'paid in']
_BALANCE_COLS = ['balance', 'running balance', 'closing balance', 'account balance']

_DATE_FORMATS = [
    '%d/%m/%Y', '%d-%m-%Y', '%Y-%m-%d', '%d/%m/%y', '%d-%b-%Y', '%d %b %Y',
    '%m/%d/%Y', '%Y/%m/%d',
    # Excel stores a date cell as a datetime; openpyxl hands it back as
    # datetime(2026,7,2,0,0) which str()s to "2026-07-02 00:00:00". A CSV
    # exported from Excel carries the same midnight suffix. Accept it so a
    # statement whose Date column is a real Excel date is not rejected
    # (bug 20b32822 round 2, 11 Aug 2026).
    '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%d/%m/%Y %H:%M:%S', '%d/%m/%Y %H:%M',
]


class StatementFileError(ValueError):
    """Raised with a message meant for the person doing the upload."""


# ---------------------------------------------------------------------------
# File → CSV text
# ---------------------------------------------------------------------------
def to_csv_text(uploaded_file, encoding: str = 'utf-8') -> str:
    """Return CSV text for an uploaded .csv/.txt or .xlsx/.xlsm file.

    Excel is converted sheet-1 → CSV so the existing CSV importer stays the one
    parser. `.xls` (the pre-2007 binary format) is not supported — we have no
    reader for it — and says so plainly instead of failing later.
    """
    name = (getattr(uploaded_file, 'name', '') or '').lower()
    raw = uploaded_file.read()

    if name.endswith('.xls'):
        raise StatementFileError(
            'Old Excel .xls files cannot be read. Open it in Excel and use '
            '"Save As" to save it as .xlsx or .csv, then upload that.'
        )
    if name.endswith('.ofx') or name.endswith('.qfx'):
        raise StatementFileError(
            'OFX/QFX bank files are not supported yet. Please upload the CSV or '
            'Excel version of the statement.'
        )

    if name.endswith(('.xlsx', '.xlsm')):
        return _xlsx_to_csv_text(raw)

    for enc in (encoding, 'utf-8', 'utf-8-sig', 'latin-1'):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    raise StatementFileError(
        'This file is not readable as text. Please upload it as CSV or .xlsx.'
    )


def _xlsx_to_csv_text(raw: bytes) -> str:
    try:
        import openpyxl
    except ImportError:                                    # pragma: no cover
        raise StatementFileError(
            'Excel statements cannot be read on this server. Please save the '
            'statement as CSV and upload that instead.'
        )
    try:
        wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True, read_only=True)
    except Exception:                                      # noqa: BLE001
        raise StatementFileError(
            'This Excel file could not be opened. It may be password-protected '
            'or damaged. Try opening it and saving it again as .xlsx or .csv.'
        )
    ws = wb[wb.sheetnames[0]]
    buf = io.StringIO()
    writer = csv.writer(buf)
    for row in ws.iter_rows(values_only=True):
        if row is None:
            continue
        writer.writerow([_cell_to_text(c) for c in row])
    wb.close()
    return buf.getvalue()


def _cell_to_text(c) -> str:
    """One Excel cell → CSV text.

    A date cell comes back from openpyxl as a datetime, and str() on it yields
    "2026-07-02 00:00:00" — a midnight suffix that matched none of our date
    formats, so a perfectly clean Excel statement was rejected (bug 20b32822
    round 2, 11 Aug 2026). Emit a date-valued cell as plain YYYY-MM-DD (dropping a
    midnight time), and a cell with a real time as ISO date-time, so the date
    detector reads it. Everything else is str().strip() exactly as before.
    """
    import datetime as _dt
    if c is None:
        return ''
    if isinstance(c, _dt.datetime):
        return c.strftime('%Y-%m-%d') if (c.hour, c.minute, c.second) == (0, 0, 0) \
            else c.strftime('%Y-%m-%d %H:%M:%S')
    if isinstance(c, _dt.date):
        return c.strftime('%Y-%m-%d')
    return str(c).strip()


# ---------------------------------------------------------------------------
# CSV text → column mapping
# ---------------------------------------------------------------------------
def _pick(headers: List[str], candidates: List[str]) -> Optional[str]:
    """First header that equals, then that contains, one of the candidates."""
    lower = {h.strip().lower(): h for h in headers if h}
    for cand in candidates:
        if cand in lower:
            return lower[cand]
    for cand in candidates:
        for low, original in lower.items():
            if cand in low:
                return original
    return None


def find_header_row(csv_text: str, delimiter: str = ',') -> Tuple[int, List[str]]:
    """Locate the real header row.

    Bank exports often carry a few title/account-summary lines above the table.
    The header is the first row that yields both a date-ish and a
    description-ish column. Returns (rows_to_skip, headers).
    """
    reader = csv.reader(io.StringIO(csv_text), delimiter=delimiter)
    for idx, row in enumerate(reader):
        if idx > 30:
            break
        headers = [(c or '').strip() for c in row]
        if not any(headers):
            continue
        if _pick(headers, _DATE_COLS) and _pick(headers, _DESC_COLS):
            return idx, headers
    raise StatementFileError(
        'Could not find the statement table in this file. It needs a header row '
        'with at least a date column and a description column.'
    )


def detect_date_format(csv_text: str, skip: int, date_header: str,
                       delimiter: str = ',') -> str:
    """Guess the date format from the first few dated rows."""
    from datetime import datetime

    lines = csv_text.splitlines()
    body = '\n'.join(lines[skip:])
    reader = csv.DictReader(io.StringIO(body), delimiter=delimiter)
    samples: List[str] = []
    for row in reader:
        val = (row.get(date_header) or '').strip()
        if val:
            samples.append(val)
        if len(samples) >= 5:
            break
    for fmt in _DATE_FORMATS:
        if samples and all(_parses(s, fmt, datetime) for s in samples):
            return fmt
    return '%d/%m/%Y'


def _parses(value: str, fmt: str, datetime_cls) -> bool:
    try:
        datetime_cls.strptime(value, fmt)
        return True
    except ValueError:
        return False


def build_format_from_file(csv_text: str, *, bank_name: str = '',
                           delimiter: str = ','):
    """Return (unsaved BankStatementFormat, rows_to_skip_before_header).

    The instance is never saved — BankStatementImporter only reads attributes
    off it, so an in-memory format is enough to parse one upload.
    """
    from .models import BankStatementFormat

    skip, headers = find_header_row(csv_text, delimiter)

    date_col = _pick(headers, _DATE_COLS)
    desc_col = _pick(headers, _DESC_COLS)
    amount_col = _pick(headers, _AMOUNT_COLS)
    debit_col = _pick(headers, _DEBIT_COLS)
    credit_col = _pick(headers, _CREDIT_COLS)

    # A single "amount" column and a debit/credit pair are mutually exclusive
    # in the model. Prefer the pair when both are present — it is unambiguous.
    if debit_col and credit_col:
        amount_col = None
    elif not amount_col:
        raise StatementFileError(
            'This file has no amount column. Found: '
            + ', '.join(h for h in headers if h)
            + '. It needs either an "Amount" column, or "Debit" and "Credit" columns.'
        )

    fmt = BankStatementFormat(
        name=f'auto-detected ({bank_name or "upload"})',
        bank_name=bank_name or 'Unknown',
        delimiter=delimiter,
        encoding='utf-8',
        skip_rows=0,
        date_column=date_col,
        date_format=_require_date_format(csv_text, skip, date_col, delimiter),
        description_column=desc_col,
        reference_column=_pick(headers, _REF_COLS),
        balance_column=_pick(headers, _BALANCE_COLS),
        amount_column=amount_col,
        debit_column=debit_col if not amount_col else None,
        credit_column=credit_col if not amount_col else None,
    )
    return fmt, skip


def _require_date_format(csv_text: str, skip: int, date_header: str,
                         delimiter: str = ',') -> str:
    """detect_date_format() guesses '%d/%m/%Y' when nothing matches.

    Bug 20b32822: that guess is why an upload failed with "No valid data rows
    parsed from CSV" — every strptime then raised, every row was dropped, and
    nothing said the dates were the problem. Confirm the chosen format actually
    parses the file's own dates, and if it does not, say so with a real example
    so the person uploading can see what we could not read.
    """
    from datetime import datetime

    fmt = detect_date_format(csv_text, skip, date_header, delimiter)
    samples = _date_samples(csv_text, skip, date_header, delimiter)
    if samples and not all(_parses(s, fmt, datetime) for s in samples):
        raise StatementFileError(
            f'The dates in the "{date_header}" column are in a format we do not '
            f'recognise — for example "{samples[0]}". Supported formats are: '
            + ', '.join(_DATE_FORMATS) + '.'
        )
    return fmt


def _date_samples(csv_text: str, skip: int, date_header: str,
                  delimiter: str = ',') -> List[str]:
    """The first few non-empty values in the date column."""
    body = '\n'.join(csv_text.splitlines()[skip:])
    reader = csv.DictReader(io.StringIO(body), delimiter=delimiter)
    out: List[str] = []
    for row in reader:
        val = (row.get(date_header) or '').strip()
        if val:
            out.append(val)
        if len(out) >= 5:
            break
    return out


def strip_preamble(csv_text: str, skip: int) -> str:
    """Drop the title rows above the header so csv.DictReader sees the header."""
    if skip <= 0:
        return csv_text
    return '\n'.join(csv_text.splitlines()[skip:])
