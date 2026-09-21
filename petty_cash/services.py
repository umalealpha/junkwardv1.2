"""
petty_cash/services.py

State-transition + JE-posting service layer for the petty cash module.

Public API:
  Voucher
    create_voucher(...)             -> PettyCashVoucher (DRAFT)
    submit_voucher(voucher, user)   DRAFT -> PENDING_APPROVAL
    approve_voucher(voucher, user)  PENDING_APPROVAL -> POSTED (posts JE)
    reject_voucher(voucher, user, reason)
    reopen_voucher(voucher, user)   REJECTED -> DRAFT (creator only)

  Reimbursement (three-stage approval chain — CFO directive 2026-07-15)
    preview_reimbursement(location, period_start, period_end)
    create_reimbursement(location, period_start, period_end, user, period=None)
    submit_reimbursement(reimb, user)      DRAFT/REJECTED -> PENDING_FM
    fm_review_reimbursement(reimb, user)   PENDING_FM  -> PENDING_CFO
    reject_reimbursement(reimb, user, reason)  PENDING_* -> REJECTED
    reopen_reimbursement(reimb, user)      REJECTED    -> DRAFT (creator)
    post_reimbursement(reimb, user)        PENDING_CFO -> POSTED (CFO posts JE)

Approval rules (petty cash — deliberately lighter than procurement/JE):
  • TWO signatures post a voucher. Eligible signer title in {Accountant,
    Senior Accountant, Finance Manager, Financial Controller, CFO} OR a
    Django superuser (break-glass).
  • Segregation of duties: a signer != submitter and != creator; the second
    signer must also differ from the first.
  • Vouchers cannot be posted if the resulting cash on hand would go
    negative — the cash isn't there.

JE shapes:
  Voucher approval (one JE per voucher):
    DR  expense_account     (amount)
    CR  petty cash (1160)   (amount)

  Reimbursement post (one JE per reimbursement):
    DR  petty cash (1160)   (total)
    CR  reimbursing bank    (total)
"""

from __future__ import annotations

from datetime import date as Date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import UserProfile
from ledger.models import JournalEntry, JournalEntryLine

from .models import (
    PettyCashLocation,
    PettyCashReimbursement,
    PettyCashVoucher,
    ZERO,
)


# ---------------------------------------------------------------------------
# Authority helpers
# ---------------------------------------------------------------------------

# CFO directive 2026-08-03: "petty cash I don't need to approve — it's Keetile,
# or Pako, Legakwa or Tlamelo."
#
# The old pool was TITLE-based (any Accountant / Senior Accountant / FM / FC /
# CFO). On live data that resolved to 46 people, because `accountant` is the
# catch-all title carried by shared mailboxes (health@, people@, hc@), a service
# account (svc-ceomonitor) and outside addresses. A money control must name its
# people, not infer them from a title that everyone happens to hold — the same
# reasoning that already narrowed the stage-1 reviewer below (2026-07-15).
#
# Two DISTINCT signatures still post a voucher (see approve_voucher); the named
# set is comfortably enough. To change the signers, edit this set and deploy.
_PETTY_CASH_APPROVER_IDS = {
    'kmokhendo@alphadirect.co.bw', 'keetile.mokhendo',   # Keetile Mokhendo — Senior Accountant
    'pkago@alphadirect.co.bw', 'pkago',                  # Pako Kago — Financial Controller
    'lntabeni@alphadirect.co.bw', 'lntabeni',            # Legakwa Tsala Ntabeni
    'tchimidza@alphadirect.co.bw', 'tlamelo.chimidza',   # Tlamelo Chimidza — Senior Accountant
    # Keetile asked for Lefika as the custodian going forward; CFO approved
    # 2026-08-07. Note this is the SAME set that signs vouchers — a custodian
    # here can sign and correct, not merely edit.
    'lbasotli@alphadirect.co.bw', 'lefika.basotli',      # Lefika Basotli — custodian
    # CFO 2026-08-07: "give the approving powers to Pako, Kago, Keetile."
    # Pako and Keetile were already here; Kago Tshutlhedi was only the
    # reimbursement reviewer, so he could not sign a voucher. Now he can.
    'ktshutlhedi@alphadirect.co.bw', 'ktshutlhedi',      # Kago Tshutlhedi
}

# Kept only so existing imports/tests referring to it keep working; the pool is
# now the named set above, NOT this.
_APPROVAL_TITLES = {
    UserProfile.Title.SENIOR_ACCOUNTANT,
    UserProfile.Title.FINANCIAL_CONTROLLER,
    UserProfile.Title.FINANCE_MANAGER,
}


def _profile(user):
    if user is None:
        return None
    try:
        return user.profile
    except UserProfile.DoesNotExist:
        return None


def _is_named_approver(user) -> bool:
    """One of the signers the CFO named. Positive match on email OR
    username — never a title, never a substring, and an unknown user is
    REFUSED rather than defaulted through (checklist L6)."""
    if user is None:
        return False
    profile = _profile(user)
    if profile is not None and not profile.is_active:
        return False
    email = (getattr(user, 'email', '') or '').strip().lower()
    uname = (getattr(user, 'username', '') or '').strip().lower()
    return email in _PETTY_CASH_APPROVER_IDS or uname in _PETTY_CASH_APPROVER_IDS


def is_routine_approver(user) -> bool:
    """Whose day-job this is — drives the My-Approvals inbox. Deliberately does
    NOT include the superuser break-glass, so petty cash stops appearing on the
    CFO's dashboard while he can still act in an emergency via _can_approve."""
    return _is_named_approver(user)


def _can_approve(user) -> bool:
    if user is None:
        return False
    if getattr(user, 'is_superuser', False):
        return True          # break-glass only; not a routine signer
    return _is_named_approver(user)


# CFO directive 2026-07-15: the petty-cash middle reviewer is specifically
# Kago Tshutlhedi (Finance Manager) or Pako Kago (Financial Controller) — NOT
# every FM/FC. Narrowed from a title check to these two named people so the
# other finance managers (Legakwa, Oprah, Bharath) are not in the petty-cash
# chain. Matches on email or username (belt-and-suspenders). To change the
# reviewers, edit this set and redeploy.
_PETTY_CASH_FM_REVIEWER_IDS = {
    'ktshutlhedi@alphadirect.co.bw', 'ktshutlhedi',   # Kago Tshutlhedi
    'pkago@alphadirect.co.bw', 'pkago',               # Pako Kago
}


