"""Supplier Payables Reconciliation — data models.

Monthly, per-supplier reconciliation of vendor bills (mostly panel beaters and
parts suppliers) against payments, purchase orders / goods receipts, and the
claim authorisation the spend was raised under — the "3-way match".

Every unpaid or held bill must carry a reason code and a written justification
before the month can be finalised. Escalation-worthy items must be escalated.

Design notes
------------
* Inherits ``BaseModel`` (UUID pk, created_at/updated_at) and, on the rows that
  matter for audit, ``AuditableMixin`` — so core.AuditLog is written by the
  framework rather than by hand-rolled signals.
* Company-scoped at the run, and enforced again in the API queryset. Vendors do
  not cross legal entities (CFO directive 2026-05-18).
* This module does NOT move money and does NOT post to the GL. Payment
  execution stays in ``payments`` and the FNB flow; this observes and explains.
* Amounts are Decimal throughout. Never float — a payables board that rounds
  is a payables board nobody trusts.

Reuse rather than duplication
-----------------------------
* Bill amount / paid / balance come from ``billing.Invoice`` and
  ``payments.PaymentAllocation`` — recomputed as at the period end, never
  copied from Invoice.amount_paid (which is "as at now").
* Payment terms come from ``billing.Contact.payment_terms_days``. This app does
  not keep a second copy.
* The PO and goods-receipt legs come from ``procurement``; the claim leg from
  ``procurement.PurchaseOrder.related_claim_reference`` (the master claims
  register lives in Graphite, so a reference is all omni holds).
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from core.models import AuditableMixin, BaseModel

from .constants import (
    AGEING_BUCKETS,
    CLAIM_BACKED_CATEGORIES,
    EscalationStatus,
    LIVE_ESCALATION_STATUSES,
    LedgerStage,
    LineStatus,
    MIN_JUSTIFICATION_CHARS,
    MatchStatus,
    NOT_FULLY_PAID,
    OVERDUE_ESCALATION_DAYS,
    PaymentStatus,
    ReasonGroup,
    RunStatus,
    SupplierCategory,
    ZERO,
)


# ---------------------------------------------------------------------------
# Supplier scoping
# ---------------------------------------------------------------------------

class ReconSupplierProfile(AuditableMixin, BaseModel):
    """Classifies a vendor for reconciliation, without touching billing.Contact.

    Only the classification lives here. Payment terms, active flag and owning
    company stay on ``billing.Contact`` — this app reads them.
    """

    contact = models.OneToOneField(
                  'billing.Contact', on_delete=models.CASCADE,
                  related_name='recon_profile',
              )
    category = models.CharField(
                  max_length=20, choices=SupplierCategory.choices,
                  default=SupplierCategory.GENERAL,
               )
    in_scope = models.BooleanField(
                  default=True,
                  help_text='Include this supplier in monthly reconciliation runs.',
               )
    notes = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        verbose_name        = 'Recon Supplier Profile'
        verbose_name_plural = 'Recon Supplier Profiles'
        indexes = [
            models.Index(fields=['category', 'in_scope'],
                         name='reconprofile_cat_scope_idx'),
        ]

    def __str__(self):
        return f"{self.contact.name} [{self.get_category_display()}]"


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------

class ReconOwner(AuditableMixin, BaseModel):
    """Who owns the supplier reconciliation board for one legal entity.

    CFO directive 2026-07-25: the board is owned by Bharath Balasubramanian.
    "Owner" is the accountable business owner — escalations land with them by
    default and the board says whose it is. It grants no extra authority on its
    own: the owner still needs the ordinary view/prepare/review rights, and
    sign-off remains CFO / Financial Controller / Finance Manager.
    """

    company = models.OneToOneField(
                  'core.Company', on_delete=models.CASCADE,
                  related_name='supplier_recon_owner',
              )
    owner = models.ForeignKey(
                settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                related_name='owned_recon_boards',
                help_text='Accountable owner. Escalations default to this person.',
            )
    notes = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        verbose_name = 'Recon Board Owner'
        verbose_name_plural = 'Recon Board Owners'

    def __str__(self):
        who = (self.owner.get_full_name() or self.owner.username) if self.owner_id else '?'
        return f"{self.company.code}: {who}"

    @classmethod
    def for_company(cls, company):
        """The owner for this entity, or None. Never raises."""
        row = cls.objects.filter(company=company).select_related('owner').first()
        return row.owner if row else None


# ---------------------------------------------------------------------------
# Reason codes
# ---------------------------------------------------------------------------

class ReasonCode(BaseModel):
    """Standardised reason why a bill was not paid in the month."""

    code  = models.CharField(max_length=32, unique=True)
    label = models.CharField(max_length=120)
    group = models.CharField(max_length=32, choices=ReasonGroup.choices)
    requires_escalation = models.BooleanField(
                              default=False,
                              help_text='Using this reason forces an escalation '
                                        'to be raised before the run can close.',
                          )
    active = models.BooleanField(default=True)

    class Meta(BaseModel.Meta):
        ordering = ['group', 'code']

    def __str__(self):
        return f"{self.code} — {self.label}"


# ---------------------------------------------------------------------------
# Run (monthly, per company)
# ---------------------------------------------------------------------------

class SupplierReconRun(AuditableMixin, BaseModel):
    """A monthly supplier reconciliation for one legal entity."""

    company = models.ForeignKey(
                  'core.Company', on_delete=models.PROTECT,
                  related_name='supplier_recon_runs',
              )
    # Traceability only. A run can be built before the accounting period is
    # opened, so this is optional and never gates the build.
    fiscal_period = models.ForeignKey(
                        'ledger.FiscalPeriod', on_delete=models.SET_NULL,
                        null=True, blank=True, related_name='supplier_recon_runs',
                    )
    period_label = models.CharField(max_length=7,
                                    help_text="Reconciliation month, e.g. '2026-07'.")
    period_start = models.DateField()
    period_end   = models.DateField()

    status = models.CharField(max_length=20, choices=RunStatus.choices,
                              default=RunStatus.OPEN)

    prepared_by = models.ForeignKey(
                      settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                      null=True, blank=True, related_name='prepared_recon_runs',
                  )
    reviewed_by = models.ForeignKey(
                      settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                      null=True, blank=True, related_name='reviewed_recon_runs',
                  )
    finalised_at = models.DateTimeField(null=True, blank=True)
    last_built_at = models.DateTimeField(null=True, blank=True)
    # Stamped at build from ReconOwner, so a historic month still shows who
    # owned it even after ownership moves on.
    owner = models.ForeignKey(
                settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                null=True, blank=True, related_name='owned_recon_runs',
            )

    # Cached roll-ups (BWP), refreshed by services.recompute_run_totals().
    total_invoiced  = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    total_paid      = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    total_unpaid    = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    total_held      = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    total_escalated = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    # Subset of total_invoiced sitting in draft — raised but never posted, so
    # absent from the GL, the balance sheet and the AP Aging report.
    total_not_posted = models.DecimalField(max_digits=18, decimal_places=2,
                                           default=ZERO)

    class Meta(BaseModel.Meta):
        verbose_name        = 'Supplier Recon Run'
        verbose_name_plural = 'Supplier Recon Runs'
        constraints = [
            models.UniqueConstraint(fields=['company', 'period_label'],
                                    name='uniq_recon_run_company_period'),
        ]
        ordering = ['-period_start', 'company_id']
        indexes = [
            models.Index(fields=['company', 'status'], name='reconrun_co_status_idx'),
        ]

    def __str__(self):
        return (f"Supplier recon {self.company.code if self.company_id else '?'} "
                f"{self.period_label} ({self.get_status_display()})")

    @property
    def is_locked(self) -> bool:
        return self.status == RunStatus.FINALISED

    @property
    def pct_paid(self) -> float:
        """Share of the month's billed value actually settled. Display only."""
        if not self.total_invoiced:
            return 0.0
        return round(float(self.total_paid) / float(self.total_invoiced) * 100, 1)


