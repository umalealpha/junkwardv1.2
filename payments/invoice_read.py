"""payments/invoice_read.py — read an uploaded invoice and pre-fill a payment.

CFO 2026-08-22 (raised by Tlamelo): let staff upload the invoice when entering a
payment so Omni reads the total due and the payee's bank account instead of
re-keying them. This module SAVES NOTHING — it returns the fields for the
operator to check and confirm before the payment is created.

Engine ladder — the CFO's requested order, all REUSED (nothing built from
scratch):
  1. Own, offline extract — core.doc_parse.parse: RapidOCR for images,
     pdfplumber for born-digital PDFs, calamine for spreadsheets. Nothing leaves
     the server on this tier.
  2. Field extraction from that text — core.ai_assist.reasoning_complete, whose
     own chain is Ollama(local) -> DeepSeek -> Gemini -> ...

     PII firewall (CFO decision 2026-09-02, data controller): the text is run
     through is_safe_for_ai() before it is sent — the same redactor the Bonu
     reader uses (AD-POL-AI-GOV-001). The supplier account number (10+ digits)
     is redacted to [NUMBER-REDACTED], so it never reaches an external engine in
     the clear. (An earlier version of this docstring claimed such a firewall
     already existed; it did not — no is_safe_for_ai() call was here — and this
     is the change that makes the claim true.)

     This is why the account number below is read DETERMINISTICALLY from the
     original local text, not asked of the model: redaction removes the very
     digits from what the model sees, and the model declined to echo them
     anyway. Amounts / IDs / emails / phones are redacted too; a field the model
     then cannot see comes back empty and the operator types it — the correct
     trade against leaking it. (Two documented residuals for the CFO as data
     controller: a total over ~1,000,000 is redacted, so the operator types it;
     and a bank account of only 6–8 digits is NOT redacted — is_safe_for_ai
     catches 9-digit Omang and 10+-digit numbers — so it would still be sent in
     the clear. Real Botswana EFT accounts are 10–13 digits, so this is a
     theoretical edge, not a live exposure.)
  3. Scans / photos where the local OCR is too weak to yield the fields — the
     image itself is read by core.ai_assist.vision_complete (Gemini vision).
     Here the account number is visible to the vision model; the CFO authorised
     this external read on 2026-08-22 (he is the data controller) as the only way
     to read a photographed account number. A document whose TEXT the firewall
     refused (too PII-dense) is NEVER sent to the vision model as an image — its
     text was blocked for a reason, so its pixels must not leave either.

Extracted: total amount due, payee bank account number, bank name, branch code,
invoice number, payee name.
"""
from __future__ import annotations

import base64
import logging
import re
from decimal import Decimal, InvalidOperation

log = logging.getLogger(__name__)

_FIELDS_PROMPT = (
    'You are reading a supplier invoice so it can be paid. From the invoice text '
    'below, extract these fields and return ONLY a JSON object with EXACTLY these '
    'keys:\n'
    '  total_amount   - the total amount DUE / PAYABLE, as a plain number with no '
    'commas or currency symbol, or null.\n'
    '  account_number - the payee / beneficiary BANK ACCOUNT NUMBER, digits only, '
    'or null.\n'
    '  bank_name      - the payee bank name, or null.\n'
    '  branch_code    - the bank branch or sort code, digits only, or null.\n'
    '  invoice_number - the invoice or reference number, or null.\n'
    '  payee_name     - the supplier / beneficiary name, or null.\n'
    'Use null for anything not clearly present. Do NOT guess or invent a value.\n\n'
    'INVOICE TEXT:\n'
)

_KEYS = ('total_amount', 'account_number', 'bank_name', 'branch_code',
         'invoice_number', 'payee_name')

_IMAGE_EXTS = ('.png', '.jpg', '.jpeg', '.webp', '.tif', '.tiff', '.bmp', '.heic')


def _blank_fields() -> dict:
    return {k: None for k in _KEYS}



# ── Reading the ACCOUNT NUMBER without the model (CFO 2026-09-02) ────────────
# Proven on prod against two different invoices: the reasoning model returns
# `"account_number": null` while correctly returning the branch code from the
# very next line of the same invoice. The digits ARE in the text we send it —
# the model simply declines to echo a bank account number. No prompt wording
# fixes that reliably.
#
# It also should not have to. This repo already settled the principle for this
# exact field, in taskboard/payee_bank_history.py: "Deliberately not an AI …
# this is the field that decides who receives the money — the one place in Omni
# where a plausible invention is most expensive. Deterministic code decides, AI
# only explains." A regex over text we already hold is exactly that.
#
# Deliberately conservative — it may only ADD a number the model declined, and
# only when the invoice LABELS it. Every rule below exists because Fable proved
# a wrong-but-confident read without it (2026-09-02):
#   * a label is required; a bare number floating on the page is never taken;
#   * the label must not be part of a bigger word — "FAC No", "HVAC", "AC unit"
#     are NOT account labels (no word-embedded "ac"); short forms (a/c, acc,
#     acct) must carry a number/no/#/:/- suffix, only the full word "account"
#     may take bare whitespace;
#   * a candidate sitting AFTER a branch / sort / swift / bic word on the line is
#     dropped, so a customer/branch number is never taken as the account;
#   * a candidate immediately followed by a decimal (",dd" / ".dd") is an amount,
#     not an account;
#   * a run that is really two ≥5-digit numbers jammed together (account + an
#     unlabelled branch in the next column) is dropped;
#   * two DIFFERENT surviving candidates return nothing rather than a guess.
# A wrong account number is worse than an empty box the operator fills in.
_ACCT_LABEL = re.compile(
    r'(?<![A-Za-z0-9])'                     # label not embedded in a bigger word
    r'(?:bank\s*)?'
    r'(?:'
    r'account\.?\s*(?:number|no\.?|nr\.?|#)?'   # full word may take bare space
    r'|(?:a/?c|acct?)\.?\s*(?:number|no\.?|nr\.?|#)'  # short forms NEED a suffix
    r')'
    r'\s*[:\-]?\s*'
    r'([0-9][0-9\s\-]{4,24}[0-9])',
    re.I)
