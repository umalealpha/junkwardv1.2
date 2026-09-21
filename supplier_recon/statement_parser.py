"""supplier_recon/statement_parser.py — read an uploaded supplier statement
(CSV / XLSX / XLS / ODS) into structured, validated lines.

Runs entirely server-side; the bytes never leave the server. Reuses the house
cell coercers from commissions.importer (``_dec`` money, ``_date`` dates,
``_norm`` header text) and python-calamine — the reader the rest of the codebase
standardises on — for spreadsheets, so behaviour matches the payroll / vendor /
commission uploads. Old Excel .xlsb and PDF are out of scope for this first cut
(the usability test asked for CSV/XLSX first, PDF later).

Column layout is NOT assumed — headers are matched by text, in either a
debit/credit pair or a single signed-amount column. Every parsed row keeps its
original cells in ``raw`` for audit, and every rejected row is reported with a
row number and a plain-English reason rather than silently dropped.
"""
from __future__ import annotations

import csv
import io
import os
from dataclasses import dataclass, field
from decimal import Decimal

from commissions.importer import _date, _dec, _norm

from .constants import StatementLineType

ZERO = Decimal('0.00')

SUPPORTED_EXT = ('.csv', '.txt', '.xlsx', '.xlsm', '.xls', '.ods')

# header alias -> field. Matched against the normalised (lower, single-spaced)
# header cell with a substring test, most specific aliases first.
_HEADER_ALIASES = {
    'date':        ('doc date', 'document date', 'invoice date', 'transaction date',
                    'trans date', 'posting date', 'date'),
    'reference':   ('invoice number', 'invoice no', 'invoice #', 'tax invoice',
                    'document no', 'doc no', 'reference', 'ref no', 'invoice',
                    'document', 'number', 'ref'),
    'description': ('description', 'details', 'narration', 'particulars',
                    'transaction type', 'comment'),
    'debit':       ('debit', 'charges', 'charge', ' dr', 'dr '),
    'credit':      ('credit', 'payments', 'payment', 'receipts', 'receipt', ' cr', 'cr '),
    'amount':      ('amount', 'value'),
    'balance':     ('running balance', 'balance due', 'outstanding', 'balance'),
}

_CREDIT_NOTE_WORDS = ('credit note', 'credit memo', 'cr note', ' cn ', 'cn-',
                      'return', 'reversal', 'rebate')
_PAYMENT_WORDS = ('payment', 'receipt', 'eft', 'rtgs', 'paid', 'remittance',
                  'deposit', 'transfer')
_OPENING_WORDS = ('opening balance', 'balance brought forward', 'balance b/f',
                  'bal b/f', 'b/fwd', 'brought forward', 'opening')
_CLOSING_WORDS = ('closing balance', 'balance carried forward', 'balance c/f',
                  'bal c/f', 'c/fwd', 'carried forward', 'closing')


class StatementParseError(Exception):
    """The file could not be read at all (wrong type, no header, no data)."""


@dataclass
class ParsedLine:
    row_number: int
    line_type: str
    reference: str
    doc_date: object
    description: str
    amount: Decimal
    running_balance: object
    raw: dict


@dataclass
class ParsedStatement:
    currency: str = 'BWP'
    statement_date: object = None
    opening_balance: object = None
    closing_balance: object = None
    lines: list = field(default_factory=list)
    errors: list = field(default_factory=list)


# --------------------------------------------------------------------------- #
# File -> rows
# --------------------------------------------------------------------------- #
def _read_rows(data: bytes, filename: str):
    ext = os.path.splitext(filename or '')[1].lower()
    if ext not in SUPPORTED_EXT:
        raise StatementParseError(
            f"Unsupported file type '{ext or '?'}'. Upload a CSV or Excel (.xlsx) file.")
    if ext in ('.csv', '.txt'):
        text = data.decode('utf-8-sig', errors='replace')
        if not text.strip():
            raise StatementParseError('The file is empty.')
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=',;\t|')
        except csv.Error:
            dialect = csv.excel
        return [list(r) for r in csv.reader(io.StringIO(text), dialect)]
    # Spreadsheet — pick the sheet with the most rows (statements are one sheet,
    # but a workbook can carry a cover tab).
    from python_calamine import CalamineWorkbook
    try:
        wb = CalamineWorkbook.from_filelike(io.BytesIO(data))
    except Exception as e:  # noqa: BLE001 — any reader failure = unreadable file
        raise StatementParseError(f'Could not read the spreadsheet: {e}') from e
    best = []
    for name in wb.sheet_names:
        rows = wb.get_sheet_by_name(name).to_python()
        if len(rows) > len(best):
            best = rows
    return best


# --------------------------------------------------------------------------- #
# Header detection
# --------------------------------------------------------------------------- #
def _map_headers(row) -> dict:
    """{field: column_index} for a candidate header row (first free match wins)."""
    norm = [_norm(c) for c in row]
    used, mapping = set(), {}
    for field_name, aliases in _HEADER_ALIASES.items():
        for alias in aliases:
            hit = None
            for i, h in enumerate(norm):
                if i in used or not h:
                    continue
                if alias.strip() in h:
                    hit = i
                    break
            if hit is not None:
                mapping[field_name] = hit
                used.add(hit)
                break
    return mapping