def _can_fm_review(user) -> bool:
    """Stage-1 reviewer: Kago or Pako only (or a Django superuser, break-glass)."""
    if user is None:
        return False
    if getattr(user, 'is_superuser', False):
        return True
    profile = _profile(user)
    if profile is None or not profile.is_active:
        return False
    email = (getattr(user, 'email', '') or '').strip().lower()
    uname = (getattr(user, 'username', '') or '').strip().lower()
    return email in _PETTY_CASH_FM_REVIEWER_IDS or uname in _PETTY_CASH_FM_REVIEWER_IDS


def _can_cfo_approve(user) -> bool:
    """Stage-2 (final) approver: the CFO — the person who releases the bank
    payment (or a Django superuser as break-glass)."""
    if user is None:
        return False
    if getattr(user, 'is_superuser', False):
        return True
    profile = _profile(user)
    if profile is None or not profile.is_active:
        return False
    return profile.title == UserProfile.Title.CFO


# ---------------------------------------------------------------------------
# Per-entity access override — Unicoin (CFO directive 2026-08-31)
# ---------------------------------------------------------------------------
# Unicoin petty cash is ring-fenced from the group finance pool:
#   • RAISE (input) a Unicoin voucher: Bakang Mhusiwa or Phatsimo Moseki only.
#   • APPROVE / reject / amend a Unicoin voucher: Bharath Balasubramanian
#     (first preference) or Keetile Mokhendo (fallback) only — via Omni.
#   • ONE approval posts a Unicoin voucher (single-signature — see
#     _PETTY_CASH_SIGNATURES_BY_COMPANY), unlike the group tins' two-signature
#     rule. CFO 2026-08-31: "Bharath is preferred and Keetile is fallback."
# Cash taken WITHOUT an Omni approval from Bharath or Keetile is treated as
# theft (the red notice on the Unicoin petty-cash screens says exactly that).
# Keyed by core.Company.code; a company not listed keeps the group rules above.
# To change the people, edit these sets + the labels and redeploy.
_PETTY_CASH_APPROVERS_BY_COMPANY = {
    'UNI': {
        'bbalasubramanian@alphadirect.co.bw', 'bbalasubramanian',   # Bharath Balasubramanian (1st preference)
        'kmokhendo@alphadirect.co.bw', 'keetile.mokhendo',          # Keetile Mokhendo (fallback)
    },
}
_PETTY_CASH_INPUTTERS_BY_COMPANY = {
    'UNI': {
        'bmhusiwa@insurance.co.bw', 'bmhusiwa',                     # Bakang Mhusiwa
        'pmoseki@insurance.co.bw', 'pmoseki',                       # Phatsimo Moseki
    },
}
# Number of signatures a voucher needs to post, per entity. The group default is
# TWO (SoD, unchanged). Unicoin is ONE: the CFO named a preferred approver with a
# fallback, i.e. a single approval releases it. Keyed by core.Company.code.
_PETTY_CASH_SIGNATURES_BY_COMPANY = {
    'UNI': 1,
}
# Human-readable, for the on-screen notice + the enforcement error messages.
# Keep in step with the id sets above (first name = first-preference approver).
_PETTY_CASH_OVERRIDE_LABELS = {
    'UNI': {
        'approvers': 'Bharath Balasubramanian (first preference) or Keetile Mokhendo',
        'inputters': 'Bakang Mhusiwa or Phatsimo Moseki',
    },
}


def _signatures_required(voucher) -> int:
    """How many signatures post this voucher. Group tins = 2 (SoD); a ring-fenced
    tin may override to 1 (Unicoin). Keyed on the voucher's company."""
    return _PETTY_CASH_SIGNATURES_BY_COMPANY.get(
        _company_code(getattr(voucher, 'location', None)), 2)


def _company_code(location) -> str:
    code = getattr(getattr(location, 'company', None), 'code', '') or ''
    return code.strip().upper()


def _matches_ids(user, id_set) -> bool:
    email = (getattr(user, 'email', '') or '').strip().lower()
    uname = (getattr(user, 'username', '') or '').strip().lower()
    return email in id_set or uname in id_set


def can_input_for_location(user, location) -> bool:
    """May this user RAISE a voucher against this tin?

    A company with an input override (Unicoin) accepts only its named raisers
    (plus a Django superuser, break-glass). Every other tin keeps the existing
    open rule — any authenticated staff may raise; the coded-voucher maker gate
    in the API still applies on top."""
    if user is None:
        return False
    override = _PETTY_CASH_INPUTTERS_BY_COMPANY.get(_company_code(location))
    if override is None:
        return True
    if getattr(user, 'is_superuser', False):
        return True
    profile = _profile(user)
    if profile is not None and not profile.is_active:
        return False
    return _matches_ids(user, override)


def _can_approve_voucher(user, voucher) -> bool:
    """Signer/actioner gate that honours a per-entity approver override.

    For a company with an override (Unicoin), ONLY its named approvers may sign,
    reject or amend — the group finance pool does NOT apply to that tin. A
    Django superuser keeps break-glass. Companies without an override keep the
    group rule (_can_approve)."""
    if user is None:
        return False
    if getattr(user, 'is_superuser', False):
        return True
    override = _PETTY_CASH_APPROVERS_BY_COMPANY.get(
        _company_code(getattr(voucher, 'location', None)))
    if override is not None:
        profile = _profile(user)
        if profile is not None and not profile.is_active:
            return False
        return _matches_ids(user, override)
    return _is_named_approver(user)


def access_notice_for_location(location):
    """The ring-fence notice for a tin with a per-entity override, else None.

    Server-owned so the on-screen red banner and the enforced rule can never
    drift apart. Returned to the frontend on the location serializer."""
    labels = _PETTY_CASH_OVERRIDE_LABELS.get(_company_code(location))
    if not labels:
        return None
    name = getattr(location, 'name', None) or 'This'
    return {
        'level': 'theft-warning',
        'approvers': labels['approvers'],
        'inputters': labels['inputters'],
        'text': (
            f'{name} may only be raised by {labels["inputters"]}, and must be '
            f'approved in Omni by {labels["approvers"]}. Any petty cash taken '
            f'WITHOUT an Omni approval from one of them is considered THEFT and '
            f'will lead to disciplinary action.'
        ),
    }


def _approver_gate_message(voucher) -> str:
    """The refusal shown when someone not authorised tries to action a voucher —
    entity-specific when the tin is ring-fenced, else the group message."""
    override = _PETTY_CASH_OVERRIDE_LABELS.get(_company_code(getattr(voucher, 'location', None)))
    if override:
        name = getattr(getattr(voucher, 'location', None), 'name', None) or 'This tin'
        return (
            f'{name} petty cash must be approved in Omni by {override["approvers"]}. '
            f'Cash taken without their Omni approval is treated as theft.'
        )
    return (
        'Petty-cash vouchers are signed by Keetile Mokhendo, Pako Kago, '
        'Legakwa Tsala Ntabeni or Tlamelo Chimidza (CFO directive 2026-08-03). '
        'Two different signers are needed.'
    )


