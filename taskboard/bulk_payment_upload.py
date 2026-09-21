"""taskboard/bulk_payment_upload.py — read a list of payments out of a file.

Legakwa Ntabeni, 2026-09-11: "on payment requests you can only load payments
individually, there should be a feature that allows for batch payments such as
commission and salary payments. we should be able to upload into the FNB template
file so it uploads successfully."

THIS MODULE ONLY READS. It creates nothing, and deliberately so. Omni's payment
create path carries roughly six hundred lines of money controls — the duplicate
gate (PAY-DUP-01), the bank-change gate (PAY-BANK-01), first-payment-to-a-new-payee
(PAY-BANK-03), the hard bank cross-check (PAY-BANK-04), supplier payment terms.
A bulk importer that wrote rows straight into the database would walk past every
one of them, and would keep walking past every control added after it. So the
upload reads and checks the file here, and each row is then created through the
ordinary gated endpoint. A bulk upload is a faster way of typing, never a second
way in.

It reads the FNB bulk-payment template itself — the file Finance already makes
by hand — so nobody has to learn a new layout. A plain CSV/XLSX with recognisable
headers is read too.

THE REAL FNB TEMPLATE (from Legakwa's own July commission file):

    BInSol - U ver 1.00,,,,,,,
    11/09/2026,,,,,,,
    62842621725,1.23E+11,,,,,,
    RECIPIENT NAME,RECIPIENT ACCOUNT,RECIPIENT ACCOUNT TYPE,BRANCHCODE,AMOUNT,\
OWN REFERENCE,RECIPIENT REFERENCE,EMAIL 1 NOTIFY
    Maatla Boletswane,62776765160,1,283767,851.06,Unicoin Commission. July. 2026,...

🔴 NOTE THE THIRD LINE. `1.23E+11` is a twelve-digit number that Excel silently
turned into scientific notation when somebody saved the file, and it went to the
bank that way. That is the single best argument for Omni building this file
instead of a person: `_account` below REFUSES a value like that rather than
guessing what it used to be, because guessing an account number is how money
reaches a stranger.
"""
from __future__ import annotations

import csv
import io
import re
from decimal import Decimal, InvalidOperation

# The FNB template's own header row, and the friendlier names a hand-made
# spreadsheet is likely to use. Matched on a squashed, case-folded form so
# "BRANCHCODE", "Branch Code" and "branch_code" are all the same column.
_COLUMNS = {
    'name': ('recipientname', 'name', 'payee', 'beneficiary', 'agent',
             'accountname', 'supplier', 'recipient'),
    'account_number': ('recipientaccount', 'accountnumber', 'account', 'acc',
                       'accountno', 'bankaccount'),
    'account_type': ('recipientaccounttype', 'accounttype', 'type'),
    'branch_code': ('branchcode', 'branch', 'sortcode'),
    'amount': ('amount', 'net', 'netpayable', 'total', 'value', 'pay'),
    'own_reference': ('ownreference', 'ourreference', 'reference', 'ref',
                      'narration', 'description'),
    'recipient_reference': ('recipientreference', 'theirreference',
                            'beneficiaryreference'),
    'email': ('email1notify', 'email', 'emailnotify', 'notify'),
}

# FNB account types. 1 is a current/cheque account and is what every row of the
# real file carries; the others are accepted because the template allows them.
_ACCOUNT_TYPES = {'1', '2', '3', '4'}

# Excel's scientific notation, e.g. 1.23E+11. Never a real account number.
_SCIENTIFIC = re.compile(r'^\d+(\.\d+)?[eE][+-]?\d+$')


def _squash(s: str) -> str:
    return ''.join(ch for ch in (s or '').lower() if ch.isalnum())


def _match_columns(headers: list[str]) -> dict:
    """Map our field names onto the file's actual column names."""
    found = {}
    squashed = {(h or ''): _squash(h) for h in headers}
    for field, aliases in _COLUMNS.items():
        for header, sq in squashed.items():
            if sq and sq in aliases:
                found[field] = header
                break
    return found


