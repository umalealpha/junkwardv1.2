"""
regulatory/models.py

NBFIRA capital-adequacy monitoring for Alpha Direct Insurance.

Two models:

  CapitalRequirementParameter
    Configurable inputs to the capital-adequacy formula. Seeded with the
    historical NBFIRA general-insurer numbers as PLACEHOLDERS — the CFO MUST
    verify against the current Insurance Industry Regulations before any
    real submission.

  RegulatoryCapitalSnapshot
    Point-in-time computation. Stores Available Capital, Required Capital,
    each component, the resulting Capital Adequacy Ratio (CAR), and the
    status (compliant / margin / breach). Snapshots are immutable once
    approved by the CFO — they are the audit trail for NBFIRA returns.

CAR formula (industry-standard simplified):

    Required Capital = max(
        minimum_capital_floor,
        premium_factor   × Gross Written Premium (12m),
        claims_factor    × Average claims incurred (last 3y),
    )
    Available Capital = Total Equity − Intangibles − Related-party deductions
    CAR              = Available / Required          (>= 1.0 = compliant)

The thresholds:
    CAR >= 1.25  → COMPLIANT       (comfortable buffer)
    1.00–1.25    → MARGIN          (reportable to CFO; watch list)
    <  1.00      → BREACH          (immediate escalation)
"""

from decimal import Decimal

from django.contrib.auth.models import User
from django.db import models

from core.models import AuditableMixin, BaseModel, Company


ZERO = Decimal('0.00')


class CapitalRequirementParameter(AuditableMixin, BaseModel):
    """
    A single editable parameter to the capital-adequacy formula.

    Code values used by services.compute_capital_check():
        minimum_capital_floor       — absolute BWP floor (e.g., 5,000,000)
        premium_factor              — % of GWP                (e.g., 0.18)
        claims_factor               — % of avg claims         (e.g., 0.26)
        compliant_threshold         — CAR >= this is COMPLIANT (e.g., 1.25)
        margin_threshold            — CAR >= this is MARGIN    (e.g., 1.00)
        gwp_account_codes           — CSV of account codes that count as GWP
        claims_account_codes        — CSV of account codes that count as claims
        intangibles_account_codes   — CSV of equity-side deductions
    """

    code         = models.CharField(max_length=60, unique=True)
    label        = models.CharField(max_length=200)
    value        = models.CharField(
                       max_length=200,
                       help_text='String — services parse to Decimal or list as needed.',
                   )
    notes        = models.TextField(blank=True, default='')
    effective_from = models.DateField(null=True, blank=True)
    is_active    = models.BooleanField(default=True)

    class Meta(BaseModel.Meta):
        ordering = ['code']
        verbose_name        = 'Capital Requirement Parameter'
        verbose_name_plural = 'Capital Requirement Parameters'

    def __str__(self):
        return f"{self.code} = {self.value}"


class RegulatoryCapitalSnapshot(AuditableMixin, BaseModel):
    """A snapshot of the capital-adequacy calculation at a point in time."""

    class Status(models.TextChoices):
        COMPLIANT = 'compliant', 'Compliant'
        MARGIN    = 'margin',    'Margin (watch list)'
        BREACH    = 'breach',    'BREACH'
        DRAFT     = 'draft',     'Draft'

    as_of_date         = models.DateField()
    company            = models.ForeignKey(
                             Company, on_delete=models.PROTECT,
                             related_name='capital_snapshots',
                             null=True, blank=True,
                         )
    available_capital  = models.DecimalField(max_digits=18, decimal_places=2)
    required_capital   = models.DecimalField(max_digits=18, decimal_places=2)
    capital_adequacy_ratio = models.DecimalField(
                                  max_digits=10, decimal_places=4,
                                  help_text='Available / Required',
                              )
    status             = models.CharField(
                              max_length=15, choices=Status.choices,
                              default=Status.DRAFT,
                          )
    components         = models.JSONField(
                              default=dict, blank=True,
                              help_text='Full breakdown — equity, deductions, '
                                        'GWP, claims, the 3 candidates for required, etc.',
                          )
    notes              = models.TextField(blank=True, default='')
    prepared_by        = models.ForeignKey(
                              User, on_delete=models.PROTECT,
                              related_name='capital_snapshots_prepared',
                          )
    approved_by        = models.ForeignKey(
                              User, null=True, blank=True,
                              on_delete=models.SET_NULL,
                              related_name='capital_snapshots_approved',
                          )
    approved_at        = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ['-as_of_date', '-created_at']
        verbose_name        = 'Regulatory Capital Snapshot'
        verbose_name_plural = 'Regulatory Capital Snapshots'

    def __str__(self):
        return f"Capital snapshot {self.as_of_date} CAR={self.capital_adequacy_ratio:.2f} ({self.status})"

    @property
    def car_pct(self):
        return (self.capital_adequacy_ratio * Decimal('100')).quantize(Decimal('0.01'))


