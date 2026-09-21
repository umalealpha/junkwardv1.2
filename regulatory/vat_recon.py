"""
regulatory/vat_recon.py — VAT reconciliation, the arithmetic.

Pure Python. No ORM, no Django, no I/O — the same rule `claims/reconciliation/
engine.py` follows, so the whole calculation can be unit-tested directly with
worked examples. `regulatory/vat_recon_service.py` is the caller that reads the
database and hands the figures in.

WHAT IT PRODUCES
----------------
For a VAT period: output VAT, input VAT, the net payable or refundable, and —
the part that makes it a RECONCILIATION rather than a report — the tie-out of
each of those to the general ledger VAT control accounts, with an EXCEPTIONS
list naming every line that does not reconcile.

    net VAT = output VAT (sales, net of credit notes, plus self-assessed
              reverse-charge output) − input VAT (purchases, plus recoverable
              reverse-charge input)

    net > 0  → PAYABLE to BURS
    net < 0  → REFUNDABLE from BURS

🔴 THIS MODULE POSTS NOTHING.
It reads figures that already exist and compares them. It does not create a
journal entry, it does not change a GL mapping, and it must not be extended to
do either without the CFO signing off first (build spec B2, hard stop; the
revenue format and the management-accounts P&L have been FROZEN since
13 May 2026). A difference between the sub-ledger and the GL is REPORTED as an
exception — it is never "corrected" by posting the plug.

THE RATE IS A PARAMETER
-----------------------
`vat_rate` is an argument to every function here. There is no 0.14 anywhere in
this file. The caller supplies `settings.RC_VAT_RATE` — the one VAT rate
constant this repo has (`alpha_finance/settings.py`, env-configurable), already
used the same way by `billing/reverse_charge_models.py`. A second VAT rate
constant is not introduced; two rates that can disagree is a worse bug than a
hardcoded one.

ROUNDING
--------
`Decimal(...).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)`. Botswana VAT
rounds HALF UP. That is a tax decision, never a language default — Python's
built-in `round()` and Decimal's own default do BANKER'S rounding
(ROUND_HALF_EVEN) and are wrong here: 0.105 must become 0.11, not 0.10.

DATES
-----
Every date in and out is a plain `datetime.date` in Gaborone terms. This module
never asks what day it is; the caller obtains "today" from
`regulatory.tax_workflow.today_gabs()` (which wraps `timezone.localdate()`),
never `date.today()`, which returns the server's UTC day and rolls over two
hours early.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

ZERO = Decimal('0.00')
TWO_PLACES = Decimal('0.01')

# How far a line's stated VAT may drift from net × rate and still pass without
# an exception. One thebe — the unavoidable residue of rounding each line
# individually, never a tolerance for a genuinely wrong figure.
LINE_ROUNDING_TOLERANCE = Decimal('0.01')

# How far the sub-ledger may drift from the GL control account and still be
# called reconciled. Zero: a VAT control account either agrees with the
# documents behind it or it does not. This is deliberately NOT a "materiality"
# threshold — a one-thebe difference is a real difference and BURS is owed an
# explanation for it, so it gets named.
TIE_OUT_TOLERANCE = ZERO


def money(value) -> Decimal:
    """Two decimals, HALF UP. Rounding is a tax decision, never a language default."""
    return Decimal(value).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

class Side:
    """Which half of the return a figure belongs to."""
    OUTPUT = 'output'
    INPUT = 'input'


@dataclass(frozen=True)
class VatSourceLine:
    """One document behind the return: a customer invoice, a credit note, a
    vendor bill, or a self-assessed reverse-charge entry.

    `net_amount` and `vat_amount` are SIGNED. A credit note carries negative
    amounts, so it reduces output VAT by arithmetic rather than by a special
    case — and the net × rate check below still holds for it unchanged.

    `rate_applies` is False for a zero-rated, exempt or out-of-scope line
    (exports, bank charges, a supplier who is not VAT-registered). Those lines
    are legitimately not net × rate and must not be flagged; they still count
    towards the totals.
    """
    reference: str
    party: str
    doc_date: date
    net_amount: Decimal
    vat_amount: Decimal
    side: str                      # Side.OUTPUT | Side.INPUT
    kind: str                      # 'customer_invoice' | 'credit_note' | ...
    rate_applies: bool = True


@dataclass(frozen=True)
class LedgerBalance:
    """The period movement on one GL VAT control account.

    `debit` and `credit` are the totals of POSTED journal lines hitting that
    account within the period — not the account's standing balance. VAT is
    reconciled on movement, because the return covers a period, not a position.
    """
    account_code: str
    account_name: str
    debit: Decimal = ZERO
    credit: Decimal = ZERO

    @property
    def output_movement(self) -> Decimal:
        """Output VAT sits on a LIABILITY account: a credit increases it."""
        return money(self.credit - self.debit)

    @property
    def input_movement(self) -> Decimal:
        """Input VAT sits on an ASSET (recoverable) account: a debit increases it."""
        return money(self.debit - self.credit)


# ---------------------------------------------------------------------------
# Exceptions — nothing is silently absorbed
# ---------------------------------------------------------------------------

class ExceptionCode:
    RATE = 'VAT-RATE-01'        # line VAT ≠ net × rate
    ORPHAN = 'VAT-ORPHAN-01'    # VAT charged on a zero net
    PERIOD = 'VAT-PERIOD-01'    # document dated outside the period
    TIE_OUTPUT = 'VAT-TIE-01'   # output sub-ledger ≠ GL control account
    TIE_INPUT = 'VAT-TIE-02'    # input sub-ledger ≠ GL control account
    NO_CONTROL = 'VAT-TIE-03'   # no GL control account supplied for a side
    # A control account this system is CONFIGURED to read is not in the chart
    # of accounts. Never skipped silently: a code that resolves to nothing
    # contributes zero movement, which looks exactly like a real zero, and the
    # tie-out would then report a difference with no way to tell why.
    MISSING_CONTROL = 'VAT-TIE-04'
    # Reverse-charge VAT is SELF-ASSESSED and has no journal in Omni, so it is
    # in the return but can never be in a control account. That is a different
    # fact from VAT-TIE-04 above — one is a configuration error, the other is
    # how a reverse charge works — and they must not share a code, or a filter
    # that acts on "missing control account" would act on both.
    RC_NO_JOURNAL = 'VAT-TIE-05'


@dataclass(frozen=True)
class VatException:
    code: str
    severity: str                  # 'error' | 'warning'
    reference: str
    message: str
    expected: Decimal | None = None
    actual: Decimal | None = None
    difference: Decimal | None = None

    def as_dict(self) -> dict:
        return {
            'code': self.code,
            'severity': self.severity,
            'reference': self.reference,
            'message': self.message,
            'expected': str(self.expected) if self.expected is not None else None,
            'actual': str(self.actual) if self.actual is not None else None,
            'difference': str(self.difference) if self.difference is not None else None,
        }


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class VatReconResult:
    period_start: date
    period_end: date
    vat_rate: Decimal

    # The documents
    output_net: Decimal
    output_vat: Decimal
    input_net: Decimal
    input_vat: Decimal
    net_vat: Decimal               # output − input; >0 payable, <0 refundable

    # The ledger
    ledger_output_vat: Decimal
    ledger_input_vat: Decimal
    ledger_net_vat: Decimal

    # The tie-out
    output_difference: Decimal     # sub-ledger − ledger
    input_difference: Decimal
    net_difference: Decimal

    due_date: date
    prepare_by_date: date

    exceptions: list[VatException] = field(default_factory=list)
    lines: list[VatSourceLine] = field(default_factory=list)

    @property
    def is_payable(self) -> bool:
        return self.net_vat > ZERO

    @property
    def is_refundable(self) -> bool:
        return self.net_vat < ZERO

    @property
    def position(self) -> str:
        if self.is_payable:
            return 'payable'
        if self.is_refundable:
            return 'refundable'
        return 'nil'

    @property
    def amount_due(self) -> Decimal:
        """What is actually paid to (or claimed from) BURS — always positive."""
        return money(abs(self.net_vat))

    @property
    def reconciled(self) -> bool:
        """True only when BOTH sides tie to the ledger and nothing is flagged
        as an error. A warning does not stop the return; an error does."""
        if abs(self.output_difference) > TIE_OUT_TOLERANCE:
            return False
        if abs(self.input_difference) > TIE_OUT_TOLERANCE:
            return False
        return not any(e.severity == 'error' for e in self.exceptions)

    def as_dict(self) -> dict:
        return {
            'period_start': str(self.period_start),
            'period_end': str(self.period_end),
            'vat_rate': str(self.vat_rate),
            'subledger': {
                'output_net': str(self.output_net),
                'output_vat': str(self.output_vat),
                'input_net': str(self.input_net),
                'input_vat': str(self.input_vat),
                'net_vat': str(self.net_vat),
            },
            'ledger': {
                'output_vat': str(self.ledger_output_vat),
                'input_vat': str(self.ledger_input_vat),
                'net_vat': str(self.ledger_net_vat),
            },
            'tie_out': {
                'output_difference': str(self.output_difference),
                'input_difference': str(self.input_difference),
                'net_difference': str(self.net_difference),
                'reconciled': self.reconciled,
            },
            'position': self.position,
            'amount_due': str(self.amount_due),
            'due_date': str(self.due_date),
            'prepare_by_date': str(self.prepare_by_date),
            'exceptions': [e.as_dict() for e in self.exceptions],
        }


# ---------------------------------------------------------------------------
# The calculation
# ---------------------------------------------------------------------------

def expected_vat(net_amount, vat_rate) -> Decimal:
    """net × rate, two decimals, HALF UP. The rate is a parameter."""
    return money(Decimal(net_amount) * Decimal(vat_rate))


def check_line(line: VatSourceLine, vat_rate: Decimal,
               period_start: date, period_end: date) -> list[VatException]:
    """Every reason one document might not belong in the return, named."""
    out: list[VatException] = []

    if not (period_start <= line.doc_date <= period_end):
        out.append(VatException(
            code=ExceptionCode.PERIOD, severity='error', reference=line.reference,
            message=(f'{line.party}: document dated {line.doc_date} falls outside '
                     f'the VAT period {period_start} to {period_end}.'),
        ))

    if line.net_amount == ZERO and line.vat_amount != ZERO:
        out.append(VatException(
            code=ExceptionCode.ORPHAN, severity='error', reference=line.reference,
            message=(f'{line.party}: VAT of {money(line.vat_amount)} charged on a '
                     f'net amount of zero.'),
            expected=ZERO, actual=money(line.vat_amount),
            difference=money(line.vat_amount),
        ))
        return out

    if line.rate_applies:
        want = expected_vat(line.net_amount, vat_rate)
        got = money(line.vat_amount)
        diff = money(got - want)
        if abs(diff) > LINE_ROUNDING_TOLERANCE:
            out.append(VatException(
                code=ExceptionCode.RATE, severity='error', reference=line.reference,
                message=(f'{line.party}: VAT of {got} does not equal net '
                         f'{money(line.net_amount)} × {vat_rate} = {want}.'),
                expected=want, actual=got, difference=diff,
            ))

    return out


def reconcile_vat(
    *,
    period_start: date,
    period_end: date,
    vat_rate: Decimal,
    due_date: date,
    prepare_by_date: date,
    lines: list[VatSourceLine],
    output_control_accounts: list[LedgerBalance] | None = None,
    input_control_accounts: list[LedgerBalance] | None = None,
) -> VatReconResult:
    """Reconcile a VAT period's documents to the general ledger.

    Args:
        period_start / period_end: the VAT period, inclusive, Gaborone dates.
        vat_rate: the standard rate as a Decimal (0.14 today). A PARAMETER —
            the caller reads it from settings; this function has no default and
            no literal, so a rate change is a settings edit, not a code change.
        due_date / prepare_by_date: from `regulatory.tax_calendar` — the 25th of
            the following month, and ten days before that.
        lines: every document behind the return, output and input, signed.
        output_control_accounts / input_control_accounts: the period movement
            on the GL VAT control accounts. Empty or omitted means the tie-out
            cannot be performed, which is itself reported — never assumed to
            agree.

    Returns:
        VatReconResult. Nothing is posted, nothing is written.
    """
    vat_rate = Decimal(vat_rate)
    exceptions: list[VatException] = []

    output_net = ZERO
    output_vat = ZERO
    input_net = ZERO
    input_vat = ZERO

    for line in lines:
        exceptions.extend(check_line(line, vat_rate, period_start, period_end))
        if line.side == Side.OUTPUT:
            output_net += Decimal(line.net_amount)
            output_vat += Decimal(line.vat_amount)
        elif line.side == Side.INPUT:
            input_net += Decimal(line.net_amount)
            input_vat += Decimal(line.vat_amount)
        else:
            exceptions.append(VatException(
                code=ExceptionCode.PERIOD, severity='error', reference=line.reference,
                message=(f'{line.party}: unknown VAT side {line.side!r} — the line '
                         f'was counted on neither the output nor the input total.'),
            ))

    output_net = money(output_net)
    output_vat = money(output_vat)
    input_net = money(input_net)
    input_vat = money(input_vat)
    net_vat = money(output_vat - input_vat)

    # ---- the ledger side -------------------------------------------------
    output_accounts = list(output_control_accounts or [])
    input_accounts = list(input_control_accounts or [])

    ledger_output_vat = money(sum((a.output_movement for a in output_accounts), ZERO))
    ledger_input_vat = money(sum((a.input_movement for a in input_accounts), ZERO))
    ledger_net_vat = money(ledger_output_vat - ledger_input_vat)

    if not output_accounts:
        exceptions.append(VatException(
            code=ExceptionCode.NO_CONTROL, severity='error', reference='output',
            message=('No GL output-VAT control account was supplied, so the output '
                     'VAT on the return could not be tied to the ledger. The figure '
                     'below is the documents only, unreconciled.'),
        ))
    if not input_accounts:
        exceptions.append(VatException(
            code=ExceptionCode.NO_CONTROL, severity='error', reference='input',
            message=('No GL input-VAT control account was supplied, so the input '
                     'VAT on the return could not be tied to the ledger. The figure '
                     'below is the documents only, unreconciled.'),
        ))

    output_difference = money(output_vat - ledger_output_vat)
    input_difference = money(input_vat - ledger_input_vat)
    net_difference = money(net_vat - ledger_net_vat)

    if output_accounts and abs(output_difference) > TIE_OUT_TOLERANCE:
        exceptions.append(VatException(
            code=ExceptionCode.TIE_OUTPUT, severity='error',
            reference=', '.join(a.account_code for a in output_accounts),
            message=(f'Output VAT per the documents is {output_vat} but the GL '
                     f'control account movement is {ledger_output_vat}. '
                     f'Difference {output_difference} — investigate and correct at '
                     f'source. This report posts nothing.'),
            expected=ledger_output_vat, actual=output_vat, difference=output_difference,
        ))

    if input_accounts and abs(input_difference) > TIE_OUT_TOLERANCE:
        exceptions.append(VatException(
            code=ExceptionCode.TIE_INPUT, severity='error',
            reference=', '.join(a.account_code for a in input_accounts),
            message=(f'Input VAT per the documents is {input_vat} but the GL '
                     f'control account movement is {ledger_input_vat}. '
                     f'Difference {input_difference} — investigate and correct at '
                     f'source. This report posts nothing.'),
            expected=ledger_input_vat, actual=input_vat, difference=input_difference,
        ))

    return VatReconResult(
        period_start=period_start,
        period_end=period_end,
        vat_rate=vat_rate,
        output_net=output_net,
        output_vat=output_vat,
        input_net=input_net,
        input_vat=input_vat,
        net_vat=net_vat,
        ledger_output_vat=ledger_output_vat,
        ledger_input_vat=ledger_input_vat,
        ledger_net_vat=ledger_net_vat,
        output_difference=output_difference,
        input_difference=input_difference,
        net_difference=net_difference,
        due_date=due_date,
        prepare_by_date=prepare_by_date,
        exceptions=exceptions,
        lines=list(lines),
    )
