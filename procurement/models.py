"""
procurement/models.py

Purchase Order, Goods Receipt Note, and 3-way match models.

A Purchase Order (PO) is a legal commitment from Alpha Direct to a supplier,
issued BEFORE goods or services are delivered. It serves three functions:

  1. Authorisation — every spend is pre-approved (FM + CFO joint approval).
  2. Audit trail — Request -> PO -> GRN -> Bill -> Payment chain is preserved.
  3. 3-way match — when the supplier's bill arrives, finance verifies
     PO (what we agreed) ↔ GRN (what we received) ↔ Bill (what's invoiced).

Models:
  - PurchaseOrder       header
  - PurchaseOrderLine   one line per item / service ordered
  - GoodsReceiptNote    receipt header
  - GoodsReceiptNoteLine receipt against a specific PO line
  - POBillMatch         link record between a PO and a posted vendor bill

Every monetary field: DecimalField(max_digits=18, decimal_places=2).

INTERNAL CONTROL — joint approval required:
  - DRAFT -> submit_for_approval -> PENDING_FM_APPROVAL
  - PENDING_FM_APPROVAL -> fm_approve -> PENDING_CFO_APPROVAL
  - PENDING_CFO_APPROVAL -> cfo_approve -> APPROVED
  - The CFO approver must be a different user from both the creator and the FM.
"""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal, ROUND_HALF_UP

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from core.models import AuditLog, AuditableMixin, BaseModel, Currency


TWO_PLACES = Decimal('0.01')
ZERO       = Decimal('0.00')


def _bwp(amount, rate):
    """Convert *amount* in foreign currency to BWP at *rate*, rounded to 2dp."""
    return (amount * rate).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# PO number generator
# ---------------------------------------------------------------------------

# Purchase orders are raised by Claims, Admin, and HR only.
# Finance does NOT raise POs — Finance (FM + CFO) verifies and approves them.
_DEPT_PREFIX = {
    'admin':  'ADM',
    'claims': 'CLM',
    'hr':     'HR',
}


def _generate_po_number(department):
    """Auto-incrementing PO number, e.g. PO-OPS-2026-000001."""
    dept   = _DEPT_PREFIX.get(department, 'OTH')
    year   = timezone.now().year
    prefix = f"PO-{dept}-{year}-"
    with transaction.atomic():
        last = (
            PurchaseOrder.objects
            .select_for_update()
            .filter(po_number__startswith=prefix)
            .order_by('-po_number')
            .values_list('po_number', flat=True)
            .first()
        )
        next_seq = int(last.rsplit('-', 1)[-1]) + 1 if last else 1
        return f"{prefix}{next_seq:06d}"


def _generate_grn_number():
    """Auto-incrementing GRN number, e.g. GRN-2026-000001."""
    year   = timezone.now().year
    prefix = f"GRN-{year}-"
    with transaction.atomic():
        last = (
            GoodsReceiptNote.objects
            .select_for_update()
            .filter(grn_number__startswith=prefix)
            .order_by('-grn_number')
            .values_list('grn_number', flat=True)
            .first()
        )
        next_seq = int(last.rsplit('-', 1)[-1]) + 1 if last else 1
        return f"{prefix}{next_seq:06d}"


# ---------------------------------------------------------------------------
# Purchase Order
# ---------------------------------------------------------------------------

