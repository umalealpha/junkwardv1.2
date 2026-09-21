"""
auto_reply_omni_mail — nudge staff who email excoboard@ / omni@ instead of using Omni.

CFO directive 2026-07-31: "people are sending emails to omni@ or excoboard@ when they
need features or bugs. I want them to start using the Omni bug report or the Omni new
feature request. Create an auto response to everyone who sends email to these
addresses, explain it in a fun manner, make the bug report button big and flashy.
Internal emails only."

How it works
------------
Neither excoboard@ nor omni@ can be read directly: the tenant's app-only access policy
scopes our Graph apps to pganesharajah@ only (checked 2026-07-31 — both return
"Blocked by tenant configured AppOnly AccessPolicy settings"). But mail addressed to
excoboard@ and omni@ *is* delivered into the CFO's own mailbox, which we CAN read. So
this command:

  1. reads the CFO's inbox via Graph (reader app: GRAPH_READER_* — Mail.Read),
  2. keeps only messages whose To/Cc includes one of AUTO_REPLY_WATCH_ADDRESSES,
  3. keeps only senders on an internal domain (AUTO_REPLY_INTERNAL_DOMAINS),
  4. drops system senders, bounces and anything already auto-generated,
  5. replies once per sender per AUTO_REPLY_COOLOFF_DAYS, from omni@ via the normal
     Django mail path (Graph sendMail).

Loop safety: the reply carries `X-Auto-Response-Suppress: All` (Graph only forwards
x-* headers); system senders (omni@, excoboard@, noreply…) are never
replied to; and MailAutoReply gives a hard per-sender cool-off even if a header is
missed. The reply never CCs the CFO — that would put one copy of every nudge back in
the inbox we are trying to empty.

Usage
-----
    python manage.py auto_reply_omni_mail --dry-run        # show who would get it
    python manage.py auto_reply_omni_mail --limit 1        # send one, for real
    python manage.py auto_reply_omni_mail --to me@x.co.bw  # force one test send
"""
from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any, Dict, List, Optional

import requests
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import MailAutoReply

log = logging.getLogger(__name__)

GRAPH_BASE = 'https://graph.microsoft.com/v1.0'

# Senders that are machines, mailing lists or the watched mailboxes themselves.
# Replying to any of these is either pointless or a loop.
_SYSTEM_LOCALPARTS = {
    'omni', 'excoboard', 'noreply', 'no-reply', 'donotreply', 'do-not-reply',
    'postmaster', 'mailer-daemon', 'notifications', 'notification', 'alerts',
    'alert', 'admin', 'cfo', 'hr', 'allusers', 'finance', 'headoffice', 'manco',
    'transformation', 'app-scope-mailsend', 'erp',
}
_SKIP_SUBJECT_PREFIXES = (
    'automatic reply', 'auto reply', 'auto-reply', 'undeliverable',
    'out of office', 'delivery has failed', 'delivery status notification',
)

_TOKEN_CACHE: Dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Graph reader (separate app from the sender — this one has Mail.Read)
# ---------------------------------------------------------------------------
def _reader_token() -> str:
    tenant = getattr(settings, 'GRAPH_READER_TENANT_ID', '') or ''
    cid    = getattr(settings, 'GRAPH_READER_CLIENT_ID', '') or ''
    secret = getattr(settings, 'GRAPH_READER_CLIENT_SECRET', '') or ''
    if not (tenant and cid and secret):
        raise RuntimeError(
            'auto_reply_omni_mail needs GRAPH_READER_TENANT_ID / _CLIENT_ID / '
            '_CLIENT_SECRET (an Entra app with Mail.Read on the watched mailbox).'
        )
    cached = _TOKEN_CACHE.get(cid)
    if cached and cached['expires_at'] > time.time() + 30:
        return cached['token']

    resp = requests.post(
        f'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token',
        data={
            'client_id':     cid,
            'client_secret': secret,
            'scope':         'https://graph.microsoft.com/.default',
            'grant_type':    'client_credentials',
        },
        timeout=20,
    )
    if resp.status_code != 200:
        raise RuntimeError(f'Graph token {resp.status_code}: {resp.text[:200]}')
    body = resp.json()
    _TOKEN_CACHE[cid] = {
        'token': body['access_token'],
        'expires_at': time.time() + int(body.get('expires_in', 3600)),
    }
    return body['access_token']


