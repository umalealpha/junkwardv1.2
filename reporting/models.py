"""
reporting/models.py — persistence for the reporting app.

CFO directive 2026-05-24: every place a financial number lands —
dashboard, TB, GL, MA P&L, MA BS, CoA-MA-tree, audit pack — must
agree byte-for-byte. Any mismatch between two builders reading the
same posted JEs is an exception. The Reconciliation model captures
each pair-disagreement so DeepSeek can explain it and Finance can
either fix the root cause or sign off an explanation.

Done state = `Reconciliation.objects.filter(status='open').count() == 0`.
"""
from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class Reconciliation(models.Model):
    """Single A-vs-B disagreement on a financial number."""

    class Status(models.TextChoices):
        OPEN      = 'open',      'Open'           # newly detected, awaiting attention
        EXPLAINED = 'explained', 'Explained'      # Finance signed off — not a bug
        RESOLVED  = 'resolved',  'Resolved'       # spec/code fix landed; next run will clear

    class Severity(models.TextChoices):
        LOW    = 'low',    'Low'        # |Delta%| < 0.5
        MEDIUM = 'medium', 'Medium'     # 0.5 <= |Delta%| < 5
        HIGH   = 'high',   'High'       # |Delta%| >= 5

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    company = models.ForeignKey(
        'core.Company', on_delete=models.CASCADE,
        related_name='reconciliations',
    )
    period_label = models.CharField(max_length=24, db_index=True,
                                    help_text='FY25, FY26_9M, 2026-03, etc.')
    period_start = models.DateField(null=True, blank=True)
    period_end   = models.DateField(db_index=True)

    metric         = models.CharField(max_length=80, db_index=True,
                                      help_text='NEP, GWP, Total Assets, etc.')
    source_a_name  = models.CharField(max_length=80)
    source_a_value = models.DecimalField(max_digits=20, decimal_places=2)
    source_b_name  = models.CharField(max_length=80)
    source_b_value = models.DecimalField(max_digits=20, decimal_places=2)
    delta_bwp      = models.DecimalField(max_digits=20, decimal_places=2)
    delta_pct      = models.DecimalField(max_digits=8,  decimal_places=4)
    severity       = models.CharField(max_length=8, choices=Severity.choices,
                                      default=Severity.LOW, db_index=True)

    status         = models.CharField(max_length=10, choices=Status.choices,
                                      default=Status.OPEN, db_index=True)

    # DeepSeek AI explanation. Refreshed when delta changes. NEVER
    # overwrites the manual `explanation` field.
    ai_cause       = models.TextField(blank=True, default='')
    ai_fix         = models.TextField(blank=True, default='')
    ai_confidence  = models.DecimalField(max_digits=4, decimal_places=2,
                                         null=True, blank=True)
    ai_reviewed_at = models.DateTimeField(null=True, blank=True)

    explanation    = models.TextField(blank=True, default='',
                                      help_text='Hand-typed sign-off from Finance.')

    detected_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at  = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL,
                                    null=True, blank=True,
                                    on_delete=models.SET_NULL,
                                    related_name='+')

    class Meta:
        # One row per (company, period, metric, source pair). A re-run
        # of reconcile_balances upserts on this key.
        unique_together = [(
            'company', 'period_label', 'metric',
            'source_a_name', 'source_b_name',
        )]
        ordering = ['-detected_at']
        indexes = [
            models.Index(fields=['status', 'severity']),
            models.Index(fields=['company', 'period_label', 'metric']),
        ]

    def __str__(self) -> str:
        return (f'{self.company.code} {self.period_label} {self.metric}: '
                f'{self.source_a_name}={self.source_a_value} vs '
                f'{self.source_b_name}={self.source_b_value} '
                f'delta={self.delta_bwp} ({self.delta_pct}%)')


# ─── AP Age-Analysis upload (CFO 2026-07-13) ────────────────────────────────
# Omni's AP Aging report is COMPUTED from posted vendor bills. Until the
# opening AP is fully loaded it reads empty / doesn't tie to the GL AP control.
# This lets Finance (Pako / Bontle / Kago) upload the maintained age analysis
# (e.g. as at 30 June 2026) as an as-at snapshot the AP Aging page can show.
class APAgingSnapshot(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    as_of_date = models.DateField(db_index=True)
    company_id = models.UUIDField(null=True, blank=True, db_index=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name='+')
    source_filename = models.CharField(max_length=200, blank=True, default='')
    vendor_count = models.PositiveIntegerField(default=0)
    grand_total = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    currency_code = models.CharField(max_length=3, default='BWP')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['as_of_date', 'company_id', '-created_at'])]

    def __str__(self):
        return f'AP aging {self.as_of_date} — {self.vendor_count} vendors, {self.grand_total}'