class PurchaseOrder(AuditableMixin, BaseModel):
    """
    A purchase commitment to a supplier. Once APPROVED, the PO is the basis
    for goods receipt and bill matching.

    Currency:
      - currency_code is the PO's transaction currency (BWP, USD, ZAR, etc.)
      - exchange_rate is locked at issue date — does NOT revalue afterwards.
      - All BWP-equivalent fields are computed at this rate.

    The fiscal_period field locks the PO to the period it was raised in,
    which the period-close workflow uses to refuse closing periods that
    still contain unmatched approved POs.
    """

    class Department(models.TextChoices):
        # Only these three departments may raise POs. Finance does not raise
        # POs — it verifies and approves them via the FM + CFO workflow.
        ADMIN  = 'admin',  'Admin'
        CLAIMS = 'claims', 'Claims'
        HR     = 'hr',     'Human Resources'

    class Status(models.TextChoices):
        DRAFT                  = 'draft',                  'Draft'
        PENDING_FM_APPROVAL    = 'pending_fm_approval',    'Pending FM Approval'
        PENDING_CFO_APPROVAL   = 'pending_cfo_approval',   'Pending CFO Approval'
        REJECTED               = 'rejected',               'Rejected'
        APPROVED               = 'approved',               'Approved'
        PARTIALLY_RECEIVED     = 'partially_received',     'Partially Received'
        FULLY_RECEIVED         = 'fully_received',         'Fully Received'
        CLOSED                 = 'closed',                 'Closed'
        CANCELLED              = 'cancelled',              'Cancelled'
        # Manus PO Audit #8 — PO Expiry / validity period (CFO 2026-05-20).
        EXPIRED                = 'expired',                'Expired (auto-closed)'

    po_number       = models.CharField(max_length=30, unique=True, blank=True)
    department      = models.CharField(max_length=15, choices=Department.choices)
    supplier        = models.ForeignKey(
                          'billing.Contact', on_delete=models.PROTECT,
                          related_name='purchase_orders',
                          help_text='Vendor or broker the PO is issued to. '
                                    'Must have contact_type=vendor or broker.',
                      )
    company         = models.ForeignKey(
                          'core.Company', on_delete=models.PROTECT,
                          related_name='purchase_orders', null=True, blank=True,
                      )
    issue_date      = models.DateField()
    expected_delivery_date = models.DateField(null=True, blank=True)
    currency_code   = models.ForeignKey(
                          Currency, on_delete=models.PROTECT,
                          related_name='purchase_orders', default='BWP',
                      )
    exchange_rate   = models.DecimalField(
                          max_digits=18, decimal_places=8,
                          default=Decimal('1.00000000'),
                          help_text='Locked at issue. Does not revalue.',
                      )
    subtotal        = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    tax_total       = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    total_amount    = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    total_bwp       = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    # Discount (CFO directive 2026-07-08 — Wame/Native Events could not apply a
    # 5% discount). One uniform % applied to every line BEFORE VAT, so the VAT
    # and total_amount (and therefore the commitment GL) are all struck on the
    # discounted figures. `subtotal` is stored NET of the discount, so the
    # total_amount formula and the GL posting are unchanged. `discount_total`
    # is display-only (the money taken off). 0 = no discount (legacy default).
    discount_percent = models.DecimalField(
                          max_digits=5, decimal_places=2, default=ZERO,
                          help_text='Uniform % discount on every line before VAT (0-100).',
                      )
    discount_total   = models.DecimalField(
                          max_digits=18, decimal_places=2, default=ZERO,
                          help_text='Sum of per-line discount amounts. Display only; '
                                    'subtotal is already net of it.',
                      )
    status          = models.CharField(
                          max_length=22, choices=Status.choices, default=Status.DRAFT,
                      )

    # Manus PO Audit Phase-B 2026-05-20
    # #7 Blanket PO — pre-approved standing commitment that absorbs
    # multiple GRNs / bills up to blanket_limit until valid_until expires.
    is_blanket      = models.BooleanField(default=False)
    blanket_limit   = models.DecimalField(
                          max_digits=18, decimal_places=2, default=ZERO,
                          help_text='BWP ceiling for blanket draws. '
                                    'Ignored when is_blanket=False.',
                      )
    # #8 PO expiry / validity period — APPROVED POs past valid_until with
    # no remaining activity get flipped to EXPIRED by expire_stale_pos cron.
    valid_until     = models.DateField(
                          null=True, blank=True,
                          help_text='Optional. After this date the PO becomes '
                                    'eligible for auto-expiry.',
                      )
    # #4 Amendment versioning — bumped each time POAmendment.applied_at fires.
    amendment_version = models.PositiveSmallIntegerField(
                          default=1,
                          help_text='1 = original PO. Each applied amendment +1.',
                      )

    # Optional link to a claim — when a PO authorises spend on a specific claim
    # (assessor, panel-beater, recovery agent, salvage operator, etc.). Stored
    # as free text reference because the master claims register lives in
    # Graphite, not here.
    related_claim_reference = models.CharField(
                          max_length=100, blank=True, default='',
                          help_text='Graphite claim reference, when this PO '
                                    'relates to a specific claim.',
                      )
    related_claim_recovery  = models.ForeignKey(
                          'claims.Subrogation', null=True, blank=True,
                          on_delete=models.SET_NULL, related_name='purchase_orders',
                          help_text='Optional link to a recovery / subrogation case.',
                      )

    # Justification — required for spend > P5,000 to enforce a written reason
    # in the approval audit trail.
    justification   = models.TextField(blank=True, default='')

    # Approval audit trail
    fiscal_period   = models.ForeignKey(
                          'ledger.FiscalPeriod', on_delete=models.PROTECT,
                          related_name='purchase_orders', null=True, blank=True,
                          help_text='Period this PO was raised in. Locked at submission.',
                      )
    submitted_by    = models.ForeignKey(
                          User, null=True, blank=True,
                          on_delete=models.SET_NULL, related_name='pos_submitted',
                      )
    submitted_at    = models.DateTimeField(null=True, blank=True)
    fm_approved_by  = models.ForeignKey(
                          User, null=True, blank=True,
                          on_delete=models.SET_NULL, related_name='pos_fm_approved',
                      )
    fm_approved_at  = models.DateTimeField(null=True, blank=True)
    cfo_approved_by = models.ForeignKey(
                          User, null=True, blank=True,
                          on_delete=models.SET_NULL, related_name='pos_cfo_approved',
                      )
    cfo_approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True, default='')
    cancelled_by    = models.ForeignKey(
                          User, null=True, blank=True,
                          on_delete=models.SET_NULL, related_name='pos_cancelled',
                      )
    cancelled_at    = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True, default='')

    # "Sent ✓" — stamped by POEmailView on each successful supplier send
    # (CFO claims-PO email flow 2026-07-07). Read-only through the API.
    last_emailed_at = models.DateTimeField(null=True, blank=True)
    last_emailed_to = models.CharField(max_length=255, blank=True, default='')

    # GL linkage — populated by services._post_commitment_je on cfo_approve.
    # See .claude/steering/erp-relationships.md rule 4.
    commitment_journal_entry = models.ForeignKey(
                          'ledger.JournalEntry',
                          null=True, blank=True,
                          on_delete=models.PROTECT,
                          related_name='commitment_for_pos',
                          help_text='The commitment JE created when this PO was '
                                    'CFO-approved. NULL for POs approved before '
                                    'this feature shipped, cleared on cancellation.',
                      )

    created_by      = models.ForeignKey(
                          User, on_delete=models.PROTECT,
                          related_name='pos_created',
                      )

    class Meta(BaseModel.Meta):
        verbose_name        = 'Purchase Order'
        verbose_name_plural = 'Purchase Orders'
        ordering            = ['-issue_date', '-po_number']

    def __str__(self):
        return f"{self.po_number} — {self.supplier.name} ({self.total_amount} {self.currency_code_id})"

    # ---- Contact types permitted as PO supplier ----
    # POs are issued only to parties we PAY: vendors, brokers, and reinsurers.
    # Customers and employees must never be PO suppliers.
    _ALLOWED_SUPPLIER_TYPES = frozenset({'vendor', 'broker', 'reinsurer'})

    def clean(self):
        super().clean()
        if self.supplier_id and self.supplier.contact_type not in self._ALLOWED_SUPPLIER_TYPES:
            raise ValidationError({
                'supplier': (
                    f"Supplier {self.supplier.name} has contact_type "
                    f"'{self.supplier.contact_type}'. POs can only be issued to "
                    f"vendors, brokers, or reinsurers."
                )
            })

    # ------------------------------------------------------------------
    # save — auto number, immutability guard
    # ------------------------------------------------------------------

    def save(self, *args, audit_user=None, audit_ip=None, audit_description=None, **kwargs):
        # Validate supplier contact_type before persisting.
        self.clean()

        if not self.po_number:
            self.po_number = _generate_po_number(self.department)

        # Immutability: block edits to approved/closed/cancelled POs except for
        # status transitions handled by the service layer.
        if self.pk:
            try:
                db_status = PurchaseOrder.objects.values_list('status', flat=True).get(pk=self.pk)
                # Only the status field can move forward once approved
                terminal_db = db_status in (
                    self.Status.APPROVED,
                    self.Status.PARTIALLY_RECEIVED,
                    self.Status.FULLY_RECEIVED,
                    self.Status.CLOSED,
                    self.Status.CANCELLED,
                    self.Status.REJECTED,
                )
                if terminal_db and not getattr(self, '_allow_status_transition', False):
                    raise ValidationError(
                        f"{self.po_number} is {db_status} and cannot be edited."
                    )
            except PurchaseOrder.DoesNotExist:
                pass

        super().save(
            *args,
            audit_user=audit_user,
            audit_ip=audit_ip,
            audit_description=audit_description,
            **kwargs,
        )

    # ------------------------------------------------------------------
    # Computed
    # ------------------------------------------------------------------

    def recalculate_totals(self):
        """Re-sum lines into header totals. Does NOT save.

        `line_total` is already net of the PO discount (see
        PurchaseOrderLine.save), so `subtotal` here is the discounted subtotal
        and the total_amount / GL commitment need no further adjustment.
        `discount_total` is the money taken off, summed for display only.
        """
        # Re-query fresh. After an amend (serializer deletes + recreates the
        # lines) the related manager may still hold a PREFETCH CACHE of the
        # pre-edit lines — the viewset prefetches 'lines' — and summing that
        # cache re-strikes the totals on the OLD figures. Drop any cached
        # prefetch so the totals reflect the current lines.
        # (Kao PO-amend "wrong figures after changing lines" bug, 2026-07-09.)
        cache = getattr(self, '_prefetched_objects_cache', None)
        if cache:
            cache.pop('lines', None)
        lines = self.lines.all()
        self.subtotal       = sum((ln.line_total for ln in lines), ZERO)
        self.tax_total      = sum((ln.tax_amount for ln in lines), ZERO)
        self.discount_total = sum((ln.discount_amount for ln in lines), ZERO)
        self.total_amount   = self.subtotal + self.tax_total
        self.total_bwp      = _bwp(self.total_amount, self.exchange_rate)

    @property
    def is_fully_received(self):
        return all(ln.is_fully_received for ln in self.lines.all())

    @property
    def is_partially_received(self):
        return any(ln.quantity_received > ZERO for ln in self.lines.all())

    @property
    def is_fully_billed(self):
        return all(ln.is_fully_billed for ln in self.lines.all())

    @property
    def status_display_label(self):
        """Human status label. A CLAIMS PO awaiting its (single, no-CFO)
        operational approval reads 'Pending Claims Approval' — those are
        approved by claims seniors, not Finance — instead of the generic
        'Pending FM Approval'. All other cases use the enum label.
        CFO directive 2026-07-09."""
        if (self.status == self.Status.PENDING_FM_APPROVAL
                and self.department == self.Department.CLAIMS):
            return 'Pending Claims Approval'
        return self.get_status_display()


