"""
fnb/notifications.py

Pull real-time notifications from FNB via the Notification Execution API.

FNB's V2 Notifications API is a PULL-style feed: we poll
/notificationExecution/retrieveNewNotifications/v1 every minute. Each
notification represents a posting on one of our accounts (credit/debit
confirmation, return, reversal, etc.).

We store the raw payload + a short-summary on the existing
`FNBWebhookEvent` model. The name was chosen back when we expected push
webhooks; pull notifications use the same model with `direction=INBOUND`
so the downstream consumers — bank-rec, payment-status sync, audit log —
don't need to care about the transport.

Scheduling: `manage.py fnb_poll_notifications` is run by a cron entry on
prod every minute. The command is safe to overlap (database upsert on
the FNB notification id), so we don't bother with a lock.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Iterable

from django.db import transaction
from django.utils import timezone

from .client import FNBClient, FNBNotConfigured
from .endpoints import NOTIFICATIONS_NEW, NOTIFICATIONS_FILTERED
from .models import FNBSyncLog, FNBWebhookEvent
from .webhooks import dispatch_webhook

log = logging.getLogger(__name__)


def poll_new_notifications(*,
                           account_ids: list[str] | None = None,
                           limit: int = 50,
                           user=None) -> int:
    """Pull every notification FNB has marked as 'new' for us.

    v2 endpoint (confirmed by Kabelo Sekoto, RMB, 2026-05-26):
        POST /notificationExecution/retrieveNewNotifications/v2
        body: { accountIdList: ["63001966639", ...],
                limitNumberOfNotifications: 50 }
    Response (camt.054-style) shows entries under `Ntfctn` array, each
    with an `Id` (e.g. COR0JRSVTBBB000) + nested `Ntry[].TxDtls`.
    """
    client = FNBClient(user=user)
    if account_ids is None:
        from banking.models import BankAccount
        account_ids = list(
            BankAccount.objects.filter(is_active=True)
            .exclude(account_number__in=('', '0'))
            .values_list('account_number', flat=True)
        )
        if not account_ids:
            log.info('FNB notifications: no active bank accounts configured.')
            return 0
    body = {
        'accountIdList':              account_ids,
        'limitNumberOfNotifications': int(limit),
    }
    try:
        resp = client.post(
            NOTIFICATIONS_NEW,
            service        = FNBSyncLog.Service.NOTIFICATION,
            json_body      = body,
            request_summary= f'Poll FNB new notifications (accts={len(account_ids)},lim={limit})',
            extra_headers  = {'X-Request-ID': str(uuid.uuid4())},
        )
    except FNBNotConfigured:
        log.warning('FNB notifications skipped — API not configured.')
        return 0

    items = _extract_items(resp.json or {})
    return _persist_items(items)


def poll_filtered(
    *,
    from_dt: datetime,
    to_dt: datetime,
    account_ids: list[str] | None = None,
    page: int = 1,
    page_size: int = 100,
    user=None,
) -> int:
    """Pull notifications inside a specific window. Useful for back-fill
    after downtime.

    v2 body (confirmed by Kabelo Sekoto, RMB, 2026-05-26):
        {
          "accountIdList": ["63001966639"],
          "pageSize": 10,
          "page": 1,
          "startDate": "2026-05-26",
          "endDate":   "2026-05-26"
        }
    Dates are YYYY-MM-DD only, NOT ISO timestamps. Earlier v1 used
    fromDateTime / toDateTime with full ISO — keep this in mind if we
    ever roll back.
    """
    client = FNBClient(user=user)
    if account_ids is None:
        from banking.models import BankAccount
        account_ids = list(
            BankAccount.objects.filter(is_active=True)
            .exclude(account_number__in=('', '0'))
            .values_list('account_number', flat=True)
        )
        if not account_ids:
            log.info('FNB filtered notifications: no active bank accounts.')
            return 0
    body = {
        'accountIdList': account_ids,
        'pageSize':      int(page_size),
        'page':          int(page),
        'startDate':     from_dt.strftime('%Y-%m-%d'),
        'endDate':       to_dt.strftime('%Y-%m-%d'),
    }
    resp = client.post(
        NOTIFICATIONS_FILTERED,
        service        = FNBSyncLog.Service.NOTIFICATION,
        json_body      = body,
        request_summary= f'Poll FNB filtered notifications {body["startDate"]}..{body["endDate"]}',
        extra_headers  = {'X-Request-ID': str(uuid.uuid4())},
    )
    items = _extract_items(resp.json or {})
    return _persist_items(items)


def _extract_items(payload: dict) -> list[dict]:
    """FNB nests the array under one of several keys depending on version.

    v2 (May 2026) uses camt.054 envelope: top-level GrpHdr + `Ntfctn` list.
    Older v1 used `notifications` / `items` — keep those as fallbacks.
    """
    for key in ('Ntfctn', 'notifications', 'items', 'events', 'records', 'data'):
        v = payload.get(key)
        if isinstance(v, list):
            return v
    if isinstance(payload, list):
        return payload
    return []


def _classify(item: dict) -> tuple[str, str]:
    """camt.054 derives a posting's type from the first Ntry's CdtDbtInd +
    BkTxCd. Both present means FNB told us clearly what happened (a credit or
    a debit, in a recognised family); either missing means we cannot say what
    this posting is."""
    first_ntry = ((item.get('Ntry') or [{}])[0]
                  if isinstance(item.get('Ntry'), list) else {})
    cdt_dbt = (first_ntry.get('CdtDbtInd') or '').upper()
    bk_tx = (((first_ntry.get('BkTxCd') or {}).get('Domn') or {})
             .get('Fmly') or {}).get('Cd', '')
    return cdt_dbt, bk_tx


def process_notification_event(event: FNBWebhookEvent) -> None:
    """Advance ONE polled notification past 'received'. This is the step that
    was missing entirely: `_persist_items` used to create the row and stop,
    with a comment claiming "downstream consumers... are dispatched by
    signals on FNBWebhookEvent post_save" — no such signal exists anywhere in
    this codebase (checked 2026-09-14). So every notification FNB ever sent
    sat at status=received forever; 840 of them on production, the oldest
    from 27-May-2026, none ever verified or moved on.

    A notification arrives over FNB's authenticated pull API (OAuth + TLS to
    FNB, not an unauthenticated public webhook) — there is no separate HMAC
    signature to check the way the push /fnb/webhook/ endpoint checks one, so
    "received but unverified" does not describe it. What DOES need checking
    is whether we could actually classify the posting (CdtDbtInd + BkTxCd). If
    we can, the event is genuinely understood — verify it and dispatch it,
    same as a signed push webhook would be. If we can't, that is exactly the
    "cannot be verified" case: it must stay VISIBLE to a person (status=failed
    with a reason), never silently disappear.

    Only ever touches a row still at status=received — re-checked here (not
    just at the call site) so this function is safe to call more than once on
    the same event without re-dispatching it (panel review, 2026-09-14).
    """
    event.refresh_from_db(fields=['status'])
    if event.status != FNBWebhookEvent.Status.RECEIVED:
        return
    cdt_dbt, bk_tx = _classify(event.raw_payload or {})
    if not (cdt_dbt and bk_tx):
        FNBWebhookEvent.objects.filter(pk=event.pk).update(
            status=FNBWebhookEvent.Status.FAILED,
            error_message='Could not classify this notification (missing '
                          'CdtDbtInd/BkTxCd) — check it by hand.',
        )
        log.warning('FNB notification %s could not be classified — flagged '
                    'for review.', event.external_id)
        return
    FNBWebhookEvent.objects.filter(pk=event.pk).update(
        status=FNBWebhookEvent.Status.VERIFIED)
    event.status = FNBWebhookEvent.Status.VERIFIED
    dispatch_webhook(event)


@transaction.atomic
def _persist_items(items: Iterable[dict]) -> int:
    """Upsert each notification by FNB's external id, then advance it past
    'received' immediately — see process_notification_event() for why that
    step used to never happen."""
    inserted = 0
    for item in items:
        # v2 camt.054: top-level `Id` (e.g. COR0JRSVTBBB000) + `Ntry[].BkTxCd`
        ext_id = str(item.get('Id') or item.get('id') or item.get('notificationId')
                     or item.get('eventId') or '')
        if not ext_id:
            log.warning('FNB notification with no id: %r', item)
            continue
        cdt_dbt, bk_tx = _classify(item)
        event_type = (item.get('type') or item.get('eventType')
                      or item.get('category')
                      or f'camt054_{cdt_dbt}_{bk_tx}' or 'unknown')[:50]
        # Each item gets its OWN savepoint, nested inside the outer atomic()
        # for the whole batch. dispatch_webhook() (called by
        # process_notification_event below) has its own `except Exception:`
        # that issues a further .update() on a DB-level failure; without a
        # savepoint that second query runs inside an already-poisoned
        # transaction, raises TransactionManagementError, escapes, and rolls
        # back every row this poll inserted — including ones FNB will never
        # resend (Fable 5.1 review, 2026-09-14 — same class as L26: a swallow
        # inside an outer atomic() silently rolls back the caller's save).
        with transaction.atomic():
            # get_or_create, not update_or_create: FNB's "new" feed can
            # redeliver an id we already advanced past received, and
            # update_or_create's defaults would stamp status back to
            # RECEIVED on every such repeat, forcing a re-dispatch of an
            # already-settled event (panel review, 2026-09-14). An existing
            # row is left exactly as it is; only a brand-new row is created
            # (and then advanced) here.
            event, was_created = FNBWebhookEvent.objects.get_or_create(
                external_id=ext_id,
                defaults={
                    'event_type':  event_type,
                    'received_at': timezone.now(),
                    'raw_payload': item,
                    'status':      FNBWebhookEvent.Status.RECEIVED,
                },
            )
            if was_created:
                inserted += 1
            # process_notification_event() re-checks status itself, so a
            # repeat delivery of an already-processed/failed event is a
            # no-op here too.
            process_notification_event(event)
    log.info('FNB notifications: %d new of %d received', inserted, len(list(items)) if not isinstance(items, list) else len(items))
    return inserted
