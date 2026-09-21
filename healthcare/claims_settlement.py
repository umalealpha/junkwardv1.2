"""
healthcare/claims_settlement.py - B4, the ADH health claims EFT settlement loader.

Keetile Mokhendo, Senior Debtors Accountant: every Saturday between about 01:00
and 01:20 Gaborone time, FOUR near-identical emails arrive from AFA. Exactly ONE
of them carries the file Alpha Direct settles. Today Keetile opens it and keys
every claim line into Omni by hand.

🔴 THE BOUNDARY, AND IT IS THE WHOLE POINT OF THIS MODULE
The flow creates PAYMENT REQUESTS only. A payment request is a workflow record:
it asks two named people to look at a settlement and sign it. Finance sign-off
in Omni and CFO authorisation in FNB stay manual, and the money itself only ever
leaves at FNB under a human's two-factor. Nothing here settles, pays, releases
or transfers anything, and no change to this file may make it do so.

A MAILBOX, NOT A FILE DROP - CFO decision 2026-09-13
This was first built to read a zip plus an AFA-written `<name>.manifest.json`
off the SFTP link. That shape does not exist and never did: AFA have never been
asked to write a manifest, and asking them to change their Friday night job is
a change we do not control. The CFO ruled that Omni reads the mailbox instead.
So the source is now `healthcare/afa_mailbox.py`, and the conditions are tested
against the EMAIL SUBJECT and the ATTACHMENT NAME - the text AFA actually send.
The SFTP link is left alone: it is the OUTBOUND leg that sends AFA the member
loadfile (`healthcare/afa_sftp.py`), a different direction and a different job,
and this module no longer touches it at all.

🔴 IT CANNOT RUN UNTIL IT MAY READ THE MAILBOX. Omni's Microsoft
registration is Mail.Send only. `healthcare/afa_mailbox.py` carries the exact
permission an administrator has to grant, and a run that lacks it FAILS LOUDLY -
never a silent "no file this week".

THE FOUR-FILE FILTER, AGAINST THE REAL 12 SEPTEMBER SUBJECTS
    EFT FILE: ADI_AFT_20260912.xls *** MANUAL SUBMISSION REQUIRED ***   <- ours
    EFT FILE: ADI_AFT_20260912_RSA.xls *** MANUAL SUBMISSION REQUIRED ***
    ADI EFT Payments - Payment Run Summary
    Payment run messages for ALPHA DIRECT INSURANCE on 12 September 2026

The conditions are joined by AND, and each one rejects exactly one of the things
that actually lands:

    it has an attachment we can open    (the fourth email has none)
    AND sender is demi@afa.co.bw
    AND it contains "EFT FILE: ADI_AFT"     (the third email does not)
    AND it contains "MANUAL SUBMISSION REQUIRED"
    AND it does NOT contain "_RSA"          (the second email does)

Note the subject names a `.xls` while the attachment is a `.zip` - the subject's
extension is never taken for the attachment's. `_RSA` is tested against the
subject AND the attachment name, because either one alone identifies the South
African twin and a truncated subject must not let it through.

REPLAY SAFETY IS KEYED ON THE ATTACHMENT BYTES
`AdhSettlementRun.file_sha256` (unique) and `AdhSettlementLine.dedupe_key`
(unique) are both computed from CONTENT - the attachment bytes and the claim
line itself. Never the message id and never the subject: a forwarded or re-sent
copy of the same file is a new message with a new id and often a "FW:" subject,
and keying on either of those would raise the whole settlement a second time.

DATA PROTECTION
The listing is medical, identifiable claims data. Claimant names and claim
detail are never logged, never put in an email subject, and never sent to any
external model. Counts and amounts are what the logs and the run screen carry.
"""
from __future__ import annotations

import hashlib
import io
import logging
import re
import zipfile
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Iterable, Optional

log = logging.getLogger('adh-settlement')

# ── The filter, in the spec's own words ─────────────────────────────────────
AFA_SENDER = 'demi@afa.co.bw'
TOKEN_EFT_FILE = 'EFT FILE: ADI_AFT'
TOKEN_MANUAL = 'MANUAL SUBMISSION REQUIRED'
TOKEN_RSA = '_RSA'

#: Who raises these requests, and who is told when a Saturday goes wrong.
#: Keetile asked for the automation and keys these settlements today.
ENTERED_BY_EMAIL = 'kmokhendo@alphadirect.co.bw'

