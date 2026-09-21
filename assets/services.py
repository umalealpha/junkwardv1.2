"""
assets/services.py

Business logic for the Fixed Assets module:

  - depreciate_asset(asset, period, user)        post one month of depreciation
  - run_monthly_depreciation(period, user, ...)  bulk run for all active assets
  - dispose_asset(asset, ..., user)              post disposal entry
  - parse_odoo_csv(file_obj)                     parse Odoo Asset export to rows
  - import_assets(batch, user)                   commit a previewed import batch

The depreciation and disposal helpers are wrapped in transaction.atomic so a
failure mid-flow rolls back BOTH the asset/disposal record and the journal
entry.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable, Optional

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import Company
from ledger.models import Account, FiscalPeriod, JournalEntry, JournalEntryLine

from .component_models import walk_total_monthly_depreciation
from .models import (
    Asset,
    AssetAssignment,
    AssetCategory,
    AssetDisposal,
    AssetImportBatch,
    DepreciationEntry,
)


ZERO = Decimal('0.00')


@transaction.atomic
def transfer_asset_custodian(asset, *, to_employee, to_location, reason, user, transferred_at=None):
    """Hand an asset over to a new custodian (person/department) and/or location.

    NON-GL — custodianship + physical location only; money, depreciation and the
    legal owning entity are untouched (entity→entity moves use the disposal/
    transfer flow). Snapshots the current holder, updates the asset, and writes an
    append-only AssetAssignment history row. Returns the new AssetAssignment.
    CFO 2026-06-26.
    """
    if to_employee is None and not (to_location or '').strip():
        raise ValidationError('Choose a new holder and/or a new location to transfer to.')
    # Asset Control gate (CFO 2026-09-02, "close both side doors"): a change of
    # HOLDER must go through the controlled path — a requisition approved by the
    # Finance Manager and the CFO, then a signed handover — never this quick
    # button. A location-only move (no new holder) is still allowed, and a
    # superuser/admin keeps a data-correction escape hatch. This closes the old
    # gap where IT could hand any asset to anyone with no approval or signed note.
    is_holder_change = (
        to_employee is not None
        and getattr(to_employee, 'id', None) != asset.custodian_employee_id
    )
    if is_holder_change and not getattr(user, 'is_superuser', False):
        raise ValidationError(
            'Handing an asset to a new person must go through an asset requisition '
            '(approved by the Finance Manager and the CFO) and a signed handover — '
            'it cannot be moved with the quick transfer. You can still change only '
            'the location here.'
        )
    when = transferred_at or timezone.localdate()
    from_emp = asset.custodian_employee
    from_cust = asset.custodian or ''
    from_loc = asset.location or ''
    to_cust = (to_employee.full_name if to_employee else from_cust)

    asset.custodian_employee = to_employee if to_employee is not None else asset.custodian_employee
    if to_employee is not None:
        asset.custodian = to_employee.full_name
    if (to_location or '').strip():
        asset.location = to_location.strip()
    asset.save(update_fields=['custodian_employee', 'custodian', 'location', 'updated_at'])

    return AssetAssignment.objects.create(
        asset=asset,
        from_employee=from_emp,
        to_employee=to_employee,
        from_custodian=from_cust,
        to_custodian=to_cust,
        from_location=from_loc,
        to_location=asset.location or '',
        reason=(reason or '').strip()[:300],
        transferred_at=when,
        transferred_by=user,
    )


# ===========================================================================
#  Depreciation
# ===========================================================================

@transaction.atomic
def depreciate_asset(
    asset: Asset,
    period: FiscalPeriod,
    user: User,
    *,
    skip_if_already_posted: bool = True,
) -> Optional[DepreciationEntry]:
    """
    Post one month of depreciation for *asset* in *period*.

    Returns the DepreciationEntry, or None if the asset is fully depreciated
    or already has an entry for this period.

    Journal entry posted (for one asset, BWP):
        DR  6600 Depreciation expense        amount
        CR  145x Accumulated depreciation    amount
    """
    # Idempotency
    if skip_if_already_posted and DepreciationEntry.objects.filter(
        asset=asset, period=period
    ).exists():
        return None

    # Don't depreciate periods at or before the migration cutover
    if asset.last_depreciation_date and period.end_date <= asset.last_depreciation_date:
        return None

    # Don't depreciate before the asset is in service
    if asset.in_service_date and period.end_date < asset.in_service_date:
        return None

    if asset.status != Asset.Status.ACTIVE:
        return None

    # Componentisation (IFRS — assets/component_models.py):
    # For a parent composite asset, monthly depreciation is the sum of
    # the parent's own charge AND every active component child's charge.
    # walk_total_monthly_depreciation() collapses to plain
    # `asset.monthly_depreciation_amount()` when there are no children,
    # so behaviour is identical for the standalone-asset case.
    amount = walk_total_monthly_depreciation(asset)
    if amount <= ZERO:
        return None

    cat = asset.category
    je = JournalEntry.objects.create(
        entry_date=period.end_date,
        description=f"Monthly depreciation — {asset.tag_number} ({period.period_name})",
        source_type='depreciation',
        source_id=asset.pk,
        journal_type=JournalEntry.JournalType.GENERAL,
        currency_code_id='BWP',
        exchange_rate=Decimal('1.00000000'),
        is_related_party=False,
        created_by=user,
        status=JournalEntry.Status.DRAFT,
    )
    JournalEntryLine.objects.create(
        journal_entry=je,
        account=cat.depreciation_expense_account,
        description=f"Depreciation {asset.tag_number}",
        debit_amount=amount, credit_amount=ZERO,
        debit_bwp=amount,    credit_bwp=ZERO,
    )
    JournalEntryLine.objects.create(
        journal_entry=je,
        account=cat.accum_depr_account,
        description=f"Accum. depr. {asset.tag_number}",
        debit_amount=ZERO, credit_amount=amount,
        debit_bwp=ZERO,    credit_bwp=amount,
    )
    # Post directly — depreciation is system-generated and bypasses approval
    je.post(user=user, _allow_direct=True)

    entry = DepreciationEntry.objects.create(
        asset=asset,
        period=period,
        period_end_date=period.end_date,
        amount=amount,
        journal_entry=je,
        posted_by=user,
    )

    # Roll the asset's high-water mark forward
    asset.last_depreciation_date = period.end_date
    asset.save(audit_user=user, audit_description=f"Depreciation {period.period_name}")

    return entry


@dataclass
class DepreciationRunResult:
    period: str
    assets_considered: int = 0
    assets_depreciated: int = 0
    assets_skipped: int = 0
    assets_fully_depreciated: int = 0
    total_amount: Decimal = ZERO
    errors: list = field(default_factory=list)


@transaction.atomic
def run_monthly_depreciation(
    period: FiscalPeriod,
    user: User,
    *,
    company: Optional[Company] = None,
    dry_run: bool = False,
) -> DepreciationRunResult:
    """Loop every active asset and post depreciation for *period*."""
    result = DepreciationRunResult(period=period.period_name)

    qs = Asset.objects.filter(status=Asset.Status.ACTIVE)
    if company:
        qs = qs.filter(company=company)

    # Componentisation — depreciate parents only at the top level. Child
    # components are rolled into their parent's depreciate_asset() call
    # via walk_total_monthly_depreciation. Standalone assets (no parent,
    # no children) are unaffected — they still iterate as before.
    qs = qs.filter(parent_asset__isnull=True)

    sid = transaction.savepoint() if dry_run else None

    for asset in qs.select_related('category'):
        result.assets_considered += 1
        try:
            if asset.is_fully_depreciated:
                result.assets_fully_depreciated += 1
                continue
            entry = depreciate_asset(asset, period, user)
            if entry is None:
                result.assets_skipped += 1
            else:
                result.assets_depreciated += 1
                result.total_amount += entry.amount
        except Exception as exc:  # noqa: BLE001
            result.errors.append({'tag_number': asset.tag_number, 'error': str(exc)})

    if dry_run and sid is not None:
        transaction.savepoint_rollback(sid)

    return result


# ===========================================================================
#  Disposal
# ===========================================================================

@transaction.atomic
def request_disposal(
    asset: Asset,
    *,
    disposal_type: str,
    disposal_date: date,
    proceeds: Decimal,
    bank_account: Optional[Account],
    user: User,
    notes: str = '',
) -> AssetDisposal:
    """
    Stage a disposal as PENDING_APPROVAL — no GL impact yet.

    The GL is hit only when an approver (different person, with approver
    title) calls approve_disposal(). Segregation of duties is enforced.
    """
    from django.utils import timezone

    if asset.status != Asset.Status.ACTIVE:
        raise ValidationError(f"Asset {asset.tag_number} is not active.")
    if disposal_type == AssetDisposal.DisposalType.SALE and proceeds > ZERO and not bank_account:
        raise ValidationError("A bank account is required when recording sale proceeds.")

    cost = asset.cost or ZERO
    accum = asset.accumulated_depreciation
    nbv = asset.net_book_value
    gain_loss = (proceeds or ZERO) - nbv

    disposal = AssetDisposal.objects.create(
        asset=asset,
        disposal_type=disposal_type,
        disposal_date=disposal_date,
        proceeds=proceeds or ZERO,
        bank_account=bank_account,
        cost_at_disposal=cost,
        accumulated_depr_at_disposal=accum,
        nbv_at_disposal=nbv,
        gain_loss=gain_loss,
        journal_entry=None,
        notes=notes,
        posted_by=user,            # legacy field — set to requester for back-compat
        requested_by=user,
        requested_at=timezone.now(),
        status=AssetDisposal.Status.PENDING_APPROVAL,
    )
    disposal.save(audit_user=user, audit_description=f'Disposal requested for {asset.tag_number}')
    return disposal


@transaction.atomic
def approve_disposal(disposal: AssetDisposal, user: User) -> AssetDisposal:
    """
    Approve a pending disposal:
      - SoD: approver title required, approver != requester
      - Posts the GL journal in the same transaction
      - Marks asset as disposed
    """
    from core.models import get_user_profile
    from django.utils import timezone

    if disposal.status != AssetDisposal.Status.PENDING_APPROVAL:
        raise ValidationError(f"Disposal is {disposal.status} — cannot approve.")

    profile = get_user_profile(user)
    is_su = bool(getattr(user, 'is_superuser', False))
    if not is_su and (profile is None or not profile.can_approve_journal_entries):
        raise ValidationError(
            "Approval requires CFO, Finance Manager, or Financial Controller title."
        )
    if disposal.requested_by_id and disposal.requested_by_id == getattr(user, 'id', None):
        raise ValidationError("Segregation of duties: cannot approve your own disposal request.")

    # FA-001 (CFO directive 2026-05-29) — intercompany branch.
    # If the disposal is a TRANSFER with a recipient_company set AND the
    # recipient differs from the sender's company, route through the
    # intercompany flow: sender JE + receiver Asset row + receiver JE.
    if (disposal.disposal_type == AssetDisposal.DisposalType.TRANSFER
            and disposal.recipient_company_id
            and disposal.asset.company_id != disposal.recipient_company_id):
        return _approve_intercompany_transfer(disposal, user)

    asset    = disposal.asset
    cost     = disposal.cost_at_disposal or ZERO
    accum    = disposal.accumulated_depr_at_disposal or ZERO
    proceeds = disposal.proceeds or ZERO
    gain_loss = disposal.gain_loss or ZERO
    bank_account = disposal.bank_account

    period = FiscalPeriod.get_open_period_for_date(disposal.disposal_date)
    if period is None:
        raise ValidationError(f"No open fiscal period covers {disposal.disposal_date}.")

    # A disposal with sale proceeds must name the bank/cash account that
    # received them — otherwise the proceeds line is dropped and the entry
    # cannot balance. Fail with a clear message instead of an opaque
    # "entry not balanced" further down.
    if proceeds > ZERO and bank_account is None:
        raise ValidationError(
            "This disposal has sale proceeds but no bank/cash account set. "
            "Select the account that received the proceeds before posting."
        )

    cat = asset.category

    # Discover gain/loss accounts by code (sensible Botswana CoA defaults)
    def _try(code):
        return Account.objects.filter(code=code, is_active=True).first()

    gain_acct = _try('4900') or _try('7900')   # Other income / gain on disposal
    loss_acct = _try('6900') or _try('7910')   # Other expense / loss on disposal

    je = JournalEntry.objects.create(
        entry_date=disposal.disposal_date,
        description=f"Disposal of asset {asset.tag_number}",
        source_type='asset_disposal',
        source_id=asset.pk,
        journal_type=JournalEntry.JournalType.GENERAL,
        currency_code_id='BWP',
        exchange_rate=Decimal('1.00000000'),
        is_related_party=False,
        created_by=user,
        status=JournalEntry.Status.DRAFT,
    )

    if proceeds > ZERO and bank_account is not None:
        JournalEntryLine.objects.create(
            journal_entry=je, account=bank_account,
            description=f"Proceeds from disposal {asset.tag_number}",
            debit_amount=proceeds, credit_amount=ZERO,
            debit_bwp=proceeds,    credit_bwp=ZERO,
        )

    if accum > ZERO:
        JournalEntryLine.objects.create(
            journal_entry=je, account=cat.accum_depr_account,
            description=f"Clear accumulated depreciation {asset.tag_number}",
            debit_amount=accum, credit_amount=ZERO,
            debit_bwp=accum,    credit_bwp=ZERO,
        )

    JournalEntryLine.objects.create(
        journal_entry=je, account=cat.cost_account,
        description=f"Clear asset cost {asset.tag_number}",
        debit_amount=ZERO, credit_amount=cost,
        debit_bwp=ZERO,    credit_bwp=cost,
    )

    if gain_loss > ZERO and gain_acct is not None:
        JournalEntryLine.objects.create(
            journal_entry=je, account=gain_acct,
            description=f"Gain on disposal {asset.tag_number}",
            debit_amount=ZERO, credit_amount=gain_loss,
            debit_bwp=ZERO,    credit_bwp=gain_loss,
        )
    elif gain_loss < ZERO and loss_acct is not None:
        JournalEntryLine.objects.create(
            journal_entry=je, account=loss_acct,
            description=f"Loss on disposal {asset.tag_number}",
            debit_amount=-gain_loss, credit_amount=ZERO,
            debit_bwp=-gain_loss,    credit_bwp=ZERO,
        )

    je.post(user=user, _allow_direct=True)

    disposal.journal_entry = je
    disposal.status        = AssetDisposal.Status.APPROVED
    disposal.approved_by   = user
    disposal.approved_at   = timezone.now()
    disposal.save(audit_user=user, audit_description=f'Disposal approved & posted')

    # Mark asset as disposed
    asset.status = {
        AssetDisposal.DisposalType.WRITE_OFF: Asset.Status.WRITTEN_OFF,
        AssetDisposal.DisposalType.TRANSFER:  Asset.Status.TRANSFERRED,
    }.get(disposal.disposal_type, Asset.Status.DISPOSED)
    asset.save(audit_user=user, audit_description=f"Disposed via {disposal.disposal_type}")

    return disposal


@transaction.atomic
def reject_disposal(disposal: AssetDisposal, user: User, reason: str) -> AssetDisposal:
    """Reject a pending disposal with a mandatory reason."""
    from core.models import get_user_profile
    from django.utils import timezone

    if disposal.status != AssetDisposal.Status.PENDING_APPROVAL:
        raise ValidationError(f"Disposal is {disposal.status} — cannot reject.")
    profile = get_user_profile(user)
    is_su = bool(getattr(user, 'is_superuser', False))
    if not is_su and (profile is None or not profile.can_approve_journal_entries):
        raise ValidationError("Approver title required.")
    if not reason or not str(reason).strip():
        raise ValidationError("Rejection reason is required.")

    disposal.status           = AssetDisposal.Status.REJECTED
    disposal.rejected_by      = user
    disposal.rejected_at      = timezone.now()
    disposal.rejection_reason = str(reason).strip()
    disposal.save(audit_user=user, audit_description=f'Disposal rejected: {reason}')
    return disposal


# Backwards-compat alias — older callers passed dispose_asset() expecting it
# to post the GL immediately. Now it just requests; an approver must approve.
dispose_asset = request_disposal


# ===========================================================================
#  FA-001 — Intercompany asset transfer (CFO directive 2026-05-29)
# ===========================================================================

@transaction.atomic
def _approve_intercompany_transfer(disposal: AssetDisposal, user: User) -> AssetDisposal:
    """Post the sender JE + create the receiver-side Asset row + post the
    receiver JE for an intercompany asset transfer.

    Sender JE (sender's books):
        DR Accumulated Depreciation       (clears the asset's accum depr)
        DR Intercompany Receivable        (transfer_value)
        CR Asset Cost                     (clears the asset's cost)
        DR Loss / CR Gain                 (balancing if transfer_value ≠ NBV)

    Receiver JE (recipient's books):
        DR Asset Cost                     (transfer_value)
        CR Intercompany Payable           (transfer_value)

    Default transfer_value = sender-side NBV (no gain/loss in either company —
    the standard IFRS-compliant intra-group treatment that consolidates out
    to zero in group eliminations). CFO can override by setting
    disposal.transfer_value before approval.

    Both JEs are marked is_related_party=True (every ADIC↔subsidiary pair is
    a related party). A new Asset row is created in recipient_company with
    cost = transfer_value, opening_accumulated_depreciation = 0, and
    transferred_from_asset back-pointer for audit.
    """
    from core.models import IntercompanyAccountPolicy
    from django.utils import timezone

    policy = IntercompanyAccountPolicy.current()
    if not policy.is_configured:
        raise ValidationError(
            "Intercompany account codes not configured. Set "
            "receivable_account_code + payable_account_code on "
            "/admin/core/intercompanyaccountpolicy/ first."
        )

    sender_asset = disposal.asset
    recipient    = disposal.recipient_company
    cost  = disposal.cost_at_disposal or ZERO
    accum = disposal.accumulated_depr_at_disposal or ZERO
    nbv   = disposal.nbv_at_disposal or (cost - accum)
    transfer_value = disposal.transfer_value if disposal.transfer_value is not None else nbv
    gain_loss = transfer_value - nbv      # + = gain (transfer > NBV), - = loss

    period = FiscalPeriod.get_open_period_for_date(disposal.disposal_date)
    if period is None:
        raise ValidationError(
            f"No open fiscal period covers {disposal.disposal_date}."
        )

    cat = sender_asset.category
    ic_recv_acct = Account.objects.filter(code=policy.receivable_account_code, is_active=True).first()
    ic_pay_acct  = Account.objects.filter(code=policy.payable_account_code,    is_active=True).first()
    if ic_recv_acct is None:
        raise ValidationError(
            f"Intercompany receivable account {policy.receivable_account_code!r} "
            f"not found in active CoA."
        )
    if ic_pay_acct is None:
        raise ValidationError(
            f"Intercompany payable account {policy.payable_account_code!r} "
            f"not found in active CoA."
        )

    def _gain():  return Account.objects.filter(code='4900', is_active=True).first() \
                       or Account.objects.filter(code='7900', is_active=True).first()
    def _loss():  return Account.objects.filter(code='6900', is_active=True).first() \
                       or Account.objects.filter(code='7910', is_active=True).first()

    # ─── Sender JE (sender's books) ──────────────────────────────────────────
    sender_je = JournalEntry.objects.create(
        entry_date=disposal.disposal_date,
        description=(
            f"Intercompany transfer of asset {sender_asset.tag_number} "
            f"→ {recipient.code}"
        ),
        source_type='asset_disposal',
        source_id=sender_asset.pk,
        journal_type=JournalEntry.JournalType.GENERAL,
        currency_code_id='BWP',
        exchange_rate=Decimal('1.00000000'),
        is_related_party=True,
        company=sender_asset.company,
        created_by=user,
        status=JournalEntry.Status.DRAFT,
    )
    if accum > ZERO:
        JournalEntryLine.objects.create(
            journal_entry=sender_je, account=cat.accum_depr_account,
            description=f"Clear accum depr {sender_asset.tag_number}",
            debit_amount=accum, credit_amount=ZERO,
            debit_bwp=accum,    credit_bwp=ZERO,
        )
    JournalEntryLine.objects.create(
        journal_entry=sender_je, account=ic_recv_acct,
        description=f"Due from {recipient.code} — transfer {sender_asset.tag_number}",
        debit_amount=transfer_value, credit_amount=ZERO,
        debit_bwp=transfer_value,    credit_bwp=ZERO,
    )
    JournalEntryLine.objects.create(
        journal_entry=sender_je, account=cat.cost_account,
        description=f"Clear asset cost {sender_asset.tag_number}",
        debit_amount=ZERO, credit_amount=cost,
        debit_bwp=ZERO,    credit_bwp=cost,
    )
    if gain_loss > ZERO:
        g = _gain()
        if g is None:
            raise ValidationError("Gain account (4900 / 7900) missing — run setup_chart_of_accounts.")
        JournalEntryLine.objects.create(
            journal_entry=sender_je, account=g,
            description=f"Gain on intercompany transfer {sender_asset.tag_number}",
            debit_amount=ZERO, credit_amount=gain_loss,
            debit_bwp=ZERO,    credit_bwp=gain_loss,
        )
    elif gain_loss < ZERO:
        l = _loss()
        if l is None:
            raise ValidationError("Loss account (6900 / 7910) missing — run setup_chart_of_accounts.")
        JournalEntryLine.objects.create(
            journal_entry=sender_je, account=l,
            description=f"Loss on intercompany transfer {sender_asset.tag_number}",
            debit_amount=-gain_loss, credit_amount=ZERO,
            debit_bwp=-gain_loss,    credit_bwp=ZERO,
        )
    sender_je.post(user=user, _allow_direct=True)

    # ─── Receiver-side new Asset row ─────────────────────────────────────────
    receiver_asset = Asset.objects.create(
        tag_number=f"{sender_asset.tag_number}-IC-{recipient.code}",
        external_ref=sender_asset.external_ref,
        name=sender_asset.name,
        description=sender_asset.description,
        serial_number=sender_asset.serial_number,
        barcode=sender_asset.barcode,
        company=recipient,
        category=sender_asset.category,
        cost=transfer_value,
        salvage_value=sender_asset.salvage_value,
        vat_treatment=sender_asset.vat_treatment,
        purchase_vat_amount=ZERO,
        capital_allowance_method=sender_asset.capital_allowance_method,
        capital_allowance_rate=sender_asset.capital_allowance_rate,
        opening_accumulated_depreciation=ZERO,
        useful_life_months=sender_asset.useful_life_months,
        in_service_date=disposal.disposal_date,
        status=Asset.Status.ACTIVE,
        transferred_from_asset=sender_asset,
    )

    # ─── Receiver JE (recipient's books) ─────────────────────────────────────
    receiver_je = JournalEntry.objects.create(
        entry_date=disposal.disposal_date,
        description=(
            f"Intercompany transfer of asset {sender_asset.tag_number} "
            f"← {sender_asset.company.code}"
        ),
        source_type='asset_disposal',
        source_id=receiver_asset.pk,
        journal_type=JournalEntry.JournalType.GENERAL,
        currency_code_id='BWP',
        exchange_rate=Decimal('1.00000000'),
        is_related_party=True,
        company=recipient,
        created_by=user,
        status=JournalEntry.Status.DRAFT,
    )
    JournalEntryLine.objects.create(
        journal_entry=receiver_je, account=cat.cost_account,
        description=f"Asset cost on intercompany receipt {receiver_asset.tag_number}",
        debit_amount=transfer_value, credit_amount=ZERO,
        debit_bwp=transfer_value,    credit_bwp=ZERO,
    )
    JournalEntryLine.objects.create(
        journal_entry=receiver_je, account=ic_pay_acct,
        description=f"Due to {sender_asset.company.code} — transfer {sender_asset.tag_number}",
        debit_amount=ZERO, credit_amount=transfer_value,
        debit_bwp=ZERO,    credit_bwp=transfer_value,
    )
    receiver_je.post(user=user, _allow_direct=True)

    # ─── Close disposal + flip sender asset ──────────────────────────────────
    disposal.journal_entry = sender_je
    disposal.gain_loss     = gain_loss
    disposal.status        = AssetDisposal.Status.APPROVED
    disposal.approved_by   = user
    disposal.approved_at   = timezone.now()
    disposal.save(
        audit_user=user,
        audit_description=(
            f"Intercompany transfer approved & posted "
            f"{sender_asset.company.code} → {recipient.code} @ {transfer_value}"
        ),
    )

    sender_asset.status = Asset.Status.TRANSFERRED
    sender_asset.save(audit_user=user,
                      audit_description=f"Transferred to {recipient.code}")

    return disposal


# ===========================================================================
#  Odoo CSV importer
# ===========================================================================

# Odoo's Asset model exports columns that vary slightly by version.  The
# importer accepts any of the common header labels and maps them to our
# canonical fields.

ODOO_COLUMN_ALIASES = {
    'tag_number': [
        'reference', 'code', 'asset code', 'asset_code', 'asset reference',
        'tag', 'tag number', 'tag_number', 'name',
    ],
    'external_ref': ['id', 'external id', 'external_id', 'odoo id', 'odoo_id'],
    'name': ['name', 'asset name', 'asset_name', 'description', 'display_name'],
    'category_code': [
        'asset type', 'category', 'asset category', 'category code',
        'asset_type', 'category_id', 'category/code', 'category_id/name',
    ],
    'cost': [
        'gross value', 'gross_value', 'original value', 'original_value',
        'acquisition cost', 'acquisition_cost', 'cost', 'value',
    ],
    'salvage_value': ['salvage value', 'salvage_value', 'residual', 'residual_value'],
    'useful_life_months': [
        'method number', 'method_number', 'duration', 'periods',
        'useful life (months)', 'useful_life_months', 'months',
    ],
    'useful_life_years': [
        'useful life', 'useful life (years)', 'useful_life', 'method_period_year', 'years',
    ],
    'method': ['method', 'depreciation method', 'depreciation_method'],
    'purchase_date': [
        'purchase date', 'purchase_date', 'acquisition date', 'acquisition_date',
        'date', 'asset start date',
    ],
    'in_service_date': [
        'first depreciation date', 'first_depreciation_date',
        'in service date', 'in_service_date', 'service start',
    ],
    'opening_accumulated_depreciation': [
        'accumulated depreciation', 'accumulated_depreciation',
        'depreciated value', 'depreciated_value', 'value depreciated',
    ],
    'location': ['location', 'site', 'asset location'],
    'custodian': ['custodian', 'employee', 'responsible', 'owner'],
    'serial_number': ['serial', 'serial number', 'serial_number'],
    'barcode': ['barcode', 'bar code'],
    'notes': ['note', 'notes', 'description (long)', 'comment'],
}


def _normalise_header(h: str) -> str:
    return (h or '').strip().lower().replace('  ', ' ')


def _build_header_map(headers: list[str]) -> dict[str, int]:
    """Map our canonical field names to column indices using ODOO_COLUMN_ALIASES."""
    norm = [_normalise_header(h) for h in headers]
    out: dict[str, int] = {}
    for canonical, aliases in ODOO_COLUMN_ALIASES.items():
        for alias in aliases:
            a = _normalise_header(alias)
            for i, h in enumerate(norm):
                if h == a and canonical not in out:
                    out[canonical] = i
                    break
            if canonical in out:
                break
    return out


def _to_decimal(value) -> Optional[Decimal]:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    # Strip thousands separators and currency symbols
    for ch in [',', ' ', 'P', 'BWP', 'R', '$']:
        s = s.replace(ch, '')
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None


def _to_date(value) -> Optional[date]:
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%m/%d/%Y', '%Y/%m/%d', '%d.%m.%Y'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _looks_like_xlsx(raw: bytes) -> bool:
    """XLSX files are zip archives — they start with PK\\x03\\x04."""
    return isinstance(raw, (bytes, bytearray)) and len(raw) >= 4 and bytes(raw[:4]) == b'PK\x03\x04'


def _rows_from_xlsx(raw: bytes) -> tuple[list[str], list[list]]:
    """Return (headers, data_rows) from the first sheet of an XLSX byte stream."""
    from openpyxl import load_workbook
    from io import BytesIO

    wb = load_workbook(filename=BytesIO(raw), read_only=True, data_only=True)
    ws = wb.active
    if ws is None:
        return [], []
    iterator = ws.iter_rows(values_only=True)
    try:
        header_row = next(iterator)
    except StopIteration:
        return [], []
    headers = ['' if c is None else str(c) for c in header_row]
    data_rows = []
    for r in iterator:
        # Stop at the first fully blank row to avoid trailing-blank tails
        if all(c is None or (isinstance(c, str) and not c.strip()) for c in r):
            continue
        data_rows.append(['' if c is None else (c if isinstance(c, (int, float)) else str(c)) for c in r])
    return headers, data_rows


def parse_odoo_file(file_obj, *, file_name: str = '') -> tuple[list[dict], list[dict]]:
    """
    Parse an Odoo asset export — auto-detects CSV or XLSX.

    Returns (rows, errors) where rows is a list of canonicalised dicts ready
    for validation. errors collects file-level and per-row parse problems.
    """
    rows: list[dict] = []
    errors: list[dict] = []

    raw = file_obj.read() if hasattr(file_obj, 'read') else file_obj
    if not isinstance(raw, (bytes, bytearray)) and isinstance(raw, str):
        raw_bytes = raw.encode('utf-8')
    else:
        raw_bytes = bytes(raw) if isinstance(raw, (bytes, bytearray)) else b''

    is_xlsx = _looks_like_xlsx(raw_bytes) or (file_name or '').lower().endswith(('.xlsx', '.xlsm'))

    if is_xlsx:
        try:
            headers, data_rows = _rows_from_xlsx(raw_bytes)
        except Exception as exc:  # noqa: BLE001
            errors.append({'row_index': -1, 'field': 'file', 'message': f'XLSX parse failed: {exc}'})
            return rows, errors
        return _canonicalise_rows(headers, data_rows, errors)

    # CSV path
    if isinstance(raw_bytes, bytes):
        for enc in ('utf-8-sig', 'utf-8', 'cp1252', 'latin-1'):
            try:
                text = raw_bytes.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            errors.append({'row_index': -1, 'field': 'file', 'message': 'Unable to decode file.'})
            return rows, errors
    else:
        text = str(raw_bytes)

    sample = text[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=',;\t|')
    except csv.Error:
        dialect = csv.excel

    reader = csv.reader(io.StringIO(text), dialect=dialect)
    try:
        headers = next(reader)
    except StopIteration:
        errors.append({'row_index': -1, 'field': 'file', 'message': 'File is empty.'})
        return rows, errors
    data_rows = list(reader)
    return _canonicalise_rows(headers, data_rows, errors)


# Backwards-compat alias — older callers may still use parse_odoo_csv.
def parse_odoo_csv(file_obj) -> tuple[list[dict], list[dict]]:
    return parse_odoo_file(file_obj)


def _canonicalise_rows(headers: list, data_rows: list, errors: list[dict]) -> tuple[list[dict], list[dict]]:
    """Shared logic that maps a generic (headers, data_rows) tuple into our schema."""
    rows: list[dict] = []

    header_map = _build_header_map(headers)

    if 'cost' not in header_map and 'name' not in header_map:
        errors.append({
            'row_index': -1, 'field': 'headers',
            'message': f'Could not find required columns. Headers seen: {headers}',
        })
        return rows, errors

    for idx, raw_row in enumerate(data_rows, start=1):
        if not any(c is not None and (str(c) if not isinstance(c, str) else c).strip() for c in raw_row):
            continue  # skip blank lines

        def get(field_name):
            i = header_map.get(field_name)
            if i is None or i >= len(raw_row):
                return None
            v = raw_row[i]
            if v is None:
                return None
            return v.strip() if isinstance(v, str) else v

        useful_months = None
        if 'useful_life_months' in header_map:
            v = _to_decimal(get('useful_life_months'))
            useful_months = int(v) if v is not None else None
        if useful_months is None and 'useful_life_years' in header_map:
            v = _to_decimal(get('useful_life_years'))
            useful_months = int(v) * 12 if v is not None else None

        method_raw = (get('method') or '').lower()
        if 'reduc' in method_raw or 'declin' in method_raw or 'degress' in method_raw:
            method = 'reducing_balance'
        else:
            method = 'straight_line'

        rows.append({
            'row_index': idx,
            'tag_number': get('tag_number') or get('external_ref') or f'IMPORT-{idx:04d}',
            'external_ref': get('external_ref') or '',
            'name': get('name') or '',
            'category_code': get('category_code') or '',
            'cost': str(_to_decimal(get('cost')) or ''),
            'salvage_value': str(_to_decimal(get('salvage_value')) or '0'),
            'useful_life_months': useful_months,
            'method': method,
            'purchase_date': _to_date(get('purchase_date')).isoformat() if _to_date(get('purchase_date')) else None,
            'in_service_date': _to_date(get('in_service_date')).isoformat() if _to_date(get('in_service_date')) else None,
            'opening_accumulated_depreciation': str(_to_decimal(get('opening_accumulated_depreciation')) or '0'),
            'location': get('location') or '',
            'custodian': get('custodian') or '',
            'serial_number': get('serial_number') or '',
            'barcode': get('barcode') or '',
            'notes': get('notes') or '',
        })

    return rows, errors


def validate_parsed_rows(rows: list[dict]) -> list[dict]:
    """Run cheap business validation across previewed rows. Returns error list."""
    errors: list[dict] = []
    seen_tags: set[str] = set()
    existing_tags = set(Asset.objects.values_list('tag_number', flat=True))
    valid_categories = {c.code: c for c in AssetCategory.objects.filter(is_active=True)}

    for row in rows:
        idx = row['row_index']
        tag = (row.get('tag_number') or '').strip()
        if not tag:
            errors.append({'row_index': idx, 'field': 'tag_number', 'message': 'Missing tag.'})
        else:
            if tag in seen_tags:
                errors.append({'row_index': idx, 'field': 'tag_number',
                               'message': f'Duplicate tag in file: {tag}'})
            if tag in existing_tags:
                errors.append({'row_index': idx, 'field': 'tag_number',
                               'message': f'Tag already exists in register: {tag}'})
            seen_tags.add(tag)

        cost = _to_decimal(row.get('cost'))
        if cost is None or cost <= ZERO:
            errors.append({'row_index': idx, 'field': 'cost', 'message': 'Cost must be > 0.'})

        if not row.get('name'):
            errors.append({'row_index': idx, 'field': 'name', 'message': 'Missing name.'})

        if not row.get('useful_life_months'):
            errors.append({'row_index': idx, 'field': 'useful_life_months',
                           'message': 'Missing useful life.'})

        if not row.get('purchase_date'):
            errors.append({'row_index': idx, 'field': 'purchase_date',
                           'message': 'Missing purchase date.'})

        cat_code = (row.get('category_code') or '').strip()
        if cat_code and cat_code not in valid_categories:
            errors.append({'row_index': idx, 'field': 'category_code',
                           'message': f'Unknown category code: {cat_code}'})

    return errors


@transaction.atomic
def commit_import(batch: AssetImportBatch, user: User) -> tuple[int, list[dict]]:
    """
    Create Asset rows from a previewed and validated batch.

    Returns (created_count, errors).  Aborts the whole transaction if any row
    fails.
    """
    if batch.status != AssetImportBatch.Status.DRAFT:
        raise ValidationError("Only draft import batches can be committed.")

    valid_categories = {c.code: c for c in AssetCategory.objects.filter(is_active=True)}
    fallback_category = AssetCategory.objects.filter(is_active=True).first()
    if fallback_category is None:
        raise ValidationError("No active asset categories. Run setup_asset_categories first.")

    created = 0
    errors: list[dict] = []

    for row in batch.parsed_rows:
        idx = row.get('row_index')
        try:
            cat_code = (row.get('category_code') or '').strip()
            category = valid_categories.get(cat_code, fallback_category)

            cost = Decimal(row.get('cost') or '0')
            salvage = Decimal(row.get('salvage_value') or '0')
            opening = Decimal(row.get('opening_accumulated_depreciation') or '0')
            useful_months = int(row.get('useful_life_months') or category.default_useful_life_months)

            purchase = date.fromisoformat(row['purchase_date'])
            in_service = date.fromisoformat(row['in_service_date']) if row.get('in_service_date') else purchase

            asset = Asset(
                tag_number=row['tag_number'],
                external_ref=row.get('external_ref') or '',
                name=row.get('name') or row['tag_number'],
                description=row.get('notes') or '',
                serial_number=row.get('serial_number') or '',
                barcode=row.get('barcode') or '',
                company=batch.company,
                category=category,
                cost=cost,
                salvage_value=salvage,
                method=row.get('method') or category.default_method,
                useful_life_months=useful_months,
                purchase_date=purchase,
                in_service_date=in_service,
                opening_accumulated_depreciation=opening,
                last_depreciation_date=batch.cutover_date,
                location=row.get('location') or '',
                custodian=row.get('custodian') or '',
                status=Asset.Status.ACTIVE,
                created_by=user,
            )
            asset.full_clean()
            asset.save(audit_user=user, audit_description=f"Imported from {batch.source}")
            created += 1
        except Exception as exc:  # noqa: BLE001
            errors.append({'row_index': idx, 'field': '__row__', 'message': str(exc)})

    if errors:
        # Roll back EVERYTHING — caller should review and fix
        raise ValidationError({'errors': errors})

    batch.rows_imported = created
    batch.status = AssetImportBatch.Status.COMMITTED
    batch.committed_at = timezone.now()
    batch.save(audit_user=user, audit_description=f"Committed {created} assets")

    return created, errors
