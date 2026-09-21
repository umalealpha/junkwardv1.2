"""
fnb/pop_capture.py — turn FNB payment-result emails into filed proofs of payment.

CFO 2026-08-26: "these are the payment proofs that come into my email. Can we pick
them up and put them inside the relevant payment request so we keep a long-term
record?"

What the mail actually contains (measured over 30 days before this was built, so
the design is not a guess):
  * 48 payments, BWP 2,585,588.37, every one "Fully Processed".
  * NOT ONE of them carries an attachment. The body text IS the proof.
  * 3 of 48 match an Omni payment request. 15 of the 17 carrying a claim number
    match a real claim.

So the filing order is CLAIM first, payment request second, queue third — and the
queue is a deliverable in its own right: paid money with no record in Omni.

Reuses `fnb.email_reconcile.parse_result_email` (the parser already trusted by
fnb_email_autoclose) and the same Mail.Read reader app. Nothing here posts to the
ledger, moves money, or changes a payment's status.
"""
from __future__ import annotations

import io
import logging
import re
from datetime import date, timedelta
from decimal import Decimal

import requests
from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from core.management.commands.auto_reply_omni_mail import _reader_token, GRAPH_BASE
from fnb.email_reconcile import parse_result_email
from fnb.models import FNBProofOfPayment as POP

log = logging.getLogger(__name__)

FNB_SENDER = 'noreply@fnb.co.za'
DEFAULT_MAILBOX = 'pganesharajah@alphadirect.co.bw'

# FNB's OnceOff reference starts with the claim number when the payment is a
# claim: "G2026005234 LBM (PTY) LTD". Nine or ten digits after the G.
CLAIM_NUMBER_RE = re.compile(r'\b(G\d{9,10})\b', re.IGNORECASE)

# The external-sender warning Exchange staples onto every inbound mail. Kept out
# of the stored proof so the document reads as the bank's words, not IT's.
# Ordered longest-first: Exchange appends TWO sentences and a non-greedy match on
# the first one ("content is safe.") leaves the second in the stored proof. That
# shipped in the first real PDF off prod on 26-Aug — the fix is to try the full
# banner before the short form, never the other way round.
_CAUTION_RES = [
    re.compile(r'CAUTION:.*?ransomware attacks\.', re.IGNORECASE | re.DOTALL),
    re.compile(r'CAUTION:.*?content is safe\.', re.IGNORECASE | re.DOTALL),
    # The second sentence ON ITS OWN, with no CAUTION: in front of it.
    # Needed because the first version of this cleaner stripped
    # "CAUTION: … content is safe." and left this sentence behind — so the
    # rows already stored have no anchor for the patterns above, and a
    # re-render was a silent no-op until this pattern existed (found on prod
    # 26-Aug by checking the stored text, not by trusting "re-rendered 29").
    # Exchange boilerplate, matched precisely so it cannot eat FNB's own words.
    re.compile(r'Do not process any payments based on this email.*?'
               r'ransomware attacks\.', re.IGNORECASE | re.DOTALL),
]

NAVY = (0x0D / 255, 0x1B / 255, 0x2A / 255)


def capture_from() -> 'date | None':
    """The first day proofs are captured for. Nothing before it is ever stored.

    CFO 2026-08-26: "old payments ignore, we do this properly from today." A
    fixed date rather than a rolling window, so a widened --hours or a re-run
    cannot quietly pull history in behind it.
    """
    raw = getattr(settings, 'FNB_POP_CAPTURE_FROM', '') or ''
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw).strip())
    except ValueError:
        log.warning('FNB_POP_CAPTURE_FROM=%r is not an ISO date — ignoring it', raw)
        return None


def clean_body(raw: str) -> str:
    """The bank's own text, with the Exchange caution banner removed."""
    out = raw or ''
    for rx in _CAUTION_RES:
        out = rx.sub('', out)
    # Collapse the blank run the banner leaves behind.
    return re.sub(r'\n{3,}', '\n\n', out).strip()


def claim_number_in(reference: str) -> str:
    m = CLAIM_NUMBER_RE.search(reference or '')
    return m.group(1).upper() if m else ''


