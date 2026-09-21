"""
fx/models.py

Month-end foreign-currency revaluation.

For each balance-sheet account that holds foreign-currency balances (e.g. USD
and ZAR bank accounts, USD-denominated payables), the book BWP balance is
restated at the period-end exchange rate. The difference is posted as an
unrealised FX gain or loss to account 6950.

Models:
  - FXRevaluation       header — one per period per company
  - FXRevaluationLine   one per (account, currency) combination revalued
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import User
from django.db import models

from core.models import AuditableMixin, BaseModel, Currency


ZERO = Decimal('0.00')


class FXRevaluation(AuditableMixin, BaseModel):
    """
    A period-end FX revaluation run. Posting creates one journal entry that
    bumps every foreign-currency balance to its BWP value at the closing rate.
    """

    class Status(models.TextChoices):
        DRAFT     = 'draft',     'Draft'
        POSTED    = 'posted',    'Posted'
        REVERSED  = 'reversed',  'Reversed'
        CANCELLED = 'cancelled', 'Cancelled'

    period         = models.ForeignKey(
                         'ledger.FiscalPeriod', on_delete=models.PROTECT,
                         related_name='fx_revaluations',
                     )
    company        = models.ForeignKey(
                         'core.Company', on_delete=models.PROTECT,
                         related_name='fx_revaluations', null=True, blank=True,
                     )
    run_date       = models.DateField()
    status         = models.CharField(
                         max_length=10, choices=Status.choices, default=Status.DRAFT,
                     )
    journal_entry  = models.ForeignKey(
                         'ledger.JournalEntry', null=True, blank=True,
                         on_delete=models.SET_NULL, related_name='fx_revaluations',
                     )
    notes          = models.TextField(blank=True, default='')
    total_gain_bwp = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    total_loss_bwp = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    net_bwp        = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    created_by     = models.ForeignKey(
                         User, on_delete=models.PROTECT,
                         related_name='fx_revaluations_created',
                     )

    class Meta(BaseModel.Meta):
        verbose_name        = 'FX Revaluation'
        verbose_name_plural = 'FX Revaluations'
        ordering            = ['-run_date']
        constraints = [
            models.UniqueConstraint(
                fields=['period', 'company'],
                name='uq_fxreval_period_company',
            ),
        ]

    def __str__(self):
        return f"FX revaluation — {self.period.period_name} ({self.get_status_display()})"


class FXRevaluationLine(BaseModel):
    """One revaluation line: account × currency at the closing rate."""

    revaluation     = models.ForeignKey(
                          FXRevaluation, on_delete=models.CASCADE, related_name='lines',
                      )
    account         = models.ForeignKey(
                          'ledger.Account', on_delete=models.PROTECT,
                          related_name='fx_reval_lines',
                      )
    currency_code   = models.ForeignKey(
                          Currency, on_delete=models.PROTECT,
                          related_name='fx_reval_lines',
                      )
    balance_foreign = models.DecimalField(
                          max_digits=18, decimal_places=2,
                          help_text='Net balance in original currency.',
                      )
    closing_rate    = models.DecimalField(max_digits=18, decimal_places=8)
    book_bwp        = models.DecimalField(
                          max_digits=18, decimal_places=2,
                          help_text='BWP value currently in the GL.',
                      )
    revalued_bwp    = models.DecimalField(
                          max_digits=18, decimal_places=2,
                          help_text='BWP value at the closing rate.',
                      )
    revaluation_amount = models.DecimalField(
                          max_digits=18, decimal_places=2,
                          help_text='Positive = gain (BWP up), Negative = loss (BWP down).',
                      )

    class Meta(BaseModel.Meta):
        ordering            = ['account__code']
        verbose_name        = 'FX Revaluation Line'
        verbose_name_plural = 'FX Revaluation Lines'

    def __str__(self):
        return f"{self.account.code} {self.currency_code_id} {self.revaluation_amount:+,.2f}"


# ---------------------------------------------------------------------------
# FxRevaluationRun — open-Invoice + foreign-bank-account revaluation log.
# Lightweight idempotency record for `fx.services.revalue_period`. Distinct
# from FXRevaluation (CoA-wide revaluation): this one only covers open
# non-BWP Invoices and non-BWP BankAccount net balances, posting a single
# JE per (company, period_end). Re-running with the same key is a no-op
# unless the previous run has been reversed.
# ---------------------------------------------------------------------------

class FxRevaluationRun(BaseModel):
    """One execution of `fx.services.revalue_period` for (company, period_end)."""

    company         = models.ForeignKey(
                          'core.Company', on_delete=models.PROTECT,
                          related_name='fx_revaluation_runs',
                      )
    period_end      = models.DateField()
    journal_entry   = models.ForeignKey(
                          'ledger.JournalEntry', null=True, blank=True,
                          on_delete=models.SET_NULL,
                          related_name='fx_revaluation_runs',
                      )
    total_delta_bwp = models.DecimalField(
                          max_digits=18, decimal_places=2, default=ZERO,
                          help_text='Sum of all unrealised FX deltas posted in '
                                    'BWP. Positive = gain, negative = loss.',
                      )
    is_reversed     = models.BooleanField(
                          default=False,
                          help_text='Flip to True when journal_entry is reversed '
                                    'so the same (company, period_end) becomes '
                                    'eligible for a re-run.',
                      )

    class Meta(BaseModel.Meta):
        verbose_name        = 'FX Revaluation Run'
        verbose_name_plural = 'FX Revaluation Runs'
        ordering            = ['-period_end', '-created_at']
        indexes = [
            models.Index(fields=['company', 'period_end', 'is_reversed']),
        ]

    def __str__(self):
        return f"FxRevaluationRun {self.company_id} {self.period_end} {self.total_delta_bwp:+,.2f}"
