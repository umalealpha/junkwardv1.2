"""
claims/models.py

Two simple recovery-tracking models:

  Subrogation — money we recover from a third party after we paid a claim.
                Example: We pay our customer P 80,000 for a car accident,
                then chase the at-fault driver's insurer for reimbursement.

  Salvage     — value recovered by selling/disposing of damaged property
                we acquired through paying a total-loss claim.
                Example: We total-loss a vehicle for P 150,000, then sell
                the wreck for P 25,000 — that's salvage.

Both modules are stand-alone today. A future Graphite integration will
auto-populate them when the corresponding button is pressed in the core
policy admin system. For now they accept manual entry and bulk file imports.
"""

from decimal import Decimal

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone

from core.models import AuditableMixin, BaseModel, Company
from billing.models import Contact

# Pure-Python recovery helpers (no Django) — the single source of the register's
# arithmetic and date handling. Reused here so the model and any importer agree.
from claims.recoveries.ageing import age_bucket, days_since_appointed
from claims.recoveries.costs import compute_outstanding, compute_total_recoverable
from claims.recoveries.prescription import prescription_status


ZERO = Decimal('0.00')


# ---------------------------------------------------------------------------
# Subrogation
# ---------------------------------------------------------------------------

class Subrogation(AuditableMixin, BaseModel):
    """A claim we paid where we are pursuing recovery from a third party."""

    class Status(models.TextChoices):
        PENDING          = 'pending',          'Pending — pursuing recovery'
        PARTIAL          = 'partial',          'Partially recovered'
        FULLY_RECOVERED  = 'fully_recovered',  'Fully recovered'
        WRITTEN_OFF      = 'written_off',      'Written off — no recovery'
        IN_LITIGATION    = 'in_litigation',    'In litigation'

    claim_reference     = models.CharField(
                              max_length=80,
                              help_text='Graphite claim reference, e.g. CLM-2026-001234',
                          )
    incident_date       = models.DateField(null=True, blank=True)
    company             = models.ForeignKey(
                              Company, on_delete=models.PROTECT,
                              related_name='subrogations', null=True, blank=True,
                          )
    third_party_name    = models.CharField(
                              max_length=200,
                              help_text='Person or organisation we are recovering from.',
                          )
    third_party_insurer = models.CharField(max_length=200, blank=True, default='')
    third_party_contact = models.ForeignKey(
                              Contact, on_delete=models.SET_NULL,
                              related_name='subrogations', null=True, blank=True,
                          )
    claim_paid_amount   = models.DecimalField(
                              max_digits=18, decimal_places=2,
                              help_text='How much we paid out on the original claim.',
                          )
    expected_recovery   = models.DecimalField(
                              max_digits=18, decimal_places=2, default=ZERO,
                              help_text='Best estimate of recoverable amount.',
                          )
    actual_recovery     = models.DecimalField(
                              max_digits=18, decimal_places=2, default=ZERO,
                              help_text='Cumulative cash actually recovered to date.',
                          )
    last_recovery_date  = models.DateField(null=True, blank=True)
    status              = models.CharField(
                              max_length=20, choices=Status.choices,
                              default=Status.PENDING,
                          )
    notes               = models.TextField(blank=True, default='')
    graphite_id         = models.CharField(max_length=100, blank=True, default='')

    # --- Recovery-possible flag, demand letter, escalation (B9, 2026-09-13) ---
    # Pako Kago's request: the flag ORIGINATES on the Graphite claim and rides
    # the nightly feed in. Omni never writes to Graphite. These four stamps are
    # the whole state machine, and they are deliberately SEPARATE timestamps
    # rather than a status column, because the escalation must be measured from
    # the moment the flag arrived — not from `updated_at`.
    #
    # Why that matters (lesson already paid for once): a control written against
    # the CURRENT STATE of a row is defeated by people touching the row. If the
    # 48-hour clock ran off `updated_at`, anyone opening and saving the case —
    # or any importer re-writing it — would reset the clock and the escalation
    # would never fire. It runs off `recovery_flagged_at`, which is stamped ONCE
    # and never re-stamped while the flag stands.
    recovery_flagged_at    = models.DateTimeField(
                                 null=True, blank=True, db_index=True,
                                 help_text='When Claims flagged this claim as recovery-possible. '
                                           'Stamped once; the 48-hour escalation clock runs from here.',
                             )
    recovery_flag_source   = models.CharField(
                                 max_length=20, blank=True, default='',
                                 help_text="'graphite' when it rode the nightly feed, 'manual' when keyed in Omni.",
                             )
    demand_letter_sent_at  = models.DateTimeField(
                                 null=True, blank=True,
                                 help_text='When the demand letter went to the third party. '
                                           'This is the action that clears the alert.',
                             )
    demand_letter_sent_to  = models.CharField(max_length=254, blank=True, default='')
    escalated_at           = models.DateTimeField(
                                 null=True, blank=True,
                                 help_text='When the 48-hour escalation to the Finance Manager fired. '
                                           'Set once, so it can never fire twice.',
                             )

    # --- Recovery register fields (2026-08-17) --------------------------------
    # Added to replace Keetile Mokhendo's manual spreadsheet. `incident_date`
    # above doubles as the date of loss (drives prescription). All nullable so
    # the migration is safe on existing rows and manual entry stays light.
    class ClaimTypeChoice(models.TextChoices):
        MOTOR_ACCIDENT      = 'motor_accident',      'Motor Accident'
        BUILDINGS_COMBINED  = 'buildings_combined',  'Buildings Combined'
        FIRE_SPECIAL_PERILS = 'fire_special_perils', 'Fire and Special Perils'
        MONEY_FIDELITY      = 'money_fidelity',       'Money and Fidelity'
        OTHER               = 'other',                'Other'

    claim_type       = models.CharField(
                           max_length=32, choices=ClaimTypeChoice.choices,
                           blank=True, default='',
                       )
    date_appointed   = models.DateField(
                           null=True, blank=True,
                           help_text='When the lawyer / collector was appointed. Drives ageing.',
                       )
    appointed_to     = models.ForeignKey(
                           'SubrogationPanel', on_delete=models.SET_NULL,
                           related_name='subrogations', null=True, blank=True,
                           help_text='Who is monitoring / collecting this recovery.',
                       )
    # Cost build-up — Keetile's formula. Total recoverable is COMPUTED from
    # these (see total_recoverable), never typed, so the header always foots.
    assessor_fees    = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    repair_costs     = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    client_excess    = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    towing_fees      = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    legal_fees       = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    salvage_amount   = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)

    created_by          = models.ForeignKey(
                              User, on_delete=models.PROTECT,
                              related_name='subrogations_created',
                          )

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']
        verbose_name        = 'Subrogation'
        verbose_name_plural = 'Subrogations'
        indexes = [
            models.Index(fields=['claim_reference']),
            models.Index(fields=['status']),
            models.Index(fields=['date_appointed']),
        ]

    def __str__(self):
        return f"{self.claim_reference} → {self.third_party_name}"

    # --- Cost build-up ------------------------------------------------------
    @property
    def _has_cost_components(self):
        return any(v is not None for v in (
            self.assessor_fees, self.repair_costs, self.client_excess,
            self.towing_fees, self.legal_fees, self.salvage_amount,
        ))

    @property
    def total_recoverable(self) -> Decimal:
        """The amount we are entitled to recover.

        Computed from the six cost components (assessor + repairs + excess +
        towing + legal, LESS salvage) whenever any is captured; otherwise the
        legacy typed `expected_recovery`, so old rows still show a figure.
        """
        if self._has_cost_components:
            return compute_total_recoverable(
                self.assessor_fees, self.repair_costs, self.client_excess,
                self.towing_fees, self.legal_fees, self.salvage_amount,
            )
        return (self.expected_recovery or ZERO)

    @property
    def amount_recovered(self) -> Decimal:
        """Cash recovered to date — the SUM of receipt records, so it can never
        drift from the money actually banked. Falls back to the legacy typed
        `actual_recovery` for rows that have no receipts yet.

        Uses the `_receipts_total` annotation when the queryset supplied one
        (SubrogationViewSet does), so a list of N rows is one query, not N
        aggregates fired four times each via the serializer's money fields.
        """
        # hasattr, not getattr(...None): a Sum over zero receipts annotates as
        # None, and getattr couldn't tell that apart from "no annotation", so
        # every zero-receipt row still re-queried — exactly the freshly-loaded
        # book the annotation exists to spare. When annotated (even None), trust
        # it and fall to actual_recovery; only aggregate when truly un-annotated.
        if hasattr(self, '_receipts_total'):
            total = self._receipts_total
        else:
            total = self.receipts.aggregate(total=models.Sum('amount'))['total']
        if total is not None:
            return Decimal(total).quantize(Decimal('0.01'))
        return (self.actual_recovery or ZERO)

    @property
    def outstanding_balance(self) -> Decimal:
        return compute_outstanding(self.total_recoverable, self.amount_recovered)

    # Legacy names kept so existing serializer / frontend keep working.
    @property
    def outstanding(self):
        return self.outstanding_balance

    @property
    def recovery_pct(self):
        base = self.total_recoverable
        if not base:
            return Decimal('0')
        return (self.amount_recovered / base * Decimal('100')).quantize(Decimal('0.1'))

    # --- Ageing & prescription (reuse the pure helpers) ---------------------
    @property
    def age_days(self):
        return days_since_appointed(self.date_appointed, as_at=timezone.localdate())

    @property
    def age_bucket_label(self) -> str:
        return age_bucket(self.date_appointed, as_at=timezone.localdate()).label

    @property
    def prescription(self):
        """Prescription verdict (idea 1). Clock runs from date of loss where
        known (`incident_date`), else the date appointed."""
        return prescription_status(
            date_of_loss=self.incident_date,
            date_appointed=self.date_appointed,
            as_at=timezone.localdate(),
        )


