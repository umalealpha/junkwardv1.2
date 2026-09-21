"""salvage/parts_models.py — Veritas Parts & Assessment Savings.

CFO directive 2026-09-10: Bharath forwards two workbooks from the Parts &
Assessments team every month (assessment savings, parts purchases). They are
the only record of what the panel-beater contract pricing actually saves and
of who we buy parts from. This puts them in Omni under Veritas.

Kept in a dedicated module and imported from `salvage/models.py` so Django's
app registry picks the models up without an app-config change — same pattern
as `salvage/auction_models.py`.

READ-ONLY MONEY: nothing here posts to the GL. These are management figures
sourced from a spreadsheet, so every row keeps a link back to the upload it
came from and the file's own stated numbers sit alongside ours.
"""
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import User
from django.db import models

from core.models import BaseModel


class EntrySource(models.TextChoices):
    """How a row got here. An upload replaces only what an upload created —
    a line somebody typed must survive the next workbook (CFO 2026-09-10:
    "I want people to actually enter data into this going forward")."""

    UPLOAD = 'UPLOAD', 'From a workbook'
    MANUAL = 'MANUAL', 'Typed in'


class PartsUpload(BaseModel):
    """One workbook, one upload. Re-uploading the same month replaces it."""

    class Kind(models.TextChoices):
        ASSESSMENT = 'ASSESSMENT', 'Assessment savings report'
        PARTS      = 'PARTS',      'Parts purchase summary'

    kind          = models.CharField(max_length=12, choices=Kind.choices)
    file_name     = models.CharField(max_length=255)
    uploaded_by   = models.ForeignKey(User, null=True, blank=True,
                                      on_delete=models.SET_NULL,
                                      related_name='parts_uploads')
    periods       = models.JSONField(default=list, blank=True,
                                     help_text='Month starts (ISO) found in the workbook.')
    rows_created  = models.PositiveIntegerField(default=0)
    notes         = models.TextField(blank=True, default='')
    reconciliation = models.JSONField(default=list, blank=True,
                                      help_text='Stated-vs-computed lines, as parsed.')

    class Meta(BaseModel.Meta):
        indexes = [models.Index(fields=['kind', '-created_at'])]

    def __str__(self):
        return f'{self.get_kind_display()} · {self.file_name}'


class AssessmentSaving(BaseModel):
    """One assessed job: what the repairer quoted, what we assessed it at,
    and therefore what the contract pricing saved."""

    upload        = models.ForeignKey(PartsUpload, on_delete=models.CASCADE,
                                      related_name='assessment_rows',
                                      null=True, blank=True)
    source        = models.CharField(max_length=8, choices=EntrySource.choices,
                                     default=EntrySource.MANUAL)
    entered_by    = models.ForeignKey(User, null=True, blank=True,
                                      on_delete=models.SET_NULL,
                                      related_name='assessment_savings_entered')
    notes         = models.TextField(blank=True, default='')
    period        = models.DateField(help_text='First day of the month the job was authorised in.')
    assessment_id = models.CharField(max_length=40)
    reg_no        = models.CharField(max_length=32,  blank=True, default='')
    vehicle       = models.CharField(max_length=120, blank=True, default='')
    repairer      = models.CharField(max_length=120, blank=True, default='')
    req_auth_date = models.DateField(null=True, blank=True)

    quote_parts   = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    quote_labour  = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    quote_paint   = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    quote_total   = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))

    report_parts  = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    report_labour = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    report_paint  = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    report_total  = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))

    # Ours: quote − assessment, per block. This is what the dashboard totals.
    saving_parts  = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    saving_labour = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    saving_paint  = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    saving_total  = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))

    # The file's own savings column, kept so the variance is visible instead
    # of a silent overwrite (some rows in the July sheet disagree by >P5,000).
    file_saving_total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))
    savings_variance  = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))

    class Meta(BaseModel.Meta):
        ordering = ['-period', 'assessment_id']
        indexes  = [
            models.Index(fields=['period']),
            models.Index(fields=['repairer']),
            models.Index(fields=['assessment_id']),
        ]

    def recompute(self):
        """Totals are derived, never typed. A register row cannot disagree with
        its own parts/labour/paint the way the workbook's dragged formulas do."""
        self.quote_total  = self.quote_parts + self.quote_labour + self.quote_paint
        self.report_total = self.report_parts + self.report_labour + self.report_paint
        self.saving_parts  = self.quote_parts  - self.report_parts
        self.saving_labour = self.quote_labour - self.report_labour
        self.saving_paint  = self.quote_paint  - self.report_paint
        self.saving_total  = self.saving_parts + self.saving_labour + self.saving_paint
        if self.source == EntrySource.MANUAL:
            # Nothing to reconcile against — the register IS the source.
            self.file_saving_total = self.saving_total
        self.savings_variance = self.file_saving_total - self.saving_total
        if self.period is None and self.req_auth_date:
            self.period = self.req_auth_date.replace(day=1)

    def save(self, *args, **kwargs):
        # Derive before the insert, not after — a typed line arrives with the
        # month and the totals blank, and the column is NOT NULL. Note this
        # means save() ALWAYS recomputes: never hand it a total you want kept.
        # (`bulk_create`, which the workbook import uses, does not call save(),
        # so the parser's own figures go in untouched.)
        self.recompute()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.assessment_id} · {self.period:%b %Y}'


