"""salvage/models.py

Salvage Portal data model — port of motor-liquidators (SQLite) to Postgres.
See .claude/specs/salvage-portal/design.md.

Phase 1: PartCategory, VehicleBrand, VehicleModel, SalvageItem, SalvageImage.
Phase 2 (2026-05-18): BuyerQuote, Sale, SalvageApproval, item-location fields.
"""
import re
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import IntegrityError, models, transaction

from core.models import AuditableMixin, BaseModel, Company

#: The yard's own numbering, unbroken since the Motor Liquidators SQLite
#: import — ML-0001 … ML-0051 as at 17-Sep-2026. Bharath asked for it to be
#: generated rather than typed ("Manually doing this might end up in getting
#: duplicates"), so new items continue the same stream rather than starting a
#: second one the yard staff would not recognise.
ITEM_CODE_PREFIX = 'ML-'
ITEM_CODE_WIDTH  = 4
_ITEM_CODE_RE    = re.compile(rf'^{re.escape(ITEM_CODE_PREFIX)}(\d+)$')


def next_item_code():
    """The next free ``SalvageItem.item_code`` in the ML-#### stream.

    Same shape as the house generators (``payments._generate_payment_number``,
    ``ledger._generate_entry_number``): lock the rows carrying the prefix,
    read the highest tail, add one. ``item_code`` is globally unique — one
    index, no company in it (salvage/migrations/0001_initial.py:63) — so this
    is one stream across every entity.

    Ranked on the NUMBER, not the string: ``order_by('-item_code')`` would put
    'ML-9' above 'ML-10' the day the yard runs past ML-9999 and the padding
    gives out. Callers must already be inside ``transaction.atomic()``.
    """
    codes = (
        SalvageItem.objects
        .select_for_update()
        .filter(item_code__startswith=ITEM_CODE_PREFIX)
        .values_list('item_code', flat=True)
    )
    highest = 0
    for code in codes:
        m = _ITEM_CODE_RE.match(code or '')
        if m:
            highest = max(highest, int(m.group(1)))
    return f'{ITEM_CODE_PREFIX}{highest + 1:0{ITEM_CODE_WIDTH}d}'


class PartCategory(BaseModel):
    name        = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, default='')
    is_active   = models.BooleanField(default=True)
    # CFO directive 2026-05-24: reserve auto-suggest uses this multiplier
    # against the linked claim's gross amount. Tuneable per category so
    # high-recovery parts (engines, gearboxes) can sit above low-recovery
    # parts (interior trim, scrap body panels). Default 0.30 = 30%.
    expected_recovery_pct = models.DecimalField(
                                max_digits=5, decimal_places=4,
                                default=Decimal('0.3000'),
                                help_text=(
                                    'Default expected recovery as a fraction of '
                                    'claim gross. Drives suggest_reserve().'
                                ),
                            )

    class Meta(BaseModel.Meta):
        ordering            = ['name']
        verbose_name_plural = 'Part categories'

    def __str__(self):
        return self.name


class VehicleBrand(BaseModel):
    name      = models.CharField(max_length=80, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta(BaseModel.Meta):
        ordering = ['name']

    def __str__(self):
        return self.name


class VehicleModel(BaseModel):
    brand     = models.ForeignKey(
                    VehicleBrand, on_delete=models.PROTECT, related_name='models',
                )
    name      = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)

    class Meta(BaseModel.Meta):
        ordering = ['brand__name', 'name']
        constraints = [
            models.UniqueConstraint(
                fields=['brand', 'name'], name='uniq_vehicle_model_per_brand',
            ),
        ]

    def __str__(self):
        return f"{self.brand.name} {self.name}"


