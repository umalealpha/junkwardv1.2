"""reinsurance/onboarding.py — the reinsurer approval state machine.

Arun P. Iyer's control brief, 15-Sep-2026. Reinsurer security is being treated
as a core ERM risk, so who a counterparty is, and who said we may use them, has
to be decided in one place and enforced on the server. The frontend mirrors
these rules for the sake of the buttons; it is not what stops anything.

THE CHAIN
    DRAFT
      → PENDING_UW_MANAGER      submitted by an underwriter
      → PENDING_COMPLIANCE      underwriting approved
      → PENDING_PRINCIPAL       KYC / AML / sanctions / PEP completed
      → PENDING_CEO             principal & operations review (Paul Beka ONLY)
      → APPROVED                final approval (CEO Arun P. Iyer ONLY)

    RETURNED / REJECTED / SUSPENDED may be reached from any live stage, each
    with a mandatory reason. EXPIRED is reached by the passage of time.

WHAT IS DELIBERATELY NOT NEGOTIABLE HERE
  * No stage-skipping. A move is legal only if the target is in the transition
    table for the current status, so "approve" cannot jump the queue.
  * No self-approval. The person who submitted cannot approve their own
    counterparty, at any stage.
  * The last two stages are gated on IDENTITY as well as permission. A person
    holding the right role is not enough: the brief names Paul Beka and the CEO
    by account, because these two stages exist to put two specific people's
    names on the decision. A superuser passes the permission check by design
    (see core.models.user_has_permission) — the identity check is what makes
    that harmless here.
  * Every transition is a row, with actor, time and comment, plus an AuditLog
    entry. A control nobody can reconstruct afterwards is not a control.
  * The row is locked for the duration (select_for_update inside a transaction),
    so two approvals landing together cannot both read the same "before" state.
"""
from __future__ import annotations

import logging
from typing import Iterable

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import user_has_permission
from reinsurance.models import Reinsurer, ReinsurerApprovalTransition

log = logging.getLogger(__name__)

S = Reinsurer.ApprovalStatus

#: The named accounts for the last two stages. Overridable in settings so a
#: change of postholder is a configuration change, not a deploy — and so the
#: tests can run without inventing a real person's login.
PRINCIPAL_EMAIL = getattr(
    settings, 'REINSURANCE_PRINCIPAL_EMAIL', 'pbeka@alphadirect.co.bw').lower()
CEO_EMAIL = getattr(
    settings, 'REINSURANCE_CEO_EMAIL', 'aiyer@alphadirect.co.bw').lower()

#: status → the statuses it may move to. Anything absent is refused.
TRANSITIONS: dict[str, tuple[str, ...]] = {
    S.DRAFT: (S.PENDING_UW_MANAGER,),
    S.PENDING_UW_MANAGER: (S.PENDING_COMPLIANCE, S.RETURNED, S.REJECTED),
    S.PENDING_COMPLIANCE: (S.PENDING_PRINCIPAL, S.RETURNED, S.REJECTED),
    S.PENDING_PRINCIPAL: (S.PENDING_CEO, S.RETURNED, S.REJECTED),
    S.PENDING_CEO: (S.APPROVED, S.RETURNED, S.REJECTED),
    # EXPIRED is deliberately NOT reachable through the API. It was, and with no
    # entry in REQUIRED_PERMISSION the permission check was skipped entirely, so
    # any signed-in user with no roles at all could POST {"target": "expired"}
    # and knock a live reinsurer off the panel (proved over HTTP, 200, by the
    # Fable review 15-Sep-2026). Only expire_due() sets it, on the date.
    S.APPROVED: (S.SUSPENDED, S.RETURNED),
    S.RETURNED: (S.PENDING_UW_MANAGER, S.REJECTED),
    S.SUSPENDED: (S.APPROVED, S.REJECTED),
    S.EXPIRED: (S.PENDING_UW_MANAGER,),
    S.REJECTED: (S.PENDING_UW_MANAGER,),
}

#: The permission each target stage requires.
REQUIRED_PERMISSION: dict[str, str] = {
    S.PENDING_UW_MANAGER: 'uw.counterparty.submit',
    S.PENDING_COMPLIANCE: 'uw.counterparty.approve',
    S.PENDING_PRINCIPAL: 'compliance.counterparty.approve',
    S.PENDING_CEO: 're.counterparty.approve_principal',
    S.APPROVED: 're.counterparty.approve_ceo',
    S.RETURNED: 're.view',
    S.REJECTED: 're.counterparty.approve_principal',
    S.SUSPENDED: 're.counterparty.approve_principal',
}

