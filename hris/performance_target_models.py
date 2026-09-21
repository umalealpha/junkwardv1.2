"""
hris/performance_target_models.py — standing monthly targets + monthly outcomes.

CFO directive 2026-07-20: every employee can carry a monthly target drawn from
their job description (e.g. Gosego Makone — BWP 30,000 new sales/month). At
month-end the manager's feedback prompt shows target vs actual and asks whether
it was achieved. This is the STRUCTURED target store the system lacked — the
6-monthly Development Dialogue only holds free-text objectives, and MonthlyCheckIn
holds the feedback itself, not a standing target.

Two models:
  * PerformanceTarget       — the standing target definition (one per metric per person).
  * PerformanceTargetResult — the outcome for a given month (actual + achieved),
                              which powers the trend view, the league table, and
                              caches an auto-pulled actual. One row per target+month.

Both are entity-agnostic HR data (no GL, no customer PII). Company/entity is
reached via profile.employee.company like the rest of the module.
"""
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import User
from django.db import models

from core.models import AuditableMixin, BaseModel


class PerformanceTarget(AuditableMixin, BaseModel):
    """A standing, recurring target for one employee (from their job description)."""

    class Cadence(models.TextChoices):
        MONTHLY   = 'monthly',   'Monthly'
        QUARTERLY = 'quarterly', 'Quarterly'
        ANNUAL    = 'annual',    'Annual'

    class Source(models.TextChoices):
        # How the ACTUAL is obtained at month-end.
        MANUAL        = 'manual',        'Manager enters the actual'
        HEALTH_QUOTES = 'health_quotes', 'Auto: group-health new sales (omni)'

    class Unit(models.TextChoices):
        BWP     = 'BWP',     'Pula (BWP)'
        COUNT   = 'count',   'Count / number'
        PERCENT = 'percent', 'Percentage'

    profile = models.ForeignKey(
        'hris.HRISProfile', on_delete=models.CASCADE,
        related_name='performance_targets',
    )
    metric       = models.CharField(max_length=200, help_text='e.g. "New sales", "Claims closed"')
    target_value = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))
    unit         = models.CharField(max_length=10, choices=Unit.choices, default=Unit.BWP)
    cadence      = models.CharField(max_length=10, choices=Cadence.choices, default=Cadence.MONTHLY)
    source       = models.CharField(max_length=20, choices=Source.choices, default=Source.MANUAL)
    active       = models.BooleanField(default=True, db_index=True)
    note         = models.TextField(blank=True, default='',
                       help_text='From the job description — context for the target.')
    created_by   = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='performance_targets_created',
    )

    class Meta(BaseModel.Meta):
        ordering            = ['profile__employee__full_name', 'metric']
        verbose_name        = 'Performance Target'
        verbose_name_plural = 'Performance Targets'
        indexes = [
            models.Index(fields=['profile', 'active'], name='perftarget_profile_active_idx'),
        ]

    def __str__(self):
        who = getattr(getattr(self.profile, 'employee', None), 'full_name', '') or 'employee'
        return f"{who} — {self.metric}: {self.target_value} {self.unit}/{self.cadence}"


class PerformanceTargetResult(AuditableMixin, BaseModel):
    """The outcome of one target for one month: actual value + achieved flag."""

    profile = models.ForeignKey(
        'hris.HRISProfile', on_delete=models.CASCADE,
        related_name='performance_target_results',
    )
    target = models.ForeignKey(
        PerformanceTarget, on_delete=models.CASCADE, related_name='results',
    )
    period_month = models.PositiveSmallIntegerField()   # 1-12
    period_year  = models.PositiveSmallIntegerField()

    # Snapshot the target at recording time so history is stable if the target changes.
    target_value = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0'))
    # actual_value = the achievement basis (VAT-exclusive / net). The VAT split is
    # kept alongside so the sheet can show excl / VAT / incl (CFO 2026-07-20).
    actual_value = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    actual_excl  = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    actual_vat   = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    actual_incl  = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    achieved     = models.BooleanField(null=True, blank=True)   # null = not yet confirmed
    source_used  = models.CharField(max_length=20, blank=True, default='')  # 'health_quotes'/'manual'
    note         = models.TextField(blank=True, default='')

    # Link to the feedback record + who confirmed it.
    checkin = models.ForeignKey(
        'hris.MonthlyCheckIn', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='target_results',
    )
    recorded_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='performance_results_recorded',
    )

    class Meta(BaseModel.Meta):
        ordering            = ['-period_year', '-period_month']
        verbose_name        = 'Performance Target Result'
        verbose_name_plural = 'Performance Target Results'
        unique_together     = [('target', 'period_year', 'period_month')]
        indexes = [
            models.Index(fields=['profile', 'period_year', 'period_month'],
                         name='perfresult_profile_period_idx'),
        ]

    def __str__(self):
        return (f"{self.target.metric} {self.period_year}-{self.period_month:02d}: "
                f"{self.actual_value} vs {self.target_value} "
                f"({'✓' if self.achieved else '✗' if self.achieved is False else '—'})")
