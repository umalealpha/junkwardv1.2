"""
exceptions/services.py

Authoritative API for the exception engine. Models import this; views
import this; signals call this. Direct model writes from outside are
discouraged.

Workflow:
  create_exception(...)           — write a new row (idempotent on
                                     source_app/source_model/source_id +
                                     exception_type when dedupe=True).
  acknowledge_exception(exc, user) — OPEN → ACKNOWLEDGED (anyone).
  resolve_exception(exc, user, notes) — → RESOLVED. Gated by requires_role.
  dismiss_exception(exc, user, reason) — → DISMISSED. Gated by requires_role.
  notify_via_linker(exc)           — fire-and-forget HTTP POST to the
                                     LINKER_API_BASE with the exception
                                     payload. No-op if not configured.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

import requests
from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import AuditLog, get_user_profile, UserProfile

from .models import Exception as ExceptionModel


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Authorisation — who can clear which kind of exception
# ---------------------------------------------------------------------------

_ROLE_CLEARANCE = {
    ExceptionModel.RequiresRole.ANY: lambda profile, user: True,
    ExceptionModel.RequiresRole.DEPT_MANAGER: lambda profile, user: (
        bool(getattr(user, 'is_superuser', False))
        or (profile is not None and profile.title in {
            UserProfile.Title.CLAIMS_MANAGER,
            UserProfile.Title.OPERATIONS_MANAGER,
            UserProfile.Title.HR_MANAGER,
            UserProfile.Title.FINANCE_MANAGER,
            UserProfile.Title.CFO,
        } if hasattr(UserProfile.Title, 'CLAIMS_MANAGER') else (
            profile is not None and profile.title in {
                UserProfile.Title.FINANCE_MANAGER, UserProfile.Title.CFO,
            }
        ))
    ),
    ExceptionModel.RequiresRole.FINANCE_MANAGER: lambda profile, user: (
        bool(getattr(user, 'is_superuser', False))
        or (profile is not None and profile.title in {
            UserProfile.Title.FINANCE_MANAGER,
            UserProfile.Title.FINANCIAL_CONTROLLER,
            UserProfile.Title.CFO,
        })
    ),
    ExceptionModel.RequiresRole.CFO: lambda profile, user: (
        bool(getattr(user, 'is_superuser', False))
        or (profile is not None and profile.title == UserProfile.Title.CFO)
    ),
}


def _can_clear(exc: ExceptionModel, user) -> bool:
    profile = get_user_profile(user)
    check = _ROLE_CLEARANCE.get(exc.requires_role, _ROLE_CLEARANCE[ExceptionModel.RequiresRole.ANY])
    try:
        return bool(check(profile, user))
    except AttributeError:
        # Fallback if older UserProfile titles haven't shipped yet — only
        # superusers, FM, CFO can clear anything sensitive.
        is_super = bool(getattr(user, 'is_superuser', False))
        if not profile:
            return is_super
        return is_super or profile.title in {
            UserProfile.Title.FINANCE_MANAGER, UserProfile.Title.CFO,
        }


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------

@transaction.atomic
def create_exception(
    *,
    exception_type: str,
    title: str,
    description: str = '',
    severity: str = ExceptionModel.Severity.MEDIUM,
    requires_role: str = ExceptionModel.RequiresRole.ANY,
    source_app: str = '',
    source_model: str = '',
    source_id: str = '',
    source_label: str = '',
    metadata: Optional[Dict[str, Any]] = None,
    created_by: Optional[User] = None,
    dedupe: bool = True,
) -> ExceptionModel:
    """
    Write an exception. Idempotent by default — if an OPEN/ACKNOWLEDGED
    exception with the same (source_app, source_model, source_id,
    exception_type) already exists, that one is returned instead of a
    duplicate.

    Fires `notify_via_linker` synchronously; failures are swallowed.
    """
    if dedupe and source_app and source_model and source_id:
        existing = ExceptionModel.objects.filter(
            source_app=source_app,
            source_model=source_model,
            source_id=str(source_id),
            exception_type=exception_type,
            status__in=[
                ExceptionModel.Status.OPEN,
                ExceptionModel.Status.ACKNOWLEDGED,
                ExceptionModel.Status.IN_PROGRESS,
            ],
        ).first()
        if existing:
            return existing

    exc = ExceptionModel.objects.create(
        exception_type=exception_type,
        title=title[:200],
        description=description or '',
        severity=severity,
        requires_role=requires_role,
        source_app=source_app or '',
        source_model=source_model or '',
        source_id=str(source_id) if source_id else '',
        source_label=source_label or '',
        metadata=metadata or {},
        created_by=created_by,
    )

    AuditLog.objects.create(
        table_name='Exception',
        record_id=str(exc.pk),
        action=AuditLog.Action.CREATE,
        new_values={
            'exception_type': exception_type,
            'severity':       severity,
            'requires_role':  requires_role,
            'source':         f'{source_app}.{source_model}#{source_id}',
        },
        user=created_by,
        description=f'Exception created: {title[:140]}',
    )

    # Fire Linker notification — best-effort, never blocks
    try:
        notify_via_linker(exc)
    except Exception as e:  # noqa: BLE001
        log.warning('Linker notify failed for %s: %s', exc.pk, e)

    return exc


# ---------------------------------------------------------------------------
# Workflow transitions
# ---------------------------------------------------------------------------

@transaction.atomic
def acknowledge_exception(exc: ExceptionModel, user) -> ExceptionModel:
    """OPEN → ACKNOWLEDGED. Anyone in finance can claim a row."""
    if exc.status != ExceptionModel.Status.OPEN:
        raise ValidationError(
            f'Only OPEN exceptions can be acknowledged. Current: {exc.status}.'
        )
    exc.status          = ExceptionModel.Status.ACKNOWLEDGED
    exc.acknowledged_by = user
    exc.acknowledged_at = timezone.now()
    exc.save()
    AuditLog.objects.create(
        table_name='Exception', record_id=str(exc.pk),
        action=AuditLog.Action.UPDATE,
        new_values={'status': exc.status, 'acknowledged_by': str(user.pk)},
        user=user, description=f'Acknowledged: {exc.title[:140]}',
    )
    return exc


@transaction.atomic
def resolve_exception(exc: ExceptionModel, user, notes: str) -> ExceptionModel:
    """Move to RESOLVED. Gated by requires_role."""
    if not exc.is_open:
        raise ValidationError(
            f'Cannot resolve a {exc.get_status_display()} exception.'
        )
    if not _can_clear(exc, user):
        raise ValidationError(
            f'Resolving this exception requires the role: '
            f'{exc.get_requires_role_display()}.'
        )
    if not (notes or '').strip():
        raise ValidationError('Resolution notes are required.')

    exc.status           = ExceptionModel.Status.RESOLVED
    exc.resolved_by      = user
    exc.resolved_at      = timezone.now()
    exc.resolution_notes = notes.strip()[:5000]
    exc.save()

    AuditLog.objects.create(
        table_name='Exception', record_id=str(exc.pk),
        action=AuditLog.Action.UPDATE,
        new_values={
            'status': exc.status, 'resolved_by': str(user.pk),
            'resolution_notes': notes[:300],
        },
        user=user, description=f'Resolved: {exc.title[:140]}',
    )
    return exc


@transaction.atomic
def dismiss_exception(exc: ExceptionModel, user, reason: str) -> ExceptionModel:
    """Move to DISMISSED — closes the row without claiming it was fixed.

    Use when the exception is determined to be false-positive or no longer
    relevant. Gated by requires_role.
    """
    if not exc.is_open:
        raise ValidationError(
            f'Cannot dismiss a {exc.get_status_display()} exception.'
        )
    if not _can_clear(exc, user):
        raise ValidationError(
            f'Dismissing this exception requires the role: '
            f'{exc.get_requires_role_display()}.'
        )
    if not (reason or '').strip():
        raise ValidationError('Dismissal reason is required.')

    exc.status           = ExceptionModel.Status.DISMISSED
    exc.dismissed_by     = user
    exc.dismissed_at     = timezone.now()
    exc.dismissal_reason = reason.strip()[:5000]
    exc.save()

    AuditLog.objects.create(
        table_name='Exception', record_id=str(exc.pk),
        action=AuditLog.Action.UPDATE,
        new_values={
            'status': exc.status, 'dismissed_by': str(user.pk),
            'dismissal_reason': reason[:300],
        },
        user=user, description=f'Dismissed: {exc.title[:140]}',
    )
    return exc


# ---------------------------------------------------------------------------
# Linker external-notification webhook
# ---------------------------------------------------------------------------

def notify_via_linker(exc: ExceptionModel, *, timeout: float = 5.0) -> bool:
    """
    Best-effort POST to the configured Linker webhook. Returns True if the
    request was attempted and the server returned 2xx, False otherwise.
    No-op if `LINKER_API_KEY` or `LINKER_API_BASE` is unset.
    """
    api_key  = getattr(settings, 'LINKER_API_KEY', '') or ''
    api_base = getattr(settings, 'LINKER_API_BASE', '') or ''
    if not (api_key and api_base):
        return False  # not configured — silently skip

    payload = {
        'exception_id':    str(exc.pk),
        'exception_type':  exc.exception_type,
        'severity':        exc.severity,
        'title':           exc.title,
        'description':     exc.description[:500],
        'source':          f'{exc.source_app}.{exc.source_model}#{exc.source_id}'.strip('.#'),
        'source_label':    exc.source_label,
        'requires_role':   exc.requires_role,
        'created_at':      exc.created_at.isoformat() if exc.created_at else None,
        'metadata':        exc.metadata or {},
    }
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type':  'application/json',
    }

    ok = False
    response_text = ''
    try:
        resp = requests.post(api_base, headers=headers,
                             data=json.dumps(payload), timeout=timeout)
        ok = 200 <= resp.status_code < 300
        response_text = f'HTTP {resp.status_code}'
    except requests.RequestException as e:
        response_text = f'Network error: {e}'[:200]
        log.warning('Linker network error: %s', e)
    except Exception as e:  # noqa: BLE001
        response_text = f'Error: {e}'[:200]
        log.warning('Linker unexpected error: %s', e)

    # Record the dispatch attempt — never overwrite if it already succeeded
    ExceptionModel.objects.filter(pk=exc.pk).update(
        linker_notified_at=timezone.now(),
        linker_response=response_text[:200],
    )
    return ok