class SalvageItem(AuditableMixin, BaseModel):
    """A single piece of salvage stock — vehicle or part."""

    class Condition(models.TextChoices):
        EXCELLENT = 'excellent', 'Excellent'
        GOOD      = 'good',      'Good'
        FAIR      = 'fair',      'Fair'
        POOR      = 'poor',      'Poor'
        SCRAP     = 'scrap',     'Scrap'

    class Status(models.TextChoices):
        AVAILABLE   = 'available',   'Available'
        QUOTED      = 'quoted',      'Quoted (offer received)'
        RESERVED    = 'reserved',    'Reserved'
        SOLD        = 'sold',        'Sold'
        WRITTEN_OFF = 'written_off', 'Written off'
        DISPOSED    = 'disposed',    'Disposed'
        SCRAPPED    = 'scrapped',    'Scrapped'
        ON_HOLD     = 'on_hold',     'On hold'
        VOIDED      = 'voided',      'Voided (data-entry correction)'

    # blank=True since 17-Sep-2026: leave it empty and save() draws the next
    # ML-#### itself. Still unique, still never actually stored blank.
    item_code        = models.CharField(max_length=40, unique=True,
                                        blank=True, default='')
    claim_number     = models.CharField(max_length=60, blank=True, default='')
    policy_number    = models.CharField(max_length=60, blank=True, default='')

    category         = models.ForeignKey(
                           PartCategory, on_delete=models.PROTECT,
                           null=True, blank=True, related_name='items',
                       )
    part_name        = models.CharField(max_length=200)
    part_description = models.TextField(blank=True, default='')
    quantity         = models.PositiveIntegerField(default=1)

    vehicle_brand    = models.ForeignKey(
                           VehicleBrand, on_delete=models.PROTECT,
                           null=True, blank=True, related_name='items',
                       )
    vehicle_model    = models.ForeignKey(
                           VehicleModel, on_delete=models.PROTECT,
                           null=True, blank=True, related_name='items',
                       )
    vehicle_year     = models.PositiveIntegerField(null=True, blank=True)
    vehicle_colour   = models.CharField(max_length=40, blank=True, default='')
    vin_number       = models.CharField(max_length=50, blank=True, default='')

    condition        = models.CharField(
                           max_length=12, choices=Condition.choices,
                           default=Condition.FAIR,
                       )
    asking_price     = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    reserve_price    = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    # Carrying value on the balance sheet (CFO directive 2026-05-18).
    # Set at intake = recoverable salvage value estimate (typically the
    # reserve_price). Updated on NRV writedown. On sale, BS releases
    # `cost_basis` and any delta vs sale_price hits Gain/Loss on Salvage.
    cost_basis       = models.DecimalField(
                           max_digits=18, decimal_places=2, default=0,
                           help_text='Carrying value on BS. Released on sale; gain/loss to P&L.',
                       )
    intake_posted_at = models.DateTimeField(null=True, blank=True)
    intake_journal_entry = models.ForeignKey(
                           'ledger.JournalEntry', on_delete=models.SET_NULL,
                           null=True, blank=True,
                           related_name='+',
                           help_text='Auto-posted JE when item was first taken into inventory.',
                       )
    # NRV impairment trail (CFO directive 2026-05-24, Track-B audit).
    # IAS 2 + IFRS 17 expect impairment when net realisable value drops
    # below carrying amount. `cost_basis` is reduced; the delta hits
    # Loss-on-salvage P&L via a JE posted in salvage/services.write_down_to_nrv().
    impaired_at         = models.DateTimeField(null=True, blank=True)
    impaired_total_bwp  = models.DecimalField(
                              max_digits=18, decimal_places=2, default=0,
                              help_text='Cumulative impairment recognised on this item.',
                          )

    status           = models.CharField(
                           max_length=15, choices=Status.choices,
                           default=Status.AVAILABLE,
                       )
    # Location (legacy single `location` is kept for back-compat; new
    # imports populate yard_section + shelf_row from motor-liquidators).
    location         = models.CharField(max_length=80, blank=True, default='')
    yard_section     = models.CharField(max_length=40, blank=True, default='')
    shelf_row        = models.CharField(max_length=40, blank=True, default='')

    # Lifecycle dates
    received_date    = models.DateField(null=True, blank=True)
    sold_date        = models.DateField(null=True, blank=True)
    disposed_date    = models.DateField(null=True, blank=True)

    company          = models.ForeignKey(
                           Company, on_delete=models.PROTECT,
                           related_name='salvage_items', null=True, blank=True,
                       )
    received_by      = models.ForeignKey(
                           User, on_delete=models.SET_NULL, null=True, blank=True,
                           related_name='salvage_items_received',
                       )
    created_by       = models.ForeignKey(
                           User, on_delete=models.SET_NULL, null=True, blank=True,
                           related_name='salvage_items_created',
                       )
    posted_at        = models.DateTimeField(null=True, blank=True)
    notes            = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']
        indexes  = [
            models.Index(fields=['status']),
            models.Index(fields=['condition']),
            models.Index(fields=['company']),
        ]

    def save(self, *args, **kwargs):
        """Fill a blank ``item_code`` before the row is written.

        Put here rather than in the serializer so EVERY write path is covered
        — the REST create, the spreadsheet import, the management-command
        import and the Django admin all funnel through save(). A code the
        caller supplied is never touched, which keeps
        ``import_motor_liquidators`` idempotent on the legacy ML-#### codes.

        Under READ COMMITTED a waiter re-reads the same rows it was blocked
        on and computes the same number, so it is the RETRY below — not the
        lock — that actually settles a race. The unique index is the real
        guard; we retry on it rather than handing the operator a 500.
        """
        if self.item_code and self.item_code.strip():
            return super().save(*args, **kwargs)

        # Generate on INSERT only. Blanking the code of an existing item and
        # saving would otherwise renumber it — ML-0007 silently becomes
        # ML-0008 and the label on the physical part in the yard stops
        # matching the record. Only reachable from the Django admin (the API
        # has no update verb), but silent renumbering is not something to
        # leave lying around.
        if not self._state.adding:
            raise ValueError(
                'item_code cannot be blanked on an existing salvage item.'
            )

        last_error = None
        for _ in range(5):
            try:
                with transaction.atomic():
                    self.item_code = next_item_code()
                    return super().save(*args, **kwargs)
            except IntegrityError as exc:          # noqa: PERF203
                if 'item_code' not in str(exc):
                    raise
                # Someone else took the number between our read and our
                # write. Drop it and look again.
                self.item_code = ''
                last_error = exc
        raise last_error

    def __str__(self):
        return f"{self.item_code} · {self.part_name}"