#: An attachment we will not open. AFA's weekly settlement is a few hundred
#: kilobytes; anything of this order is a fault or an attack, not a listing.
MAX_ZIP_BYTES = 64 * 1024 * 1024
MAX_UNPACKED_BYTES = 256 * 1024 * 1024

_SHEET_EXT = ('.xls', '.xlsx', '.xlsm', '.ods')

_TOTALS_RE = re.compile(r'^\s*(grand\s+)?totals?\b', re.I)


class SettlementFileError(RuntimeError):
    """The weekly file is missing, ambiguous, or cannot be read.

    Carries `rejected` — the [{name, reason}] list for the files that WERE on
    the drop. The message reads them out as prose, but the run row stores the
    structured list, and the screen renders that. Without it, the one Saturday
    Keetile most needs the reasons is the Saturday the screen says no other
    files were recorded.
    """

    def __init__(self, *args, rejected: list | None = None):
        super().__init__(*args)
        self.rejected = rejected or []


class AmbiguousSettlementFile(SettlementFileError):
    """More than one dropped file passed all four conditions."""


# ---------------------------------------------------------------------------
# 1. Choosing the ONE file out of the four
# ---------------------------------------------------------------------------

@dataclass
class DroppedFile:
    """One candidate: ONE attachment on ONE email AFA sent.

    An email with no attachment is still a candidate - it is rejected with its
    own recorded reason rather than filtered away, because three of the four
    things that land every Saturday have to be named and explained, not
    silently dropped. An email with two attachments becomes two candidates.
    """
    name: str = ''                 # the ATTACHMENT's name, not the subject's
    sender: str = ''
    subject: str = ''
    body: bytes = b''
    has_attachment: bool = True
    message_id: str = ''
    attachment_id: str = ''

    @property
    def label(self) -> str:
        """What the run record calls this candidate: the attachment name when
        there is one, otherwise the subject - never a message id, which means
        nothing to the person reading the screen."""
        if self.name:
            return self.name
        return f'(no attachment) {self.subject}'[:255]

    @property
    def matchable(self) -> str:
        """The text the conditions are tested against - the subject AFA wrote
        plus the attachment name. Both, because the tokens live in the subject
        while `_RSA` appears in each of them on its own."""
        return f'{self.subject} {self.name}'


def sender_address(raw: str) -> str:
    """The bare e-mail address out of a `from`, lowercased.

    A `from` arrives in header form — `Demi <demi@afa.co.bw>` — as readily as it
    arrives bare. Comparing the WHOLE header string to `demi@afa.co.bw` rejected
    every such candidate for the wrong sender, which is the one rejection reason
    that looks like a security event rather than a formatting difference.
    `email.utils.parseaddr` is the standard library's own header parser: use it
    rather than a hand-rolled split on `<`. (Fable, 13-Sep-2026.)

    Graph hands back a bare address today, so on the mailbox path this is a
    no-op — kept because a display name costs nothing to parse and the one
    Saturday it appears is the Saturday nothing loads at all.
    """
    from email.utils import parseaddr
    return parseaddr((raw or '').strip())[1].strip().lower()


def rejection_reason(f: DroppedFile) -> str:
    """Why this candidate is NOT the one to process, or '' if it is.

    The conditions are tested SEPARATELY and in order, and each returns its own
    sentence naming the condition that failed. That is deliberate: three of the
    four emails that land every Saturday fail on exactly one condition each, and
    a run that cannot say WHICH one is a run nobody can check.
    """
    from healthcare.afa_mailbox import LISTING_EXT

    if not f.has_attachment or not f.name:
        return 'the email has no attachment - nothing to open'
    if not f.name.lower().endswith(LISTING_EXT):
        return (f'the attachment "{f.name}" is not a zip or a spreadsheet, so it '
                f'is not a claims listing')
    if sender_address(f.sender) != AFA_SENDER:
        return f'sender is "{f.sender or "(none)"}", not {AFA_SENDER}'
    hay = f.matchable.upper()
    if TOKEN_EFT_FILE.upper() not in hay:
        return f'does not contain "{TOKEN_EFT_FILE}"'
    if TOKEN_MANUAL.upper() not in hay:
        return f'does not contain "{TOKEN_MANUAL}"'
    if TOKEN_RSA.upper() in hay:
        return f'contains "{TOKEN_RSA}" - that is the South African file'
    return ''