class SupplierReconLine(BaseModel):
    """One supplier's position within a run."""

    run = models.ForeignKey(SupplierReconRun, on_delete=models.CASCADE,
                            related_name='lines')
    supplier = models.ForeignKey('billing.Contact', on_delete=models.PROTECT,
                                 related_name='recon_lines')
    category = models.CharField(max_length=20, choices=SupplierCategory.choices,
                                default=SupplierCategory.GENERAL)
    assigned_to = models.ForeignKey(
                      settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                      null=True, blank=True, related_name='assigned_recon_lines',
                      help_text='Payables team member accountable for this '
                                'supplier this month.',
                  )
    status = models.CharField(max_length=12, choices=LineStatus.choices,
                              default=LineStatus.PENDING)

    invoiced = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    paid     = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    unpaid   = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    held     = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    # Subset of ``unpaid`` that is already past its due date. Lets the board
    # colour a supplier's "Still owed" red only when something is genuinely
    # overdue, and green when it is owed but not yet due (Bharath, 2026-07-27).
    overdue  = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    # Days past due of the oldest overdue bill on this line — the "overdue by"
    # figure. 0 when nothing is overdue.
    max_days_past_due = models.PositiveIntegerField(default=0)

    invoice_count     = models.PositiveIntegerField(default=0)
    unactioned_count  = models.PositiveIntegerField(default=0)

    class Meta(BaseModel.Meta):
        constraints = [
            models.UniqueConstraint(fields=['run', 'supplier'],
                                    name='uniq_recon_line_run_supplier'),
        ]
        ordering = ['supplier__name']

    def __str__(self):
        return f"{self.supplier.name} @ {self.run.period_label}"


