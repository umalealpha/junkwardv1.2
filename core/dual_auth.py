"""
core/dual_auth.py

Generic helpers for dual-authorisation on import batches. Used by:
  - assets.AssetImportBatch
  - claims.RecoveryImportBatch
  - (future) payroll.PayrollImportBatch

Rules:
  - Approver must hold an approver title (CFO / Finance Manager /
    Financial Controller) — same set as journal-entry approvers.
  - The two approvers must be different users.
  - The batch creator may NOT be one of the approvers (segregation of
    duties — same as JE workflow).
  - Once partially approved, only the SECOND approver can advance the
    batch to APPROVED. Either approver can REJECT.
"""

from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import get_user_profile


def _is_eligible_approver(user) -> bool:
    if getattr(user, 'is_superuser', False):
        return True
    profile = get_user_profile(user)
    return bool(profile and profile.can_approve_journal_entries)


def record_approval(batch, user, *, partial_status, approved_status):
    """
    Apply *user*'s approval to *batch* and advance status.

    *partial_status* and *approved_status* are status enum VALUES on the
    target batch model (the import models have differing enum classes,
    but the values are the same strings).

    Raises ValidationError on any rule violation.
    """
    if batch.status not in ('draft', 'partially_approved'):
        raise ValidationError(
            f'Batch is {batch.status} — approvals are only valid while draft or partially_approved.'
        )

    if not _is_eligible_approver(user):
        raise ValidationError(
            'Approval requires CFO, Finance Manager, or Financial Controller title.'
        )

    if batch.created_by_id == user.id:
        raise ValidationError(
            'Segregation of duties: the user who uploaded the batch cannot also approve it. '
            'Ask another approver.'
        )

    now = timezone.now()

    if batch.first_approved_by_id is None:
        # First slot
        batch.first_approved_by = user
        batch.first_approved_at = now
        batch.status = partial_status
        # Notify other approvers that a second sign-off is needed.
        try:
            from .notifications import notify_import_partially_approved
            cls_name = type(batch).__name__
            kind_label, deep_link = {
                'AssetImportBatch':    ('Asset',    f'/assets/imports/{batch.id}'),
                'RecoveryImportBatch': ('Recovery', f'/claims/imports/{batch.id}'),
                'PayrollImportBatch':  ('Payroll',  f'/payroll/imports/{batch.id}'),
            }.get(cls_name, ('Import', '/'))
            notify_import_partially_approved(batch, kind_label, deep_link)
        except Exception:  # noqa: BLE001
            pass
    else:
        # Second slot
        if batch.first_approved_by_id == user.id:
            raise ValidationError(
                'You already provided the first approval — a second approver must sign off.'
            )
        batch.second_approved_by = user
        batch.second_approved_at = now
        batch.status = approved_status

    return batch


def record_rejection(batch, user, reason: str, *, rejected_status):
    if batch.status in ('committed', 'rejected', 'failed'):
        raise ValidationError(f'Batch is {batch.status} — cannot reject.')
    if not _is_eligible_approver(user):
        raise ValidationError(
            'Rejection requires CFO, Finance Manager, or Financial Controller title.'
        )
    if not reason or not reason.strip():
        raise ValidationError('A rejection reason is required.')

    batch.status           = rejected_status
    batch.rejected_by      = user
    batch.rejected_at      = timezone.now()
    batch.rejection_reason = reason.strip()
    return batch


def assert_ready_to_commit(batch):
    """Raise unless the batch has both approvals from distinct, eligible users."""
    if batch.status != 'approved':
        raise ValidationError(
            f'Batch is {batch.status}. Both approvers must sign off before commit.'
        )
    if not batch.is_fully_approved:
        raise ValidationError('Dual authorisation incomplete.')