# "…branch / sort code / swift / bic …" ending right before the matched digits.
_BRANCH_BEFORE = re.compile(r'(?:branch|sort\s*code|swift|bic)[\s.:#\-]*$', re.I)
# Two whitespace/dash-separated tokens, both ≥5 digits — a merged-column read.
_MERGED_COLUMNS = re.compile(r'\d{5,}[\s\-]+\d{5,}')


def _account_number_from_text(text: str) -> str | None:
    """The labelled bank account number in `text`, or None if not certain."""
    found: set[str] = set()
    for line in (text or '').splitlines():
        for m in _ACCT_LABEL.finditer(line):
            # A branch/sort/swift word sitting just before the digits means this
            # is that number, not the account (utility-bill customer ref, etc.).
            if _BRANCH_BEFORE.search(line[:m.start(1)]):
                continue
            # A decimal tail means the "number" is really a money amount.
            tail = line[m.end(1):m.end(1) + 2]
            if re.match(r'[.,]\d', tail):
                continue
            raw = m.group(1)
            # Two ≥5-digit tokens jammed together = account + a bare branch in the
            # next column; can't tell them apart, so take neither.
            if _MERGED_COLUMNS.search(raw):
                continue
            digits = re.sub(r'\D', '', raw)
            if 6 <= len(digits) <= 20:     # too short/long to be an account
                found.add(digits)
    if len(found) == 1:
        return found.pop()
    return None                            # nothing, or ambiguous — say nothing


def _parse_json(raw) -> dict | None:
    try:
        data = raw if isinstance(raw, dict) else __import__('json').loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    return {k: data.get(k) for k in _KEYS}


def _looks_like_image(mime: str, filename: str) -> bool:
    return ((mime or '').lower().startswith('image/')
            or (filename or '').lower().endswith(_IMAGE_EXTS))


def _is_pdf(mime: str, filename: str) -> bool:
    return ((mime or '').lower() == 'application/pdf'
            or (filename or '').lower().endswith('.pdf'))


def _pdf_first_page_png(file_bytes: bytes) -> bytes | None:
    """Rasterise page 1 of a scanned PDF to PNG so the vision model can read it.

    A scanner emits a PDF with no text layer, so core.doc_parse returns nothing
    and the vision path (which the CFO authorised) would otherwise never fire for
    the commonest real scan format. Uses pypdfium2 (already a dependency). Returns
    None if it is unavailable or the render fails — the caller then reports the
    invoice as unreadable rather than crashing."""
    try:
        import io
        import pypdfium2 as pdfium
    except Exception as exc:  # noqa: BLE001 — dependency missing
        log.warning('invoice_read: pypdfium2 unavailable (%s)', exc)
        return None
    try:
        pdf = pdfium.PdfDocument(file_bytes)
        if len(pdf) == 0:
            return None
        pil = pdf[0].render(scale=2.0).to_pil()
        buf = io.BytesIO()
        pil.save(buf, format='PNG')
        return buf.getvalue()
    except Exception as exc:  # noqa: BLE001
        log.warning('invoice_read: pdf rasterise failed (%s)', exc)
        return None


def _has_useful(fields: dict | None) -> bool:
    """A read is only worth pre-filling if it found the money or the account —
    the two fields the operator most wants filled."""
    return bool(fields) and any(fields.get(k) for k in ('total_amount', 'account_number'))


def _clean(fields: dict) -> dict:
    """Coerce to the shapes the form and the bank expect: amount to 2dp, account
    and branch to digits only, the rest to trimmed strings. Never raises."""
    out = dict(fields)
    amt = out.get('total_amount')
    if amt not in (None, ''):
        try:
            out['total_amount'] = f'{Decimal(str(amt).replace(",", "").strip()):.2f}'
        except (InvalidOperation, ValueError):
            out['total_amount'] = None
    else:
        out['total_amount'] = None
    for k in ('account_number', 'branch_code'):
        v = out.get(k)
        out[k] = (re.sub(r'\D', '', str(v)) or None) if v else None
    for k in ('bank_name', 'invoice_number', 'payee_name'):
        v = out.get(k)
        s = str(v).strip() if v else ''
        # A redaction placeholder the model echoed back (e.g. it saw
        # [NUMBER-REDACTED] where a long invoice ref used to be) is not a value —
        # never pre-fill that literal tag into a form box.
        out[k] = None if (not s or 'REDACTED]' in s) else s
    return out


