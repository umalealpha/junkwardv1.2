"""
core/rbac_service.py — service layer for granting and revoking role assignments.

Every role mutation in the system goes through this module. The ORM is NOT
called directly by views or management commands — they call assign_role() and
revoke_role() so that:

  1. Self-grant / self-revoke is blocked
  2. Hierarchy permission is enforced (caller must have can_manage on the target role)
  3. Justification is required for elevated roles (level <= 2)
  4. Expiry is honoured
  5. Every transition writes an immutable AuditLog entry

These rules are governance requirements aligned with NBFIRA's expected control
framework and the project's existing maker-checker / SoD design.

For bootstrap scenarios (initial Super Admin seed, system migrations) pass
`bypass_hierarchy=True` — that path is itself audit-logged as such.
"""

from __future__ import annotations

from typing import Optional

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import AuditLog, Role, UserRoleAssignment, user_can_manage_role

User = get_user_model()

# Roles at or below this level require an explicit justification on grant.
ELEVATED_LEVEL_THRESHOLD = 2


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@transaction.atomic
def assign_role(
    *,
    target_user,
    role: Role,
    granted_by,
    scope_department: Optional[str] = None,
    justification: str = '',
    expires_at=None,
    notes: str = '',
    bypass_hierarchy: bool = False,
    request_ip: Optional[str] = None,
) -> UserRoleAssignment:
    """
    Grant a role to a user, with full governance enforcement.

    Raises:
        ValidationError       — missing data, expired-on-create, duplicate active assignment
        PermissionDenied      — granter cannot manage the target role, or self-grant attempt
    """
    if target_user is None or role is None:
        raise ValidationError('target_user and role are required.')

    # Self-grant block (the single most important governance rule here)
    if granted_by is not None and granted_by.pk == target_user.pk:
        raise PermissionDenied(
            'Self-grant is not permitted. A separate user with appropriate authority must grant this role.'
        )

    # Hierarchy permission — bypass only for bootstrap / migration code paths
    if not bypass_hierarchy:
        if granted_by is None:
            raise PermissionDenied('granted_by is required unless bypass_hierarchy=True.')
        if not user_can_manage_role(granted_by, role):
            raise PermissionDenied(
                f'{granted_by.username} cannot grant role {role.code}: '
                f'insufficient authority or wrong department.'
            )

    # Justification required for elevated roles
    if role.level <= ELEVATED_LEVEL_THRESHOLD and not (justification or '').strip():
        raise ValidationError(
            f'Justification is required for grants of role {role.code} '
            f'(level {role.level}); business reason must be documented.'
        )

    # Expiry sanity check
    if expires_at is not None and expires_at <= timezone.now():
        raise ValidationError('expires_at must be in the future.')

    # Reject if there is already an identical active assignment
    existing = UserRoleAssignment.objects.filter(
        user=target_user, role=role,
        scope_department=scope_department,
        revoked_at__isnull=True,
    ).first()
    if existing and existing.is_currently_active:
        raise ValidationError(
            f'{target_user.username} already holds an active assignment to {role.code}.'
        )

    assignment = UserRoleAssignment.objects.create(
        user=target_user,
        role=role,
        scope_department=scope_department,
        assigned_by=granted_by,
        justification=justification,
        expires_at=expires_at,
        notes=notes,
    )

    AuditLog.objects.create(
        table_name='core.UserRoleAssignment',
        record_id=str(assignment.pk),
        action=AuditLog.Action.CREATE,
        new_values={
            'user_id':          str(target_user.pk),
            'username':         target_user.username,
            'role_code':        role.code,
            'role_level':       role.level,
            'scope_department': scope_department,
            'expires_at':       expires_at.isoformat() if expires_at else None,
            'justification':    justification,
            'bypass_hierarchy': bypass_hierarchy,
        },
        user=granted_by,
        ip_address=request_ip,
        # Bust the per-request cache on target so re-checks see the new assignment
    )
    _bust_cache(target_user)
    return assignment


@transaction.atomic
def revoke_role(
    *,
    assignment: UserRoleAssignment,
    revoked_by,
    reason: str = '',
    bypass_hierarchy: bool = False,
    request_ip: Optional[str] = None,
) -> UserRoleAssignment:
    """
    Revoke an active role assignment, with governance enforcement.

    Raises:
        ValidationError   — already revoked, no reason for elevated-role revoke
        PermissionDenied  — revoker cannot manage the role, or self-revoke attempt
    """
    if assignment is None:
        raise ValidationError('assignment is required.')
    if assignment.revoked_at is not None:
        raise ValidationError('Assignment is already revoked.')

    # Self-revoke is also blocked — handover should be a peer action, not self-managed
    if revoked_by is not None and revoked_by.pk == assignment.user.pk:
        raise PermissionDenied(
            'Self-revoke is not permitted. Ask another authorised user to perform handover.'
        )

    if not bypass_hierarchy:
        if revoked_by is None:
            raise PermissionDenied('revoked_by is required unless bypass_hierarchy=True.')
        if not user_can_manage_role(revoked_by, assignment.role):
            raise PermissionDenied(
                f'{revoked_by.username} cannot revoke role {assignment.role.code}: '
                f'insufficient authority or wrong department.'
            )

    # Reason required for elevated-role revoke (matches grant rule)
    if assignment.role.level <= ELEVATED_LEVEL_THRESHOLD and not (reason or '').strip():
        raise ValidationError(
            f'A reason is required when revoking role {assignment.role.code} '
            f'(level {assignment.role.level}).'
        )

    assignment.revoked_at = timezone.now()
    assignment.revoked_by = revoked_by
    assignment.revocation_reason = reason
    assignment.save(update_fields=['revoked_at', 'revoked_by', 'revocation_reason', 'updated_at'])

    AuditLog.objects.create(
        table_name='core.UserRoleAssignment',
        record_id=str(assignment.pk),
        action=AuditLog.Action.UPDATE,
        new_values={
            'revoked':            True,
            'revocation_reason':  reason,
            'role_code':          assignment.role.code,
            'user_id':            str(assignment.user.pk),
            'username':           assignment.user.username,
            'bypass_hierarchy':   bypass_hierarchy,
        },
        user=revoked_by,
        ip_address=request_ip,
    )
    _bust_cache(assignment.user)
    return assignment


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bust_cache(user):
    """Drop the cached active-assignments list so the next check rebuilds it."""
    try:
        delattr(user, '_active_role_assignments')
    except AttributeError:
        pass