# ---------------------------------------------------------------------------
# Subrogation panel — the approved list of who we appoint to collect
# ---------------------------------------------------------------------------

class SubrogationPanel(AuditableMixin, BaseModel):
    """Approved parties we appoint to monitor and collect a subrogation.

    Alpha Direct pursues recovery through inside lawyers, external law firms,
    external debt collectors, or internal accountants (CFO brief 2026-08-17).
    A closed register replaces the free-text "Appointed To" column of the old
    spreadsheet, where firm names were misspelt into duplicates and payment
    methods ('Orange Money') were entered as if they were collectors.
    """

    class Kind(models.TextChoices):
        EXTERNAL_LAWYER     = 'external_lawyer',     'External lawyer'
        INTERNAL_LAWYER     = 'internal_lawyer',     'Internal lawyer'
        DEBT_COLLECTOR      = 'debt_collector',      'Debt collector'
        INTERNAL_ACCOUNTANT = 'internal_accountant', 'Internal accountant'

    name         = models.CharField(max_length=200, unique=True)
    kind         = models.CharField(max_length=24, choices=Kind.choices)
    contact_name = models.CharField(max_length=200, blank=True, default='')
    contact_email = models.EmailField(blank=True, default='')
    contact_phone = models.CharField(max_length=40, blank=True, default='')
    is_active    = models.BooleanField(
                       default=True,
                       help_text='Inactive panel members cannot be newly appointed, '
                                 'but stay linked to existing cases for history.',
                   )
    notes        = models.TextField(blank=True, default='')
    created_by   = models.ForeignKey(
                       User, on_delete=models.PROTECT,
                       related_name='subrogation_panel_created',
                       null=True, blank=True,
                   )

    class Meta(BaseModel.Meta):
        ordering = ['name']
        verbose_name        = 'Subrogation Panel Member'
        verbose_name_plural = 'Subrogation Panel'
        indexes = [models.Index(fields=['kind']), models.Index(fields=['is_active'])]

    def __str__(self):
        return f"{self.name} ({self.get_kind_display()})"