def _je_company(location):
    """The Company whose books a petty-cash JE belongs to.

    Prefer the location's own entity; fall back to the entity that owns the
    petty-cash GL account. A JE created with company=None (the old behaviour)
    bypasses the ADIC historical FY lock (ledger/locks.py short-circuits when
    company_code != 'ADIC') and never rolls up into a per-company report — so
    stamping the company here is a correctness control, not cosmetics.
    """
    if getattr(location, 'company_id', None):
        return location.company
    return getattr(location.petty_cash_account, 'owner_company', None)


# ---------------------------------------------------------------------------
# Voucher — create
# ---------------------------------------------------------------------------

@transaction.atomic
def create_voucher(*, location, voucher_date, payee, amount, expense_account,
                   description, receipt_reference='', receipt_attached=False,
                   user) -> PettyCashVoucher:
    """Create a new voucher in DRAFT status. Pure-form validation.

    The available-float check is deferred to submit/approve, so a custodian
    can save several drafts even if their committed total temporarily
    exceeds the float (the FM will reject the surplus).
    """
    if amount is None or amount <= ZERO:
        raise ValidationError({'amount': 'Voucher amount must be positive.'})
    if not (description or '').strip():
        raise ValidationError({'description': 'Description is required.'})
    # The GL account is now OPTIONAL at entry (CFO directive 2026-08-10): the
    # person who raises the voucher does not code it — the petty-cash finance
    # team sets the expense account when they post it. Only validate the type
    # if one was supplied (e.g. a finance maker entering a coded voucher).
    if expense_account is not None and expense_account.account_type != 'expense':
        raise ValidationError({
            'expense_account': 'Voucher must be charged to an expense account.',
        })
    if not location.is_active:
        raise ValidationError({'location': 'Location is not active.'})

    voucher = PettyCashVoucher(
        location=location,
        voucher_date=voucher_date or timezone.localdate(),
        payee=(payee or '').strip()[:200],
        amount=amount,
        expense_account=expense_account,
        description=description.strip(),
        receipt_reference=(receipt_reference or '').strip()[:120],
        receipt_attached=bool(receipt_attached),
        status=PettyCashVoucher.Status.DRAFT,
        created_by=user,
    )
    voucher.full_clean(exclude=['voucher_number'])
    voucher.save(audit_user=user)
    return voucher


# ---------------------------------------------------------------------------
# Voucher — submit
# ---------------------------------------------------------------------------

@transaction.atomic
def submit_voucher(voucher: PettyCashVoucher, user: User) -> PettyCashVoucher:
    """DRAFT -> PENDING_APPROVAL.

    Validates that the available float (after this voucher) wouldn't go
    negative — fails loudly if you'd be promising cash you don't have.
    """
    if voucher.status != PettyCashVoucher.Status.DRAFT:
        raise ValidationError(
            f'Only draft vouchers can be submitted. Current status: '
            f'{voucher.get_status_display()}.'
        )
    available = voucher.location.available_for_voucher()
    # available_for_voucher() already includes this voucher only if it's in
    # PENDING_APPROVAL or POSTED; this one is still DRAFT, so subtract it now.
    projected = available - voucher.amount
    if projected < ZERO:
        raise ValidationError(
            f'Insufficient float at {voucher.location.name}. Available '
            f'P{available}, voucher amount P{voucher.amount}. Run a '
            f'reimbursement first to top up the tin.'
        )

    voucher.status = PettyCashVoucher.Status.PENDING_APPROVAL
    voucher.submitted_by = user
    voucher.submitted_at = timezone.now()
    voucher.save(audit_user=user, audit_description=f'Submitted {voucher.voucher_number}')
    # Route to a TASK for the petty-cash handlers (Keetile, Tlamelo) + give the
    # CFO passive visibility — best-effort, must never block the submit.
    # CFO 2026-07-16.
    from core.notifications import notify_petty_cash_pending
    notify_petty_cash_pending(voucher, user)
    return voucher


# ---------------------------------------------------------------------------
# Voucher — approve (posts JE)
# ---------------------------------------------------------------------------

@transaction.atomic
def amend_voucher(voucher: PettyCashVoucher, user: User, *, amount=None,
                  expense_account=None, reason: str = '') -> PettyCashVoucher:
    """Let the custodian correct the amount and/or the GL line before signing.

    CFO approved 2026-08-05, on Keetile's request: the custodian should be able to finish the
    job rather than bounce the voucher back for a typo. Three rules make that safe rather than
    a hole in the float:

      1. **A reason is required.** An amended amount with no explanation is indistinguishable
         from an error.
      2. **The requester's original figure is kept**, along with who changed it and when. The
         float has to be reconcilable months later.
      3. **Any signature already given is cleared.** Signing is agreement to a FIGURE — if the
         figure changes, the earlier signature no longer means anything, and letting it stand
         would let a voucher be signed at P500 and posted at P5,000.
    """
    locked = (PettyCashVoucher.objects.select_for_update().filter(pk=voucher.pk).first())
    if locked is not None:
        voucher = locked

    if voucher.status not in (PettyCashVoucher.Status.PENDING_APPROVAL,
                              PettyCashVoucher.Status.ONE_SIGNATURE):
        raise ValidationError(
            f'Only a voucher still in the signature chain can be amended. Current status: '
            f'{voucher.get_status_display()}.')
    if not _can_approve_voucher(user, voucher):
        raise ValidationError(_approver_gate_message(voucher))
    reason = (reason or '').strip()

    changes = []
    if amount is not None and Decimal(str(amount)) != voucher.amount:
        if Decimal(str(amount)) <= 0:
            raise ValidationError({'amount': 'The amount must be more than zero.'})
        if voucher.original_amount is None:
            voucher.original_amount = voucher.amount        # keep the FIRST figure, not the last
        changes.append(f'amount {voucher.amount} → {Decimal(str(amount))}')
        voucher.amount = Decimal(str(amount))

    gl_overwrite = False
    if expense_account is not None and expense_account.pk != voucher.expense_account_id:
        if expense_account.account_type != 'expense':
            raise ValidationError({'expense_account': 'Charge it to an expense account.'})
        if voucher.expense_account_id is None:
            # First-time coding of a voucher that was RAISED without a GL (CFO
            # directive 2026-08-10). There is no prior figure to preserve and
            # nothing to explain, so no reason is required.
            changes.append(f'coded to GL {expense_account.code}')
        else:
            gl_overwrite = True
            if voucher.original_expense_account_id is None:
                voucher.original_expense_account_id = voucher.expense_account_id
            changes.append(f'GL {voucher.expense_account.code} → {expense_account.code}')
        voucher.expense_account = expense_account

    if not changes:
        raise ValidationError('Nothing was changed.')

    # A reason is required when CHANGING a figure that was already set — an
    # unexplained rewrite is how a float stops reconciling. First-time coding of
    # a blank GL (no amount change) needs no explanation.
    amount_changed = any(c.startswith('amount ') for c in changes)
    if (amount_changed or gl_overwrite) and not reason:
        raise ValidationError({'reason': 'Say why you are changing it — the requester is told.'})

    voucher.amended_by = user
    voucher.amended_at = timezone.now()
    voucher.amend_reason = reason

    # The figure changed, so any agreement to the OLD figure lapses. Signatures live on the
    # voucher itself (first_approved_by / approved_by), so clearing them is what sends it back to
    # the start of the chain. Without this a voucher could be signed at one amount and posted at
    # another.
    had_signature = bool(voucher.first_approved_by_id or voucher.approved_by_id)
    voucher.first_approved_by = None
    voucher.first_approved_at = None
    voucher.approved_by = None
    voucher.approved_at = None
    voucher.status = PettyCashVoucher.Status.PENDING_APPROVAL

    voucher.full_clean(exclude=['voucher_number'])   # reuse the model's own amount / GL rules
    _desc = 'Amended: ' + '; '.join(changes)
    if reason:
        _desc += ' — ' + reason[:120]
    voucher.save(audit_user=user, audit_description=_desc)

    _notify_amended(voucher, user, changes, reason, had_signature)
    return voucher


