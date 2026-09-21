"""
payments/batch_models.py

Pay-run / payment batch models.

A PaymentBatch is a proposal of N vendor bills to pay in one go. The
Finance Manager reviews the proposed list (deselect rows, tweak amounts),
then commits — at which point one Payment row per remaining line is
created and posted atomically. EFT file generation is a separate concern
handled later by the existing EFTBatchExportView.

Lifecycle:
  draft     - created with no proposed lines yet (rare; mostly a transient
              state during propose_pay_run)
  proposed  - lines have been generated; FM is editing
  committed - Payment rows created + confirm() called for each
  cancelled - never committed; lines discarded

Models:
  - PaymentBatch       header
  - PaymentBatchLine   one proposed payment per (invoice, amount)

Imported into payments/models.py at the bottom so the models register
under the payments app label and Django finds them in migrations.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import models

from core.models import BaseModel


ZERO = Decimal('0.00')


# ---------------------------------------------------------------------------
# PaymentBatch
# ---------------------------------------------------------------------------

class PaymentBatch(BaseModel):
    """A proposed pay-run that becomes N Payments when committed."""

    class Status(models.TextChoices):
        DRAFT     = 'draft',     'Draft'
        PROPOSED  = 'proposed',  'Proposed'
        COMMITTED = 'committed', 'Committed'
        CANCELLED = 'cancelled', 'Cancelled'

    company         = models.ForeignKey(
                          'core.Company', on_delete=models.PROTECT,
                          related_name='payment_batches',
                      )
    run_date        = models.DateField(
                          help_text='The cash-out date stamped onto every '
                                    'Payment created from this batch.',
                      )
    bank_account    = models.ForeignKey(
                          'ledger.Account', on_delete=models.PROTECT,
                          related_name='payment_batches',
                          help_text='Source bank account for every payment '
                                    'in this batch.',
                      )
    status          = models.CharField(
                          max_length=10, choices=Status.choices,
                          default=Status.DRAFT,
                      )
    eft_file_name   = models.CharField(
                          max_length=200, blank=True, default='',
                          help_text='Name of the EFT file generated from this '
                                    'batch (populated when EFT export runs).',
                      )
    total_bwp       = models.DecimalField(
                          max_digits=18, decimal_places=2, default=ZERO,
                          help_text='Sum of included PaymentBatchLine.amount_proposed.',
                      )
    notes           = models.TextField(blank=True, default='')

    created_by      = models.ForeignKey(
                          User, on_delete=models.PROTECT,
                          related_name='payment_batches_created',
                      )
    committed_at    = models.DateTimeField(null=True, blank=True)
    cancelled_at    = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        verbose_name        = 'Payment Batch'
        verbose_name_plural = 'Payment Batches'
        ordering            = ['-run_date', '-created_at']

    def __str__(self):
        return f'PayRun {self.run_date} ({self.get_status_display()}, BWP {self.total_bwp:,.2f})'

    def recalculate_total(self):
        """Re-sum included lines into total_bwp. Does NOT save."""
        agg = self.lines.filter(included=True).aggregate(
            t=models.Sum('amount_proposed'),
        )
        self.total_bwp = (agg.get('t') or ZERO)


# ---------------------------------------------------------------------------
# PaymentBatchLine
# ---------------------------------------------------------------------------

class PaymentBatchLine(BaseModel):
    """One proposed payment in a PaymentBatch — typically one vendor bill."""

    batch              = models.ForeignKey(
                             PaymentBatch, on_delete=models.CASCADE,
                             related_name='lines',
                         )
    invoice            = models.ForeignKey(
                             'billing.Invoice', on_delete=models.PROTECT,
                             related_name='pay_run_lines',
                         )
    amount_proposed    = models.DecimalField(
                             max_digits=18, decimal_places=2, default=ZERO,
                             help_text='BWP amount to pay on this bill, net of '
                                       'any early-payment discount taken.',
                         )
    amount_discount    = models.DecimalField(
                             max_digits=18, decimal_places=2, default=ZERO,
                             help_text='BWP discount captured if amount_proposed '
                                       'reflects an early-pay discount.',
                         )
    payment            = models.ForeignKey(
                             'payments.Payment', null=True, blank=True,
                             on_delete=models.SET_NULL,
                             related_name='from_pay_run_lines',
                             help_text='Populated after commit_batch creates '
                                       'the Payment for this line.',
                         )
    included           = models.BooleanField(
                             default=True,
                             help_text='Finance Manager can deselect a line '
                                       'before commit — excluded lines do '
                                       'not produce a Payment.',
                         )

    class Meta(BaseModel.Meta):
        verbose_name        = 'Payment Batch Line'
        verbose_name_plural = 'Payment Batch Lines'
        ordering            = ['created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['batch', 'invoice'],
                name='uq_payment_batch_line_per_invoice',
            ),
        ]

    def __str__(self):
        return f'{self.batch} :: {self.invoice.invoice_number} -> {self.amount_proposed}'