# ---------------------------------------------------------------------------
# Subrogation receipt — money actually collected, one record per payment
# ---------------------------------------------------------------------------

class SubrogationReceipt(AuditableMixin, BaseModel):
    """One recovery payment banked against a subrogation.

    The old register kept a single typed "Recovered To Date" cell, which is
    how the sheet's headline stopped agreeing with its own detail. Here every
    receipt is its own dated, referenced, attributable line, and the case's
    recovered figure is the SUM of these — it cannot drift from the bank.

    Supports RealPay's automated paygate import (idea: reject duplicate
    instalment lines) plus manual capture of cash, EFT and card payments,
    exactly as Keetile Mokhendo requested.
    """

    class Method(models.TextChoices):
        REALPAY = 'realpay', 'RealPay collection'
        CASH    = 'cash',    'Cash'
        EFT     = 'eft',     'EFT'
        POS     = 'pos',     'Card / POS'
        OTHER   = 'other',   'Other'

    subrogation   = models.ForeignKey(
                        Subrogation, on_delete=models.CASCADE,
                        related_name='receipts',
                    )
    amount        = models.DecimalField(max_digits=18, decimal_places=2)
    received_date = models.DateField()
    method        = models.CharField(max_length=12, choices=Method.choices, default=Method.OTHER)
    reference     = models.CharField(
                        max_length=200, blank=True, default='',
                        help_text='Bank / RealPay / receipt reference.',
                    )
    # RealPay's own transaction id — used to reject a re-imported instalment
    # line so the same collection is never banked twice.
    realpay_txn_id = models.CharField(max_length=120, blank=True, default='')
    notes         = models.TextField(blank=True, default='')
    created_by    = models.ForeignKey(
                        User, on_delete=models.PROTECT,
                        related_name='subrogation_receipts_created',
                    )

    class Meta(BaseModel.Meta):
        ordering = ['-received_date', '-created_at']
        verbose_name        = 'Subrogation Receipt'
        verbose_name_plural = 'Subrogation Receipts'
        indexes = [
            models.Index(fields=['subrogation', 'received_date']),
        ]
        constraints = [
            # A RealPay transaction id, when present, is unique — the guard that
            # stops a re-imported paygate file double-counting a collection.
            models.UniqueConstraint(
                fields=['realpay_txn_id'],
                condition=models.Q(realpay_txn_id__gt=''),
                name='uniq_subrogation_realpay_txn',
            ),
        ]

    def __str__(self):
        return f"{self.subrogation.claim_reference} — {self.amount} on {self.received_date}"


