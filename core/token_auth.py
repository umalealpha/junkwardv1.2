"""core/token_auth.py — DRF token auth with an absolute session lifetime.

CFO directive 2026-07-18: a signed-in session lasts 15 hours, then the user
must sign in again. This caps the value of a stolen/left-behind token. The
60-minute inactivity ("idle") logout is enforced on the client (see the
dashboard layout); this is the hard server-side ceiling.

Applies to the DRF tokens minted by the staff email login and the break-glass
endpoint. Microsoft/Azure SSO bearer tokens carry their own expiry and are
handled by core.azure_auth, so they are unaffected.

The window is OMNI_TOKEN_TTL_HOURS (env) or 15 hours by default.

CFO directive 2026-07-22: the locked read-only identities in
core.screenshot_bot (the screenshot robot, and from 2026-07-29 the human
quality-control view) authenticate ONLY on safe (GET/HEAD/OPTIONS) requests —
any write attempt fails authentication here, across every view, so neither can
ever change anything or move money.
"""
from __future__ import annotations

import os
from datetime import timedelta

from django.utils import timezone
from rest_framework.authentication import TokenAuthentication
from rest_framework.exceptions import AuthenticationFailed

from core.screenshot_bot import READ_ONLY_USERNAMES, SAFE_METHODS


def _ttl_hours() -> float:
    try:
        return float(os.environ.get('OMNI_TOKEN_TTL_HOURS', '15'))
    except (TypeError, ValueError):
        return 15.0


class ExpiringTokenAuthentication(TokenAuthentication):
    def authenticate(self, request):
        result = super().authenticate(request)   # (user, token) or None
        if result is not None:
            user, _token = result
            # Read-only gate for the locked service identities (screenshot robot
            # and the human QA view): they may only ever GET.
            if (getattr(user, 'username', '') in READ_ONLY_USERNAMES
                    and (getattr(request, 'method', 'GET') or 'GET').upper() not in SAFE_METHODS):
                raise AuthenticationFailed('This is a read-only view — it cannot change anything.')
        return result

    def authenticate_credentials(self, key):
        user, token = super().authenticate_credentials(key)
        if token.created < timezone.now() - timedelta(hours=_ttl_hours()):
            # Expired: drop the token so the next sign-in mints a fresh one.
            token.delete()
            raise AuthenticationFailed('Session expired — please sign in again.')
        return (user, token)
