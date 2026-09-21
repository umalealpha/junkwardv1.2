"""
regulatory/vat_recon_service.py — the database half of the VAT reconciliation.

`regulatory/vat_recon.py` owns the arithmetic and touches nothing. This module
is the only place that reads: it gathers the period's documents and the GL VAT
control-account movement, hands them to the engine, and returns the result.

🔴 READ-ONLY, DELIBERATELY.
Every query here is a SELECT. Nothing in this file creates, updates or deletes a
row, and nothing posts a journal entry. A difference between the documents and
the ledger comes back as an exception for a person to investigate — it is never
plugged. Build spec B2 hard stop: "NO journal posting and NO GL mapping change
without the CFO signing off first."

WHERE THE FIGURES COME FROM
---------------------------
    output VAT   billing.Invoice, invoice_type='customer_invoice'  (+ve)
                 billing.Invoice, invoice_type='credit_note'       (−ve)
                 billing.ReverseChargeEntry.output_vat             (+ve)
    input  VAT   billing.Invoice, invoice_type='vendor_bill'       (+ve)
                 billing.ReverseChargeEntry.input_vat_recoverable  (+ve)
    the ledger   ledger.JournalEntryLine on the VAT control accounts named by
                 settings.VAT_OUTPUT_CONTROL_ACCOUNTS / _INPUT_, POSTED entries
                 only, entry_date inside the period.

WHY NOT JUST CALL reporting.reports.build_vat_return()
------------------------------------------------------
It answers a different question. `build_vat_return` PREPARES the return — it
totals the same documents and hands BURS its lines. This module RECONCILES it:
it needs each document signed (so a credit note reduces output VAT by
arithmetic), it needs `input_recovery_overridden` off the reverse-charge row to
know which lines may legitimately fail a net × rate check, and it needs the GL
control-account movement, none of which `build_vat_return`'s already-rounded
string output carries. Reading the two models directly is cheaper and less
brittle than reverse-engineering a report's dict shape.

The two do NOT read identical rows today, and that is deliberate rather than
assumed: `reporting/reports.py` filters credit notes on ('posted',
'partially_paid', 'paid') where this module's COUNTED_STATUSES also admits
'overdue', and it skips any row failing `inv_vat > 0 or inv_net > 0` — which
drops a wholly negative row, where this module drops only rows that are zero on
both. So the two can legitimately differ on those edges; they are not expected
to agree line-for-line, and neither should be "fixed" to match the other
without deciding which filter is right for BURS.

Reverse-charge entries are included on BOTH sides because that is what a
reverse charge is: the same VAT self-assessed as output and reclaimed as input.
`reporting.reports.build_vat_return` files them as two separate BURS lines for
the same reason. At full recovery they net to zero, and the reconciliation shows
that rather than hiding it.

THE RATE
--------
`settings.RC_VAT_RATE` — the one VAT rate constant this repo has, already used
by `billing/reverse_charge_models.py`. A second one is not introduced here.

DATES
-----
`regulatory.tax_workflow.today_gabs()` for "today"; `timezone.localdate()`
underneath. Never `date.today()`.
"""
from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.db.models import Sum

from .tax_calendar import vat_due_date, vat_prepare_by_date
from .vat_recon import (
    ExceptionCode,
    LedgerBalance,
    Side,
    VatException,
    VatReconResult,
    VatSourceLine,
    ZERO,
    money,
    reconcile_vat,
)

# Invoice statuses that represent a document that has actually happened. A
# draft invoice is not yet a supply and must not appear on a VAT return.
COUNTED_STATUSES = ('posted', 'partially_paid', 'paid', 'overdue')