class SalvageImage(BaseModel):
    item     = models.ForeignKey(
                   SalvageItem, on_delete=models.CASCADE, related_name='images',
               )
    image    = models.ImageField(upload_to='salvage/%Y/%m/')
    caption  = models.CharField(max_length=200, blank=True, default='')
    ordering = models.PositiveIntegerField(default=0)

    class Meta(BaseModel.Meta):
        ordering = ['ordering', 'created_at']

    def __str__(self):
        return f"image for {self.item.item_code}"


# ---------------------------------------------------------------------------
# BuyerQuote — public-facing offers (port of motor-liquidators.buyer_quotes)
# ---------------------------------------------------------------------------
class BuyerQuote(BaseModel):
    """An offer submitted by a member of the public via the salvage portal.

    Goes through a review workflow (pending → under_review → accepted /
    rejected / countered). Accepted quotes feed the Sale row when payment
    is collected.
    """

    class Status(models.TextChoices):
        PENDING       = 'pending',       'Pending review'
        UNDER_REVIEW  = 'under_review',  'Under review'
        ACCEPTED      = 'accepted',      'Accepted'
        REJECTED      = 'rejected',      'Rejected'
        COUNTERED     = 'countered',     'Countered'

    item           = models.ForeignKey(
                         SalvageItem, on_delete=models.PROTECT,
                         related_name='buyer_quotes',
                     )
    buyer_name     = models.CharField(max_length=200)
    buyer_email    = models.EmailField(max_length=254, blank=True, default='')
    buyer_phone    = models.CharField(max_length=40)
    buyer_company  = models.CharField(max_length=200, blank=True, default='')

    offered_price  = models.DecimalField(max_digits=18, decimal_places=2)
    message        = models.TextField(blank=True, default='')

    status         = models.CharField(
                         max_length=15, choices=Status.choices,
                         default=Status.PENDING,
                     )
    counter_price  = models.DecimalField(
                         max_digits=18, decimal_places=2, null=True, blank=True,
                         help_text='Set when status=countered',
                     )

    reviewed_by    = models.ForeignKey(
                         User, on_delete=models.SET_NULL, null=True, blank=True,
                         related_name='salvage_quotes_reviewed',
                     )
    review_notes   = models.TextField(blank=True, default='')
    reviewed_at    = models.DateTimeField(null=True, blank=True)

    # Audit
    submitter_ip   = models.GenericIPAddressField(null=True, blank=True)
    submitter_ua   = models.CharField(max_length=400, blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['item']),
        ]

    def __str__(self):
        return f"{self.buyer_name} → {self.item.item_code} @ {self.offered_price}"


