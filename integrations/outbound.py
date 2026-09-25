"""integrations/outbound.py — the generalised OUTBOUND state-bus dispatcher.

WS1 (2026-09-26). Omni's only write-back to Graphite used to be the bespoke
`customer_refunds.services.post_refund_back_to_graphite` (raw urllib, no record,
no retry, no sender idempotency). This module is the reusable version of that:

    enqueue(...)            -> persist an OutboundEvent (idempotent on key)
    deliver(event)          -> POST it once; update status/attempts/backoff
    enqueue_and_deliver(...) -> the sync path (enqueue + try now; keep for retry)
    drain(limit)            -> the worker: send everything that's due

Rules held here:
  * FACTS / STATES ONLY — never money. Callers pass a policy/claim/refund state.
  * Secrets are never persisted — the event stores the settings-attr NAME
    (`token_setting`); the actual token is read at send time.
  * Kill switch: `OUTBOUND_BUS_ENABLED` (settings, default True). Off = events
    still enqueue but nothing is sent, so the bus can be stopped with no deploy.
  * Idempotency: a repeat enqueue with the same `idempotency_key` returns the
    original row; Graphite's receivers are themselves idempotent on graphite_ref.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from django.conf import settings
from django.utils import timezone

from .models import OutboundEvent

# Retry backoff (seconds) indexed by attempt number; capped. Deliberately short
# early so a blip self-heals within a minute, then widens.
_BACKOFF = [0, 30, 120, 600, 1800, 3600]


def _bus_enabled() -> bool:
    return bool(getattr(settings, 'OUTBOUND_BUS_ENABLED', True))


def _backoff_seconds(attempts: int) -> int:
    return _BACKOFF[min(attempts, len(_BACKOFF) - 1)]


def _http_post(url: str, body: bytes, headers: dict, timeout: int = 20) -> int:
    """POST and return the HTTP status. Isolated so tests can monkeypatch it."""
    req = urllib.request.Request(url, data=body, method='POST', headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310 (internal URL)
        return int(r.status)


def enqueue(*, target: str, event_type: str, payload: dict, endpoint: str,
            auth_kind: str = OutboundEvent.AuthKind.BEARER,
            token_setting: str = '', idempotency_key: str = '',
            max_attempts: int = 6) -> OutboundEvent:
    """Persist an outbound event. Idempotent on idempotency_key: a repeat with the
    same key returns the existing row untouched (never a second delivery)."""
    key = (idempotency_key or '').strip()
    if key:
        existing = OutboundEvent.objects.filter(idempotency_key=key).first()
        if existing is not None:
            return existing
    return OutboundEvent.objects.create(
        target=target, event_type=event_type, payload=payload, endpoint=endpoint,
        auth_kind=auth_kind, token_setting=token_setting, idempotency_key=key,
        max_attempts=max_attempts, status=OutboundEvent.Status.PENDING,
        next_attempt_at=timezone.now(),
    )


def _headers_for(event: OutboundEvent) -> dict | None:
    """Build request headers, resolving the secret at send time. Returns None if
    the event needs a token that is not configured (kept PENDING, not failed)."""
    h = {'Content-Type': 'application/json'}
    if event.auth_kind == OutboundEvent.AuthKind.NONE:
        return h
    token = (getattr(settings, event.token_setting, '') or '') if event.token_setting else ''
    if not token:
        return None
    if event.auth_kind == OutboundEvent.AuthKind.APIKEY:
        h['Authorization'] = f'ApiKey {token}'
    else:
        h['Authorization'] = f'Bearer {token}'
    return h


def deliver(event: OutboundEvent) -> dict:
    """Attempt one delivery. Updates the row in place. Returns a small result dict."""
    if event.status == OutboundEvent.Status.SENT:
        return {'sent': True, 'reason': 'already_sent'}
    if not _bus_enabled():
        return {'sent': False, 'reason': 'bus_disabled'}
    if not event.endpoint:
        return {'sent': False, 'reason': 'no_endpoint'}
    headers = _headers_for(event)
    if headers is None:
        # Not a failure — the target simply isn't wired yet. Leave it PENDING.
        return {'sent': False, 'reason': 'not_configured'}

    body = json.dumps(event.payload).encode()
    event.attempts += 1  # counts this attempt exactly once, on every retry
    try:
        status_code = _http_post(event.endpoint, body, headers)
        event.response_status = status_code
        if 200 <= status_code < 300:
            event.status = OutboundEvent.Status.SENT
            event.sent_at = timezone.now()
            event.last_error = ''
            event.next_attempt_at = None
            event.save(update_fields=['attempts', 'response_status', 'status',
                                      'sent_at', 'last_error', 'next_attempt_at',
                                      'updated_at'])
            return {'sent': True, 'status': status_code}
        event.last_error = f'HTTP {status_code}'
    except urllib.error.HTTPError as exc:
        # urlopen raises for 4xx/5xx — capture the real code onto the record.
        event.response_status = int(getattr(exc, 'code', 0)) or event.response_status
        event.last_error = f'HTTP {getattr(exc, "code", "?")}'
    except Exception as exc:  # noqa: BLE001 — any transport error retries
        event.last_error = str(exc)[:500]

    # Failure path (non-2xx or transport error): retry with backoff, or give up.
    if event.attempts >= event.max_attempts:
        event.status = OutboundEvent.Status.DEAD
        event.next_attempt_at = None
    else:
        event.status = OutboundEvent.Status.FAILED
        event.next_attempt_at = timezone.now() + timezone.timedelta(
            seconds=_backoff_seconds(event.attempts))
    event.save(update_fields=['attempts', 'response_status', 'status',
                              'last_error', 'next_attempt_at', 'updated_at'])
    return {'sent': False, 'reason': event.last_error, 'status': event.status}


def enqueue_and_deliver(**kwargs) -> dict:
    """Enqueue then try to deliver immediately (the synchronous caller path, e.g.
    the refund post-back). If the send fails it is already persisted for the
    worker to retry. Returns deliver()'s result plus the event id."""
    event = enqueue(**kwargs)
    result = deliver(event)
    result['event_id'] = str(event.pk)
    return result


def drain(limit: int = 50) -> dict:
    """Worker pass: deliver every event that is due (PENDING or FAILED, its
    backoff elapsed, attempts left). Returns counts."""
    if not _bus_enabled():
        return {'considered': 0, 'sent': 0, 'skipped': 'bus_disabled'}
    now = timezone.now()
    due = (OutboundEvent.objects
           .filter(status__in=[OutboundEvent.Status.PENDING, OutboundEvent.Status.FAILED])
           .filter(models_q_due(now))
           .order_by('next_attempt_at')[:limit])
    sent = failed = 0
    for event in list(due):
        r = deliver(event)
        if r.get('sent'):
            sent += 1
        else:
            failed += 1
    return {'considered': sent + failed, 'sent': sent, 'failed': failed}


def models_q_due(now):
    """next_attempt_at is null (fresh) or already due."""
    from django.db.models import Q
    return Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now)