# ---------------------------------------------------------------------------
# Statutory tax compliance workflow  (Oprah Mogomotsi feature request,
# 2026-09-11; CFO-approved same day)
# ---------------------------------------------------------------------------
#
# A reminder that fires once and stops is not a control. What BURS penalties and
# the NBFIRA compliance file actually need is a chain: someone was told, someone
# did it, and the CFO checked it. That is why this is a state machine and not a
# calendar entry with an alarm on it.
#
# The dates themselves are NOT stored as rules here - they are computed by
# regulatory/tax_calendar.py and materialised into rows by the
# `generate_tax_obligations` command. This model holds only what a calculation
# cannot know: who owns it, whether it was done, and what the CFO said.

from .tax_calendar import VAT_DUE_DAY, TaxType, VatCycle  # noqa: E402


class TaxComplianceSettings(BaseModel):
    """
    The handful of facts the schedule depends on that are BURS's decision, not
    ours. One row; `load()` returns it, creating it on first use.

    Kept in the database rather than in code because the VAT registration
    category changes with turnover, and when BURS moves Alpha Direct we must be
    able to flip a setting rather than ship a release.
    """
    vat_cycle = models.CharField(
        max_length=20, choices=VatCycle.CHOICES, default=VatCycle.CATEGORY_B,
        help_text='BURS VAT registration category. CFO-confirmed as Category B on 2026-09-11.',
    )
    vat_due_day = models.PositiveSmallIntegerField(
        default=VAT_DUE_DAY,
        help_text='Day of the month VAT falls due. CFO correction 2026-09-11: Alpha '
                  'Direct pays on the 25th, not the 28th the tax advisers quote.',
    )
    financial_year_end_month = models.PositiveSmallIntegerField(
        default=6,
        help_text='Month the financial year ends. Alpha Direct = 6 (30 June).',
    )
    months_ahead = models.PositiveSmallIntegerField(
        default=18,
        help_text='How far ahead obligations are generated. 18 months keeps a '
                  'full year visible without the board filling with distant items.',
    )
    updated_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='tax_compliance_settings_updates',
    )

    class Meta(BaseModel.Meta):
        verbose_name = 'Tax compliance settings'
        verbose_name_plural = 'Tax compliance settings'

    def __str__(self):
        return f'Tax settings (VAT {self.get_vat_cycle_display()})'

    @classmethod
    def load(cls) -> 'TaxComplianceSettings':
        obj = cls.objects.order_by('created_at').first()
        if obj is None:
            obj = cls.objects.create()
        return obj


class TaxObligationOwner(BaseModel):
    """
    Who prepares each kind of filing. CFO decision 2026-09-11: by tax type, one
    named person each - VAT and SAT to the Financial Controller, PAYE and OWHT
    to the Finance Manager, because payroll sits there.

    Deliberately a table and not a constant: the CFO reassigns owners from the
    page without a release, and an owner who leaves must not take a statutory
    deadline with them.
    """
    tax_type = models.CharField(max_length=20, choices=TaxType.CHOICES, unique=True)
    owner = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='tax_obligations_owned',
        help_text='The preparer. Reminders go to this person.',
    )
    updated_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='tax_obligation_owner_updates',
    )

    class Meta(BaseModel.Meta):
        ordering = ['tax_type']

    def __str__(self):
        if self.owner:
            who = self.owner.get_full_name() or self.owner.username
        else:
            who = 'UNASSIGNED'
        return f'{TaxType.LABELS.get(self.tax_type, self.tax_type)} -> {who}'