# ---------------------------------------------------------------------------
# Read the mailbox
# ---------------------------------------------------------------------------
def fetch_fnb_emails(mailbox: str = DEFAULT_MAILBOX, *, hours: int | None = 72,
                     cap: int = 2000) -> list[dict]:
    """FNB result emails newest-first. `hours=None` reads the whole mailbox
    (used for the CFO's one-off backfill)."""
    select = 'id,subject,from,receivedDateTime,body'
    if hours is None:
        url = (f'{GRAPH_BASE}/users/{mailbox}/messages'
               f'?$select={select}&$top=200&$orderby=receivedDateTime desc')
    else:
        since = (timezone.now() - timedelta(hours=hours)).strftime('%Y-%m-%dT%H:%M:%SZ')
        url = (f'{GRAPH_BASE}/users/{mailbox}/messages'
               f'?$filter=receivedDateTime ge {since}'
               f'&$select={select}&$top=200&$orderby=receivedDateTime desc')

    # Ask Graph for PLAIN TEXT. FNB sends HTML and the parser's markers are
    # plain-text — interleaved tags would be a silent no-match (the same trap
    # fnb_email_autoclose documents).
    headers = {'Authorization': f'Bearer {_reader_token()}',
               'Prefer': 'outlook.body-content-type="text"'}
    out: list[dict] = []
    while url and len(out) < cap:
        r = requests.get(url, headers=headers, timeout=40)
        if r.status_code != 200:
            raise RuntimeError(f'Graph read {r.status_code}: {r.text[:250]}')
        data = r.json()
        for m in data.get('value', []):
            addr = ((m.get('from') or {}).get('emailAddress') or {}).get('address', '')
            if FNB_SENDER in (addr or '').lower():
                out.append(m)
        url = data.get('@odata.nextLink')
    return out


# ---------------------------------------------------------------------------
# The proof document
# ---------------------------------------------------------------------------
def build_proof_pdf(*, reference: str, amount: Decimal, bank_status: str,
                    received_at, body: str, mailbox: str) -> bytes:
    """A one-page PDF holding FNB's confirmation verbatim.

    Deliberately plain: it is evidence, not a report. The bank's sentence is
    reproduced exactly, with the provenance (mailbox, timestamp) underneath, so
    a reader can always tell where it came from.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    W, H = A4
    y = H - 24 * mm

    c.setFillColorRGB(*NAVY)
    c.rect(0, H - 18 * mm, W, 18 * mm, stroke=0, fill=1)
    c.setFillColorRGB(1, 1, 1)
    c.setFont('Helvetica-Bold', 12)
    c.drawString(18 * mm, H - 11.5 * mm, 'Alpha Direct Insurance Company (Pty) Ltd')
    c.setFont('Helvetica', 9)
    c.drawRightString(W - 18 * mm, H - 11.5 * mm, 'Proof of payment — First National Bank')

    c.setFillColorRGB(0, 0, 0)
    y -= 6 * mm
    c.setFont('Helvetica-Bold', 15)
    c.drawString(18 * mm, y, f'BWP {amount:,.2f}')
    y -= 9 * mm
    c.setFont('Helvetica', 11)
    c.drawString(18 * mm, y, reference[:95])
    y -= 7 * mm
    c.setFont('Helvetica-Bold', 10)
    c.drawString(18 * mm, y, f'Bank status: {(bank_status or "").title()}')

    y -= 12 * mm
    c.setFont('Helvetica-Bold', 9)
    c.drawString(18 * mm, y, "FNB's confirmation, as received:")
    y -= 6 * mm
    c.setFont('Helvetica', 9)
    for line in _wrap(clean_body(body), 105):
        if y < 30 * mm:
            c.showPage(); y = H - 25 * mm; c.setFont('Helvetica', 9)
        c.drawString(18 * mm, y, line)
        y -= 4.6 * mm

    c.setFont('Helvetica-Oblique', 7.5)
    c.setFillColorRGB(.42, .45, .5)
    stamp = received_at.strftime('%d %B %Y at %H:%M') if received_at else 'unknown'
    provenance = (f'Received {stamp} in {mailbox} from {FNB_SENDER}. '
                  f'Captured by Omni — the email carried no attachment, '
                  f'so this text is the proof.')
    # Wrapped, not one long line: at 7.5pt the single line ran off the right edge
    # and the sentence was cut mid-word on the first real PDF (26-Aug).
    fy = 19 * mm
    for line in _wrap(provenance, 135):
        c.drawString(18 * mm, fy, line)
        fy -= 3.4 * mm
    c.showPage()
    c.save()
    return buf.getvalue()


def _wrap(text: str, width: int) -> list[str]:
    lines: list[str] = []
    for para in (text or '').splitlines():
        para = para.rstrip()
        if not para:
            lines.append('')
            continue
        cur = ''
        for word in para.split():
            if len(cur) + len(word) + 1 > width:
                lines.append(cur); cur = word
            else:
                cur = f'{cur} {word}'.strip()
        if cur:
            lines.append(cur)
    return lines


# ---------------------------------------------------------------------------
# Filing
# ---------------------------------------------------------------------------
def _received(msg: dict):
    """The email's received timestamp, or now if Graph omitted it."""
    from django.utils.dateparse import parse_datetime
    raw = msg.get('receivedDateTime')
    return (parse_datetime(raw) if raw else None) or timezone.now()


def _find_claim(claim_number: str):
    if not claim_number:
        return None
    from integrations.models import GraphiteClaim
    return GraphiteClaim.objects.filter(claim_number__iexact=claim_number).first()


