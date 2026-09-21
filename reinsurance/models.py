"""
reinsurance/models.py

Reinsurance module — v1 backbone.

Models:
  • Reinsurer            counterparty (Munich Re, Swiss Re, Africa Re, ...)
  • ReinsuranceTreaty    a contract: type, period, share %, retention/limit
  • Cession              premium ceded to reinsurer (per-policy or per-bordereau)
  • ReinsuranceRecovery  claim recovery from reinsurer
  • BordereauImport      header for a CSV/XLSX bordereau upload from broker

GL accounts used (already in setup_chart_of_accounts):
  • 1230  Reinsurance receivable        — recoveries owed by the reinsurer
  • 2120  Reinsurance premium payable   — cessions owed to the reinsurer
  • 4200  Reinsurance premium ceded     — contra-revenue (P&L)
  • 5200  Claims recovered from reinsurers — contra-expense (P&L)

Cession JE:
    DR  4200 Reinsurance premium ceded
    CR  2120 Reinsurance premium payable

Recovery JE:
    DR  1230 Reinsurance receivable
    CR  5200 Claims recovered from reinsurers

This is a v1 backbone. Specifically NOT yet built:
  • Bordereau line model + line-level matching (header-only import for now)
  • Reinsurance commission split out from cession
  • Treaty layer / non-proportional structures (XL, surplus) calc helpers
  • IFRS 17 §63 reinsurance contract roll-forward report
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models, transaction
from django.utils import timezone

from core.models import AuditableMixin, BaseModel


ZERO = Decimal('0.00')


# ---------------------------------------------------------------------------
# Number generators
# ---------------------------------------------------------------------------

def _next_cession_number():
    year = timezone.now().year
    prefix = f'CES-{year}-'
    with transaction.atomic():
        last = (
            Cession.objects.select_for_update()
            .filter(cession_number__startswith=prefix)
            .order_by('-cession_number')
            .values_list('cession_number', flat=True).first()
        )
        seq = int(last.rsplit('-', 1)[-1]) + 1 if last else 1
        return f'{prefix}{seq:06d}'


def _next_recovery_number():
    year = timezone.now().year
    prefix = f'RCR-{year}-'
    with transaction.atomic():
        last = (
            ReinsuranceRecovery.objects.select_for_update()
            .filter(recovery_number__startswith=prefix)
            .order_by('-recovery_number')
            .values_list('recovery_number', flat=True).first()
        )
        seq = int(last.rsplit('-', 1)[-1]) + 1 if last else 1
        return f'{prefix}{seq:06d}'


def _next_bordereau_number():
    year = timezone.now().year
    prefix = f'BDX-{year}-'
    with transaction.atomic():
        last = (
            BordereauImport.objects.select_for_update()
            .filter(bordereau_number__startswith=prefix)
            .order_by('-bordereau_number')
            .values_list('bordereau_number', flat=True).first()
        )
        seq = int(last.rsplit('-', 1)[-1]) + 1 if last else 1
        return f'{prefix}{seq:06d}'


# ---------------------------------------------------------------------------
# Reinsurer
# ---------------------------------------------------------------------------

class Reinsurer(AuditableMixin, BaseModel):
    """A reinsurance counterparty."""

    name = models.CharField(max_length=200, unique=True)
    short_code = models.CharField(
        max_length=20, unique=True,
        help_text='Short code used in JE descriptions and reports (e.g. MUNICH_RE).',
    )
    country = models.CharField(max_length=2, default='BW',
                                help_text='ISO 3166-1 alpha-2.')
    credit_rating = models.CharField(
        max_length=10, blank=True,
        help_text='Latest counterparty rating (e.g. AA-, A+).',
    )
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    # ---- Counterparty identity (Arun P. Iyer control brief, 15-Sep-2026) ----
    # Every field below is optional so the rows already in the table stay valid.
    # `name` remains the display name; these record who the counterparty legally
    # IS, which is what a regulator and a KYC file ask for.
    legal_name = models.CharField(max_length=240, blank=True, default='')
    registered_name = models.CharField(max_length=240, blank=True, default='')
    trading_name = models.CharField(max_length=240, blank=True, default='')
    carrier_group = models.CharField(
        max_length=240, blank=True, default='',
        help_text='Normalised security / parent group. Concentration is measured '
                  'per group as well as per carrier — two panel lines on the same '
                  'group are one exposure.')
    domicile = models.CharField(max_length=2, blank=True, default='',
                               help_text='ISO 3166-1 alpha-2 country of domicile.')
    registration_number = models.CharField(max_length=80, blank=True, default='')
    licence_number = models.CharField(max_length=80, blank=True, default='')
    regulator = models.CharField(max_length=160, blank=True, default='')
    address = models.TextField(blank=True, default='')
    tax_id = models.CharField(max_length=80, blank=True, default='')
    broker = models.CharField(max_length=200, blank=True, default='',
                             help_text='Placing broker, where the line comes '
                                       'through one.')

    #: What we are onboarding them FOR. Multi-select, stored as a list of
    #: OnboardingPurpose values. A counterparty approved for facultative is not
    #: thereby approved for treaty.
    class OnboardingPurpose(models.TextChoices):
        FACULTATIVE = 'facultative', 'Facultative'
        TREATY = 'treaty', 'Treaty'
        RETROCESSION = 'retrocession', 'Retrocession'
        OTHER = 'other', 'Other'

    onboarding_purposes = models.JSONField(
        default=list, blank=True,
        help_text='List of OnboardingPurpose values this counterparty is '
                  'approved for.')

    effective_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    next_review_date = models.DateField(null=True, blank=True)

    # ---- Approval / eligibility -------------------------------------------
    class ApprovalStatus(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        PENDING_UW_MANAGER = 'pending_uw_manager', 'Pending Underwriting Manager'
        PENDING_COMPLIANCE = 'pending_compliance', 'Pending Compliance'
        PENDING_PRINCIPAL = 'pending_principal', 'Pending Principal review'
        PENDING_CEO = 'pending_ceo', 'Pending CEO'
        APPROVED = 'approved', 'Approved / Eligible'
        RETURNED = 'returned', 'Returned for correction'
        REJECTED = 'rejected', 'Rejected'
        SUSPENDED = 'suspended', 'Suspended'
        EXPIRED = 'expired', 'Expired'

    #: Statuses a counterparty may be SELECTED under on a new placement.
    #: Deliberately a single-member set: only an explicit approval counts, and
    #: "not yet assessed" must never read as "fine to use".
    PLACEABLE_STATUSES = (ApprovalStatus.APPROVED,)

    approval_status = models.CharField(
        max_length=24, choices=ApprovalStatus.choices,
        default=ApprovalStatus.DRAFT, db_index=True)
    suspension_reason = models.TextField(
        blank=True, default='',
        help_text='Mandatory when suspending or rejecting — enforced in the '
                  'service layer, never optional in practice.')
    submitted_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'{self.short_code} — {self.name}'

    # -- eligibility ---------------------------------------------------------
    def is_expired(self, on=None) -> bool:
        """True when the approval has run out on the given date.

        Date-driven, but never silently rewrites the stored status: a persisted
        SUSPENDED or REJECTED stays what a person decided it was.
        """
        on = on or timezone.localdate()
        return bool(self.expiry_date and self.expiry_date < on)

    def placement_block_reason(self, on=None, *, require_evidence: bool = True) -> str:
        """Why this counterparty may NOT be put on a new placement — or ''.

        Returns a sentence, not a boolean, because "you cannot use them" without
        a reason is what makes people work around a control.

        `require_evidence=False` drops the KYC half and ONLY the KYC half — the
        approval, active and expiry checks always apply. It exists for the
        legacy import path, where a signed slip placed before this module
        existed is being recorded rather than made: the brief is explicit that
        missing evidence must not stop an already-imported record. Nothing that
        creates a NEW placement passes it.
        """
        if not self.is_active:
            return f'{self.name} is not active in Omni.'
        if self.approval_status not in self.PLACEABLE_STATUSES:
            label = self.get_approval_status_display()
            return (f'{self.name} is not approved for placement — the onboarding '
                    f'status is "{label}".')
        if self.is_expired(on):
            return (f'{self.name}\'s approval expired on '
                    f'{self.expiry_date.isoformat()} and must be reviewed again.')
        # KYC evidence (control brief §5, 16-Sep-2026). Required documents that
        # are missing, unverified or out of date block a NEW placement. Note
        # what this does NOT do: it runs when somebody puts the counterparty on
        # a new allocation, not when an imported one is serviced — a legacy row
        # already on the books keeps posting while its file is remediated.
        if require_evidence:
            gaps = evidence_gaps(self, on)
            if gaps:
                return (f'{self.name} cannot be put on a new placement until the '
                        f'KYC file is complete: {"; ".join(gaps)}.')
        return ''

    def may_be_placed(self, on=None) -> bool:
        return not self.placement_block_reason(on)


# ---------------------------------------------------------------------------
# Treaty
# ---------------------------------------------------------------------------

class ReinsuranceTreaty(AuditableMixin, BaseModel):
    """A reinsurance contract.

    v1 supports proportional treaties (Quota Share, Surplus). Non-proportional
    (XL, Stop-Loss) is allowed at the type level but the calc helpers are
    flagged TBD.
    """

    class TreatyType(models.TextChoices):
        QUOTA_SHARE = 'quota_share',  'Quota Share (proportional)'
        SURPLUS     = 'surplus',      'Surplus (proportional)'
        XL          = 'xl',           'Excess of Loss (non-proportional)'
        STOP_LOSS   = 'stop_loss',    'Stop Loss (non-proportional)'
        FACULTATIVE = 'facultative',  'Facultative'

    class Status(models.TextChoices):
        DRAFT     = 'draft',     'Draft'
        ACTIVE    = 'active',    'Active'
        EXPIRED   = 'expired',   'Expired'
        CANCELLED = 'cancelled', 'Cancelled'

    treaty_number = models.CharField(max_length=40, unique=True)
    description = models.CharField(max_length=200)
    reinsurer = models.ForeignKey(
        Reinsurer, on_delete=models.PROTECT, related_name='treaties',
    )
    treaty_type = models.CharField(max_length=20, choices=TreatyType.choices)
    line_of_business = models.CharField(
        max_length=80,
        help_text='Motor / Property / Liability / All Lines / etc.',
    )
    inception_date = models.DateField()
    expiry_date = models.DateField()
    currency_code = models.ForeignKey(
        'core.Currency', on_delete=models.PROTECT, default='BWP',
    )

    # Proportional terms
    cession_share_percent = models.DecimalField(
        max_digits=6, decimal_places=4, null=True, blank=True,
        validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('100'))],
        help_text='For QS/Surplus: % of each risk ceded.',
    )
    commission_percent = models.DecimalField(
        max_digits=6, decimal_places=4, null=True, blank=True,
        validators=[MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('100'))],
        help_text='Reinsurance commission paid back to us.',
    )

    # Non-proportional terms
    retention_amount = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True,
        help_text='For XL: the retention layer below which we keep the loss.',
    )
    limit_amount = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True,
        help_text='For XL: the upper limit of the layer.',
    )

    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.DRAFT,
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-inception_date', 'treaty_number']

    def __str__(self):
        return f'{self.treaty_number} — {self.description}'

    def clean(self):
        if self.expiry_date and self.inception_date and self.expiry_date < self.inception_date:
            raise ValidationError('Expiry date must be on or after inception date.')


# ---------------------------------------------------------------------------
# Cession
# ---------------------------------------------------------------------------

class Cession(AuditableMixin, BaseModel):
    """A premium cession to a reinsurer.

    Can be raised standalone or as part of a bordereau import. Posting to the
    GL is via the matching service function — see services.post_cession.
    """

    class Status(models.TextChoices):
        DRAFT  = 'draft',  'Draft'
        POSTED = 'posted', 'Posted'
        VOIDED = 'voided', 'Voided'

    cession_number = models.CharField(max_length=20, unique=True, editable=False)
    treaty = models.ForeignKey(
        ReinsuranceTreaty, on_delete=models.PROTECT, related_name='cessions',
    )
    cession_date = models.DateField(default=timezone.localdate)
    policy_reference = models.CharField(
        max_length=120, blank=True,
        help_text='Graphite policy number this cession relates to (optional).',
    )
    risk_description = models.CharField(max_length=200, blank=True)

    gross_premium = models.DecimalField(max_digits=18, decimal_places=2)
    ceded_premium = models.DecimalField(max_digits=18, decimal_places=2)
    commission_amount = models.DecimalField(
        max_digits=18, decimal_places=2, default=ZERO,
        help_text='Reinsurance commission earned (net off the cession).',
    )

    bordereau = models.ForeignKey(
        'BordereauImport', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='cessions',
    )

    # ----- Auto-cession (CFO directive — cession_service.run_cession_pass) ----
    # When a Cession is generated by the auto-cession pass off a posted
    # customer invoice we keep the link back to the source invoice. This
    # FK is also the idempotency anchor — (invoice, treaty) is enforced
    # unique below so re-running the pass is safe.
    invoice = models.ForeignKey(
        'billing.Invoice', null=True, blank=True,
        on_delete=models.PROTECT, related_name='reinsurance_cessions',
        help_text='Source customer invoice this cession was generated from '
                  'by the auto-cession pass. NULL for manual / bordereau cessions.',
    )
    share_percent = models.DecimalField(
        max_digits=6, decimal_places=4, null=True, blank=True,
        help_text='Cession % applied at the time of the auto-cession run. '
                  'Snapshot from treaty.cession_share_percent.',
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    journal_entry = models.OneToOneField(
        'ledger.JournalEntry', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='reinsurance_cession',
    )
    posted_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='cessions_posted',
    )
    posted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-cession_date', '-cession_number']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['treaty', 'status']),
            models.Index(fields=['invoice', 'treaty']),
        ]
        constraints = [
            # Idempotency for the auto-cession pass: one Cession per
            # (invoice, treaty) pair. NULL invoices (manual / bordereau)
            # are not constrained — the UniqueConstraint with a non-NULL
            # condition lets the pass re-run safely while leaving legacy
            # rows alone.
            models.UniqueConstraint(
                fields=['invoice', 'treaty'],
                condition=models.Q(invoice__isnull=False),
                name='uniq_cession_invoice_treaty',
            ),
        ]

    def __str__(self):
        return f'{self.cession_number} — {self.treaty.treaty_number}'

    def save(self, *args, **kwargs):
        if not self.cession_number:
            self.cession_number = _next_cession_number()
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------

class ReinsuranceRecovery(AuditableMixin, BaseModel):
    """A claim recovery from a reinsurer."""

    class Status(models.TextChoices):
        DRAFT     = 'draft',     'Draft'
        POSTED    = 'posted',    'Posted'
        SETTLED   = 'settled',   'Settled (cash received)'
        VOIDED    = 'voided',    'Voided'

    recovery_number = models.CharField(max_length=20, unique=True, editable=False)
    treaty = models.ForeignKey(
        ReinsuranceTreaty, on_delete=models.PROTECT, related_name='recoveries',
    )
    recovery_date = models.DateField(default=timezone.localdate)
    claim_reference = models.CharField(
        max_length=120,
        help_text='Graphite claim number (mandatory for traceability).',
    )

    gross_loss = models.DecimalField(max_digits=18, decimal_places=2)
    ceded_recovery = models.DecimalField(max_digits=18, decimal_places=2)

    notes = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    journal_entry = models.OneToOneField(
        'ledger.JournalEntry', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='reinsurance_recovery',
    )
    posted_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='recoveries_posted',
    )
    posted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-recovery_date', '-recovery_number']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['treaty', 'status']),
        ]

    def __str__(self):
        return f'{self.recovery_number} — {self.claim_reference}'

    def save(self, *args, **kwargs):
        if not self.recovery_number:
            self.recovery_number = _next_recovery_number()
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# Bordereau import
# ---------------------------------------------------------------------------

class BordereauImport(AuditableMixin, BaseModel):
    """Header for a monthly cession bordereau uploaded by the broker.

    v1 stores the import metadata + raw file reference; line-level breakdown
    is created as Cession rows linked back via FK. A future v2 will add a
    BordereauLine model with line-level matching.
    """

    class Status(models.TextChoices):
        UPLOADED  = 'uploaded',  'Uploaded'
        PARSED    = 'parsed',    'Parsed'
        COMMITTED = 'committed', 'Committed'
        REJECTED  = 'rejected',  'Rejected'

    bordereau_number = models.CharField(max_length=20, unique=True, editable=False)
    treaty = models.ForeignKey(
        ReinsuranceTreaty, on_delete=models.PROTECT, related_name='bordereau_imports',
    )
    period_start = models.DateField()
    period_end = models.DateField()
    received_date = models.DateField(default=timezone.localdate)

    file_name = models.CharField(max_length=240, blank=True)
    line_count = models.PositiveIntegerField(default=0)
    total_gross_premium = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    total_ceded_premium = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    total_commission = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)

    status = models.CharField(max_length=12, choices=Status.choices, default=Status.UPLOADED)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-received_date', '-bordereau_number']

    def __str__(self):
        return (
            f'{self.bordereau_number} — {self.treaty.treaty_number} '
            f'{self.period_start}→{self.period_end}'
        )

    def save(self, *args, **kwargs):
        if not self.bordereau_number:
            self.bordereau_number = _next_bordereau_number()
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# 11-Year Historical Treaty Performance — LOCKED reference dataset
# ---------------------------------------------------------------------------
# CFO directive 2026-08-14. A permanent, locked record of the reinsurance
# programme's 11-year (UWY 2014/15–2025/26) performance, feeding the
# /reinsurance/history board dashboard. Requirements from the CFO:
#   • the data must STAY — it cannot be accidentally deleted (by staff OR by the AI);
#   • amending it requires the user's own password (the same login as HRIS);
#   • every amendment keeps a snapshot of the old numbers (nothing is ever lost);
#   • the dashboard reads from THIS saved copy, never from the source Excel files.
# It is a single-row (singleton) dataset; there is deliberately no delete path.

class ReinsuranceHistory(AuditableMixin, BaseModel):
    """Singleton holding the current, locked 11-year treaty-performance dataset."""

    SINGLETON_KEY = 'PRIMARY'

    key = models.CharField(max_length=20, unique=True, default=SINGLETON_KEY, editable=False)
    data = models.JSONField(default=dict)          # structured figures (see seeder)
    locked = models.BooleanField(default=True)     # read-only unless a password amend is made
    version = models.PositiveIntegerField(default=1)
    last_amended_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='reinsurance_history_amendments',
    )
    last_amended_at = models.DateTimeField(null=True, blank=True)
    source_note = models.CharField(max_length=300, blank=True)

    class Meta:
        verbose_name = 'Reinsurance 11-year history'
        verbose_name_plural = 'Reinsurance 11-year history'

    def __str__(self):
        return f'Reinsurance 11-year history v{self.version} (locked={self.locked})'

    @classmethod
    def current(cls):
        return cls.objects.filter(key=cls.SINGLETON_KEY).first()


class ReinsuranceHistorySnapshot(BaseModel):
    """Immutable copy of the dataset as it was BEFORE each amendment — so the
    old numbers are never lost and every change is auditable."""

    version = models.PositiveIntegerField()
    data = models.JSONField(default=dict)
    amended_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='reinsurance_history_snapshots',
    )
    amended_at = models.DateTimeField(auto_now_add=True)
    note = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ['-version']

    def __str__(self):
        return f'Reinsurance history snapshot v{self.version}'


# ---------------------------------------------------------------------------
# Reinsurer security assessment  (Arun P. Iyer control brief, 15-Sep-2026)
#
# One assessment per reinsurer per review — kept as history, never overwritten,
# because "what did we know when we placed it?" is the question an ERM review
# actually asks.
#
# THE RULE THAT MATTERS: a national-scale rating is NOT comparable to an
# international-scale one. AA on a national scale can sit below BBB
# internationally. The scale is therefore a required field beside the rating,
# and nothing in this module ranks two ratings against each other without it.
# ---------------------------------------------------------------------------

class ReinsurerSecurityAssessment(AuditableMixin, BaseModel):
    """A dated view of one counterparty's financial security."""

    class Scale(models.TextChoices):
        INTERNATIONAL = 'international', 'International scale'
        NATIONAL = 'national', 'National scale'
        UNKNOWN = 'unknown', 'Scale not stated'

    class Tier(models.TextChoices):
        TIER_1 = 'tier_1', 'Tier 1'
        TIER_2 = 'tier_2', 'Tier 2'
        TIER_3 = 'tier_3', 'Tier 3'
        WATCH = 'watch', 'Watch list'
        UNRATED = 'unrated', 'Unrated'

    reinsurer = models.ForeignKey(
        Reinsurer, on_delete=models.PROTECT, related_name='security_assessments')

    rating = models.CharField(max_length=16, blank=True, default='')
    rating_agency = models.CharField(max_length=80, blank=True, default='')
    rating_scale = models.CharField(
        max_length=16, choices=Scale.choices, default=Scale.UNKNOWN,
        help_text='Never compare a national-scale rating with an international '
                  'one — they are different measures.')
    outlook = models.CharField(max_length=40, blank=True, default='')
    rating_date = models.DateField(null=True, blank=True)
    evidence_date = models.DateField(
        null=True, blank=True,
        help_text='When we last SAW the evidence, which is not the same as when '
                  'the agency issued the rating.')

    internal_tier = models.CharField(
        max_length=12, choices=Tier.choices, default=Tier.UNRATED)
    verified = models.BooleanField(
        default=False,
        help_text='Set only when a person has checked the rating against source '
                  'evidence. Unverified must never render as verified.')
    conflicts = models.TextField(
        blank=True, default='',
        help_text='Where two sources disagree, record BOTH here rather than '
                  'picking one silently.')

    audited_financial_years = models.JSONField(
        default=list, blank=True,
        help_text='Financial years for which we hold audited statements, e.g. '
                  '[2024, 2025]. Paul Beka reported these incomplete for the '
                  'offshore carriers — the gap stays visible.')
    solvency_ratio = models.DecimalField(
        max_digits=9, decimal_places=4, null=True, blank=True)
    capital_amount = models.DecimalField(
        max_digits=20, decimal_places=2, null=True, blank=True)
    capital_currency = models.CharField(max_length=3, blank=True, default='')

    panel_provider = models.CharField(max_length=200, blank=True, default='')
    panel_section = models.CharField(max_length=200, blank=True, default='')
    participation_share = models.DecimalField(
        max_digits=7, decimal_places=4, null=True, blank=True,
        help_text='Percent share on this panel section. NULL means the panel '
                  'did not state a share — four of the supplied panels did not '
                  '— and NULL must render as unknown, never as zero.')
    cover_from = models.DateField(null=True, blank=True)
    cover_to = models.DateField(null=True, blank=True)

    source_note = models.CharField(
        max_length=300, blank=True, default='',
        help_text='Where this came from: file, sheet and row.')
    assessed_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta(BaseModel.Meta):
        ordering = ['-rating_date', '-created_at']
        verbose_name = 'reinsurer security assessment'
        indexes = [
            models.Index(fields=['reinsurer', '-rating_date']),
            models.Index(fields=['internal_tier']),
        ]

    def __str__(self):
        return f'{self.reinsurer.short_code} {self.rating or "unrated"} ({self.rating_scale})'

    @property
    def is_comparable_internationally(self) -> bool:
        """Only an international-scale rating may be ranked against the panel."""
        return self.rating_scale == self.Scale.INTERNATIONAL and bool(self.rating)


