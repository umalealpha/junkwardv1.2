"""
procurement/services.py

Business logic for the Procurement module:

  - submit_for_approval(po, user)        DRAFT -> PENDING_FM_APPROVAL
  - fm_approve(po, user)                 PENDING_FM_APPROVAL -> PENDING_CFO_APPROVAL
                                         OR (po total < 100,000 BWP)
                                         PENDING_FM_APPROVAL -> APPROVED
                                         (single-approval path)
  - cfo_approve(po, user)                PENDING_CFO_APPROVAL -> APPROVED
  - reject(po, user, reason)             any pending -> REJECTED
  - cancel(po, user, reason)             approved/received -> CANCELLED (CFO only)
  - post_grn(grn, user)                  posts the JE for a goods receipt
  - match_bill_to_po(bill, po, user)     creates 3-way match record + clears GR-IR
  - check_open_pos_for_period(period)    returns POs blocking period close

Approval rules (joint CFO + Finance Manager):
  - Submitter ≠ FM approver ≠ CFO approver  (segregation of duties)
  - FM leg requires title in {FINANCE_MANAGER, FINANCIAL_CONTROLLER, CFO}
  - CFO leg requires title = CFO  (or Django superuser as a backup)

CFO directive 2026-05-21: POs whose value is strictly less than
SINGLE_APPROVAL_THRESHOLD_BWP (100,000 BWP) only need a single approval.
The FM step becomes the final approval, the CFO step is skipped, and the
segregation-of-duties self-approval block is relaxed so the submitter can
clear their own low-value PO. Material spend (>= 100K BWP) still needs two
distinct approvers.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone

from core.models import AuditLog, get_user_profile, UserProfile
from ledger.models import Account, FiscalPeriod, JournalEntry, JournalEntryLine

from .models import (
    BillAIVerification,
    GoodsReceiptNote,
    GoodsReceiptNoteLine,
    POBillMatch,
    PurchaseOrder,
    PurchaseOrderLine,
    VariancePolicy,
    VendorBankAccount,
)


TWO_PLACES = Decimal('0.01')
ZERO       = Decimal('0.00')

GR_IR_ACCOUNT_CODE = '2145'   # Goods Received Not Invoiced (clearing)

# Commitment accounting (steering rule 4, issue #58).
# Paired memorandum accounts — net to zero in aggregate.
COMMITMENT_ASSET_CODE     = '1990'   # Encumbered Purchase Commitments (DR)
COMMITMENT_LIABILITY_CODE = '2199'   # Reserve for Encumbered Commitments (CR)


def _bwp(amount, rate):
    return (amount * rate).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _can_approve_as_fm(user) -> bool:
    """FM leg can be approved by FM, Financial Controller, or CFO."""
    profile = get_user_profile(user)
    if user is not None and getattr(user, 'is_superuser', False):
        return True
    if profile is None or not profile.is_active:
        return False
    return profile.title in {
        UserProfile.Title.FINANCE_MANAGER,
        UserProfile.Title.FINANCIAL_CONTROLLER,
        UserProfile.Title.CFO,
    }


def _can_approve_as_cfo(user) -> bool:
    """CFO leg requires the CFO title (or Django superuser as backup)."""
    if user is not None and getattr(user, 'is_superuser', False):
        return True
    profile = get_user_profile(user)
    if profile is None or not profile.is_active:
        return False
    return profile.title == UserProfile.Title.CFO


def _can_approve_po(user, po) -> bool:
    """Who may approve a PO at the first (operational) approval leg.

    CFO directive 2026-07-08 (Kago's control): CLAIMS purchase orders are
    approved by the CLAIMS seniors — operational judgment of which claims need
    parts sits with claims, not finance. Finance's control is exercised later,
    at PAYMENT (three-way match invoice/PO/loaded amount). All other
    departments' POs keep the Finance Manager / Financial Controller leg.

    CFO directive 2026-07-19: the CFO is OUT of CLAIMS purchase orders
    ENTIRELY, any amount — not as an approver and NOT via the superuser
    backstop. He only authorises OPERATIONAL POs. So the superuser / CFO
    backstop below applies to operational POs only; claims POs are approvable
    strictly by the claims seniors.
    """
    if po is not None and po.department == PurchaseOrder.Department.CLAIMS:
        # Claims SENIORS only — Claims Manager, Claims Team Leader, Senior
        # Claims Associate. NOT the CFO, NOT a generic superuser (CFO
        # 2026-07-19). Junior associates and interns do not approve.
        profile = get_user_profile(user)
        return bool(profile and profile.is_active and profile.title in {
            UserProfile.Title.CLAIMS_MANAGER,
            UserProfile.Title.CLAIMS_TEAM_LEADER,
            UserProfile.Title.SENIOR_CLAIMS_ASSOCIATE,
        })
    # Operational POs: superuser / CFO backstop + FM / FC / CFO leg.
    if user is not None and getattr(user, 'is_superuser', False):
        return True
    return _can_approve_as_fm(user)


def _can_cancel_po(user, po) -> bool:
    """Who may cancel an APPROVED PO.

    CFO directive 2026-07-09: for CLAIMS POs the CFO stays out of the loop —
    the CLAIMS MANAGER cancels (claims owns its operational decisions, same
    principle as claims-PO approval). Cancel is a tighter control than approval,
    so it is the Claims Manager ONLY, not the wider claims-approver set.
    All other departments keep the CFO cancel control. CFO / superuser is the
    backstop everywhere.
    """
    if _can_approve_as_cfo(user):
        return True
    if po is not None and po.department == PurchaseOrder.Department.CLAIMS:
        profile = get_user_profile(user)
        return bool(profile and profile.is_active
                    and profile.title == UserProfile.Title.CLAIMS_MANAGER)
    return False


# ===========================================================================
#  Approval workflow
# ===========================================================================

@transaction.atomic
def submit_for_approval(po: PurchaseOrder, user: User) -> PurchaseOrder:
    """DRAFT -> PENDING_FM_APPROVAL. Locks the PO into a fiscal period."""
    if po.status != PurchaseOrder.Status.DRAFT:
        raise ValidationError(
            f"Only draft POs can be submitted. Current status: {po.status}."
        )

    lines = list(po.lines.all())
    if not lines:
        raise ValidationError("Add at least one line before submitting the PO.")

    # Quantity can never be negative. A negative unit price / line total is
    # LEGITIMATE — it is how a supplier discount (e.g. "Discount 5%") or a
    # claims excess deduction is carried on the PO (CFO 2026-07-08, Native
    # Events quote). The understatement risk a blanket negative-line ban was
    # guarding is still covered by the "PO total must be > 0" gate below, so
    # only reject a negative quantity here.
    for ln in lines:
        if (ln.quantity or ZERO) < ZERO:
            raise ValidationError("PO lines cannot have a negative quantity.")

    # Exchange rate must be positive — a zero/blank rate makes total_bwp 0, so
    # the commitment JE balances at P0 and a real foreign commitment vanishes
    # from the BWP ledger.
    if not po.exchange_rate or Decimal(str(po.exchange_rate)) <= ZERO:
        raise ValidationError("Exchange rate must be greater than zero.")

    po.recalculate_totals()
    if po.total_amount <= ZERO:
        raise ValidationError("PO total must be greater than zero.")

    # A reason is required on EVERY purchase order, at any value (CFO 2026-08-11).
    # The threshold used to be P5,000, and PO-ADM-2026-000045 — an iPad at
    # P4,114.26 — reached the CFO with the field blank, so the only record of why
    # we were buying it was "kindly approve the attached" in an email. It cannot be
    # repaired afterwards either: an approved PO is immutable, so the reason has to
    # be captured before submission or it is lost for good.
    if not (po.justification or '').strip():
        raise ValidationError(
            "Say why this is being bought. Every purchase order needs a reason, "
            "and it cannot be added once the PO is approved."
        )

    # Lock to a fiscal period
    period = FiscalPeriod.get_open_period_for_date(po.issue_date)
    if not period:
        raise ValidationError(
            f"No open fiscal period covers {po.issue_date}. "
            "Open or create a fiscal period before submitting."
        )
    po.fiscal_period = period

    po.status       = PurchaseOrder.Status.PENDING_FM_APPROVAL
    po.submitted_by = user
    po.submitted_at = timezone.now()
    po._allow_status_transition = True
    po.save(audit_user=user, audit_description=f"Submitted {po.po_number} for FM approval")

    AuditLog.objects.create(
        table_name='PurchaseOrder',
        record_id=str(po.pk),
        action=AuditLog.Action.UPDATE,
        new_values={'status': po.status, 'submitted_by': str(user.pk)},
        user=user,
        description=f"Submitted {po.po_number} for FM approval",
    )
    return po


# CFO directive 2026-06-29:
#   Claims POs (department='claims') — FM approval only, any amount. CFO never in chain.
#   Operational POs (admin/hr) above this threshold — FM + CFO both required.
#   Operational POs at or below this threshold — FM approval only.
CFO_APPROVAL_THRESHOLD_BWP = Decimal('10000.00')


def _po_total_bwp(po: PurchaseOrder) -> Decimal:
    """Return PO total in BWP, falling back to subtotal*exchange_rate."""
    if po.total_bwp:
        return Decimal(str(po.total_bwp))
    rate = Decimal(str(po.exchange_rate or 1))
    sub = Decimal(str(po.subtotal or 0))
    tax = Decimal(str(po.tax_total or 0))
    return (sub + tax) * rate


def _fm_leg_sod_ok(po: PurchaseOrder, user) -> bool:
    """True if segregation of duties permits ``user`` to FM-approve ``po`` now.

    Single-step POs (claims dept, or an operational PO below
    ``CFO_APPROVAL_THRESHOLD_BWP``) carry no SoD restriction. A two-step
    operational PO (>= threshold) bars its creator and its submitter from the
    FM leg. This MIRRORS the guard enforced in :func:`fm_approve`; the detail
    serializer consumes it so the "Approve" button is only offered when the
    action would actually succeed (no dead-end button). Keep the two in step.
    """
    single_step = (po.department == PurchaseOrder.Department.CLAIMS
                   or _po_total_bwp(po) < CFO_APPROVAL_THRESHOLD_BWP)
    if single_step:
        return True
    return po.created_by_id != user.pk and po.submitted_by_id != user.pk


def _cfo_leg_sod_ok(po: PurchaseOrder, user) -> bool:
    """True if segregation of duties permits ``user`` to CFO-approve ``po``.

    The CFO leg is only ever reached by two-step operational POs, so the
    creator, the submitter and the FM-approver are all barred. This MIRRORS the
    guard enforced in :func:`cfo_approve`; the detail serializer consumes it so
    the "Approve" button is only offered when the action would succeed.
    """
    return (po.created_by_id != user.pk
            and po.submitted_by_id != user.pk
            and po.fm_approved_by_id != user.pk)


# GR-IR clearing account — auto-drafted bill lines default here so that, on
# post, the bill debits 2145 (clearing the GRN's credit) instead of double-
# booking expense. PAY-001 / GR-IR-fix, CFO directive 2026-05-27.
_GR_IR_CODE = '2145'


def _materialise_vendor_bill(po: PurchaseOrder, user: User):
    """PAY-001 (CFO directive 2026-05-27): when a PO is APPROVED, auto-create
    a DRAFT vendor bill pre-filled from the PO so the Payables team does not
    re-key it. Idempotent — never creates a second bill for the same PO.

    The draft is left in DRAFT for a preparer to attach the supplier's tax
    invoice and submit through maker-checker. Line accounts default to the
    GR-IR clearing account (2145); the bill's posting JE therefore clears the
    GRN receipt (Dr 2145 / Cr AP) rather than re-recognising expense.

    Returns the created Invoice, or None if one already exists / PO has no
    supplier. Failures here must NOT roll back the PO approval, so the caller
    wraps this in its own savepoint and swallows errors (the bill can always
    be created manually as a fallback).
    """
    from billing.models import (
        Invoice, InvoiceLine, BillApprovalPolicy, Contact,
    )
    from core.models import TaxRate
    from ledger.models import Account

    if po.supplier_id is None:
        return None
    # Idempotency: one auto bill per PO.
    existing = Invoice.objects.filter(
        purchase_order=po,
        invoice_type=Invoice.InvoiceType.VENDOR_BILL,
    ).first()
    if existing is not None:
        return existing

    gr_ir = Account.objects.filter(code=_GR_IR_CODE).first()
    zero_tax = (TaxRate.objects.filter(tax_code='VAT_ZERO').first()
                or TaxRate.objects.filter(rate=Decimal('0.00')).first())

    bill = Invoice(
        invoice_type  = Invoice.InvoiceType.VENDOR_BILL,
        contact       = po.supplier,
        company       = po.company,
        issue_date    = timezone.localdate(),
        currency_code = po.currency_code,
        exchange_rate = po.exchange_rate,
        status        = Invoice.Status.DRAFT,
        purchase_order = po,
        created_by    = user,
        description   = f"Auto-drafted from {po.po_number}",
    )
    bill.save(audit_user=user,
              audit_description=f"Auto-drafted vendor bill from {po.po_number}")

    for ln in po.lines.all():
        InvoiceLine.objects.create(
            invoice     = bill,
            account     = ln.account or gr_ir,
            description = ln.description,
            quantity    = ln.quantity,
            unit_price  = ln.unit_price,
            tax_code    = ln.tax_code or zero_tax,
        )

    bill.recalculate_totals()
    # Approval tier from the configurable policy (placeholders → Tier 3/CFO).
    policy = BillApprovalPolicy.current()
    total_bwp = _bwp(bill.total_amount, bill.exchange_rate)
    tier = policy.assign_tier(total_bwp)
    bill.approval_tier = f"Tier {tier}"
    bill.save(update_fields=['subtotal', 'tax_total', 'total_amount',
                             'balance_due', 'approval_tier', 'updated_at'])
    return bill


@transaction.atomic
@transaction.atomic
def fm_approve(po: PurchaseOrder, user: User) -> PurchaseOrder:
    """
    PENDING_FM_APPROVAL -> PENDING_CFO_APPROVAL (normal) OR
    PENDING_FM_APPROVAL -> APPROVED (single-approval path,
    when po total < SINGLE_APPROVAL_THRESHOLD_BWP — CFO directive 2026-05-21).
    """
    # Concurrency guard — lock the row and re-read committed status so two
    # simultaneous approve clicks can't both pass the check and post TWO
    # commitment JEs (double encumbrance). Mirrors JournalEntry.post().
    _locked = (PurchaseOrder.objects.select_for_update()
               .filter(pk=po.pk).values_list('status', flat=True).first())
    if _locked is not None:
        po.status = _locked
    if po.status != PurchaseOrder.Status.PENDING_FM_APPROVAL:
        raise ValidationError(
            f"Only PENDING_FM_APPROVAL POs can be FM-approved. Current: {po.status}."
        )
    if not _can_approve_po(user, po):
        if po.department == PurchaseOrder.Department.CLAIMS:
            raise ValidationError(
                "Claims purchase orders are approved by the claims seniors "
                "(Claims Manager, Claims Team Leader, or Senior Claims "
                "Associate). Finance approves the payment later via the "
                "three-way match."
            )
        raise ValidationError(
            "You do not have authority to approve this PO. "
            "Title must be Finance Manager, Financial Controller, or CFO."
        )

    total_bwp = _po_total_bwp(po)
    # Claims: always single-step (no CFO). Operational: single-step only if <= 10K BWP.
    single_step = (po.department == PurchaseOrder.Department.CLAIMS
                   or total_bwp < CFO_APPROVAL_THRESHOLD_BWP)

    # Segregation of duties for operational POs above the CFO threshold only.
    if not single_step:
        if po.created_by_id == user.pk:
            raise ValidationError(
                "Segregation of duties: you cannot FM-approve a PO you created."
            )
        if po.submitted_by_id == user.pk:
            raise ValidationError(
                "Segregation of duties: you cannot FM-approve a PO you submitted."
            )

    po.fm_approved_by  = user
    po.fm_approved_at  = timezone.now()
    if single_step:
        po.status = PurchaseOrder.Status.APPROVED
    else:
        po.status = PurchaseOrder.Status.PENDING_CFO_APPROVAL
    po._allow_status_transition = True
    po.save(audit_user=user, audit_description=f"FM-approved {po.po_number}")

    AuditLog.objects.create(
        table_name='PurchaseOrder',
        record_id=str(po.pk),
        action=AuditLog.Action.UPDATE,
        new_values={
            'status': po.status,
            'fm_approved_by': str(user.pk),
            'single_approval_path': bool(single_step),
            'po_total_bwp': str(total_bwp),
        },
        user=user,
        description=(
            f"FM-approved {po.po_number}"
            + (" — single approval (claims dept)" if po.department == PurchaseOrder.Department.CLAIMS
               else f" — single approval (< {CFO_APPROVAL_THRESHOLD_BWP} BWP)" if single_step
               else "")
        ),
    )

    # Single-approval path: post the commitment JE here (the CFO step would
    # normally do it). Same @transaction.atomic envelope from the caller
    # rolls back the approval on JE failure.
    if single_step:
        commitment_je = _post_commitment_je(po, user)
        po.commitment_journal_entry = commitment_je
        po._allow_status_transition = True
        po.save(update_fields=['commitment_journal_entry'])
        # PAY-001: auto-draft the vendor bill. Own savepoint so a bill error
        # never rolls back the approval + commitment JE.
        try:
            with transaction.atomic():
                _materialise_vendor_bill(po, user)
        except Exception:   # noqa: BLE001
            pass

    return po


@transaction.atomic
def cfo_approve(po: PurchaseOrder, user: User) -> PurchaseOrder:
    """PENDING_CFO_APPROVAL -> APPROVED. Final approval — PO is now committed."""
    # Concurrency guard — lock + re-read committed status so a double-click
    # can't post two commitment JEs for the same PO.
    _locked = (PurchaseOrder.objects.select_for_update()
               .filter(pk=po.pk).values_list('status', flat=True).first())
    if _locked is not None:
        po.status = _locked
    if po.status != PurchaseOrder.Status.PENDING_CFO_APPROVAL:
        raise ValidationError(
            f"Only PENDING_CFO_APPROVAL POs can be CFO-approved. Current: {po.status}."
        )
    if not _can_approve_as_cfo(user):
        raise ValidationError(
            "Only the CFO can give final approval on a Purchase Order."
        )
    if po.created_by_id == user.pk:
        raise ValidationError(
            "Segregation of duties: the CFO cannot approve a PO they created."
        )
    if po.fm_approved_by_id == user.pk:
        raise ValidationError(
            "Segregation of duties: the CFO approver must be different from the FM approver."
        )
    if po.submitted_by_id == user.pk:
        raise ValidationError(
            "Segregation of duties: the CFO approver cannot be the person who submitted the PO."
        )

    po.cfo_approved_by = user
    po.cfo_approved_at = timezone.now()
    po.status          = PurchaseOrder.Status.APPROVED
    po._allow_status_transition = True
    po.save(audit_user=user, audit_description=f"CFO-approved {po.po_number}")

    # Issue #58 / steering rule 4: post the commitment JE inside the same
    # @transaction.atomic — failure rolls back the approval too.
    commitment_je = _post_commitment_je(po, user)
    po.commitment_journal_entry = commitment_je
    po._allow_status_transition = True
    po.save(update_fields=['commitment_journal_entry'])

    # PAY-001: auto-draft the vendor bill. Own savepoint so a bill error
    # never rolls back the approval + commitment JE.
    try:
        with transaction.atomic():
            _materialise_vendor_bill(po, user)
    except Exception:   # noqa: BLE001
        pass

    AuditLog.objects.create(
        table_name='PurchaseOrder',
        record_id=str(po.pk),
        action=AuditLog.Action.APPROVE,
        new_values={
            'status':           po.status,
            'cfo_approved_by':  str(user.pk),
            'commitment_je':    commitment_je.entry_number,
        },
        user=user,
        description=f"CFO-approved {po.po_number}; commitment JE {commitment_je.entry_number}",
    )
    return po


@transaction.atomic
def reject(po: PurchaseOrder, user: User, reason: str) -> PurchaseOrder:
    """Reject a PO at either approval stage."""
    if po.status not in (
        PurchaseOrder.Status.PENDING_FM_APPROVAL,
        PurchaseOrder.Status.PENDING_CFO_APPROVAL,
    ):
        raise ValidationError(
            f"Only pending POs can be rejected. Current: {po.status}."
        )
    # Whoever may APPROVE this PO's current leg may also reject it — for a
    # claims PO that is the claims seniors (Fable audit 2026-07-09: they could
    # approve but not decline), for operational POs the FM/FC/CFO.
    if not _can_approve_po(user, po):
        raise ValidationError(
            "Only an authorised approver of this PO may reject it."
        )
    if not reason.strip():
        raise ValidationError("Rejection reason is required.")

    po.rejection_reason = reason.strip()
    po.status           = PurchaseOrder.Status.REJECTED
    po._allow_status_transition = True
    po.save(audit_user=user, audit_description=f"Rejected {po.po_number}")

    AuditLog.objects.create(
        table_name='PurchaseOrder',
        record_id=str(po.pk),
        action=AuditLog.Action.REJECT if hasattr(AuditLog.Action, 'REJECT') else AuditLog.Action.UPDATE,
        new_values={'status': po.status, 'rejection_reason': reason},
        user=user,
        description=f"Rejected {po.po_number}: {reason}",
    )
    return po


@transaction.atomic
def cancel(po: PurchaseOrder, user: User, reason: str) -> PurchaseOrder:
    """Cancel an approved PO (CFO only). Cannot cancel if any goods received."""
    if po.status not in (
        PurchaseOrder.Status.APPROVED,
        PurchaseOrder.Status.PARTIALLY_RECEIVED,
    ):
        raise ValidationError(
            f"Only approved POs can be cancelled. Current: {po.status}."
        )
    if not _can_cancel_po(user, po):
        if po.department == PurchaseOrder.Department.CLAIMS:
            raise ValidationError("Only the Claims Manager may cancel a claims PO.")
        raise ValidationError("Only the CFO may cancel an approved PO.")
    if not reason.strip():
        raise ValidationError("Cancellation reason is required.")

    if po.is_partially_received:
        raise ValidationError(
            "Cannot cancel a PO that has received goods. "
            "Process a return first, or close the PO with a note instead."
        )

    # Issue #58: release any remaining commitment exposure first. Returns
    # None for legacy POs without a commitment JE.
    commitment_reversal = _reverse_remaining_commitment(po, user, reason)

    po.status              = PurchaseOrder.Status.CANCELLED
    po.cancelled_by        = user
    po.cancelled_at        = timezone.now()
    po.cancellation_reason = reason.strip()
    po._allow_status_transition = True
    po.save(audit_user=user, audit_description=f"Cancelled {po.po_number}")

    AuditLog.objects.create(
        table_name='PurchaseOrder',
        record_id=str(po.pk),
        action=AuditLog.Action.UPDATE,
        new_values={
            'status':                 po.status,
            'cancellation_reason':    reason,
            'commitment_reversal_je': (
                commitment_reversal.entry_number if commitment_reversal else None
            ),
        },
        user=user,
        description=f"Cancelled {po.po_number}: {reason}",
    )
    return po


# ===========================================================================
#  Goods Receipt Note
# ===========================================================================

@transaction.atomic
def post_grn(grn: GoodsReceiptNote, user: User) -> JournalEntry:
    """
    Post a draft GRN. Creates and posts the journal entry:

        DR  expense/asset accounts (per line, at PO unit price)
        CR  2145 Goods Received Not Invoiced

    Updates running quantity_received on each PO line and rolls forward
    PO header status (PARTIALLY_RECEIVED / FULLY_RECEIVED).
    """
    if grn.status != GoodsReceiptNote.Status.DRAFT:
        raise ValidationError(
            f"Only draft GRNs can be posted. Current: {grn.status}."
        )

    po = grn.purchase_order
    if po.status not in (
        PurchaseOrder.Status.APPROVED,
        PurchaseOrder.Status.PARTIALLY_RECEIVED,
    ):
        raise ValidationError(
            f"Cannot receive against PO {po.po_number} (status: {po.status})."
        )

    lines = list(grn.lines.select_related('po_line', 'po_line__account').all())
    if not lines:
        raise ValidationError("Add at least one line before posting the GRN.")

    # Validate quantities — cannot receive more than ordered
    for ln in lines:
        outstanding = ln.po_line.quantity_outstanding
        if ln.quantity_received > outstanding + Decimal('0.0001'):
            raise ValidationError(
                f"Line '{ln.po_line.description}': cannot receive "
                f"{ln.quantity_received} — only {outstanding} outstanding."
            )

    # Resolve clearing account
    try:
        gr_ir = Account.objects.get(code=GR_IR_ACCOUNT_CODE)
    except Account.DoesNotExist:
        raise ValidationError(
            f"Required clearing account {GR_IR_ACCOUNT_CODE} (Goods Received Not Invoiced) "
            "is missing from the chart of accounts. Run setup_chart_of_accounts."
        )

    rate = po.exchange_rate

    # Issue #58: release the proportional commitment for the value being
    # received. Returns None for legacy POs that predate the feature.
    _reverse_commitment_for_received_lines(grn, user)

    # Build the JE
    je = JournalEntry.objects.create(
        entry_date    = grn.receipt_date,
        description   = f"GRN {grn.grn_number} — {po.po_number} — {po.supplier.name}",
        source_type   = 'grn',
        source_id     = grn.pk,
        journal_type  = JournalEntry.JournalType.PURCHASES,
        currency_code = po.currency_code,
        exchange_rate = rate,
        company       = po.company,
        is_related_party = po.supplier.is_related_party,
        created_by    = user,
        status        = JournalEntry.Status.DRAFT,
    )

    total_received_value_orig = ZERO
    total_received_value_bwp  = ZERO

    # CFO directive 2026-05-21 — POs no longer carry a GL account. When the
    # PO line has no account set, the receipt is recorded against the GR-IR
    # clearing account on BOTH legs (Dr GR-IR / Cr GR-IR nets to zero per
    # line) and the actual expense is recognised only when the bill is
    # approved against the PO. Legacy PO lines with an account FK keep the
    # original behaviour so historical commitments still post correctly.
    for ln in lines:
        line_value_orig = (ln.quantity_received * ln.po_line.unit_price).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP
        )
        line_value_bwp = _bwp(line_value_orig, rate)

        dr_account = ln.po_line.account or gr_ir

        JournalEntryLine.objects.create(
            journal_entry  = je,
            account        = dr_account,
            description    = f"Receipt: {ln.po_line.description} (qty {ln.quantity_received})",
            debit_amount   = line_value_orig,
            credit_amount  = ZERO,
            debit_bwp      = line_value_bwp,
            credit_bwp     = ZERO,
            contact        = po.supplier,
            is_related_party = po.supplier.is_related_party,
        )
        total_received_value_orig += line_value_orig
        total_received_value_bwp  += line_value_bwp

        # Update PO line running total
        ln.po_line.quantity_received = (ln.po_line.quantity_received or ZERO) + ln.quantity_received
        ln.po_line.save()

    # Credit GR-IR clearing for the same total
    JournalEntryLine.objects.create(
        journal_entry  = je,
        account        = gr_ir,
        description    = f"GR-IR clearing for {grn.grn_number}",
        debit_amount   = ZERO,
        credit_amount  = total_received_value_orig,
        debit_bwp      = ZERO,
        credit_bwp     = total_received_value_bwp,
        contact        = po.supplier,
        is_related_party = po.supplier.is_related_party,
    )

    # Post the JE — direct post is appropriate here because the PO already
    # carried FM + CFO approval, so a second human approval would be ceremony.
    je.post(user=user, _allow_direct=True)

    # Update GRN
    grn.status         = GoodsReceiptNote.Status.POSTED
    grn.journal_entry  = je
    grn._allow_status_transition = True
    grn.save(audit_user=user, audit_description=f"Posted {grn.grn_number}")

    # Roll up PO status
    if po.is_fully_received:
        po.status = PurchaseOrder.Status.FULLY_RECEIVED
    elif po.is_partially_received:
        po.status = PurchaseOrder.Status.PARTIALLY_RECEIVED
    po._allow_status_transition = True
    po.save(audit_user=user, audit_description=f"GRN {grn.grn_number} posted")

    AuditLog.objects.create(
        table_name='GoodsReceiptNote',
        record_id=str(grn.pk),
        action=AuditLog.Action.POST,
        new_values={
            'status':        grn.status,
            'journal_entry': str(je.pk),
            'value_bwp':     str(total_received_value_bwp),
        },
        user=user,
        description=f"Posted GRN {grn.grn_number} (BWP {total_received_value_bwp})",
    )

    # Manus PO Audit #6 (CFO 2026-05-20): auto-create Asset records when
    # a GRN posts against a fixed-asset GL. Best-effort — failure here
    # never blocks the GRN itself.
    try:
        from .phase_b_services import link_capex_to_assets
        link_capex_to_assets(grn, user)
    except Exception:    # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning(
            'link_capex_to_assets failed for grn=%s; GRN already posted.',
            grn.id,
        )

    return je


# ===========================================================================
#  3-way match
# ===========================================================================

@transaction.atomic
def match_bill_to_po(bill, po: PurchaseOrder, user: User,
                     override_reason: str = '') -> POBillMatch:
    """
    Create a match record linking *bill* (a vendor_bill Invoice) to *po*.

    Computes quantity and price variances by comparing posted bill total
    against received-but-not-billed value on the PO.

    Tolerance: zero. Any non-zero variance is flagged.
    Override: if *override_reason* is supplied, status = OVERRIDE.

    NOTE: this v1 implementation matches at the header level (total amounts).
    Line-level matching is a follow-up enhancement.
    """
    from billing.models import Invoice

    if bill.invoice_type != Invoice.InvoiceType.VENDOR_BILL:
        raise ValidationError(
            f"Only vendor bills can be matched to a PO. "
            f"Got invoice_type={bill.invoice_type}."
        )
    if bill.status != Invoice.Status.POSTED:
        raise ValidationError(
            f"Only posted bills can be matched to a PO. Current: {bill.status}."
        )
    if bill.contact_id != po.supplier_id:
        raise ValidationError(
            f"Supplier mismatch: bill is for {bill.contact.name}, "
            f"PO is for {po.supplier.name}."
        )
    if po.status not in (
        PurchaseOrder.Status.APPROVED,
        PurchaseOrder.Status.PARTIALLY_RECEIVED,
        PurchaseOrder.Status.FULLY_RECEIVED,
    ):
        raise ValidationError(
            f"PO {po.po_number} is {po.status} and cannot be matched."
        )
    if POBillMatch.objects.filter(purchase_order=po, bill=bill).exists():
        raise ValidationError(
            f"Bill {bill.invoice_number} is already matched to PO {po.po_number}."
        )

    # Compare bill total to outstanding received-but-not-billed PO value
    received_value = ZERO
    for ln in po.lines.all():
        received_value += (ln.quantity_received * ln.unit_price).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP
        )
        # bump quantity_billed up to quantity_received for this match
        new_billed = min(ln.quantity_received, ln.quantity)
        ln.quantity_billed = new_billed
        ln.save()

    price_variance = bill.total_amount - received_value

    if override_reason.strip():
        # Overriding a variance clears the bill for payment (is_payable includes
        # OVERRIDE) — so it needs Tier-2 authority, exactly like tier2_approve.
        # Fable audit 2026-07-09: create-match let ANY user pass an override
        # reason and mark a bill payable, bypassing the tier approval flow.
        if not _can_approve_tier2(user):
            raise ValidationError(
                "Overriding a bill/PO variance clears the bill for payment and "
                "requires Tier-2 authority (CFO / Financial Controller / senior). "
                "You are not authorised to override — route it for approval instead."
            )
        match_status = POBillMatch.MatchStatus.OVERRIDE
    elif price_variance == ZERO:
        match_status = POBillMatch.MatchStatus.MATCHED
    else:
        match_status = POBillMatch.MatchStatus.VARIANCE_PRICE

    match = POBillMatch.objects.create(
        purchase_order   = po,
        bill             = bill,
        match_status     = match_status,
        quantity_variance = ZERO,   # header match — not computed line-by-line
        price_variance   = price_variance,
        override_reason  = override_reason.strip(),
        matched_by       = user,
    )

    # Roll up PO status to CLOSED if every line is fully received and billed
    if po.is_fully_received and po.is_fully_billed:
        po.status = PurchaseOrder.Status.CLOSED
        po._allow_status_transition = True
        po.save(audit_user=user, audit_description=f"PO {po.po_number} closed by match")

    AuditLog.objects.create(
        table_name='POBillMatch',
        record_id=str(match.pk),
        action=AuditLog.Action.CREATE,
        new_values={
            'po':             po.po_number,
            'bill':           bill.invoice_number,
            'match_status':   match_status,
            'price_variance': str(price_variance),
        },
        user=user,
        description=f"Matched {bill.invoice_number} to {po.po_number} ({match_status})",
    )
    return match


# ===========================================================================
#  Period close hook
# ===========================================================================

def check_open_pos_for_period(period: FiscalPeriod) -> List[PurchaseOrder]:
    """
    Return POs that block closing *period* — any PO raised in this period
    that is still in an active state (not closed / cancelled / rejected).
    """
    blocking_statuses = [
        PurchaseOrder.Status.DRAFT,
        PurchaseOrder.Status.PENDING_FM_APPROVAL,
        PurchaseOrder.Status.PENDING_CFO_APPROVAL,
        PurchaseOrder.Status.APPROVED,
        PurchaseOrder.Status.PARTIALLY_RECEIVED,
        PurchaseOrder.Status.FULLY_RECEIVED,   # received but not yet billed/matched
    ]
    return list(
        PurchaseOrder.objects
        .filter(fiscal_period=period, status__in=blocking_statuses)
        .select_related('supplier')
        .order_by('po_number')
    )


# ===========================================================================
#  Vendor Bank Account — maker-checker workflow
# ===========================================================================
#
# Authorisation:
#   - Any user with finance/procurement access creates a DRAFT
#   - Approver must be Finance Manager / Financial Controller / CFO
#   - Approver MUST NOT be the creator OR the submitter (segregation of duties)
#   - Retire is also approver-only
# ===========================================================================


def _can_approve_bank(user) -> bool:
    """CHECKER side of the vendor-bank SoD — Finance Manager or CFO.

    Workstream #3 (CFO-approved 2026-07-02): aligned to the central SoD engine
    (`UserProfile.can_check_controlled_txn`). Financial Controller was REMOVED
    from the approver set — under the maker-checker model FC is a MAKER, so
    leaving FC as a checker allowed FC-A-creates / FC-B-approves collusion. The
    record-level 'approver != creator/submitter' guards below still apply and
    are never waived."""
    profile = get_user_profile(user)
    return bool(profile and profile.can_check_controlled_txn)


@transaction.atomic
def submit_bank_for_approval(bank: VendorBankAccount, user) -> VendorBankAccount:
    """DRAFT -> PENDING_APPROVAL."""
    # SoD #3 (CFO-approved 2026-07-02): only a MAKER (Financial Controller /
    # Senior Accountant / Accountant) may originate a vendor bank change.
    # Finance Manager is a CHECKER here — it approves, it does not originate.
    # This removes the FM-creates + FM-approves collusion path the auditor
    # flagged. A different Finance Manager approves it in approve_bank below.
    _profile = get_user_profile(user)
    if not (_profile and _profile.can_originate_controlled_txn):
        raise ValidationError(
            "Only a maker (Financial Controller / Senior Accountant / Accountant) "
            "may submit a vendor bank change. Finance Managers approve them."
        )
    if bank.status != VendorBankAccount.Status.DRAFT:
        raise ValidationError(
            f"Only draft bank accounts can be submitted. Current: {bank.status}."
        )
    if not bank.bank_name or not bank.account_number or not bank.account_holder_name:
        raise ValidationError(
            "Bank name, account holder name, and account number are required "
            "before submitting for approval."
        )

    bank.status        = VendorBankAccount.Status.PENDING_APPROVAL
    bank.submitted_by  = user
    bank.submitted_at  = timezone.now()
    bank._allow_status_transition = True
    bank.save(audit_user=user, audit_description=f"Submitted bank account for {bank.contact.name}")

    AuditLog.objects.create(
        table_name='VendorBankAccount',
        record_id=str(bank.pk),
        action=AuditLog.Action.UPDATE,
        new_values={'status': bank.status, 'submitted_by': str(user.pk)},
        user=user,
        description=f"Submitted bank account for {bank.contact.name} for approval",
    )
    return bank


@transaction.atomic
def approve_bank(bank: VendorBankAccount, user) -> VendorBankAccount:
    """PENDING_APPROVAL -> ACTIVE.

    Segregation of duties: the approver must be a different user from the
    creator AND the submitter — otherwise the maker-checker control is just
    theatre.
    """
    if bank.status != VendorBankAccount.Status.PENDING_APPROVAL:
        raise ValidationError(
            f"Only PENDING_APPROVAL bank accounts can be approved. Current: {bank.status}."
        )
    if not _can_approve_bank(user):
        raise ValidationError(
            "Only the Finance Manager, Financial Controller, or CFO may "
            "approve a vendor bank account."
        )
    if bank.created_by_id == user.pk:
        raise ValidationError(
            "Segregation of duties: you cannot approve a bank account you created."
        )
    if bank.submitted_by_id == user.pk:
        raise ValidationError(
            "Segregation of duties: you cannot approve a bank account you submitted."
        )

    bank.status       = VendorBankAccount.Status.ACTIVE
    bank.approved_by  = user
    bank.approved_at  = timezone.now()
    bank._allow_status_transition = True
    bank.save(audit_user=user, audit_description=f"Approved bank account for {bank.contact.name}")

    AuditLog.objects.create(
        table_name='VendorBankAccount',
        record_id=str(bank.pk),
        action=AuditLog.Action.APPROVE,
        new_values={
            'status':       bank.status,
            'approved_by':  str(user.pk),
            'bank_name':    bank.bank_name,
            'account_no':   bank.account_number[-4:].rjust(len(bank.account_number), '*'),
        },
        user=user,
        description=f"Approved bank account for {bank.contact.name} ({bank.bank_name})",
    )
    return bank


@transaction.atomic
def reject_bank(bank: VendorBankAccount, user, reason: str) -> VendorBankAccount:
    """PENDING_APPROVAL -> REJECTED."""
    if bank.status != VendorBankAccount.Status.PENDING_APPROVAL:
        raise ValidationError(
            f"Only PENDING_APPROVAL bank accounts can be rejected. Current: {bank.status}."
        )
    if not _can_approve_bank(user):
        raise ValidationError(
            "Only an authorised approver may reject a vendor bank account."
        )
    if not reason.strip():
        raise ValidationError("Rejection reason is required.")

    bank.status            = VendorBankAccount.Status.REJECTED
    bank.rejected_by       = user
    bank.rejected_at       = timezone.now()
    bank.rejection_reason  = reason.strip()
    bank._allow_status_transition = True
    bank.save(audit_user=user, audit_description=f"Rejected bank account for {bank.contact.name}")

    AuditLog.objects.create(
        table_name='VendorBankAccount',
        record_id=str(bank.pk),
        action=AuditLog.Action.UPDATE,
        new_values={'status': bank.status, 'reason': reason},
        user=user,
        description=f"Rejected bank account for {bank.contact.name}: {reason}",
    )
    return bank


@transaction.atomic
def retire_bank(bank: VendorBankAccount, user, reason: str) -> VendorBankAccount:
    """ACTIVE -> RETIRED.

    Use this when banking details have changed. The new details must be
    entered as a NEW DRAFT that goes through approval — this is the only
    path to changing an active bank account.
    """
    if bank.status != VendorBankAccount.Status.ACTIVE:
        raise ValidationError(
            f"Only ACTIVE bank accounts can be retired. Current: {bank.status}."
        )
    if not _can_approve_bank(user):
        raise ValidationError(
            "Only an authorised approver may retire a vendor bank account."
        )
    if not reason.strip():
        raise ValidationError(
            "Retirement reason is required (state why the account is no longer used)."
        )

    bank.status              = VendorBankAccount.Status.RETIRED
    bank.retired_by          = user
    bank.retired_at          = timezone.now()
    bank.retirement_reason   = reason.strip()
    bank._allow_status_transition = True
    bank.save(audit_user=user, audit_description=f"Retired bank account for {bank.contact.name}")

    AuditLog.objects.create(
        table_name='VendorBankAccount',
        record_id=str(bank.pk),
        action=AuditLog.Action.UPDATE,
        new_values={'status': bank.status, 'reason': reason},
        user=user,
        description=f"Retired bank account for {bank.contact.name}: {reason}",
    )
    return bank


def get_active_bank_accounts(contact, currency_code=None):
    """Return ACTIVE bank accounts for *contact*, optionally filtered by currency."""
    qs = VendorBankAccount.objects.filter(
        contact=contact,
        status=VendorBankAccount.Status.ACTIVE,
    )
    if currency_code:
        qs = qs.filter(currency_code_id=currency_code)
    return qs.order_by('-is_default', '-approved_at')


# ===========================================================================
#  Auto-match on bill post (CFO-mandated)
#
#  When a vendor bill posts to the GL, the system instantly:
#    1. Computes the bill-vs-PO variance percentage.
#    2. Routes to the right approval tier:
#         bill ≤ PO              → MATCHED (silently)
#         bill > PO ≤ tier-1 %   → NEEDS_TIER1_APPROVAL (dept manager alone)
#         bill > PO > tier-1 %   → NEEDS_TIER2_APPROVAL (dept manager + co)
#    3. Kicks off the DeepSeek second-pass verification (synchronous,
#       15s timeout, falls back to async-style ANOMALY="unavailable" verdict).
# ===========================================================================


# Department → manager-title mapping for tier-1 approval. Each PO-raising
# department has one title that may approve a small variance on its own.
_DEPT_TIER1_TITLES = {
    'claims': UserProfile.Title.CLAIMS_MANAGER,
    'admin':  UserProfile.Title.OPERATIONS_MANAGER,
    'hr':     UserProfile.Title.HR_MANAGER,
}

# Titles that may co-approve at Tier 2 (alongside the dept manager).
_TIER2_COAPPROVER_TITLES = {
    UserProfile.Title.CEO,          # functional exec (CFO directive 2026-08-04)
    UserProfile.Title.COO,
    UserProfile.Title.OPERATIONS_MANAGER,
    UserProfile.Title.FINANCE_MANAGER,
    UserProfile.Title.CFO,
}


def _expected_tier1_title(po: PurchaseOrder):
    return _DEPT_TIER1_TITLES.get(po.department)


def _can_approve_tier1(user, po: PurchaseOrder) -> bool:
    if user is not None and getattr(user, 'is_superuser', False):
        return True
    profile = get_user_profile(user)
    if profile is None or not profile.is_active:
        return False
    expected = _expected_tier1_title(po)
    # CFO can always approve tier 1
    if profile.title == UserProfile.Title.CFO:
        return True
    return expected is not None and profile.title == expected


def _can_approve_tier2(user) -> bool:
    if user is not None and getattr(user, 'is_superuser', False):
        return True
    profile = get_user_profile(user)
    if profile is None or not profile.is_active:
        return False
    return profile.title in _TIER2_COAPPROVER_TITLES


def _system_user():
    """Return the SYSTEM_API user used for system-driven matches.

    Falls back to the first superuser if no SYSTEM_API user exists.
    """
    from django.contrib.auth.models import User as _U
    sys_user = (
        _U.objects.filter(profile__title=UserProfile.Title.SYSTEM_API)
        .order_by('pk').first()
    )
    if sys_user:
        return sys_user
    return _U.objects.filter(is_superuser=True).order_by('pk').first()


@transaction.atomic
def auto_match_on_bill_post(bill, posting_user) -> POBillMatch:
    """
    Run the deterministic 3-way match the moment a vendor bill posts.

    Computes the variance percentage and assigns the right MatchStatus.
    Also kicks off the DeepSeek AI second-pass — failures there are NOT
    blocking (the verification record is written with verdict=UNAVAILABLE).
    """
    from billing.models import Invoice
    if bill.invoice_type != Invoice.InvoiceType.VENDOR_BILL:
        raise ValidationError('auto_match_on_bill_post is only for vendor bills.')
    if not bill.purchase_order_id:
        raise ValidationError('Bill has no PO reference — cannot auto-match.')

    po = bill.purchase_order
    if POBillMatch.objects.filter(purchase_order=po, bill=bill).exists():
        # Idempotent — never duplicate a match
        return POBillMatch.objects.get(purchase_order=po, bill=bill)

    # Variance math — based on totals, not line-by-line, in v1
    po_total   = po.total_amount or ZERO
    bill_total = bill.total_amount or ZERO
    if po_total > ZERO:
        variance_pct = ((bill_total - po_total) / po_total * Decimal('100')).quantize(
            Decimal('0.0001'), rounding=ROUND_HALF_UP,
        )
    else:
        variance_pct = ZERO
    price_variance = bill_total - po_total

    # Roll up quantity_billed on each PO line
    for ln in po.lines.all():
        ln.quantity_billed = min(ln.quantity_received or ZERO, ln.quantity)
        ln.save()

    # Decide match status
    policy   = VariancePolicy.current()
    tier1_pct = policy.tier1_ceiling_pct or Decimal('5.0000')

    if price_variance <= ZERO:
        status = POBillMatch.MatchStatus.MATCHED
    elif variance_pct <= tier1_pct:
        status = POBillMatch.MatchStatus.NEEDS_TIER1_APPROVAL
    else:
        status = POBillMatch.MatchStatus.NEEDS_TIER2_APPROVAL

    sys_user = _system_user() or posting_user

    match = POBillMatch.objects.create(
        purchase_order   = po,
        bill             = bill,
        match_status     = status,
        quantity_variance = ZERO,   # header-level v1
        price_variance   = price_variance,
        variance_pct     = variance_pct,
        matched_by       = sys_user,
        matched_at       = timezone.now(),
    )

    # Roll up PO status if everything is now received and matched
    if po.is_fully_received and po.is_fully_billed and match.is_payable:
        po.status = PurchaseOrder.Status.CLOSED
        po._allow_status_transition = True
        po.save(audit_user=sys_user, audit_description=f"PO closed by auto-match {bill.invoice_number}")

    AuditLog.objects.create(
        table_name='POBillMatch',
        record_id=str(match.pk),
        action=AuditLog.Action.CREATE,
        new_values={
            'po':              po.po_number,
            'bill':            bill.invoice_number,
            'match_status':    status,
            'variance_pct':    str(variance_pct),
            'price_variance':  str(price_variance),
            'tier1_ceiling':   str(tier1_pct),
        },
        user=sys_user,
        description=(
            f"Auto-match {bill.invoice_number} to {po.po_number}: "
            f"{status} (variance {variance_pct}%)"
        ),
    )

    # Fire the DeepSeek second-pass — synchronous with timeout + fallback
    try:
        verify_bill_with_ai(bill, po, match, posting_user, timeout=15.0)
    except Exception as exc:  # noqa: BLE001
        # AI failure must never block the deterministic match record
        BillAIVerification.objects.create(
            bill=bill, po=po, match=match,
            verdict=BillAIVerification.Verdict.UNAVAILABLE,
            error_message=str(exc)[:500],
        )

    return match


# ---------------------------------------------------------------------------
# Line-level 3-way match
# ---------------------------------------------------------------------------

@transaction.atomic
def match_bill_lines(bill):
    """
    Walk each bill line and create a POBillMatchLine linking it back to the
    PurchaseOrderLine (matched by description, falling back to first
    available line in the same PO) and the most-recent GoodsReceiptNoteLine
    for that PO line.

    Updates PurchaseOrderLine.qty_billed_to_date in lockstep. Raises
    ValidationError if the cumulative qty_billed_to_date on any PO line
    would exceed its quantity_received.

    Idempotent at the (match, bill_line) level — duplicate calls do not
    create duplicate rows thanks to the unique constraint.

    Returns the list of created POBillMatchLine instances.
    """
    from billing.models import Invoice
    from .match_models import POBillMatchLine

    if bill.invoice_type != Invoice.InvoiceType.VENDOR_BILL:
        raise ValidationError('match_bill_lines is only for vendor bills.')
    if not bill.purchase_order_id:
        raise ValidationError('Bill has no PO reference — cannot match lines.')

    po = bill.purchase_order
    match = (
        POBillMatch.objects
        .filter(purchase_order=po, bill=bill)
        .order_by('-matched_at')
        .first()
    )
    if match is None:
        raise ValidationError(
            f'No POBillMatch header exists for bill {bill.invoice_number} / '
            f'PO {po.po_number}. Run auto_match_on_bill_post first.'
        )

    po_lines = list(po.lines.all())
    if not po_lines:
        raise ValidationError(f'PO {po.po_number} has no lines.')

    created: list = []
    for bl in bill.lines.select_related('account').all():
        # Match strategy: same description (case-insensitive trim) wins,
        # else the first PO line with remaining qty, else po_lines[0].
        bl_desc = (bl.description or '').strip().lower()
        chosen_po_line = None
        for pl in po_lines:
            pl_desc = (pl.description or '').strip().lower()
            if bl_desc and bl_desc == pl_desc:
                chosen_po_line = pl
                break
        if chosen_po_line is None:
            for pl in po_lines:
                outstanding = (pl.quantity_received or ZERO) - (pl.qty_billed_to_date or ZERO)
                if outstanding > ZERO:
                    chosen_po_line = pl
                    break
        if chosen_po_line is None:
            chosen_po_line = po_lines[0]

        # Most-recent GRN line for this PO line
        grn_line = (
            GoodsReceiptNoteLine.objects
            .filter(po_line=chosen_po_line, grn__status=GoodsReceiptNote.Status.POSTED)
            .order_by('-grn__receipt_date', '-created_at')
            .first()
        )

        line_variance = (
            (bl.quantity * bl.unit_price) - (chosen_po_line.quantity * chosen_po_line.unit_price)
        ).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

        match_line, was_created = POBillMatchLine.objects.get_or_create(
            match=match,
            bill_line=bl,
            defaults=dict(
                po_line=chosen_po_line,
                grn_line=grn_line,
                qty=bl.quantity,
                unit_price=bl.unit_price,
                line_variance=line_variance,
            ),
        )
        if was_created:
            chosen_po_line.qty_billed_to_date = (
                (chosen_po_line.qty_billed_to_date or ZERO) + (bl.quantity or ZERO)
            )
            chosen_po_line.save()
            created.append(match_line)

    # Over-bill guard — refuse if cumulative qty_billed_to_date > qty_received
    # on any PO line touched by this bill.
    over = (
        PurchaseOrderLine.objects
        .filter(purchase_order=po)
        .filter(qty_billed_to_date__gt=F('quantity_received'))
    )
    if over.exists():
        offenders = ', '.join(
            f'{ln.description[:40]} (billed {ln.qty_billed_to_date} > received {ln.quantity_received})'
            for ln in over
        )
        raise ValidationError(
            f'Bill {bill.invoice_number} would over-bill the PO: {offenders}. '
            'Reduce the bill line quantities or raise a GRN before re-matching.'
        )

    return created


# ---------------------------------------------------------------------------
# Tier-1 + Tier-2 approval workflow (variance overrides)
# ---------------------------------------------------------------------------

@transaction.atomic
def tier1_approve_match(match: POBillMatch, user) -> POBillMatch:
    """
    Department manager (Claims / Operations / HR) signs off on a small
    variance (≤ tier1 ceiling) OR provides the first signature on a
    larger one (Tier-2).

    Segregation of duties: approver cannot be the bill creator.
    """
    if match.match_status not in (
        POBillMatch.MatchStatus.NEEDS_TIER1_APPROVAL,
        POBillMatch.MatchStatus.NEEDS_TIER2_APPROVAL,
    ):
        raise ValidationError(
            f"Match is {match.get_match_status_display()}, "
            'not awaiting Tier-1 approval.'
        )
    if not _can_approve_tier1(user, match.purchase_order):
        expected = _expected_tier1_title(match.purchase_order)
        raise ValidationError(
            f"You are not authorised to provide Tier-1 approval for a "
            f"{match.purchase_order.get_department_display()} PO. "
            f"Required title: {expected}."
        )
    if match.bill.created_by_id == user.pk:
        raise ValidationError(
            'Segregation of duties: you cannot approve a bill you created.'
        )

    match.tier1_approved_by = user
    match.tier1_approved_at = timezone.now()

    if match.match_status == POBillMatch.MatchStatus.NEEDS_TIER1_APPROVAL:
        # Single-signature path — Tier-1 alone clears the variance
        match.match_status = POBillMatch.MatchStatus.MATCHED
    else:
        # Tier-2 required — record the first signature, await Tier-2 co-approver
        match.match_status = POBillMatch.MatchStatus.TIER1_APPROVED

    match.save(audit_user=user, audit_description=f"Tier-1 approved {match.bill.invoice_number}")

    AuditLog.objects.create(
        table_name='POBillMatch',
        record_id=str(match.pk),
        action=AuditLog.Action.APPROVE,
        new_values={
            'tier1_approved_by': str(user.pk),
            'match_status':      match.match_status,
        },
        user=user,
        description=(
            f"Tier-1 approval for {match.bill.invoice_number} ↔ "
            f"{match.purchase_order.po_number}"
        ),
    )
    return match


@transaction.atomic
def tier2_approve_match(match: POBillMatch, user) -> POBillMatch:
    """
    Co-signature on a Tier-2 variance (> tier1 ceiling). Requires:
      - Tier-1 already approved
      - Approver title in {Operations Manager, Finance Manager, CFO}
      - Approver ≠ Tier-1 approver (segregation of duties)
      - Approver ≠ bill creator
    """
    if match.match_status != POBillMatch.MatchStatus.TIER1_APPROVED:
        raise ValidationError(
            f"Match is {match.get_match_status_display()}, "
            'not awaiting Tier-2 co-approval.'
        )
    if not _can_approve_tier2(user):
        raise ValidationError(
            'Tier-2 co-approval requires the Operations Manager, '
            'Finance Manager, or CFO.'
        )
    if match.tier1_approved_by_id == user.pk:
        raise ValidationError(
            'Segregation of duties: the Tier-2 co-approver must be a '
            'different person from the Tier-1 approver.'
        )
    if match.bill.created_by_id == user.pk:
        raise ValidationError(
            'Segregation of duties: you cannot approve a bill you created.'
        )

    match.tier2_approved_by = user
    match.tier2_approved_at = timezone.now()
    match.match_status      = POBillMatch.MatchStatus.MATCHED
    match.save(audit_user=user, audit_description=f"Tier-2 approved {match.bill.invoice_number}")

    AuditLog.objects.create(
        table_name='POBillMatch',
        record_id=str(match.pk),
        action=AuditLog.Action.APPROVE,
        new_values={
            'tier2_approved_by': str(user.pk),
            'match_status':      match.match_status,
        },
        user=user,
        description=(
            f"Tier-2 co-approval for {match.bill.invoice_number} ↔ "
            f"{match.purchase_order.po_number}"
        ),
    )
    return match


@transaction.atomic
def reject_match(match: POBillMatch, user, reason: str) -> POBillMatch:
    """Reject a variance match with a reason. Bill stays unpaid."""
    if match.match_status not in (
        POBillMatch.MatchStatus.NEEDS_TIER1_APPROVAL,
        POBillMatch.MatchStatus.NEEDS_TIER2_APPROVAL,
        POBillMatch.MatchStatus.TIER1_APPROVED,
    ):
        raise ValidationError(
            'Only pending matches can be rejected.'
        )
    if not (_can_approve_tier1(user, match.purchase_order)
            or _can_approve_tier2(user)):
        raise ValidationError(
            'Only an authorised approver may reject a variance match.'
        )
    if not reason.strip():
        raise ValidationError('Rejection reason is required.')

    match.match_status     = POBillMatch.MatchStatus.REJECTED
    match.rejection_reason = reason.strip()
    match.save(audit_user=user, audit_description=f"Rejected {match.bill.invoice_number}")

    AuditLog.objects.create(
        table_name='POBillMatch',
        record_id=str(match.pk),
        action=AuditLog.Action.UPDATE,
        new_values={'match_status': match.match_status, 'reason': reason},
        user=user,
        description=f"Rejected match {match.bill.invoice_number}: {reason}",
    )
    return match


# ===========================================================================
#  DeepSeek AI verification — second-pass on bill ↔ PO
# ===========================================================================

_AI_SYSTEM_PROMPT = (
    "You are a Botswana CFO's anti-fraud co-pilot reviewing a vendor bill "
    "against the matching purchase order. Compare the two records and report "
    "discrepancies. Respond ONLY with valid JSON of the form:\n"
    '{"verdict": "clean|variance|anomaly|fraud_cue",\n'
    ' "vendor_match": true|false,\n'
    ' "currency_match": true|false,\n'
    ' "total_match": true|false,\n'
    ' "flags": ["vendor_mismatch","total_variance","line_drift",'
    '"back_dated_bill","tax_anomaly","bank_detail_drift","suspicious"],\n'
    ' "notes": "≤ 200 chars",\n'
    ' "confidence": 0..100}\n'
    'Use "clean" if no issues, "variance" for math-only differences, '
    '"anomaly" if a non-math signal (date, name drift, line drift) looks off, '
    '"fraud_cue" if anything smells like fraud (e.g. holder/vendor name '
    'mismatch with bank, suspicious round numbers, back-dating). '
    'Be conservative; reasoning steps stay private — only emit the JSON.'
)


def _build_ai_prompt(bill, po: PurchaseOrder) -> str:
    """Build the user-prompt body. No customer PII; safe to send."""
    po_lines = [
        {
            'description': (ln.description or '')[:120],
            'quantity':    str(ln.quantity),
            'unit_price':  str(ln.unit_price),
            'line_total':  str(ln.line_total),
        }
        for ln in po.lines.all()[:30]
    ]
    bill_lines = [
        {
            'description': (ln.description or '')[:120],
            'quantity':    str(ln.quantity),
            'unit_price':  str(ln.unit_price),
            'line_total':  str(ln.line_total),
        }
        for ln in bill.lines.all()[:30]
    ]
    payload = {
        'po': {
            'po_number':    po.po_number,
            'supplier':     po.supplier.name,
            'department':   po.get_department_display(),
            'currency':     po.currency_code_id,
            'total':        str(po.total_amount),
            'issue_date':   po.issue_date.isoformat() if po.issue_date else None,
            'cfo_approved_at': (po.cfo_approved_at.date().isoformat()
                                if po.cfo_approved_at else None),
            'lines':        po_lines,
        },
        'bill': {
            'invoice_number':  bill.invoice_number,
            'supplier_billed': bill.contact.name,
            'currency':        bill.currency_code_id,
            'total':           str(bill.total_amount),
            'issue_date':      bill.issue_date.isoformat() if bill.issue_date else None,
            'description':     (bill.description or '')[:300],
            'lines':           bill_lines,
        },
    }
    import json as _json
    return _json.dumps(payload, ensure_ascii=False)


def verify_bill_with_ai(bill, po: PurchaseOrder, match: POBillMatch,
                        user, *, timeout: float = 15.0) -> BillAIVerification:
    """
    Synchronous DeepSeek second-pass on a bill ↔ PO comparison. Runs after
    the deterministic match. Stores the verdict in BillAIVerification —
    advisory, never blocks the GL.

    Falls back to verdict=UNAVAILABLE on any failure (timeout, missing key,
    bad JSON, network) — the deterministic match still stands.
    """
    import json as _json
    import time as _time
    from core.ai_assist import deepseek_complete, DeepSeekUnavailable

    started = _time.monotonic()
    prompt  = _build_ai_prompt(bill, po)

    try:
        raw = deepseek_complete(
            prompt,
            system_prompt=_AI_SYSTEM_PROMPT,
            timeout=timeout,
            response_format='json_object',
        )
    except DeepSeekUnavailable as exc:
        return BillAIVerification.objects.create(
            bill=bill, po=po, match=match,
            verdict=BillAIVerification.Verdict.UNAVAILABLE,
            error_message=str(exc)[:500],
            raw_prompt=prompt[:5000],
            elapsed_seconds=Decimal(f"{_time.monotonic() - started:.2f}"),
        )

    elapsed = Decimal(f"{_time.monotonic() - started:.2f}")

    # Parse the JSON; any malformation falls back to UNAVAILABLE
    try:
        data = _json.loads(raw)
    except (TypeError, ValueError) as exc:
        return BillAIVerification.objects.create(
            bill=bill, po=po, match=match,
            verdict=BillAIVerification.Verdict.UNAVAILABLE,
            error_message=f'Bad JSON: {exc}'[:500],
            raw_prompt=prompt[:5000], raw_response=str(raw)[:5000],
            elapsed_seconds=elapsed,
        )

    verdict = data.get('verdict', 'unavailable')
    if verdict not in {'clean', 'variance', 'anomaly', 'fraud_cue'}:
        verdict = 'unavailable'

    flags = data.get('flags') or []
    if not isinstance(flags, list):
        flags = []
    flags = [str(f)[:40] for f in flags][:20]

    confidence_raw = data.get('confidence', 0)
    try:
        confidence = max(0, min(100, int(confidence_raw)))
    except (TypeError, ValueError):
        confidence = 0

    return BillAIVerification.objects.create(
        bill=bill, po=po, match=match,
        verdict=verdict,
        vendor_match=bool(data.get('vendor_match', True)),
        currency_match=bool(data.get('currency_match', True)),
        total_match=bool(data.get('total_match', True)),
        flags=flags,
        notes=str(data.get('notes', ''))[:500],
        confidence=confidence,
        raw_prompt=prompt[:5000],
        raw_response=str(raw)[:5000],
        model_used=getattr(__import__('django.conf', fromlist=['settings']).settings,
                           'DEEPSEEK_MODEL', 'deepseek-chat'),
        elapsed_seconds=elapsed,
    )


# ===========================================================================
#  PO Commitment Journal Entries (issue #58, steering rule 4)
#
#  On cfo_approve:   DR 1990  CR 2199  for po.total_bwp
#  On post_grn:      pro-rata reversal for the value of goods received
#  On cancel:        reversal of remaining (un-received) commitment
#
#  All three operate inside the caller's @transaction.atomic.
# ===========================================================================


def _commitment_accounts():
    """Resolve the two paired commitment accounts. Raises ValidationError
    if either is missing from the chart of accounts."""
    try:
        debit_acct  = Account.objects.get(code=COMMITMENT_ASSET_CODE,     is_active=True)
        credit_acct = Account.objects.get(code=COMMITMENT_LIABILITY_CODE, is_active=True)
    except Account.DoesNotExist as exc:
        raise ValidationError(
            f"Commitment accounts missing from CoA "
            f"({COMMITMENT_ASSET_CODE} / {COMMITMENT_LIABILITY_CODE}). "
            "Run `python manage.py setup_chart_of_accounts`."
        ) from exc
    return debit_acct, credit_acct


def _post_commitment_je(po: PurchaseOrder, user: User) -> JournalEntry:
    """Post a balanced DR 1990 / CR 2199 JE for po.total_bwp at CFO approval."""
    a1990, a2199 = _commitment_accounts()
    total_orig = po.total_amount
    total_bwp  = po.total_bwp

    je = JournalEntry.objects.create(
        entry_date    = po.cfo_approved_at.date() if po.cfo_approved_at else timezone.localdate(),
        description   = f"Commitment for PO {po.po_number} — {po.supplier.name}",
        source_type   = 'purchase_order',
        source_id     = po.pk,
        journal_type  = JournalEntry.JournalType.COMMITMENT,
        currency_code = po.currency_code,
        exchange_rate = po.exchange_rate,
        company       = po.company,
        is_related_party = po.supplier.is_related_party,
        created_by    = user,
        status        = JournalEntry.Status.DRAFT,
    )
    JournalEntryLine.objects.create(
        journal_entry  = je,
        account        = a1990,
        description    = f"PO {po.po_number} commitment",
        debit_amount   = total_orig,
        credit_amount  = ZERO,
        debit_bwp      = total_bwp,
        credit_bwp     = ZERO,
        contact        = po.supplier,
        is_related_party = po.supplier.is_related_party,
    )
    JournalEntryLine.objects.create(
        journal_entry  = je,
        account        = a2199,
        description    = f"PO {po.po_number} commitment reserve",
        debit_amount   = ZERO,
        credit_amount  = total_orig,
        debit_bwp      = ZERO,
        credit_bwp     = total_bwp,
        contact        = po.supplier,
        is_related_party = po.supplier.is_related_party,
    )
    je.post(user=user, _allow_direct=True)
    return je


def _reverse_commitment_for_received_lines(
    grn: GoodsReceiptNote, user: User,
) -> Optional[JournalEntry]:
    """Pro-rata reverse the PO's commitment for the value being receipted
    on this GRN. Returns None if the PO predates the commitment feature."""
    po = grn.purchase_order
    if po.commitment_journal_entry_id is None:
        return None

    rate = po.exchange_rate
    # The commitment was posted NET of the PO discount, so its pro-rata reversal
    # must be struck on the discounted unit price too — otherwise a discounted PO
    # would over-reverse its commitment. discount_percent is 0 on legacy POs.
    disc_factor = (Decimal('100') - (po.discount_percent or ZERO)) / Decimal('100')
    total_received_value_orig = ZERO
    total_received_value_bwp  = ZERO
    for ln in grn.lines.select_related('po_line').all():
        line_value_orig = (
            ln.quantity_received * ln.po_line.unit_price * disc_factor
        ).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        line_value_bwp = _bwp(line_value_orig, rate)
        total_received_value_orig += line_value_orig
        total_received_value_bwp  += line_value_bwp

    if total_received_value_orig <= ZERO:
        return None

    a1990, a2199 = _commitment_accounts()
    je = JournalEntry.objects.create(
        entry_date    = grn.receipt_date,
        description   = f"Commitment reversal — GRN {grn.grn_number} — PO {po.po_number}",
        source_type   = 'grn',
        source_id     = grn.pk,
        journal_type  = JournalEntry.JournalType.COMMITMENT_REVERSAL,
        currency_code = po.currency_code,
        exchange_rate = rate,
        company       = po.company,
        is_related_party = po.supplier.is_related_party,
        created_by    = user,
        status        = JournalEntry.Status.DRAFT,
    )
    JournalEntryLine.objects.create(
        journal_entry  = je,
        account        = a2199,
        description    = f"Release commitment reserve for {grn.grn_number}",
        debit_amount   = total_received_value_orig,
        credit_amount  = ZERO,
        debit_bwp      = total_received_value_bwp,
        credit_bwp     = ZERO,
        contact        = po.supplier,
        is_related_party = po.supplier.is_related_party,
    )
    JournalEntryLine.objects.create(
        journal_entry  = je,
        account        = a1990,
        description    = f"Release commitment for {grn.grn_number}",
        debit_amount   = ZERO,
        credit_amount  = total_received_value_orig,
        debit_bwp      = ZERO,
        credit_bwp     = total_received_value_bwp,
        contact        = po.supplier,
        is_related_party = po.supplier.is_related_party,
    )
    je.post(user=user, _allow_direct=True)
    return je


def _reverse_remaining_commitment(
    po: PurchaseOrder, user: User, reason: str,
) -> Optional[JournalEntry]:
    """Reverse any un-received commitment when a PO is cancelled.
    Returns None if no commitment was ever posted (legacy PO)."""
    if po.commitment_journal_entry_id is None:
        return None

    rate = po.exchange_rate
    received_value_orig = ZERO
    for ln in po.lines.all():
        if ln.quantity_received and ln.unit_price:
            received_value_orig += (ln.quantity_received * ln.unit_price).quantize(
                TWO_PLACES, rounding=ROUND_HALF_UP
            )
    remaining_orig = (po.total_amount - received_value_orig).quantize(
        TWO_PLACES, rounding=ROUND_HALF_UP
    )
    if remaining_orig <= ZERO:
        return None
    remaining_bwp = _bwp(remaining_orig, rate)

    a1990, a2199 = _commitment_accounts()
    je = JournalEntry.objects.create(
        entry_date    = timezone.localdate(),
        description   = f"Commitment cancellation — PO {po.po_number}: {reason.strip()[:200]}",
        source_type   = 'purchase_order',
        source_id     = po.pk,
        journal_type  = JournalEntry.JournalType.COMMITMENT_REVERSAL,
        currency_code = po.currency_code,
        exchange_rate = rate,
        company       = po.company,
        is_related_party = po.supplier.is_related_party,
        created_by    = user,
        status        = JournalEntry.Status.DRAFT,
    )
    JournalEntryLine.objects.create(
        journal_entry = je, account = a2199,
        description   = f"Release remaining commitment reserve for {po.po_number}",
        debit_amount  = remaining_orig, credit_amount = ZERO,
        debit_bwp     = remaining_bwp,  credit_bwp    = ZERO,
        contact       = po.supplier, is_related_party = po.supplier.is_related_party,
    )
    JournalEntryLine.objects.create(
        journal_entry = je, account = a1990,
        description   = f"Release remaining commitment for {po.po_number}",
        debit_amount  = ZERO, credit_amount = remaining_orig,
        debit_bwp     = ZERO, credit_bwp    = remaining_bwp,
        contact       = po.supplier, is_related_party = po.supplier.is_related_party,
    )
    je.post(user=user, _allow_direct=True)
    return je
