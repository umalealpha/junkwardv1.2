"""
payments/batch_service.py

Pay-run service layer.

Two public entry points:
  - propose_pay_run(company, run_date, bank_account, due_through_date)
        Scan open vendor bills due on/before due_through_date, compute any
        early-pay discount available via the bill's contact PaymentTerm,
        and create a PaymentBatch (status=proposed) with one
        PaymentBatchLine per bill.

  - commit_batch(batch, user)
        For each included batch line, create a Payment + PaymentAllocation,
        then call Payment.confirm(user). Everything runs in one atomic
        transaction so a partial pay-run is impossible.
"""

from __future__ import annotations

import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from billing.models import Invoice
from .models import (
    Payment,
    PaymentAllocation,
    PaymentBatch,
    PaymentBatchLine,
)


TWO_PLACES = Decimal('0.01')
ZERO       = Decimal('0.00')
HUNDRED    = Decimal('100.00')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _outstanding_for(bill: Invoice) -> Decimal:
    """Bill total minus allocated payments so far. Always >= 0."""
    total = bill.total_amount or ZERO
    paid  = bill.amount_paid or ZERO
    diff  = total - paid
    return diff if diff > ZERO else ZERO


def _discount_for_bill(bill: Invoice, run_date: datetime.date) -> Decimal:
    """
    BWP discount captured on this bill if its contact carries a PaymentTerm
    whose discount window includes *run_date*. Zero otherwise.

    Note: the discount is taken against the bill's OUTSTANDING balance,
    not the original total — partial payments already drawn down do not
    re-earn a discount.
    """
    term = getattr(bill.contact, 'payment_term', None)
    if term is None or not term.is_active:
        return ZERO
    outstanding = _outstanding_for(bill)
    if outstanding <= ZERO:
        return ZERO

    # Pick the line whose discount window contains run_date.
    # First-match wins; for a single-instalment "2/10 Net 30" there is only one.
    for ln in term.lines.order_by('sequence_no', 'created_at'):
        if not ln.discount_pct or not ln.discount_days:
            continue
        deadline = bill.issue_date + datetime.timedelta(days=int(ln.discount_days))
        if run_date <= deadline:
            return (outstanding * ln.discount_pct / HUNDRED).quantize(
                TWO_PLACES, rounding=ROUND_HALF_UP,
            )
    return ZERO


# ---------------------------------------------------------------------------
# propose_pay_run
# ---------------------------------------------------------------------------

@transaction.atomic
def propose_pay_run(
    company,
    run_date: datetime.date,
    bank_account,
    due_through_date: datetime.date,
    user=None,
) -> PaymentBatch:
    """
    Build a draft PaymentBatch for *company* covering every open vendor bill
    whose due_date is on or before *due_through_date*.

    For each candidate bill:
      - amount_proposed = outstanding - discount (if any)
      - amount_discount = the discount captured (zero if none applicable)

    The returned PaymentBatch is in status=PROPOSED. Lines can be edited
    (.included flag flipped, amounts tweaked via the API) before commit.
    """
    if run_date is None:
        run_date = timezone.localdate()
    if due_through_date is None:
        due_through_date = run_date

    # Pull open vendor bills for this company
    bills_qs = (
        Invoice.objects
        .select_related('contact', 'contact__payment_term')
        .filter(
            company=company,
            invoice_type=Invoice.InvoiceType.VENDOR_BILL,
            status__in=[
                Invoice.Status.POSTED,
                Invoice.Status.PARTIALLY_PAID,
                Invoice.Status.OVERDUE,
            ],
            due_date__lte=due_through_date,
        )
        .order_by('due_date', 'invoice_number')
    )

    batch = PaymentBatch.objects.create(
        company       = company,
        run_date      = run_date,
        bank_account  = bank_account,
        status        = PaymentBatch.Status.DRAFT,
        created_by    = user,
    )

    total = ZERO
    for bill in bills_qs:
        outstanding = _outstanding_for(bill)
        if outstanding <= ZERO:
            continue
        discount     = _discount_for_bill(bill, run_date)
        amount_pay   = (outstanding - discount).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        if amount_pay <= ZERO:
            continue
        PaymentBatchLine.objects.create(
            batch           = batch,
            invoice         = bill,
            amount_proposed = amount_pay,
            amount_discount = discount,
            included        = True,
        )
        total += amount_pay

    batch.status    = PaymentBatch.Status.PROPOSED
    batch.total_bwp = total
    batch.save()
    return batch


# ---------------------------------------------------------------------------
# commit_batch
# ---------------------------------------------------------------------------

def _payment_method_for(bill: Invoice) -> str:
    """Default to BANK_TRANSFER — overridable on the Payment after creation."""
    return Payment.PaymentMethod.BANK_TRANSFER


@transaction.atomic
def commit_batch(batch: PaymentBatch, user) -> PaymentBatch:
    """
    Materialise every included PaymentBatchLine as a Payment + allocation,
    then post each. Atomic — any failure rolls back the whole batch.
    """
    if batch.status != PaymentBatch.Status.PROPOSED:
        raise ValidationError(
            f'Batch is {batch.get_status_display()}, not proposed. '
            'Only PROPOSED batches can be committed.'
        )

    lines = list(
        batch.lines.select_related('invoice', 'invoice__contact')
        .filter(included=True)
    )
    if not lines:
        raise ValidationError('No included lines on this batch — nothing to commit.')

    for line in lines:
        bill = line.invoice
        amount = line.amount_proposed
        if amount <= ZERO:
            continue
        # Re-check outstanding at commit time to avoid double-paying a bill
        # that got partially settled between propose + commit.
        outstanding = _outstanding_for(bill)
        if outstanding <= ZERO:
            continue
        if amount > outstanding + line.amount_discount:
            # The proposed amount + the discount should not exceed the
            # current outstanding (the discount has not been applied yet).
            amount = outstanding - line.amount_discount
            if amount <= ZERO:
                continue

        payment = Payment(
            payment_type   = Payment.PaymentType.SENT,
            contact        = bill.contact,
            company        = batch.company,
            bank_account   = batch.bank_account,
            payment_date   = batch.run_date,
            currency_code  = bill.currency_code,
            exchange_rate  = bill.exchange_rate,
            amount         = amount,
            payment_method = _payment_method_for(bill),
            reference      = f'PAYRUN {batch.run_date:%Y%m%d} {bill.invoice_number}'[:200],
            description    = (
                f'Pay-run {batch.run_date} — {bill.invoice_number} '
                f'(discount {line.amount_discount})'
            )[:1000],
            created_by     = user,
        )
        payment.save(audit_user=user, audit_description=f'Created from batch {batch.pk}')

        PaymentAllocation.objects.create(
            payment          = payment,
            invoice          = bill,
            amount_allocated = amount,
        )

        # Stamp the discount onto the payment so Payment.confirm() can pick
        # it up and post the "Discount received" credit line in the JE.
        # Stored as a transient attribute (not a model field) — confirm()
        # reads it before building the JE.
        if line.amount_discount and line.amount_discount > ZERO:
            payment._early_pay_discount_bwp = line.amount_discount

        # Post the GL. _system: the pay-run batch carries its own controls
        # (batch approval), so it is exempt from the interactive dual-control
        # payment quorum (see Payment.confirm).
        payment.confirm(user=user, _system=True)

        line.payment = payment
        line.save()

    batch.status       = PaymentBatch.Status.COMMITTED
    batch.committed_at = timezone.now()
    batch.save()
    return batch
