"""core/webpush.py — Web Push sender for the approvals nudge (CFO 2026-07-23).

Self-contained + fail-soft: if pywebpush isn't installed or the VAPID private
key isn't configured, send_push_to_user() is a silent no-op and NEVER raises, so
nothing in the app can break because push isn't set up. A subscription that the
browser has expired (410/404) is pruned automatically.

Config (env, all optional — absence = push disabled):
  WEBPUSH_VAPID_PRIVATE_KEY   base64url VAPID private key
  WEBPUSH_VAPID_PUBLIC_KEY    base64url VAPID public key (also served to the client)
  WEBPUSH_VAPID_CLAIMS_EMAIL  mailto contact (defaults to the CFO)
"""
from __future__ import annotations

import json
import logging
import os

log = logging.getLogger(__name__)

_DEFAULT_CLAIMS_EMAIL = "mailto:pganesharajah@alphadirect.co.bw"


def push_enabled() -> bool:
    return bool(os.environ.get("WEBPUSH_VAPID_PRIVATE_KEY"))


def _vapid_claims_email() -> str:
    return os.environ.get("WEBPUSH_VAPID_CLAIMS_EMAIL") or _DEFAULT_CLAIMS_EMAIL


def send_push_to_user(user, title: str, body: str,
                      url: str = "/m/staff/approvals") -> int:
    """Push a notification to every device `user` has subscribed. Returns the
    number of devices actually notified. No-op (returns 0) if push is disabled."""
    priv = os.environ.get("WEBPUSH_VAPID_PRIVATE_KEY")
    if not priv:
        return 0
    try:
        from pywebpush import webpush, WebPushException
    except Exception:  # noqa: BLE001 — library not installed → disabled
        return 0

    from core.models import PushSubscription
    payload = json.dumps({"title": title, "body": body, "url": url})
    sent = 0
    for sub in PushSubscription.objects.filter(user=user):
        try:
            webpush(
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
                },
                data=payload,
                vapid_private_key=priv,
                vapid_claims={"sub": _vapid_claims_email()},
                timeout=10,
            )
            sent += 1
            from django.utils import timezone
            PushSubscription.objects.filter(pk=sub.pk).update(
                last_sent_at=timezone.now())
        except WebPushException as exc:  # noqa: PERF203
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (404, 410):
                # The browser dropped this subscription — stop pushing to it.
                sub.delete()
            else:
                log.warning("web push failed for %s: %s", sub, exc)
        except Exception as exc:  # noqa: BLE001
            log.warning("web push error for %s: %s", sub, exc)
    return sent