# ---------------------------------------------------------------------------
# Purchase Order Line
# ---------------------------------------------------------------------------

class PurchaseOrderLine(BaseModel):
    """One line on a PO — typically one product or service."""

    purchase_order  = models.ForeignKey(
                          PurchaseOrder, on_delete=models.CASCADE, related_name='lines',
                      )
    # Display order (Kao 2026-07-08: reorder lines, e.g. place a new line
    # ABOVE the Excess line). Set from the editor's row order on save.
    sequence        = models.PositiveIntegerField(default=0, db_index=True)
    description     = models.CharField(max_length=500)
    # CFO directive 2026-05-21 — purchase orders MUST NOT be linked to a
    # GL account. The GL leg is recognised when the BILL is approved, not
    # when the PO is raised. Field kept on the model for legacy POs that
    # were created under the prior workflow; new POs always leave it NULL.
    account         = models.ForeignKey(
                          'ledger.Account', on_delete=models.PROTECT,
                          related_name='po_lines',
                          null=True, blank=True,
                          help_text='Legacy field. New POs are no longer tied to a GL '
                                    'account — the bill carries the GL line.',
                      )
    quantity        = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal('1.0000'))
    unit_price      = models.DecimalField(max_digits=18, decimal_places=2)
    tax_code        = models.ForeignKey(
                          'core.TaxRate', null=True, blank=True,
                          on_delete=models.PROTECT, related_name='po_lines',
                      )
    tax_rate        = models.DecimalField(max_digits=5, decimal_places=2, default=ZERO)
    tax_amount      = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    # Discount taken off this line = gross (qty x unit_price) x PO discount_percent.
    # Netted into line_total below so VAT is charged on the discounted amount.
    discount_amount = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    line_total      = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)

    # Running counters — updated when GRNs are posted and bills are matched.
    quantity_received = models.DecimalField(
                          max_digits=12, decimal_places=4, default=ZERO,
                          help_text='Sum of GRN line quantities to date.',
                      )
    quantity_billed   = models.DecimalField(
                          max_digits=12, decimal_places=4, default=ZERO,
                          help_text='Sum of matched bill line quantities to date.',
                      )
    # Running per-line counter incremented inside the LINE-level match.
    # Distinct from quantity_billed (which the header-level match maintains
    # in lockstep with quantity_received) — qty_billed_to_date records the
    # exact qty drawn down by all POBillMatchLine rows. Over-bill guard
    # uses this counter, not quantity_billed.
    qty_billed_to_date = models.DecimalField(
                          max_digits=12, decimal_places=4, default=ZERO,
                          help_text='Sum of qty across every POBillMatchLine '
                                    'against this PO line. Used by the '
                                    'over-bill guard in match_bill_lines.',
                      )

    class Meta(BaseModel.Meta):
        ordering            = ['sequence', 'created_at']
        verbose_name        = 'Purchase Order Line'
        verbose_name_plural = 'Purchase Order Lines'

    def __str__(self):
        return f"{self.description} — {self.line_total}"

    # ---- GL account types permitted as the receipt target on a PO line ----
    # POs commit spend, so the GL leg posted at receipt must be either an
    # expense (operating cost) or an asset (capex / inventory / prepayment).
    # Bank, liability, equity, and revenue accounts must never appear here.
    #
    # Additional CFO directive (2026-05-13): POs are for CLAIMS + OPERATIONS
    # only. Reinsurance, commission, payroll, depreciation and provisions
    # belong to other workflows and are explicitly blocked. See
    # procurement.po_classification for the blocklist and rationale.
    _ALLOWED_PO_ACCOUNT_TYPES = frozenset({'expense', 'asset'})

    def clean(self):
        super().clean()
        if self.account_id:
            if self.account.is_bank_account:
                raise ValidationError({
                    'account': (
                        f"Account {self.account.code} ({self.account.name}) is a bank "
                        f"account and cannot be used on a PO line. Choose an expense or "
                        f"asset account."
                    )
                })
            if self.account.account_type not in self._ALLOWED_PO_ACCOUNT_TYPES:
                raise ValidationError({
                    'account': (
                        f"Account {self.account.code} ({self.account.name}) is type "
                        f"'{self.account.account_type}' and cannot be used on a PO line. "
                        f"Choose an expense or asset account — bank, liability, equity, "
                        f"and revenue accounts are blocked."
                    )
                })
            # CFO blocklist: reinsurance, commission, payroll, etc.
            from procurement.po_classification import is_po_eligible_code
            if not is_po_eligible_code(self.account.code):
                raise ValidationError({
                    'account': (
                        f"Account {self.account.code} ({self.account.name}) is not "
                        f"PO-eligible. POs are for CLAIMS suppliers and OPERATIONAL "
                        f"expenses only. Reinsurance (101xxx, 104xxx, 106xxx), "
                        f"commission (107xxx), payroll (110xxx), depreciation, "
                        f"and provisions flow through their own workflows."
                    )
                })

    def save(self, *args, **kwargs):
        # Validate GL account choice before computing totals.
        self.clean()

        # Copy tax rate from TaxRate (preserves historical rate)
        if self._state.adding and self.tax_code_id:
            self.tax_rate = self.tax_code.rate or ZERO

        # Auto-calculate line totals. The PO carries one uniform discount_percent;
        # net it off the gross line BEFORE VAT so VAT and the total are struck on
        # the discounted amount. self.purchase_order is the in-memory PO the
        # serializer just set (with the current discount_percent), so this reads
        # the right figure on both create and amend.
        gross = (self.quantity * self.unit_price).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        disc_pct = ZERO
        if self.purchase_order_id:
            disc_pct = self.purchase_order.discount_percent or ZERO
        self.discount_amount = (gross * disc_pct / 100).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        self.line_total = gross - self.discount_amount
        if self.tax_rate:
            self.tax_amount = (self.line_total * self.tax_rate / 100).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        else:
            self.tax_amount = ZERO

        super().save(*args, **kwargs)

    @property
    def is_fully_received(self):
        return self.quantity_received >= self.quantity

    @property
    def is_fully_billed(self):
        return self.quantity_billed >= self.quantity

    @property
    def quantity_outstanding(self):
        return max(self.quantity - self.quantity_received, ZERO)