class APAgingSnapshotLine(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    snapshot = models.ForeignKey(APAgingSnapshot, on_delete=models.CASCADE, related_name='lines')
    vendor_name = models.CharField(max_length=200)
    b_0_30 = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    b_31_60 = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    b_61_90 = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    b_90_plus = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=16, decimal_places=2, default=0)

    class Meta:
        ordering = ['-total', 'vendor_name']

    def __str__(self):
        return f'{self.vendor_name}: {self.total}'


class ReportRecipient(models.Model):
    """Who a scheduled finance report is emailed to — kept by Finance themselves.

    CFO 2026-09-11: "Rose or Keetile or any accountant can put the email ids of
    the people it should send automatically." Until now the lists lived in a
    Python dictionary (``send_finance_monitoring.REPORT_RECIPIENTS``), so adding
    one address needed a developer, a pull request, a review and a deploy. That
    is why the failed-debits report kept being rebuilt by hand in Outlook: the
    automated one went to two people and could not be pointed anywhere else.

    One row per (report, address). ``report_slug`` matches the keys in
    ``reporting.finance_monitoring.REPORTS``, so a new report gets an editable
    list for free.

    THE HARDCODED LISTS REMAIN, as the fallback for any report with no rows yet.
    Switching to an empty table would have silently stopped reports that are
    delivering correctly today — the migration to database-held lists has to be
    something Finance opts into report by report, not a cutover.

    ``active`` rather than deleting: taking somebody off a financial
    distribution is ordinary and weekly, and who used to receive it is worth
    keeping.

    ``email_key`` (lower-cased) carries the uniqueness, per report. Without it
    'RMokgware@' and 'rmokgware@' are two rows, the same person is mailed twice,
    and it reads to them as the system double-sending.
    """

    class Kind(models.TextChoices):
        TO = 'to', 'Addressed to'
        CC = 'cc', 'Copied'

    id          = models.UUIDField(primary_key=True, default=uuid.uuid4,
                                   editable=False)
    report_slug = models.CharField(max_length=64, db_index=True,
                    help_text='Matches reporting.finance_monitoring.REPORTS.')
    email       = models.EmailField()
    email_key   = models.CharField(max_length=254, editable=False)
    name        = models.CharField(max_length=120, blank=True, default='',
                    help_text='Who this is, for the screen. Optional.')
    kind        = models.CharField(max_length=2, choices=Kind.choices,
                                   default=Kind.TO)
    active      = models.BooleanField(default=True)
    added_by    = models.ForeignKey(settings.AUTH_USER_MODEL, null=True,
                                    blank=True, on_delete=models.SET_NULL,
                                    related_name='report_recipients_added')
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['report_slug', 'kind', 'email']
        verbose_name = 'report recipient'
        verbose_name_plural = 'report recipients'
        constraints = [
            models.UniqueConstraint(fields=['report_slug', 'email_key'],
                                    name='uniq_report_recipient'),
        ]

    def __str__(self):
        state = '' if self.active else ' (off)'
        return f'{self.report_slug}: {self.email} [{self.kind}]{state}'

    def save(self, *args, **kwargs):
        self.email = (self.email or '').strip()
        self.email_key = self.email.lower()
        super().save(*args, **kwargs)

    @classmethod
    def route_for(cls, report_slug: str):
        """{'to': [...], 'cc': [...]} for a report, or None when Finance have
        not set one up — the caller then keeps its existing default.

        A list with only CC rows and nobody addressed is treated as NOT set up.
        Sending a report to nobody, copied to two people, is a mistake somebody
        made on a screen; honouring it literally would deliver a financial
        report with an empty To line every Monday.
        """
        rows = list(cls.objects.filter(report_slug=report_slug, active=True))
        to = [r.email for r in rows if r.kind == cls.Kind.TO]
        cc = [r.email for r in rows if r.kind == cls.Kind.CC]
        if not to:
            return None
        return {'to': to, 'cc': cc}
