"""
nbfira/workflow.py — return state transitions + role gates.

CFO directive (NBFIRA_MODULE_BLUEPRINT.md Phase 4).

States:
    draft → reviewed → approved → locked → submitted
            ↓rejected
            ↓reopened (from any non-locked state)

Roles (mapped onto existing UserProfile.title where possible; new
'compliance_officer' / 'compliance_reviewer' roles can be added by
RBAC admin without code change — workflow reads `title` + the existing
`is_administrator` / superuser flags):

  prepare  — compliance_officer, accountant, finance_analyst, cfo
  review   — compliance_officer, compliance_reviewer, finance_manager,
             financial_controller, cfo, auditor
  approve  — compliance_officer, finance_manager, financial_controller,
             cfo
  lock     — cfo (only) + superuser
  submit   — cfo, financial_controller
  reject   — review-eligible
  reopen   — approve-eligible
"""

from __future__ import annotations

from django.utils import timezone

from core.models import get_user_profile

from .models import NBFIRAReturn


def _title(user) -> str:
    p = get_user_profile(user)
    return (getattr(p, 'title', '') or '').lower()


def _is_admin(user) -> bool:
    if getattr(user, 'is_superuser', False):
        return True
    p = get_user_profile(user)
    return bool(p and getattr(p, 'is_administrator', False))


CAN_PREPARE = frozenset({'compliance_officer', 'accountant', 'finance_analyst',
                         'financial_controller', 'finance_manager', 'cfo'})
CAN_REVIEW  = frozenset({'compliance_officer', 'compliance_reviewer',
                         'finance_manager', 'financial_controller',
                         'cfo', 'auditor'})
CAN_APPROVE = frozenset({'compliance_officer', 'finance_manager',
                         'financial_controller', 'cfo'})
CAN_LOCK    = frozenset({'cfo'})
CAN_SUBMIT  = frozenset({'cfo', 'financial_controller'})


def _check(user, allowed: frozenset, label: str):
    if _is_admin(user):
        return
    title = _title(user)
    if title not in allowed:
        from rest_framework.exceptions import PermissionDenied
        raise PermissionDenied(
            f'{label} requires title ∈ {sorted(allowed)} (you are: {title or "—"}).'
        )


def _sod(actor_field: str, ret: NBFIRAReturn, user, label: str):
    """Same-person SoD guard: same user cannot fill two adjacent roles."""
    other = getattr(ret, actor_field + '_id', None)
    if other is not None and other == user.id and not _is_admin(user):
        from rest_framework.exceptions import PermissionDenied
        raise PermissionDenied(
            f'Segregation of duties: you already filled the {actor_field} '
            f'slot — cannot also {label}.'
        )


# ─── Transitions ─────────────────────────────────────────────────────────
def review(ret: NBFIRAReturn, user, comment: str = ''):
    _check(user, CAN_REVIEW, 'Review')
    if ret.status not in (NBFIRAReturn.Status.DRAFT, NBFIRAReturn.Status.REOPENED):
        raise ValueError(f'Cannot review a {ret.status} return.')
    _sod('initiated_by', ret, user, 'review')
    ret.status = NBFIRAReturn.Status.REVIEWED
    ret.reviewed_by = user
    ret.reviewed_at = timezone.now()
    ret.save(audit_user=user, audit_description=f'Reviewed. {comment[:200]}'.strip())


def approve(ret: NBFIRAReturn, user, comment: str = ''):
    _check(user, CAN_APPROVE, 'Approve')
    if ret.status != NBFIRAReturn.Status.REVIEWED:
        raise ValueError(f'Cannot approve a {ret.status} return.')
    _sod('reviewed_by', ret, user, 'approve')
    _sod('initiated_by', ret, user, 'approve')
    ret.status = NBFIRAReturn.Status.APPROVED
    ret.approved_by = user
    ret.approved_at = timezone.now()
    ret.save(audit_user=user, audit_description=f'Approved. {comment[:200]}'.strip())


def reject(ret: NBFIRAReturn, user, comment: str):
    _check(user, CAN_REVIEW, 'Reject')
    if not comment.strip():
        raise ValueError('Rejection requires a comment.')
    if ret.status in (NBFIRAReturn.Status.LOCKED, NBFIRAReturn.Status.SUBMITTED):
        raise ValueError(f'Cannot reject a {ret.status} return.')
    ret.status = NBFIRAReturn.Status.REJECTED
    ret.save(audit_user=user, audit_description=f'Rejected. {comment[:200]}')


def reopen(ret: NBFIRAReturn, user, comment: str):
    _check(user, CAN_APPROVE, 'Reopen')
    if not comment.strip():
        raise ValueError('Reopen requires a comment.')
    if ret.status in (NBFIRAReturn.Status.LOCKED, NBFIRAReturn.Status.SUBMITTED):
        raise ValueError(f'Cannot reopen a {ret.status} return — locked.')
    ret.status = NBFIRAReturn.Status.REOPENED
    ret.save(audit_user=user, audit_description=f'Reopened. {comment[:200]}')


def lock(ret: NBFIRAReturn, user, comment: str = ''):
    _check(user, CAN_LOCK, 'Lock')
    if ret.status != NBFIRAReturn.Status.APPROVED:
        raise ValueError(f'Cannot lock a {ret.status} return.')
    ret.status = NBFIRAReturn.Status.LOCKED
    ret.locked_by = user
    ret.locked_at = timezone.now()
    ret.save(audit_user=user, audit_description=f'Locked. {comment[:200]}'.strip())


def submit(ret: NBFIRAReturn, user, comment: str = '',
           filing_reference: str = '', acknowledgement_ref: str = ''):
    _check(user, CAN_SUBMIT, 'Submit')
    if ret.status != NBFIRAReturn.Status.LOCKED:
        raise ValueError(f'Cannot submit a {ret.status} return — must be locked first.')
    ret.status = NBFIRAReturn.Status.SUBMITTED
    ret.submitted_by = user
    ret.submitted_at = timezone.now()
    ret.save(audit_user=user, audit_description=f'Submitted. {comment[:200]}'.strip())

    # Create / update the submission record
    from .models import NBFIRASubmission
    sub, _ = NBFIRASubmission.objects.update_or_create(
        return_obj=ret,
        defaults=dict(
            submitted_by=user,
            filing_reference=filing_reference,
            acknowledgement_ref=acknowledgement_ref,
        ),
    )
    return sub