# ---------------------------------------------------------------------------
# Goods Receipt Note
# ---------------------------------------------------------------------------

class GoodsReceiptNote(AuditableMixin, BaseModel):
    """
    Records receipt of goods or services against a PO. Posting a GRN posts
    a JE: DR expense/asset (per line), CR Goods Received Not Invoiced (2145).
    The clearing account is settled when the supplier's bill is later matched
    to the PO.
    """

    class Status(models.TextChoices):
        DRAFT     = 'draft',     'Draft'
        POSTED    = 'posted',    'Posted'
        CANCELLED = 'cancelled', 'Cancelled'

    grn_number      = models.CharField(max_length=30, unique=True, blank=True)
    purchase_order  = models.ForeignKey(
                          PurchaseOrder, on_delete=models.PROTECT, related_name='grns',
                      )
    receipt_date    = models.DateField()
    delivery_note_reference = models.CharField(max_length=100, blank=True, default='')
    received_by     = models.ForeignKey(
                          User, on_delete=models.PROTECT, related_name='grns_received',
                          help_text='Person who physically received the goods.',
                      )
    notes           = models.TextField(blank=True, default='')
    status          = models.CharField(
                          max_length=10, choices=Status.choices, default=Status.DRAFT,
                      )
    journal_entry   = models.ForeignKey(
                          'ledger.JournalEntry', null=True, blank=True,
                          on_delete=models.SET_NULL, related_name='grns',
                      )
    created_by      = models.ForeignKey(
                          User, on_delete=models.PROTECT,
                          related_name='grns_created',
                      )

    class Meta(BaseModel.Meta):
        verbose_name        = 'Goods Receipt Note'
        verbose_name_plural = 'Goods Receipt Notes'
        ordering            = ['-receipt_date', '-grn_number']

    def __str__(self):
        return f"{self.grn_number} — {self.purchase_order.po_number}"

    def save(self, *args, audit_user=None, audit_ip=None, audit_description=None, **kwargs):
        if not self.grn_number:
            self.grn_number = _generate_grn_number()

        if self.pk:
            try:
                db_status = GoodsReceiptNote.objects.values_list('status', flat=True).get(pk=self.pk)
                if db_status in (self.Status.POSTED, self.Status.CANCELLED) \
                        and not getattr(self, '_allow_status_transition', False):
                    raise ValidationError(
                        f"{self.grn_number} is {db_status} and cannot be edited."
                    )
            except GoodsReceiptNote.DoesNotExist:
                pass

        super().save(
            *args,
            audit_user=audit_user,
            audit_ip=audit_ip,
            audit_description=audit_description,
            **kwargs,
        )