def select_settlement_file(files: Iterable[DroppedFile]
                           ) -> tuple[Optional[DroppedFile], list[dict]]:
    """(the one candidate to process, [{name, reason} for every one rejected]).

    Raises AmbiguousSettlementFile if two candidates pass every condition.
    Guessing between two settlement listings is not a thing this may do.
    """
    passed: list[DroppedFile] = []
    rejected: list[dict] = []
    for f in files:
        reason = rejection_reason(f)
        if reason:
            rejected.append({'name': f.label, 'reason': reason})
        else:
            passed.append(f)
    if len(passed) > 1:
        raise AmbiguousSettlementFile(
            'Two or more emails match every condition ('
            + ', '.join(p.label for p in passed)
            + '). Nothing was loaded - a person picks.',
            rejected=rejected)
    return (passed[0] if passed else None), rejected


# ---------------------------------------------------------------------------
# 2. Reading the mailbox
# ---------------------------------------------------------------------------

def fetch_candidates() -> list[DroppedFile]:
    """Every recent AFA email in the settlement mailbox, one candidate per
    attachment. Reads only - nothing is marked read, moved or deleted.

    Attachment bytes are NOT downloaded here. The conditions are decided on the
    subject and the attachment name, so only the one that wins is ever pulled.
    """
    from healthcare import afa_mailbox

    out: list[DroppedFile] = []
    for msg in afa_mailbox.list_candidates():
        if not msg['attachments']:
            out.append(DroppedFile(sender=msg['sender'], subject=msg['subject'],
                                   has_attachment=False,
                                   message_id=msg['message_id']))
            continue
        for att in msg['attachments']:
            out.append(DroppedFile(
                name=att['name'], sender=msg['sender'], subject=msg['subject'],
                message_id=msg['message_id'], attachment_id=att['id']))
    return out


def load_body(chosen: DroppedFile,
              rejected: list[dict] | None = None) -> DroppedFile:
    """Pull the chosen attachment's bytes down. In memory; never to disk.

    BOTH size refusals live here, not in the mailbox reader. A file that is
    empty or absurdly large is a FILE problem, not a permission problem: raised
    from the reader it would wear `MailboxNotReadable`, whose whole message is
    "ask IT to grant Mail.Read", and Keetile would be sent to IT for a grant
    that would not help. It would also arrive with no `rejected` list, because
    that class cannot carry one — and the settlement screen, reading an empty
    list, would say no other emails were recorded on exactly the Saturday
    something went wrong. `rejected` is carried onto both refusals for the same
    reason every other raise site carries it. (Fable, 14-Sep-2026.)
    """
    from healthcare import afa_mailbox

    chosen.body = afa_mailbox.read_attachment(chosen.message_id,
                                              chosen.attachment_id)
    if not chosen.body:
        raise SettlementFileError(
            f'{chosen.name} came back empty from the mailbox, so there was '
            'nothing to open.',
            rejected=rejected)
    if len(chosen.body) > MAX_ZIP_BYTES:
        raise SettlementFileError(
            f'{chosen.name} is {len(chosen.body)} bytes - far larger than a weekly '
            'settlement listing. Not opened.',
            rejected=rejected)
    return chosen


def fetch_file() -> tuple[DroppedFile, list[dict]]:
    """The ONE settlement attachment for this Saturday, with its bytes loaded,
    plus what was rejected and why. Raises SettlementFileError if none matched."""
    candidates = fetch_candidates()
    chosen, rejected = select_settlement_file(candidates)
    if chosen is None:
        raise SettlementFileError(
            'No email in the settlement mailbox matched every condition. '
            f'{len(candidates)} candidate(s) were looked at: '
            + ('; '.join(f'{r["name"]} - {r["reason"]}' for r in rejected)
               or 'none at all'),
            rejected=rejected)
    return load_body(chosen, rejected), rejected


# ---------------------------------------------------------------------------
# 3. Unzip + read the .xls
# ---------------------------------------------------------------------------