def _notify_amended(voucher, user, changes, reason, had_signature, was_posted=False) -> None:
    # Tell the requester their voucher was changed — never silently. The person who asked for the
    # money is the one who has to recognise the figure later, so the change goes to them in words,
    # with who made it and why. A notification failure must never undo the amendment.
    try:
        from core.notifications import send_html_with_cfo_cc
    except BaseException:      # noqa: BLE001
        return
    people = {voucher.submitted_by, getattr(voucher, 'created_by', None)}
    if was_posted:
        # A posted voucher was signed by two people against the old figure. They carry the
        # sign-off, so they are told as well — the audit trail alone is not a notification.
        people |= {voucher.first_approved_by, voucher.approved_by}
    to = [u.email for u in people if u is not None and getattr(u, 'email', '')]
    if not to:
        return
    again = ('<p style="margin:0 0 10px">It needs signing again from the start, because the '
             'earlier signature applied to the previous figure.</p>' if had_signature else '')
    when = ' after posting' if was_posted else ' before signing'
    gl = ('<p style="margin:0 0 10px">The original journal entry has been reversed and a '
          'corrected one posted, so the accounts already reflect this.</p>' if was_posted else '')
    body = ('<p style="margin:0 0 10px">' + voucher.voucher_number + ' was amended by <b>'
            + user.get_username() + '</b>' + when + ':</p>'
            '<ul style="margin:0 0 10px 18px"><li>' + '</li><li>'.join(changes) + '</li></ul>'
            '<p style="margin:0 0 10px"><b>Reason given:</b> ' + reason + '</p>' + again + gl
            + '<p style="margin:0;font-size:12px;color:#6B7280">What you originally asked for is '
              'kept on the voucher.</p>')
    try:
        send_html_with_cfo_cc(
            subject=voucher.voucher_number + ' amended' + when, html=body, to=to)
    except BaseException:      # noqa: BLE001
        pass


@transaction.atomic
def amend_posted_voucher(voucher: PettyCashVoucher, user: User, *, amount=None,
                         expense_account=None, reason: str = '') -> PettyCashVoucher:
    """Correct a POSTED voucher — the window between the second signature and payment.

    CFO directive 2026-08-07, on Keetile's request: a voucher that has been signed twice
    but NOT yet paid out in a replenishment must still be fixable, otherwise a typo can
    only be cleared by unwinding the whole float.

    The window closes at payment. Once the CFO posts the replenishment that sweeps this
    voucher it becomes REIMBURSED — cash has left the bank, and the model itself refuses
    any further change from that point on.

    Nothing is edited behind the GL's back: the original journal entry is reversed and a
    fresh one is posted for the corrected figures, so the trial balance is right at every
    moment and both entries stay on the record.
    """
    locked = PettyCashVoucher.objects.select_for_update().filter(pk=voucher.pk).first()
    if locked is not None:
        voucher = locked

    if voucher.status == PettyCashVoucher.Status.REIMBURSED:
        raise ValidationError(
            f'{voucher.voucher_number} has been paid out in reimbursement '
            f'{voucher.reimbursement.reimbursement_number if voucher.reimbursement else ""} '
            f'and can no longer be changed. Reverse the reimbursement first.'.replace('  ', ' ')
        )
    if voucher.status != PettyCashVoucher.Status.POSTED:
        raise ValidationError(
            f'Only a posted, not-yet-paid voucher can be corrected here. Current status: '
            f'{voucher.get_status_display()}.'
        )
    if not _can_approve_voucher(user, voucher):
        raise ValidationError(_approver_gate_message(voucher))
    reason = (reason or '').strip()
    if not reason:
        raise ValidationError({'reason': 'Say why you are correcting it — this one is already in the GL.'})

    changes = []
    old_amount = voucher.amount

    if amount is not None and Decimal(str(amount)) != voucher.amount:
        new_amount = Decimal(str(amount))
        if new_amount <= 0:
            raise ValidationError({'amount': 'The amount must be more than zero.'})
        # Cash-on-hand already reflects the OLD figure, so add it back before testing the new
        # one — otherwise raising a P50 voucher to P60 is judged as if P110 left the tin.
        cash_after = voucher.location.cash_on_hand() + old_amount - new_amount
        if cash_after < ZERO:
            raise ValidationError({
                'amount': f'That would put cash on hand at P{cash_after}. There is not that '
                          f'much in the tin — run a reimbursement first.',
            })
        if voucher.original_amount is None:
            voucher.original_amount = voucher.amount
        changes.append(f'amount {voucher.amount} → {new_amount}')
        voucher.amount = new_amount

    if expense_account is not None and expense_account.pk != voucher.expense_account_id:
        if expense_account.account_type != 'expense':
            raise ValidationError({'expense_account': 'Charge it to an expense account.'})
        if voucher.original_expense_account_id is None:
            voucher.original_expense_account_id = voucher.expense_account_id
        changes.append(f'GL {voucher.expense_account.code} → {expense_account.code}')
        voucher.expense_account = expense_account

    if not changes:
        raise ValidationError('Nothing was changed.')

    # Reverse the entry that carried the wrong figures, then post the right ones. Order
    # matters: reverse() refuses anything that is not POSTED, so a second correction of the
    # same voucher reverses the CORRECTION, never the already-reversed original.
    old_je = voucher.journal_entry
    if old_je is not None:
        # _allow_direct mirrors how the voucher JE was posted in the first place: the control
        # on petty cash is the two signatures, not the JE approval chain. Without it only a
        # CFO/FM could correct a voucher — which is the wall Keetile hit.
        old_je.reverse(
            user, f'Petty cash {voucher.voucher_number} corrected: ' + '; '.join(changes),
            _allow_direct=True,
        )

    voucher.amended_by = user
    voucher.amended_at = timezone.now()
    voucher.amend_reason = reason
    voucher.full_clean(exclude=['voucher_number'])
    voucher.journal_entry = _post_voucher_je(voucher, user)
    voucher.save(
        audit_user=user,
        audit_description='Corrected after posting: ' + '; '.join(changes) + ' — ' + reason[:120],
    )

    _refresh_open_reimbursements(voucher, user)
    _notify_amended(voucher, user, changes, reason, had_signature=False, was_posted=True)
    return voucher


