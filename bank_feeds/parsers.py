"""
bank_feeds/parsers.py

Per-bank CSV parsers for Botswana banks. Each parser takes a raw byte
stream from a bank statement file and returns a ParsedStatement that
the service layer feeds into BankStatement / BankStatementLine rows.

Parsers shipped in v1:

    parse_fnb_botswana_csv     FNB Botswana — typical online-banking CSV export
    parse_first_capital_csv    First Capital Bank Botswana — CSV export
    parse_generic_csv          Heuristic fallback for unknown banks

Auto-detection at the top of the file inspects the header row and picks
the right parser; the service layer never needs to know which bank
produced the file. When a new bank is added, register its parser in
``BANK_DETECTORS`` and write a per-bank function below.

Important: the actual column names and date formats below are based on
the most common publicly-documented Botswana bank export shapes. When
you have a real sample file from FNB or First Capital, run it through
``parse_csv`` and check the returned ParsedStatement matches the file —
the column-mapping table at the top of each parser is the ONE place to
adjust.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Callable

from .services import ParsedLine, ParsedStatement


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DATE_FORMATS = [
    '%Y-%m-%d',         # 2026-04-30
    '%d/%m/%Y',         # 30/04/2026  (BW default)
    '%d-%m-%Y',         # 30-04-2026
    '%d %b %Y',         # 30 Apr 2026
    '%d %B %Y',         # 30 April 2026
    '%m/%d/%Y',         # 04/30/2026  (rare, US-flavoured exports)
]


def _parse_date(raw: str) -> str:
    """Return ISO YYYY-MM-DD or '' on failure."""
    if not raw:
        return ''
    s = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return ''


_AMOUNT_RE = re.compile(r'^[+\-]?\s*[0-9.,]+$')


def _parse_amount(raw: str | None) -> Decimal:
    """Parse a money string. Accepts thousands separators and parentheses
    for negative ('(1,234.56)'). Returns Decimal('0.00') on failure."""
    if raw is None:
        return Decimal('0.00')
    s = str(raw).strip()
    if not s:
        return Decimal('0.00')
    negative = False
    if s.startswith('(') and s.endswith(')'):
        negative = True
        s = s[1:-1]
    s = s.replace(',', '').replace(' ', '')
    if not _AMOUNT_RE.match(s):
        return Decimal('0.00')
    try:
        v = Decimal(s)
    except InvalidOperation:
        return Decimal('0.00')
    return -v if negative else v


def _read_csv(raw: bytes) -> tuple[list[str], list[list[str]]]:
    """Decode + parse a CSV byte stream. Returns (headers, rows).

    Tolerates UTF-8 BOM, cp1252 (common Windows encoding from bank apps),
    and several common delimiters (',' ';' '\\t' '|').
    """
    text = ''
    for enc in ('utf-8-sig', 'utf-8', 'cp1252', 'latin-1'):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if not text:
        return [], []

    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=',;\t|')
    except csv.Error:
        dialect = csv.excel

    reader = csv.reader(io.StringIO(text), dialect=dialect)
    rows = [r for r in reader if any((c or '').strip() for c in r)]
    if not rows:
        return [], []
    headers = [h.strip() for h in rows[0]]
    data = [r for r in rows[1:]]
    return headers, data


def _row_dict(headers: list[str], row: list[str]) -> dict[str, str]:
    """Pair headers and values, lowercasing the header keys for tolerance."""
    out: dict[str, str] = {}
    for i, h in enumerate(headers):
        key = (h or '').strip().lower()
        val = row[i] if i < len(row) else ''
        out[key] = (val or '').strip()
    return out


def _first(d: dict[str, str], *names: str) -> str:
    """Return the first non-empty value under any of the candidate keys."""
    for n in names:
        v = d.get(n.lower(), '')
        if v:
            return v
    return ''


# ---------------------------------------------------------------------------
# Bank detectors
# ---------------------------------------------------------------------------

def _is_fnb_botswana(headers: list[str]) -> bool:
    h = ' '.join(headers).lower()
    # FNB exports typically include account-name banner rows OR the
    # standard column set 'date,description,amount,balance'. They also
    # frequently use 'transaction date' + 'reference' + a 'category' col.
    if 'fnb' in h:
        return True
    has_date = any(k in h for k in ('transaction date', 'date'))
    has_amt  = any(k in h for k in ('amount', 'money in', 'money out', 'debit', 'credit'))
    has_bal  = 'balance' in h
    has_desc = any(k in h for k in ('description', 'transaction description'))
    has_fnb_marker = 'category' in h or 'service fee' in h
    return has_date and has_amt and has_bal and has_desc and has_fnb_marker


def _is_first_capital(headers: list[str]) -> bool:
    h = ' '.join(headers).lower()
    if 'first capital' in h or 'fcb' in h:
        return True
    # FCB exports tend to use ValueDate / TransactionDate + split debit/credit
    has_value_date = 'value date' in h or 'valuedate' in h
    has_split_dr_cr = 'debit' in h and 'credit' in h
    has_running_bal = 'running balance' in h or 'balance' in h
    return has_value_date and has_split_dr_cr and has_running_bal


# ---------------------------------------------------------------------------
# FNB Botswana
# ---------------------------------------------------------------------------

def parse_fnb_botswana_csv(raw: bytes) -> ParsedStatement | None:
    """Parse an FNB Botswana online-banking CSV export.

    Column-mapping table — adjust here when a real sample file lands:

        date          ← 'Transaction Date' / 'Date'
        description   ← 'Description' / 'Transaction Description'
        reference     ← 'Reference' / 'Source Reference'
        money_in      ← 'Amount' (positive) / 'Money In' / 'Credit'
        money_out     ← 'Amount' (negative) / 'Money Out' / 'Debit'
        balance       ← 'Balance' / 'Running Balance'

    FNB sometimes uses one combined 'Amount' column where credits are
    positive and debits are negative; sometimes splits into separate
    'Money In' and 'Money Out' columns. We handle both.
    """
    headers, rows = _read_csv(raw)
    if not headers:
        return None

    lines: list[ParsedLine] = []
    last_balance = ''
    first_balance = ''
    last_date = ''

    for r in rows:
        d = _row_dict(headers, r)

        date_raw = _first(d, 'transaction date', 'date', 'posting date')
        date_iso = _parse_date(date_raw)
        if not date_iso:
            continue

        description = _first(d, 'description', 'transaction description', 'narrative')
        reference   = _first(d, 'reference', 'source reference', 'ref')

        # Amount handling — combined col first, then split cols
        combined = _first(d, 'amount')
        money_in  = _first(d, 'money in', 'credit')
        money_out = _first(d, 'money out', 'debit')

        if combined:
            amt = _parse_amount(combined)
        elif money_in or money_out:
            amt = _parse_amount(money_in) - _parse_amount(money_out)
        else:
            continue  # row has no money — header / blank line

        balance = _first(d, 'balance', 'running balance')
        if balance and not first_balance:
            # Reverse the running balance to get opening: opening = first_balance - amt
            try:
                first_balance = str((Decimal(balance.replace(',', '')) - amt).quantize(Decimal('0.01')))
            except (InvalidOperation, ValueError):
                pass
        if balance:
            last_balance = balance
        last_date = date_iso

        lines.append(ParsedLine(
            transaction_date=date_iso,
            description=description,
            amount=str(amt),
            balance=balance.replace(',', '') if balance else '',
            reference=reference,
            raw=d,
        ))

    if not lines:
        return None

    return ParsedStatement(
        statement_date=last_date or '',
        opening_balance=first_balance or '0.00',
        closing_balance=(last_balance or '0.00').replace(',', ''),
        lines=lines,
    )


# ---------------------------------------------------------------------------
# First Capital Bank
# ---------------------------------------------------------------------------

def parse_first_capital_csv(raw: bytes) -> ParsedStatement | None:
    """Parse a First Capital Bank Botswana CSV export.

    Column-mapping table — adjust when a real sample lands:

        transaction_date ← 'Transaction Date' / 'TransactionDate' / 'Posting Date'
        value_date       ← 'Value Date' / 'ValueDate'  (used as fallback)
        description      ← 'Description' / 'Narrative' / 'Remarks'
        reference        ← 'Reference' / 'Cheque Number'
        debit            ← 'Debit' / 'Withdrawal'
        credit           ← 'Credit' / 'Deposit'
        balance          ← 'Balance' / 'Running Balance' / 'Closing Balance'

    FCB exports typically split debit + credit into separate columns.
    Convention: debit reduces our balance, credit increases it. We
    normalise to a signed amount where credit = positive, debit = negative.
    """
    headers, rows = _read_csv(raw)
    if not headers:
        return None

    lines: list[ParsedLine] = []
    last_balance = ''
    first_balance = ''
    last_date = ''

    for r in rows:
        d = _row_dict(headers, r)

        date_raw = (
            _first(d, 'transaction date', 'transactiondate', 'posting date')
            or _first(d, 'value date', 'valuedate')
        )
        date_iso = _parse_date(date_raw)
        if not date_iso:
            continue

        description = _first(d, 'description', 'narrative', 'remarks', 'transaction description')
        reference   = _first(d, 'reference', 'cheque number', 'ref')

        debit = _parse_amount(_first(d, 'debit', 'withdrawal'))
        credit = _parse_amount(_first(d, 'credit', 'deposit'))
        amt = credit - debit
        if amt == Decimal('0.00'):
            continue

        balance = _first(d, 'balance', 'running balance', 'closing balance')
        if balance and not first_balance:
            try:
                first_balance = str((Decimal(balance.replace(',', '')) - amt).quantize(Decimal('0.01')))
            except (InvalidOperation, ValueError):
                pass
        if balance:
            last_balance = balance
        last_date = date_iso

        lines.append(ParsedLine(
            transaction_date=date_iso,
            description=description,
            amount=str(amt),
            balance=balance.replace(',', '') if balance else '',
            reference=reference,
            raw=d,
        ))

    if not lines:
        return None

    return ParsedStatement(
        statement_date=last_date or '',
        opening_balance=first_balance or '0.00',
        closing_balance=(last_balance or '0.00').replace(',', ''),
        lines=lines,
    )


# ---------------------------------------------------------------------------
# Generic CSV fallback
# ---------------------------------------------------------------------------

def parse_generic_csv(raw: bytes) -> ParsedStatement | None:
    """Best-effort parser for an unknown bank's CSV. Looks for any column
    that smells like a date, any column that smells like a description,
    and any column that smells like an amount or debit/credit pair."""
    headers, rows = _read_csv(raw)
    if not headers:
        return None
    h_lower = [h.lower() for h in headers]

    def find_idx(*names: str) -> int:
        for n in names:
            if n in h_lower:
                return h_lower.index(n)
            for i, h in enumerate(h_lower):
                if n in h:
                    return i
        return -1

    date_i = find_idx('date', 'transaction date', 'posting date', 'value date')
    desc_i = find_idx('description', 'narrative', 'transaction description')
    amt_i  = find_idx('amount', 'transaction amount')
    debit_i  = find_idx('debit', 'withdrawal')
    credit_i = find_idx('credit', 'deposit')
    bal_i    = find_idx('balance', 'running balance')
    ref_i    = find_idx('reference', 'ref')

    if date_i < 0 or desc_i < 0 or (amt_i < 0 and (debit_i < 0 or credit_i < 0)):
        return None

    lines: list[ParsedLine] = []
    last_balance = ''
    first_balance = ''
    last_date = ''

    for r in rows:
        try:
            date_iso = _parse_date(r[date_i])
        except IndexError:
            continue
        if not date_iso:
            continue
        description = (r[desc_i] if desc_i < len(r) else '').strip()

        if amt_i >= 0:
            amt = _parse_amount(r[amt_i] if amt_i < len(r) else '')
        else:
            dr = _parse_amount(r[debit_i]  if debit_i  < len(r) else '')
            cr = _parse_amount(r[credit_i] if credit_i < len(r) else '')
            amt = cr - dr
        if amt == Decimal('0.00'):
            continue

        balance = (r[bal_i] if bal_i >= 0 and bal_i < len(r) else '').strip()
        reference = (r[ref_i] if ref_i >= 0 and ref_i < len(r) else '').strip()

        if balance and not first_balance:
            try:
                first_balance = str((Decimal(balance.replace(',', '')) - amt).quantize(Decimal('0.01')))
            except (InvalidOperation, ValueError):
                pass
        if balance:
            last_balance = balance
        last_date = date_iso

        lines.append(ParsedLine(
            transaction_date=date_iso,
            description=description,
            amount=str(amt),
            balance=balance.replace(',', '') if balance else '',
            reference=reference,
        ))

    if not lines:
        return None
    return ParsedStatement(
        statement_date=last_date or '',
        opening_balance=first_balance or '0.00',
        closing_balance=(last_balance or '0.00').replace(',', ''),
        lines=lines,
    )


# ---------------------------------------------------------------------------
# Top-level dispatch
# ---------------------------------------------------------------------------

# Each entry: (detector_fn, parser_fn, friendly_name) — first match wins.
BANK_DETECTORS: list[tuple[Callable[[list[str]], bool], Callable[[bytes], ParsedStatement | None], str]] = [
    (_is_fnb_botswana,    parse_fnb_botswana_csv, 'FNB Botswana'),
    (_is_first_capital,   parse_first_capital_csv, 'First Capital Bank'),
]


def detect_bank_name(headers: list[str]) -> str:
    """Return the friendly name of the bank we'd route this header set to,
    or '' if we'd fall through to the generic parser."""
    for detector, _parser, name in BANK_DETECTORS:
        if detector(headers):
            return name
    return ''


def parse_csv(raw: bytes, *, hint_bank: str | None = None) -> ParsedStatement | None:
    """Auto-detect bank from CSV headers and route to the right parser.

    `hint_bank` overrides auto-detection — pass 'fnb_botswana' or
    'first_capital' to force a specific parser. Falls through to the
    generic parser when nothing matches.
    """
    if hint_bank:
        forced = {
            'fnb_botswana':   parse_fnb_botswana_csv,
            'fnb':            parse_fnb_botswana_csv,
            'first_capital':  parse_first_capital_csv,
            'fcb':            parse_first_capital_csv,
        }.get(hint_bank.lower())
        if forced:
            result = forced(raw)
            if result is not None:
                return result
            log.warning('Hinted parser %r returned None — falling back to auto', hint_bank)

    headers, _ = _read_csv(raw)
    if not headers:
        return None

    for detector, parser, name in BANK_DETECTORS:
        if detector(headers):
            log.info('CSV detected as %s (%d headers)', name, len(headers))
            return parser(raw)

    log.info('No bank detector matched; falling back to generic parser')
    return parse_generic_csv(raw)