# ---------------------------------------------------------------------------
# GRN Line
# ---------------------------------------------------------------------------

class GoodsReceiptNoteLine(BaseModel):
    """One line on a GRN — receipt against a specific PO line."""

    class Condition(models.TextChoices):
        GOOD     = 'good',     'Good'
        DAMAGED  = 'damaged',  'Damaged'
        REJECTED = 'rejected', 'Rejected'

    grn               = models.ForeignKey(
                            GoodsReceiptNote, on_delete=models.CASCADE,
                            related_name='lines',
                        )
    po_line           = models.ForeignKey(
                            PurchaseOrderLine, on_delete=models.PROTECT,
                            related_name='grn_lines',
                        )
    quantity_received = models.DecimalField(max_digits=12, decimal_places=4)
    condition         = models.CharField(
                            max_length=10, choices=Condition.choices,
                            default=Condition.GOOD,
                        )
    notes             = models.CharField(max_length=500, blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering            = ['created_at']
        verbose_name        = 'Goods Receipt Note Line'
        verbose_name_plural = 'Goods Receipt Note Lines'

    def __str__(self):
        return f"{self.po_line.description} — {self.quantity_received}"


# ---------------------------------------------------------------------------
# 3-way match record (PO ↔ GRN ↔ Bill)
# ---------------------------------------------------------------------------

class POBillMatch(AuditableMixin, BaseModel):
    """
    Record of a vendor bill matched against a PO.

    Match status:
      - MATCHED            quantities and prices agree within tolerance
      - VARIANCE_QUANTITY  bill quantity ≠ received quantity
      - VARIANCE_PRICE     bill unit price ≠ PO unit price
      - VARIANCE_BOTH      both variances present
      - OVERRIDE           variance was approved with explicit reason
    """

    class MatchStatus(models.TextChoices):
        MATCHED              = 'matched',              'Matched'
        NEEDS_TIER1_APPROVAL = 'needs_tier1_approval', 'Needs Tier-1 Approval'
        NEEDS_TIER2_APPROVAL = 'needs_tier2_approval', 'Needs Tier-2 Approval'
        TIER1_APPROVED       = 'tier1_approved',       'Tier-1 Approved (awaiting Tier-2)'
        REJECTED             = 'rejected',             'Rejected'
        VARIANCE_QUANTITY    = 'variance_quantity',    'Variance — Quantity'
        VARIANCE_PRICE       = 'variance_price',       'Variance — Price'
        VARIANCE_BOTH        = 'variance_both',        'Variance — Both'
        OVERRIDE             = 'override',             'Variance Overridden'

    purchase_order = models.ForeignKey(
                         PurchaseOrder, on_delete=models.PROTECT,
                         related_name='bill_matches',
                     )
    bill           = models.ForeignKey(
                         'billing.Invoice', on_delete=models.PROTECT,
                         related_name='po_matches',
                     )
    match_status   = models.CharField(max_length=24, choices=MatchStatus.choices)
    quantity_variance = models.DecimalField(
                         max_digits=12, decimal_places=4, default=ZERO,
                     )
    price_variance    = models.DecimalField(
                         max_digits=18, decimal_places=2, default=ZERO,
                     )
    # Variance as a percentage of the PO total — drives tier routing.
    # Positive = bill > PO (you'd pay more than ordered). Negative = bill <
    # PO (you'd pay less; auto-matches without escalation).
    variance_pct      = models.DecimalField(
                            max_digits=8, decimal_places=4, default=ZERO,
                            help_text='(bill_total − po_total) / po_total * 100. '
                                      'Negative = bill is less than PO.',
                        )

    # Two-tier approval workflow for bills ABOVE the PO total
    #   Tier 1 — single approval by the department manager (≤ tier1 ceiling)
    #   Tier 2 — Tier 1 + a co-approver (Ops Manager / Finance Manager / CFO)
    tier1_approved_by = models.ForeignKey(
                            User, null=True, blank=True,
                            on_delete=models.SET_NULL,
                            related_name='po_match_tier1_approvals',
                        )
    tier1_approved_at = models.DateTimeField(null=True, blank=True)
    tier2_approved_by = models.ForeignKey(
                            User, null=True, blank=True,
                            on_delete=models.SET_NULL,
                            related_name='po_match_tier2_approvals',
                        )
    tier2_approved_at = models.DateTimeField(null=True, blank=True)
    rejection_reason  = models.TextField(blank=True, default='')

    override_reason   = models.TextField(blank=True, default='')
    matched_by        = models.ForeignKey(
                            User, on_delete=models.PROTECT,
                            related_name='po_matches_made',
                            help_text='User (or system) that ran the auto-match.',
                        )
    matched_at        = models.DateTimeField(default=timezone.now)

    class Meta(BaseModel.Meta):
        verbose_name        = 'PO ↔ Bill Match'
        verbose_name_plural = 'PO ↔ Bill Matches'
        ordering            = ['-matched_at']
        constraints = [
            models.UniqueConstraint(
                fields=['purchase_order', 'bill'],
                name='uq_po_bill_match',
            ),
        ]

    def __str__(self):
        return f"{self.purchase_order.po_number} ↔ {self.bill.invoice_number}"

    @property
    def is_finalised(self) -> bool:
        return self.match_status in (
            self.MatchStatus.MATCHED,
            self.MatchStatus.OVERRIDE,
            self.MatchStatus.REJECTED,
        )

    @property
    def is_payable(self) -> bool:
        """True if the matched bill is cleared to pay — used by Payment.confirm."""
        return self.match_status in (
            self.MatchStatus.MATCHED,
            self.MatchStatus.OVERRIDE,
        )


# ---------------------------------------------------------------------------
# Variance Policy — single-row settings the CFO can edit via admin
# ---------------------------------------------------------------------------

class VariancePolicy(BaseModel):
    """
    Tunable thresholds for the auto-match workflow. Singleton — there is
    one active row at a time.

    Defaults (CFO-mandated):
      tier1_ceiling_pct = 5.00  → ≤ 5% over PO can be approved by the
                                  department manager alone (Claims Mgr,
                                  Operations Mgr, or HR Mgr).
      Above 5% → Tier 2: department manager + (Operations Mgr OR Finance Mgr).
      bill ≤ po → auto-match silently (paying less than ordered).
    """

    tier1_ceiling_pct = models.DecimalField(
                            max_digits=8, decimal_places=4,
                            default=Decimal('5.0000'),
                            help_text='Percent variance up to which the '
                                      'department manager alone can approve.',
                        )
    notes             = models.TextField(blank=True, default='')
    updated_by        = models.ForeignKey(
                            User, null=True, blank=True,
                            on_delete=models.SET_NULL,
                            related_name='variance_policy_updates',
                        )

    class Meta(BaseModel.Meta):
        verbose_name        = 'Variance Policy'
        verbose_name_plural = 'Variance Policy'

    def __str__(self):
        return f"Variance policy — Tier-1 ceiling {self.tier1_ceiling_pct}%"

    @classmethod
    def current(cls) -> 'VariancePolicy':
        """Return the active policy, creating it on first access."""
        policy = cls.objects.order_by('created_at').first()
        if policy is None:
            policy = cls.objects.create()
        return policy


# ---------------------------------------------------------------------------
# Bill ↔ PO AI verification (DeepSeek second-pass)
# ---------------------------------------------------------------------------

class BillAIVerification(BaseModel):
    """
    Stores DeepSeek's verdict on a bill ↔ PO comparison. Written every time
    a vendor bill is auto-matched to its PO. Advisory — does NOT block the
    deterministic 3-way match — but flags fraud cues that the math alone
    cannot see (vendor name drift, suspicious dates, banking detail drift).
    """

    class Verdict(models.TextChoices):
        CLEAN     = 'clean',     'Clean — no concerns'
        VARIANCE  = 'variance',  'Variance — within math tolerance'
        ANOMALY   = 'anomaly',   'Anomaly — review recommended'
        FRAUD_CUE = 'fraud_cue', 'Fraud cue — escalate'
        UNAVAILABLE = 'unavailable', 'AI unavailable'

    bill        = models.ForeignKey(
                      'billing.Invoice', on_delete=models.CASCADE,
                      related_name='ai_verifications',
                  )
    po          = models.ForeignKey(
                      PurchaseOrder, on_delete=models.PROTECT,
                      related_name='ai_verifications',
                  )
    match       = models.ForeignKey(
                      POBillMatch, null=True, blank=True,
                      on_delete=models.SET_NULL,
                      related_name='ai_verifications',
                  )
    verdict     = models.CharField(max_length=16, choices=Verdict.choices)
    vendor_match     = models.BooleanField(default=True)
    currency_match   = models.BooleanField(default=True)
    total_match      = models.BooleanField(default=True)
    flags            = models.JSONField(default=list, blank=True,
                       help_text='List of structured flag strings: '
                                 'vendor_mismatch / currency_mismatch / '
                                 'total_variance / line_drift / back_dated_bill / '
                                 'tax_anomaly / bank_detail_drift / suspicious')
    notes            = models.TextField(blank=True, default='')
    confidence       = models.PositiveSmallIntegerField(default=0,
                       help_text='AI self-reported confidence 0–100.')
    raw_prompt       = models.TextField(blank=True, default='')
    raw_response     = models.TextField(blank=True, default='')
    model_used       = models.CharField(max_length=50, blank=True, default='')
    elapsed_seconds  = models.DecimalField(
                           max_digits=6, decimal_places=2, default=ZERO,
                       )
    error_message    = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        verbose_name        = 'Bill AI Verification'
        verbose_name_plural = 'Bill AI Verifications'
        ordering            = ['-created_at']

    def __str__(self):
        return f"AI verify {self.bill_id} vs {self.po_id} — {self.verdict}"


# ---------------------------------------------------------------------------
# Vendor Bank Account — maker-checker fraud control
# ---------------------------------------------------------------------------

class VendorBankAccount(AuditableMixin, BaseModel):
    """
    Vendor (or broker / reinsurer) bank account where Alpha Direct sends
    outbound payments.

    INTERNAL CONTROL — maker-checker:
      DRAFT → submit_for_approval → PENDING_APPROVAL
      PENDING_APPROVAL → approve   → ACTIVE         (Finance Manager / CFO)
      PENDING_APPROVAL → reject    → REJECTED       (with reason)
      ACTIVE           → retire    → RETIRED        (cannot be reused)

    Once ACTIVE, the record is IMMUTABLE — to change any banking detail
    (the classic fraud vector — "change a real vendor's bank to attacker's
    account"), the existing record must be RETIRED and a new DRAFT created,
    which then goes through approval again.

    Segregation of duties: the approver must be a different user from the
    creator AND the submitter.

    Only ACTIVE accounts may be selected as the destination on an outbound
    Payment. The Payment service layer enforces this.
    """

    class Status(models.TextChoices):
        DRAFT             = 'draft',             'Draft'
        PENDING_APPROVAL  = 'pending_approval',  'Pending Approval'
        ACTIVE            = 'active',            'Active'
        REJECTED          = 'rejected',          'Rejected'
        RETIRED           = 'retired',           'Retired'

    contact         = models.ForeignKey(
                          'billing.Contact', on_delete=models.PROTECT,
                          related_name='bank_accounts',
                          help_text='The vendor / broker / reinsurer this bank '
                                    'account belongs to.',
                      )
    bank_name       = models.CharField(
                          max_length=200,
                          help_text='e.g. First National Bank, Stanbic Bank Botswana.',
                      )
    account_holder_name = models.CharField(
                          max_length=300,
                          help_text='Name printed on the bank account. Should '
                                    'match the vendor name; FM should question '
                                    'any mismatch.',
                      )
    account_number  = models.CharField(max_length=40)
    branch_code     = models.CharField(
                          max_length=20, blank=True, default='',
                          help_text='Botswana sort code or branch number.',
                      )
    branch_name     = models.CharField(max_length=200, blank=True, default='')
    swift_bic       = models.CharField(
                          max_length=12, blank=True, default='',
                          help_text='SWIFT/BIC code — required for international '
                                    'payments.',
                      )
    iban            = models.CharField(max_length=40, blank=True, default='')
    email           = models.EmailField(
                          max_length=254, blank=True, default='',
                          help_text='Where FNB emails this vendor their proof of '
                                    'payment (POP). Remembered here so it does not '
                                    'have to be re-typed each payment (CFO 2026-08-22).')
    currency_code   = models.ForeignKey(
                          Currency, on_delete=models.PROTECT,
                          related_name='vendor_bank_accounts', default='BWP',
                      )
    is_default      = models.BooleanField(
                          default=False,
                          help_text='If True, this is the default bank account '
                                    'for this vendor in this currency.',
                      )

    proof_document  = models.FileField(
                          upload_to='vendor-bank-proofs/%Y/%m/',
                          null=True, blank=True,
                          help_text='Bank confirmation letter or stamped void '
                                    'cheque. Strongly recommended.',
                      )

    status          = models.CharField(
                          max_length=18, choices=Status.choices,
                          default=Status.DRAFT,
                      )
    notes           = models.TextField(blank=True, default='')

    submitted_by    = models.ForeignKey(
                          User, null=True, blank=True,
                          on_delete=models.SET_NULL,
                          related_name='vendor_banks_submitted',
                      )
    submitted_at    = models.DateTimeField(null=True, blank=True)
    approved_by     = models.ForeignKey(
                          User, null=True, blank=True,
                          on_delete=models.SET_NULL,
                          related_name='vendor_banks_approved',
                      )
    approved_at     = models.DateTimeField(null=True, blank=True)
    rejected_by     = models.ForeignKey(
                          User, null=True, blank=True,
                          on_delete=models.SET_NULL,
                          related_name='vendor_banks_rejected',
                      )
    rejected_at     = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True, default='')
    retired_by      = models.ForeignKey(
                          User, null=True, blank=True,
                          on_delete=models.SET_NULL,
                          related_name='vendor_banks_retired',
                      )
    retired_at      = models.DateTimeField(null=True, blank=True)
    retirement_reason = models.TextField(blank=True, default='')

    replaces        = models.ForeignKey(
                          'self', null=True, blank=True,
                          on_delete=models.SET_NULL,
                          related_name='replaced_by',
                      )

    created_by      = models.ForeignKey(
                          User, on_delete=models.PROTECT,
                          related_name='vendor_banks_created',
                      )

    class Meta(BaseModel.Meta):
        verbose_name        = 'Vendor Bank Account'
        verbose_name_plural = 'Vendor Bank Accounts'
        ordering            = ['contact__name', '-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['contact', 'account_number'],
                condition=models.Q(status='active'),
                name='uq_active_vendor_bank_account_no',
            ),
            models.UniqueConstraint(
                fields=['contact', 'currency_code'],
                condition=models.Q(status='active', is_default=True),
                name='uq_active_default_vendor_bank_per_ccy',
            ),
        ]

    def __str__(self):
        return f"{self.contact.name} — {self.bank_name} {self.account_number} ({self.get_status_display()})"

    def save(self, *args, audit_user=None, audit_ip=None, audit_description=None, **kwargs):
        if self.pk:
            try:
                db_status = VendorBankAccount.objects.values_list('status', flat=True).get(pk=self.pk)
                terminal = db_status in (
                    self.Status.ACTIVE,
                    self.Status.REJECTED,
                    self.Status.RETIRED,
                )
                if terminal and not getattr(self, '_allow_status_transition', False):
                    raise ValidationError(
                        f"Bank account for {self.contact.name} is {db_status} "
                        "and cannot be edited. Retire it and create a new "
                        "draft if banking details have changed."
                    )
            except VendorBankAccount.DoesNotExist:
                pass

        super().save(
            *args,
            audit_user=audit_user,
            audit_ip=audit_ip,
            audit_description=audit_description,
            **kwargs,
        )

    @property
    def is_usable_for_payment(self) -> bool:
        return self.status == self.Status.ACTIVE

    @property
    def name_mismatch(self) -> bool:
        """True if the bank-side holder name differs from the vendor name —
        a flag the Finance Manager should question before approving."""
        if not self.contact_id:
            return False
        a = (self.account_holder_name or '').strip().lower()
        b = (self.contact.name or '').strip().lower()
        return bool(a and b and a != b and a not in b and b not in a)