# ---------------------------------------------------------------------------
# GL account mapping — assigned by hand, read by (future) GL posting
# ---------------------------------------------------------------------------

class SubrogationGLConfig(AuditableMixin, BaseModel):
    """Which chart-of-accounts account a subrogation recovery credits.

    A singleton (one row). The Finance Manager / CFO assigns the account here;
    GL posting is deliberately NOT wired yet, so nothing is posted until this is
    set AND the posting step is built and signed off (CR-007, C6). This model is
    just the safe place to record the choice — no journal is created by saving it.
    """

    recovery_income_account = models.ForeignKey(
        'ledger.Account', on_delete=models.PROTECT,
        related_name='+', null=True, blank=True,
        help_text='Revenue account that subrogation recoveries credit. '
                  'Unset = GL posting stays blocked.',
    )
    updated_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='subrogation_gl_config_updates',
    )

    class Meta(BaseModel.Meta):
        verbose_name        = 'Subrogation GL Configuration'
        verbose_name_plural = 'Subrogation GL Configuration'

    def __str__(self):
        return f"Subrogation GL config (income → {self.recovery_income_account or 'UNSET'})"

    @classmethod
    def get_solo(cls):
        """Return the single config row, creating an empty one if missing."""
        obj = cls.objects.first()
        if obj is None:
            obj = cls.objects.create()
        return obj


# ---------------------------------------------------------------------------
# Salvage
# ---------------------------------------------------------------------------