class InvoiceReconItem(AuditableMixin, BaseModel):
    """One vendor bill inside a run — the unit the payables team actions."""

    line = models.ForeignKey(SupplierReconLine, on_delete=models.CASCADE,
                             related_name='items')
    invoice = models.ForeignKey('billing.Invoice', on_delete=models.PROTECT,
                                related_name='recon_items')

    # --- 3-way match legs ------------------------------------------------ #
    purchase_order = models.ForeignKey(
                         'procurement.PurchaseOrder', on_delete=models.SET_NULL,
                         null=True, blank=True, related_name='recon_items',
                     )
    goods_receipt = models.ForeignKey(
                        'procurement.GoodsReceiptNote', on_delete=models.SET_NULL,
                        null=True, blank=True, related_name='recon_items',
                    )
    # The claims master register lives in Graphite; omni holds the reference the
    # PO was raised against. Copied here so the board stays readable after a PO
    # is amended.
    claim_reference = models.CharField(max_length=100, blank=True, default='')

    match_status = models.CharField(max_length=12, choices=MatchStatus.choices,
                                    default=MatchStatus.UNMATCHED)
    payment_status = models.CharField(max_length=16, choices=PaymentStatus.choices,
                                      default=PaymentStatus.UNPAID)
    # Has the bill actually reached the GL? A draft bill is a real obligation
    # but is absent from AP Aging and the balance sheet — see LedgerStage.
    ledger_stage = models.CharField(max_length=8, choices=LedgerStage.choices,
                                    default=LedgerStage.POSTED)

    amount      = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    amount_paid = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    due_date    = models.DateField(null=True, blank=True)

    # --- payables workflow ----------------------------------------------- #
    reason_code = models.ForeignKey(ReasonCode, on_delete=models.PROTECT,
                                    null=True, blank=True, related_name='items')
    justification = models.TextField(
                        blank=True, default='',
                        help_text='Mandatory for any bill not fully paid.',
                    )
    assigned_to = models.ForeignKey(
                      settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                      null=True, blank=True, related_name='assigned_recon_items',
                  )
    actioned    = models.BooleanField(default=False)
    actioned_by = models.ForeignKey(
                      settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                      null=True, blank=True, related_name='actioned_recon_items',
                  )
    actioned_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        constraints = [
            models.UniqueConstraint(fields=['line', 'invoice'],
                                    name='uniq_recon_item_line_invoice'),
        ]
        ordering = ['due_date', 'invoice__invoice_number']
        indexes = [
            models.Index(fields=['payment_status', 'actioned'],
                         name='reconitem_pstatus_act_idx'),
            models.Index(fields=['match_status'], name='reconitem_match_idx'),
            models.Index(fields=['ledger_stage'], name='reconitem_stage_idx'),
        ]

    def __str__(self):
        return f"Bill {self.invoice.invoice_number} ({self.get_payment_status_display()})"

    # --- derived --------------------------------------------------------- #
    @property
    def amount_outstanding(self):
        return (self.amount or ZERO) - (self.amount_paid or ZERO)

    @property
    def days_past_due(self) -> int:
        """Days past due as at today. 0 when not yet due or no due date."""
        if not self.due_date:
            return 0
        return max(0, (timezone.localdate() - self.due_date).days)

    @property
    def is_overdue(self) -> bool:
        if self.payment_status == PaymentStatus.PAID:
            return False
        return self.days_past_due > 0

    @property
    def due_state(self) -> str:
        """Traffic-light for the amount still owed — what colours the board.

        Bharath's feedback 2026-07-27: a payment that is not yet due (most of
        the board is due next month) should read green, not red; only an
        overdue payment should be red; and a payment more than
        ``OVERDUE_ESCALATION_DAYS`` past due is the serious tier that must be
        explained and escalated.

          paid       – settled, nothing owed
          not_due    – still owed but not yet due (green)
          overdue    – 1 to 29 days past due (red)
          overdue_30 – 30+ days past due (strong red, escalates)
        """
        if self.payment_status == PaymentStatus.PAID:
            return 'paid'
        days = self.days_past_due
        if days <= 0:
            return 'not_due'
        if days < OVERDUE_ESCALATION_DAYS:
            return 'overdue'
        return 'overdue_30'

    @property
    def ageing_bucket(self) -> str:
        """Bucket key matching reporting's AP-aging, so the two agree."""
        days = self.days_past_due
        for key, lower, upper in AGEING_BUCKETS:
            if lower is not None and days < lower:
                continue
            if upper is not None and days > upper:
                continue
            return key
        return 'over_120'

    @property
    def is_posted(self) -> bool:
        return self.ledger_stage == LedgerStage.POSTED

    @property
    def needs_justification(self) -> bool:
        """Any bill not fully paid must carry a reason + justification — UNTIL one
        is recorded.

        A bill still sitting in draft always needs one too: it is an obligation
        that has not reached the ledger, and 'nobody posted it' is exactly the
        kind of thing this board exists to force into the open.

        Once the bill has been actioned (a reason + justification recorded, or it
        was escalated / put on hold through the same flow) it is explained and no
        longer counts. Without this check the flag ignored `actioned`, so a
        recorded bill stayed 'Pending Action', the 'Needs a reason' counter stuck
        at N of N, and on reload the reason looked lost — the whole board could
        never be cleared (Oprah, 2026-07-27).
        """
        if self.actioned:
            return False
        if not self.is_posted:
            return True
        return self.payment_status in NOT_FULLY_PAID

    @property
    def requires_escalation(self) -> bool:
        """Seriously overdue (30+ days past due), or a reason code flagged
        escalation-worthy.

        Bharath's feedback 2026-07-27 (points 2/3): a bill only a few days past
        due should not force a formal escalation to close the month — a reason
        still must be recorded, but escalation is reserved for the bills that
        have gone unpaid and unexplained for more than a month, which is exactly
        the case ('finance not responding') he wants surfaced. Was previously
        any day past due.
        """
        if self.payment_status == PaymentStatus.PAID:
            return False
        if self.days_past_due >= OVERDUE_ESCALATION_DAYS:
            return True
        return bool(self.reason_code and self.reason_code.requires_escalation)

    @property
    def has_live_escalation(self) -> bool:
        return self.escalations.filter(status__in=LIVE_ESCALATION_STATUSES).exists()

    @property
    def claim_leg_required(self) -> bool:
        """Claim-supplier spend must tie back to a claim authorisation."""
        return self.line.category in CLAIM_BACKED_CATEGORIES

    def clean(self):
        super().clean()
        # Data-integrity gate: any bill not fully paid, or not yet posted, must
        # carry a reason + justification. Deliberately status-based, NOT keyed
        # off `needs_justification`: action_item() sets `actioned = True` before
        # calling full_clean(), and needs_justification returns False once a bill
        # is actioned (a board-display concept — an actioned bill is "explained").
        # Routing this check through needs_justification therefore let an unpaid
        # bill be actioned with NO reason at all — the reason/justification gate
        # silently did nothing (regression from the 2026-07-27 board-clears fix;
        # caught by CI, restored here). needs_justification stays as-is for the
        # board counter and finalise; this is the integrity rule.
        requires_reason = (not self.is_posted) or (self.payment_status in NOT_FULLY_PAID)
        if not requires_reason:
            return
        if self.reason_code_id is None:
            raise ValidationError({
                'reason_code': 'A reason code is required for any unpaid or held bill.',
            })
        text = (self.justification or '').strip()
        if not text:
            raise ValidationError({
                'justification': 'A written justification is required for any '
                                 'unpaid or held bill.',
            })
        if len(text) < MIN_JUSTIFICATION_CHARS:
            raise ValidationError({
                'justification': f'Justification must be at least '
                                 f'{MIN_JUSTIFICATION_CHARS} characters — '
                                 f'explain the position, do not just tick a box.',
            })


