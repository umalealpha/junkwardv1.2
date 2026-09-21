"""
payroll/contract_models.py

CFO directive 2026-05-24 — Payroll + HR upgrades pass.

This module defines three new entities, kept in a dedicated file so the
core payroll/models.py grows linearly per CFO directive 2026-05-22
("split heavy modules per concern"):

  - EmploymentContract   one row per employee per active contract window.
  - EmployeeLoan         the principal facility (P000 → P+++).
  - LoanRepayment        one repayment row per loan per payroll period
                         (the amortisation schedule materialised lazily
                         by payroll/loan_service.apply_loan_repayments).

The file is *imported* into payroll/models.py so Django's app registry
sees all three at startup — no extra INSTALLED_APPS / app config plumbing.

Bible-check guard
-----------------
Touches payroll/ only. No imports from reporting / ledger / billing in
this module (LoanRepayment writes happen via a PayslipLine on the active
period — the existing GL posting service then picks them up like any
other line; no direct ledger writes here).
"""

from __future__ import annotations

import logging
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models

from core.models import AuditableMixin, BaseModel, Currency


logger = logging.getLogger(__name__)

ZERO = Decimal('0.00')


# ---------------------------------------------------------------------------
# Employment contract
# ---------------------------------------------------------------------------

class EmploymentContract(AuditableMixin, BaseModel):
    """One contract window for one employee.

    The active contract on any given date is the row where
    start_date <= date and (end_date is null or end_date >= date) and
    status = 'active'. Multiple historical contracts may exist — only one
    is active on a given date by convention (admin clean() validates this
    when ENFORCE_GRADE_BANDS is on, otherwise a warning is logged).
    """

    class Frequency(models.TextChoices):
        MONTHLY  = 'monthly',  'Monthly'
        BIWEEKLY = 'biweekly', 'Bi-weekly'
        WEEKLY   = 'weekly',   'Weekly'

    class Status(models.TextChoices):
        ACTIVE     = 'active',     'Active'
        TERMINATED = 'terminated', 'Terminated'
        PENDING    = 'pending',    'Pending'

    class ContractType(models.TextChoices):
        PERMANENT  = 'permanent',  'Permanent'
        FIXED_TERM = 'fixed_term', 'Fixed-term / duration'
        PROBATION  = 'probation',  'Probation'
        INTERNSHIP = 'internship', 'Internship / attachment'

    employee       = models.ForeignKey(
                         'payroll.Employee', on_delete=models.PROTECT,
                         related_name='contracts',
                     )
    start_date     = models.DateField()
    end_date       = models.DateField(null=True, blank=True)
    basic          = models.DecimalField(
                         max_digits=18, decimal_places=2, default=ZERO,
                         help_text='Basic salary per `frequency`. BWP unless `currency_code` differs.',
                     )
    currency_code  = models.ForeignKey(
                         Currency, on_delete=models.PROTECT,
                         related_name='employment_contracts',
                         default='BWP',
                     )
    frequency      = models.CharField(
                         max_length=10, choices=Frequency.choices,
                         default=Frequency.MONTHLY,
                     )
    grade          = models.ForeignKey(
                         'hris.Grade', null=True, blank=True,
                         on_delete=models.SET_NULL,
                         related_name='contracts',
                     )
    status         = models.CharField(
                         max_length=12, choices=Status.choices,
                         default=Status.ACTIVE,
                     )
    # Conditions-of-service structure (HR request 2026-07-01). end_date already
    # captures the fixed-term duration; probation_end_date marks the probation window.
    contract_type  = models.CharField(
                         max_length=12, choices=ContractType.choices,
                         default=ContractType.PERMANENT,
                     )
    probation_end_date = models.DateField(null=True, blank=True)
    class RetirementFund(models.TextChoices):
        PENSION   = 'pension',   'Pension fund'
        PROVIDENT = 'provident', 'Provident fund'
        NONE      = 'none',      'None'

    retirement_fund = models.CharField(
                         max_length=10, choices=RetirementFund.choices,
                         blank=True, default='',
                         help_text='Pension or provident fund (HC 19-Sep-2026).',
                     )
    allowance_template = models.JSONField(
                         default=dict, blank=True,
                         help_text='Snapshot of recurring allowances at signing — '
                                   '{component_code: amount}. Picked up by the amendment '
                                   'engine when materialising the next period.',
                     )

    class Meta(BaseModel.Meta):
        ordering            = ['-start_date', 'employee__full_name']
        verbose_name        = 'Employment Contract'
        verbose_name_plural = 'Employment Contracts'

    def __str__(self):
        end = self.end_date or '—'
        return f"{self.employee.full_name} · {self.start_date}→{end} ({self.status})"

    # ------------------------------------------------------------------
    # Pay-grade in-band validation
    # ------------------------------------------------------------------
    def clean(self):
        """Enforce / warn that `basic` sits inside `grade.band_min..band_max`.

        Behaviour is gated by Django setting `ENFORCE_GRADE_BANDS`:
          - True  → ValidationError raised (admin form rejects save).
          - False → warning logged, save proceeds (current default —
                    don't break legacy contracts mid-migration).
        """
        super_clean = getattr(super(), 'clean', None)
        if callable(super_clean):
            super_clean()

        self._clean_elra_limits()

        if not self.grade_id or self.basic is None:
            return

        try:
            band_min = self.grade.band_min
            band_max = self.grade.band_max
        except Exception:                               # noqa: BLE001
            return

        if not (band_min <= self.basic <= band_max):
            from django.conf import settings
            enforce = bool(getattr(settings, 'ENFORCE_GRADE_BANDS', False))
            msg = (
                f'Basic {self.basic} is outside grade {self.grade.code} band '
                f'[{band_min:.2f} … {band_max:.2f}]'
            )
            if enforce:
                raise ValidationError({'basic': msg})
            logger.warning('Grade-band breach (advisory): %s', msg)