def _find_header(rows, scan=40):
    """Row index + column map. A usable header needs a reference column AND a way
    to read money (debit/credit pair, or an amount column)."""
    for i, row in enumerate(rows[:scan]):
        m = _map_headers(row)
        has_money = ('debit' in m or 'credit' in m or 'amount' in m)
        if 'reference' in m and has_money:
            return i, m
    raise StatementParseError(
        'No recognisable statement header found (need an invoice/reference '
        'column and a debit/credit or amount column).')


# --------------------------------------------------------------------------- #
# Row classification
# --------------------------------------------------------------------------- #
def _classify(description: str, debit, credit, amount):
    """Return (line_type, positive_amount). Sign is carried by line_type so an
    invoice amount is always shown positive."""
    d = _norm(description)
    if any(w in d for w in _OPENING_WORDS):
        return StatementLineType.BALANCE_FWD, ZERO
    if debit is not None and debit != ZERO:
        return StatementLineType.INVOICE, abs(debit)
    if credit is not None and credit != ZERO:
        if any(w in d for w in _CREDIT_NOTE_WORDS):
            return StatementLineType.CREDIT_NOTE, abs(credit)
        if any(w in d for w in _PAYMENT_WORDS):
            return StatementLineType.PAYMENT, abs(credit)
        return StatementLineType.PAYMENT, abs(credit)      # a credit defaults to a payment
    if amount is not None and amount != ZERO:
        if amount < ZERO:
            if any(w in d for w in _CREDIT_NOTE_WORDS):
                return StatementLineType.CREDIT_NOTE, abs(amount)
            return StatementLineType.PAYMENT, abs(amount)
        if any(w in d for w in _CREDIT_NOTE_WORDS):
            return StatementLineType.CREDIT_NOTE, abs(amount)
        return StatementLineType.INVOICE, abs(amount)
    return StatementLineType.OTHER, ZERO


def _cell(row, idx):
    return row[idx] if idx is not None and idx < len(row) else None


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def parse_statement(data: bytes, filename: str) -> ParsedStatement:
    """Parse the uploaded bytes into a ParsedStatement. Raises StatementParseError
    only when the file is unreadable as a whole; individual bad rows land in
    ``.errors`` and are skipped."""
    rows = _read_rows(data, filename)
    if not rows:
        raise StatementParseError('The file has no rows.')
    header_idx, m = _find_header(rows)

    out = ParsedStatement()
    row_no = 0
    last_balance = None
    first_balance_seen = False

    for raw_row in rows[header_idx + 1:]:
        row_no += 1
        # Skip wholly-blank rows.
        if not any(str(c).strip() for c in raw_row if c is not None):
            continue

        desc = str(_cell(raw_row, m.get('description')) or '').strip()
        ref = str(_cell(raw_row, m.get('reference')) or '').strip()
        norm_desc = _norm(desc + ' ' + ref)

        debit = _dec(_cell(raw_row, m['debit'])) if 'debit' in m else None
        credit = _dec(_cell(raw_row, m['credit'])) if 'credit' in m else None
        amount = _dec(_cell(raw_row, m['amount'])) if 'amount' in m else None
        balance = _dec(_cell(raw_row, m['balance'])) if 'balance' in m else None
        doc_date = _date(_cell(raw_row, m.get('date')))

        line_type, pos_amount = _classify(desc, debit, credit, amount)

        raw = {str(i): (str(c) if c is not None else '')
               for i, c in enumerate(raw_row)}

        # Opening / closing balance rows anchor the statement totals rather than
        # becoming a payable line.
        if line_type == StatementLineType.BALANCE_FWD or any(
                w in norm_desc for w in _OPENING_WORDS):
            if out.opening_balance is None and balance is not None:
                out.opening_balance = balance
            first_balance_seen = True
            if balance is not None:
                last_balance = balance
            continue
        if any(w in norm_desc for w in _CLOSING_WORDS):
            if balance is not None:
                out.closing_balance = balance
                last_balance = balance
            continue

        # A totals/summary row with no reference and no amount is not a payable.
        if not ref and pos_amount == ZERO:
            continue

        # An invoice line with no reference cannot be matched — keep it, but flag.
        if line_type == StatementLineType.INVOICE and not ref:
            out.errors.append({
                'row': header_idx + 1 + row_no,
                'message': 'Invoice line has no invoice/reference number.'})

        if pos_amount == ZERO and line_type in (
                StatementLineType.INVOICE, StatementLineType.PAYMENT,
                StatementLineType.CREDIT_NOTE):
            out.errors.append({
                'row': header_idx + 1 + row_no,
                'message': f'{line_type} line has a zero or unreadable amount.'})

        out.lines.append(ParsedLine(
            row_number=row_no,
            line_type=line_type,
            reference=ref[:100],
            doc_date=doc_date,
            description=desc[:255],
            amount=pos_amount,
            running_balance=balance,
            raw=raw,
        ))
        if not first_balance_seen and balance is not None and out.opening_balance is None:
            # Derive an opening balance from the first line that carries a running
            # balance: opening = running - this line's signed movement.
            signed = pos_amount if line_type == StatementLineType.INVOICE else -pos_amount
            out.opening_balance = balance - signed
            first_balance_seen = True
        if balance is not None:
            last_balance = balance
        if doc_date and (out.statement_date is None or doc_date > out.statement_date):
            out.statement_date = doc_date

    if not out.lines:
        raise StatementParseError('No statement lines could be read from the file.')

    if out.closing_balance is None and last_balance is not None:
        out.closing_balance = last_balance

    return out