def _fetch_recent(minutes: int) -> List[Dict[str, Any]]:
    """Inbox messages received in the last `minutes`, newest first."""
    mailbox = getattr(settings, 'AUTO_REPLY_READ_MAILBOX', '')
    since = (timezone.now() - timedelta(minutes=minutes)).strftime('%Y-%m-%dT%H:%M:%SZ')
    url = (
        f'{GRAPH_BASE}/users/{mailbox}/mailFolders/inbox/messages'
        f'?$filter=receivedDateTime ge {since}'
        f'&$select=id,subject,from,sender,toRecipients,ccRecipients,'
        f'receivedDateTime,internetMessageId'
        f'&$top=100&$orderby=receivedDateTime desc'
    )
    out: List[Dict[str, Any]] = []
    token = _reader_token()
    while url and len(out) < 300:
        r = requests.get(url, headers={'Authorization': f'Bearer {token}'}, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f'Graph read {r.status_code}: {r.text[:250]}')
        body = r.json()
        out.extend(body.get('value', []))
        url = body.get('@odata.nextLink')
    return out


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------
def _addresses(msg: Dict[str, Any], key: str) -> List[str]:
    return [
        (r.get('emailAddress', {}).get('address') or '').strip().lower()
        for r in (msg.get(key) or [])
    ]


def _sender(msg: Dict[str, Any]) -> str:
    box = (msg.get('from') or msg.get('sender') or {}).get('emailAddress', {})
    return (box.get('address') or '').strip().lower()


def _sender_name(msg: Dict[str, Any]) -> str:
    box = (msg.get('from') or msg.get('sender') or {}).get('emailAddress', {})
    return (box.get('name') or '').strip()


def _is_internal(email: str) -> bool:
    domain = email.rpartition('@')[2]
    return domain in {
        d.strip().lower()
        for d in getattr(settings, 'AUTO_REPLY_INTERNAL_DOMAINS', [])
    }


def _is_system(email: str) -> bool:
    if email.partition('@')[0] in _SYSTEM_LOCALPARTS:
        return True
    # Never nudge the owner of the mailbox we are reading (he sends TO excoboard@
    # himself), nor anyone on the explicit exclude list.
    if email == (getattr(settings, 'AUTO_REPLY_READ_MAILBOX', '') or '').lower():
        return True
    return email in {
        e.strip().lower()
        for e in getattr(settings, 'AUTO_REPLY_EXCLUDE_EMAILS', [])
    }


def _watched(msg: Dict[str, Any]) -> bool:
    watch = {
        a.strip().lower()
        for a in getattr(settings, 'AUTO_REPLY_WATCH_ADDRESSES', [])
    }
    got = set(_addresses(msg, 'toRecipients')) | set(_addresses(msg, 'ccRecipients'))
    return bool(watch & got)


def _skip_subject(subject: str) -> bool:
    s = (subject or '').strip().lower()
    return any(s.startswith(p) for p in _SKIP_SUBJECT_PREFIXES)


# ---------------------------------------------------------------------------
# The message itself — fun, and one very big flashy button
# ---------------------------------------------------------------------------
def _first_name(name: str, email: str) -> str:
    if name:
        # Outlook gives "Ikanyeng A Sechele" or "Sechele, Ikanyeng"
        part = name.split(',')[-1].strip() if ',' in name else name.strip()
        first = part.split()[0] if part.split() else ''
        if first and first.lower() not in {'mr', 'mrs', 'ms', 'dr'}:
            return first
    return (email.partition('@')[0] or 'there').split('.')[0].title()