class TaxComplianceTask(AuditableMixin, BaseModel):
    """
    One statutory filing, with its completion and verification chain.

    The six states are the reporter's, unchanged:

      SCHEDULED         the deadline exists but the 10-day window has not opened
      REMINDING         inside the window, reminders going out daily
      PREPARER_COMPLETE the preparer says it is filed - the reminder does NOT stop
      VERIFIED          the CFO checked it. The ONLY state that closes the task.
      LATE              reached VERIFIED after the target but before the due date
      BREACH            the statutory due date passed without VERIFIED

    Two things about this are easy to get wrong and are enforced in code below:

    1. PREPARER_COMPLETE does not stop the reminders. If it did, the preparer
       could close their own statutory obligation, and the verification chain
       would be decorative.

    2. LATE and BREACH are NOT the same failure and do not share a field.
       Missing the internal 10-day target is a service failure and takes a short
       reason from the preparer. Missing the statutory date is a live BURS
       breach: it is higher severity, it goes to the CFO separately, and only
       the CFO can close it, in writing. `late_reason` and `breach_note` are
       therefore separate columns on purpose - do not merge them.
    """

    class Status(models.TextChoices):
        SCHEDULED         = 'scheduled',         'Scheduled'
        REMINDING         = 'reminding',         'Active / reminding'
        PREPARER_COMPLETE = 'preparer_complete', 'Preparer complete'
        VERIFIED          = 'verified',          'CFO verified / closed'
        LATE              = 'late',              'Closed late (before due date)'
        BREACH            = 'breach',            'BREACH - statutory date missed'

    # Any state in which the task still needs someone to act.
    OPEN_STATUSES = [Status.SCHEDULED, Status.REMINDING, Status.PREPARER_COMPLETE, Status.BREACH]
    # Reminders go out in these states. PREPARER_COMPLETE is in the list on purpose.
    REMINDING_STATUSES = [Status.REMINDING, Status.PREPARER_COMPLETE, Status.BREACH]

    # --- identity (from tax_calendar.py) ---
    obligation_key = models.CharField(
        max_length=60, unique=True,
        help_text='Stable identity from tax_calendar.py, e.g. "vat:2026-10-31". '
                  'Unique so regenerating the schedule can never duplicate a filing.',
    )
    tax_type     = models.CharField(max_length=20, choices=TaxType.CHOICES)
    period_label = models.CharField(max_length=60)
    period_start = models.DateField()
    period_end   = models.DateField()

    # --- the two deadlines ---
    due_date = models.DateField(
        db_index=True,
        help_text='The STATUTORY deadline. Passing this without CFO verification is a breach.',
    )
    target_date = models.DateField(
        db_index=True,
        help_text='Internal deadline, 10 days before the statutory one. Reminders '
                  'start here and lateness is measured against it.',
    )

    # --- workflow ---
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.SCHEDULED, db_index=True,
    )
    owner = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='tax_compliance_tasks',
        help_text='Snapshotted from TaxObligationOwner when the task is created, so '
                  'reassigning a tax type later does not rewrite history.',
    )

    completed_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='tax_tasks_completed',
    )
    completed_at    = models.DateTimeField(null=True, blank=True)
    completion_note = models.TextField(
        blank=True, default='',
        help_text='What was filed, and the BURS reference if there is one.',
    )

    verified_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='tax_tasks_verified',
    )
    verified_at   = models.DateTimeField(null=True, blank=True)
    verified_note = models.TextField(blank=True, default='')

    late_reason = models.TextField(
        blank=True, default='',
        help_text='State 5 only: completed after the 10-day target but before the '
                  'statutory due date. An internal service miss.',
    )
    breach_note = models.TextField(
        blank=True, default='',
        help_text='State 6 only, CFO-written: the statutory date was missed. A live '
                  'BURS exposure. Separate from late_reason by design - never merge them.',
    )
    breach_flagged_at = models.DateTimeField(
        null=True, blank=True,
        help_text='When the CFO was alerted to the breach. Set once; the alert never repeats.',
    )

    # --- date changes (CFO instruction 2026-09-11) ---
    # Oprah / Kago / Legakwa may move a statutory date when BURS shifts one.
    # The ORIGINAL is kept forever: without it, "was this deadline always the
    # 20th?" has no answer, and a moved date would quietly rewrite history.
    original_due_date = models.DateField(
        null=True, blank=True,
        help_text='The computed date, kept when someone overrides due_date. '
                  'Null means the date has never been changed.',
    )
    date_change_reason = models.TextField(
        blank=True, default='',
        help_text='Why the statutory date was moved. Mandatory on any change.',
    )
    date_changed_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='tax_tasks_date_changed',
    )
    date_changed_at = models.DateTimeField(null=True, blank=True)

    last_reminded_on = models.DateField(
        null=True, blank=True,
        help_text='Guards against two reminder runs in one day sending two emails.',
    )
    reminder_count = models.PositiveIntegerField(default=0)

    class Meta(BaseModel.Meta):
        ordering = ['due_date', 'tax_type']
        indexes = [
            models.Index(fields=['status', 'due_date']),
        ]

    def __str__(self):
        return f'{TaxType.LABELS.get(self.tax_type, self.tax_type)} - {self.period_label} (due {self.due_date})'

    # --- derived ---

    @property
    def label(self) -> str:
        return f'{TaxType.LABELS.get(self.tax_type, self.tax_type)} - {self.period_label}'

    @property
    def is_closed(self) -> bool:
        """A task is closed only once the CFO has verified it. PREPARER_COMPLETE
        is NOT closed, and BREACH is NOT closed - that is the whole point."""
        return self.status in (self.Status.VERIFIED, self.Status.LATE)

    def days_to_due(self, today) -> int:
        return (self.due_date - today).days

    def escalates_to_cfo(self, today) -> bool:
        """The CFO is copied on the daily reminder from 3 days before the
        statutory date. Earlier than that it is the preparer's job and the CFO
        does not need to see it - which is what stops the escalation becoming
        noise he learns to ignore."""
        return self.days_to_due(today) <= 3