def unzip_listing(blob: bytes, attachment_name: str = '') -> tuple[str, bytes]:
    """(member name, bytes) of the single spreadsheet in the attachment.

    AFA attach a `.zip` even though the subject names a `.xls`, so the zip is
    the normal case. A bare spreadsheet is accepted too and handed straight
    back: the selector allows a spreadsheet attachment, and accepting a shape
    there that this refuses would be a filter promising something the reader
    cannot keep.
    """
    low = attachment_name.lower()
    if low.endswith(_SHEET_EXT) and not low.endswith('.zip'):
        # A spreadsheet IS the listing. Decided on the attachment's own name,
        # never on its first two bytes: .xlsx is itself a PK archive, so a
        # sniff would send it down the unzip path and then refuse it for
        # holding no spreadsheet.
        return attachment_name, blob
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as exc:
        raise SettlementFileError('The settlement attachment is not a readable '
                                  'zip file.') from exc
    with zf:
        members = [i for i in zf.infolist()
                   if not i.is_dir()
                   and i.filename.lower().endswith(_SHEET_EXT)
                   # A member name that climbs out of the archive is never a
                   # claims listing. We extract in memory, so this cannot write
                   # anywhere — but it is still not a file we read.
                   and '..' not in i.filename and not i.filename.startswith('/')]
        if not members:
            raise SettlementFileError(
                'The settlement zip has no spreadsheet in it.')
        if len(members) > 1:
            raise SettlementFileError(
                f'The settlement zip holds {len(members)} spreadsheets. Expected '
                'one; nothing was read.')
        info = members[0]
        if info.file_size > MAX_UNPACKED_BYTES:
            raise SettlementFileError(
                f'{info.filename} unpacks to {info.file_size} bytes — too large to '
                'be a weekly claims listing. Not read.')
        return info.filename, zf.read(info)


#: Column names we accept, matched on a squashed, case-folded form so
#: "Claim Number", "CLAIMNO" and "claim_number" are one column. Taken from the
#: shapes AFA's other files use; a real weekly file may carry a name not listed
#: here, and the loader says so by name rather than loading a half-read row.
_COLUMNS = {
    'claim_number': ('claimnumber', 'claimno', 'claim', 'claimref',
                     'claimreference'),
    'payee': ('payeename', 'payee', 'providername', 'provider', 'practicename',
              'practice', 'beneficiary', 'accountholder', 'supplier'),
    'amount': ('amount', 'amountpaid', 'paid', 'nettpayable', 'netpayable',
               'settlementamount', 'eftamount', 'totalamount'),
    'account_number': ('accountnumber', 'account', 'accountno', 'bankaccount',
                       'accno'),
    'bank_name': ('bank', 'bankname'),
    'branch_code': ('branchcode', 'branch', 'sortcode'),
    'account_type': ('accounttype', 'acctype'),
    'reference': ('reference', 'ref', 'paymentreference', 'eftreference',
                  'ownreference', 'narration'),
}


def _squash(s) -> str:
    return ''.join(ch for ch in str(s or '').lower() if ch.isalnum())


def _dec(v) -> Optional[Decimal]:
    txt = str(v if v is not None else '').strip().replace(',', '').replace(' ', '')
    if not txt:
        return None
    neg = txt.startswith('(') and txt.endswith(')')
    txt = txt.strip('()')
    for sym in ('BWP', 'P', 'ZAR', 'R'):
        if txt.upper().startswith(sym):
            txt = txt[len(sym):]
    try:
        val = Decimal(txt)
    except InvalidOperation:
        return None
    # Decimal happily parses 'NaN' and 'Infinity'. Both compare strangely and
    # neither is an amount (taskboard/bulk_payment_upload.py learned this the
    # expensive way — an Infinity passed "greater than zero").
    if not val.is_finite():
        return None
    return (-val if neg else val).quantize(Decimal('0.01'))


def _rows(blob: bytes, filename: str) -> list[list]:
    """Every row of the first sheet. python-calamine reads .xls, .xlsx and .ods
    in one path and is the reader the rest of this codebase standardises on."""
    from python_calamine import CalamineWorkbook
    wb = CalamineWorkbook.from_filelike(io.BytesIO(blob))
    if not wb.sheet_names:
        raise SettlementFileError(f'{filename} has no sheets in it.')
    return wb.get_sheet_by_name(wb.sheet_names[0]).to_python()