# ---------------------------------------------------------------------------
# Manus PO Audit Phase-B models — CFO directive 2026-05-20
# (Approval delegation, PO amendments, PO attachments)
# ---------------------------------------------------------------------------

class ApprovalDelegate(AuditableMixin, BaseModel):
    """Route an approver's authority to a delegate for a date window.

    Used by fm_approve / cfo_approve to honour out-of-office routing.
    """
    class Role(models.TextChoices):
        FM            = 'fm',            'Finance Manager'
        CFO           = 'cfo',           'CFO'
        TIER1_MANAGER = 'tier1_manager', 'Tier-1 dept manager'

    delegator  = models.ForeignKey(
                     User, on_delete=models.CASCADE,
                     related_name='approval_delegations_granted',
                 )
    delegate   = models.ForeignKey(
                     User, on_delete=models.PROTECT,
                     related_name='approval_delegations_received',
                 )
    role       = models.CharField(max_length=20, choices=Role.choices)
    starts_at  = models.DateField()
    ends_at    = models.DateField()
    reason     = models.CharField(max_length=200, blank=True, default='')
    is_active  = models.BooleanField(default=True)

    class Meta(BaseModel.Meta):
        ordering = ['-starts_at']
        verbose_name = 'Approval Delegate'
        verbose_name_plural = 'Approval Delegates'

    def __str__(self):
        return f'{self.delegator.username} → {self.delegate.username} [{self.role}]'

    @property
    def is_currently_active(self) -> bool:
        from django.utils import timezone
        today = timezone.localdate()
        return bool(self.is_active and self.starts_at <= today <= self.ends_at)