#: Stages that additionally require one NAMED person.
REQUIRED_IDENTITY: dict[str, str] = {
    S.PENDING_CEO: PRINCIPAL_EMAIL,   # Paul Beka signs off before it reaches the CEO
    S.APPROVED: CEO_EMAIL,            # Arun P. Iyer gives the final approval
}

#: Moves whose reason is mandatory. A block with no stated reason is the thing
#: people escalate around, so the service refuses it rather than storing ''.
REASON_REQUIRED: tuple[str, ...] = (S.RETURNED, S.REJECTED, S.SUSPENDED)

#: Evidence that must be on file before a counterparty can leave compliance.
#: Kept as a plain list of missing-item sentences so the UI can show WHAT is
#: missing rather than a bare "blocked".
def missing_prerequisites(reinsurer: Reinsurer, target: str) -> list[str]:
    """Data that has to be present before this move is allowed.

    The KYC document register (16-Sep-2026) adds its checks HERE, exactly as
    this docstring promised, and nowhere else — one place that answers "what is
    missing", so the screen, the refused click and the audit line use the same
    words.

    Note what is NOT here: nothing in this function runs against an already
    imported treaty, cession or FAC row. It is called on a TRANSITION only, so a
    legacy counterparty with an empty document file keeps servicing and posting
    exactly as before. The gap is shown, loudly; it does not stop the business.
    """
    from reinsurance.models import ReinsurerSecurityAssessment, evidence_gaps

    missing: list[str] = []
    if target == S.PENDING_COMPLIANCE:
        if not (reinsurer.legal_name or reinsurer.registered_name):
            missing.append('the legal or registered name')
        if not reinsurer.domicile:
            missing.append('the country of domicile')
        if not reinsurer.onboarding_purposes:
            missing.append('what they are being onboarded for '
                           '(facultative / treaty / retrocession)')
    if target == S.PENDING_PRINCIPAL:
        # `.exists()` alone was "deficient evidence acceptance" (QC 3885a3ec):
        # a blank stub — no rating, no scale, no evidence_date, unverified —
        # satisfied it exactly as well as a real assessment. Compliance is the
        # stage that signs the rating off, so it needs an assessment a person
        # has actually VERIFIED against source evidence, carrying the rating,
        # scale and evidence date the missing-item sentence promises. History
        # is kept and never overwritten (models.py), so this checks whether an
        # adequate one exists at all — not merely the most recent row.
        adequate = reinsurer.security_assessments.filter(
            verified=True, evidence_date__isnull=False,
        ).exclude(rating='').exclude(
            rating_scale=ReinsurerSecurityAssessment.Scale.UNKNOWN,
        ).exists()
        if not adequate:
            missing.append('a security assessment that has been VERIFIED and '
                           'carries a rating, scale and evidence date — an '
                           'unverified or incomplete one does not count')
        # Compliance is the stage that signs KYC off, so this is the stage the
        # evidence has to exist by. "Uploaded" does not count — only a document
        # Compliance has marked VERIFIED, and that has not expired, is evidence.
        missing.extend(evidence_gaps(reinsurer))
    if target == S.APPROVED:
        if not reinsurer.expiry_date:
            missing.append('an approval expiry date, so the review actually falls due')
    return missing


class TransitionRefused(ValidationError):
    """The move is not legal. Carries a sentence a person can act on."""


def _email_of(user) -> str:
    return (getattr(user, 'email', '') or '').strip().lower()


def can_transition(user, reinsurer: Reinsurer, target: str) -> str:
    """Why `user` may NOT move `reinsurer` to `target` — or '' if they may.

    A sentence rather than a boolean so the API, the UI and the audit trail all
    say the same thing, and so a refused click explains itself.
    """
    current = reinsurer.approval_status
    allowed = TRANSITIONS.get(current, ())
    if target not in allowed:
        return (f'A counterparty at "{reinsurer.get_approval_status_display()}" '
                f'cannot move straight to "{dict(S.choices).get(target, target)}".')

    perm = REQUIRED_PERMISSION.get(target)
    # Returning something still in the queue is ordinary reviewer work. Pulling
    # an APPROVED counterparty back is undoing a decision the CEO signed, so it
    # takes the principal's authority — otherwise anyone holding plain re.view
    # (which the bordereau processors hold) can reverse it.
    if target == S.RETURNED and current == S.APPROVED:
        perm = 're.counterparty.approve_principal'
    if perm is None:
        return ('That step cannot be made from here — it is set by the system, '
                'not by a person.')
    if not user_has_permission(user, perm):
        return 'You do not have permission to make that approval.'

    wanted = REQUIRED_IDENTITY.get(target)
    if wanted:
        who = ('Paul Beka' if wanted == PRINCIPAL_EMAIL else 'the CEO')
        if _email_of(user) != wanted:
            return (f'Only {who} can give that approval, from their own Omni '
                    f'account.')
        if not getattr(user, 'is_active', False):
            return f'{who}\'s Omni account is not active.'
        # An e-mail address is not an identity: User.email is neither unique nor
        # immutable here, and a Super Admin can set his own to anything through
        # the users API. Two accounts sharing the address makes the gate a coin
        # toss, so it refuses rather than picking one (H54).
        from django.contrib.auth.models import User as _U
        matches = _U.objects.filter(email__iexact=wanted, is_active=True).count()
        if matches != 1:
            return (f'{who}\'s approval cannot be taken: {matches} active Omni '
                    f'accounts carry that e-mail address. Fix the duplicate '
                    f'before this approval can be given.')

    # Segregation of duties: whoever put it forward cannot wave it through.
    approving = target in (S.PENDING_COMPLIANCE, S.PENDING_PRINCIPAL,
                           S.PENDING_CEO, S.APPROVED)
    if approving and reinsurer.submitted_by_id and user is not None:
        if reinsurer.submitted_by_id == getattr(user, 'id', None):
            return ('You submitted this counterparty, so you cannot also '
                    'approve it. It needs a second person.')

    missing = missing_prerequisites(reinsurer, target)
    if missing:
        return ('Before that step this counterparty still needs: '
                + ', '.join(missing) + '.')
    return ''


