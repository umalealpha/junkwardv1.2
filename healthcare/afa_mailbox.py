"""
healthcare/afa_mailbox.py — read AFA's weekly settlement email out of a mailbox.

WHY THIS EXISTS (CFO decision 2026-09-13)
The loader was first built to read a zip plus an AFA-written manifest off an
SFTP drop. That shape does not exist: AFA have never been asked for it. What
actually happens, confirmed on the live mailbox, is that FOUR emails arrive from
demi@afa.co.bw every Friday night around 23:20 UTC (Saturday 01:20 Gaborone),
and exactly ONE of them carries the settlement Alpha Direct raises. The CFO
ruled that Omni reads the mailbox rather than asking AFA to change anything.

🔴 THE ACCESS PROBLEM, STATED UP FRONT
Omni's Microsoft registration is **Mail.Send only** — it can send email and it
cannot read any mailbox (`bonu/mailbox.py` says the same thing about the same
registration). Reading requires an administrator to grant, in Microsoft Entra:

    Microsoft Graph → APPLICATION permission "Mail.Read" → admin consent,
    then scoped to this ONE mailbox with an application access policy
    (Exchange Online: New-ApplicationAccessPolicy ... -AccessRight
    RestrictAccess).

Neither the CFO nor this code can grant it. Until IT do, every run FAILS LOUDLY
— a FAILED run record and an email to the owner naming the permission — and
never a quiet "no file this week", which is the one outcome that would let a
whole settlement go unraised without anybody noticing.

WHAT IT READS AND WHAT IT NEVER DOES
It lists messages and downloads ONE attachment: the one the four-way selector
picked. It marks nothing read, moves nothing, deletes nothing, replies to
nothing. The mailbox is a named setting (ADH_SETTLEMENT_MAILBOX), not an
address baked into code.

DATA PROTECTION
Subjects and attachment names are file names, not people. The attachment itself
is medical, identifiable claims data: it is held in memory, never written to
disk here, never logged, and never sent to any external model.
"""
from __future__ import annotations

import base64
import logging
import re
import urllib.parse
from datetime import timedelta, timezone as dt_timezone

from django.conf import settings
from django.utils import timezone

log = logging.getLogger('adh-settlement')

#: The exact thing IT must grant, in one sentence that can be forwarded as-is.
MAIL_READ_PERMISSION = 'Mail.Read'
PERMISSION_SENTENCE = (
    'Omni has not been granted permission to read {mailbox}; please ask IT to '
    'grant the Microsoft Graph APPLICATION permission "Mail.Read" (with admin '
    'consent) to Omni\'s mail app registration, scoped to {mailbox} only using '
    'an Entra/Exchange application access policy.'
)

#: The attachment has to be something we can actually open. AFA send a .zip
#: even when the subject names a .xls, so both are accepted — and this is also
#: what stops a signature logo on the RIGHT email from becoming a second
#: candidate and making the run ambiguous.
LISTING_EXT = ('.zip', '.xls', '.xlsx', '.xlsm')

#: A settlement listing that is far too big, or empty, is refused in
#: `claims_settlement.load_body`, NOT here. `MailboxNotReadable` carries a fixed
#: remedy ("ask IT for Mail.Read"), and a file that is merely the wrong size is
#: not a permission problem: it would send Keetile to IT for a grant that would
#: not help, under a subject line saying the mailbox could not be read, and with
#: none of the structured "what else arrived" list the settlement screen renders,
#: because that class cannot carry one. ONE EXCEPTION CLASS PER REMEDY.
#: (Fable, 14-Sep-2026.)

MESSAGE_LIMIT = 50

#: THE WINDOW MUST BE SHORTER THAN THE FEED'S WEEKLY PERIOD, and this constant
#: is the number that decides it when the setting is absent — a slim test
#: settings module, a future split — so it may not disagree with settings.py.
#: The two disagreeing is how the every-week ambiguity would come back by the
#: back door, and a test that reads the SETTING would never see it.
#:
#: Five days: the job fires 00:00 Saturday UTC and AFA send ~23:20 the Friday
#: night before, so this reaches back to Monday 00:00 — clear of last Friday by
#: two whole days even if AFA run early or late — while still letting a manual
#: catch-up work any time up to Wednesday. Two days did not: from Sunday night
#: onwards the file was invisible, and the run would have told Keetile AFA sent
#: nothing when they had. (Fable, 14-Sep-2026.)
DEFAULT_LOOKBACK_DAYS = 5


class MailboxNotReadable(RuntimeError):
    """Omni cannot read the mailbox — usually the missing Mail.Read grant.

    Its own class so the run that fails can say WHY in the subject line of the
    email it sends, instead of looking like an ordinary empty Saturday.
    """


def mailbox_name() -> str:
    """The mailbox to read. A named setting, never a hardcoded address."""
    return (getattr(settings, 'ADH_SETTLEMENT_MAILBOX', '') or '').strip()


def folder_name() -> str:
    return (getattr(settings, 'ADH_SETTLEMENT_MAIL_FOLDER', '') or 'Inbox').strip()


def lookback_days() -> int:
    return int(getattr(settings, 'ADH_SETTLEMENT_LOOKBACK_DAYS',
                       DEFAULT_LOOKBACK_DAYS) or DEFAULT_LOOKBACK_DAYS)


