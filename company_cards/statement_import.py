"""
company_cards/statement_import.py — read a card statement file into rows.

Reuses banking.statement_files, which sniffs the header row instead of needing
a format configured per bank. That module exists because manual statement
upload had NEVER once worked in production: every upload died on a missing
BankStatementFormat, and an .xlsx was never decodable at all (bug 713d6218).
No reason to repeat that here.

Returns [{'date': date, 'description': str, 'amount': Decimal}] with amounts as
POSITIVE spend. A credit-card statement shows a purchase as a debit and a
refund as a credit; only spend needs a receipt, so credits are dropped.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
from decimal import Decimal, InvalidOperation

from banking import statement_files


def _money(raw: str) -> Decimal | None:
    txt = (raw or '').strip().replace(',', '').replace(' ', '')
    if not txt:
        return None
    neg = txt.startswith('(') and txt.endswith(')')
    txt = txt.strip('()')
    for sym in ('BWP', 'P', 'R', '$', 'ZAR', 'USD'):
        if txt.upper().startswith(sym):
            txt = txt[len(sym):]
    try:
        val = Decimal(txt)
    except InvalidOperation:
        return None
    return -val if neg else val


def parse_card_statement(uploaded_file) -> list[dict]:
    """Rows of spend from a CSV / XLSX card statement. Raises ValueError with a
    plain-English reason a person can act on."""
    try:
        csv_text = statement_files.to_csv_text(uploaded_file)
    except Exception as exc:                    # noqa: BLE001
        raise ValueError('We could not read that file. Save it as CSV or XLSX '
                         f'and try again. ({exc})') from exc

    skip, headers = statement_files.find_header_row(csv_text)
    if not headers:
        raise ValueError('We could not find a header row with a date and an '
                         'amount in that file.')

    date_col = statement_files._pick(headers, statement_files._DATE_COLS)
    desc_col = statement_files._pick(headers, statement_files._DESC_COLS)
    amt_col = statement_files._pick(headers, statement_files._AMOUNT_COLS)
    debit_col = statement_files._pick(headers, statement_files._DEBIT_COLS)
    credit_col = statement_files._pick(headers, statement_files._CREDIT_COLS)
    if not date_col or not (amt_col or debit_col):
        raise ValueError('That file needs at least a date column and an amount '
                         '(or debit) column.')

    fmt = statement_files.detect_date_format(csv_text, skip, date_col) or '%Y-%m-%d'
    body = statement_files.strip_preamble(csv_text, skip)

    out: list[dict] = []
    for row in csv.DictReader(io.StringIO(body)):
        raw_date = (row.get(date_col) or '').strip()
        if not raw_date:
            continue
        try:
            when = dt.datetime.strptime(raw_date, fmt).date()
        except ValueError:
            continue

        # A purchase is a debit. A credit is a refund and needs no receipt.
        if debit_col and (row.get(debit_col) or '').strip():
            amount = _money(row.get(debit_col))
        elif amt_col:
            amount = _money(row.get(amt_col))
            if amount is not None and amount < 0:
                amount = -amount        # some banks sign spend negative
            elif amount is not None and credit_col and (row.get(credit_col) or '').strip():
                continue                # explicit credit column populated → refund
        else:
            amount = None
        if amount is None or amount == 0:
            continue

        out.append({
            'date': when,
            'description': (row.get(desc_col) or '').strip() if desc_col else '',
            'amount': abs(amount),
        })
    return out