# ---------------------------------------------------------------------------
# Sale — completed transaction
# ---------------------------------------------------------------------------
class Sale(BaseModel):
    """A salvage item sale — terminal posting after CFO / EXCO approval."""

    class PaymentMethod(models.TextChoices):
        CASH         = 'cash',         'Cash'
        EFT          = 'eft',          'EFT / bank transfer'
        CHEQUE       = 'cheque',       'Cheque'
        MOBILE_MONEY = 'mobile_money', 'Mobile money'
        CARD         = 'card',         'Card'

    item            = models.ForeignKey(
                          SalvageItem, on_delete=models.PROTECT,
                          related_name='sales',
                      )
    buyer_quote     = models.ForeignKey(
                          BuyerQuote, on_delete=models.PROTECT,
                          null=True, blank=True, related_name='sales',
                      )
    buyer_name      = models.CharField(max_length=200)
    buyer_phone     = models.CharField(max_length=40, blank=True, default='')
    buyer_email     = models.EmailField(max_length=254, blank=True, default='')

    sale_price      = models.DecimalField(max_digits=18, decimal_places=2)
    payment_method  = models.CharField(
                          max_length=15, choices=PaymentMethod.choices,
                      )
    payment_ref     = models.CharField(max_length=120, blank=True, default='')

    approved_by     = models.ForeignKey(
                          User, on_delete=models.SET_NULL, null=True, blank=True,
                          related_name='salvage_sales_approved',
                      )
    sold_by         = models.ForeignKey(
                          User, on_delete=models.SET_NULL, null=True, blank=True,
                          related_name='salvage_sales_sold',
                      )
    sale_date       = models.DateField()
    notes           = models.TextField(blank=True, default='')

    # Once a Sale is posted, optionally link the resulting GL journal entry
    # for traceability (kept loose to avoid hard dependency between apps).
    journal_entry   = models.OneToOneField(
                          'ledger.JournalEntry', on_delete=models.SET_NULL,
                          null=True, blank=True, related_name='salvage_sale',
                      )

    class Meta(BaseModel.Meta):
        ordering = ['-sale_date', '-created_at']
        indexes = [
            models.Index(fields=['sale_date']),
            models.Index(fields=['item']),
        ]

    def __str__(self):
        return f"Sale {self.item.item_code} → {self.buyer_name} @ {self.sale_price}"