def _extract_from_text(text_for_ai: str) -> dict | None:
    """Ask the reasoning ladder to pull fields from invoice text.

    The CALLER redacts — read_invoice runs is_safe_for_ai() and passes the
    redacted text — so the account number and other PII never reach an external
    engine in the clear. This function must never be handed raw invoice text.
    """
    from core.ai_assist import reasoning_complete
    try:
        raw = reasoning_complete(_FIELDS_PROMPT + text_for_ai[:6000],
                                 response_format='json_object', max_tokens=400)
    except Exception as exc:  # noqa: BLE001 — any engine failure = no fields
        log.warning('invoice_read: text extraction failed (%s)', exc)
        return None
    return _parse_json(raw)


def _extract_from_image(file_bytes: bytes, mime: str) -> dict | None:
    from core.ai_assist import vision_complete
    b64 = base64.b64encode(file_bytes).decode('ascii')
    data_url = f'data:{mime or "image/png"};base64,{b64}'
    try:
        raw = vision_complete(
            _FIELDS_PROMPT + '(The invoice is the attached image. Return the JSON.)',
            data_url, timeout=40.0)
    except Exception as exc:  # noqa: BLE001
        log.warning('invoice_read: vision extraction failed (%s)', exc)
        return None
    return _parse_json(raw)


def read_invoice(file_bytes: bytes, mime: str = '', filename: str = '') -> dict:
    """Read an invoice and return {ok, fields, tier, message}. Saves nothing.

    `tier` is 'text' (own OCR/text + reasoning ladder) or 'vision' (image read by
    the vision model) or 'none' (nothing usable found)."""
    from core.doc_parse import parse

    fields: dict | None = None
    tier = ''
    # Tier 1 + 2: own extract, then the reasoning ladder over the text.
    try:
        result = parse(file_bytes, mime=mime, filename=filename)
        text = (getattr(result, 'text', '') or '').strip()
    except Exception as exc:  # noqa: BLE001
        log.warning('invoice_read: doc parse failed (%s)', exc)
        text = ''
    # Whether the text existed but the PII firewall REFUSED to send it. A refused
    # document must not then be shipped to the vision model as pixels — that would
    # leak the very text the firewall just blocked (Fable 2026-09-02).
    text_refused = False
    if text:
        from core.ai_assist import is_safe_for_ai
        # PII firewall (CFO decision 2026-09-02): redact before the text leaves
        # the box, the same is_safe_for_ai() the Bonu reader uses
        # (AD-POL-AI-GOV-001). The account number (10+ digits) becomes
        # [NUMBER-REDACTED], so it never reaches an external engine in the clear.
        safety = is_safe_for_ai(text)
        tier = 'text'
        if safety.safe:
            fields = _extract_from_text(safety.redacted_text)
        else:
            # Too PII-dense to send at all. Do NOT call the model, and mark the
            # document refused so the vision fallback below is skipped too.
            text_refused = True
            fields = _blank_fields()
            log.info('invoice_read: text too PII-dense to send to AI — %s',
                     '; '.join(safety.notes))
        # The account number is read from the ORIGINAL local text, never the
        # model — the model declines it (proven 2026-09-02) and cannot see it
        # anyway now it is redacted. The local read is authoritative; a model
        # value is only kept if the local read found nothing AND the model
        # returned real digits (a placeholder like "[NUMBER-REDACTED]" would
        # otherwise clean to empty and blank the field).
        from_text = _account_number_from_text(text)
        if from_text:
            fields = fields if fields is not None else _blank_fields()
            fields['account_number'] = from_text
        elif fields is not None and not re.sub(r'\D', '', str(fields.get('account_number') or '')):
            fields['account_number'] = None
    # Tier 3: read the image directly with the vision model when the text route
    # gave nothing usable. Covers photos/images AND scanned PDFs — a scanner
    # emits a PDF with no text layer, which core.doc_parse cannot read, so page 1
    # is rasterised first. NEVER for a document the firewall refused: its text
    # was blocked for a reason, so its image must not leave either.
    if not _has_useful(fields) and not text_refused:
        image_bytes, image_mime = None, mime
        if _looks_like_image(mime, filename):
            image_bytes = file_bytes
        elif _is_pdf(mime, filename):
            image_bytes, image_mime = _pdf_first_page_png(file_bytes), 'image/png'
        if image_bytes:
            img_fields = _extract_from_image(image_bytes, image_mime)
            if _has_useful(img_fields):
                fields, tier = img_fields, 'vision'

    if not _has_useful(fields):
        return {'ok': False, 'fields': _blank_fields(), 'tier': tier or 'none',
                'message': 'Could not read the invoice — please type the details in.'}
    return {'ok': True, 'fields': _clean(fields), 'tier': tier,
            'message': 'Read by Omni — check the highlighted boxes before you save.'}
