"""
assets/models.py

Fixed Assets Register for Alpha Direct Insurance.

Models:
  - AssetCategory       Classification (IT equipment, motor vehicles, etc.)
                        with default depreciation method, useful life, and the
                        GL accounts the category posts to.
  - Asset               A single physical asset on the register.
                        Holds cost, salvage value, useful life, location,
                        custodian, and an opening accumulated depreciation
                        balance for assets migrated mid-life from another system
                        (e.g. Odoo).
  - DepreciationEntry   One month of depreciation posted against an asset.
                        Linked to a JournalEntry that hits the GL.
  - AssetDisposal       Sale, write-off, or transfer of an asset.
                        Reverses cost and accumulated depreciation, books
                        proceeds, and recognises gain/loss to P&L.

Conventions follow the rest of alpha-finance:
  - UUID PKs from BaseModel
  - AuditableMixin tracks creates/updates in AuditLog
  - All monetary values: DecimalField(max_digits=18, decimal_places=2)
  - Multi-company support via FK to core.Company
  - Method 'straight_line' is the default; 'reducing_balance' supported for
    motor vehicles per BURS practice.
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from core.models import AuditableMixin, BaseModel, Company
from ledger.models import Account


ZERO = Decimal('0.00')
TWELVE = Decimal('12')


# ---------------------------------------------------------------------------
# Asset Category
# ---------------------------------------------------------------------------

class AssetCategory(AuditableMixin, BaseModel):
    """
    A category groups assets that share depreciation defaults and post to the
    same pair of GL accounts (cost + accumulated depreciation).

    Default categories created by the management command match the chart of
    accounts already present in the system:

        Office equipment        cost 1410   accum-depr 1451
        IT equipment            cost 1420   accum-depr 1452
        Motor vehicles          cost 1430   accum-depr 1453   passenger-cap P175k, VAT denied
        Furniture and fittings  cost 1440   accum-depr 1454

    Tax behaviour (BURS Income Tax Act + VAT Act):
      - Passenger motor vehicles: cost cap P 175,000 for capital allowances;
        input VAT on the purchase is denied (s.20 VAT Act) — the VAT becomes
        part of the cost rather than a recoverable input.
      - Plant & machinery: 25% reducing balance capital allowance.
      - Industrial / commercial buildings: 2.5% straight line.
      - Capital allowances are tracked separately from accounting depreciation;
        both schedules co-exist on each asset.
    """

    class Method(models.TextChoices):
        STRAIGHT_LINE     = 'straight_line',     'Straight-line'
        REDUCING_BALANCE  = 'reducing_balance',  'Reducing balance'
        FULL_YEAR_ONE     = 'full_year_one',     '100% in year of acquisition'

    code            = models.CharField(max_length=20, unique=True)
    name            = models.CharField(max_length=100)
    cost_account    = models.ForeignKey(
                          Account, on_delete=models.PROTECT,
                          related_name='asset_categories_cost',
                          help_text='GL account holding the gross cost of assets in this category',
                      )
    accum_depr_account = models.ForeignKey(
                          Account, on_delete=models.PROTECT,
                          related_name='asset_categories_accum_depr',
                          help_text='GL account holding accumulated depreciation for this category',
                      )
    depreciation_expense_account = models.ForeignKey(
                          Account, on_delete=models.PROTECT,
                          related_name='asset_categories_depr_expense',
                          help_text='P&L account where monthly depreciation is booked (typically 6600)',
                      )
    default_method  = models.CharField(
                          max_length=20, choices=Method.choices,
                          default=Method.STRAIGHT_LINE,
                      )
    default_useful_life_months = models.PositiveSmallIntegerField(
                          default=60,
                          help_text='Default useful life in months (60 = 5 years)',
                      )
    default_salvage_pct = models.DecimalField(
                          max_digits=5, decimal_places=2, default=ZERO,
                          help_text='Default salvage value as a percent of cost (0–100)',
                      )
    # ---- Tax-side defaults --------------------------------------------------
    is_passenger_vehicle = models.BooleanField(
                          default=False,
                          help_text='Triggers BURS passenger-vehicle treatment: '
                                    'cost cap P 175,000 for capital allowances + '
                                    'input VAT denial (capitalised into cost).',
                      )
    default_capital_allowance_method = models.CharField(
                          max_length=20, choices=Method.choices,
                          default=Method.REDUCING_BALANCE,
                          help_text='Capital allowance method per BURS Income Tax Act.',
                      )
    default_capital_allowance_rate = models.DecimalField(
                          max_digits=5, decimal_places=2, default=Decimal('25.00'),
                          help_text='Annual rate (% of opening tax NBV for reducing balance, '
                                    '% of cost for straight-line). 25 for plant/vehicles, '
                                    '2.5 for buildings.',
                      )
    default_tax_cost_cap = models.DecimalField(
                          max_digits=18, decimal_places=2, null=True, blank=True,
                          help_text='If set, the asset cost is capped at this value for tax '
                                    'purposes. P 175,000 for passenger motor vehicles per BURS.',
                      )
    is_active       = models.BooleanField(default=True)

    class Meta(BaseModel.Meta):
        ordering            = ['code']
        verbose_name        = 'Asset Category'
        verbose_name_plural = 'Asset Categories'

    def __str__(self):
        return f"{self.code} - {self.name}"


# ---------------------------------------------------------------------------
# Asset
# ---------------------------------------------------------------------------

class Asset(AuditableMixin, BaseModel):
    """A single asset on the fixed assets register."""

    class Status(models.TextChoices):
        ACTIVE    = 'active',    'Active'
        DISPOSED  = 'disposed',  'Disposed'
        WRITTEN_OFF = 'written_off', 'Written off'
        TRANSFERRED = 'transferred', 'Transferred'
        # CFO directive 2026-05-20 (FAR Defects Memo DR-002): flag-then-
        # hard-delete sequence for the 11 numeric-tag vehicles that
        # duplicate canonical ADI-FAR-* records. NOT a true asset
        # lifecycle state — it's a quarantine bucket until the CFO
        # confirms the canonical pair is sound, then a separate
        # mgmt command performs the hard delete.
        MIGRATED_DUPLICATE = 'migrated_duplicate', 'Migrated duplicate (audit hold)'

    class Method(models.TextChoices):
        STRAIGHT_LINE     = 'straight_line',     'Straight-line'
        REDUCING_BALANCE  = 'reducing_balance',  'Reducing balance'

    class VatTreatment(models.TextChoices):
        STANDARD = 'standard', 'Standard — input VAT recoverable, cost is net of VAT'
        DENIED   = 'denied',   'Denied — VAT capitalised into cost (luxury / passenger vehicle)'
        ZERO_OR_EXEMPT = 'zero_exempt', 'Zero-rated or exempt — no VAT'

    class Condition(models.TextChoices):
        FUNCTIONAL = 'functional', 'Functional'
        BROKEN     = 'broken',     'Broken'
        UNKNOWN    = 'unknown',    'Unknown'

    class CustodyStatus(models.TextChoices):
        # CONTROL-layer status (Asset Control & Handover module, CFO 2026-09-02).
        # Independent of `status` above, which is the GL / depreciation
        # lifecycle. custody_status tracks where a physical asset sits in the
        # issue/return control: it only reaches IN_USE through a signed
        # three-signature handover, and a RETURNED_SPARE can only go out again
        # via a fresh, dual-approved requisition. [spec §6.1]
        IN_STOCK       = 'in_stock',       'In stock'
        ISSUED         = 'issued',         'Issued — awaiting signed handover'
        IN_USE         = 'in_use',         'In use'
        RETURNED_SPARE = 'returned_spare', 'Returned / spare'
        RETIRED        = 'retired',        'Written off / retired'

    # Identity
    tag_number        = models.CharField(
                            max_length=50, unique=True,
                            help_text='Asset tag — physical label, e.g. ADI-IT-0042',
                        )
    external_ref      = models.CharField(
                            max_length=100, blank=True, default='',
                            help_text='Reference from source system (Odoo asset code, etc.)',
                        )
    name              = models.CharField(max_length=200)
    description       = models.TextField(blank=True, default='')
    serial_number     = models.CharField(max_length=100, blank=True, default='')
    barcode           = models.CharField(max_length=100, blank=True, default='')

    # Classification
    company           = models.ForeignKey(
                            Company, on_delete=models.PROTECT,
                            related_name='assets',
                            help_text='Owning legal entity',
                        )
    category          = models.ForeignKey(
                            AssetCategory, on_delete=models.PROTECT,
                            related_name='assets',
                        )

    # Money
    cost              = models.DecimalField(
                            max_digits=18, decimal_places=2,
                            help_text='Original capitalised cost. For VAT-denied assets '
                                      '(passenger vehicles), this INCLUDES the irrecoverable VAT.',
                        )
    salvage_value     = models.DecimalField(
                            max_digits=18, decimal_places=2, default=ZERO,
                            help_text='Estimated residual value at end of useful life',
                        )

    # ---- VAT treatment ------------------------------------------------------
    vat_treatment      = models.CharField(
                             max_length=15, choices=VatTreatment.choices,
                             default=VatTreatment.STANDARD,
                             help_text='Standard: VAT is reclaimed via the VAT return and is NOT '
                                       'in cost. Denied: VAT is capitalised into cost (luxury / '
                                       'passenger motor vehicles per VAT Act s.20).',
                         )
    purchase_vat_amount = models.DecimalField(
                             max_digits=18, decimal_places=2, default=ZERO,
                             help_text='VAT charged on the original purchase. Informational only '
                                       'when vat_treatment=denied (the VAT is already inside cost).',
                         )

    # ---- Tax / capital allowance side ---------------------------------------
    capital_allowance_method = models.CharField(
                             max_length=20, choices=Method.choices,
                             default=Method.REDUCING_BALANCE,
                             help_text='Method used for BURS capital allowance computation. '
                                       'Independent from the accounting depreciation method above.',
                         )
    capital_allowance_rate   = models.DecimalField(
                             max_digits=5, decimal_places=2, default=Decimal('25.00'),
                             help_text='Annual % rate for capital allowances.',
                         )
    tax_cost_cap             = models.DecimalField(
                             max_digits=18, decimal_places=2, null=True, blank=True,
                             help_text='If set, the cost recognised for tax (capital allowance) '
                                       'purposes is capped at this value. BURS sets P 175,000 '
                                       'for passenger motor vehicles.',
                         )

    # Depreciation
    method            = models.CharField(
                            max_length=20, choices=Method.choices,
                            default=Method.STRAIGHT_LINE,
                        )
    useful_life_months = models.PositiveSmallIntegerField(
                            help_text='Useful life in months (e.g. 60 for 5 years)',
                        )
    purchase_date     = models.DateField(
                            help_text='Original date the asset was acquired',
                        )
    in_service_date   = models.DateField(
                            help_text='Date depreciation started (= purchase_date if same month)',
                        )

    # Migration support — assets brought across from Odoo / another system
    opening_accumulated_depreciation = models.DecimalField(
                            max_digits=18, decimal_places=2, default=ZERO,
                            help_text='Accumulated depreciation already booked in the prior system '
                                      'before migration. Forward depreciation starts after '
                                      'last_depreciation_date.',
                        )
    last_depreciation_date = models.DateField(
                            null=True, blank=True,
                            help_text='Last month-end through which depreciation has been booked. '
                                      'For Odoo imports, set to the migration cutover date.',
                        )

    # Where it lives
    location          = models.CharField(max_length=200, blank=True, default='')
    custodian         = models.CharField(
                            max_length=200, blank=True, default='',
                            help_text='Person or department responsible for the asset '
                                      '(free-text display; mirrors custodian_employee name).',
                        )
    # Structured current holder (CFO 2026-06-26 — asset hand-overs). Links the
    # asset to a staff record so "assets held by X" is a reliable query; the
    # free-text `custodian` above is kept in sync for display + search. Every
    # change is logged in AssetAssignment (append-only history).
    custodian_employee = models.ForeignKey(
                            'payroll.Employee', null=True, blank=True,
                            on_delete=models.SET_NULL, related_name='assets_held',
                            help_text='Staff member currently holding this asset.',
                        )

    # Status
    status            = models.CharField(
                            max_length=20, choices=Status.choices,
                            default=Status.ACTIVE,
                        )
    # Control-layer custody status — set by the Asset Control & Handover module.
    # Defaults IN_STOCK; never reaches IN_USE without a signed 3-signature
    # handover note. CFO 2026-09-02. [spec §6.1]
    custody_status    = models.CharField(
                            max_length=20, choices=CustodyStatus.choices,
                            default=CustodyStatus.IN_STOCK,
                            db_index=True,
                        )
    condition         = models.CharField(
                            max_length=20, choices=Condition.choices,
                            default=Condition.UNKNOWN,
                        )

    # ---- Componentisation (IFRS — assets/component_models.py) ---------------
    # Self-FK lets a single physical asset be modelled as a parent "shell"
    # with child components that depreciate on independent schedules
    # (building shell + roof + HVAC, for example). Walking helper:
    # assets.component_models.walk_total_monthly_depreciation(asset).
    parent_asset      = models.ForeignKey(
                            'self', null=True, blank=True,
                            on_delete=models.PROTECT,
                            related_name='child_components',
                            help_text='Parent composite asset. Leave blank for '
                                      'standalone assets and for parents themselves. '
                                      'Children depreciate independently; the parent '
                                      'aggregates via the components property.',
                        )

    # FA-001 (CFO directive 2026-05-29) — audit link from a receiver-side
    # Asset row back to the sender row on an intercompany transfer.
    transferred_from_asset = models.ForeignKey(
                            'self', null=True, blank=True,
                            on_delete=models.SET_NULL,
                            related_name='transferred_to',
                            help_text='Audit link: the sender-side Asset row this '
                                      'asset was transferred FROM. Only set on '
                                      'receiver rows created by an intercompany '
                                      'TRANSFER.',
                        )

    # Audit
    created_by        = models.ForeignKey(
                            User, on_delete=models.PROTECT,
                            related_name='assets_created',
                        )
    notes             = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['tag_number']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['category', 'status']),
            models.Index(fields=['company', 'status']),
        ]

    def __str__(self):
        return f"{self.tag_number} - {self.name}"

    # ----------------------------------------------------------------- helpers

    @property
    def accumulated_depreciation(self):
        """Total depreciation booked = opening + sum of posted DepreciationEntry."""
        booked = self.depreciation_entries.filter(
            reversed_at__isnull=True
        ).aggregate(t=models.Sum('amount'))['t'] or ZERO
        return (self.opening_accumulated_depreciation or ZERO) + booked

    @property
    def net_book_value(self):
        """NBV = cost - accumulated depreciation."""
        return (self.cost or ZERO) - self.accumulated_depreciation

    @property
    def depreciable_amount(self):
        """Cost less salvage value — never goes below zero."""
        amount = (self.cost or ZERO) - (self.salvage_value or ZERO)
        return amount if amount > ZERO else ZERO

    @property
    def is_fully_depreciated(self):
        return self.net_book_value <= (self.salvage_value or ZERO)

    # ---- Componentisation accessors ----------------------------------------
    @property
    def components(self):
        """Child component assets (IFRS componentisation). Empty queryset when none."""
        return self.child_components.all()

    @property
    def has_components(self):
        """Cheap existence check used by the depreciation walker."""
        return self.child_components.exists()

    # ----------------------------------------------------------------- tax-side helpers

    @property
    def tax_cost(self):
        """
        Cost recognised for capital-allowance purposes.

        For passenger motor vehicles the BURS Income Tax Act caps cost at
        P 175,000. The tax_cost_cap on the asset (or category) controls this.
        """
        cost = self.cost or ZERO
        if self.tax_cost_cap is not None and cost > self.tax_cost_cap:
            return self.tax_cost_cap
        return cost

    @property
    def cost_capped_by_tax_rule(self):
        """True iff the asset's accounting cost exceeds the tax cap."""
        return self.tax_cost_cap is not None and (self.cost or ZERO) > self.tax_cost_cap

    def annual_capital_allowance(self, *, opening_tax_nbv=None):
        """
        Compute one year's capital allowance.

        Reducing balance:    rate% × opening_tax_nbv
        Straight-line:       rate% × tax_cost
        Full year one:       100% in year 1, 0% thereafter
        """
        rate = (self.capital_allowance_rate or ZERO) / Decimal('100')
        if rate <= 0 or self.tax_cost <= 0:
            return ZERO
        if self.capital_allowance_method == self.Method.STRAIGHT_LINE:
            return (self.tax_cost * rate).quantize(Decimal('0.01'))
        if self.capital_allowance_method == 'full_year_one':
            return self.tax_cost if opening_tax_nbv is None or opening_tax_nbv >= self.tax_cost else opening_tax_nbv
        # Reducing balance — caller supplies opening NBV
        nbv = opening_tax_nbv if opening_tax_nbv is not None else self.tax_cost
        return (nbv * rate).quantize(Decimal('0.01'))

    def monthly_depreciation_amount(self):
        """
        Compute the depreciation charge for ONE month, ignoring whether it has
        already been booked. Caller is responsible for the period check.

        Straight-line:
            charge = (cost - salvage) / useful_life_months
        Reducing balance:
            annual_rate = 1 - (salvage / cost) ** (1 / years)   (capped at 50%)
            monthly_charge = NBV * (annual_rate / 12)
        Both are floored at zero and capped so NBV never drops below salvage.
        """
        if self.status != self.Status.ACTIVE:
            return ZERO
        if self.is_fully_depreciated:
            return ZERO

        if self.method == self.Method.STRAIGHT_LINE:
            life = Decimal(self.useful_life_months or 0)
            if life <= 0:
                return ZERO
            charge = (self.depreciable_amount / life).quantize(Decimal('0.01'))
        else:
            # Reducing balance — pragmatic default rate based on category default
            # (configurable extension can plug in here later)
            from math import pow as _pow
            years = max(1, (self.useful_life_months or 60) // 12)
            cost_f = float(self.cost or 0)
            salv_f = float(self.salvage_value or 0)
            if cost_f <= 0:
                return ZERO
            ratio = max(salv_f / cost_f, 0.01) if salv_f > 0 else 0.10
            annual_rate = 1 - _pow(ratio, 1 / years)
            annual_rate = min(annual_rate, 0.50)
            monthly_rate = Decimal(str(annual_rate)) / TWELVE
            charge = (Decimal(str(self.net_book_value)) * monthly_rate).quantize(Decimal('0.01'))

        # Cap so we don't depreciate past salvage value
        room = self.net_book_value - (self.salvage_value or ZERO)
        if charge > room:
            charge = room if room > ZERO else ZERO
        return charge

    # ----------------------------------------------------------------- validation

    def clean(self):
        super().clean()
        if self.cost is not None and self.cost < ZERO:
            raise ValidationError({'cost': 'Cost cannot be negative.'})
        if self.salvage_value is not None and self.salvage_value < ZERO:
            raise ValidationError({'salvage_value': 'Salvage value cannot be negative.'})
        if self.cost is not None and self.salvage_value is not None and self.salvage_value > self.cost:
            raise ValidationError({'salvage_value': 'Salvage value cannot exceed cost.'})
        if self.useful_life_months is not None and self.useful_life_months <= 0:
            raise ValidationError({'useful_life_months': 'Useful life must be positive.'})
        if self.in_service_date and self.purchase_date and self.in_service_date < self.purchase_date:
            raise ValidationError({'in_service_date': 'In-service date cannot be before purchase date.'})
        if self.opening_accumulated_depreciation and self.cost is not None:
            if self.opening_accumulated_depreciation > (self.cost - (self.salvage_value or ZERO)):
                raise ValidationError({
                    'opening_accumulated_depreciation':
                    'Opening accumulated depreciation cannot exceed cost minus salvage.'
                })


# ---------------------------------------------------------------------------
# Depreciation Entry
# ---------------------------------------------------------------------------

class DepreciationEntry(AuditableMixin, BaseModel):
    """
    One month of depreciation posted against one asset.

    Each entry is tied to:
      - the Asset
      - the FiscalPeriod that was depreciated
      - the JournalEntry that hit the GL  (DR depreciation expense, CR accum-depr)

    Reversed entries keep the row but set reversed_at — they are excluded from
    accumulated_depreciation aggregation.
    """

    asset            = models.ForeignKey(
                           Asset, on_delete=models.PROTECT,
                           related_name='depreciation_entries',
                       )
    period           = models.ForeignKey(
                           'ledger.FiscalPeriod', on_delete=models.PROTECT,
                           related_name='depreciation_entries',
                       )
    period_end_date  = models.DateField(
                           help_text='Last day of the depreciated month',
                       )
    amount           = models.DecimalField(max_digits=18, decimal_places=2)
    journal_entry    = models.OneToOneField(
                           'ledger.JournalEntry', on_delete=models.PROTECT,
                           related_name='depreciation_entry',
                           null=True, blank=True,
                       )
    posted_by        = models.ForeignKey(
                           User, on_delete=models.PROTECT,
                           related_name='depreciation_entries_posted',
                       )
    reversed_at      = models.DateTimeField(null=True, blank=True)
    reversed_by      = models.ForeignKey(
                           User, null=True, blank=True,
                           on_delete=models.SET_NULL,
                           related_name='depreciation_entries_reversed',
                       )

    class Meta(BaseModel.Meta):
        ordering = ['-period_end_date', 'asset__tag_number']
        unique_together = [('asset', 'period')]
        indexes = [
            models.Index(fields=['period_end_date']),
        ]
        verbose_name        = 'Depreciation Entry'
        verbose_name_plural = 'Depreciation Entries'

    def __str__(self):
        return f"{self.asset.tag_number} {self.period.period_name}: {self.amount}"


# ---------------------------------------------------------------------------
# Asset Disposal
# ---------------------------------------------------------------------------

class AssetDisposal(AuditableMixin, BaseModel):
    """
    Disposal (sale, write-off, transfer) of an asset.

    Two-step workflow: the requester creates a PENDING_APPROVAL disposal
    record, and an approver (CFO / Finance Manager / Financial Controller —
    not the requester) approves it, at which point the GL is hit:
      DR  bank account             proceeds
      DR  accumulated depreciation (clears the asset's accum-depr)
      CR  asset cost account       (clears cost)
      DR  loss-on-disposal         OR  CR gain-on-disposal   (balancing figure)

    Disposals are not single-person actions — segregation of duties is
    enforced at the model layer (creator cannot approve).
    """

    class DisposalType(models.TextChoices):
        SALE        = 'sale',        'Sale'
        WRITE_OFF   = 'write_off',   'Write-off'
        TRANSFER    = 'transfer',    'Transfer'
        TRADE_IN    = 'trade_in',    'Trade-in'

    class Status(models.TextChoices):
        PENDING_APPROVAL = 'pending_approval', 'Pending approval'
        APPROVED         = 'approved',         'Approved (posted to GL)'
        REJECTED         = 'rejected',         'Rejected'

    asset           = models.OneToOneField(
                          Asset, on_delete=models.PROTECT,
                          related_name='disposal',
                      )
    disposal_type   = models.CharField(
                          max_length=15, choices=DisposalType.choices,
                          default=DisposalType.SALE,
                      )
    disposal_date   = models.DateField()
    proceeds        = models.DecimalField(
                          max_digits=18, decimal_places=2, default=ZERO,
                          help_text='Cash or other consideration received (zero for write-off)',
                      )
    bank_account    = models.ForeignKey(
                          Account, on_delete=models.PROTECT,
                          related_name='+', null=True, blank=True,
                          help_text='Bank account proceeds were paid into (sale only)',
                      )
    cost_at_disposal              = models.DecimalField(max_digits=18, decimal_places=2)
    accumulated_depr_at_disposal  = models.DecimalField(max_digits=18, decimal_places=2)
    nbv_at_disposal               = models.DecimalField(max_digits=18, decimal_places=2)
    gain_loss                     = models.DecimalField(
                                        max_digits=18, decimal_places=2,
                                        help_text='Positive = gain, negative = loss',
                                    )
    journal_entry   = models.OneToOneField(
                          'ledger.JournalEntry', on_delete=models.PROTECT,
                          related_name='asset_disposal',
                          null=True, blank=True,
                      )
    notes           = models.TextField(blank=True, default='')
    posted_by       = models.ForeignKey(
                          User, on_delete=models.PROTECT,
                          related_name='asset_disposals_posted',
                      )
    # Approval workflow — disposals must be checked by a different user than
    # the one who initiated them (segregation of duties).
    status          = models.CharField(
                          max_length=20, choices=Status.choices,
                          default=Status.PENDING_APPROVAL,
                      )
    requested_by    = models.ForeignKey(
                          User, null=True, blank=True,
                          on_delete=models.SET_NULL,
                          related_name='asset_disposals_requested',
                      )
    requested_at    = models.DateTimeField(null=True, blank=True)
    approved_by     = models.ForeignKey(
                          User, null=True, blank=True,
                          on_delete=models.SET_NULL,
                          related_name='asset_disposals_approved',
                      )
    approved_at     = models.DateTimeField(null=True, blank=True)
    rejected_by     = models.ForeignKey(
                          User, null=True, blank=True,
                          on_delete=models.SET_NULL,
                          related_name='asset_disposals_rejected',
                      )
    rejected_at     = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True, default='')

    # FA-001 (CFO directive 2026-05-29) — intercompany transfer fields.
    # `recipient_company` is required only when disposal_type=TRANSFER and the
    # transfer is intercompany (sender ≠ recipient). `transfer_value` overrides
    # the default (NBV) when set; the variance vs NBV posts as gain/loss.
    recipient_company = models.ForeignKey(
                            'core.Company', on_delete=models.PROTECT,
                            null=True, blank=True,
                            related_name='asset_disposals_received',
                            help_text='Receiving company on a TRANSFER disposal. '
                                      'Required when disposal_type=transfer and '
                                      'intercompany.',
                        )
    transfer_value    = models.DecimalField(
                            max_digits=18, decimal_places=2,
                            null=True, blank=True,
                            help_text='Override transfer value for intercompany '
                                      'transfers. NULL = transfer at NBV (no '
                                      'gain/loss). When set, the variance vs NBV '
                                      'posts as gain or loss.',
                        )

    class Meta(BaseModel.Meta):
        ordering = ['-disposal_date']
        verbose_name = 'Asset Disposal'
        verbose_name_plural = 'Asset Disposals'

    def __str__(self):
        return f"{self.asset.tag_number} disposed {self.disposal_date} ({self.status})"