def parse_eft_xls(blob: bytes, filename: str = 'listing.xls') -> list[dict]:
    """The claim lines in the settlement listing.

    Returns [{claim_number, payee, amount, account_number, bank_name,
    branch_code, account_type, reference}] — one dict per claim to be settled.
    Raises SettlementFileError if the header cannot be found or the two columns
    everything else hangs off (claim number and amount) are absent: a listing we
    only half understand is never half-loaded.
    """
    rows = _rows(blob, filename)
    header_i = cols = None
    for i, row in enumerate(rows[:40]):
        squashed = {_squash(c): j for j, c in enumerate(row) if _squash(c)}
        found = {}
        for field_name, aliases in _COLUMNS.items():
            for alias in aliases:
                if alias in squashed:
                    found[field_name] = squashed[alias]
                    break
        if 'claim_number' in found and 'amount' in found:
            header_i, cols = i, found
            break
    if cols is None:
        raise SettlementFileError(
            f'{filename}: could not find a header row carrying both a claim '
            'number and an amount. Nothing was loaded — check the column names '
            'against healthcare/claims_settlement.py._COLUMNS.')

    out: list[dict] = []
    for row in rows[header_i + 1:]:
        def cell(name: str) -> str:
            j = cols.get(name)
            if j is None or j >= len(row) or row[j] is None:
                return ''
            return str(row[j]).strip()

        claim = cell('claim_number')
        if not claim or _TOTALS_RE.match(claim):
            # Blank rows, and the "Total" label parked in the claim column —
            # which would otherwise become a payment request for the whole file.
            continue
        amount = _dec(cell('amount'))
        if amount is None or amount <= 0:
            # Never raise a 0.00 request: it would burn this claim's one dedupe
            # slot, so the corrected figure could never load. Left for a human.
            out.append({'claim_number': claim, 'amount': None,
                        'problem': 'the amount is blank, zero or unreadable',
                        'payee': cell('payee'), 'account_number': '',
                        'bank_name': '', 'branch_code': '', 'account_type': '',
                        'reference': cell('reference')})
            continue
        out.append({
            'claim_number': claim[:60],
            'payee': cell('payee')[:200],
            'amount': amount,
            'account_number': re.sub(r'[^0-9]', '', cell('account_number'))[:64],
            'bank_name': cell('bank_name')[:120],
            'branch_code': cell('branch_code')[:20],
            'account_type': cell('account_type')[:10],
            'reference': cell('reference')[:60],
            'problem': '',
        })
    return out


# ---------------------------------------------------------------------------
# 4. The no-double-load key
# ---------------------------------------------------------------------------

def line_identity(claim_number, amount, reference, payee) -> tuple:
    """The parts of a settlement line that make it the line it is."""
    return (
        str(claim_number or '').strip().lower(),
        str(Decimal(amount or 0).quantize(Decimal('0.01'))),
        str(reference or '').strip().lower(),
        str(payee or '').strip().lower(),
    )


def compute_line_dedupe_key(claim_number, amount, reference, payee,
                            occurrence: int = 0) -> str:
    """Deterministic fingerprint for one settlement line.

    Modelled on `banking.models.compute_line_dedupe_key`, including the
    occurrence counter and for the same reason. The key is built from the
    line's own CONTENT and nothing about the file that carried it: AFA re-send
    a corrected file under a new name, and a key that included the file name
    would call every line in it new and raise the whole settlement twice.

    `occurrence` — 0 for the first line with this exact content in a listing,
    1 for the second, and so on — because one claim genuinely can carry two
    identical lines, while the commonest real double-load is the SAME file
    arriving again, where every line is occurrence 0 again and collides with
    the key already on file. Caught, at the database level.
    """
    parts = list(line_identity(claim_number, amount, reference, payee)) + [
        str(occurrence)]
    return hashlib.sha256('|'.join(parts).encode('utf-8')).hexdigest()


def with_occurrences(lines: list[dict]) -> list[tuple[dict, str]]:
    """Pair each line with its dedupe key, counting repeats within the listing."""
    seen: dict[tuple, int] = {}
    out = []
    for ln in lines:
        ident = line_identity(ln.get('claim_number'), ln.get('amount') or 0,
                              ln.get('reference'), ln.get('payee'))
        n = seen.get(ident, 0)
        seen[ident] = n + 1
        out.append((ln, compute_line_dedupe_key(
            ln.get('claim_number'), ln.get('amount') or 0, ln.get('reference'),
            ln.get('payee'), occurrence=n)))
    return out


def file_fingerprint(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


# ---------------------------------------------------------------------------
# 5. Creating the payment requests — through the ordinary gated path
# ---------------------------------------------------------------------------

@dataclass
class LoadResult:
    created: list[str] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)


def _entered_by():
    from django.contrib.auth.models import User
    return User.objects.filter(email__iexact=ENTERED_BY_EMAIL).first()


