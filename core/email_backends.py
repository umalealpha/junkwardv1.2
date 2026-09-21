"""
core/email_backends.py — Microsoft Graph `sendMail` email backend.

Django ships with SMTP backends. Microsoft 365 increasingly blocks
Basic-Auth SMTP — the modern path is OAuth2 client_credentials against
Entra ID + a POST to the Graph `users/{upn}/sendMail` endpoint.

Configure via:

    EMAIL_BACKEND          = 'core.email_backends.MicrosoftGraphEmailBackend'
    MICROSOFT_TENANT_ID    = '...uuid...'
    MICROSOFT_CLIENT_ID    = '...uuid...'
    MICROSOFT_CLIENT_SECRET= '...secret...'
    MICROSOFT_SENDER_UPN   = 'omni@alphadirect.co.bw'

Required Entra app permission (admin-consented):
    Mail.Send  (Application)

Optionally narrow the app to a single mailbox via M365 "Application
Access Policy" (so the app can ONLY send-as omni@...).

Caches the access token for its `expires_in - 30s` window. Falls back
to raising `smtplib.SMTPException` so callers can catch the same way
they catch SMTP errors.
"""
from __future__ import annotations

import base64
import logging
import time
from email.mime.base import MIMEBase
from typing import Any, Dict, List, Optional

import requests
from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.message import EmailMessage

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Token cache — module-level, in-memory, sufficient for a single gunicorn
# worker. For multi-worker token sharing, swap for django.core.cache.
# ---------------------------------------------------------------------------
_TOKEN_CACHE: Dict[str, Dict[str, Any]] = {}


def _get_token() -> str:
    """Fetch (or reuse) an Entra ID access token for Graph."""
    tenant = getattr(settings, 'MICROSOFT_TENANT_ID', '') or ''
    cid    = getattr(settings, 'MICROSOFT_CLIENT_ID', '') or ''
    secret = getattr(settings, 'MICROSOFT_CLIENT_SECRET', '') or ''
    if not (tenant and cid and secret):
        raise RuntimeError(
            'Microsoft Graph email backend requires MICROSOFT_TENANT_ID, '
            'MICROSOFT_CLIENT_ID and MICROSOFT_CLIENT_SECRET in settings.'
        )

    cached = _TOKEN_CACHE.get(cid)
    if cached and cached['expires_at'] > time.time() + 30:
        return cached['token']

    url = f'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token'
    resp = requests.post(
        url,
        data={
            'client_id':     cid,
            'client_secret': secret,
            'scope':         'https://graph.microsoft.com/.default',
            'grant_type':    'client_credentials',
        },
        timeout=15,
    )
    if resp.status_code != 200:
        log.warning('Graph token fetch failed %s: %s',
                    resp.status_code, resp.text[:200])
        raise RuntimeError(
            f'Entra ID token endpoint returned {resp.status_code}: '
            f'{resp.text[:200]}'
        )
    body = resp.json()
    token = body['access_token']
    expires_in = int(body.get('expires_in', 3600))
    _TOKEN_CACHE[cid] = {
        'token':      token,
        'expires_at': time.time() + expires_in,
    }
    return token


# ---------------------------------------------------------------------------
# Django → Graph payload conversion
# ---------------------------------------------------------------------------
def _addr(s: str) -> Dict[str, Dict[str, str]]:
    """'Name <addr>' → {emailAddress: {address: addr, name: Name}}."""
    if not s:
        return {'emailAddress': {'address': ''}}
    if '<' in s and s.endswith('>'):
        name, _, rest = s.partition('<')
        return {
            'emailAddress': {
                'address': rest.rstrip('>').strip(),
                'name':    name.strip(),
            },
        }
    return {'emailAddress': {'address': s.strip()}}


def _attachments_payload(msg: EmailMessage) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for att in msg.attachments:
        # Django attachment forms: (filename, content, mimetype) tuple OR MIMEBase.
        if isinstance(att, MIMEBase):
            filename = att.get_filename() or 'attachment'
            content  = att.get_payload(decode=True) or b''
            mimetype = att.get_content_type() or 'application/octet-stream'
        else:
            filename, content, mimetype = att
            if isinstance(content, str):
                content = content.encode('utf-8')
            mimetype = mimetype or 'application/octet-stream'
        out.append({
            '@odata.type':  '#microsoft.graph.fileAttachment',
            'name':         filename,
            'contentType':  mimetype,
            'contentBytes': base64.b64encode(content).decode('ascii'),
        })
    return out