def month_period(year: int, month: int) -> tuple[date, date]:
    """First and last day of a calendar month, as Gaborone dates."""
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def _invoice_lines(invoice_type: str, side: str, sign: int,
                   period_start: date, period_end: date,
                   company_id=None) -> list[VatSourceLine]:
    from billing.models import Invoice

    qs = Invoice.objects.filter(
        invoice_type=invoice_type,
        status__in=COUNTED_STATUSES,
        issue_date__gte=period_start,
        issue_date__lte=period_end,
    ).select_related('contact')
    if company_id:
        qs = qs.filter(contact__company_id=company_id)

    lines: list[VatSourceLine] = []
    for inv in qs.order_by('issue_date', 'invoice_number'):
        net = (inv.subtotal or ZERO) * sign
        vat = (inv.tax_total or ZERO) * sign
        if net == ZERO and vat == ZERO:
            continue
        lines.append(VatSourceLine(
            reference=inv.invoice_number,
            party=inv.contact.name if inv.contact_id else '(no contact)',
            doc_date=inv.issue_date,
            net_amount=net,
            vat_amount=vat,
            side=side,
            kind=invoice_type,
            # A line whose VAT is zero on a non-zero net is zero-rated, exempt
            # or from a supplier who is not VAT-registered — all legitimate, and
            # all of them would fail a net × rate check. They still count
            # towards the totals; they are simply not rate-checked.
            rate_applies=vat != ZERO,
        ))
    return lines


def _reverse_charge_lines(period_start: date, period_end: date,
                          company_id=None) -> list[VatSourceLine]:
    from billing.models import ReverseChargeEntry

    qs = ReverseChargeEntry.objects.filter(
        reverse_charge_applies=True,
        invoice_date__gte=period_start,
        invoice_date__lte=period_end,
    )
    if company_id:
        qs = qs.filter(company_id=company_id)

    lines: list[VatSourceLine] = []
    for entry in qs.order_by('invoice_date', 'vendor'):
        reference = f'RC/{entry.invoice_date}/{entry.vendor}'[:120]
        lines.append(VatSourceLine(
            reference=reference,
            party=entry.vendor,
            doc_date=entry.invoice_date,
            net_amount=entry.bwp_amount or ZERO,
            vat_amount=entry.output_vat or ZERO,
            side=Side.OUTPUT,
            kind='reverse_charge_output',
            rate_applies=True,
        ))
        lines.append(VatSourceLine(
            reference=reference,
            party=entry.vendor,
            doc_date=entry.invoice_date,
            net_amount=entry.bwp_amount or ZERO,
            vat_amount=entry.input_vat_recoverable or ZERO,
            side=Side.INPUT,
            kind='reverse_charge_input',
            # A partial recovery is a deliberate decision recorded on the row,
            # not a mistake — so it must not be flagged as "VAT ≠ net × rate".
            rate_applies=not entry.input_recovery_overridden,
        ))
    return lines


def _control_balances(account_codes, period_start: date, period_end: date,
                      company_id=None) -> tuple[list[LedgerBalance], list[str]]:
    """Period MOVEMENT on each VAT control account, POSTED entries only.

    Movement, not balance: the return covers a period, so what must tie is what
    went through the account in that period, not where it ended up.

    Returns the balances AND any configured code that is not in the chart of
    accounts. A code that resolves to nothing used to be skipped silently, and
    a silently-absent account is indistinguishable from an account with no
    movement: the tie-out would report a difference and give no clue that the
    cause was configuration rather than the books. The caller turns the missing
    list into a VAT-TIE-04 warning.
    """
    from ledger.models import Account, JournalEntry, JournalEntryLine

    balances: list[LedgerBalance] = []
    missing: list[str] = []
    for code in account_codes:
        account = Account.objects.filter(code=code).first()
        if account is None:
            missing.append(code)
            continue
        qs = JournalEntryLine.objects.filter(
            account=account,
            journal_entry__status=JournalEntry.Status.POSTED,
            journal_entry__entry_date__gte=period_start,
            journal_entry__entry_date__lte=period_end,
        )
        if company_id:
            qs = qs.filter(journal_entry__company_id=company_id)
        totals = qs.aggregate(debit=Sum('debit_bwp'), credit=Sum('credit_bwp'))
        balances.append(LedgerBalance(
            account_code=account.code,
            account_name=account.name,
            debit=totals['debit'] or ZERO,
            credit=totals['credit'] or ZERO,
        ))
    return balances, missing