def _money(raw) -> Decimal | None:
    txt = str(raw or '').strip().replace(',', '').replace(' ', '')
    if not txt:
        return None
    for sym in ('BWP', 'P', 'ZAR', 'R', 'USD', '$'):
        if txt.upper().startswith(sym):
            txt = txt[len(sym):]
    neg = txt.startswith('(') and txt.endswith(')')
    txt = txt.strip('()')
    try:
        val = Decimal(txt)
    except InvalidOperation:
        return None
    # Decimal ACCEPTS 'NaN', 'sNaN', 'Infinity' and '-inf' — they are valid
    # decimals, not errors. They then reached the "must be more than zero"
    # check below, where comparing a NaN raises InvalidOperation and the whole
    # upload became a 500 instead of one line saying which row is wrong
    # (Manus QC-UNICOIN-BULK-2026-09-12). An Infinity was worse: it compares
    # greater than zero, so it passed as a real amount.
    if not val.is_finite():
        return None
    return -val if neg else val


def _account(raw) -> tuple[str, str]:
    """(account_number, problem). Digits only, and NEVER a guess.

    Excel writes a long number as 1.23E+11 the moment a column is too narrow or
    the cell is not formatted as text. The digits are then genuinely gone — the
    file no longer contains them. Reconstructing 123000000000 from that would
    send real money to an account nobody typed, so this refuses instead.
    """
    txt = str(raw or '').strip().replace(' ', '').replace('-', '')
    if not txt:
        return '', 'No account number.'
    if _SCIENTIFIC.match(txt):
        return '', (f'"{raw}" is not an account number — Excel has shortened it '
                    f'and the real digits are gone. Format that column as Text '
                    f'in the spreadsheet and save it again.')
    if txt.endswith('.0'):          # Excel read it as a number, digits intact
        txt = txt[:-2]
    if not txt.isdigit():
        return '', f'"{raw}" is not an account number — digits only.'
    if not (5 <= len(txt) <= 20):
        return '', f'"{txt}" is {len(txt)} digits — that is not a bank account number.'
    return txt, ''


def _branch(raw) -> tuple[str, str]:
    txt = str(raw or '').strip().replace(' ', '')
    if txt.endswith('.0'):
        txt = txt[:-2]
    if not txt:
        return '', 'No branch code.'
    if not txt.isdigit():
        return '', f'"{raw}" is not a branch code — digits only.'
    return txt, ''


def _to_csv_text(uploaded_file) -> str:
    """CSV straight through; XLSX via the shared sniffing reader Finance's other
    uploads already use (banking.statement_files), so a .xlsx is never an
    unreadable file the way it was in bug 713d6218."""
    name = (getattr(uploaded_file, 'name', '') or '').lower()
    if name.endswith(('.xlsx', '.xls')):
        from banking import statement_files
        return statement_files.to_csv_text(uploaded_file)
    raw = uploaded_file.read()
    if isinstance(raw, bytes):
        for enc in ('utf-8-sig', 'utf-8', 'cp1252', 'latin-1'):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        raise ValueError('We could not read that file. Save it as CSV or XLSX '
                         'and try again.')
    return raw


def find_header_row(rows: list[list[str]]) -> int:
    """Which row is the header? The FNB template hides it under three lines of
    preamble (a version tag, the date, the source account), so it is found by
    looking for the row that names a payee column AND an amount column, rather
    than by assuming a row number."""
    for i, row in enumerate(rows[:25]):
        found = _match_columns(row)
        if 'name' in found and 'amount' in found and 'account_number' in found:
            return i
    return -1


def source_account_from(rows: list[list[str]], header_row: int) -> str:
    """The FNB template carries the paying account on the line above the header.
    Read it so the file Omni produces goes out of the same account it came from,
    rather than a default nobody chose."""
    for row in rows[max(0, header_row - 2):header_row]:
        for cell in row:
            txt = str(cell or '').strip()
            if txt.isdigit() and 8 <= len(txt) <= 20:
                return txt
    return ''