def build_html(first_name: str, base_url: str) -> str:
    bug     = f'{base_url}/report-bug'
    feature = f'{base_url}/report-bug?intent=feature'
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#F3F4F6;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F3F4F6;padding:24px 12px;">
<tr><td align="center">
<table role="presentation" width="620" cellpadding="0" cellspacing="0"
       style="width:620px;max-width:100%;background:#FFFFFF;border-radius:14px;overflow:hidden;
              font-family:'Segoe UI',Arial,sans-serif;box-shadow:0 8px 28px rgba(13,27,42,0.13);">

  <!-- header -->
  <tr><td style="background:#0D1B2A;padding:26px 30px 22px;">
    <div style="color:#F4A623;font-size:11px;letter-spacing:2.4px;text-transform:uppercase;font-weight:700;">
      Omni &middot; Alpha Direct
    </div>
    <div style="color:#FFFFFF;font-size:25px;font-weight:700;line-height:1.25;margin-top:8px;">
      Thanks for the email &mdash; but there&rsquo;s a faster door&nbsp;🚪
    </div>
  </td></tr>

  <!-- body -->
  <tr><td style="padding:26px 30px 6px;color:#1F2937;font-size:15px;line-height:1.6;">
    <p style="margin:0 0 14px;">Hi {first_name},</p>
    <p style="margin:0 0 14px;">
      Sharp sharp &mdash; your email landed safely, and a human will still read it.
      But here&rsquo;s the honest truth: this mailbox is <i>somebody&rsquo;s inbox</i>.
      Your bug queues up behind budgets, board packs and 400 other emails, and it can sit
      there for days.
    </p>
    <p style="margin:0 0 6px;">
      Omni has its own front door. It goes <b>straight onto the fix-it list</b>, gives you a
      reference number, and emails you the moment something changes. Same effort, much
      faster answer.
    </p>
  </td></tr>

  <!-- THE BIG FLASHY BUTTON -->
  <tr><td align="center" style="padding:22px 24px 6px;">
    <table role="presentation" cellpadding="0" cellspacing="0" width="100%"
           style="background:#FFF8EC;border:2px dashed #F4A623;border-radius:16px;">
      <tr><td align="center" style="padding:26px 20px 24px;">
        <div style="font-size:34px;line-height:1;margin-bottom:8px;">🐞</div>
        <div style="color:#0D1B2A;font-size:13px;font-weight:700;letter-spacing:1.6px;
                    text-transform:uppercase;margin-bottom:14px;">
          Something broken? Press this
        </div>
        <a href="{bug}"
           style="display:block;background:#F4A623;color:#0D1B2A;text-decoration:none;
                  font-size:23px;font-weight:800;letter-spacing:0.4px;padding:22px 18px;
                  border-radius:12px;border-bottom:5px solid #C97F0C;
                  box-shadow:0 8px 22px rgba(244,166,35,0.55);">
          🐞&nbsp; REPORT A BUG &nbsp;&rarr;
        </a>
        <div style="color:#6B7280;font-size:12px;margin-top:12px;">
          50 words about what happened + 2 screenshots. Two minutes, done.
        </div>
      </td></tr>
    </table>
  </td></tr>

  <!-- second door -->
  <tr><td align="center" style="padding:14px 24px 4px;">
    <table role="presentation" cellpadding="0" cellspacing="0" width="100%"
           style="border:1px solid #E5E7EB;border-radius:14px;">
      <tr><td align="center" style="padding:20px;">
        <div style="color:#0D1B2A;font-size:13px;font-weight:700;letter-spacing:1.4px;
                    text-transform:uppercase;margin-bottom:12px;">
          💡 Not broken &mdash; just missing?
        </div>
        <a href="{feature}"
           style="display:inline-block;background:#0D1B2A;color:#FFFFFF;text-decoration:none;
                  font-size:16px;font-weight:700;padding:14px 26px;border-radius:10px;">
          ASK FOR A NEW FEATURE &nbsp;&rarr;
        </a>
        <div style="color:#6B7280;font-size:12px;margin-top:11px;">
          25 words on the idea. No screenshots needed. This is where new features start.
        </div>
      </td></tr>
    </table>
  </td></tr>

  <!-- why -->
  <tr><td style="padding:24px 30px 4px;color:#1F2937;font-size:14px;line-height:1.6;">
    <div style="color:#0D1B2A;font-size:12px;font-weight:700;letter-spacing:1.4px;
                text-transform:uppercase;border-bottom:2px solid #F4A623;
                padding-bottom:6px;margin-bottom:12px;">
      Why it&rsquo;s worth the two clicks
    </div>
    <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="font-size:14px;">
      <tr><td width="26" valign="top" style="padding:4px 0;">🎟️</td>
          <td style="padding:4px 0;">You get a <b>reference number</b> &mdash; nothing quietly disappears.</td></tr>
      <tr><td width="26" valign="top" style="padding:4px 0;">⚡</td>
          <td style="padding:4px 0;">It is looked at <b>within the hour</b>, not whenever the inbox clears.</td></tr>
      <tr><td width="26" valign="top" style="padding:4px 0;">📬</td>
          <td style="padding:4px 0;">You get an <b>email when it&rsquo;s fixed</b>. No chasing anyone.</td></tr>
      <tr><td width="26" valign="top" style="padding:4px 0;">👀</td>
          <td style="padding:4px 0;">You can see all your reports any time under <b>Bug Reports</b> in Omni.</td></tr>
    </table>
    <p style="margin:16px 0 0;">
      Can&rsquo;t log in at all? Use the same page &mdash; there&rsquo;s a
      <b>Report a Login Issue</b> card next to the bug one.
    </p>
    <p style="margin:14px 0 0;">O dire sentle. 🙌</p>
  </td></tr>

  <!-- footer -->
  <tr><td style="padding:22px 30px 26px;">
    <div style="border-top:1px solid #E5E7EB;padding-top:14px;color:#6B7280;font-size:11.5px;line-height:1.55;">
      This is an automatic reply from Omni, sent once every
      {getattr(settings, 'AUTO_REPLY_COOLOFF_DAYS', 14)} days so it never becomes noise.
      <b>Your email has still been delivered</b> &mdash; no need to send it again.
      Internal staff only.<br>
      <a href="{bug}" style="color:#C97F0C;">{bug}</a>
    </div>
  </td></tr>

