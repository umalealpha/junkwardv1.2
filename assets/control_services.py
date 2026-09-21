"""
assets/control_services.py

Business logic for the Asset Control & Handover module (CFO spec, 2026-09-02).

The controls that give the module teeth (spec §7) live here, NOT in the views —
so every entry point (API, admin, management command) is gated identically:

  - create_requisition       IT raises a request; recipient is a directory FK,
                             never typed. [Rule 2]
  - submit_requisition       DRAFT -> PENDING_FM_APPROVAL
  - fm_approve / cfo_approve  Sequential dual gate mirroring procurement POs.
                             Self-approval is blocked ABSOLUTELY (AC2), even for
                             below-threshold single sign-off.
  - reject_requisition        Either leg, with a mandatory reason.
  - create_handover          Only from an APPROVED requisition. [AC3]
  - handover_it_release / _finance_record / _employee_accept
                             The three signatures. The asset becomes IN_USE only
                             when all three are present. [AC5]
  - return_asset             A held asset drops into the Returned/Spare pool.
  - assets_blocking_offboarding
                             The gate HR/offboarding calls before finalising a
                             leaver. [AC6]

Reuse: approver eligibility resolves through core.models.get_user_profile ->
UserProfile.title (same source as the JE / procurement gates). Custody history
is the existing append-only assets.AssetAssignment.

Nothing here depends on the local AI box or any component that can silently
fail (spec §10): the whole path is pure Django + the database.
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.models import UserProfile, get_user_profile
from payroll.models import Employee

from .control_models import AssetControlPolicy, AssetHandover, AssetRequisition
from .models import Asset, AssetAssignment


# ---------------------------------------------------------------------------
# Approver eligibility — same title source as procurement + journal entries.
# ---------------------------------------------------------------------------

def can_approve_as_fm(user) -> bool:
    """FM leg: Finance Manager, Financial Controller, or CFO (or superuser)."""
    if user is not None and getattr(user, 'is_superuser', False):
        return True
    profile = get_user_profile(user)
    if profile is None or not profile.is_active:
        return False
    return profile.title in {
        UserProfile.Title.FINANCE_MANAGER,
        UserProfile.Title.FINANCIAL_CONTROLLER,
        UserProfile.Title.CFO,
    }


def can_approve_as_cfo(user) -> bool:
    """CFO leg: the CFO title only (or a Django superuser as backup)."""
    if user is not None and getattr(user, 'is_superuser', False):
        return True
    profile = get_user_profile(user)
    if profile is None or not profile.is_active:
        return False
    return profile.title == UserProfile.Title.CFO


def is_it_asset_officer(user) -> bool:
    """The named IT Asset Officers (policy allow-list) who may raise a
    requisition and sign the IT-release. Superusers / omni administrators are
    always included so the CFO can never be locked out. CFO 2026-09-02."""
    if user is None or not getattr(user, 'is_authenticated', True):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    profile = get_user_profile(user)
    if profile and getattr(profile, 'is_administrator', False):
        return True
    allowed = {e.strip().lower()
               for e in (AssetControlPolicy.current().it_officer_emails or [])
               if e and e.strip()}
    if not allowed:
        return False
    emails = {(getattr(user, 'email', '') or '').strip().lower()}
    emp = getattr(user, 'employee_record', None)
    if emp is not None and emp.email:
        emails.add(emp.email.strip().lower())
    return bool(emails & allowed)


def can_record_finance(user) -> bool:
    """Who may provide the 'Finance records' signature on a handover note."""
    if user is not None and getattr(user, 'is_superuser', False):
        return True
    profile = get_user_profile(user)
    return bool(profile and profile.is_active and (
        profile.can_view_financials or profile.can_approve_journal_entries
    ))


# ---------------------------------------------------------------------------
# Numbering — REQ-YYYY-NNNN / HO-YYYY-NNNN. Unique constraint + short retry.
# ---------------------------------------------------------------------------

def _next_number(prefix: str, model, field: str) -> str:
    year = timezone.now().year
    stem = f'{prefix}-{year}-'
    last = (model.objects.filter(**{f'{field}__startswith': stem})
            .order_by(f'-{field}').values_list(field, flat=True).first())
    seq = (int(last.rsplit('-', 1)[-1]) + 1) if last else 1
    return f'{stem}{seq:04d}'


# ---------------------------------------------------------------------------
# Requisition lifecycle
# ---------------------------------------------------------------------------

@transaction.atomic
def create_requisition(*, company, req_type, category, description, estimated_value,
                       reason, recipient: Employee, user: User, spare_asset=None,
                       auto_submit=True) -> AssetRequisition:
    """Raise a requisition. The recipient is an Employee FK — a free-typed name
    is impossible (AC1). New buys carry no spare; a reissue MUST reference a
    returned spare from the pool (AC4)."""
    if not is_it_asset_officer(user):
        raise ValidationError(
            'Only a named IT Asset Officer may raise an asset requisition.')
    if recipient is None:
        raise ValidationError('A recipient must be selected from the staff directory.')
    if recipient.status != Employee.Status.ACTIVE:
        raise ValidationError('The recipient must be an active member of staff.')
    if not (reason or '').strip():
        raise ValidationError('A reason / justification is required.')

    req_type = str(req_type)
    if req_type == AssetRequisition.Type.REISSUE:
        if spare_asset is None:
            raise ValidationError('A reissue must name the spare asset being reissued.')
        if spare_asset.custody_status != Asset.CustodyStatus.RETURNED_SPARE:
            raise ValidationError(
                'Only a returned/spare asset can be reissued. This asset is '
                f'"{spare_asset.get_custody_status_display()}".'
            )
    else:
        spare_asset = None  # new purchase never carries a spare

    est = Decimal(str(estimated_value or 0))
    policy = AssetControlPolicy.current()

    for _attempt in range(4):
        # Savepoint per attempt: a numbering IntegrityError inside the outer
        # @transaction.atomic would otherwise poison the whole transaction and
        # the next _next_number query would raise TransactionManagementError.
        try:
            with transaction.atomic():
                req = AssetRequisition(
                    requisition_number=_next_number('REQ', AssetRequisition, 'requisition_number'),
                    req_type=req_type,
                    company=company,
                    category=category,
                    description=(description or '').strip()[:300],
                    estimated_value=est,
                    spare_asset=spare_asset,
                    recipient=recipient,
                    recipient_name=recipient.full_name,
                    recipient_email=recipient.email or '',
                    reason=(reason or '').strip()[:500],
                    requires_full_gate=(est >= policy.material_threshold_bwp),
                    status=AssetRequisition.Status.DRAFT,
                    requested_by=user,
                )
                req.save(audit_user=user, audit_description='Raised asset requisition')
            break
        except IntegrityError:
            if _attempt == 3:
                raise
            continue

    if auto_submit:
        submit_requisition(req, user)
    return req


@transaction.atomic
def submit_requisition(req: AssetRequisition, user: User) -> AssetRequisition:
    """DRAFT -> PENDING_FM_APPROVAL."""
    if req.status != AssetRequisition.Status.DRAFT:
        raise ValidationError(f'Only a draft requisition can be submitted. Current: {req.status}.')
    req.submitted_by = user
    req.submitted_at = timezone.now()
    req.status = AssetRequisition.Status.PENDING_FM_APPROVAL
    req.save(audit_user=user, audit_description='Submitted for approval')
    return req


def _block_self_approval(req: AssetRequisition, user: User) -> None:
    """AC2 — a user can NEVER approve their own requisition. Absolute; not
    relaxed for the single-sign-off tier the way procurement relaxes low-value
    POs."""
    uid = getattr(user, 'id', None)
    if req.requested_by_id == uid:
        raise ValidationError('Segregation of duties: you cannot approve a requisition you raised.')
    if req.submitted_by_id == uid:
        raise ValidationError('Segregation of duties: you cannot approve a requisition you submitted.')


@transaction.atomic
def fm_approve(req: AssetRequisition, user: User, comment: str = '') -> AssetRequisition:
    """PENDING_FM_APPROVAL -> PENDING_CFO_APPROVAL (material) or APPROVED (light)."""
    # Lock + re-read the FULL committed row so the guards below run on fresh
    # state, not a stale in-memory instance passed in by the caller.
    req = AssetRequisition.objects.select_for_update().get(pk=req.pk)
    if req.status != AssetRequisition.Status.PENDING_FM_APPROVAL:
        raise ValidationError(f'Only a requisition awaiting the Finance Manager can be FM-approved. Current: {req.status}.')
    if not can_approve_as_fm(user):
        raise ValidationError('Approval requires the Finance Manager, Financial Controller, or CFO title.')
    _block_self_approval(req, user)

    req.fm_approved_by = user
    req.fm_approved_at = timezone.now()
    req.fm_comment = (comment or '').strip()[:500]
    req.status = (
        AssetRequisition.Status.PENDING_CFO_APPROVAL
        if req.requires_full_gate
        else AssetRequisition.Status.APPROVED
    )
    req.save(audit_user=user, audit_description='FM-approved requisition')
    return req


@transaction.atomic
def cfo_approve(req: AssetRequisition, user: User, comment: str = '') -> AssetRequisition:
    """PENDING_CFO_APPROVAL -> APPROVED. Final gate; the two approvers must differ."""
    # Lock + re-read the FULL committed row: the FM≠CFO check below reads
    # fm_approved_by_id, which must be the committed value, not a stale one.
    req = AssetRequisition.objects.select_for_update().get(pk=req.pk)
    if req.status != AssetRequisition.Status.PENDING_CFO_APPROVAL:
        raise ValidationError(f'Only a requisition awaiting the CFO can be CFO-approved. Current: {req.status}.')
    if not can_approve_as_cfo(user):
        raise ValidationError('Only the CFO can give final approval on a requisition.')
    _block_self_approval(req, user)
    if req.fm_approved_by_id == getattr(user, 'id', None):
        raise ValidationError('Segregation of duties: the CFO approver must differ from the FM approver.')

    req.cfo_approved_by = user
    req.cfo_approved_at = timezone.now()
    req.cfo_comment = (comment or '').strip()[:500]
    req.status = AssetRequisition.Status.APPROVED
    req.save(audit_user=user, audit_description='CFO-approved requisition')
    return req


@transaction.atomic
def reject_requisition(req: AssetRequisition, user: User, reason: str) -> AssetRequisition:
    """Reject at either pending leg. A reason is mandatory."""
    if req.status not in (
        AssetRequisition.Status.PENDING_FM_APPROVAL,
        AssetRequisition.Status.PENDING_CFO_APPROVAL,
    ):
        raise ValidationError(f'Only a pending requisition can be rejected. Current: {req.status}.')
    if not (can_approve_as_fm(user) or can_approve_as_cfo(user)):
        raise ValidationError('Only an authorised approver may reject a requisition.')
    if not (reason or '').strip():
        raise ValidationError('A rejection reason is required.')

    req.rejected_by = user
    req.rejected_at = timezone.now()
    req.rejection_reason = reason.strip()[:500]
    req.status = AssetRequisition.Status.REJECTED
    req.save(audit_user=user, audit_description=f'Rejected: {reason.strip()[:120]}')
    return req


@transaction.atomic
def cancel_requisition(req: AssetRequisition, user: User, reason: str = '') -> AssetRequisition:
    """Cancel an open requisition (requester or an approver), before handover."""
    if not req.is_open:
        raise ValidationError(f'Only an open requisition can be cancelled. Current: {req.status}.')
    is_owner = req.requested_by_id == getattr(user, 'id', None)
    if not (is_owner or can_approve_as_fm(user)):
        raise ValidationError('Only the requester or an approver can cancel this requisition.')
    req.status = AssetRequisition.Status.CANCELLED
    req.rejection_reason = (reason or '').strip()[:500]
    req.save(audit_user=user, audit_description='Cancelled requisition')
    return req


# ---------------------------------------------------------------------------
# Handover — the three-signature note. The asset becomes IN_USE only at the end.
# ---------------------------------------------------------------------------

@transaction.atomic
def create_handover(req: AssetRequisition, *, asset: Asset, user: User,
                    condition_on_issue='', accessories='') -> AssetHandover:
    """Open a handover note against an APPROVED requisition (AC3). For a reissue
    the asset must be the approved spare (AC4); for a new purchase it must be a
    freshly registered asset sitting in stock."""
    if req.status != AssetRequisition.Status.APPROVED:
        raise ValidationError(
            'A handover can only start once the requisition is fully approved '
            f'(both approvals). Current: {req.status}.'
        )
    if hasattr(req, 'handover'):
        raise ValidationError('This requisition already has a handover note.')

    # Lock the asset row so two requisitions can't both read it IN_STOCK and
    # both reserve it (double-issue). Validate the custody state on the locked row.
    asset = Asset.objects.select_for_update().get(pk=asset.pk)

    if req.req_type == AssetRequisition.Type.REISSUE:
        if req.spare_asset_id is None or asset.id != req.spare_asset_id:
            raise ValidationError('The handover asset must be the approved spare from this requisition.')
        if asset.custody_status != Asset.CustodyStatus.RETURNED_SPARE:
            raise ValidationError('The spare is no longer in the returned/spare pool.')
    else:
        if asset.custody_status != Asset.CustodyStatus.IN_STOCK:
            raise ValidationError(
                'A new-purchase handover must use a newly registered asset that is in stock '
                f'(this asset is "{asset.get_custody_status_display()}").'
            )

    for _attempt in range(4):
        try:
            with transaction.atomic():   # savepoint per numbering attempt
                ho = AssetHandover(
                    handover_number=_next_number('HO', AssetHandover, 'handover_number'),
                    requisition=req,
                    asset=asset,
                    recipient=req.recipient,
                    recipient_name=req.recipient_name,
                    recipient_email=req.recipient_email,
                    condition_on_issue=(condition_on_issue or '').strip()[:300],
                    accessories=(accessories or '').strip()[:300],
                    status=AssetHandover.Status.PENDING,
                )
                ho.save(audit_user=user, audit_description='Opened handover note')
            break
        except IntegrityError:
            if _attempt == 3:
                raise
            continue

    # Reserve the asset to this handover so it can't be double-issued.
    asset.custody_status = Asset.CustodyStatus.ISSUED
    asset.save(update_fields=['custody_status', 'updated_at'])
    req.resulting_asset = asset
    req.save(update_fields=['resulting_asset', 'updated_at'])
    return ho


@transaction.atomic
def handover_it_release(ho: AssetHandover, user: User, signature='') -> AssetHandover:
    """Signature 1 — IT releases the asset."""
    if ho.status != AssetHandover.Status.PENDING:
        raise ValidationError(f'Handover is "{ho.get_status_display()}" — cannot record IT release.')
    if not is_it_asset_officer(user):
        raise ValidationError('Only a named IT Asset Officer may sign the IT-release.')
    # The recipient cannot also release the asset — the three signers must be
    # three different people (IT ≠ Finance ≠ recipient). [spec §6.3]
    if not getattr(user, 'is_superuser', False) and ho.recipient.user_id == getattr(user, 'id', None):
        raise ValidationError('Segregation of duties: the recipient cannot sign the IT release.')
    ho.it_released_by = user
    ho.it_released_at = timezone.now()
    ho.it_signature = signature or ''
    ho.status = AssetHandover.Status.IT_RELEASED
    ho.save(audit_user=user, audit_description='IT released asset')
    return ho


@transaction.atomic
def handover_finance_record(ho: AssetHandover, user: User, signature='') -> AssetHandover:
    """Signature 2 — Finance records the movement."""
    if ho.status != AssetHandover.Status.IT_RELEASED:
        raise ValidationError('Finance can only record after IT has released the asset.')
    if not can_record_finance(user):
        raise ValidationError('The Finance signature requires a finance title.')
    if ho.it_released_by_id == getattr(user, 'id', None):
        raise ValidationError('Segregation of duties: the Finance signer must differ from the IT releaser.')
    ho.finance_recorded_by = user
    ho.finance_recorded_at = timezone.now()
    ho.finance_signature = signature or ''
    ho.status = AssetHandover.Status.FINANCE_RECORDED
    ho.save(audit_user=user, audit_description='Finance recorded handover')
    return ho


@transaction.atomic
def handover_employee_accept(ho: AssetHandover, user: User, signature='') -> AssetHandover:
    """Signature 3 — the recipient e-signs to accept. Completing the note is the
    ONLY way an asset becomes IN_USE (AC5): custodian is set and an append-only
    AssetAssignment row is written."""
    if ho.status != AssetHandover.Status.FINANCE_RECORDED:
        raise ValidationError('The employee can only accept after Finance has recorded the handover.')

    # The acceptance must be the recipient's own e-signature (ties the asset to
    # a real, active staff member), unless a superuser signs on their behalf.
    recipient_user_id = ho.recipient.user_id
    if not getattr(user, 'is_superuser', False):
        if recipient_user_id is None or recipient_user_id != getattr(user, 'id', None):
            raise ValidationError('Only the named recipient can accept this handover.')
    # All three signatures must be three distinct people — the recipient must
    # not also have provided the IT-release or Finance signature.
    uid = getattr(user, 'id', None)
    if uid in (ho.it_released_by_id, ho.finance_recorded_by_id):
        raise ValidationError('Segregation of duties: the accepting recipient already signed another slot.')

    ho.employee_accepted_by = user
    ho.employee_accepted_at = timezone.now()
    ho.employee_signature = signature or ''
    ho.status = AssetHandover.Status.ACCEPTED
    ho.save(audit_user=user, audit_description='Employee accepted asset')

    asset = ho.asset
    from_emp = asset.custodian_employee
    from_cust = asset.custodian or ''
    from_loc = asset.location or ''

    asset.custody_status = Asset.CustodyStatus.IN_USE
    asset.custodian_employee = ho.recipient
    asset.custodian = ho.recipient_name
    asset.save(update_fields=['custody_status', 'custodian_employee', 'custodian', 'updated_at'])

    AssetAssignment.objects.create(
        asset=asset,
        from_employee=from_emp,
        to_employee=ho.recipient,
        from_custodian=from_cust,
        to_custodian=ho.recipient_name,
        from_location=from_loc,
        to_location=asset.location or '',
        reason=f'Handover {ho.handover_number}'[:300],
        transferred_at=timezone.localdate(),
        transferred_by=user,
    )

    ho.requisition.status = AssetRequisition.Status.FULFILLED
    ho.requisition.save(update_fields=['status', 'updated_at'])
    return ho


@transaction.atomic
def cancel_handover(ho: AssetHandover, user: User, reason: str = '') -> AssetHandover:
    """Cancel a handover note that was never accepted, and release the asset it
    reserved so it is not stuck in ISSUED. Because AssetHandover.requisition is a
    OneToOne, a cancelled note cannot be replaced under the SAME requisition — so
    the requisition is CANCELLED too and IT raises a fresh one. Only Finance /
    an FM (or a superuser) may cancel; never the recipient."""
    # Lock + re-read the note FIRST so a stale in-memory instance can't cancel an
    # already-accepted note (which would strand the asset IN_USE). Lock order is
    # note -> asset, matching handover_employee_accept.
    ho = AssetHandover.objects.select_for_update().get(pk=ho.pk)
    if ho.status == AssetHandover.Status.ACCEPTED:
        raise ValidationError('An accepted handover cannot be cancelled — return the asset instead.')
    if ho.status == AssetHandover.Status.CANCELLED:
        raise ValidationError('This handover is already cancelled.')

    is_su = bool(getattr(user, 'is_superuser', False))
    if not is_su:
        if ho.recipient.user_id == getattr(user, 'id', None):
            raise ValidationError('The recipient cannot cancel their own handover note.')
        if not (can_approve_as_fm(user) or can_record_finance(user)):
            raise ValidationError('Only Finance or a Finance Manager can cancel a handover note.')

    asset = Asset.objects.select_for_update().get(pk=ho.asset_id)
    req = ho.requisition
    # Put the asset back where it was before the note reserved it (ISSUED).
    if asset.custody_status == Asset.CustodyStatus.ISSUED:
        asset.custody_status = (
            Asset.CustodyStatus.RETURNED_SPARE
            if req.req_type == AssetRequisition.Type.REISSUE
            else Asset.CustodyStatus.IN_STOCK
        )
        asset.save(update_fields=['custody_status', 'updated_at'])

    ho.status = AssetHandover.Status.CANCELLED
    ho.notes = (f'Cancelled: {reason.strip()}' if reason else 'Cancelled')[:500]
    ho.save(audit_user=user, audit_description='Cancelled handover note')

    # OneToOne blocks re-opening a note on this requisition — cancel it so the
    # dead-end is visible and IT raises a fresh requisition.
    req.status = AssetRequisition.Status.CANCELLED
    req.resulting_asset = None
    req.rejection_reason = (f'Handover {ho.handover_number} cancelled: {reason.strip()}')[:500]
    req.save(update_fields=['status', 'resulting_asset', 'rejection_reason', 'updated_at'])
    return ho


# ---------------------------------------------------------------------------
# Return to the Spare Pool + the offboarding gate
# ---------------------------------------------------------------------------

@transaction.atomic
def return_asset(asset: Asset, user: User, *, reason='Returned to store') -> AssetAssignment:
    """Bring a held asset back into the Returned/Spare pool. Reissuing it later
    needs a fresh, dual-approved requisition (AC4). Reuses the append-only
    AssetAssignment custody history."""
    if asset.custody_status not in (
        Asset.CustodyStatus.IN_USE,
        Asset.CustodyStatus.ISSUED,
    ):
        raise ValidationError(
            f'Only a held asset can be returned (this asset is '
            f'"{asset.get_custody_status_display()}").'
        )
    from_emp = asset.custodian_employee
    from_cust = asset.custodian or ''
    from_loc = asset.location or ''

    asset.custody_status = Asset.CustodyStatus.RETURNED_SPARE
    asset.custodian_employee = None
    asset.custodian = ''
    asset.save(update_fields=['custody_status', 'custodian_employee', 'custodian', 'updated_at'])

    return AssetAssignment.objects.create(
        asset=asset,
        from_employee=from_emp,
        to_employee=None,
        from_custodian=from_cust,
        to_custodian='Spare pool',
        from_location=from_loc,
        to_location=asset.location or '',
        reason=(reason or 'Returned to store').strip()[:300],
        transferred_at=timezone.localdate(),
        transferred_by=user,
    )


def assets_blocking_offboarding(employee: Employee):
    """Assets still with a leaver that block finalising their exit (AC6).

    A returned/spare asset no longer blocks — it has left the person. Only
    assets they physically still hold (issued or in use) do.
    """
    if employee is None or employee.id is None:
        return Asset.objects.none()
    return (Asset.objects
            .filter(custodian_employee_id=employee.id,
                    custody_status__in=[
                        Asset.CustodyStatus.ISSUED,
                        Asset.CustodyStatus.IN_USE,
                    ])
            .order_by('tag_number'))
