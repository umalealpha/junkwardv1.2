"""taskboard/fnb_email_view_helpers.py — read FNB "Fully Processed" emails for the
payment-requests catch-up screen (CFO 2026-08-24).

Kept in its own module (not in payment_views, not in the fnb_email_autoclose
management command) so the web view can reuse the exact same read+parse without a
circular import: the command imports from taskboard.payment_views, so payment_views
must not import the command back.
"""
from __future__ import annotations

import logging
from datetime import timedelta

import requests
from django.conf import settings
from django.utils import timezone

from fnb.email_reconcile import parse_result_email

log = logging.getLogger(__name__)

_FNB_SENDER = 'noreply@fnb.co.za'
# Sentinel cached briefly after a failed read so an outage does not re-hit Graph
# on every payment-screen load; readers treat it as "unavailable" (return None).
_UNAVAILABLE = '__unavailable__'
# The hourly fnb_email_autoclose run saves the parsed list here, so a page load
# reads a file instead of the mailbox. The default cache is per-process locmem
# (six gunicorn workers), so a cache alone made each worker pay the full mailbox
# read on its own every 15 minutes — the 8.5-11s /payment-requests load.
_STORE_MAX_AGE_S = 3 * 3600
# FNB's own emails in the window, not the whole mailbox. The old cap of 500 was
# counted over EVERY message, so on a busy mailbox it saw ~4 days of a 30-day
# window: 40 of 531 paid confirmations on 19-Sep-2026.
_MAX_FNB_MESSAGES = 3000


def _mailbox() -> str:
    return (getattr(settings, 'FNB_EMAIL_READ_MAILBOX', '')
            or getattr(settings, 'AUTO_REPLY_READ_MAILBOX', ''))


def _sender(msg: dict) -> str:
    box = (msg.get('from') or msg.get('sender') or {}).get('emailAddress', {})
    return (box.get('address') or '').strip().lower()


def _body_text(msg: dict) -> str:
    b = msg.get('body') or {}
    return b.get('content') or msg.get('bodyPreview') or ''


def fetch_paid_fnb_emails(hours: int) -> list[dict]:
    """The FNB "Fully Processed" payment confirmations from the CFO mailbox in the
    last `hours`, parsed to [{ref, amount, status, paid, date}] (paid ones only).

    Raises ValueError if no reader mailbox is configured, RuntimeError on a Graph
    read failure — the caller turns these into friendly 5xx responses.
    """
    mailbox = _mailbox()
    if not mailbox:
        raise ValueError('No FNB reader mailbox is configured — ask IT to set it up.')

    # Imported lazily: this pulls the Mail.Read reader app the auto-reply command
    # already uses; keeping it out of module import avoids dragging Graph setup
    # into every payment_views import.
    from core.management.commands.auto_reply_omni_mail import _reader_token, GRAPH_BASE

    since = (timezone.now() - timedelta(hours=hours)).strftime('%Y-%m-%dT%H:%M:%SZ')
    url = (f'{GRAPH_BASE}/users/{mailbox}/messages'
           f"?$filter=receivedDateTime ge {since} "
           f"and from/emailAddress/address eq '{_FNB_SENDER}'"
           f'&$select=id,subject,from,sender,receivedDateTime,body,bodyPreview'
           f'&$top=100&$orderby=receivedDateTime desc')
    headers = {'Authorization': f'Bearer {_reader_token()}',
               'Prefer': 'outlook.body-content-type="text"'}

    out: list[dict] = []
    fetched = 0
    while url and fetched < _MAX_FNB_MESSAGES:
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f'Graph read {r.status_code}: {r.text[:200]}')
        body = r.json()
        for m in body.get('value', []):
            fetched += 1
            if _sender(m) != _FNB_SENDER:
                continue
            parsed = parse_result_email(_body_text(m))
            if parsed and parsed['paid']:
                parsed['date'] = (m.get('receivedDateTime') or '')[:10]
                out.append(parsed)
        url = body.get('@odata.nextLink')
    return out


def _store():
    from django.core.cache.backends.filebased import FileBasedCache
    return FileBasedCache(getattr(settings, 'FNB_PAID_EMAIL_STORE_DIR',
                                  '/tmp/omni-fnb-paid-emails'), {'TIMEOUT': None})


def refresh_paid_fnb_emails_store(hours: int) -> int:
    """Read the mailbox once and save the parsed list for every web worker.
    Called by the hourly fnb_email_autoclose run. Raises like the fetch does."""
    emails = fetch_paid_fnb_emails(hours)
    _store().set(f'fnb_paid_emails:{int(hours)}',
                 {'at': timezone.now().timestamp(), 'emails': emails})
    return len(emails)


def _stored(hours: int) -> list[dict] | None:
    try:
        rec = _store().get(f'fnb_paid_emails:{int(hours)}')
    except (OSError, ValueError, EOFError):
        return None
    if not rec or timezone.now().timestamp() - rec.get('at', 0) > _STORE_MAX_AGE_S:
        return None
    return rec.get('emails')


def fetch_paid_fnb_emails_cached(hours: int) -> list[dict] | None:
    """`fetch_paid_fnb_emails` but cached ~15 min and FAIL-SAFE, for the hot
    payment-screen path.

    Returns the parsed paid emails on success (a list, possibly empty), or **None
    when the mailbox could not be read** — a distinct "unavailable" that the caller
    surfaces, rather than a silent [] that would look like "nothing already paid"
    and could hide a double payment (Fable F1, 2026-09-06). A failure is LOGGED and
    negative-cached for 2 min, so a Graph outage can never turn one 15-min read into
    a 30s timeout on every CFO page load."""
    from django.core.cache import cache
    key = f'fnb_paid_emails:{int(hours)}'
    hit = cache.get(key)
    if hit == _UNAVAILABLE:
        return None
    if hit is not None:
        return hit
    stored = _stored(hours)
    if stored is not None:
        cache.set(key, stored, 900)
        return stored
    try:
        emails = fetch_paid_fnb_emails(hours)
    except ValueError as exc:            # no mailbox/reader configured — a real fault
        log.error('FNB paid-email read misconfigured: %s', exc)
        cache.set(key, _UNAVAILABLE, 120)
        return None
    except Exception as exc:             # transient Graph error (timeout / 5xx / 429)
        log.warning('FNB paid-email read failed (transient): %s', exc)
        cache.set(key, _UNAVAILABLE, 120)
        return None
    cache.set(key, emails, 900)  # 15 min; the hourly auto-close keeps the box fresh
    try:
        _store().set(f'fnb_paid_emails:{int(hours)}',
                     {'at': timezone.now().timestamp(), 'emails': emails})
    except OSError as exc:
        log.warning('FNB paid-email store not writable: %s', exc)
    return emails