def _build_graph_message(msg: EmailMessage) -> Dict[str, Any]:
    """Translate Django EmailMessage → Graph sendMail JSON body.

    Graph accepts a single body, so an attached text/html alternative
    (EmailMultiAlternatives / send_html_with_cfo_cc) must win over the plain
    `body` — otherwise the HTML is silently dropped and only the plain-text
    fallback is sent. Bug fix 2026-06-08: previously this read only
    msg.content_subtype + msg.body and ignored msg.alternatives, so every
    HTML email went out as plain text.
    """
    html_alt = None
    for content, mimetype in (getattr(msg, 'alternatives', None) or []):
        if (mimetype or '').lower() == 'text/html':
            html_alt = content
            break
    if html_alt is not None:
        body_type, body_content = 'HTML', html_alt
    elif (msg.content_subtype or '').lower() == 'html':
        body_type, body_content = 'HTML', (msg.body or '')
    else:
        body_type, body_content = 'Text', (msg.body or '')
    payload: Dict[str, Any] = {
        'message': {
            'subject': msg.subject or '',
            'body': {
                'contentType': body_type,
                'content':     body_content,
            },
            'toRecipients':  [_addr(a) for a in (msg.to or []) if a],
            'ccRecipients':  [_addr(a) for a in (msg.cc or []) if a],
            'bccRecipients': [_addr(a) for a in (msg.bcc or []) if a],
        },
        'saveToSentItems': True,
    }
    if msg.reply_to:
        payload['message']['replyTo'] = [_addr(a) for a in msg.reply_to if a]
    # Graph only accepts custom internet headers whose name starts with 'x-'.
    # Needed so auto-replies can carry X-Auto-Response-Suppress and not set off
    # the recipient's own out-of-office. Anything else Django put in headers=
    # (Subject, To, Reply-To…) is already handled above, so it is skipped here.
    x_headers = [
        {'name': name, 'value': str(value)}
        for name, value in (getattr(msg, 'extra_headers', None) or {}).items()
        if name.lower().startswith('x-')
    ]
    if x_headers:
        payload['message']['internetMessageHeaders'] = x_headers[:5]  # Graph caps at 5
    attachments = _attachments_payload(msg)
    if attachments:
        payload['message']['attachments'] = attachments
    return payload


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------
class MicrosoftGraphEmailBackend(BaseEmailBackend):
    """Send each Django EmailMessage via POST users/{upn}/sendMail."""

    GRAPH_BASE = 'https://graph.microsoft.com/v1.0'

    def __init__(self, fail_silently: bool = False, **kwargs):
        super().__init__(fail_silently=fail_silently, **kwargs)
        self.timeout = float(getattr(settings, 'EMAIL_TIMEOUT', 20))

    def _sender_upn(self, msg: EmailMessage) -> str:
        # Prefer the EmailMessage's from_email when present (less the
        # display name) so /omni / report runs can use DEFAULT_FROM_EMAIL.
        raw = (msg.from_email or '').strip()
        if raw and '<' in raw and raw.endswith('>'):
            raw = raw.partition('<')[2].rstrip('>').strip()
        return raw or getattr(settings, 'MICROSOFT_SENDER_UPN', '') or \
               getattr(settings, 'EMAIL_HOST_USER', 'omni@alphadirect.co.bw')

    def send_messages(self, email_messages):
        if not email_messages:
            return 0
        try:
            token = _get_token()
        except Exception as exc:    # noqa: BLE001
            log.warning('Graph token unavailable: %s', exc)
            if not self.fail_silently:
                raise
            return 0
        sent = 0
        for msg in email_messages:
            try:
                upn = self._sender_upn(msg)
                url = f'{self.GRAPH_BASE}/users/{upn}/sendMail'
                payload = _build_graph_message(msg)
                resp = requests.post(
                    url,
                    json=payload,
                    headers={
                        'Authorization': f'Bearer {token}',
                        'Content-Type':  'application/json',
                    },
                    timeout=self.timeout,
                )
                if resp.status_code in (200, 202):
                    sent += 1
                else:
                    log.warning('Graph sendMail %s -> %s: %s',
                                upn, resp.status_code, resp.text[:300])
                    if not self.fail_silently:
                        raise RuntimeError(
                            f'Graph sendMail returned {resp.status_code}: '
                            f'{resp.text[:300]}'
                        )
            except Exception as exc:    # noqa: BLE001
                log.warning('Graph send failed: %s', exc)
                if not self.fail_silently:
                    raise
        return sent


# ─────────────────────────────────────────────────────────────────────────────
# GuardedEmailBackend — CFO instruction 2026-07-25, option A
# ─────────────────────────────────────────────────────────────────────────────
# Why this exists at BACKEND level rather than in a helper.
#
# `core.notifications.send_with_cfo_cc` already stripped blocklisted addresses,
# but ~18 senders build EmailMessage / EmailMultiAlternatives / send_mail
# DIRECTLY and never touched it — including the two that email the Omni sign-in
# code and the password-reset code. The shared `admin` account's address was
# admin@alphadirect.co.bw until 2026-07-25, so its sign-in codes were delivered
# into a mailbox worked by junior clerks: anyone reading it could finish the
# login. A helper can always be bypassed by the next new sender. A backend
# cannot — every Django email goes through send_messages(), so this is the only
# bypass-proof place to put the rule.
#
# TWO DIFFERENT LISTS, deliberately not merged:
#   * NEVER_DELIVER_EMAILS — shared/junior mailboxes that must not receive
#     ANY omni mail, ever, however the message was built. Hard-stripped here.
#   * NEVER_CC_EMAILS (core.notifications._NEVER_CC) — people who must not be
#     AUTO-CC'd as decision-makers, but who are perfectly valid recipients when
#     a caller names them. Arun and Arjun are on that list, and
#     integrations.check_timedoctor_token sends to Arjun DIRECTLY on purpose so
#     the strip does not apply. Enforcing that list here would silently drop the
#     token-renewal reminder and let the Time Doctor token lapse. So it is NOT
#     enforced here — only the hard list is.
#
# Bodies are NEVER logged. The log records metadata only (subject, addresses,
# counts). Storing bodies would put every payslip figure and every live sign-in
# code into the database, which would be a worse leak than the one being fixed.

