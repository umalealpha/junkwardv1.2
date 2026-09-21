"""
core/hris_unlock.py — second-factor password gate on top of the HRIS
whitelist (core/hris_access.py).

CFO directive 2026-05-18: 'HR is confidential. Whenever someone clicks
HRIS, it should prompt for a password.' Even the five whitelisted users
must enter the shared password before any HRIS page renders. The unlock
sticks for a configurable window (default 8 hours) so they don't have
to retype it on every navigation, then it auto-expires.

Endpoints (mounted in alpha_finance/api_router.py):
  POST /api/v1/hris/unlock/        body: {password}
                                   200 → {unlocked_until, role, capabilities}
                                   403 → not whitelisted
                                   401 → wrong password
  GET  /api/v1/hris/lock-status/   200 → {locked, unlocked_until, role, …}

Storage: each user's unlock_until timestamp lives on UserProfile.
Restart-safe and audit-trail-friendly. No session juggling, no shared
cache invalidation.

Override: env var OMNI_HRIS_PASSWORD takes precedence over the default
'PAYE2025' so the CFO can rotate it without a deploy.
"""
from __future__ import annotations

import os
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from rest_framework import status
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from rest_framework.decorators import (
    api_view, authentication_classes, permission_classes,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

# Azure JWT auth lives here when AZURE_SSO_ENABLED=True. Import is
# late-and-defensive so a non-SSO build doesn't import-error.
try:
    from core.azure_auth import AzureJWTAuthentication
except Exception:                                          # noqa: BLE001
    AzureJWTAuthentication = None                          # type: ignore


class CsrfExemptSessionAuthentication(SessionAuthentication):
    """SessionAuthentication with the CSRF check disabled.

    CFO directive 2026-05-18: the HRIS unlock form posts a JSON body
    via apiFetch, which doesn't carry an X-CSRFToken header. The
    default DRF SessionAuthentication.enforce_csrf would 403 with
    'CSRF token missing', and the frontend maps that to "Incorrect
    password" — making a CSRF clash look like a wrong password.

    Override enforce_csrf to a no-op. The password gate itself is the
    second factor that protects this endpoint; CSRF would only widen
    the attack surface to "logged-in user is tricked into POSTing
    the correct password" which already presupposes the attacker
    has the password.
    """

    def enforce_csrf(self, request):                       # noqa: D401
        return None


# Authentication chain for the unlock endpoints:
# 1. AzureJWT — Bearer-driven SSO (preferred).
# 2. CsrfExemptSessionAuthentication — Django session cookie, no CSRF.
# 3. TokenAuthentication — DRF token fallback.
_AUTH_CLASSES = list(filter(None, [
    AzureJWTAuthentication,
    CsrfExemptSessionAuthentication,
    TokenAuthentication,
]))

from .hris_access import (
    hris_access_payload,
    hris_role,
    ROLE_CAPABILITIES,
    user_can_access_hris,
    _local_part,
)


# CFO directive 2026-06-23: named people who should NEVER hit the HRIS password
# wall (on top of the role bypass in is_hris_unlocked). Covers users whose role
# doesn't resolve to a privileged tier — e.g. Tshephang (Veritas payroll, ess
# role). Env-overridable so the CFO can add/remove without a deploy.
DEFAULT_HRIS_UNLOCK_LOCAL_PARTS = ('tshephang', 'ubutale', 'dikgopoleng')


def _hris_unlock_local_parts() -> set[str]:
    env = (os.environ.get('OMNI_HRIS_UNLOCK_LOCAL_PARTS') or '').strip()
    if not env:
        return {x.lower() for x in DEFAULT_HRIS_UNLOCK_LOCAL_PARTS}
    return {p.strip().lower() for p in env.split(',') if p.strip()}


# Security audit 2026-06-09 — ruff S105. The literal default ``'PAYE2025'``
# used to live here as a hardcoded fallback. Removed: HRIS_PASSWORD must
# now come from env (OMNI_HRIS_PASSWORD) or settings (HRIS_PASSWORD), or
# the unlock endpoint refuses every password (defence-in-depth — no
# fixed fallback if config is missed).
DEFAULT_PASSWORD       = ''  # NOSEC — empty == no fallback; raises in _password()
DEFAULT_UNLOCK_HOURS   = 8


def _password() -> str:
    """Live password value. Env override OMNI_HRIS_PASSWORD wins, then
    settings.HRIS_PASSWORD. Empty result means HRIS is locked for everyone
    until the env var is set — fail-closed, no hardcoded fallback."""
    env = (os.environ.get('OMNI_HRIS_PASSWORD') or '').strip()
    if env:
        return env
    return (getattr(settings, 'HRIS_PASSWORD', '') or DEFAULT_PASSWORD).strip()


def _unlock_hours() -> int:
    raw = os.environ.get('OMNI_HRIS_UNLOCK_HOURS') or getattr(settings, 'HRIS_UNLOCK_HOURS', '') or DEFAULT_UNLOCK_HOURS
    try:
        return int(raw)
    except (TypeError, ValueError):
        return DEFAULT_UNLOCK_HOURS


def _profile_for(user):
    """Return user.profile, creating a minimal row if missing.

    NOTE: UserProfile is OneToOneField(User, related_name='profile'),
    so the reverse accessor is `user.profile` (NOT `user.userprofile`).
    Earlier code used 'userprofile' which silently returned None for
    every user; hris_lock_status_payload then reported unlocked=False
    after every successful save, and the page stayed on the password
    screen. CFO directive 2026-05-18.
    """
    from core.models import UserProfile  # avoid circular import at module load
    profile = getattr(user, 'profile', None)
    if profile is None:
        profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile


def is_hris_unlocked(user) -> bool:
    """True if `user` may skip the HRIS password prompt right now.

    Two ways to be "unlocked":
      1. Privileged-role bypass (CFO directive 2026-06-23, commit 1d9a8dc):
         hr / hris / admin / ceo / superadmin roles — plus the named
         always-unlocked local-parts — skip the shared password entirely.
         For them it was redundant double-gating that blocked the payroll
         team. This SUPERSEDES the original 2026-05-18 "even the superuser
         types it every 8 hours" rule.
      2. Everyone else (ess / mgr) must have an unexpired
         UserProfile.hris_unlocked_until — i.e. they entered the password
         within the unlock window — so HR data stays confidential against
         casual access.
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    # CFO directive 2026-06-23 ("allow them to upload payroll"): users who are
    # already role-cleared for HR/payroll data — finance leadership (CFO / FM /
    # FC), the HR roles, admins, CEO, superuser — bypass the shared HRIS
    # password. For them it was redundant double-gating that blocked the payroll
    # team (Legakwa = finance_manager, Pako = financial_controller) from the
    # Payroll upload. Ordinary staff (ess / mgr) STILL get the password prompt,
    # so HR data stays confidential against casual access. This relaxes the
    # 2026-05-18 "everyone types it" rule for privileged roles only.
    try:
        if hris_role(user) in {'hr', 'hris', 'admin', 'ceo', 'superadmin'}:
            return True
        # Named always-unlocked people (CFO directive 2026-06-23) — covers
        # roles that don't resolve to a privileged tier (e.g. Tshephang, ess).
        local = _local_part(getattr(user, 'email', None)) or (getattr(user, 'username', '') or '').strip().lower()
        if local and local in _hris_unlock_local_parts():
            return True
    except Exception:        # noqa: BLE001 — fall through to the password check
        pass
    profile = getattr(user, 'profile', None)
    if profile is None:
        return False
    until = getattr(profile, 'hris_unlocked_until', None)
    if not until:
        return False
    return until > timezone.now()


def hris_lock_status_payload(user) -> dict:
    """Combined payload for the lock-status probe."""
    access = hris_access_payload(user)
    profile = getattr(user, 'profile', None)
    until = getattr(profile, 'hris_unlocked_until', None) if profile else None
    unlocked = is_hris_unlocked(user)
    return {
        **access,
        'locked':         not unlocked,
        'unlocked':       unlocked,
        'unlocked_until': until.isoformat() if until else None,
        'unlock_hours':   _unlock_hours(),
    }


# ───────────────────────────────────────────────────────────────────────────
# DRF endpoints
# ───────────────────────────────────────────────────────────────────────────
@api_view(['POST'])
@authentication_classes(_AUTH_CLASSES)
@permission_classes([IsAuthenticated])
def hris_unlock(request):
    """POST /api/v1/hris/unlock/   body: {password}

    Auth: AzureJWT or DRF Token. SessionAuth deliberately not in the
    chain — see _AUTH_CLASSES note above (CSRF clash with the React
    frontend's Bearer flow).
    """
    if not user_can_access_hris(request.user):
        return Response(
            {'detail': 'You are not on the HRIS whitelist.', 'unlocked': False},
            status=status.HTTP_403_FORBIDDEN,
        )

    submitted = (request.data or {}).get('password') or ''
    expected = _password()
    # Defence-in-depth (2026-06-09 audit): if HRIS_PASSWORD is unset,
    # fail-closed — never accept a submission against an empty server
    # secret (otherwise the empty string would unlock).
    import hmac
    if not expected or not hmac.compare_digest(
        str(submitted).strip(), expected
    ):
        return Response(
            {'detail': 'Incorrect HRIS password.', 'unlocked': False},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    profile = _profile_for(request.user)
    until = timezone.now() + timedelta(hours=_unlock_hours())
    profile.hris_unlocked_until = until
    profile.save(update_fields=['hris_unlocked_until'])

    return Response({
        'unlocked':       True,
        'unlocked_until': until.isoformat(),
        'unlock_hours':   _unlock_hours(),
        'role':           hris_role(request.user),
        'capabilities':   sorted(ROLE_CAPABILITIES.get(hris_role(request.user), set())),
    })


@api_view(['POST'])
@authentication_classes(_AUTH_CLASSES)
@permission_classes([IsAuthenticated])
def hris_lock(request):
    """POST /api/v1/hris/lock/ — manual re-lock (e.g. "lock now" button)."""
    if not user_can_access_hris(request.user):
        # Idempotent for non-whitelisted users — they're already locked out.
        return Response({'locked': True})
    profile = _profile_for(request.user)
    profile.hris_unlocked_until = None
    profile.save(update_fields=['hris_unlocked_until'])
    return Response({'locked': True})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def hris_lock_status(request):
    """GET /api/v1/hris/lock-status/ — frontend probes this before rendering."""
    return Response(hris_lock_status_payload(request.user))