@transaction.atomic
def unpost_voucher(voucher: PettyCashVoucher, user: User, *, reason: str = '') -> PettyCashVoucher:
    """POSTED -> DRAFT, reversing the journal entry on the way out.

    Keetile's second request, CFO approved 2026-08-07. Correcting the amount or the GL
    line in place covers most typos, but a wrong payee, date or description needs the
    whole voucher open again — and a voucher that should never have been paid at all
    needs to come off the books entirely.

    The GL is not rewritten: the original entry is reversed, so the expense and the tin
    both return to where they were, and both entries stay visible. The voucher goes back
    to draft with its signatures cleared, because a signature is agreement to a figure
    that no longer stands. It has to be signed twice again to post.

    Refused once the replenishment has paid it out — that is the CFO's line.
    """
    locked = PettyCashVoucher.objects.select_for_update().filter(pk=voucher.pk).first()
    if locked is not None:
        voucher = locked

    if voucher.status == PettyCashVoucher.Status.REIMBURSED:
        raise ValidationError(
            f'{voucher.voucher_number} has already been paid out and cannot be returned '
            f'to draft. Reverse the reimbursement first.'
        )
    if voucher.status != PettyCashVoucher.Status.POSTED:
        raise ValidationError(
            f'Only a posted, not-yet-paid voucher can be returned to draft. Current '
            f'status: {voucher.get_status_display()}.'
        )
    if not _can_approve_voucher(user, voucher):
        raise ValidationError(_approver_gate_message(voucher))
    reason = (reason or '').strip()
    if not reason:
        raise ValidationError({'reason': 'Say why it is coming back — it is already in the GL.'})

    old_je = voucher.journal_entry
    if old_je is not None:
        old_je.reverse(
            user, f'Petty cash {voucher.voucher_number} returned to draft: {reason}',
            _allow_direct=True,
        )

    # Tell the people whose sign-off is being undone BEFORE the fields are cleared —
    # afterwards there is nobody left on the voucher to notify.
    told = {voucher.submitted_by, voucher.created_by,
            voucher.first_approved_by, voucher.approved_by}

    voucher.journal_entry = None
    voucher.status = PettyCashVoucher.Status.DRAFT
    voucher.first_approved_by = None
    voucher.first_approved_at = None
    voucher.approved_by = None
    voucher.approved_at = None
    voucher.submitted_by = None
    voucher.submitted_at = None
    voucher.amended_by = user
    voucher.amended_at = timezone.now()
    voucher.amend_reason = reason
    voucher.save(
        audit_user=user,
        audit_description=f'Returned to draft (JE reversed) — {reason[:120]}',
    )

    _refresh_open_reimbursements(voucher, user)
    _notify_returned_to_draft(voucher, user, reason, told)
    return voucher


def _notify_returned_to_draft(voucher, user, reason, people) -> None:
    """Unposting erases two signatures and reverses a GL entry. An audit row is not a
    notification — the requester and both signers are told in words. A mail failure must
    never undo the reversal."""
    try:
        from core.notifications import send_html_with_cfo_cc
    except BaseException:      # noqa: BLE001
        return
    to = [u.email for u in people if u is not None and getattr(u, 'email', '')]
    if not to:
        return
    body = ('<p style="margin:0 0 10px">' + voucher.voucher_number + ' has been sent back to '
            'draft by <b>' + user.get_username() + '</b>.</p>'
            '<p style="margin:0 0 10px"><b>Reason given:</b> ' + reason + '</p>'
            '<p style="margin:0 0 10px">Its journal entry has been reversed, so the expense and '
            'the petty cash float are back where they were. Both signatures have been cleared — '
            'if the voucher is still valid it has to be submitted and signed again.</p>')
    try:
        send_html_with_cfo_cc(
            subject=voucher.voucher_number + ' returned to draft — signatures cleared',
            html=body, to=to)
    except BaseException:      # noqa: BLE001
        pass


def _refresh_open_reimbursements(voucher: PettyCashVoucher, user: User) -> None:
    """Re-total any replenishment still in flight that would sweep this voucher.

    post_reimbursement recomputes the total from the vouchers at posting time anyway, so
    without this the CFO would be approving a figure on screen that quietly changes when
    posted. Rejected and already-posted replenishments are left alone.
    """
    open_states = (
        PettyCashReimbursement.Status.DRAFT,
        PettyCashReimbursement.Status.PENDING_FM,
        PettyCashReimbursement.Status.PENDING_CFO,
    )
    affected = PettyCashReimbursement.objects.filter(
        location=voucher.location,
        status__in=open_states,
        period_start__lte=voucher.voucher_date,
        period_end__gte=voucher.voucher_date,
    )
    for reimb in affected:
        vouchers = list(_eligible_vouchers(reimb.location, reimb.period_start, reimb.period_end))
        reimb.total_amount = sum((v.amount for v in vouchers), ZERO)
        reimb.voucher_count = len(vouchers)
        reimb.save(
            audit_user=user,
            audit_description=(
                f'Re-totalled after {voucher.voucher_number} was corrected: '
                f'P{reimb.total_amount} over {reimb.voucher_count} vouchers'
            ),
        )