# ---------------------------------------------------------------------------
# SalvageApproval — EXCO approval workflow for an item or quote
# ---------------------------------------------------------------------------
class SalvageApproval(BaseModel):
    """A pending-approval row that gates a high-value sale.

    Triggered when an accepted BuyerQuote exceeds a threshold the CFO has
    set (or when a Sale is created for a reserve-priced item below
    reserve). Approver is a user with role EXCO / CFO.
    """

    class Kind(models.TextChoices):
        SALE          = 'sale',          'Sale below reserve'
        QUOTE_ACCEPT  = 'quote_accept',  'Quote acceptance'
        DISPOSAL      = 'disposal',      'Disposal / write-off'

    class Status(models.TextChoices):
        PENDING  = 'pending',  'Pending'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'

    kind           = models.CharField(
                         max_length=15, choices=Kind.choices, default=Kind.SALE,
                     )
    item           = models.ForeignKey(
                         SalvageItem, on_delete=models.PROTECT,
                         related_name='approvals',
                     )
    buyer_quote    = models.ForeignKey(
                         BuyerQuote, on_delete=models.PROTECT, null=True,
                         blank=True, related_name='approvals',
                     )
    sale           = models.ForeignKey(
                         Sale, on_delete=models.PROTECT, null=True,
                         blank=True, related_name='approvals',
                     )
    requested_by   = models.ForeignKey(
                         User, on_delete=models.PROTECT,
                         related_name='salvage_approvals_requested',
                     )
    approved_by    = models.ForeignKey(
                         User, on_delete=models.SET_NULL, null=True, blank=True,
                         related_name='salvage_approvals_resolved',
                     )

    status         = models.CharField(
                         max_length=15, choices=Status.choices, default=Status.PENDING,
                     )
    requested_amount = models.DecimalField(
                           max_digits=18, decimal_places=2, default=Decimal('0'),
                       )
    threshold_amount = models.DecimalField(
                           max_digits=18, decimal_places=2, default=Decimal('0'),
                           help_text='Approval threshold in force at request time',
                       )
    notes          = models.TextField(blank=True, default='')
    resolved_at    = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['kind']),
        ]

    def __str__(self):
        return f"{self.kind} approval — {self.item.item_code} [{self.status}]"


# ---------------------------------------------------------------------------
# SalvageInspection — pre-sale inspection report attached to a SalvageItem
# ---------------------------------------------------------------------------
class SalvageInspection(BaseModel):
    """A structured inspection report for a salvage item.

    Captured by yard staff before listing the item publicly. The
    inspection summary is surfaced on the public storefront so buyers
    see the condition transparently. One item can have multiple
    inspections over time (e.g. before/after a refurb pass) — the most
    recent is the one shown.
    """

    class SalvageTitleStatus(models.TextChoices):
        CLEAN    = 'clean',    'Clean title'
        SALVAGE  = 'salvage',  'Salvage title'
        REBUILT  = 'rebuilt',  'Rebuilt title'
        JUNK     = 'junk',     'Junk title'

    item                 = models.ForeignKey(
                               SalvageItem, on_delete=models.CASCADE,
                               related_name='inspections',
                           )
    inspected_by         = models.ForeignKey(
                               User, on_delete=models.SET_NULL,
                               null=True, blank=True,
                               related_name='salvage_inspections',
                           )
    inspected_at         = models.DateTimeField()
    runs_drives          = models.BooleanField(default=False)
    mileage_km           = models.PositiveIntegerField(null=True, blank=True)
    key_present          = models.BooleanField(default=False)
    # Flexible per-panel grading (e.g. {"front_bumper": "dented",
    # "left_door": "ok", "roof": "creased"}). JSONField keeps the
    # storage open so the form can evolve without a migration.
    body_panels_jsonb    = models.JSONField(default=dict, blank=True)
    engine_status        = models.CharField(max_length=80, blank=True, default='')
    transmission_status  = models.CharField(max_length=80, blank=True, default='')
    airbags_deployed     = models.BooleanField(default=False)
    salvage_title_status = models.CharField(
                               max_length=12,
                               choices=SalvageTitleStatus.choices,
                               default=SalvageTitleStatus.SALVAGE,
                           )
    notes                = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-inspected_at', '-created_at']
        indexes  = [
            models.Index(fields=['item']),
            models.Index(fields=['inspected_at']),
        ]

    def __str__(self):
        return f"Inspection {self.item.item_code} @ {self.inspected_at:%Y-%m-%d}"


# ---------------------------------------------------------------------------
# Auction surface — defined in salvage/auction_models.py; imported here so
# Django's app registry picks the models up under the `salvage` app label.
# ---------------------------------------------------------------------------
from .auction_models import Bid, SalvageAuction  # noqa: E402,F401

# ---------------------------------------------------------------------------
# Veritas Parts & Assessment Savings — defined in salvage/parts_models.py.
# ---------------------------------------------------------------------------
from .parts_models import (  # noqa: E402,F401
    AssessmentSaving, PartsContractPricing, PartsHistory, PartsSpend, PartsUpload,
)