def require_maker():
    """The Omni login everything is entered in the name of, or refuse.

    Its own function so the COMMAND can ask the question BEFORE it writes the
    run row. Asking afterwards was a silent failure of the worst kind: the row
    was already created carrying the file's fingerprint and the model's default
    status of LOADED, the raise escaped every `except`, and so the Saturday
    ended with a traceback, no e-mail and a row claiming success — and because
    the fingerprint is UNIQUE, every later run of the same file then reported
    "already loaded ... Nothing to do" and stopped. One missing login would
    have blocked that settlement for good, quietly. (Fable, 14-Sep-2026.)
    """
    user = _entered_by()
    if user is None:
        raise SettlementFileError(
            f'{ENTERED_BY_EMAIL} has no Omni login, so nothing can be entered in '
            'their name. Nothing was loaded.')
    return user


def _payload(line: dict, run_label: str) -> dict:
    """One payment request, in the shape the ordinary create endpoint expects."""
    claim = line['claim_number']
    return {
        'entity': 'ADIC',
        # `adh` already exists on PaymentRequest.Category — the spec is explicit
        # that no new category is invented for this.
        'category': 'adh',
        'currency': 'BWP',
        'subject': f'ADH claim settlement {claim} — AFA {run_label}'[:200],
        'payee': line.get('payee') or '',
        'account_name': line.get('payee') or '',
        'account_number': line.get('account_number') or '',
        'bank_name': line.get('bank_name') or '',
        'branch_code': line.get('branch_code') or '',
        'account_type': line.get('account_type') or '',
        'line_items': [{
            'description': f'ADH health claim settlement {claim}',
            'ref': line.get('reference') or claim,
            'amount': str(line['amount']),
        }],
    }


def create_payment_requests(run, lines: list[dict], *, dry_run: bool = False
                            ) -> LoadResult:
    """One Omni payment request per claim line. Creates nothing else.

    🔴 Every request goes through the ORDINARY create endpoint
    (`taskboard.payment_views.payment_requests`), exactly as a person typing one
    would, and exactly as the Drop Box does. That endpoint carries roughly six
    hundred lines of money controls — the duplicate gate PAY-DUP-01, the
    bank-change gate PAY-BANK-01, first-payment-to-a-new-payee PAY-BANK-03, the
    bank cross-check, the supplier terms. A loader that wrote PaymentRequest
    rows itself would walk past every one of them, and past every control added
    after it. So this builds the payload and calls the endpoint.

    The no-double-load guard is a UNIQUE column, not an `if`. The key row is
    CLAIMED first, inside its own transaction; a second run loading the same
    line loses that insert to the database and skips. If the request itself then
    fails, the claim is released so the line can come in clean next Saturday.
    """
    from django.db import IntegrityError, transaction
    from rest_framework.test import APIRequestFactory, force_authenticate

    from healthcare.models import AdhSettlementLine

    result = LoadResult()
    user = require_maker()

    run_label = run.file_name or run.loaded_on.isoformat()
    for line, key in with_occurrences(lines):
        claim = line['claim_number']
        if line.get('problem'):
            result.failed.append({'claim_number': claim,
                                  'reason': line['problem']})
            continue
        if dry_run:
            if AdhSettlementLine.objects.filter(dedupe_key=key).exists():
                result.skipped.append({'claim_number': claim,
                                       'reason': 'already loaded'})
            else:
                result.created.append(f'(dry run) {claim}')
            continue

        try:
            with transaction.atomic():
                claimed = AdhSettlementLine.objects.create(
                    run=run, dedupe_key=key, claim_number=claim[:60],
                    amount=line['amount'])
        except IntegrityError:
            # The unique column caught a line an earlier run already took. This
            # is the expected, harmless outcome of a file arriving twice.
            result.skipped.append({'claim_number': claim,
                                   'reason': 'already loaded'})
            continue

        factory = APIRequestFactory()
        inner = factory.post('/api/v1/payment-requests/',
                             _payload(line, run_label), format='json')
        force_authenticate(inner, user=user)
        from taskboard.payment_views import payment_requests
        resp = payment_requests(inner)
        data = getattr(resp, 'data', {}) or {}
        if resp.status_code == 201:
            claimed.payment_request_id = data.get('id')
            claimed.payment_ref = data.get('ref') or ''
            claimed.save(update_fields=['payment_request_id', 'payment_ref'])
            result.created.append(data.get('ref') or claim)
        else:
            # Release the key so a corrected file can load this line. A claim
            # held by a request that was never created is a claim nobody can
            # ever settle through Omni.
            claimed.delete()
            result.failed.append({
                'claim_number': claim,
                'reason': str(data.get('detail') or f'HTTP {resp.status_code}')})
    return result