def _post_voucher_je(voucher: PettyCashVoucher, user: User) -> JournalEntry:
    """DR expense / CR petty cash for this voucher's CURRENT figures, posted.

    Shared by the second signature (approve_voucher) and by a post-signature
    correction (amend_posted_voucher), so a corrected voucher always produces
    exactly the same JE shape as an uncorrected one.
    """
    je = JournalEntry.objects.create(
        entry_date=voucher.voucher_date,
        description=(
            f'Petty cash voucher {voucher.voucher_number} — '
            f'{voucher.payee} — {voucher.description[:80]}'
        ),
        source_type='petty_cash_voucher',
        source_id=voucher.pk,
        journal_type=JournalEntry.JournalType.CASH_PAYMENTS,
        company=_je_company(voucher.location),
        currency_code_id='BWP',
        exchange_rate=Decimal('1.0'),
        created_by=user,
        status=JournalEntry.Status.DRAFT,
    )
    JournalEntryLine.objects.create(
        journal_entry=je,
        account=voucher.expense_account,
        description=(
            f'Petty cash: {voucher.payee} — {voucher.description[:120]}'
        ),
        debit_amount=voucher.amount,
        credit_amount=ZERO,
        debit_bwp=voucher.amount,
        credit_bwp=ZERO,
    )
    JournalEntryLine.objects.create(
        journal_entry=je,
        account=voucher.location.petty_cash_account,
        description=f'Disbursement from {voucher.location.name}',
        debit_amount=ZERO,
        credit_amount=voucher.amount,
        debit_bwp=ZERO,
        credit_bwp=voucher.amount,
    )
    je.post(user=user, _allow_direct=True)
    return je


@transaction.atomic
def approve_voucher(voucher: PettyCashVoucher, user: User) -> PettyCashVoucher:
    """Add a signature. Group tins need TWO signatures: first
    PENDING_APPROVAL -> ONE_SIGNATURE (no JE yet), second by a DIFFERENT
    eligible signer -> POSTED, cutting the DR expense / CR petty cash JE.
    A ring-fenced tin may need only ONE (Unicoin) — the single approval posts
    immediately (see _signatures_required).

    SoD: a signer must differ from the submitter AND the creator; on a
    two-signature tin the second signer must also differ from the first.
    """
    # Concurrency guard: lock the voucher row and re-read its committed status
    # before doing anything. @transaction.atomic gives atomicity, not isolation
    # against a read-modify-write race — without this lock a double-click / two
    # approvers both read PENDING_APPROVAL and BOTH build+post a JE, double-
    # counting the expense (there is no unique (source_type, source_id) on
    # JournalEntry to catch it). select_for_update blocks the second caller
    # until the first commits, after which it sees POSTED and aborts below.
    locked = (
        PettyCashVoucher.objects
        .select_for_update()
        .filter(pk=voucher.pk)
        .first()
    )
    if locked is not None:
        voucher = locked
    if voucher.status not in (PettyCashVoucher.Status.PENDING_APPROVAL,
                              PettyCashVoucher.Status.ONE_SIGNATURE):
        raise ValidationError(
            f'Only vouchers in the signature chain can be signed. Current '
            f'status: {voucher.get_status_display()}.'
        )
    # The GL account is set by the finance team WHEN they post (CFO directive
    # 2026-08-10) — the requester leaves it blank. A voucher cannot be signed
    # until it has been coded: refuse clearly (null-safe) rather than crash on
    # a None account, and tell the signer to code it first via Amend.
    if voucher.expense_account_id is None:
        raise ValidationError({
            'expense_account': 'This voucher has not been coded yet. Set the '
                               'expense GL account first (use "Amend amount / '
                               'GL"), then sign.',
        })
    # Re-assert the GL classification at post time — a draft can be edited
    # (PUT/PATCH) to point at a non-expense account after create-time
    # validation, so we re-check here rather than trust the create path.
    if voucher.expense_account.account_type != 'expense':
        raise ValidationError({
            'expense_account': 'Voucher must be charged to an expense account.',
        })
    if not _can_approve_voucher(user, voucher):
        raise ValidationError(_approver_gate_message(voucher))
    if voucher.submitted_by_id == user.pk:
        raise ValidationError(
            'Segregation of duties: a signer cannot be the same person '
            'who submitted the voucher.'
        )
    if voucher.created_by_id == user.pk:
        raise ValidationError(
            'Segregation of duties: a signer cannot be the same person '
            'who created the voucher.'
        )

    # ---- Single-signature tins (e.g. Unicoin): one approval posts. ----
    # The CFO named a preferred approver with a fallback, so a single sign-off
    # releases the voucher. SoD still holds (signer != creator/submitter, checked
    # above); there is no second-signer stage. (CFO directive 2026-08-31.)
    if _signatures_required(voucher) == 1:
        cash_after = voucher.location.cash_on_hand() - voucher.amount
        if cash_after < ZERO:
            raise ValidationError(
                f'Approving this voucher would push physical cash on hand '
                f'below zero (would be P{cash_after}). Run a reimbursement first.'
            )
        je = _post_voucher_je(voucher, user)
        voucher.status = PettyCashVoucher.Status.POSTED
        voucher.approved_by = user
        voucher.approved_at = timezone.now()
        voucher.journal_entry = je
        voucher.save(
            audit_user=user,
            audit_description=f'Approved + posted {voucher.voucher_number} (single approval)',
        )
        from core.notifications import close_petty_cash_tasks
        close_petty_cash_tasks(voucher, 'approved')
        return voucher

    # ---- First signature: record it and wait for a second. No JE yet. ----
    if voucher.status == PettyCashVoucher.Status.PENDING_APPROVAL:
        voucher.status = PettyCashVoucher.Status.ONE_SIGNATURE
        voucher.first_approved_by = user
        voucher.first_approved_at = timezone.now()
        voucher.save(
            audit_user=user,
            audit_description=f'First signature on {voucher.voucher_number}',
        )
        return voucher

    # ---- Second signature: must be a different person; this one posts. ----
    if voucher.first_approved_by_id == user.pk:
        raise ValidationError(
            'Segregation of duties: the second signature must be a different '
            'person from the first signer.'
        )

    # Cash-on-hand sanity — prevents a stack of pre-approved vouchers from
    # collectively exceeding the float.
    cash_after = voucher.location.cash_on_hand() - voucher.amount
    if cash_after < ZERO:
        raise ValidationError(
            f'Approving this voucher would push physical cash on hand '
            f'below zero (would be P{cash_after}). Run a reimbursement '
            f'first.'
        )

    # Build and post the journal entry.
    je = _post_voucher_je(voucher, user)

    voucher.status = PettyCashVoucher.Status.POSTED
    voucher.approved_by = user
    voucher.approved_at = timezone.now()
    voucher.journal_entry = je
    voucher.save(
        audit_user=user,
        audit_description=f'Approved + posted {voucher.voucher_number}',
    )
    # Approved — close the handlers'/CFO review tasks so they don't linger.
    from core.notifications import close_petty_cash_tasks
    close_petty_cash_tasks(voucher, 'approved')
    return voucher


# ---------------------------------------------------------------------------
# Voucher — reject / reopen
# ---------------------------------------------------------------------------

