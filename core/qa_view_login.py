"""core/qa_view_login.py — the no-sign-in, READ-ONLY quality-control view.

CFO directive 2026-07-29: quality-checking Omni through a normal staff login
(omni@alphadirect.co.bw) is a headache — a password, an emailed one-time code, a
token that dies after 15 hours, a privacy pop-up, and role denials that look
like bugs. To INSPECT a screen the CFO needs three things only: see everything,
change nothing, and no sign-in ceremony.

So this endpoint hands the browser a fresh token for `omni-qa-view` — a locked
service identity from core.screenshot_bot. It is a superuser, so every page
renders exactly as the CFO sees it, and core.token_auth refuses its token on any
method other than GET/HEAD/OPTIONS. The QA view therefore cannot write, approve,
delete or move money in ANY view, including views with custom permissions. It is
a separate identity from the screenshot robot so a headless run and an open QA
tab never revoke each other (DRF keeps one token per user).

The only credential is a shared secret in the OMNI_QA_VIEW_KEY environment
variable on the server — never in the repo, no default, and if it is unset the
endpoint is simply off. The key is compared in constant time, the endpoint is
per-IP throttled on the same 10/minute 'login' bucket, and every attempt (pass
or fail) is written to the audit log.

Honest limits, stated for the record:
  * The link is a bearer credential. Anyone holding the key can READ everything
    in Omni, including policyholder and payroll personal data. It grants no
    writes, but it is not anonymous access — keep the key to the CFO.
  * Because the identity is a superuser it never feels a low role's denials, so
    it does not replace testing as a restricted staff user when the thing under
    test IS the permission.
"""
from __future__ import annotations

import hmac
import logging
import os
from datetime import timedelta

from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.decorators import (api_view, authentication_classes,
                                       permission_classes, throttle_classes)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle

from core.models import AuditLog
from core.screenshot_bot import QA_VIEW_USERNAME, get_or_create_qa_viewer

log = logging.getLogger('qa-view')

MIN_KEY_LEN = 24


def _client_ip(request) -> str:
    # Cloudflare fronts omni and sets CF-Connecting-IP to the real client; it
    # cannot be spoofed past Cloudflare. NEVER trust X-Forwarded-For here.
    return (request.META.get('HTTP_CF_CONNECTING_IP')
            or request.META.get('REMOTE_ADDR') or '')


class QaViewThrottle(SimpleRateThrottle):
    """Per-real-client throttle on the 'login' rate (10/min), FBV-safe."""
    scope = 'login'

    def get_cache_key(self, request, view):
        return f'throttle_qaview_{_client_ip(request) or "anon"}'


def _configured_key() -> str:
    """The shared secret, or '' when the QA view is switched off. A key shorter
    than MIN_KEY_LEN is treated as unset — a weak key is worse than no feature."""
    key = (os.environ.get('OMNI_QA_VIEW_KEY') or '').strip()
    return key if len(key) >= MIN_KEY_LEN else ''


def _live_token(viewer):
    """The viewer's current token, minted only when there isn't a usable one.

    DRF keeps ONE token per user, so deleting and recreating on every open would
    log out whatever else is already using the QA view — a second browser tab,
    another device, or the screenshot harness. Instead reuse the existing token
    while it has real life left, and only replace it when it is missing or close
    enough to the 15-hour ceiling that a check would expire mid-way.
    """
    from core.token_auth import _ttl_hours
    margin = timedelta(hours=max(_ttl_hours() - 1, 0.25))
    existing = Token.objects.filter(user=viewer).first()
    if existing and existing.created > timezone.now() - margin:
        return existing
    if existing:
        existing.delete()
    return Token.objects.create(user=viewer)


def _audit(request, *, ok: bool, reason: str = '') -> None:
    try:
        A = AuditLog.Action
        AuditLog.objects.create(
            table_name='auth_user', record_id='',
            action=getattr(A, 'UPDATE', list(A)[0]), user=None,
            ip_address=_client_ip(request)[:45],
            description=(f"QA read-only view {'OPENED' if ok else 'DENIED'} "
                         f"{QA_VIEW_USERNAME} {reason} ip={_client_ip(request)}")[:250],
        )
    except Exception:
        log.exception('qa-view audit write failed')


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([QaViewThrottle])
def qa_view_open(request):
    """POST {key} -> {token} for the read-only QA identity.

    503 when the QA view is not configured on this server, 403 on a wrong key.
    The key travels in the body, never in the URL, so it stays out of access
    logs and out of the Referer header.
    """
    expected = _configured_key()
    if not expected:
        _audit(request, ok=False, reason='not-configured')
        return Response(
            {'detail': 'The read-only QA view is not switched on for this server.'},
            status=503)

    supplied = str(request.data.get('key') or '')
    if not hmac.compare_digest(supplied, expected):
        _audit(request, ok=False, reason='bad-key')
        return Response({'detail': 'That key is not right.'}, status=403)

    viewer, _created = get_or_create_qa_viewer()
    token = _live_token(viewer)

    # The staff privacy pop-up is a consent record for real employees; a locked
    # service identity has nothing to consent to, so pre-record it rather than
    # make the QA view click through a declaration on every visit.
    try:
        from core import privacy_notice as pn
        pn.record_ack(viewer)
    except Exception:
        log.exception('qa-view privacy pre-ack failed')

    _audit(request, ok=True)
    return Response({'token': token.key, 'read_only': True,
                     'username': QA_VIEW_USERNAME})
