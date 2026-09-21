"""
bonu/mailbox.py — firms email the bill; it is read and waiting.

The best upload screen is the one nobody has to open. Panel firms already email their
invoices, so this pulls the attachments straight out of a mailbox, runs them through the
same reader as an upload, and leaves each one in the waiting room for the accountant to
confirm. She never downloads, saves, and re-uploads a file again.

TWO HONEST LIMITS, stated here so nobody discovers them later:

  1. **This needs permission to READ a mailbox.** The mail app registration we use today
     is a SENDER (Mail.Send). Reading needs `Mail.Read` granted to the same registration.
     `fetch()` reports that plainly instead of failing in a way that looks like "no mail".
  2. **It saves nothing automatically.** Each attachment becomes a document in the waiting
     room with the same warnings an upload gets. A firm cannot create a payable by sending
     an email.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request

GRAPH = 'https://graph.microsoft.com/v1.0'
ALLOWED_EXT = ('.xlsx', '.xlsm', '.csv', '.pdf', '.png', '.jpg', '.jpeg', '.tif', '.tiff')
MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024


class MailboxUnavailable(RuntimeError):
    """Raised with a plain-English reason a person can act on."""


def _cfg():
    """Credentials from the environment only — never from the database, never from code."""
    missing = [k for k in ('GRAPH_TENANT_ID', 'GRAPH_CLIENT_ID', 'GRAPH_CLIENT_SECRET')
               if not os.environ.get(k)]
    if missing:
        raise MailboxUnavailable(
            'The mail connection is not configured on this server, so invoices cannot be '
            'collected by email yet. Uploading still works.')
    return (os.environ['GRAPH_TENANT_ID'], os.environ['GRAPH_CLIENT_ID'],
            os.environ['GRAPH_CLIENT_SECRET'])


def _token():
    tenant, client, secret = _cfg()
    data = urllib.parse.urlencode({
        'grant_type': 'client_credentials', 'client_id': client, 'client_secret': secret,
        'scope': 'https://graph.microsoft.com/.default'}).encode()
    req = urllib.request.Request(
        f'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token', data=data, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())['access_token']
    except urllib.error.HTTPError as e:
        raise MailboxUnavailable(f'The mail service refused our login (code {e.code}).') from None
    except OSError:
        raise MailboxUnavailable('Could not reach the mail service from this server.') from None


def _get(path, token):
    req = urllib.request.Request(f'{GRAPH}{path}', headers={'Authorization': f'Bearer {token}'})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise MailboxUnavailable(
                'We can send email but we are not allowed to READ this mailbox yet. Someone with '
                'Microsoft admin rights must grant read access to the same mail connection '
                '(Mail.Read). Until then, invoices have to be uploaded.') from None
        raise MailboxUnavailable(f'The mailbox could not be read (code {e.code}).') from None
    except OSError:
        raise MailboxUnavailable('Could not reach the mail service from this server.') from None


#: Public names for the Graph plumbing above. `healthcare/afa_mailbox.py` reads
#: a different mailbox for a different reason, but the login and the
#: "we can send but we cannot READ" message must exist in exactly ONE place —
#: two copies of that sentence is two places to fix when the wording is wrong.
graph_token = _token
graph_get = _get


def fetch(mailbox: str, limit: int = 25, folder: str = 'Inbox', unread_only: bool = True):
    """Read invoice attachments out of a mailbox into the waiting room.

    Returns a summary dict. Never raises on a single bad attachment — one unreadable file
    must not stop the other nine.
    """
    token = _token()
    q = ['$top=%d' % max(1, min(limit, 50)), '$select=id,subject,from,hasAttachments,receivedDateTime']
    if unread_only:
        q.append('$filter=isRead eq false and hasAttachments eq true')
    else:
        q.append('$filter=hasAttachments eq true')
    listing = _get(f'/users/{urllib.parse.quote(mailbox)}/mailFolders/'
                   f'{urllib.parse.quote(folder)}/messages?' + '&'.join(q), token)

    made, skipped, errors = [], 0, []
    for msg in (listing.get('value') or []):
        sender = (((msg.get('from') or {}).get('emailAddress') or {}).get('address') or '')
        atts = _get(f'/users/{urllib.parse.quote(mailbox)}/messages/{msg["id"]}/attachments', token)
        for att in (atts.get('value') or []):
            name = att.get('name') or ''
            if not name.lower().endswith(ALLOWED_EXT):
                skipped += 1
                continue
            if (att.get('size') or 0) > MAX_ATTACHMENT_BYTES:
                errors.append(f'{name}: too big to read ({att.get("size")} bytes)')
                continue
            raw = att.get('contentBytes')
            if not raw:
                skipped += 1
                continue
            try:
                blob = base64.b64decode(raw)
                doc = ingest_blob(name, blob, arrived_by='email', from_address=sender)
                made.append({'document_id': str(doc.pk), 'filename': name, 'from': sender,
                             'status': doc.status})
            except Exception as exc:                      # noqa: BLE001 — one bad file, not nine
                errors.append(f'{name}: {type(exc).__name__}')

    return {
        'mailbox': mailbox, 'messages_looked_at': len(listing.get('value') or []),
        'documents_created': len(made), 'attachments_ignored': skipped,
        'errors': errors, 'documents': made,
        'note': 'Nothing was paid or posted. Each bill is waiting for the accountant to confirm.',
    }


def ingest_blob(filename: str, blob: bytes, arrived_by='upload', from_address='', firm=None):
    """One file → one document in the waiting room, with its warnings already worked out.

    Shared by the upload screen and the mailbox so both behave identically — the accountant
    should not be able to tell how a bill arrived.
    """
    from bonu.confirm import warnings_for_draft
    from bonu.ingest import parse_invoice
    from bonu.models import IngestedDocument

    draft = parse_invoice(filename, blob)

    if draft.get('needs_vision_model'):
        status = IngestedDocument.Status.NEEDS_OCR
    elif draft.get('extraction_error') or not draft.get('lines'):
        status = (IngestedDocument.Status.FAILED if draft.get('extraction_error')
                  else IngestedDocument.Status.PARSED)
    else:
        status = IngestedDocument.Status.PARSED

    guessed_firm = firm or _guess_firm(from_address, draft.get('text_preview') or '')
    warns = warnings_for_draft(draft, firm=guessed_firm)

    return IngestedDocument.objects.create(
        filename=filename[:255], arrived_by=arrived_by, from_address=from_address[:200],
        firm=guessed_firm,
        extraction_method=(draft.get('extraction_method') or '')[:60],
        extraction_error=(draft.get('extraction_error') or ''),
        draft=draft, text_preview=(draft.get('text_preview') or ''),
        status=status, warnings=warns)


def _guess_firm(from_address: str, text: str):
    """Best-effort match to a panel firm, by email domain then by name in the document.

    A guess only — the confirm screen always shows which firm was picked and lets the
    accountant change it, because attaching a bill to the wrong firm corrupts every
    figure downstream.
    """
    from bonu.models import LawFirm
    domain = (from_address.split('@')[-1] or '').lower().strip()
    if domain:
        for f in LawFirm.objects.filter(is_active=True).exclude(contact_email=''):
            if domain and domain in (f.contact_email or '').lower():
                return f
    low = (text or '').lower()
    if low:
        for f in LawFirm.objects.filter(is_active=True):
            for candidate in (f.name, f.trading_name):
                if candidate and len(candidate) > 4 and candidate.lower() in low:
                    return f
    return None