# ---------------------------------------------------------------------------
# Approval transitions  (segregation of duties)
#
# Every move is a row: who, when, from, to, and why. The reason is mandatory on
# a return, rejection or suspension — an unexplained block is the thing people
# escalate around.
# ---------------------------------------------------------------------------

class ReinsurerApprovalTransition(BaseModel):
    """One state change on a reinsurer's onboarding."""

    # PROTECT, not CASCADE: deleting a counterparty must not take the record of
    # who approved it with it. ReinsurerViewSet is a stock ModelViewSet on
    # IsAuthenticated, so any signed-in user could otherwise DELETE a draft and
    # its whole approval history.
    reinsurer = models.ForeignKey(
        Reinsurer, on_delete=models.PROTECT, related_name='approval_transitions')
    from_status = models.CharField(max_length=24)
    to_status = models.CharField(max_length=24)
    actor = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    actor_email = models.CharField(
        max_length=254, blank=True, default='',
        help_text='Captured at the time. A user row can be renamed later; the '
                  'audit trail must still say who acted.')
    comment = models.TextField(blank=True, default='')
    created_at_local = models.DateTimeField(default=timezone.now)

    class Meta(BaseModel.Meta):
        ordering = ['created_at_local']
        verbose_name = 'reinsurer approval transition'
        indexes = [models.Index(fields=['reinsurer', 'created_at_local'])]

    def __str__(self):
        return (f'{self.reinsurer.short_code}: {self.from_status} → '
                f'{self.to_status} by {self.actor_email or "system"}')


