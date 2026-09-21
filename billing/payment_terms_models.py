"""
billing/payment_terms_models.py

Multi-instalment + early-payment-discount payment terms.

Models:
  - PaymentTerm        Named template, e.g. "Net 30", "30/60/90".
  - PaymentTermLine    One scheduled instalment within a PaymentTerm.
                       pct + days_after_issue. Optional early-pay discount.

This file is intentionally a sibling of billing/models.py and is imported
from the bottom of billing/models.py so that:
  (a) Django sees the models on the billing app_label, and
  (b) migrations register them in billing/migrations/.

Contact.payment_term FK is also declared from billing/models.py (kept in
the main module so the migration auto-discovers it through the canonical
import path the rest of the codebase already uses).

NOTE: The existing Contact.payment_terms_days int is retained for backwards
compatibility — Invoice.save() still reads it when no PaymentTerm is set.
The new helper PaymentTerm.compute_due_dates() returns the full schedule
when a PaymentTerm IS set, so consumers (pay-run, dunning, AR aging) can
choose the appropriate path.
"""

from __future__ import annotations

import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Tuple

from django.core.exceptions import ValidationError
from django.db import models

from core.models import BaseModel


TWO_PLACES = Decimal('0.01')
ZERO       = Decimal('0.00')
HUNDRED    = Decimal('100.00')


# ---------------------------------------------------------------------------
# PaymentTerm
# ---------------------------------------------------------------------------

class PaymentTerm(BaseModel):
    """
    A reusable payment-term template.

    A term breaks the invoice total into one or more scheduled instalments
    via PaymentTermLine rows. Each line carries a percentage of the invoice
    total and a "days after issue" offset that derives its due_date.

    A line may also carry an early-payment discount: if the customer pays
    within `discount_days`, they take `discount_pct` off the line.
    """

    name        = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, default='')
    is_default  = models.BooleanField(
                      default=False,
                      help_text='True for the system-wide default term. '
                                'Exactly one row should carry this flag.',
                  )
    is_active   = models.BooleanField(default=True)

    class Meta(BaseModel.Meta):
        verbose_name        = 'Payment Term'
        verbose_name_plural = 'Payment Terms'
        ordering            = ['name']

    def __str__(self):
        return self.name

    # ------------------------------------------------------------------
    # Schedule
    # ------------------------------------------------------------------

    def clean(self):
        super().clean()
        # Defer line-sum validation until lines exist; full_clean is invoked
        # by the API serializer after lines are attached.

    def validate_lines(self):
        """
        Sum of PaymentTermLine.pct must equal 100. Called from the API
        layer when the term is being saved with its lines in one shot.
        """
        lines = list(self.lines.all())
        if not lines:
            raise ValidationError('A payment term must have at least one line.')
        total_pct = sum((ln.pct for ln in lines), ZERO)
        if abs(total_pct - HUNDRED) > Decimal('0.01'):
            raise ValidationError(
                f'PaymentTerm "{self.name}": line percentages sum to '
                f'{total_pct}, expected 100.'
            )

    def compute_due_dates(
        self,
        issue_date: datetime.date,
        total: Decimal,
    ) -> List[Tuple[datetime.date, Decimal, Decimal, datetime.date | None]]:
        """
        Return the instalment schedule for an invoice that uses this term.

        Parameters
        ----------
        issue_date : date  -- the invoice's issue date.
        total      : Decimal -- the invoice total (gross of tax).

        Returns
        -------
        list of 4-tuples, one per PaymentTermLine, in sequence order:
            (due_date, amount, discount_pct, discount_deadline)
        - amount is `total * line.pct / 100`, rounded to 2dp.
        - discount_deadline is None when the line carries no discount.

        Rounding gap handling: the LAST line absorbs any 1c rounding gap so
        the sum of `amount` equals `total` exactly.
        """
        if total is None:
            total = ZERO
        if not isinstance(total, Decimal):
            total = Decimal(str(total))

        lines = list(self.lines.order_by('sequence_no', 'created_at'))
        if not lines:
            return []

        schedule: List[Tuple[datetime.date, Decimal, Decimal, datetime.date | None]] = []
        running = ZERO
        for i, ln in enumerate(lines):
            due_date = issue_date + datetime.timedelta(days=int(ln.days_after_issue or 0))
            if i == len(lines) - 1:
                # Last line absorbs the rounding gap
                amount = (total - running).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
            else:
                amount = (total * (ln.pct or ZERO) / HUNDRED).quantize(
                    TWO_PLACES, rounding=ROUND_HALF_UP,
                )
                running += amount

            if ln.discount_pct and ln.discount_pct > ZERO and ln.discount_days:
                discount_deadline = issue_date + datetime.timedelta(
                    days=int(ln.discount_days),
                )
                discount_pct = ln.discount_pct
            else:
                discount_deadline = None
                discount_pct = ZERO

            schedule.append((due_date, amount, discount_pct, discount_deadline))
        return schedule

    @classmethod
    def get_default(cls) -> 'PaymentTerm | None':
        """Return the default PaymentTerm (or None)."""
        return cls.objects.filter(is_default=True, is_active=True).first()