class Escalation(AuditableMixin, BaseModel):
    """A held / overdue bill escalated to a reviewer with a justification."""

    item = models.ForeignKey(InvoiceReconItem, on_delete=models.CASCADE,
                             related_name='escalations')
    raised_by = models.ForeignKey(
                    settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                    null=True, blank=True, related_name='raised_escalations',
                )
    raised_to = models.ForeignKey(
                    settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                    null=True, blank=True, related_name='received_escalations',
                )
    justification = models.TextField()
    amount = models.DecimalField(max_digits=18, decimal_places=2, default=ZERO)
    status = models.CharField(max_length=16, choices=EscalationStatus.choices,
                              default=EscalationStatus.OPEN)
    resolved_by = models.ForeignKey(
                      settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                      null=True, blank=True, related_name='resolved_escalations',
                  )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_note = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status'], name='reconesc_status_idx'),
        ]

    def __str__(self):
        return (f"Escalation on {self.item.invoice.invoice_number} "
                f"({self.get_status_display()})")


class ReconActionLog(BaseModel):
    """Domain trail of payables decisions — the 'who said what, and why' record.

    core.AuditLog captures field-level changes; this captures the business
    action in one readable row for the dashboard drawer.
    """

    item = models.ForeignKey(InvoiceReconItem, on_delete=models.CASCADE,
                             related_name='action_logs')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                              null=True, blank=True)
    action      = models.CharField(max_length=64)
    from_status = models.CharField(max_length=16, blank=True, default='')
    to_status   = models.CharField(max_length=16, blank=True, default='')
    note        = models.TextField(blank=True, default='')

    class Meta(BaseModel.Meta):
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.action} on {self.item.invoice.invoice_number}"


# Supplier-statement ingestion + statement-to-ledger matching (2026-08-24).
# Kept in their own module for readability; imported here so Django discovers
# them as supplier_recon models.
from .statement_models import (  # noqa: E402,F401
    SupplierStatement,
    SupplierStatementLine,
    StatementMatch,
)
