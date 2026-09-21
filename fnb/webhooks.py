"""
fnb/webhooks.py

Receive and verify webhooks from FNB Botswana.

Common FNB webhook events we'll handle once the spec is known:
  - payment.acknowledged   FNB has accepted the batch (move SUBMITTED → ACKNOWLEDGED)
  - payment.settled        Funds debited (SUBMITTED → SETTLED + post the JE)
  - payment.failed         Batch rejected (SUBMITTED → FAILED + raise an Exception)
  - statement.ready        New statement is available for pull
  - beneficiary.verified   Beneficiary account-name verification result
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Optional

from django.conf import settings
from django.utils import timezone

from .models import FNBBatchSubmission, FNBSyncLog, FNBWebhookEvent


log = logging.getLogger(__name__)


def verify_signature(raw_body: bytes, signature_header: str) -> bool:
    """Verify the HMAC signature on an FNB webhook.

    Currently uses HMAC-SHA256 with FNB_WEBHOOK_SECRET as the key — the
    most common pattern. If FNB uses a different scheme (RSA, JWT,
    detached JWS), swap this implementation when the spec arrives.

    Returns False if no secret is configured (so webhooks without
    signatures are rejected by default).
    """
    secret = getattr(settings, 'FNB_WEBHOOK_SECRET', '') or ''
    if not (secret and signature_header):
        return False
    expected = hmac.new(
        secret.encode('utf-8'), raw_body, hashlib.sha256,
    ).hexdigest()
    # Constant-time compare
    return hmac.compare_digest(expected, signature_header.strip())


def record_webhook(
    raw_body: bytes,
    headers: dict,
    *,
    signature_header: str = '',
) -> FNBWebhookEvent:
    """Persist the inbound webhook + flag whether the signature verified.
    Always succeeds — the dispatcher decides whether to act on it.
    """
    try:
        payload = json.loads(raw_body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = {'_raw': raw_body[:2000].decode('latin-1', errors='replace')}

    verified = verify_signature(raw_body, signature_header) if signature_header else False

    event_type = (payload.get('event_type') or payload.get('type')
                  or payload.get('event') or '')[:80]
    external_id = (payload.get('id') or payload.get('event_id')
                   or payload.get('external_id') or '')[:128]

    # FNB redelivers an event when it does not get a 2xx quickly enough. The
    # uniqueness rule on external_id used to turn that redelivery into an
    # IntegrityError → HTTP 500 → another redelivery (Fable 5.1 audit
    # 2026-09-02, M8). Hand back the row we already hold; the view then skips
    # the dispatch because it is already processed.
    if external_id:
        existing = FNBWebhookEvent.objects.filter(external_id=external_id).first()
        if existing is not None:
            log.info('FNB webhook %s redelivered — already recorded as %s',
                     external_id, existing.status)
            return existing

    event = FNBWebhookEvent.objects.create(
        event_type    = event_type,
        external_id   = external_id,
        raw_payload   = payload,
        raw_headers   = {k: v for k, v in headers.items()
                         if k.lower() in {'content-type', 'user-agent',
                                          'x-fnb-signature', 'x-fnb-event-id',
                                          'x-fnb-timestamp'}},
        signature     = signature_header[:300],
        status        = (FNBWebhookEvent.Status.VERIFIED if verified
                         else FNBWebhookEvent.Status.RECEIVED),
    )

    FNBSyncLog.objects.create(
        direction       = FNBSyncLog.Direction.INBOUND,
        service         = FNBSyncLog.Service.WEBHOOK,
        endpoint        = '(webhook)',
        status          = (FNBSyncLog.Status.SUCCESS if verified
                           else FNBSyncLog.Status.PENDING),
        request_summary = f'Webhook {event_type or "unknown"} {external_id}',
        response_payload= payload,
    )

    return event


def dispatch_webhook(event: FNBWebhookEvent) -> None:
    """Apply the webhook's effect to internal models. Idempotent.

    Stub right now — actual handlers depend on FNB's event names and
    payload shapes. When the spec arrives, fill in the handlers below.
    """
    try:
        # Route by event_type — fill in once spec is known
        et = event.event_type
        if et in ('payment.acknowledged', 'batch.acknowledged'):
            _on_batch_acknowledged(event)
        elif et in ('payment.settled', 'batch.settled'):
            _on_batch_settled(event)
        elif et in ('payment.failed', 'batch.failed'):
            _on_batch_failed(event)
        else:
            log.info('Unhandled FNB webhook event_type: %s', et)

        FNBWebhookEvent.objects.filter(pk=event.pk).update(
            status       = FNBWebhookEvent.Status.PROCESSED,
            processed_at = timezone.now(),
        )
    except Exception as e:  # noqa: BLE001
        log.warning('Failed to dispatch FNB webhook %s: %s', event.pk, e)
        FNBWebhookEvent.objects.filter(pk=event.pk).update(
            status        = FNBWebhookEvent.Status.FAILED,
            error_message = str(e)[:500],
        )


# A webhook may only move a batch FORWARD. Before this every handler overwrote
# the status unconditionally, so a late "acknowledged" pulled a SETTLED batch
# back, and a "settled" flipped a batch the bank had already REJECTED — the
# record the CFO reads would then say paid for money that never left (Fable 5.1
# audit 2026-09-02, M8). The bank is not wrong about its own ledger, so a
# conflict is recorded on the event for a human, not applied.
_BATCH_RANK = {
    FNBBatchSubmission.Status.PENDING:      0,
    FNBBatchSubmission.Status.SUBMITTED:    1,
    FNBBatchSubmission.Status.ACKNOWLEDGED: 2,
    FNBBatchSubmission.Status.SETTLED:      3,
}
_BATCH_TERMINAL = {FNBBatchSubmission.Status.FAILED,
                   FNBBatchSubmission.Status.CANCELLED}


def _may_move(batch, new_status) -> bool:
    cur = batch.status
    if cur == new_status:
        return False
    if new_status == FNBBatchSubmission.Status.FAILED:
        # A reject after settlement is a bank-side correction a human must read.
        return cur != FNBBatchSubmission.Status.SETTLED
    if cur in _BATCH_TERMINAL:
        return False
    return _BATCH_RANK.get(new_status, -1) > _BATCH_RANK.get(cur, -1)


def _refuse(event, batch, new_status) -> None:
    msg = (f'{event.event_type} would move batch {batch.idempotency_key} from '
           f'{batch.status} to {new_status} — kept {batch.status}; check with FNB.')
    log.warning('FNB webhook conflict: %s', msg)
    raise ValueError(msg)


def _on_batch_acknowledged(event: FNBWebhookEvent) -> None:
    payload = event.raw_payload or {}
    ref = (payload.get('reference') or payload.get('batch_reference')
           or payload.get('idempotency_key') or '')
    if not ref:
        return
    batch = (FNBBatchSubmission.objects.filter(idempotency_key=ref).first()
             or FNBBatchSubmission.objects.filter(fnb_reference=ref).first())
    if not batch:
        return
    FNBWebhookEvent.objects.filter(pk=event.pk).update(related_batch=batch)
    if not _may_move(batch, FNBBatchSubmission.Status.ACKNOWLEDGED):
        if batch.status == FNBBatchSubmission.Status.ACKNOWLEDGED:
            return
        _refuse(event, batch, FNBBatchSubmission.Status.ACKNOWLEDGED)
    FNBBatchSubmission.objects.filter(pk=batch.pk).update(
        status          = FNBBatchSubmission.Status.ACKNOWLEDGED,
        acknowledged_at = timezone.now(),
    )


def _on_batch_settled(event: FNBWebhookEvent) -> None:
    payload = event.raw_payload or {}
    ref = (payload.get('reference') or payload.get('batch_reference') or '')
    if not ref:
        return
    batch = (FNBBatchSubmission.objects.filter(idempotency_key=ref).first()
             or FNBBatchSubmission.objects.filter(fnb_reference=ref).first())
    if not batch:
        return
    FNBWebhookEvent.objects.filter(pk=event.pk).update(related_batch=batch)
    if not _may_move(batch, FNBBatchSubmission.Status.SETTLED):
        if batch.status == FNBBatchSubmission.Status.SETTLED:
            return
        _refuse(event, batch, FNBBatchSubmission.Status.SETTLED)
    FNBBatchSubmission.objects.filter(pk=batch.pk).update(
        status     = FNBBatchSubmission.Status.SETTLED,
        settled_at = timezone.now(),
    )


def _on_batch_failed(event: FNBWebhookEvent) -> None:
    payload = event.raw_payload or {}
    ref = (payload.get('reference') or payload.get('batch_reference') or '')
    reason = payload.get('reason', '')
    if not ref:
        return
    batch = FNBBatchSubmission.objects.filter(idempotency_key=ref).first()
    if not batch:
        return
    FNBWebhookEvent.objects.filter(pk=event.pk).update(related_batch=batch)
    if not _may_move(batch, FNBBatchSubmission.Status.FAILED):
        if batch.status == FNBBatchSubmission.Status.FAILED:
            return
        _refuse(event, batch, FNBBatchSubmission.Status.FAILED)
    FNBBatchSubmission.objects.filter(pk=batch.pk).update(
        status         = FNBBatchSubmission.Status.FAILED,
        failure_reason = str(reason)[:500],
    )