# ---------------------------------------------------------------------------
# Employee loan + repayment schedule
# ---------------------------------------------------------------------------

    # ------------------------------------------------------------------
    # ELRA No. 27 of 2025 limits (CFO HR plan 19-Sep-2026, item #06)
    # ------------------------------------------------------------------
    def _clean_elra_limits(self):
        """s.157: a fixed-term contract may not run past 12 months (it is then
        deemed indefinite). s.155: probation may not exceed 6 months."""
        errors = {}
        if (self.contract_type == self.ContractType.FIXED_TERM
                and self.start_date and self.end_date
                and self.end_date > _add_months(self.start_date, 12)):
            errors['end_date'] = ('A fixed-term contract cannot run longer than 12 months '
                                  '(ELRA s.157). Split it into a renewal.')
        if (self.start_date and self.probation_end_date
                and self.probation_end_date > _add_months(self.start_date, 6)):
            errors['probation_end_date'] = 'Probation cannot be longer than 6 months (ELRA s.155).'
        if errors:
            raise ValidationError(errors)


class EmployeeLoan(AuditableMixin, BaseModel):
    """A principal advanced to one employee, repaid via payroll deductions."""

    class Status(models.TextChoices):
        ACTIVE    = 'active',    'Active'
        PAID      = 'paid',      'Paid in full'
        CANCELLED = 'cancelled', 'Cancelled'

    employee        = models.ForeignKey(
                          'payroll.Employee', on_delete=models.PROTECT,
                          related_name='loans',
                      )
    principal       = models.DecimalField(
                          max_digits=18, decimal_places=2, default=ZERO,
                          help_text='Original loan amount advanced to the employee.',
                      )
    annual_rate_pct = models.DecimalField(
                          max_digits=6, decimal_places=3, default=ZERO,
                          help_text='Annual interest %. 0 → straight-line amortisation.',
                      )
    term_months     = models.PositiveIntegerField(
                          default=12,
                          help_text='Number of monthly instalments.',
                      )
    start_period    = models.ForeignKey(
                          'payroll.PayrollPeriod', null=True, blank=True,
                          on_delete=models.PROTECT,
                          related_name='loans_starting_here',
                          help_text='First period this loan starts deducting from. '
                                    'Null → starts the next OPEN period encountered.',
                      )
    status          = models.CharField(
                          max_length=12, choices=Status.choices,
                          default=Status.ACTIVE,
                      )
    outstanding     = models.DecimalField(
                          max_digits=18, decimal_places=2, default=ZERO,
                          help_text='Live outstanding balance — decremented by '
                                    'apply_loan_repayments() each period.',
                      )

    class Meta(BaseModel.Meta):
        ordering            = ['-created_at']
        verbose_name        = 'Employee Loan'
        verbose_name_plural = 'Employee Loans'

    def __str__(self):
        return (
            f'{self.employee.full_name} · principal={self.principal} '
            f'outstanding={self.outstanding} ({self.status})'
        )


class LoanRepayment(BaseModel):
    """One scheduled / posted repayment row of one loan."""

    loan      = models.ForeignKey(
                    EmployeeLoan, on_delete=models.CASCADE,
                    related_name='repayments',
                )
    period    = models.ForeignKey(
                    'payroll.PayrollPeriod', on_delete=models.PROTECT,
                    related_name='loan_repayments',
                )
    principal = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    interest  = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    posted_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        ordering            = ['period__start_date', 'loan__employee__full_name']
        unique_together     = [('loan', 'period')]
        verbose_name        = 'Loan Repayment'
        verbose_name_plural = 'Loan Repayments'

    def __str__(self):
        return f'{self.loan.employee.full_name} · {self.period.period_name} · {self.principal + self.interest}'


def _add_months(d, months):
    """Same day `months` later, clamped to month end (31-Jan + 1 = 28/29-Feb)."""
    import calendar
    y, m = divmod(d.month - 1 + months, 12)
    y, m = d.year + y, m + 1
    return d.replace(year=y, month=m, day=min(d.day, calendar.monthrange(y, m)[1]))