class TaxCalendarEditor(BaseModel):
    """
    Who may CHANGE a statutory date. The CFO (superuser) plus exactly this list
    — a Finance title is NOT a grant.

    CFO instruction 2026-09-11: Oprah Mogomotsi, Kago Tshutlhedi and Legakwa
    Ntabeni. Oprah raised the requirement and follows the Acts; the other two
    run the filings.

    A table rather than a hardcoded list because this is exactly the kind of
    access that changes, and because an editor who leaves must be removable
    without a release.

    THE CONFLICT THIS CREATES, ON PURPOSE AND WITH EYES OPEN: Kago is also the
    PREPARER for PAYE and OWHT. Someone who can move their own deadline can make
    a late filing look on time, which would hollow out the breach flag. The CFO
    accepted that trade for speed, so the control moved rather than disappearing:
    every change is logged with who/when/old/new and a written reason
    (TaxComplianceTask.date_change_* fields), a change CANNOT clear a breach that
    has already been raised, and the CFO is emailed on every statutory date move.
    Visible beats forbidden here — but only because it IS visible. Do not remove
    the log or the CFO notice and leave the permission.
    """
    # OneToOne, not ForeignKey(unique=True) — Django's own system check asks for
    # this (models.W342) and it says the intent plainly: one row per person.
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name='tax_calendar_edit_right',
    )
    note = models.CharField(
        max_length=200, blank=True, default='',
        help_text='Why this person has the right. Shown to the CFO on review.',
    )
    added_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='tax_calendar_editors_added',
    )

    class Meta(BaseModel.Meta):
        ordering = ['user__first_name']

    def __str__(self):
        return self.user.get_full_name() or self.user.username