def _find_payment_request(parsed: dict):
    """Exact amount AND a reference overlap — never amount alone.

    Same rule as fnb_email_autoclose, and for the same reason: two payments can
    share an amount, and that is exactly how a double-payment gets mis-filed.
    An ambiguous amount match returns None rather than guessing.
    """
    from taskboard.models import PaymentRequest
    from fnb.email_reconcile import _norm

    email_ref = _norm(parsed['ref'])
    if not email_ref:
        return None
    cands = list(PaymentRequest.objects.filter(total=parsed['amount'])[:25])
    hits = []
    for pr in cands:
        for field in (pr.graphite_ref, pr.bank_our_reference, pr.bank_narration,
                      pr.payee, pr.ref):
            token = _norm(field or '')
            if token and len(token) >= 5 and (token in email_ref or email_ref in token):
                hits.append(pr)
                break
    return hits[0] if len(hits) == 1 else None


@transaction.atomic
def capture_one(msg: dict, *, mailbox: str, dry_run: bool = False) -> dict:
    """Store one FNB email as a proof and file it. Idempotent on the message id."""
    mid = msg.get('id') or ''
    existing = POP.objects.filter(graph_message_id=mid).first()
    if existing:
        return {'ref': existing.reference, 'state': existing.state, 'action': 'already-captured'}

    received_at = _received(msg)
    floor = capture_from()
    if floor and received_at and received_at.date() < floor:
        return {'ref': '', 'state': '', 'action': 'before-cutoff'}

    raw = (msg.get('body') or {}).get('content') or ''
    parsed = parse_result_email(raw)
    if not parsed:
        return {'ref': '', 'state': '', 'action': 'unparseable'}

    claim_no = claim_number_in(parsed['ref'])
    claim = _find_claim(claim_no)
    pr = None if claim else _find_payment_request(parsed)

    if claim:
        state = POP.State.FILED_CLAIM
    elif pr:
        state = POP.State.FILED_REQUEST
    else:
        state = POP.State.UNFILED

    if dry_run:
        return {'ref': parsed['ref'], 'amount': parsed['amount'], 'state': state,
                'claim': claim_no or '-', 'action': 'would-capture'}

    pop = POP(
        graph_message_id=mid,
        mailbox=mailbox,
        received_at=received_at,
        subject=(msg.get('subject') or '')[:300],
        body_text=clean_body(raw),
        reference=parsed['ref'][:200],
        amount=parsed['amount'],
        bank_status=(parsed['status'] or '')[:60],
        paid=bool(parsed['paid']),
        state=state,
        claim=claim,
        payment_request=pr,
        claim_number_seen=claim_no,
    )
    pdf = build_proof_pdf(reference=pop.reference, amount=pop.amount,
                          bank_status=pop.bank_status, received_at=received_at,
                          body=raw, mailbox=mailbox)
    safe_ref = re.sub(r'[^A-Za-z0-9]+', '-', pop.reference)[:60].strip('-') or 'proof'
    pop.proof_pdf.save(f'FNB-POP-{safe_ref}.pdf', ContentFile(pdf), save=False)
    pop.save()

    # When it belongs to a payment request, also hang it on that request's own
    # attachment list so it appears where Finance already looks for documents.
    if pr is not None:
        _attach_to_request(pop, pr, pdf)

    return {'ref': pop.reference, 'amount': pop.amount, 'state': pop.state,
            'claim': claim_no or '-', 'action': 'captured'}


def _attach_to_request(pop: POP, pr, pdf_bytes: bytes) -> None:
    """Mirror the proof onto PaymentRequestAttachment (the existing document list)."""
    try:
        from taskboard.models import PaymentRequestAttachment as PRA
        att = PRA(request=pr, original_name='FNB payment confirmation.pdf')
        att.file.save(f'FNB-POP-{pop.reference[:40]}.pdf'.replace('/', '-'),
                      ContentFile(pdf_bytes), save=False)
        att.save()
    except Exception as exc:                                     # noqa: BLE001
        # A mirror failure must never lose the proof — the POP row is the record.
        log.warning('POP %s captured but not mirrored onto %s: %s', pop.pk, pr.ref, exc)


def capture(*, mailbox: str = DEFAULT_MAILBOX, hours: int | None = 72,
            dry_run: bool = False) -> dict:
    """Read the mailbox and file every FNB proof it contains."""
    msgs = fetch_fnb_emails(mailbox, hours=hours)
    counts = {'seen': len(msgs), 'captured': 0, 'already': 0, 'unparseable': 0,
              'before_cutoff': 0, 'filed_claim': 0, 'filed_request': 0, 'unfiled': 0}
    rows = []
    for m in msgs:
        r = capture_one(m, mailbox=mailbox, dry_run=dry_run)
        rows.append(r)
        if r['action'] == 'already-captured':
            counts['already'] += 1
        elif r['action'] == 'before-cutoff':
            counts['before_cutoff'] += 1
        elif r['action'] == 'unparseable':
            counts['unparseable'] += 1
        else:
            counts['captured'] += 1
            if r['state'] in counts:
                counts[r['state']] += 1
    return {'counts': counts, 'rows': rows}