_DEFAULT_NEVER_DELIVER = ('admin@alphadirect.co.bw',)

OUTBOUND_SENT    = 'sent'
OUTBOUND_BLOCKED = 'blocked'     # every recipient was barred; not delivered
OUTBOUND_FAILED  = 'failed'      # inner backend raised


def never_deliver_addresses() -> set[str]:
    """Mailboxes that must never receive omni mail, lower-cased."""
    raw = getattr(settings, 'NEVER_DELIVER_EMAILS', None) or _DEFAULT_NEVER_DELIVER
    return {(a or '').strip().lower() for a in raw if (a or '').strip()}


def _addr_only(value: str) -> str:
    """'Name <a@b.c>' -> 'a@b.c' (lower-cased); plain addresses pass through."""
    from email.utils import parseaddr
    return (parseaddr(value or '')[1] or '').strip().lower()


class GuardedEmailBackend(BaseEmailBackend):
    """Wraps the real backend: strips barred mailboxes, then logs what went out.

    Deliberately a wrapper, not a subclass: settings promote the inner backend
    through console -> SMTP -> Graph depending on which credentials are present,
    so there is no single class to inherit from. The resolved inner path is read
    from EMAIL_INNER_BACKEND.
    """

    def __init__(self, fail_silently: bool = False, **kwargs):
        super().__init__(fail_silently=fail_silently)
        from django.core.mail import get_connection
        inner_path = getattr(
            settings, 'EMAIL_INNER_BACKEND',
            'django.core.mail.backends.smtp.EmailBackend')
        self._inner = get_connection(
            backend=inner_path, fail_silently=fail_silently, **kwargs)

    # -- sanitising ------------------------------------------------------
    def _scrub(self, msg) -> list[str]:
        """Remove barred addresses from to/cc/bcc. Returns what was removed."""
        barred = never_deliver_addresses()
        if not barred:
            return []
        removed: list[str] = []
        for field in ('to', 'cc', 'bcc'):
            current = list(getattr(msg, field, None) or [])
            if not current:
                continue
            kept = []
            for entry in current:
                if _addr_only(entry) in barred:
                    removed.append(entry)
                else:
                    kept.append(entry)
            if len(kept) != len(current):
                setattr(msg, field, kept)
        return removed

    # -- logging ---------------------------------------------------------
    def _log(self, msg, removed, status):
        """Record metadata. Never the body. Never raises."""
        try:
            from core.models import OutboundEmailLog
            OutboundEmailLog.objects.create(
                subject=(msg.subject or '')[:255],
                from_email=(getattr(msg, 'from_email', '') or '')[:255],
                to_addrs=', '.join(getattr(msg, 'to', None) or [])[:2000],
                cc_addrs=', '.join(getattr(msg, 'cc', None) or [])[:2000],
                bcc_addrs=', '.join(getattr(msg, 'bcc', None) or [])[:2000],
                blocked_addrs=', '.join(removed)[:2000],
                attachment_count=len(getattr(msg, 'attachments', None) or []),
                status=status,
            )
        except Exception:                        # noqa: BLE001
            # Oversight must never be the reason a payslip or a sign-in code
            # fails to send.
            log.exception('OutboundEmailLog write failed (mail itself unaffected)')

    def send_messages(self, email_messages):
        if not email_messages:
            return 0
        deliverable, sent = [], 0
        for msg in email_messages:
            removed = self._scrub(msg)
            if not (msg.to or msg.cc or msg.bcc):
                # Everyone on it was barred. Do NOT deliver — for the sign-in
                # and reset codes this is the whole point: a one-time code must
                # never reach a shared mailbox.
                self._log(msg, removed, OUTBOUND_BLOCKED)
                log.warning('Email suppressed, all recipients barred: %r -> %s',
                            (msg.subject or '')[:120], removed)
                continue
            deliverable.append((msg, removed))

        if deliverable:
            try:
                sent = self._inner.send_messages([m for m, _ in deliverable]) or 0
            except Exception:                    # noqa: BLE001
                for msg, removed in deliverable:
                    self._log(msg, removed, OUTBOUND_FAILED)
                raise
            for msg, removed in deliverable:
                self._log(msg, removed, OUTBOUND_SENT)
        return sent

    # Delegate connection handling to the real backend.
    def open(self):
        return self._inner.open()

    def close(self):
        return self._inner.close()