# ---------------------------------------------------------------------------
# Odoo import staging
# ---------------------------------------------------------------------------

class AssetSignOff(AuditableMixin, BaseModel):
    """
    Internal-control sign-off on a fixed-asset event:

    - FULLY_DEPRECIATED_REVIEW: any active asset whose NBV has hit salvage but
      is still in use must be re-assessed by the manager AND the finance
      manager. They jointly confirm that the asset is genuinely still in
      service and that no impairment / write-off is needed. Two distinct
      signatures required (segregation of duties).

    - SEMI_ANNUAL_COUNT: the CFO must sign off on the physical asset count
      every six months. Reminders fire at 14/7/1 days before due, and the
      dashboard shows a red banner once it goes overdue.

    The dashboard aggregator surfaces every open record so the CFO can act
    from one place.
    """

    class Kind(models.TextChoices):
        FULLY_DEPRECIATED_REVIEW = 'fully_depreciated_review', 'Fully-depreciated asset still in use'
        SEMI_ANNUAL_COUNT        = 'semi_annual_count',        'Semi-annual asset count'

    class Status(models.TextChoices):
        PENDING            = 'pending',            'Pending'
        PARTIALLY_SIGNED   = 'partially_signed',   'Partially signed'
        COMPLETED          = 'completed',          'Completed'
        OVERDUE            = 'overdue',            'Overdue'
        CANCELLED          = 'cancelled',          'Cancelled'

    kind         = models.CharField(max_length=30, choices=Kind.choices)
    asset        = models.ForeignKey(
                       Asset, on_delete=models.PROTECT,
                       related_name='signoffs', null=True, blank=True,
                       help_text='Set for FULLY_DEPRECIATED_REVIEW. NULL for company-wide '
                                 'SEMI_ANNUAL_COUNT.',
                   )
    period_label = models.CharField(
                       max_length=40,
                       help_text='e.g. "H1-2026" for half-year count, or asset tag for '
                                 'fully-depreciated reviews.',
                   )
    due_date     = models.DateField()
    company      = models.ForeignKey(
                       Company, on_delete=models.PROTECT,
                       related_name='asset_signoffs', null=True, blank=True,
                   )

    # First signature — the operational/department manager
    first_signed_by   = models.ForeignKey(
                            User, null=True, blank=True,
                            on_delete=models.SET_NULL,
                            related_name='asset_signoffs_first',
                        )
    first_signed_at   = models.DateTimeField(null=True, blank=True)
    first_role_label  = models.CharField(max_length=80, blank=True, default='Manager')

    # Second signature — Finance Manager / CFO
    second_signed_by  = models.ForeignKey(
                            User, null=True, blank=True,
                            on_delete=models.SET_NULL,
                            related_name='asset_signoffs_second',
                        )
    second_signed_at  = models.DateTimeField(null=True, blank=True)
    second_role_label = models.CharField(max_length=80, blank=True, default='Finance Manager')

    status            = models.CharField(
                            max_length=20, choices=Status.choices, default=Status.PENDING,
                        )

    # Half-year count specifics
    counted_assets       = models.PositiveIntegerField(
                               default=0,
                               help_text='Number of assets physically inspected during this count.',
                           )
    discrepancies_text   = models.TextField(
                               blank=True, default='',
                               help_text='Free-text description of any discrepancies found '
                                         '(missing, damaged, location mismatch).',
                           )

    notes             = models.TextField(blank=True, default='')

    last_reminder_at  = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ['due_date', '-created_at']
        verbose_name        = 'Asset Sign-Off'
        verbose_name_plural = 'Asset Sign-Offs'
        indexes = [
            models.Index(fields=['kind', 'status']),
            models.Index(fields=['due_date']),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} — {self.period_label} ({self.status})"

    @property
    def is_complete(self):
        return self.first_signed_by_id is not None and self.second_signed_by_id is not None

    @property
    def is_overdue(self):
        from django.utils import timezone
        return (
            self.status not in (self.Status.COMPLETED, self.Status.CANCELLED)
            and self.due_date < timezone.localdate()
        )


