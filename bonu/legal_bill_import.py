"""Load the panel firms' fee-note register — what BONU was billed and what we paid.

The register arrives as one row per fee note: firm, reference, date, client,
amount, discount, amount paid, payment date, and a free-text status. It is the
answer to the CFO's question "what have I paid the lawyers", and until this
import existed Omni held none of it.

Three decisions worth knowing before reading the code:

**The status column is not believed.** It carries fourteen spellings of four
ideas ('Paid', 'Paid ', 'PAID', 'Pad', 'Npt yet captured'), and five rows say
Paid while recording no money because the payment went out in a bulk transfer.
So the stage is derived from the MONEY — settled when the cash covers the bill
net of discount — and the raw text is kept on the note for anyone who wants to
see what the sheet said. A status that disagrees with its own figures is a
question for the firm, not an instruction to us.

**Duplicates are loaded, not refused.** Twenty-four references are billed more
than once, and two of those have been PAID twice. Refusing them would delete
the evidence; they are loaded verbatim and carry the same `dup_key`, which is
what makes them findable. `LegalBill.dup_key` is deliberately not unique for
exactly this reason.

**A firm is matched, never guessed.** The register spells firms differently
from the panel list already in Omni ('Chikati and Partners' against
'CHIKATI & PARTNERS'), so names are compared with case, spacing, punctuation
and '&'/'and' normalised away. Anything that needs more than that — 'Jeremiah
& Co' for 'JEREMIAH TLADI & COMPANY' — must be written down in FIRM_ALIASES
below, where it can be read and argued with. A near-match is never inferred:
the retainer scorecard hangs off the firm record, so quietly attaching bills to
the wrong one would misstate money.
"""

from __future__ import annotations

import datetime
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# Firms the register names differently from the panel list in Omni, where the
# difference is more than spacing or punctuation. Each one is a deliberate
# human call, so each one is written down. Key and value are both normalised
# with `normalise_firm` before use, so either side may be typed naturally.
FIRM_ALIASES = {
    # The CFO's "Jeremiah and Taldi"; the panel record and the two retainer
    # agreements (P40,000 South, P85,000 North) hang off the full name.
    'JEREMIAH AND CO': 'JEREMIAH TLADI AND COMPANY',
    # The panel record carries a typo in the first name.
    'TONY MATILO ATTORNEYS': 'TONEY MATILO ATTORNEYS',
    # Partners against Associates. Same firm per the register's own exception
    # list; flagged in the import report so it can be corrected at source.
    'THANKE AND PARTNERS': 'THANKE AND ASSOCIATES',
    # The register's own exception 24: one firm captured under two spellings.
    'THOBEGA': 'THOBEGA LAW GROUP',
}

#: Columns the register must carry. Missing one is a broken file, not a row to skip.
REQUIRED_COLUMNS = ('source_row', 'firm', 'bill_date', 'reference', 'client',
                    'amount', 'discount', 'amount_paid', 'payment_date', 'status')


class RowError(ValueError):
    """One row could not be read. Carries the register's own row number."""

    def __init__(self, source_row, message):
        self.source_row = source_row
        super().__init__(f'CLAIMS row {source_row}: {message}')


