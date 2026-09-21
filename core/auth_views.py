"""
core/auth_views.py — authentication-related API views.

- RateLimitedLoginView: throttled DRF token login at POST /api-token-auth/.
  Doubles as the BREAK-GLASS admin login (CFO directive 2026-06-16): a way in
  that does NOT depend on Microsoft SSO, so the CFO + key admins are never
  locked out — even during a Microsoft Entra outage. The /break-glass page
  posts here. Hardened so it is admin-only + audited + rate-limited:
    * Rate-limited (ScopedRateThrottle 'login', per IP) — anti-brute-force.
    * Admin-only — an explicit BREAK_GLASS_EMAILS allowlist if set, else
      staff/superuser only. SSO users have unusable passwords and never
      authenticate here anyway; this is belt-and-braces.
    * Audited — every attempt (success or refusal) writes an AuditLog row
      with the source IP.
"""
import logging

from django.conf import settings
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

log = logging.getLogger('break-glass')


def _client_ip(request) -> str:
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '') or ''


def _audit(request, user, *, ok: bool, reason: str = '') -> None:
    """Best-effort AuditLog of a break-glass attempt. Never blocks login."""
    try:
        from core.models import AuditLog
        from django.contrib.auth import get_user_model
        A = AuditLog.Action
        AuditLog.objects.create(
            table_name=get_user_model()._meta.db_table,
            record_id=str(getattr(user, 'pk', '') or ''),
            action=getattr(A, 'UPDATE', list(A)[0]),
            user=user if getattr(user, 'pk', None) else None,
            ip_address=_client_ip(request)[:45],
            description=("Break-glass login " + ('SUCCESS' if ok else 'REFUSED')
                         + (' - ' + reason if reason else '')
                         + " (user=" + str(getattr(user, 'username', '?'))
                         + ", ip=" + _client_ip(request) + ")"),
        )
    except Exception:    # noqa: BLE001
        log.exception('break-glass audit write failed')


class RateLimitedLoginView(ObtainAuthToken):
    """POST /api-token-auth/ — rate-limited, admin-only, audited break-glass login."""
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'login'

    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data, context={'request': request})
        if not serializer.is_valid():
            _audit(request, None, ok=False, reason='bad credentials')
            return Response({'detail': 'Invalid username or password.'},
                            status=status.HTTP_400_BAD_REQUEST)

        user = serializer.validated_data['user']

        allow = (getattr(settings, 'BREAK_GLASS_EMAILS', '') or '')
        allowed = {e.strip().lower() for e in allow.split(',') if e.strip()}
        email = (getattr(user, 'email', '') or '').lower()
        # ADDITIVE allowlist (CFO 2026-07-09): admins keep break-glass AND any
        # explicitly-listed email is permitted too. The old form
        # (allowlist XOR admin) meant listing one person silently revoked every
        # admin's break-glass — a foot-gun. Now the list only ever GRANTS.
        permitted = bool(user.is_staff or user.is_superuser) or (email in allowed)
        if not permitted:
            _audit(request, user, ok=False, reason='not on break-glass allowlist')
            return Response(
                {'detail': 'This account is not enabled for break-glass login. '
                           'Use Sign in with Microsoft, or contact an administrator.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        token, _ = Token.objects.get_or_create(user=user)
        _audit(request, user, ok=True)
        return Response({'token': token.key})