class AssetImportBatch(AuditableMixin, BaseModel):
    """
    Tracks a bulk import (CSV / XLSX) of assets — typically from Odoo.

    Workflow now requires DUAL AUTHORISATION:
        1. POST file to /api/v1/asset-imports/preview/
        2. POST /api/v1/asset-imports/{id}/approve/  (first approver)
        3. POST /api/v1/asset-imports/{id}/approve/  (second approver — must be a
                                                      different person)
        4. POST /api/v1/asset-imports/{id}/commit/   (any approver, after both
                                                      approvals are recorded)

    The two approvers must hold an approver title (CFO / Finance Manager /
    Financial Controller) and they must NOT be the same user. Internal
    control: imports must never be a single-person action.
    """

    class Status(models.TextChoices):
        DRAFT              = 'draft',              'Draft (preview only)'
        PARTIALLY_APPROVED = 'partially_approved', 'Awaiting second approval'
        APPROVED           = 'approved',           'Fully approved — ready to commit'
        COMMITTED          = 'committed',          'Committed'
        REJECTED           = 'rejected',           'Rejected'
        FAILED             = 'failed',             'Failed'

    source            = models.CharField(
                            max_length=40, default='odoo',
                            help_text='odoo / sage / manual / etc.',
                        )
    file_name         = models.CharField(max_length=255, blank=True, default='')
    cutover_date      = models.DateField(
                            help_text='Migration cutover. Forward depreciation starts after this date.',
                        )
    rows_total        = models.PositiveIntegerField(default=0)
    rows_valid        = models.PositiveIntegerField(default=0)
    rows_invalid      = models.PositiveIntegerField(default=0)
    rows_imported     = models.PositiveIntegerField(default=0)
    parsed_rows       = models.JSONField(
                            default=list, blank=True,
                            help_text='List of parsed row dicts (preview payload).',
                        )
    validation_errors = models.JSONField(
                            default=list, blank=True,
                            help_text='List of {row_index, field, message} dicts.',
                        )
    status            = models.CharField(
                            max_length=20, choices=Status.choices,
                            default=Status.DRAFT,
                        )
    company           = models.ForeignKey(
                            Company, on_delete=models.PROTECT,
                            related_name='asset_import_batches',
                        )
    created_by        = models.ForeignKey(
                            User, on_delete=models.PROTECT,
                            related_name='asset_import_batches',
                        )
    # Dual authorisation — each commit requires two distinct approvers
    first_approved_by  = models.ForeignKey(
                             User, null=True, blank=True,
                             on_delete=models.SET_NULL,
                             related_name='asset_imports_first_approved',
                         )
    first_approved_at  = models.DateTimeField(null=True, blank=True)
    second_approved_by = models.ForeignKey(
                             User, null=True, blank=True,
                             on_delete=models.SET_NULL,
                             related_name='asset_imports_second_approved',
                         )
    second_approved_at = models.DateTimeField(null=True, blank=True)
    rejected_by        = models.ForeignKey(
                             User, null=True, blank=True,
                             on_delete=models.SET_NULL,
                             related_name='asset_imports_rejected',
                         )
    rejected_at        = models.DateTimeField(null=True, blank=True)
    rejection_reason   = models.TextField(blank=True, default='')
    committed_at      = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']
        verbose_name        = 'Asset Import Batch'
        verbose_name_plural = 'Asset Import Batches'

    def __str__(self):
        return f"Import {self.file_name or self.source} ({self.status})"

    @property
    def is_fully_approved(self):
        return (
            self.first_approved_by_id is not None
            and self.second_approved_by_id is not None
            and self.first_approved_by_id != self.second_approved_by_id
        )