@transaction.atomic
def reject_voucher(voucher: PettyCashVoucher, user: User, reason: str) -> PettyCashVoucher:
    if voucher.status not in (PettyCashVoucher.Status.PENDING_APPROVAL,
                              PettyCashVoucher.Status.ONE_SIGNATURE):
        raise ValidationError(
            f'Only vouchers in the signature chain can be rejected. Current '
            f'status: {voucher.get_status_display()}.'
        )
    if not _can_approve_voucher(user, voucher):
        raise ValidationError(_approver_gate_message(voucher))
    # SoD symmetry with approve_voucher: the maker who raised/submitted a
    # voucher cannot be the checker who dispositions it — reject is an
    # approval-authority action too, so the same person must not self-action.
    if voucher.submitted_by_id == user.pk or voucher.created_by_id == user.pk:
        raise ValidationError(
            'Segregation of duties: the person who created or submitted the '
            'voucher cannot also reject it — a different approver must.'
        )
    if not (reason or '').strip():
        raise ValidationError({'reason': 'A rejection reason is required.'})

    voucher.status = PettyCashVoucher.Status.REJECTED
    voucher.rejection_reason = reason.strip()
    voucher.approved_by = user  # reuse the field — it's the actioner
    voucher.approved_at = timezone.now()
    voucher.save(
        audit_user=user,
        audit_description=f'Rejected {voucher.voucher_number}: {reason[:80]}',
    )
    # Rejected — cancel the open review tasks.
    from core.notifications import close_petty_cash_tasks
    close_petty_cash_tasks(voucher, 'rejected')
    return voucher


@transaction.atomic
def reopen_voucher(voucher: PettyCashVoucher, user: User) -> PettyCashVoucher:
    """REJECTED -> DRAFT. Only the creator may reopen."""
    if voucher.status != PettyCashVoucher.Status.REJECTED:
        raise ValidationError(
            f'Only rejected vouchers can be reopened. Current status: '
            f'{voucher.get_status_display()}.'
        )
    if voucher.created_by_id != user.pk and not getattr(user, 'is_superuser', False):
        raise ValidationError(
            'Only the voucher creator may reopen a rejected voucher.'
        )

    voucher.status = PettyCashVoucher.Status.DRAFT
    voucher.rejection_reason = ''
    voucher.submitted_by = None
    voucher.submitted_at = None
    voucher.approved_by = None
    voucher.approved_at = None
    voucher.save(
        audit_user=user,
        audit_description=f'Reopened {voucher.voucher_number}',
    )
    return voucher


# ---------------------------------------------------------------------------
# Reimbursement — preview / create / post
# ---------------------------------------------------------------------------

def _eligible_vouchers(location, period_start, period_end, *, lock=False):
    qs = (
        location.vouchers
        .filter(
            status=PettyCashVoucher.Status.POSTED,
            reimbursement__isnull=True,
            voucher_date__gte=period_start,
            voucher_date__lte=period_end,
        )
        .order_by('voucher_date', 'voucher_number')
    )
    # lock=True: take a row lock on each eligible voucher so two reimbursements
    # posting concurrently cannot both sweep the same POSTED vouchers and each
    # credit the bank for the full total (there is no serialisation otherwise
    # under READ COMMITTED).
    if lock:
        qs = qs.select_for_update()
    return qs


def preview_reimbursement(location, period_start: Date, period_end: Date):
    """Return what *would* be reimbursed, without persisting anything."""
    vouchers = list(_eligible_vouchers(location, period_start, period_end))
    total = sum((v.amount for v in vouchers), ZERO)
    return {
        'location': location,
        'period_start': period_start,
        'period_end': period_end,
        'vouchers': vouchers,
        'voucher_count': len(vouchers),
        'total_amount': total,
        'cash_on_hand_before': location.cash_on_hand(),
        'cash_on_hand_after': location.cash_on_hand() + total,
        'float_amount': location.float_amount,
    }


@transaction.atomic
def create_reimbursement(*, location, period_start: Date, period_end: Date,
                         user: User, period=None, notes: str = '') -> PettyCashReimbursement:
    """Create a DRAFT reimbursement. The vouchers aren't locked yet."""
    if period_start > period_end:
        raise ValidationError('period_start must be on or before period_end.')

    vouchers = list(_eligible_vouchers(location, period_start, period_end))
    if not vouchers:
        raise ValidationError(
            f'No reimbursable vouchers at {location.name} between '
            f'{period_start} and {period_end}.'
        )
    total = sum((v.amount for v in vouchers), ZERO)

    reimb = PettyCashReimbursement.objects.create(
        location=location,
        period=period,
        period_start=period_start,
        period_end=period_end,
        total_amount=total,
        voucher_count=len(vouchers),
        notes=(notes or '').strip(),
        status=PettyCashReimbursement.Status.DRAFT,
        created_by=user,
    )
    return reimb


@transaction.atomic
def submit_reimbursement(reimb: PettyCashReimbursement, user: User) -> PettyCashReimbursement:
    """DRAFT -> PENDING_FM. The custodian/maker sends the top-up for review."""
    if reimb.status not in (PettyCashReimbursement.Status.DRAFT,
                            PettyCashReimbursement.Status.REJECTED):
        raise ValidationError(
            f'Only a draft (or rejected) reimbursement can be submitted for '
            f'review. Current status: {reimb.get_status_display()}.'
        )
    # Confirm there is still something to reimburse before it enters review.
    if not _eligible_vouchers(reimb.location, reimb.period_start, reimb.period_end).exists():
        raise ValidationError(
            'No reimbursable vouchers in scope any more — they may have been '
            'swept by an earlier reimbursement. Discard and re-create.'
        )
    reimb.status = PettyCashReimbursement.Status.PENDING_FM
    reimb.submitted_by = user
    reimb.submitted_at = timezone.now()
    reimb.rejection_reason = ''
    reimb.save(audit_user=user,
               audit_description=f'Submitted {reimb.reimbursement_number} for FM review')
    return reimb


@transaction.atomic
def fm_review_reimbursement(reimb: PettyCashReimbursement, user: User) -> PettyCashReimbursement:
    """PENDING_FM -> PENDING_CFO. The Finance Manager reviews and passes it up."""
    if reimb.status != PettyCashReimbursement.Status.PENDING_FM:
        raise ValidationError(
            f'Only a reimbursement awaiting FM review can be reviewed. Current '
            f'status: {reimb.get_status_display()}.'
        )
    if not _can_fm_review(user):
        raise ValidationError(
            'FM review must be done by a Finance Manager or Financial Controller.'
        )
    # SoD: the reviewer cannot be the person who created or submitted it.
    if reimb.created_by_id == user.pk or reimb.submitted_by_id == user.pk:
        raise ValidationError(
            'Segregation of duties: the person who created or submitted the '
            'reimbursement cannot also FM-review it.'
        )
    reimb.status = PettyCashReimbursement.Status.PENDING_CFO
    reimb.fm_reviewed_by = user
    reimb.fm_reviewed_at = timezone.now()
    reimb.save(audit_user=user,
               audit_description=f'FM-reviewed {reimb.reimbursement_number} — to CFO')
    return reimb


