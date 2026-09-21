"""Reconciliation Hub — Phase 1 models.

A source-to-ledger tie-out for ADIC (incl. Instant): compares an external
*source* figure (Graphite read-only replica, or a manual expected-figures
register) against the *posted* figure in omni's GL, per metric per period, and
flags variances above tolerance. Plus a three-way premium-debtor ageing panel.

PII: this module persists **totals and counts only** — never policyholder-level
rows. ``SourceFigure`` and ``AgeingTieOut`` hold aggregate numbers plus a text
reference (e.g. table name / file path), nothing identifying.
"""

from django.conf import settings
from django.db import models

from core.models import AuditableMixin, BaseModel
from ledger.models import Account
from .constants import (
    ZERO,
    DEFAULT_TOLERANCE_PCT,
    FlowType,
    Unit,
    SourceSystem,
    LineStatus,
    RunStatus,
)


class MetricSourceMap(AuditableMixin, BaseModel):
    """Config: maps a reconciled metric to its GL accounts + source behaviour.

    Precedent: ``assets.AssetCategory`` (a config entity with FK/M2M pointers to
    ``ledger.Account``). The signed GL total for a metric is the sum of
    ``_signed_balance`` over ``accounts`` (see services.posted_for_metric).
    """

    metric_key = models.CharField(max_length=40, unique=True)
    label = models.CharField(max_length=120)
    unit = models.CharField(max_length=8, choices=Unit.CHOICES, default=Unit.BWP)
    flow_type = models.CharField(
        max_length=8, choices=FlowType.CHOICES, default=FlowType.PERIOD,
        help_text='period = P&L flow over [from,to]; balance = cumulative as-of to.',
    )

    # GL side. Explicit accounts for auditability. For premium debtors the
    # ``include_all_receivable`` flag additionally pulls every is_receivable account
    # (covers Instant A/R without needing to enumerate every code).
    accounts = models.ManyToManyField(
        Account, blank=True, related_name='recon_metrics',
        help_text='GL accounts summed (signed) to get the omni posted figure.',
    )
    include_all_receivable = models.BooleanField(
        default=False,
        help_text='If set, also include every Account with is_receivable=True.',
    )

    # Source side.
    source_system = models.CharField(
        max_length=20, choices=SourceSystem.CHOICES, default=SourceSystem.REGISTER,
    )
    source_ref = models.CharField(
        max_length=200, blank=True, default='',
        help_text='Free-text source pointer (Graphite table, report type, note).',
    )

    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=100)

    class Meta(BaseModel.Meta):
        ordering = ['sort_order', 'metric_key']
        verbose_name = 'Metric source map'
        verbose_name_plural = 'Metric source maps'

    def __str__(self):
        return f'{self.metric_key} ({self.label})'


class SourceFigure(AuditableMixin, BaseModel):
    """A captured external *source* total for one metric/period/company.

    Totals + counts only — NO policyholder rows. Populated either automatically
    (Graphite replica summary) or manually via the expected-figures register.
    """

    company = models.ForeignKey(
        'core.Company', on_delete=models.CASCADE, related_name='recon_source_figures',
    )
    period_label = models.CharField(max_length=24, db_index=True)
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(db_index=True)

    metric_key = models.CharField(max_length=40, db_index=True)
    source_system = models.CharField(max_length=20, choices=SourceSystem.CHOICES)

    source_value = models.DecimalField(max_digits=20, decimal_places=2, default=ZERO)
    row_count = models.IntegerField(null=True, blank=True)
    source_ref = models.CharField(max_length=200, blank=True, default='')
    notes = models.TextField(blank=True, default='')

    captured_at = models.DateTimeField(auto_now_add=True, db_index=True)
    captured_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='recon_source_figures',
    )

    class Meta(BaseModel.Meta):
        ordering = ['-period_end', 'metric_key']
        indexes = [
            models.Index(fields=['company', 'period_label', 'metric_key']),
        ]
        verbose_name = 'Source figure'
        verbose_name_plural = 'Source figures'

    def __str__(self):
        return f'{self.metric_key} {self.period_label}: {self.source_value}'