class AssetAssignment(AuditableMixin, BaseModel):
    """Append-only hand-over log (CFO 2026-06-26): who held an asset, where, and
    when. One row per custodian/location transfer, so the chain A → B → C is
    auditable. Never edited or deleted — it is the asset's custody history.
    """
    asset          = models.ForeignKey(Asset, on_delete=models.CASCADE,
                                        related_name='assignments')
    from_employee  = models.ForeignKey('payroll.Employee', null=True, blank=True,
                                        on_delete=models.SET_NULL,
                                        related_name='asset_transfers_out')
    to_employee    = models.ForeignKey('payroll.Employee', null=True, blank=True,
                                        on_delete=models.SET_NULL,
                                        related_name='asset_transfers_in')
    # Text snapshots so the history is readable even if a staff record changes/leaves.
    from_custodian = models.CharField(max_length=200, blank=True, default='')
    to_custodian   = models.CharField(max_length=200, blank=True, default='')
    from_location  = models.CharField(max_length=200, blank=True, default='')
    to_location    = models.CharField(max_length=200, blank=True, default='')
    reason         = models.CharField(max_length=300, blank=True, default='')
    transferred_at = models.DateField()
    transferred_by = models.ForeignKey(User, on_delete=models.PROTECT,
                                       related_name='asset_transfers_made')

    class Meta(BaseModel.Meta):
        ordering = ['-transferred_at', '-created_at']
        verbose_name = 'Asset Assignment'
        verbose_name_plural = 'Asset Assignments'

    def __str__(self):
        return (f"{self.asset.tag_number}: {self.from_custodian or '—'} → "
                f"{self.to_custodian or '—'} ({self.transferred_at})")


# ---------------------------------------------------------------------------
# Componentisation helpers — assets/component_models.py
# ---------------------------------------------------------------------------
# Imported here so callers can `from assets.models import
# walk_total_monthly_depreciation` and so the helper module is loaded
# alongside the model registrations.
from assets.component_models import walk_total_monthly_depreciation  # noqa: E402,F401

# ---------------------------------------------------------------------------
# Asset Control & Handover models — assets/control_models.py
# ---------------------------------------------------------------------------
# Imported here so Django registers the control-lifecycle models
# (AssetRequisition / AssetHandover / AssetControlPolicy) with the assets app.
from assets.control_models import (  # noqa: E402,F401
    AssetControlPolicy,
    AssetRequisition,
    AssetHandover,
)
