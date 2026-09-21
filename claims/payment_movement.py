"""
claims/payment_movement.py — B6 Claims Payment Movement Report, the arithmetic.

Requested by Bontle Tendani. Every payment made in respect of claims, analysed
by TYPE of payment and by whether it was settled against a SUPPLIER INVOICE or
paid to an INDIVIDUAL CLAIMANT.

IT IS NOT A BANK RECONCILIATION. The spec says so in as many words, and it
matters: the accounting reconciliation of the bank stays in Odoo and is not
affected by anything here. This module reports on payments that have already
happened. It moves nothing, releases nothing and posts nothing to the GL.

Pure Python. No ORM, no Django, no I/O — the same rule as
`claims/reconciliation/engine.py`, which this follows, so the worked examples
in the tests run against the arithmetic itself rather than against a database.
The caller (claims/payment_movement_views.py) does the querying.

THE VAT RULE, WHICH IS THE WHOLE POINT OF THE REPORT
----------------------------------------------------
    "De-gross VAT ONLY where the payment basis is INVOICE.
     Do NOT de-gross AOL, FOR or CIL payments."

A 12-row sample in the source document had 3,984.39 of 37,061.52 — about 11% —
being wrongly de-grossed. That is the mistake this report exists to stop
making, so the rule is enforced in exactly one place, `_vat_split()` below, and
the set of bases that may be de-grossed lives in
`banking.line_references.PaymentBasis.DEGROSSED` rather than in an `if` here.

A basis of UNKNOWN is NOT de-grossed. The error being corrected was
over-de-grossing, so every direction of doubt resolves to leaving the payment
gross.

De-grossing itself reuses `claims.reconciliation.engine.degross_vat`, which
already rounds HALF UP — the Botswana rule. Rounding is a tax decision, never a
language default, and Python's built-in round() does banker's rounding, which
is wrong for this. The rate is a PARAMETER, supplied by the caller from
`settings.RC_VAT_RATE`; it is never hard-coded here.

THE SPLIT
---------
A line that carries a supplier invoice number was settled against a supplier
invoice. A line that does not was paid to an individual claimant. That is the
whole test, it comes off CR-001's `invoice_reference` field, and it is asked
once, in `LineReferences.settled_against_invoice`.

PERSONAL DATA
-------------
Rows carry payee names against claim amounts. The gate is on the view
(`permission_classes`), not on this module and not on a hidden button — see
claims/payment_movement_views.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from banking.line_references import PaymentBasis
from claims.reconciliation.engine import degross_vat, money

ZERO = Decimal('0.00')

SUPPLIER_INVOICE = 'supplier_invoice'
INDIVIDUAL_CLAIMANT = 'individual_claimant'

SETTLEMENT_LABELS = {
    SUPPLIER_INVOICE: 'Settled against supplier invoice',
    INDIVIDUAL_CLAIMANT: 'Paid to individual claimant',
}


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ClaimPaymentRow:
    """One claims payment, as it left the bank.

    `amount_gross` is the POSITIVE magnitude of the payment. Bank statement
    lines store an outflow as a negative amount (banking/models.py); converting
    that to a magnitude is the caller's job, so this module never has to guess
    which sign convention it has been handed.
    """

    transaction_date: object          # datetime.date
    payee: str
    claim_reference: str
    invoice_reference: str
    payment_basis: str
    amount_gross: Decimal
    statement_number: str = ''
    description: str = ''

    @property
    def settlement(self) -> str:
        """Supplier invoice, or an individual claimant. The B6 split."""
        return SUPPLIER_INVOICE if self.invoice_reference else INDIVIDUAL_CLAIMANT


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

@dataclass
class AnalysedRow:
    """One payment, with the VAT question answered."""

    source: ClaimPaymentRow
    amount_gross: Decimal
    amount_excl_vat: Decimal
    vat_amount: Decimal
    was_degrossed: bool

    @property
    def basis_label(self) -> str:
        return PaymentBasis.LABELS.get(self.source.payment_basis,
                                       self.source.payment_basis)


@dataclass
class Bucket:
    """A subtotal — one payment type, or one side of the split."""

    key: str
    label: str
    count: int = 0
    amount_gross: Decimal = ZERO
    amount_excl_vat: Decimal = ZERO
    vat_amount: Decimal = ZERO

    def add(self, row: AnalysedRow) -> None:
        self.count += 1
        self.amount_gross = money(self.amount_gross + row.amount_gross)
        self.amount_excl_vat = money(self.amount_excl_vat + row.amount_excl_vat)
        self.vat_amount = money(self.vat_amount + row.vat_amount)


@dataclass
class PaymentMovementReport:
    vat_rate: Decimal
    rows: list = field(default_factory=list)
    by_type: list = field(default_factory=list)
    by_settlement: list = field(default_factory=list)
    total: Bucket = None
    degrossed_count: int = 0
    left_gross_count: int = 0


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------

def _vat_split(amount_gross: Decimal, payment_basis: str,
               vat_rate: Decimal) -> tuple:
    """(excl_vat, vat, was_degrossed) for one payment.

    THE one place the B6 VAT rule is applied. Only a basis in
    PaymentBasis.DEGROSSED — which today is INVOICE and only INVOICE — is
    de-grossed. Every other basis, UNKNOWN included, is returned exactly as
    it came, with zero VAT, because no VAT was charged on it to strip out.
    """
    gross = money(amount_gross)
    if payment_basis in PaymentBasis.DEGROSSED:
        excl, vat = degross_vat(gross, vat_rate)
        return excl, vat, True
    return gross, ZERO, False


def build_payment_movement(payments, vat_rate: Decimal) -> PaymentMovementReport:
    """Analyse a period's claims payments by type and by settlement basis.

    `vat_rate` is a fraction (Decimal('0.14') for Botswana's 14% today) and is
    always supplied by the caller from settings.RC_VAT_RATE. Never defaulted
    here: a report that invents its own tax rate is worse than one that fails.
    """
    if vat_rate is None:
        raise ValueError('vat_rate is required — pass settings.RC_VAT_RATE.')
    rate = Decimal(str(vat_rate))

    report = PaymentMovementReport(vat_rate=rate)
    type_buckets: dict = {}
    settle_buckets: dict = {}
    total = Bucket(key='total', label='All claims payments')

    for src in payments:
        excl, vat, degrossed = _vat_split(src.amount_gross, src.payment_basis, rate)
        row = AnalysedRow(
            source=src,
            amount_gross=money(src.amount_gross),
            amount_excl_vat=excl,
            vat_amount=vat,
            was_degrossed=degrossed,
        )
        report.rows.append(row)
        if degrossed:
            report.degrossed_count += 1
        else:
            report.left_gross_count += 1

        basis = src.payment_basis
        bucket = type_buckets.get(basis)
        if bucket is None:
            bucket = type_buckets[basis] = Bucket(
                key=basis, label=PaymentBasis.LABELS.get(basis, basis))
        bucket.add(row)

        settlement = src.settlement
        sb = settle_buckets.get(settlement)
        if sb is None:
            sb = settle_buckets[settlement] = Bucket(
                key=settlement, label=SETTLEMENT_LABELS[settlement])
        sb.add(row)

        total.add(row)

    # Fixed, readable order — never dictionary insertion order, so two periods
    # produce comparable reports.
    type_order = [PaymentBasis.INVOICE, PaymentBasis.REPAIR, PaymentBasis.AOL,
                  PaymentBasis.FOR, PaymentBasis.CIL, PaymentBasis.THIRD_PARTY,
                  PaymentBasis.EX_GRATIA, PaymentBasis.UNKNOWN]
    report.by_type = [type_buckets[k] for k in type_order if k in type_buckets]
    report.by_type += [b for k, b in sorted(type_buckets.items())
                       if k not in type_order]
    report.by_settlement = [settle_buckets[k]
                            for k in (SUPPLIER_INVOICE, INDIVIDUAL_CLAIMANT)
                            if k in settle_buckets]
    report.total = total
    return report