def _graph():
    """(token, get) from the one Graph client this codebase has.

    Every failure — not configured, refused login, and above all the 401/403
    that means "we can send but not read" — arrives here as MailboxUnavailable
    and is re-raised as MailboxNotReadable with the permission sentence
    attached, so the person reading the email is told what to ask for.
    """
    from bonu.mailbox import MailboxUnavailable, graph_get, graph_token
    return MailboxUnavailable, graph_token, graph_get


#: Words that belong to bonu's screen and not to this one. The Graph client is
#: shared with bonu's invoice waiting-room, so its "we cannot read the mailbox"
#: text ends "Until then, invoices have to be uploaded." Concatenated into
#: Keetile's Saturday email that is not clumsy wording, it is a WRONG
#: INSTRUCTION: "upload" is a real button in bonu and nothing at all in ADH, and
#: this is the email she receives every week for as long as the Mail.Read grant
#: is outstanding. bonu's own wording is left alone — its screen depends on it.
_BONU_ONLY_WORDS = ('invoice', 'upload')


def _not_readable(exc, mailbox: str) -> 'MailboxNotReadable':
    """The one place a MailboxUnavailable becomes our own exception.

    Keeps the sentences that describe WHAT WENT WRONG, drops the ones that tell
    the reader what to do about it in a different module's product, and appends
    the remedy that is actually ours.
    """
    kept = [s for s in re.split(r'(?<=[.!?])\s+', str(exc) or '')
            if s.strip() and not any(w in s.lower() for w in _BONU_ONLY_WORDS)]
    return MailboxNotReadable(
        ' '.join(kept + [PERMISSION_SENTENCE.format(mailbox=mailbox)]).strip())


def _since_iso() -> str:
    """Botswana time in, UTC out — Graph filters on UTC and the box is UTC."""
    cutoff = timezone.now() - timedelta(days=lookback_days())
    return cutoff.astimezone(dt_timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def list_candidates() -> list[dict]:
    """Every recent message in the mailbox, with its attachments' METADATA.

    Returns [{message_id, subject, sender, received, attachments:
    [{id, name, size}]}]. Attachment BYTES are deliberately not downloaded
    here: the selector decides on the subject and the attachment name, and only
    the ONE file that wins is ever pulled down.

    Messages with no attachment are returned too, not filtered out. The fourth
    email AFA send every Saturday has no attachment at all, and a run that
    cannot name it and say why it was passed over is a run nobody can check.
    """
    mailbox = mailbox_name()
    if not mailbox:
        raise MailboxNotReadable(
            'No mailbox is configured for the ADH settlement loader '
            '(ADH_SETTLEMENT_MAILBOX is empty). Nothing was read.')

    MailboxUnavailable, graph_token, graph_get = _graph()
    try:
        token = graph_token()
        # URL-ENCODED. `$filter=receivedDateTime ge ...` and `$orderby=...
        # desc` both carry SPACES, and this string is handed straight to
        # urllib as a request target — a raw space there is not a valid URL and
        # Graph answers 400, which arrives as "the mailbox could not be read"
        # and looks exactly like the missing permission. Commas are left alone
        # because $select reads better that way and Graph accepts them.
        q = urllib.parse.urlencode(
            {
                '$top': MESSAGE_LIMIT,
                '$select': 'id,subject,from,hasAttachments,receivedDateTime',
                '$filter': 'receivedDateTime ge %s' % _since_iso(),
                '$orderby': 'receivedDateTime desc',
            },
            quote_via=urllib.parse.quote, safe=',')
        listing = graph_get(
            '/users/%s/mailFolders/%s/messages?%s' % (
                urllib.parse.quote(mailbox), urllib.parse.quote(folder_name()),
                q),
            token)
    except MailboxUnavailable as exc:
        raise _not_readable(exc, mailbox) from None

    out: list[dict] = []
    for msg in (listing.get('value') or []):
        row = {
            'message_id': msg.get('id') or '',
            'subject': msg.get('subject') or '',
            'sender': (((msg.get('from') or {}).get('emailAddress') or {})
                       .get('address') or ''),
            'received': msg.get('receivedDateTime') or '',
            'attachments': [],
        }
        if msg.get('hasAttachments'):
            try:
                atts = graph_get(
                    '/users/%s/messages/%s/attachments?$select=id,name,size,isInline'
                    % (urllib.parse.quote(mailbox),
                       urllib.parse.quote(row['message_id'])),
                    token)
            except MailboxUnavailable as exc:
                raise _not_readable(exc, mailbox) from None
            for att in (atts.get('value') or []):
                # An inline signature image is not a delivery. Skipping it here
                # is what keeps the RIGHT email a single candidate.
                if att.get('isInline'):
                    continue
                row['attachments'].append({
                    'id': att.get('id') or '',
                    'name': att.get('name') or '',
                    'size': att.get('size') or 0,
                })
        out.append(row)
    return out


def read_attachment(message_id: str, attachment_id: str) -> bytes:
    """The bytes of ONE attachment. Read-only; the message is left untouched."""
    mailbox = mailbox_name()
    MailboxUnavailable, graph_token, graph_get = _graph()
    try:
        token = graph_token()
        att = graph_get(
            '/users/%s/messages/%s/attachments/%s' % (
                urllib.parse.quote(mailbox), urllib.parse.quote(message_id),
                urllib.parse.quote(attachment_id)),
            token)
    except MailboxUnavailable as exc:
        raise _not_readable(exc, mailbox) from None

    # Size and emptiness are judged by `claims_settlement.load_body`, not here.
    # This function's ONE job is "we were allowed to read it, here are the
    # bytes" — see the note above LISTING_EXT for why.
    return base64.b64decode(att.get('contentBytes') or '')