def normalise_firm(name) -> str:
    """The comparable form of a firm's name.

    Upper case, '&' read as 'and', punctuation dropped and runs of whitespace
    collapsed — so 'Chikati and Partners', 'CHIKATI & PARTNERS' and
    'Chikati  And  Partners' are one firm. Nothing else is inferred: a name
    that still differs after this is a different firm until somebody says
    otherwise in FIRM_ALIASES.
    """
    text = str(name or '').upper().replace('&', ' AND ')
    text = re.sub(r'[^A-Z0-9 ]+', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def canonical_firm(name) -> str:
    """The normalised name this firm's bills should be filed under."""
    key = normalise_firm(name)
    return FIRM_ALIASES.get(key, key)


#: Money is stored in `numeric(14,2)`. Parsing must land on the SAME precision,
#: because a figure that is rounded on its way into the database makes the
#: reconciliation a comparison between two different numbers.
MONEY = Decimal('0.01')


def parse_money(value, *, field, source_row, quantise: bool = True) -> Decimal:
    """A money cell as an exact amount, at the two decimals the column stores.

    Blank is zero; anything unreadable raises. Never returns 0 for a value it
    could not read — a silently zeroed amount is how a register stops adding up
    to its own total.

    The register carries three decimals on some cells (4,705,044.864) and the
    column holds two, so the rounding has to happen somewhere. It happens HERE,
    half up, which is the house rule for money — rather than silently inside
    Django's save, half to even, where nothing can see it. The difference
    against the file's own total is printed by the import so it is a number
    somebody has read, not a drift nobody noticed.
    """
    if value is None or str(value).strip() == '':
        return Decimal('0')
    try:
        amount = Decimal(str(value).replace(',', '').replace('P', '').strip())
    except InvalidOperation:
        raise RowError(source_row, f'{field} is not an amount: {value!r}')
    return amount.quantize(MONEY, rounding=ROUND_HALF_UP) if quantise else amount


def parse_date(value):
    """A date cell, or None when it cannot be read.

    Returning None is right here and wrong for money: a bill with an unreadable
    PAYMENT date is still a payment (the register holds one truncated year,
    '03-Jul-202'), and dropping the row would lose the cash. The missing date is
    reported instead, so the month it belongs in can be asked for.
    """
    if value is None or str(value).strip() == '':
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    text = str(value).strip()
    for fmt in ('%d-%b-%Y', '%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
        try:
            return datetime.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def derive_stage(amount: Decimal, discount: Decimal, amount_paid: Decimal) -> str:
    """'paid' once the cash covers the bill net of discount, else 'billed'.

    Derived from the money rather than read off the status column, because the
    column says Paid for five bills that record no payment at all (settled in a
    bulk transfer nobody split back out). Treating those as paid would overstate
    what has gone out by exactly the amount nobody can trace.
    """
    net = amount - discount
    return 'paid' if amount_paid >= net and net > 0 else 'billed'


def parse_row(row: dict) -> dict:
    """One register row as the fields a LegalBill needs.

    Raises RowError on anything that would otherwise land as a wrong number:
    an unreadable amount, a missing invoice date, a bill of zero or less.
    """
    missing = [c for c in REQUIRED_COLUMNS if c not in row]
    if missing:
        raise RowError(row.get('source_row', '?'),
                       f'the file is missing {", ".join(missing)}')

    source_row = row['source_row']
    amount = parse_money(row['amount'], field='Inv Amount', source_row=source_row)
    discount = parse_money(row['discount'], field='Discount', source_row=source_row)
    amount_paid = parse_money(row['amount_paid'], field='Amount Paid', source_row=source_row)

    # The same three cells at the precision the FILE wrote them. Kept only so the
    # import can show what the rounding to two decimals cost, rather than leaving
    # a difference against the register's own total for somebody to trip over.
    exact = {
        'amount': parse_money(row['amount'], field='Inv Amount',
                              source_row=source_row, quantise=False),
        'discount': parse_money(row['discount'], field='Discount',
                                source_row=source_row, quantise=False),
        'amount_paid': parse_money(row['amount_paid'], field='Amount Paid',
                                   source_row=source_row, quantise=False),
    }

    if amount <= 0:
        raise RowError(source_row, f'bill amount is {amount}; a bill must be for money')

    bill_date = parse_date(row['bill_date'])
    if bill_date is None:
        raise RowError(source_row, f'invoice date cannot be read: {row["bill_date"]!r}')

    raw_firm = str(row['firm'] or '').strip()
    if not raw_firm:
        raise RowError(source_row, 'no law firm named')

    paid_on = parse_date(row['payment_date'])
    raw_status = str(row['status'] or '').strip()

    notes = [f'Imported from the CLAIMS fee-note register, row {source_row}.']
    if raw_firm != canonical_firm(raw_firm).title():
        notes.append(f'Firm as written on the register: "{raw_firm}".')
    if raw_status:
        notes.append(f'Status as written on the register: "{raw_status}".')
    if amount_paid > 0 and paid_on is None:
        notes.append('Paid, but the register carries no readable payment date, '
                     'so this payment cannot be placed in a month.')

    return {
        'source_row': int(source_row),
        'firm_key': canonical_firm(raw_firm),
        'raw_firm': raw_firm,
        'reference': str(row['reference'] or '').strip(),
        'bill_date': bill_date,
        'billed_client_name': str(row['client'] or '').strip(),
        'amount': amount,
        'discount': discount,
        'amount_paid': amount_paid,
        'paid_on': paid_on,
        'exact': exact,
        'stage': derive_stage(amount, discount, amount_paid),
        'raw_status': raw_status,
        'note': ' '.join(notes),
    }


def reconcile(parsed: list) -> dict:
    """The totals the load must reproduce, added from the rows themselves.

    Every figure is re-added here rather than copied from the file's own total
    row, so the two can be compared. A load that agrees with itself but not with
    the register is the failure this exists to catch.
    """
    invoiced = sum((r['amount'] for r in parsed), Decimal('0'))
    discount = sum((r['discount'] for r in parsed), Decimal('0'))
    paid = sum((r['amount_paid'] for r in parsed), Decimal('0'))
    # The same totals at the file's own precision, so the cost of storing money
    # in two decimals is a figure on the report instead of an unexplained gap.
    src_invoiced = sum((r['exact']['amount'] for r in parsed), Decimal('0'))
    src_discount = sum((r['exact']['discount'] for r in parsed), Decimal('0'))
    src_paid = sum((r['exact']['amount_paid'] for r in parsed), Decimal('0'))
    return {
        'rows': len(parsed),
        'invoiced': invoiced,
        'discount': discount,
        'paid': paid,
        'outstanding': invoiced - discount - paid,
        'source_invoiced': src_invoiced,
        'source_discount': src_discount,
        'source_paid': src_paid,
        'source_outstanding': src_invoiced - src_discount - src_paid,
        'rounding_difference': (invoiced - discount - paid)
        - (src_invoiced - src_discount - src_paid),
        'firms': len({r['firm_key'] for r in parsed}),
        'paid_without_date': sum(1 for r in parsed
                                 if r['amount_paid'] > 0 and r['paid_on'] is None),
    }