class Salvage(AuditableMixin, BaseModel):
    """DEPRECATED — use `salvage.SalvageItem` instead.

    CFO directive 2026-05-24: the canonical salvage record now lives in
    the `salvage` app (full portal — auctions, inspections, GL posting,
    cost basis, NRV impairment). This `claims.Salvage` row is retained
    for back-compat with legacy importers and audit history; new code
    must NOT create rows here.

    Migration `claims/0005_backfill_salvage_into_salvage_app.py`
    backfills a matching `salvage.SalvageItem` for every existing row
    and stores the FK in `linked_salvage_item`.

    Deletion is deliberately deferred — incoming FKs (RecoveryImportBatch,
    Contact, etc.) need a removal plan before the table is dropped.
    """

    class Status(models.TextChoices):
        PENDING        = 'pending',        'Pending — awaiting decision'
        FOR_SALE       = 'for_sale',       'For sale'
        SOLD           = 'sold',           'Sold'
        SCRAPPED       = 'scrapped',       'Scrapped'
        RETAINED       = 'retained',       'Retained by insured'

    claim_reference     = models.CharField(
                              max_length=80,
                              help_text='Graphite claim reference, e.g. CLM-2026-001234',
                          )
    incident_date       = models.DateField(null=True, blank=True)
    company             = models.ForeignKey(
                              Company, on_delete=models.PROTECT,
                              related_name='salvages', null=True, blank=True,
                          )
    asset_description   = models.CharField(
                              max_length=300,
                              help_text='What was salvaged — e.g. "2018 Toyota Hilux 2.4D, reg B-123-ABC"',
                          )
    estimated_value     = models.DecimalField(
                              max_digits=18, decimal_places=2, default=ZERO,
                              help_text='Pre-sale estimate of recoverable value.',
                          )
    sale_proceeds       = models.DecimalField(
                              max_digits=18, decimal_places=2, default=ZERO,
                          )
    sale_date           = models.DateField(null=True, blank=True)
    buyer_name          = models.CharField(max_length=200, blank=True, default='')
    buyer_contact       = models.ForeignKey(
                              Contact, on_delete=models.SET_NULL,
                              related_name='salvages_purchased', null=True, blank=True,
                          )
    status              = models.CharField(
                              max_length=20, choices=Status.choices,
                              default=Status.PENDING,
                          )
    notes               = models.TextField(blank=True, default='')
    graphite_id         = models.CharField(max_length=100, blank=True, default='')
    created_by          = models.ForeignKey(
                              User, on_delete=models.PROTECT,
                              related_name='salvages_created',
                          )
    # Backfill FK populated by claims.0005_backfill_salvage_into_salvage_app.
    # Points at the canonical record in the salvage app. SET_NULL so the
    # legacy row survives if the new record is later removed.
    linked_salvage_item = models.ForeignKey(
                              'salvage.SalvageItem',
                              on_delete=models.SET_NULL,
                              null=True, blank=True,
                              related_name='legacy_claims_salvages',
                              help_text=(
                                  'Pointer to the salvage.SalvageItem row '
                                  'that supersedes this legacy entry.'
                              ),
                          )

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']
        verbose_name        = 'Salvage'
        verbose_name_plural = 'Salvages'
        indexes = [
            models.Index(fields=['claim_reference']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f"{self.claim_reference} salvage — {self.asset_description[:60]}"

    @property
    def gain_loss(self):
        return (self.sale_proceeds or ZERO) - (self.estimated_value or ZERO)


# ---------------------------------------------------------------------------
# Import staging — append-only, restricted to CFO / Finance Manager
# ---------------------------------------------------------------------------

class RecoveryImportBatch(AuditableMixin, BaseModel):
    """
    Tracks a CSV / XLSX upload of Subrogation or Salvage rows.

    Append-only: existing rows are NEVER overwritten. Duplicates (same
    claim_reference) are skipped — the user must reach out and update by hand
    if they want to amend an existing entry.
    """

    class Kind(models.TextChoices):
        SUBROGATION = 'subrogation', 'Subrogation'
        SALVAGE     = 'salvage',     'Salvage'

    class Status(models.TextChoices):
        DRAFT              = 'draft',              'Draft (preview)'
        PARTIALLY_APPROVED = 'partially_approved', 'Awaiting second approval'
        APPROVED           = 'approved',           'Fully approved — ready to commit'
        COMMITTED          = 'committed',          'Committed'
        REJECTED           = 'rejected',           'Rejected'
        FAILED             = 'failed',             'Failed'

    kind              = models.CharField(max_length=15, choices=Kind.choices)
    file_name         = models.CharField(max_length=255, blank=True, default='')
    rows_total        = models.PositiveIntegerField(default=0)
    rows_valid        = models.PositiveIntegerField(default=0)
    rows_invalid      = models.PositiveIntegerField(default=0)
    rows_skipped_dup  = models.PositiveIntegerField(default=0)
    rows_imported     = models.PositiveIntegerField(default=0)
    parsed_rows       = models.JSONField(default=list, blank=True)
    validation_errors = models.JSONField(default=list, blank=True)
    status            = models.CharField(
                            max_length=20, choices=Status.choices,
                            default=Status.DRAFT,
                        )
    company           = models.ForeignKey(
                            Company, on_delete=models.PROTECT,
                            related_name='recovery_import_batches',
                            null=True, blank=True,
                        )
    created_by        = models.ForeignKey(
                            User, on_delete=models.PROTECT,
                            related_name='recovery_import_batches',
                        )
    # Dual authorisation — each commit requires two distinct approvers
    first_approved_by  = models.ForeignKey(
                             User, null=True, blank=True,
                             on_delete=models.SET_NULL,
                             related_name='recovery_imports_first_approved',
                         )
    first_approved_at  = models.DateTimeField(null=True, blank=True)
    second_approved_by = models.ForeignKey(
                             User, null=True, blank=True,
                             on_delete=models.SET_NULL,
                             related_name='recovery_imports_second_approved',
                         )
    second_approved_at = models.DateTimeField(null=True, blank=True)
    rejected_by        = models.ForeignKey(
                             User, null=True, blank=True,
                             on_delete=models.SET_NULL,
                             related_name='recovery_imports_rejected',
                         )
    rejected_at        = models.DateTimeField(null=True, blank=True)
    rejection_reason   = models.TextField(blank=True, default='')
    committed_at      = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']
        verbose_name        = 'Recovery Import Batch'
        verbose_name_plural = 'Recovery Import Batches'

    def __str__(self):
        return f"{self.get_kind_display()} import — {self.file_name or self.id} ({self.status})"

    @property
    def is_fully_approved(self):
        return (
            self.first_approved_by_id is not None
            and self.second_approved_by_id is not None
            and self.first_approved_by_id != self.second_approved_by_id
        )


# ---------------------------------------------------------------------------
# Month-end Claims Reconciliation — stores the output of
# claims.reconciliation.engine.reconcile_period (Kago Tshutlhedi, FM, 2026-09).
# The engine itself is pure Python (no ORM); this is just its result, kept for
# audit trail. NOT posted to the GL — posting rules are out of scope pending
# CFO sign-off.
# ---------------------------------------------------------------------------

class ClaimsReconciliationRun(AuditableMixin, BaseModel):
    """One month's computed claims reconciliation (Incurred Claims formula +
    VAT split + per-claim RI split). Stores the engine's output verbatim so a
    run can be audited without re-running it; it does not itself recompute
    anything."""

    period_label = models.CharField(max_length=24, db_index=True)
    vat_rate = models.DecimalField(max_digits=6, decimal_places=4)

    opening_claims_payable = models.DecimalField(max_digits=18, decimal_places=2)
    closing_claims_payable = models.DecimalField(max_digits=18, decimal_places=2)
    claims_paid_gross = models.DecimalField(max_digits=18, decimal_places=2)
    claims_paid_excl_vat = models.DecimalField(max_digits=18, decimal_places=2)
    vat_on_claims_paid = models.DecimalField(max_digits=18, decimal_places=2)

    incurred_claims = models.DecimalField(max_digits=18, decimal_places=2)

    retention_total = models.DecimalField(max_digits=18, decimal_places=2)
    total_ri_total = models.DecimalField(max_digits=18, decimal_places=2)

    # Per-claim results and any Retention%+RI% != 100% exceptions, as computed
    # — totals/refs only, no PII beyond claim/policy numbers already in Omni.
    claim_results = models.JSONField(default=list, blank=True)
    exceptions = models.JSONField(default=list, blank=True)

    computed_at = models.DateTimeField(auto_now_add=True, db_index=True)
    computed_by = models.ForeignKey(
        User, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='claims_recon_runs',
    )
    notes = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-computed_at']
        verbose_name = 'Claims Reconciliation Run'
        verbose_name_plural = 'Claims Reconciliation Runs'

    def __str__(self):
        return f'Claims recon {self.period_label} — incurred {self.incurred_claims}'

    @property
    def has_exceptions(self) -> bool:
        return bool(self.exceptions)


# ---------------------------------------------------------------------------
# Claim Forms Vault — Omni's copy of every blank claim form (CFO 12-Aug-2026)
# ---------------------------------------------------------------------------
# Kept in a separate module for cohesion; imported here so migrations and the
# rest of the app see it as claims.ClaimForm.
from .vault_models import ClaimForm  # noqa: E402,F401