# ---------------------------------------------------------------------------
# FAC risk register  (Arun P. Iyer control brief §6)
#
# DELIBERATELY SEPARATE from Cession/ReinsuranceTreaty. Facultative placements
# are policy-specific and one policy can carry several slips; folding them into
# the treaty tables would distort treaty calculations that already post to the
# GL. Nothing here writes a journal.
#
# The rule that is easiest to get wrong, so it is a stored field and not a
# derivation: capacity that was NOT placed stays RETAINED by Alpha Direct. It
# must never be presented as ceded.
# ---------------------------------------------------------------------------

class FacRiskExposure(AuditableMixin, BaseModel):
    """One facultative placement on one policy/risk."""

    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        PLACED = 'placed', 'Placed'
        PARTIALLY_PLACED = 'partially_placed', 'Partially placed'
        UNPLACED = 'unplaced', 'Unplaced — retained'
        EXPIRED = 'expired', 'Expired'
        CANCELLED = 'cancelled', 'Cancelled'

    reference = models.CharField(max_length=40, unique=True)

    # Graphite is the policy system; these are its identifiers, kept as given so
    # a row can always be traced back to source.
    graphite_policy_id = models.CharField(max_length=60, blank=True, default='',
                                          db_index=True)
    graphite_risk_id = models.CharField(max_length=60, blank=True, default='')
    policy_number = models.CharField(max_length=60, blank=True, default='',
                                     db_index=True)

    insured_name = models.CharField(
        max_length=240, blank=True, default='',
        help_text='PII — shown on screen to permitted users, never sent to an '
                  'AI pipeline or an unfiltered export.')
    risk_description = models.TextField(blank=True, default='')
    regulatory_class = models.CharField(max_length=120, blank=True, default='')
    risk_address = models.TextField(
        blank=True, default='',
        help_text='Accumulation is tested per address/situation where the '
                  'source data supports it.')

    currency_code = models.CharField(max_length=3, default='BWP')
    gross_sum_insured = models.DecimalField(max_digits=20, decimal_places=2,
                                            default=ZERO)
    gross_premium = models.DecimalField(max_digits=20, decimal_places=2,
                                        default=ZERO)
    net_retention = models.DecimalField(
        max_digits=20, decimal_places=2, default=ZERO,
        help_text='What Alpha Direct keeps before any facultative placement.')
    autofac_capacity = models.DecimalField(max_digits=20, decimal_places=2,
                                           default=ZERO)
    fac_placed_amount = models.DecimalField(max_digits=20, decimal_places=2,
                                            default=ZERO)
    unplaced_retained_amount = models.DecimalField(
        max_digits=20, decimal_places=2, default=ZERO,
        help_text='Capacity sought but NOT placed. This sits on Alpha Direct\'s '
                  'own balance sheet and is flagged, never shown as ceded.')
    ceded_premium = models.DecimalField(max_digits=20, decimal_places=2,
                                        default=ZERO)
    ceded_commission = models.DecimalField(max_digits=20, decimal_places=2,
                                           default=ZERO)

    slip_reference = models.CharField(max_length=120, blank=True, default='')
    evidence_note = models.CharField(max_length=300, blank=True, default='')

    placement_date = models.DateField(null=True, blank=True)
    effective_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)

    status = models.CharField(max_length=20, choices=Status.choices,
                              default=Status.DRAFT, db_index=True)
    status_override_reason = models.CharField(
        max_length=300, blank=True, default='',
        help_text='Active/expired is normally date-driven. When a person '
                  'overrides it, the reason is recorded and the override is '
                  'what stands.')
    status_overridden = models.BooleanField(default=False)

    # Import provenance — §2 of the brief.
    source_system = models.CharField(max_length=60, blank=True, default='')
    source_file = models.CharField(max_length=240, blank=True, default='')
    source_sheet = models.CharField(max_length=120, blank=True, default='')
    source_row = models.CharField(max_length=40, blank=True, default='')
    source_key = models.CharField(
        max_length=200, blank=True, default='', db_index=True,
        help_text='Idempotency key from the source. Re-importing the same row '
                  'updates it instead of creating a second exposure.')
    imported_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    import_warnings = models.JSONField(default=list, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ['-effective_date', 'reference']
        verbose_name = 'FAC risk exposure'
        verbose_name_plural = 'FAC risk exposures'
        constraints = [
            models.UniqueConstraint(
                fields=['source_system', 'source_key'],
                condition=models.Q(source_key__gt=''),
                name='uniq_fac_exposure_source_key'),
        ]
        indexes = [
            models.Index(fields=['status', 'expiry_date']),
            models.Index(fields=['policy_number']),
            models.Index(fields=['regulatory_class']),
        ]

    def __str__(self):
        return f'{self.reference} — {self.policy_number or "no policy"}'

    def is_expired(self, on=None) -> bool:
        """Date-driven, unless a person has overridden the status."""
        if self.status_overridden:
            return self.status == self.Status.EXPIRED
        on = on or timezone.localdate()
        return bool(self.expiry_date and self.expiry_date < on)

    def is_active(self, on=None) -> bool:
        if self.status in (self.Status.CANCELLED, self.Status.DRAFT):
            return False
        if self.status_overridden:
            return self.status != self.Status.EXPIRED
        on = on or timezone.localdate()
        if self.effective_date and self.effective_date > on:
            return False
        return not self.is_expired(on)

    @property
    def allocated_amount(self):
        """What the reinsurer allocations actually add up to."""
        return sum((a.allocated_amount for a in self.allocations.all()), ZERO)

    def allocation_variance(self):
        """fac_placed_amount minus what the allocations account for.

        Non-zero means the slip and the panel disagree. Surfaced as an exception
        rather than quietly reconciled — the brief asks for gaps to stay visible.
        """
        return (self.fac_placed_amount or ZERO) - self.allocated_amount


class FacReinsurerAllocation(AuditableMixin, BaseModel):
    """One reinsurer's share of one facultative placement."""

    exposure = models.ForeignKey(
        FacRiskExposure, on_delete=models.CASCADE, related_name='allocations')
    reinsurer = models.ForeignKey(
        Reinsurer, on_delete=models.PROTECT, related_name='fac_allocations')

    share_percent = models.DecimalField(
        max_digits=7, decimal_places=4, null=True, blank=True,
        validators=[MinValueValidator(Decimal('0')),
                    MaxValueValidator(Decimal('100'))],
        help_text='NULL where the panel did not state a share — renders as '
                  'unknown, never as zero.')
    allocated_amount = models.DecimalField(max_digits=20, decimal_places=2,
                                           default=ZERO)
    allocated_premium = models.DecimalField(max_digits=20, decimal_places=2,
                                            default=ZERO)
    commission_amount = models.DecimalField(max_digits=20, decimal_places=2,
                                            default=ZERO)
    slip_reference = models.CharField(max_length=120, blank=True, default='')
    signed_date = models.DateField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ['-allocated_amount']
        verbose_name = 'FAC reinsurer allocation'
        indexes = [
            models.Index(fields=['reinsurer']),
            models.Index(fields=['exposure']),
        ]

    def __str__(self):
        return f'{self.exposure.reference} → {self.reinsurer.short_code}'

    def clean(self):
        """An unapproved counterparty cannot be put on a placement."""
        super().clean()
        self._assert_placeable()

    def _assert_placeable(self):
        if self.reinsurer_id:
            # `_legacy_import` is set ONLY by the FAC master loader, on the
            # in-memory object, exactly the way payroll's `_allow_lock_bypass`
            # works. It relaxes the KYC evidence check and nothing else: an
            # unapproved, inactive or expired counterparty is still refused.
            # Without it, re-running the loader for a signed FY27 slip placed
            # before this module existed would be refused for paperwork the
            # brief says must never stop an imported record.
            reason = self.reinsurer.placement_block_reason(
                require_evidence=not getattr(self, '_legacy_import', False))
            if reason:
                raise ValidationError({'reinsurer': reason})

    def save(self, *args, **kwargs):
        """The block has to live on save(), not only on clean().

        ``clean()`` runs from a ModelForm and from ``full_clean()`` — and from
        nothing else. A management command, a shell import or a serializer that
        calls ``.save()`` walked straight past it and stored an allocation
        against a counterparty nobody had approved (proved by the Fable review,
        15-Sep-2026: two allocations written to a DRAFT reinsurer). The brief
        asks for the block at model AND service level; this is the model half.

        ``bulk_create`` still bypasses this, as it bypasses every save() in
        Django — so allocations are written one at a time, and the import path
        goes through ``onboarding.assert_placeable`` first.
        """
        self._assert_placeable()
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# KYC / evidence register
# ---------------------------------------------------------------------------

class ReinsurerDocument(AuditableMixin, BaseModel):
    """One piece of evidence held on file for a counterparty.

    Arun P. Iyer's control brief §5 (15-Sep-2026). The source story is Paul
    Beka's: incomplete offshore financials, and Munich Re / Kuwait Re / GIC Re
    information still outstanding. The whole point of this register is that a
    gap stays VISIBLE — a counterparty with nothing on file must never read the
    same as one whose file is complete.

    Two rules that shape everything here:

    * **A missing or expired required document blocks a NEW onboarding and a
      NEW placement — and nothing else.** An already-imported legacy treaty,
      cession or FAC row keeps posting exactly as it did. Those records
      pre-date this register; blocking them would stop ordinary servicing to
      punish a paperwork gap nobody has had a chance to close yet.
    * **Uploaded is not verified.** A document arrives PENDING. Only Compliance
      marking it VERIFIED counts towards the checklist, because "somebody
      attached a PDF" is not evidence that anybody read it.

    `/media/` IS NOT SERVED in this deployment — `self.file.url` 404s in
    production. Everything is streamed through
    `reinsurance.document_views.document_download`; nothing here is ever
    rendered as a public URL.
    """

    class Kind(models.TextChoices):
        INCORPORATION      = 'incorporation',      'Certificate of incorporation / registration'
        LICENCE            = 'licence',            'Licence / regulator proof'
        ADDRESS            = 'address',            'Proof of business address'
        DIRECTORS_UBO      = 'directors_ubo',      'Directors / ultimate beneficial owners'
        IDENTITY           = 'identity',           'Identity documents (where legally required)'
        BOARD_RESOLUTION   = 'board_resolution',   'Board resolution / signing authority'
        ORG_CHART          = 'org_chart',          'Organisation chart'
        TAX                = 'tax',                'Tax registration'
        AML_SANCTIONS_PEP  = 'aml_sanctions_pep',  'AML / sanctions / PEP screening result'
        AUDITED_FINANCIALS = 'audited_financials', 'Audited financial statements'
        RATING_EVIDENCE    = 'rating_evidence',    'Rating evidence'
        BROKER_PANEL       = 'broker_panel',       'Broker security panel'
        SLIP               = 'slip',               'Signed treaty / FAC / retrocession slip'
        BANK_VERIFICATION  = 'bank_verification',  'Bank account verification'
        OTHER              = 'other',              'Other compliance evidence'

    #: The evidence Compliance cannot sign a NEW counterparty off without.
    #: Deliberately a SUBSET of the checklist, held in one place so the rule can
    #: be read and changed by one edit. The other kinds are still tracked and
    #: still shown as gaps — they simply do not hold up the approval.
    #: Rating evidence is not here because the security assessment already
    #: carries its own check in `onboarding.missing_prerequisites`.
    REQUIRED_KINDS = (
        Kind.INCORPORATION,
        Kind.LICENCE,
        Kind.DIRECTORS_UBO,
        Kind.AML_SANCTIONS_PEP,
        Kind.AUDITED_FINANCIALS,
    )

    class Verification(models.TextChoices):
        PENDING  = 'pending',  'Uploaded — not yet verified'
        VERIFIED = 'verified', 'Verified'
        REJECTED = 'rejected', 'Rejected'

    reinsurer = models.ForeignKey(Reinsurer, on_delete=models.CASCADE,
                                 related_name='documents')
    kind = models.CharField(max_length=24, choices=Kind.choices, db_index=True)
    title = models.CharField(max_length=200, blank=True, default='')
    file = models.FileField(upload_to='reinsurer_documents/')
    original_filename = models.CharField(max_length=255, blank=True, default='')
    content_type = models.CharField(
        max_length=100, blank=True, default='',
        help_text='Derived from the EXTENSION on upload, never taken from the '
                  'browser — a caller-supplied content type is caller-supplied '
                  'control over what the browser does with the bytes.')
    size_bytes = models.BigIntegerField(default=0)

    issue_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(
        null=True, blank=True,
        help_text='Blank means it does not expire. A past date blocks a NEW '
                  'onboarding or placement, never an existing record.')
    source = models.CharField(
        max_length=200, blank=True, default='',
        help_text='Who provided it — the reinsurer, the broker, a registry.')

    uploaded_by = models.ForeignKey(User, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name='+')
    verification_status = models.CharField(
        max_length=10, choices=Verification.choices, default=Verification.PENDING,
        db_index=True)
    verified_by = models.ForeignKey(User, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name='+')
    verified_at = models.DateTimeField(null=True, blank=True)
    verification_note = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['reinsurer__name', 'kind', '-created_at']
        verbose_name = 'Reinsurer Document'
        verbose_name_plural = 'Reinsurer Documents'
        indexes = [
            models.Index(fields=['reinsurer', 'kind'],
                         name='re_doc_counterparty_kind_idx'),
        ]

    def __str__(self):
        return f'{self.reinsurer.short_code} — {self.get_kind_display()}'

    def is_expired(self, on=None) -> bool:
        on = on or timezone.localdate()
        return bool(self.expiry_date and self.expiry_date < on)

    def counts_as_evidence(self, on=None) -> bool:
        """Verified and in date. Anything else is a gap, however it looks."""
        return (self.verification_status == self.Verification.VERIFIED
                and not self.is_expired(on))


def evidence_gaps(reinsurer, on=None) -> list[str]:
    """The required evidence this counterparty is missing or has let expire.

    One function, so the API, the approval workflow and the placement check all
    answer the same question the same way. Sentences rather than codes — the
    screen, the refusal and the audit line are the same words.
    """
    on = on or timezone.localdate()
    labels = dict(ReinsurerDocument.Kind.choices)
    held = {}
    for doc in reinsurer.documents.all():
        if doc.counts_as_evidence(on):
            held[doc.kind] = 'ok'
        elif doc.kind not in held:
            held[doc.kind] = ('expired' if doc.is_expired(on) else 'unverified')

    gaps = []
    for kind in ReinsurerDocument.REQUIRED_KINDS:
        state = held.get(kind)
        if state == 'ok':
            continue
        if state == 'expired':
            gaps.append(f'{labels[kind]} — on file but EXPIRED')
        elif state == 'unverified':
            gaps.append(f'{labels[kind]} — uploaded but not yet verified')
        else:
            gaps.append(f'{labels[kind]} — nothing on file')
    return gaps
