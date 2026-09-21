"""FX Payment Planning — data models.

Alpha Direct makes many USD and ZAR foreign payments each month, captured
directly on the FNB Online Banking site (not through omni). Because nobody
plans ahead they pile up haphazardly (e.g. USD 121k + ZAR 670k in a single
day, 29-30 Apr 2026). This module gives a FORWARD view: a driver-based
forecast of how much USD and ZAR will be needed and when, and a planning
calendar so the team schedules and pre-funds forex.

Phase 1 (this build) covers the CLOCKWORK driver only: recurring payees learned
from the FNB "Forex" history download. Reinsurance-settlement and claims
drivers are Phase 2.

Design notes
------------
* Inherits ``BaseModel`` (UUID pk, created_at/updated_at). Amounts are Decimal
  throughout — never float.
* This module does NOT move money and does NOT post to the GL. It observes,
  forecasts and plans. Execution stays in ``taskboard.PaymentRequest`` → the
  FNB flow; a planned line can be "raised" into a PaymentRequest, and links
  back to it, but the money egress is unchanged.
* The pula estimate reuses ``payments.resolve_fx_rate`` (approved BoB/manual
  rate, control FX-001). When no approved rate exists it falls back to the
  latest rate and flags the line ``rate_is_estimate`` — a soft planning figure,
  never posted anywhere.
"""

from decimal import Decimal

from django.conf import settings
from django.db import models

from core.models import BaseModel


class ForexPaymentImport(BaseModel):
    """One uploaded FNB 'Forex' history download (PDF / CSV / Excel)."""

    filename       = models.CharField(max_length=255)
    uploaded_by    = models.ForeignKey(
                         settings.AUTH_USER_MODEL, null=True, blank=True,
                         on_delete=models.SET_NULL, related_name='fx_imports',
                     )
    row_count      = models.PositiveIntegerField(default=0)      # rows found in the file
    imported_count = models.PositiveIntegerField(default=0)      # new rows actually stored
    date_from      = models.DateField(null=True, blank=True)
    date_to        = models.DateField(null=True, blank=True)
    notes          = models.TextField(blank=True, default='')

    def __str__(self):
        return f"FX import {self.filename} ({self.imported_count}/{self.row_count})"


class ForexPaymentHistory(BaseModel):
    """One historical foreign payment parsed from an FNB forex download.

    This is the driver data the recurring detector learns from. De-duplicated on
    the FNB reference so re-uploading an overlapping window is safe.
    """

    reference       = models.CharField(max_length=40, unique=True)
    beneficiary     = models.CharField(max_length=255)
    beneficiary_key = models.CharField(max_length=255, db_index=True)  # normalised
    payment_type    = models.CharField(max_length=40, blank=True, default='')
    capture_date    = models.DateField(null=True, blank=True)
    value_date      = models.DateField(null=True, blank=True, db_index=True)
    source_account  = models.CharField(max_length=40, blank=True, default='')
    currency        = models.CharField(max_length=3, db_index=True)
    amount          = models.DecimalField(max_digits=18, decimal_places=2)
    status          = models.CharField(max_length=30, blank=True, default='')
    source_import   = models.ForeignKey(
                         ForexPaymentImport, null=True, blank=True,
                         on_delete=models.SET_NULL, related_name='rows',
                     )

    class Meta(BaseModel.Meta):
        verbose_name        = 'Forex payment (history)'
        verbose_name_plural = 'Forex payment history'
        indexes             = [
            models.Index(fields=['beneficiary_key', 'currency']),
        ]

    def __str__(self):
        return f"{self.reference} {self.currency} {self.amount} → {self.beneficiary}"


