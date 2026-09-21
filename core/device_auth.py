"""StaffDeviceAuthentication — the Omni staff phone app's bearer (CFO 2026-09-03).

Claims ONLY tokens whose sha256 exists in StaffDeviceSession. Everything else
returns None so the rest of the chain (Nexus bridge, Azure JWT, DRF Token)
runs unchanged.

MUST be listed BEFORE core.azure_auth.AzureJWTAuthentication — Azure raises on
a non-JWT Bearer, which would kill the chain before this class saw the token
(same rule as rewards.staff_bridge_auth). Device tokens are 64 hex chars with
no dots, so a real JWT (two dots) is skipped here without a DB lookup.

Expired or revoked → AuthenticationFailed (401 with a plain-English message)
rather than None, so the app's 401 handler clears the dead token and shows the
sign-in screen instead of a silently-empty form.
"""
from __future__ import annotations

from datetime import timedelta

from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from core.screenshot_bot import READ_ONLY_USERNAMES, SAFE_METHODS

_LAST_SEEN_STAMP_EVERY = timedelta(minutes=5)


class StaffDeviceAuthentication(BaseAuthentication):
    keyword = 'Bearer'

    def authenticate_header(self, request):
        return self.keyword

    def authenticate(self, request):
        header = request.META.get('HTTP_AUTHORIZATION', '')
        if not header.lower().startswith('bearer '):
            return None
        raw = header.split(' ', 1)[1].strip()
        if not raw or raw.count('.') == 2 or len(raw) != 64:
            return None
        from core.device_session_models import StaffDeviceSession
        sess = (StaffDeviceSession.objects.select_related('user')
                .filter(token_hash=StaffDeviceSession.hash_token(raw)).first())
        if sess is None:
            return None                                   # not ours — fall through
        if sess.revoked_at is not None:
            raise AuthenticationFailed('This device was signed out — please sign in again.')
        now = timezone.now()
        if sess.expires_at <= now:
            raise AuthenticationFailed('Your 30-day sign-in has ended — please sign in again.')
        if not sess.user.is_active:
            raise AuthenticationFailed('This account is inactive.')
        if sess.last_seen_at is None or now - sess.last_seen_at > _LAST_SEEN_STAMP_EVERY:
            StaffDeviceSession.objects.filter(pk=sess.pk).update(last_seen_at=now)
        # Same read-only gate as core/token_auth.py: the locked screenshot / QA
        # identities may only ever GET, whatever credential they arrive with.
        if (getattr(sess.user, 'username', '') in READ_ONLY_USERNAMES
                and (getattr(request, 'method', 'GET') or 'GET').upper() not in SAFE_METHODS):
            raise AuthenticationFailed('This is a read-only view — it cannot change anything.')
        return (sess.user, sess)
