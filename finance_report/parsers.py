"""
finance_report/parsers.py — turn the finance team's source workbooks into the
normalised rows engine.py works on.

Column names are taken from the real files (31-Aug-2026). They are matched
case-insensitively and by prefix, because these exports rename their headers
slightly between runs — "Total Premium (BWP excl VAT)" has also appeared with
the bracket text truncated. A header that cannot be found raises rather than
defaulting to zero: a report that quietly totals nothing is worse than one that
refuses to open.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from .engine import (
    ClaimRow, HEALTH_INSURANCE, INSTANT_INSURANCE, MOTOR_COMPREHENSIVE,
    PremiumRow, UNION_LEGAL, line_for_policy, money, regulatory_for_mom_product,
)

# The Premium Board is exported INC VAT as well as EX VAT. We use the ex-VAT
# column when it is there — the export already did the division, so re-deriving
# it from the inclusive figure only introduces a second rounding. The fallback
# rate is only for an export that lost the column. VAT rounds HALF UP (CFO).
DEFAULT_VAT_RATE = Decimal('0.14')


class SourceColumnMissing(LookupError):
    """A required column is not in the sheet. Names the sheet and the column so
    whoever exported it can fix the export rather than guess."""


def _norm(name) -> str:
    return str(name or '').strip().lower()


def _find(header, *candidates, required=True, sheet='sheet'):
    """Locate a column by exact match first, then by prefix."""
    norm = [_norm(h) for h in header]
    for c in candidates:
        c = _norm(c)
        if c in norm:
            return norm.index(c)
    for c in candidates:
        c = _norm(c)
        for i, h in enumerate(norm):
            if h.startswith(c):
                return i
    if required:
        raise SourceColumnMissing(
            f'{sheet}: could not find a column named any of {candidates!r}. '
            f'Found: {[h for h in header if h]!r}')
    return None


# The .xlsb claims export hands dates back as Excel serial numbers rather than
# dates — 46262.0 is a day count, not an amount. Read as a string it parses as
# nothing, and every claim row silently drops out of the report with the premium
# side still totalling correctly, which is the worst shape a bug can take here.
# Excel's day 1 is 1900-01-01 but it wrongly counts 1900 as a leap year, so the
# usable epoch is 1899-12-30.
EXCEL_EPOCH = date(1899, 12, 30)
# Serial 20000 is 1954; 200000 is the year 2447. Anything outside that is a
# quantity that happens to sit in a date column, not a date.
EXCEL_SERIAL_MIN, EXCEL_SERIAL_MAX = 20000, 200000


def _cell(row, index) -> str:
    """A cell as clean text. `str(x or '')` matters: openpyxl and pyxlsb hand
    back None for an empty cell, and str(None) is the four-character word
    "None" — which then becomes a regulatory category named None, sitting in the
    report with a real total beside it while every table still ties."""
    if index is None or index >= len(row):
        return ''
    return str(row[index] or '').strip()


def _month(value) -> str | None:
    """'YYYY-MM' from whatever the export produced — a real date, a datetime, an
    Excel serial number, or a string. An unparseable date returns None and the
    row is counted as undated rather than dropped into an arbitrary month."""
    if isinstance(value, datetime):
        return f'{value.year:04d}-{value.month:02d}'
    if isinstance(value, date):
        return f'{value.year:04d}-{value.month:02d}'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if EXCEL_SERIAL_MIN <= value <= EXCEL_SERIAL_MAX:
            d = EXCEL_EPOCH + timedelta(days=int(value))
            return f'{d.year:04d}-{d.month:02d}'
        return None
    s = str(value or '').strip()
    if not s:
        return None
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%Y/%m/%d'):
        try:
            d = datetime.strptime(s[:19] if len(s) >= 19 else s, fmt)
            return f'{d.year:04d}-{d.month:02d}'
        except ValueError:
            continue
    if len(s) >= 7 and s[4] == '-' and s[:4].isdigit():
        return s[:7]
    try:
        serial = float(s)
    except ValueError:
        return None
    return _month(serial)


def parse_premium_board(header, rows, vat_rate=DEFAULT_VAT_RATE):
    """Premium Board -> Corporate and Personal premium rows, ex-VAT, by month.

    Split by Booking Date, which is when the premium was booked — not the policy
    start date and not the payment date. The export carries three other date
    columns, and picking the wrong one shifts whole months.

    Instant Insurance and Motor Comprehensive are deliberately absent: they have
    no policy prefix here and come from the Month-on-Month report instead.
    """
    i_pol = _find(header, 'policy no.', 'policy no', 'policy number', sheet='Premium Board')
    i_book = _find(header, 'booking date', sheet='Premium Board')
    i_reg = _find(header, 'regulatory class (nbfira)', 'regulatory mapping name',
                  'regulatory class', sheet='Premium Board')
    i_ex = _find(header, 'total premium (bwp excl vat)', 'total premium (bwp excl',
                 required=False, sheet='Premium Board')
    i_inc = _find(header, 'total premium (bwp incl vat)', 'total premium (bwp incl',
                  required=False, sheet='Premium Board')
    if i_ex is None and i_inc is None:
        raise SourceColumnMissing(
            'Premium Board: no premium column found (looked for the excl-VAT and '
            'incl-VAT totals).')

    out, skipped = [], {'no_month': 0, 'no_line': 0}
    for r in rows:
        month = _month(r[i_book] if i_book < len(r) else None)
        if month is None:
            skipped['no_month'] += 1
            continue
        line = line_for_policy(r[i_pol] if i_pol < len(r) else '')
        if line is None:
            skipped['no_line'] += 1
            continue
        if i_ex is not None:
            amount = money(r[i_ex] if i_ex < len(r) else 0)
        else:
            amount = money(money(r[i_inc] if i_inc < len(r) else 0) / (Decimal('1') + vat_rate))
        out.append(PremiumRow(month=month, line=line,
                              regulatory=_cell(r, i_reg), amount=amount))
    return out, skipped


def parse_month_on_month(header, rows):
    """Month-on-Month -> Instant Insurance and Motor Comprehensive premium.

    Already ex-VAT, one row per paying client. "Product type" holds only the two
    values, so it does the split directly; "Product" is kept on the row because
    Table 3 needs to pull Third Party Car Insurance out of Instant Insurance,
    and Table 4 needs it to work out the regulatory mapping.
    """
    i_amt = _find(header, 'amount collected exc vat', 'amount collected (ex vat)',
                  'amount collected exc', sheet='Month-on-Month')
    i_type = _find(header, 'product type', sheet='Month-on-Month')
    i_prod = _find(header, 'product', sheet='Month-on-Month')
    i_date = _find(header, 'year/date', 'year / date', 'date', sheet='Month-on-Month')

    out, skipped = [], {'no_month': 0}
    for r in rows:
        month = _month(r[i_date] if i_date < len(r) else None)
        if month is None:
            skipped['no_month'] += 1
            continue
        product = _cell(r, i_prod)
        ptype = _cell(r, i_type)
        # "Product type" is the direct split. If it is blank, fall back to the
        # documented rule: Motor Comprehensive is itself, everything else is
        # Instant Insurance.
        if ptype.lower() == 'motor comprehensive' or (
                not ptype and product.lower() == 'motor comprehensive'):
            line = MOTOR_COMPREHENSIVE
        else:
            line = INSTANT_INSURANCE
        out.append(PremiumRow(month=month, line=line,
                              regulatory=regulatory_for_mom_product(product),
                              amount=money(r[i_amt] if i_amt < len(r) else 0),
                              product=product))
    return out, skipped


def parse_claims_as_on_date(header, rows):
    """Claims As On Date -> claim reserves by month.

    Split by Reported Date, and MIS is the prefix that appears only here: those
    are the Instant Insurance and Motor Comprehensive claims, separated by
    Product Name. Reserves keep whatever sign the export gave them.
    """
    i_pol = _find(header, 'policynumber', 'policy number', 'policy no.', sheet='Claims')
    i_rep = _find(header, 'reporteddate', 'reported date', sheet='Claims')
    i_res = _find(header, 'reserveamt', 'total reserves', 'reserve amount', sheet='Claims')
    i_paid = _find(header, 'paymentamt', 'total payments', 'payment amount', 'paid',
                   required=False, sheet='Claims')
    i_reg = _find(header, 'regulatorymapping', 'regulatory mapping name', sheet='Claims')
    i_type = _find(header, 'claimtype', 'claim type', sheet='Claims')
    i_prod = _find(header, 'productname', 'product name', required=False, sheet='Claims')

    out, skipped = [], {'no_month': 0, 'no_line': 0}
    for r in rows:
        month = _month(r[i_rep] if i_rep < len(r) else None)
        if month is None:
            skipped['no_month'] += 1
            continue
        policy = _cell(r, i_pol).upper()
        line = line_for_policy(policy)
        if line is None:
            if policy.startswith('MIS'):
                product = _cell(r, i_prod)
                line = (MOTOR_COMPREHENSIVE
                        if product.lower() == 'motor comprehensive'
                        else INSTANT_INSURANCE)
            else:
                skipped['no_line'] += 1
                continue
        out.append(ClaimRow(
            month=month, line=line,
            regulatory=_cell(r, i_reg),
            claim_type=_cell(r, i_type),
            reserve=money(r[i_res] if i_res < len(r) else 0),
            paid=money(r[i_paid] if (i_paid is not None and i_paid < len(r)) else 0)))
    return out, skipped


def manual_premium_rows(entries):
    """Union Legal and Health each feed a single line item from their own
    report. Rather than a fourth and fifth parser for two numbers a month, they
    are captured directly — {'month': '2026-07', 'line': ..., 'amount': ...}.
    """
    allowed = {UNION_LEGAL, HEALTH_INSURANCE}
    out = []
    for e in entries or []:
        line = (e.get('line') or '').strip()
        if line not in allowed:
            raise ValueError(
                f'{line!r} is not a manual line. Only {sorted(allowed)} are entered by hand; '
                'everything else must come from a source report.')
        month = _month(e.get('month'))
        if month is None:
            raise ValueError(f'Could not read a month from {e.get("month")!r}.')
        out.append(PremiumRow(month=month, line=line,
                              regulatory=line, amount=money(e.get('amount'))))
    return out