class ReconciliationRun(AuditableMixin, BaseModel):
    """Header for one reconciliation pass over a period."""

    company = models.ForeignKey(
        'core.Company', on_delete=models.CASCADE, related_name='recon_runs',
    )
    period_label = models.CharField(max_length=24, db_index=True)
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(db_index=True)

    tolerance_pct = models.DecimalField(
        max_digits=6, decimal_places=2, default=DEFAULT_TOLERANCE_PCT,
    )
    status = models.CharField(
        max_length=12, choices=RunStatus.CHOICES, default=RunStatus.COMPLETED,
    )

    run_at = models.DateTimeField(auto_now_add=True, db_index=True)
    run_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='recon_runs',
    )
    notes = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-run_at']
        verbose_name = 'Reconciliation run'
        verbose_name_plural = 'Reconciliation runs'

    def __str__(self):
        return f'Recon {self.company_id} {self.period_label} @ {self.run_at:%Y-%m-%d}'


class ReconciliationLine(BaseModel):
    """One metric row of the control dashboard: source vs posted vs variance."""

    run = models.ForeignKey(
        ReconciliationRun, on_delete=models.CASCADE, related_name='lines',
    )
    metric_key = models.CharField(max_length=40, db_index=True)
    label = models.CharField(max_length=120)
    unit = models.CharField(max_length=8, choices=Unit.CHOICES, default=Unit.BWP)

    source_total = models.DecimalField(
        max_digits=20, decimal_places=2, null=True, blank=True,
    )
    source_system = models.CharField(
        max_length=20, choices=SourceSystem.CHOICES, blank=True, default='',
    )
    omni_posted = models.DecimalField(
        max_digits=20, decimal_places=2, null=True, blank=True,
    )
    variance = models.DecimalField(
        max_digits=20, decimal_places=2, null=True, blank=True,
    )
    variance_pct = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True,
    )
    status = models.CharField(max_length=14, choices=LineStatus.CHOICES)
    note = models.TextField(blank=True, default='')

    # Optional link to the pairwise Reconciliation record (DeepSeek AI narrative).
    reconciliation = models.ForeignKey(
        'reporting.Reconciliation', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='recon_hub_lines',
    )

    class Meta(BaseModel.Meta):
        ordering = ['run', 'metric_key']
        verbose_name = 'Reconciliation line'
        verbose_name_plural = 'Reconciliation lines'

    def __str__(self):
        return f'{self.metric_key}: src={self.source_total} gl={self.omni_posted} [{self.status}]'


class AgeingTieOut(BaseModel):
    """Per-bucket premium-debtor ageing tie-out for a run.

    Compares Graphite (Dom-Com, read-only replica), omni GL AR-aging, and
    (Phase 2) the reporting-portal extract. Totals only.
    """

    run = models.ForeignKey(
        ReconciliationRun, on_delete=models.CASCADE, related_name='ageing_tieouts',
    )
    bucket = models.CharField(max_length=8)  # one of CANONICAL_BUCKETS, or 'total'

    graphite_total = models.DecimalField(
        max_digits=20, decimal_places=2, null=True, blank=True,
    )
    omni_total = models.DecimalField(
        max_digits=20, decimal_places=2, null=True, blank=True,
    )
    portal_total = models.DecimalField(
        max_digits=20, decimal_places=2, null=True, blank=True,
    )
    variance_graphite_vs_omni = models.DecimalField(
        max_digits=20, decimal_places=2, null=True, blank=True,
    )
    variance_pct = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True,
    )
    status = models.CharField(max_length=14, choices=LineStatus.CHOICES)

    class Meta(BaseModel.Meta):
        ordering = ['run', 'bucket']
        verbose_name = 'Ageing tie-out'
        verbose_name_plural = 'Ageing tie-outs'

    def __str__(self):
        return f'{self.bucket}: gr={self.graphite_total} omni={self.omni_total} [{self.status}]'