</table>
</td></tr></table>
</body></html>"""


def build_text(first_name: str, base_url: str) -> str:
    return (
        f"Hi {first_name},\n\n"
        "Thanks for the email — it has been delivered and a human will read it.\n\n"
        "But there is a faster door. Reporting it inside Omni puts it straight on the "
        "fix-it list, gives you a reference number, and emails you when it is fixed.\n\n"
        f"Report a bug:            {base_url}/report-bug\n"
        f"Ask for a new feature:   {base_url}/report-bug?intent=feature\n\n"
        "A bug needs 50 words and 2 screenshots. A feature idea needs 25 words and no "
        "screenshots. Can't log in? Use the Report a Login Issue card on the same page.\n\n"
        "This is an automatic reply — you will not get it again for a while."
    )


# ---------------------------------------------------------------------------
def send_nudge(to_email: str, display_name: str, subject: str) -> bool:
    """Send the auto-reply. Returns True when the mail backend accepted it."""
    base_url = getattr(settings, 'PUBLIC_BASE_URL', 'https://omni.alphadirect.co.bw').rstrip('/')
    first = _first_name(display_name, to_email)
    clean = (subject or '').strip() or 'your email'
    if len(clean) > 90:
        clean = clean[:87] + '…'

    msg = EmailMultiAlternatives(
        subject=f'Omni has a faster door 🚀 — re: {clean}',
        body=build_text(first, base_url),
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'omni@alphadirect.co.bw'),
        to=[to_email],
        # Deliberately no CC: one copy of every nudge back into excoboard@ would
        # defeat the whole point of the exercise.
        reply_to=['omni@alphadirect.co.bw'],
        # Graph only forwards x-* custom headers; this one stops the recipient's
        # own out-of-office from answering a robot.
        headers={'X-Auto-Response-Suppress': 'All'},
    )
    msg.attach_alternative(build_html(first, base_url), 'text/html')
    return bool(msg.send(fail_silently=False))


class Command(BaseCommand):
    help = ('Auto-reply to internal staff who email excoboard@ / omni@ about bugs or '
            'features, pointing them at the in-app channels.')

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Show who would be replied to; send nothing.')
        parser.add_argument('--limit', type=int, default=0,
                            help='Send at most N replies this run (0 = no cap).')
        parser.add_argument('--minutes', type=int, default=0,
                            help='Look back this many minutes (default: AUTO_REPLY_LOOKBACK_MINUTES).')
        parser.add_argument('--to', type=str, default='',
                            help='Force one test send to this address, skipping the mailbox scan.')

    def handle(self, *args, **opts):
        if opts['to']:
            ok = send_nudge(opts['to'], '', 'test of the Omni auto-reply')
            self.stdout.write(self.style.SUCCESS(f'test send to {opts["to"]}: {ok}'))
            return

        if not getattr(settings, 'AUTO_REPLY_ENABLED', False):
            self.stdout.write('AUTO_REPLY_ENABLED is off — nothing done.')
            return

        minutes  = opts['minutes'] or getattr(settings, 'AUTO_REPLY_LOOKBACK_MINUTES', 120)
        cooloff  = getattr(settings, 'AUTO_REPLY_COOLOFF_DAYS', 14)
        dry      = opts['dry_run']
        limit    = opts['limit'] or 0

        messages = _fetch_recent(minutes)
        self.stdout.write(f'read {len(messages)} message(s) from the last {minutes} min')

        # Newest first from Graph; reverse so the subject we quote is the oldest
        # unanswered one, and de-dupe per sender inside this run.
        seen_this_run: set[str] = set()
        candidates: List[Dict[str, Any]] = []
        for m in reversed(messages):
            frm = _sender(m)
            if not frm or frm in seen_this_run:
                continue
            if not _watched(m):
                continue
            if not _is_internal(frm) or _is_system(frm):
                continue
            if _skip_subject(m.get('subject', '')):
                continue
            seen_this_run.add(frm)
            candidates.append(m)

        self.stdout.write(f'{len(candidates)} internal sender(s) wrote to a watched address')

        cutoff = timezone.now() - timedelta(days=cooloff)
        sent = skipped = failed = 0
        for m in candidates:
            frm = _sender(m)
            row = MailAutoReply.objects.filter(sender_email=frm).first()
            if row and row.last_sent_at > cutoff:
                skipped += 1
                self.stdout.write(f'  – {frm}: nudged {row.last_sent_at:%Y-%m-%d}, cool-off')
                continue
            if dry:
                sent += 1
                self.stdout.write(f'  → WOULD nudge {frm} (re: {m.get("subject","")[:50]})')
                continue
            if limit and sent >= limit:
                self.stdout.write(f'  · limit {limit} reached — stopping')
                break
            try:
                ok = send_nudge(frm, _sender_name(m), m.get('subject', ''))
            except Exception as exc:                      # noqa: BLE001 — never crash the cron
                ok = False
                log.warning('auto-reply to %s failed: %s', frm, exc)
                self.stdout.write(self.style.WARNING(f'  ! {frm}: {exc}'))
            if not ok:
                failed += 1
                continue
            now = timezone.now()
            if row:
                row.last_sent_at = now
                row.times_sent += 1
                row.last_subject = (m.get('subject') or '')[:300]
                row.last_message_id = (m.get('internetMessageId') or '')[:300]
                row.save(update_fields=['last_sent_at', 'times_sent',
                                        'last_subject', 'last_message_id'])
            else:
                MailAutoReply.objects.create(
                    sender_email=frm,
                    last_sent_at=now,
                    last_subject=(m.get('subject') or '')[:300],
                    last_message_id=(m.get('internetMessageId') or '')[:300],
                )
            sent += 1
            self.stdout.write(self.style.SUCCESS(f'  ✓ nudged {frm}'))

        verb = 'would send' if dry else 'sent'
        self.stdout.write(self.style.SUCCESS(
            f'{verb}={sent} cool-off-skipped={skipped} failed={failed}'))