class POAmendment(AuditableMixin, BaseModel):
    """Versioned change to an APPROVED PO."""
    class Status(models.TextChoices):
        DRAFT            = 'draft',            'Draft'
        PENDING_APPROVAL = 'pending_approval', 'Pending approval'
        APPROVED         = 'approved',         'Approved'
        REJECTED         = 'rejected',         'Rejected'

    purchase_order = models.ForeignKey(
                         PurchaseOrder, on_delete=models.PROTECT,
                         related_name='amendments',
                     )
    version        = models.PositiveSmallIntegerField()
    requested_by   = models.ForeignKey(
                         User, on_delete=models.PROTECT,
                         related_name='po_amendments_requested',
                     )
    approved_by    = models.ForeignKey(
                         User, null=True, blank=True,
                         on_delete=models.SET_NULL,
                         related_name='po_amendments_approved',
                     )
    status         = models.CharField(
                         max_length=20, choices=Status.choices,
                         default=Status.DRAFT,
                     )
    reason         = models.CharField(max_length=500, blank=True, default='')
    diff           = models.JSONField(default=dict)
    applied_at     = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']
        unique_together = [('purchase_order', 'version')]
        verbose_name = 'PO Amendment'

    def __str__(self):
        return f'{self.purchase_order.po_number}@v{self.version} ({self.status})'