# ---------------------------------------------------------------------------
# PaymentTermLine
# ---------------------------------------------------------------------------

class PaymentTermLine(BaseModel):
    """
    One scheduled instalment within a PaymentTerm.

    Example "30/60/90":
      seq=1  pct=33.33  days_after_issue=30  label='Instalment 1'
      seq=2  pct=33.33  days_after_issue=60  label='Instalment 2'
      seq=3  pct=33.34  days_after_issue=90  label='Instalment 3'

    Example "2/10 Net 30":
      seq=1  pct=100.00  days_after_issue=30  discount_pct=2  discount_days=10
    """

    term              = models.ForeignKey(
                            PaymentTerm, on_delete=models.CASCADE,
                            related_name='lines',
                        )
    sequence_no       = models.PositiveSmallIntegerField(default=1)
    pct               = models.DecimalField(
                            max_digits=7, decimal_places=4, default=Decimal('100.0000'),
                            help_text='Percentage of invoice total this line represents.',
                        )
    days_after_issue  = models.PositiveIntegerField(default=30)

    # Optional early-payment discount
    discount_pct      = models.DecimalField(
                            max_digits=7, decimal_places=4, default=ZERO,
                            help_text='Discount the payer takes if paid within '
                                      'discount_days. Zero = no discount.',
                        )
    discount_days     = models.PositiveIntegerField(
                            default=0,
                            help_text='Days after issue_date the discount expires.',
                        )

    label             = models.CharField(max_length=120, blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering            = ['term', 'sequence_no']
        verbose_name        = 'Payment Term Line'
        verbose_name_plural = 'Payment Term Lines'
        constraints = [
            models.UniqueConstraint(
                fields=['term', 'sequence_no'],
                name='uq_payment_term_line_seq',
            ),
        ]

    def __str__(self):
        return f'{self.term.name} #{self.sequence_no} ({self.pct}% / {self.days_after_issue}d)'

    def clean(self):
        super().clean()
        if self.pct is None or self.pct <= ZERO or self.pct > HUNDRED:
            raise ValidationError({'pct': 'pct must be > 0 and <= 100.'})
        if self.discount_pct and self.discount_pct < ZERO:
            raise ValidationError({'discount_pct': 'discount_pct must be >= 0.'})
        if self.discount_pct and self.discount_pct >= HUNDRED:
            raise ValidationError({'discount_pct': 'discount_pct must be < 100.'})
        if self.discount_pct and not self.discount_days:
            raise ValidationError({
                'discount_days': 'discount_days is required when discount_pct > 0.',
            })
        if self.discount_days and self.discount_days > self.days_after_issue:
            raise ValidationError({
                'discount_days': (
                    'discount_days cannot be later than days_after_issue '
                    '(a discount that expires after the due date is meaningless).'
                ),
            })
