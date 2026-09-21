"""
core/frozen_controls.py — enforcement for the frozen-component governance.

Internal Audit (Oprah, 2026-06-23) asked for the ADIC freeze to be a SYSTEM
control rather than an informal agreement. This module is the enforcement seam:

  * FROZEN_MESSAGE          — the exact block message shown on a frozen edit.
  * assert_frozen_change_allowed(key, user, consume=) — the API block: raises
    403 with FROZEN_MESSAGE unless an APPROVED, unconsumed FrozenChangeRequest
    exists for the component. Every call (allowed OR blocked) is written to the
    immutable AuditLog — that is ask #3 (audit every attempt + outcome).
  * file_change_request / decide_change_request — the maker-checker helpers the
    API views call, each of which also audits.

The CFO replaced the shared override password (Alpha@12345) with this gate for
frozen-FIGURE changes (CFO directive 2026-06-23). The unrelated closed-period
JE lock in ledger/locks.py is intentionally NOT touched.
"""
from __future__ import annotations

from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from .models import AuditLog, FrozenComponent, FrozenChangeRequest


# Internal Audit specified this exact wording.
FROZEN_MESSAGE = "This item is frozen — CFO approval required before any change."

# The three components Internal Audit named. Seeded by manage.py seed_frozen.
FROZEN_COMPONENTS = [
    {
        'key':   FrozenComponent.Key.ADIC_MA_PL_LAYOUT,
        'label': 'ADIC MA P&L layout',
        'description': 'The Management Accounts P&L section/line ordering for ADIC '
                       '(reporting/ma_pl_spec.py). Locked to the CFO MA workbook.',
    },
    {
        'key':   FrozenComponent.Key.ADIC_REVENUE_MAPPING,
        'label': 'ADIC revenue mapping',
        'description': 'Which GL accounts map to each revenue / MA line for ADIC. '
                       'Changing it moves reported revenue — board-sensitive.',
    },
    {
        'key':   FrozenComponent.Key.ADIC_GWP_FIGURE,
        'label': 'ADIC GWP figure',
        'description': 'The locked headline Gross Written Premium figure (FrozenFigure). '
                       'FY25 ADIC GWP = 125.15 Mn is the canonical value.',
    },
]


def _audit(*, action: str, user, record_id: str, description: str,
           new_values: dict | None = None) -> None:
    """Write one immutable AuditLog row for a frozen-control event."""
    AuditLog.objects.create(
        table_name='core_frozenchangerequest',
        record_id=str(record_id),
        action=action,
        new_values=new_values,
        user=user if (user and getattr(user, 'is_authenticated', False)) else None,
        description=description,
    )


def assert_frozen_change_allowed(component_key: str, user, *, consume: bool = False):
    """The API block. Raise 403 FROZEN_MESSAGE unless an approved, unconsumed
    FrozenChangeRequest exists for `component_key`. Logs the attempt either way.

    Args:
      component_key: a FrozenComponent.Key value.
      user:          the acting user (for the audit trail).
      consume:       if True, mark the authorising request consumed (one change
                     per approval). Pass True only when the change is actually
                     being applied, not on a dry-run check.

    Returns the authorising FrozenChangeRequest when allowed.
    """
    comp = FrozenComponent.objects.filter(key=component_key).first()
    # If a component isn't registered as frozen, there is nothing to block.
    if comp is None or not comp.is_frozen:
        return None

    approved = (FrozenChangeRequest.objects
                .filter(component=comp, status=FrozenChangeRequest.Status.APPROVED,
                        consumed=False)
                .order_by('-decided_at')
                .first())

    if approved is None:
        _audit(action=AuditLog.Action.UPDATE, user=user, record_id=comp.key,
               description=f'BLOCKED frozen-change attempt on "{comp.label}" — '
                           f'no approved CFO request. {FROZEN_MESSAGE}')
        raise PermissionDenied(FROZEN_MESSAGE)

    if consume:
        approved.consumed = True
        approved.save(update_fields=['consumed', 'updated_at'])
    _audit(action=AuditLog.Action.APPROVE, user=user, record_id=comp.key,
           description=f'ALLOWED frozen change on "{comp.label}" under approved '
                       f'request {approved.id} (consume={consume}).')
    return approved


def file_change_request(*, component_key: str, user, summary: str, reason: str,
                        board_impact: str) -> FrozenChangeRequest:
    """Maker step: create a pending request. Audited."""
    comp = FrozenComponent.objects.filter(key=component_key).first()
    if comp is None:
        raise ValidationError({'component': f'Unknown frozen component {component_key!r}.'})
    summary = (summary or '').strip()
    reason = (reason or '').strip()
    board_impact = (board_impact or '').strip()
    if not (summary and reason and board_impact):
        raise ValidationError(
            {'detail': 'summary, reason and board_impact are all required — '
                       'document what is changing, why, and the board impact.'})
    req = FrozenChangeRequest.objects.create(
        component=comp, summary=summary, reason=reason, board_impact=board_impact,
        requested_by=user if (user and getattr(user, 'is_authenticated', False)) else None,
    )
    _audit(action=AuditLog.Action.CREATE, user=user, record_id=req.id,
           description=f'Frozen-change request filed on "{comp.label}": {summary[:80]}',
           new_values={'summary': summary, 'reason': reason[:500],
                       'board_impact': board_impact[:500]})
    return req


def decide_change_request(*, request_obj: FrozenChangeRequest, user, approve: bool,
                          comment: str) -> FrozenChangeRequest:
    """Checker step (CFO): approve/reject with a MANDATORY comment. Audited.
    Immutable once decided — a decided request cannot be re-decided."""
    comment = (comment or '').strip()
    if not comment:
        raise ValidationError({'comment': 'A decision comment is mandatory.'})
    if request_obj.status != FrozenChangeRequest.Status.PENDING:
        raise ValidationError(
            {'detail': f'Request already {request_obj.status}; decisions are final.'})
    request_obj.status = (FrozenChangeRequest.Status.APPROVED if approve
                          else FrozenChangeRequest.Status.REJECTED)
    request_obj.decided_by = user if (user and getattr(user, 'is_authenticated', False)) else None
    request_obj.decision_comment = comment
    request_obj.decided_at = timezone.now()
    request_obj.save(update_fields=['status', 'decided_by', 'decision_comment',
                                    'decided_at', 'updated_at'])
    _audit(action=AuditLog.Action.APPROVE if approve else AuditLog.Action.UPDATE,
           user=user, record_id=request_obj.id,
           description=f'Frozen-change request {request_obj.status.upper()} by CFO on '
                       f'"{request_obj.component.label}". Comment: {comment[:160]}',
           new_values={'status': request_obj.status, 'comment': comment[:500]})
    return request_obj