@transaction.atomic
def reject_reimbursement(reimb: PettyCashReimbursement, user: User, reason: str) -> PettyCashReimbursement:
    """PENDING_FM or PENDING_CFO -> REJECTED. Either reviewer can bounce it back
    to the maker with a reason."""
    if reimb.status == PettyCashReimbursement.Status.PENDING_FM:
        if not _can_fm_review(user):
            raise ValidationError('Only a Finance Manager / Financial Controller can reject at FM review.')
    elif reimb.status == PettyCashReimbursement.Status.PENDING_CFO:
        if not _can_cfo_approve(user):
            raise ValidationError('Only the CFO can reject at CFO approval.')
    else:
        raise ValidationError(
            f'Only a reimbursement in review can be rejected. Current status: '
            f'{reimb.get_status_display()}.'
        )
    if not (reason or '').strip():
        raise ValidationError({'reason': 'A rejection reason is required.'})
    reimb.status = PettyCashReimbursement.Status.REJECTED
    reimb.rejection_reason = reason.strip()
    reimb.save(audit_user=user,
               audit_description=f'Rejected {reimb.reimbursement_number}: {reason[:80]}')
    return reimb


@transaction.atomic
def reopen_reimbursement(reimb: PettyCashReimbursement, user: User) -> PettyCashReimbursement:
    """REJECTED -> DRAFT. The creator reworks a bounced reimbursement."""
    if reimb.status != PettyCashReimbursement.Status.REJECTED:
        raise ValidationError(
            f'Only a rejected reimbursement can be reopened. Current status: '
            f'{reimb.get_status_display()}.'
        )
    if reimb.created_by_id != user.pk and not getattr(user, 'is_superuser', False):
        raise ValidationError('Only the reimbursement creator may reopen it.')
    reimb.status = PettyCashReimbursement.Status.DRAFT
    reimb.submitted_by = None
    reimb.submitted_at = None
    reimb.fm_reviewed_by = None
    reimb.fm_reviewed_at = None
    reimb.rejection_reason = ''
    reimb.save(audit_user=user,
               audit_description=f'Reopened {reimb.reimbursement_number}')
    return reimb


@transaction.atomic
def post_reimbursement(reimb: PettyCashReimbursement, user: User) -> PettyCashReimbursement:
    """PENDING_CFO -> POSTED. The CFO's final "approve in the bank" step: posts
    the DR petty cash / CR bank JE and locks every swept voucher into REIMBURSED."""
    # Concurrency guard: lock the reimbursement row and re-read committed
    # status. Without it, two approvers posting the same reimbursement both
    # read PENDING_CFO and each post a full-total bank credit. select_for_update
    # serialises them; the loser sees POSTED and aborts below.
    locked = (
        PettyCashReimbursement.objects
        .select_for_update()
        .filter(pk=reimb.pk)
        .first()
    )
    if locked is not None:
        reimb = locked
    if reimb.status != PettyCashReimbursement.Status.PENDING_CFO:
        raise ValidationError(
            f'Only a CFO-stage reimbursement can be posted to the bank. Current '
            f'status: {reimb.get_status_display()}. It must pass FM review first.'
        )
    if not _can_cfo_approve(user):
        raise ValidationError(
            'The final bank posting must be approved by the CFO (or a Django '
            'superuser).'
        )
    # SoD across the whole chain: the CFO poster must differ from the creator,
    # the submitter, AND the FM reviewer.
    uid = getattr(user, 'pk', None)
    is_su = getattr(user, 'is_superuser', False)
    if not is_su and uid in (reimb.created_by_id, reimb.submitted_by_id, reimb.fm_reviewed_by_id):
        raise ValidationError(
            'Segregation of duties: whoever created, submitted, or FM-reviewed '
            'the reimbursement cannot also post it — the CFO approving must be a '
            'different person.'
        )

    # Re-compute the eligible set inside the transaction to defend against
    # races (e.g. another voucher approved while this draft sat open), and
    # row-lock each voucher so a concurrent reimbursement cannot sweep the
    # same ones.
    vouchers = list(_eligible_vouchers(
        reimb.location, reimb.period_start, reimb.period_end, lock=True,
    ))
    if not vouchers:
        raise ValidationError(
            'No reimbursable vouchers in scope any more — they may have '
            'been swept by an earlier reimbursement. Cancel and re-create.'
        )
    total = sum((v.amount for v in vouchers), ZERO)

    je = JournalEntry.objects.create(
        entry_date=reimb.reimbursement_date,
        description=(
            f'Petty cash reimbursement {reimb.reimbursement_number} — '
            f'{reimb.location.name} — {len(vouchers)} vouchers'
        ),
        source_type='petty_cash_reimbursement',
        source_id=reimb.pk,
        journal_type=JournalEntry.JournalType.CASH_PAYMENTS,
        company=_je_company(reimb.location),
        currency_code_id='BWP',
        exchange_rate=Decimal('1.0'),
        created_by=user,
        status=JournalEntry.Status.DRAFT,
    )
    JournalEntryLine.objects.create(
        journal_entry=je,
        account=reimb.location.petty_cash_account,
        description=f'Top-up of {reimb.location.name} float',
        debit_amount=total,
        credit_amount=ZERO,
        debit_bwp=total,
        credit_bwp=ZERO,
    )
    JournalEntryLine.objects.create(
        journal_entry=je,
        account=reimb.location.reimbursing_bank_account,
        description=(
            f'Petty cash reimbursement {reimb.reimbursement_number}'
        ),
        debit_amount=ZERO,
        credit_amount=total,
        debit_bwp=ZERO,
        credit_bwp=total,
    )
    je.post(user=user, _allow_direct=True)

    # Lock every voucher under this reimbursement
    for v in vouchers:
        v.status = PettyCashVoucher.Status.REIMBURSED
        v.reimbursement = reimb
        v.save(
            audit_user=user,
            audit_description=(
                f'Swept into reimbursement {reimb.reimbursement_number}'
            ),
        )

    reimb.total_amount = total
    reimb.voucher_count = len(vouchers)
    reimb.status = PettyCashReimbursement.Status.POSTED
    reimb.posted_by = user
    reimb.posted_at = timezone.now()
    reimb.journal_entry = je
    reimb.save(
        audit_user=user,
        audit_description=f'Posted {reimb.reimbursement_number}',
    )
    return reimb