@transaction.atomic
def transition(user, reinsurer: Reinsurer, target: str, comment: str = '') -> Reinsurer:
    """Move a counterparty to `target`, or raise.

    Locks the row first, so two people approving at the same moment cannot both
    act on the same "before" state.
    """
    locked = (Reinsurer.objects.select_for_update()
              .get(pk=reinsurer.pk))
    comment = (comment or '').strip()

    if target in REASON_REQUIRED and not comment:
        raise TransitionRefused(
            'That step needs a reason — say what has to change, or why the '
            'counterparty is being stopped.')

    reason = can_transition(user, locked, target)
    if reason:
        raise PermissionDenied(reason)

    before = locked.approval_status
    locked.approval_status = target

    now = timezone.now()
    if target == S.PENDING_UW_MANAGER and before in (S.DRAFT, S.RETURNED,
                                                     S.REJECTED, S.EXPIRED):
        locked.submitted_by = user
        locked.submitted_at = now
    if target == S.APPROVED:
        locked.approved_at = now
        locked.suspension_reason = ''
    if target in (S.SUSPENDED, S.REJECTED):
        locked.suspension_reason = comment

    # AuditableMixin.save() writes its own AuditLog row, so this passes the user
    # and the description INTO it rather than writing a second row under a
    # different table name at the same timestamp.
    locked.save(update_fields=['approval_status', 'submitted_by', 'submitted_at',
                               'approved_at', 'suspension_reason', 'updated_at'],
                audit_user=user if getattr(user, 'is_authenticated', False) else None,
                audit_description=(f'Reinsurer onboarding {locked.short_code}: '
                                   f'{before} → {target}'
                                   + (f' — {comment}' if comment else '')))

    ReinsurerApprovalTransition.objects.create(
        reinsurer=locked, from_status=before, to_status=target,
        actor=user if getattr(user, 'is_authenticated', False) else None,
        actor_email=_email_of(user), comment=comment,
        created_at_local=now,
    )

    return locked


def expire_due(on=None) -> int:
    """Move approved counterparties past their expiry date to EXPIRED.

    Date-driven and idempotent. Returns how many moved. Run from a scheduled
    job; deliberately does NOT touch SUSPENDED or REJECTED rows, which are where
    a person put them.
    """
    on = on or timezone.localdate()
    due = Reinsurer.objects.filter(approval_status=S.APPROVED,
                                   expiry_date__lt=on)
    moved = 0
    for r in due:
        with transaction.atomic():
            locked = Reinsurer.objects.select_for_update().get(pk=r.pk)
            if locked.approval_status != S.APPROVED:
                continue
            locked.approval_status = S.EXPIRED
            locked.save(update_fields=['approval_status', 'updated_at'])
            ReinsurerApprovalTransition.objects.create(
                reinsurer=locked, from_status=S.APPROVED, to_status=S.EXPIRED,
                actor=None, actor_email='',
                comment=f'Approval expired on {locked.expiry_date.isoformat()}.',
            )
            moved += 1
    return moved


def assert_placeable(reinsurers: Iterable[Reinsurer], on=None) -> None:
    """Raise unless every counterparty may be put on a new placement.

    The single gate the FAC register and any future treaty/retro placement call
    before writing an allocation.
    """
    blocked = [r.placement_block_reason(on) for r in reinsurers]
    blocked = [b for b in blocked if b]
    if blocked:
        raise TransitionRefused(blocked)