def parse_payment_file(uploaded_file) -> dict:
    """Read a payment list. Returns
       {'rows': [...], 'source_account': str, 'total': str, 'ok': int, 'bad': int}

    Every row comes back, good or bad, each carrying its own `problems` list. A
    row is NEVER dropped silently: a payment list that quietly loses a line is
    how somebody goes unpaid and nobody finds out until they call.
    """
    text = _to_csv_text(uploaded_file)
    grid = [r for r in csv.reader(io.StringIO(text))]
    if not grid:
        raise ValueError('That file is empty.')

    h = find_header_row(grid)
    if h < 0:
        raise ValueError(
            'We could not find the column headings in that file. It needs a row '
            'naming at least the payee, the account number and the amount — the '
            'FNB template row starting "RECIPIENT NAME" is exactly right.')

    headers = [str(c or '').strip() for c in grid[h]]
    cols = _match_columns(headers)
    missing = [f for f in ('name', 'account_number', 'amount') if f not in cols]
    if missing:
        pretty = {'name': 'payee name', 'account_number': 'account number',
                  'amount': 'amount'}
        raise ValueError('That file is missing a column for '
                         + ' and '.join(pretty[m] for m in missing) + '.')

    def cell(row, field):
        header = cols.get(field)
        if not header:
            return ''
        try:
            return row[headers.index(header)]
        except (ValueError, IndexError):
            return ''

    out, seen = [], {}
    for n, raw_row in enumerate(grid[h + 1:], start=h + 2):
        if not any(str(c or '').strip() for c in raw_row):
            continue                       # a blank spacer line, not a payment

        problems = []
        # Names come off a spreadsheet with stray spaces (" Pako Mampane",
        # "Thato Barati ") — tidy them, they are not a fault.
        name = ' '.join(str(cell(raw_row, 'name') or '').split())
        if not name:
            problems.append('No payee name.')

        acct, why = _account(cell(raw_row, 'account_number'))
        if why:
            problems.append(why)
        branch, why = _branch(cell(raw_row, 'branch_code'))
        if why:
            problems.append(why)

        amount = _money(cell(raw_row, 'amount'))
        if amount is None:
            problems.append(f'"{cell(raw_row, "amount")}" is not an amount.')
        elif amount <= 0:
            problems.append('The amount must be more than zero — a bank file '
                            'cannot carry a zero or a refund.')

        atype = str(cell(raw_row, 'account_type') or '').strip() or '1'
        if atype.endswith('.0'):
            atype = atype[:-2]
        if atype not in _ACCOUNT_TYPES:
            problems.append(f'"{atype}" is not an FNB account type (1 = cheque '
                            f'or current, which is what nearly every row is).')
            atype = '1'

        own_ref = ' '.join(str(cell(raw_row, 'own_reference') or '').split())
        their_ref = ' '.join(
            str(cell(raw_row, 'recipient_reference') or '').split()) or own_ref
        email = str(cell(raw_row, 'email') or '').strip()

        # The same account for the same amount, twice in one file. Not refused —
        # it is occasionally real — but never passed over in silence.
        key = (acct, str(amount))
        if acct and amount is not None:
            if key in seen:
                problems.append(f'Same account and same amount as line {seen[key]} '
                                f'in this file — check it is not a copy.')
            else:
                seen[key] = n

        out.append({
            'line': n,
            'name': name,
            'account_number': acct,
            'account_type': atype,
            'branch_code': branch,
            'amount': str(amount) if amount is not None else '',
            'own_reference': own_ref,
            'recipient_reference': their_ref,
            'email': email,
            'problems': problems,
            'ok': not problems,
        })

    if not out:
        raise ValueError('We found the headings but no payment lines underneath '
                         'them.')

    good = [r for r in out if r['ok']]
    total = sum((Decimal(r['amount']) for r in good), Decimal('0'))
    return {
        'rows': out,
        'source_account': source_account_from(grid, h),
        'total': str(total),
        'ok': len(good),
        'bad': len(out) - len(good),
    }