def _po_attachment_upload_to(instance, filename):
    return f'po_attachments/{instance.purchase_order_id}/{filename}'


class POAttachment(AuditableMixin, BaseModel):
    """Supplier quote, justification, contract — supporting docs on a PO."""

    purchase_order = models.ForeignKey(
                         PurchaseOrder, on_delete=models.CASCADE,
                         related_name='attachments',
                     )
    file           = models.FileField(upload_to=_po_attachment_upload_to)
    label          = models.CharField(max_length=120, blank=True, default='')
    content_type   = models.CharField(max_length=100, blank=True, default='')
    size_bytes     = models.PositiveIntegerField(default=0)
    uploaded_by    = models.ForeignKey(
                         User, null=True, blank=True,
                         on_delete=models.SET_NULL,
                         related_name='po_attachments_uploaded',
                     )

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']
        verbose_name = 'PO Attachment'

    def __str__(self):
        return f'{self.purchase_order.po_number} — {self.label or self.file.name}'


# ---------------------------------------------------------------------------
# Vendor KYC — model lives in procurement/kyc_models.py, exposed here so
# Django's app loader registers it on the `procurement` app label and so
# `from procurement.models import VendorKYC` works for downstream callers.
# ---------------------------------------------------------------------------

from procurement.kyc_models import VendorKYC  # noqa: E402,F401


# ---- Attach `kyc_status` to billing.Contact -------------------------------
#
# The Contact model lives in billing/ which is bible-protected — we cannot
# edit billing/models.py directly. Instead we monkey-attach a property here.
# This is loaded once at Django app-ready time (procurement is in
# INSTALLED_APPS and imports this module on startup).
#
# Returns one of:
#   'missing'  — no VendorKYC row attached
#   'expired'  — kyc_expires_on is set and in the past
#   'flagged'  — sanctions_status == 'flagged' OR pep_status in {pep,associate}
#   'ok'       — everything else
#
# Order of precedence: missing > expired > flagged > ok. (Expired beats
# flagged because an expired record means the screening data itself is
# stale and can't be relied on for the flagged signal.)

def _contact_kyc_status(self):

    kyc = getattr(self, 'kyc', None)
    if kyc is None:
        return 'missing'

    if kyc.kyc_expires_on is not None and kyc.kyc_expires_on < timezone.localdate():
        return 'expired'

    if kyc.sanctions_status == VendorKYC.SanctionsStatus.FLAGGED:
        return 'flagged'
    if kyc.pep_status in (VendorKYC.PEPStatus.PEP, VendorKYC.PEPStatus.ASSOCIATE):
        return 'flagged'

    return 'ok'


def _install_contact_kyc_status():
    """Attach the kyc_status property to billing.Contact exactly once."""
    try:
        from billing.models import Contact as _Contact
    except Exception:  # pragma: no cover — import-order safety net
        return
    if not hasattr(_Contact, 'kyc_status'):
        _Contact.kyc_status = property(_contact_kyc_status)


_install_contact_kyc_status()


# ---------------------------------------------------------------------------
# POBillMatchLine (line-level 3-way match)
# ---------------------------------------------------------------------------
# Imported at the bottom so the model registers on the procurement
# app_label and migrations land in procurement/migrations/.
from procurement.match_models import (  # noqa: E402,F401
    POBillMatchLine,
)


# ---------------------------------------------------------------------------
# ClaimsAssessment (Claims PO — assessment -> 2 draft POs)
# ---------------------------------------------------------------------------
# CFO 2026-07-06 claims-PO port. Model lives in procurement/claims_models.py;
# imported here so it registers on the procurement app_label and migrations
# land in procurement/migrations/.
from procurement.claims_models import ClaimsAssessment  # noqa: E402,F401