class PartsSpend(BaseModel):
    """Parts bought from one supplier in one month, by category."""

    class Category(models.TextChoices):
        DEALERSHIP  = 'DEALERSHIP',  'Dealership'
        AFTERMARKET = 'AFTERMARKET', 'Aftermarket'
        WINDSCREEN  = 'WINDSCREEN',  'Windscreen / glass'

    upload   = models.ForeignKey(PartsUpload, on_delete=models.CASCADE,
                                 related_name='spend_rows', null=True, blank=True)
    source   = models.CharField(max_length=8, choices=EntrySource.choices,
                                default=EntrySource.MANUAL)
    entered_by = models.ForeignKey(User, null=True, blank=True,
                                   on_delete=models.SET_NULL,
                                   related_name='parts_spend_entered')
    notes    = models.TextField(blank=True, default='')
    category = models.CharField(max_length=12, choices=Category.choices)
    supplier = models.CharField(max_length=120)
    month    = models.DateField()
    amount   = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))

    class Meta(BaseModel.Meta):
        ordering = ['-month', 'category', 'supplier']
        indexes  = [
            models.Index(fields=['month']),
            models.Index(fields=['category', 'month']),
            models.Index(fields=['supplier']),
        ]

    def __str__(self):
        return f'{self.supplier} · {self.month:%b %Y} · {self.amount}'


class PartsContractPricing(BaseModel):
    """The monthly contract-pricing figure — parts we did not have to source
    because the panel beater matched our pricing. Lives only on the SUMMARY
    sheet, so it is stored separately from supplier spend."""

    upload = models.ForeignKey(PartsUpload, on_delete=models.CASCADE,
                               related_name='contract_rows', null=True, blank=True)
    source = models.CharField(max_length=8, choices=EntrySource.choices,
                              default=EntrySource.MANUAL)
    entered_by = models.ForeignKey(User, null=True, blank=True,
                                   on_delete=models.SET_NULL,
                                   related_name='contract_pricing_entered')
    notes  = models.TextField(blank=True, default='')
    month  = models.DateField()
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))

    class Meta(BaseModel.Meta):
        ordering = ['-month']
        indexes  = [models.Index(fields=['month'])]

    def __str__(self):
        return f'Contract pricing {self.month:%b %Y} · {self.amount}'


class PartsHistory(BaseModel):
    """Five-financial-year parts spend by month, as stated on SUMMARY.
    Financial year runs July → June, so FY-27 starts 1 July 2026."""

    upload       = models.ForeignKey(PartsUpload, on_delete=models.CASCADE,
                                     related_name='history_rows',
                                     null=True, blank=True)
    fy_label     = models.CharField(max_length=8)
    month_number = models.PositiveSmallIntegerField()
    amount       = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'))

    class Meta(BaseModel.Meta):
        ordering = ['fy_label', 'month_number']
        indexes  = [models.Index(fields=['fy_label'])]

    def __str__(self):
        return f'{self.fy_label} m{self.month_number} · {self.amount}'