def build_vat_reconciliation(
    period_start: date,
    period_end: date,
    company_id=None,
    vat_rate: Decimal | None = None,
) -> VatReconResult:
    """Reconcile a VAT period. Reads only; posts nothing.

    Args:
        period_start / period_end: inclusive, Gaborone dates.
        company_id: optional entity scope.
        vat_rate: override for testing. Defaults to `settings.RC_VAT_RATE` —
            the rate is a PARAMETER read from settings, never a literal here.
    """
    rate = Decimal(vat_rate) if vat_rate is not None else Decimal(settings.RC_VAT_RATE)

    lines: list[VatSourceLine] = []
    lines += _invoice_lines('customer_invoice', Side.OUTPUT, 1,
                            period_start, period_end, company_id)
    # A credit note reverses a sale, so it reduces output VAT. Signed, not
    # special-cased — the net × rate check then holds for it unchanged.
    lines += _invoice_lines('credit_note', Side.OUTPUT, -1,
                            period_start, period_end, company_id)
    lines += _invoice_lines('vendor_bill', Side.INPUT, 1,
                            period_start, period_end, company_id)
    rc_lines = _reverse_charge_lines(period_start, period_end, company_id)
    lines += rc_lines

    output_balances, output_missing = _control_balances(
        settings.VAT_OUTPUT_CONTROL_ACCOUNTS, period_start, period_end, company_id)
    input_balances, input_missing = _control_balances(
        settings.VAT_INPUT_CONTROL_ACCOUNTS, period_start, period_end, company_id)

    result = reconcile_vat(
        period_start=period_start,
        period_end=period_end,
        vat_rate=rate,
        due_date=vat_due_date(period_end),
        prepare_by_date=vat_prepare_by_date(period_end),
        lines=lines,
        output_control_accounts=output_balances,
        input_control_accounts=input_balances,
    )

    # ── Configured control accounts that are not in the chart ────────────────
    # A warning, not an error: the figures above are still the best available
    # answer. But it must be SAID, because a code that resolves to nothing
    # contributes zero and so looks identical to a genuine nil movement.
    for side, codes in (('output', output_missing), ('input', input_missing)):
        for code in codes:
            result.exceptions.append(VatException(
                code=ExceptionCode.MISSING_CONTROL, severity='warning',
                reference=code,
                message=(f'The {side} VAT control account {code} is configured '
                         f'but is not in the chart of accounts, so it '
                         f'contributed no movement to this tie-out. Correct '
                         f'the configured account codes — the difference below '
                         f'may be configuration, not the books.'),
            ))

    # ── Reverse charge is self-assessed and has no journal in Omni ───────────
    # The RC legs are counted in the return (BURS is owed them), but nothing
    # posts them to a control account, so they cannot tie. Without this the
    # report shows a tie-out difference of exactly the RC amount and tells the
    # preparer to "correct at source" — when there is nothing at source to
    # correct. Name the gap instead of letting it read as a books error.
    #
    # Its OWN code (VAT-TIE-05), not VAT-TIE-04: a reverse charge having no
    # journal is normal and permanent, a configured account missing from the
    # chart is a mistake someone must fix. Sharing one code would have let a
    # filter for one act on the other.
    if rc_lines:
        for side in (Side.OUTPUT, Side.INPUT):
            rc_vat = money(sum(
                (Decimal(ln.vat_amount) for ln in rc_lines if ln.side == side),
                ZERO))
            if rc_vat == ZERO:
                continue
            result.exceptions.append(VatException(
                code=ExceptionCode.RC_NO_JOURNAL, severity='warning',
                reference=f'reverse-charge-{side}',
                actual=rc_vat,
                message=(f'Reverse-charge {side} VAT of {rc_vat} is self-'
                         f'assessed and has no journal in Omni, so it is in '
                         f'the return but not in the control account. Expect '
                         f'the {side} tie-out to differ by this amount.'),
            ))

    return result