class RecurringForexPayee(BaseModel):
    """A payee the detector believes recurs on a cadence.

    One row per (beneficiary, currency). Finance can turn a row off (so it drops
    out of the forward calendar) or tune the typical amount / day.
    """

    class Cadence(models.TextChoices):
        MONTHLY   = 'monthly',   'Monthly'
        QUARTERLY = 'quarterly', 'Quarterly'
        IRREGULAR = 'irregular', 'Irregular'

    beneficiary_key = models.CharField(max_length=255, db_index=True)
    display_name    = models.CharField(max_length=255)
    currency        = models.CharField(max_length=3)
    typical_amount  = models.DecimalField(max_digits=18, decimal_places=2, default=Decimal('0'))
    amount_min      = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    amount_max      = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    cadence         = models.CharField(max_length=12, choices=Cadence.choices,
                                       default=Cadence.MONTHLY)
    typical_day     = models.PositiveSmallIntegerField(null=True, blank=True)  # day-of-month
    source_account  = models.CharField(max_length=40, blank=True, default='')
    occurrences     = models.PositiveIntegerField(default=0)   # total times seen
    months_active   = models.PositiveIntegerField(default=0)   # distinct calendar months seen
    confidence      = models.PositiveSmallIntegerField(default=0)  # 0-100, drives the forecast label
    last_seen       = models.DateField(null=True, blank=True)
    active          = models.BooleanField(default=True)        # include in the forward calendar
    watch           = models.BooleanField(default=False)       # material but not clockwork → plan by hand
    confirmed_by    = models.ForeignKey(
                         settings.AUTH_USER_MODEL, null=True, blank=True,
                         on_delete=models.SET_NULL, related_name='+',
                     )
    notes           = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        verbose_name        = 'Recurring forex payee'
        verbose_name_plural = 'Recurring forex payees'
        constraints         = [
            models.UniqueConstraint(
                fields=['beneficiary_key', 'currency'],
                name='uniq_recurring_payee_ccy',
            ),
        ]

    def __str__(self):
        return f"{self.display_name} ({self.currency}, {self.cadence})"


class PlannedForexPayment(BaseModel):
    """A forward planned foreign payment on the planning calendar."""

    class Driver(models.TextChoices):
        RECURRING   = 'recurring',   'Recurring'
        REINSURANCE = 'reinsurance', 'Reinsurance'   # Phase 2
        CLAIM       = 'claim',       'Claim'          # Phase 2
        MANUAL      = 'manual',      'Manual'

    class Status(models.TextChoices):
        PLANNED   = 'planned',   'Planned'
        REQUESTED = 'requested', 'Payment requested'
        PAID      = 'paid',      'Paid'
        SKIPPED   = 'skipped',   'Skipped'

    beneficiary         = models.CharField(max_length=255)
    beneficiary_key     = models.CharField(max_length=255, db_index=True, blank=True, default='')
    currency            = models.CharField(max_length=3)
    expected_amount     = models.DecimalField(max_digits=18, decimal_places=2)
    expected_value_date = models.DateField(db_index=True)
    source_account      = models.CharField(max_length=40, blank=True, default='')
    driver              = models.CharField(max_length=12, choices=Driver.choices,
                                           default=Driver.MANUAL)
    recurring_payee     = models.ForeignKey(
                             RecurringForexPayee, null=True, blank=True,
                             on_delete=models.SET_NULL, related_name='planned',
                         )
    status              = models.CharField(max_length=12, choices=Status.choices,
                                           default=Status.PLANNED)
    # Pula estimate captured at materialisation time (kept for stability/history).
    estimated_rate      = models.DecimalField(max_digits=18, decimal_places=8,
                                              null=True, blank=True)
    estimated_bwp       = models.DecimalField(max_digits=18, decimal_places=2,
                                              null=True, blank=True)
    rate_is_estimate    = models.BooleanField(default=True)  # True = fallback/unapproved rate
    payment_request     = models.ForeignKey(
                             'taskboard.PaymentRequest', null=True, blank=True,
                             on_delete=models.SET_NULL, related_name='fx_plan_lines',
                         )
    created_by          = models.ForeignKey(
                             settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.SET_NULL, related_name='+',
                         )
    notes               = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        verbose_name        = 'Planned forex payment'
        verbose_name_plural = 'Planned forex payments'
        ordering            = ['expected_value_date', 'currency']
        constraints         = [
            # A recurring payee auto-generates at most one line per value date, so
            # re-running the materialiser never duplicates the calendar.
            models.UniqueConstraint(
                fields=['recurring_payee', 'expected_value_date'],
                condition=models.Q(driver='recurring'),
                name='uniq_recurring_plan_per_date',
            ),
        ]

    def __str__(self):
        return (f"{self.expected_value_date} {self.currency} "
                f"{self.expected_amount} → {self.beneficiary}")
