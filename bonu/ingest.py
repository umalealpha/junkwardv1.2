"""
bonu/ingest.py — the accountant drops an invoice in, the figures fill themselves.

CFO 2026-08-03: *"create a place where the accountant can upload the invoices, a cheap
OCR tool with the local ollama, where it will populate all the figures so they don't
physically manually capture."*

THE LADDER, CHEAPEST AND MOST ACCURATE FIRST. Reading a document with an AI when the
document already contains its own text is both slower and less accurate than just reading
the text — so the model is the LAST resort, not the first:

  1. **Excel / CSV**            → openpyxl / csv.       Exact. Free. No model.
  2. **PDF with a text layer**  → pdfplumber.           Exact. Free. No model.
     Most law-firm invoices are generated from a practice-management system, so this
     covers the majority and never hallucinates a figure.
  3. **Scanned PDF / photo**    → a vision model.       Only when there is genuinely no
     text to extract. This is the one path that needs OCR at all.

Then, and only then, a small language model is used for MAPPING — turning extracted text
into structured lines. It never invents a number: every figure it returns is checked back
against the extracted text, and anything it cannot ground is dropped and reported.

NOTHING IS SAVED AUTOMATICALLY. The parse comes back as a draft for the accountant to
confirm on screen next to the document. The machine types, the human agrees — which is the
whole point of the exercise and also the only safe way to let a model near a payable.
"""
from __future__ import annotations

import csv
import io
import json
import re
from decimal import Decimal, InvalidOperation

# Money as it really appears on invoices AND as Excel hands it over: 26,562.00 /
# 26 562 / 26562.00 / and a bare integer like 26562 — the last one is what an Excel
# cell gives and the first version of this pattern missed it, so every Excel invoice
# came back with no total.
MONEY = re.compile(
    r'(?<![\d.])('
    r'\d{1,3}(?:[ ,]\d{3})+(?:\.\d{1,2})?'   # 26,562.00 or 26 562
    r'|\d+\.\d{1,2}'                          # 26562.00
    r'|\d{3,}'                                 # 26562  (bare, 3+ digits)
    r')(?![\d.])')
DATE_PATTERNS = [
    (re.compile(r'\b(\d{4})-(\d{2})-(\d{2})\b'), lambda m: f'{m.group(1)}-{m.group(2)}-{m.group(3)}'),
    (re.compile(r'\b(\d{1,2})/(\d{1,2})/(\d{4})\b'),
     lambda m: f'{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}'),
]
# The reference. `\b` after the keyword and an EXPLICIT optional "No." matter:
# the old pattern put n, o and . inside a character class and required 3+ chars
# for the reference, so on "Invoice 15" it backtracked to `inv`, let the class
# eat the "o" of "invoice", and returned the reference **"ice"** — every bill
# with a one- or two-digit number came back as "ice", and they then collided
# with each other in the duplicate guard. (Fable, 9 Sep 2026.)
INVOICE_NO = re.compile(
    r'(?:tax\s+invoice|invoice|inv)\b'      # the keyword, as a whole word
    r'[ \t.#:]*'                           # punctuation / space
    r'(?:no\.?|number|nr\.?)?'              # an optional "No." / "Number"
    r'[ \t.#:]*'                           # more punctuation / space
    r'(?=[A-Z0-9/\-]*\d)'                    # ...and it must carry a digit: a label
                                             # word ("No", "Date") is never the ref
    r'([A-Z0-9][A-Z0-9/\-]*)',               # the reference itself, 1+ chars
    re.I)

# ---------------------------------------------------------------------------
# Which figure on the page is the TOTAL
#
# It used to be "the largest money figure anywhere on the document". That is
# wrong on an ordinary Botswana law-firm bill, and Fable proved it on 9 Sep 2026
# by running this function over eight plausible layouts. Because MONEY has a
# bare `\d{3,}` branch, the winner was:
#
#   the firm's bank account number in the footer   -> P62,455,718,890
#   a VAT registration number                      -> P12,345,678
#   a compact date, 20260904                       -> P20,260,904
#   a mobile number                                -> P71,234,567
#   "P O Box 1234" on a P1,026 bill                -> P1,234
#   "a claim of P450,000 for damages" in the text  -> P450,000 (bill was P20,976)
#   "balance brought forward" above a P10,830 bill -> P73,130
#
# A bank footer and a VAT number are on virtually EVERY page of a real firm's
# bill, so this was the normal case, not an edge case. And the figure feeds a
# spend cap that REFUSES a bill, so a wrong one either fires the refusal on a
# client who is under (which trains people to override the control) or lets a
# real breach through.
#
# So: only a figure on a line that SAYS it is the total counts, and when there
# is no such line the answer is None — the form leaves the box empty and the
# person types it. A blank is honest; a plausible wrong number is not, because
# a pre-filled figure is anchoring and gets confirmed unread.
# ---------------------------------------------------------------------------

#: A line that claims to carry the total.
TOTAL_LABEL = re.compile(
    r'\b(?:total|amount\s+due|balance\s+due|now\s+due|amount\s+payable|due\s+now)\b',
    re.I)

#: ...unless it is one of these, which also say "total" but name a DIFFERENT
#: amount: a running balance, or the figure before the last lines were added.
#:
#: "excluding VAT" is deliberately NOT here. It is the same bill, just a lower
#: figure, and a bill almost always shows the inclusive total beside it — taking
#: the largest figure ON THE LABELLED LINE then resolves it correctly, including
#: when a PDF flattens both columns onto one line. Excluding the whole line
#: instead threw the real total away.
#: WE ARE AN INSURER, and that is the point of the second line below. "total
#: loss" and "sum insured" are the ordinary words in a matter description on our
#: own legal bills — `\btotal\b` in "the vehicle a total loss, sum insured
#: P450,000" or "total claim value in dispute" satisfied the label rule and
#: handed back the sum in dispute instead of the P20,976 fee. Not an edge case
#: here; it is the house vocabulary. (Fable, 9 Sep 2026.)
NOT_THE_TOTAL = re.compile(
    r'\b(?:brought\s+forward|b/?f\b|previous|prior|sub[\s-]*total'
    r'|total\s+loss|sum\s+insured|claim|dispute|damages)', re.I)

#: Money that is unmistakably money, because it carries a decimal or a
#: thousands separator. An account number never does.
#: A SPACE-grouped number needs cents to count. Botswana mobiles are written
#: "71 234 567", which the comma-or-space form matched happily — and a flattened
#: PDF footer puts the firm's telephone on the very line that says TOTAL, so it
#: won. Comma grouping keeps its optional cents; space grouping must show them.
#: (Fable, /fabe gate, 9 Sep 2026 — probed with 22 adversarial lines.)
MONEY_STRICT = re.compile(
    r'(?<![\d.])('
    r'\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?'      # 26,562 or 26,562.00
    r'|\d{1,3}(?: \d{3})+\.\d{1,2}'          # 26 562.00 — cents required
    r'|\d+\.\d{1,2}'                          # 26562.00
    r')(?![\d.])')

#: "Excluding VAT" is not a disqualifier on its own — see NOT_THE_TOTAL — but a
#: labelled line carrying ONLY an exclusive figure is skipped, because taking it
#: understates the bill by the VAT, and understating is the direction that lets
#: a cap breach through. It counts only when a second figure sits beside it,
#: which is the flattened "excl | incl" column pair.
EXCLUSIVE = re.compile(r'\b(?:excl|ex\.?\s*vat)', re.I)   # excl / excluding / exclusive / ex VAT

#: A bare integer on a labelled line is allowed (Excel gives no decimals), but
#: only up to seven digits. Nothing we pay a law firm exceeds P9,999,999, while
#: a bank account is eleven digits and a mobile or a compact date is eight — so
#: the cap is what stops the bare branch reopening the hole it exists for.
MAX_BARE = 10_000_000


def total_from_labelled_line(text: str):
    """The largest money figure on a line that says it is the total, or None.

    Largest ON THAT LINE is deliberate: a total line often shows the amount
    excluding and including VAT, and the inclusive figure is the one owed.
    Preferring a properly formatted figure and only then falling back to a bare
    integer keeps Excel dumps working — an Excel row reads
    "TOTAL\t\t\t5250", where the label is what makes a bare number safe.
    """
    best = None
    for line in (text or '').splitlines():
        if not TOTAL_LABEL.search(line) or NOT_THE_TOTAL.search(line):
            continue
        figs = [f for f in (_money(x) for x in MONEY_STRICT.findall(line))
                if f is not None]
        # An "excluding VAT" line only counts when a second figure sits beside
        # it — otherwise we would take the pre-VAT amount and understate.
        if EXCLUSIVE.search(line) and len(figs) < 2:
            continue
        if not figs:
            # No formatted money on the line. Excel gives bare integers, so
            # allow one — capped, or a bank account on the total line wins.
            figs = [f for f in (_money(x) for x in MONEY.findall(line))
                    if f is not None and f < MAX_BARE]
        if figs:
            top = max(figs)
            if best is None or top > best:
                best = top
    return float(best) if best is not None else None


def _money(s):
    try:
        return Decimal(str(s).replace(',', '').replace(' ', ''))
    except (InvalidOperation, ValueError, AttributeError):
        return None


def extract_text(filename: str, blob: bytes) -> tuple[str, str, str]:
    """(text, method, error). Deterministic extraction only — no model involved."""
    name = (filename or '').lower()

    if name.endswith(('.xlsx', '.xlsm')):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(blob), data_only=True)
            rows = []
            for ws in wb.worksheets:
                for row in ws.iter_rows(values_only=True):
                    cells = [str(c) for c in row if c is not None]
                    if cells:
                        rows.append('\t'.join(cells))
            return '\n'.join(rows), 'excel (openpyxl) — exact, no AI', ''
        except Exception as e:      # noqa: BLE001
            return '', 'excel', f'could not open the workbook: {type(e).__name__}'

    if name.endswith('.csv'):
        try:
            txt = blob.decode('utf-8', 'replace')
            rows = ['\t'.join(r) for r in csv.reader(io.StringIO(txt))]
            return '\n'.join(rows), 'csv — exact, no AI', ''
        except Exception as e:      # noqa: BLE001
            return '', 'csv', f'could not read the file: {type(e).__name__}'

    if name.endswith('.pdf'):
        try:
            import pdfplumber
            out = []
            with pdfplumber.open(io.BytesIO(blob)) as pdf:
                for page in pdf.pages:
                    out.append(page.extract_text() or '')
                    for table in (page.extract_tables() or []):
                        for row in table:
                            cells = [str(c) for c in row if c]
                            if cells:
                                out.append('\t'.join(cells))
            text = '\n'.join(x for x in out if x.strip())
            if len(text.strip()) >= 40:
                return text, 'pdf text layer (pdfplumber) — exact, no AI', ''
            # Nothing to read: it is a scan. Say so rather than guessing.
            return '', 'pdf-scanned', ('this PDF has no text layer — it is a scan, so it needs '
                                       'the vision model path')
        except Exception as e:      # noqa: BLE001
            return '', 'pdf', f'could not open the PDF: {type(e).__name__}'

    if name.endswith(('.png', '.jpg', '.jpeg', '.tif', '.tiff')):
        return '', 'image', 'an image needs the vision model path'

    return '', 'unknown', f'unsupported file type: {name.rsplit(".", 1)[-1] if "." in name else name}'


def guess_header(text: str) -> dict:
    """Invoice number, date and total, straight off the text. No model, no guessing."""
    out = {'invoice_number': '', 'invoice_date': '', 'total': None}
    m = INVOICE_NO.search(text or '')
    if m:
        out['invoice_number'] = m.group(1).strip()[:80]
    for pat, fmt in DATE_PATTERNS:
        m = pat.search(text or '')
        if m:
            out['invoice_date'] = fmt(m)
            break
    # ONLY a figure on a line that says it is the total. None when the document
    # does not label one — see total_from_labelled_line for why "the largest
    # figure on the page" was wrong.
    out['total'] = total_from_labelled_line(text)
    return out


STRUCTURE_PROMPT = """You convert an already-extracted legal invoice into structured lines.
You are NOT reading an image and you must NOT invent anything: every figure you output has
to appear in the text given to you.

Return STRICT JSON only:
{"lines":[{"service_date":"YYYY-MM-DD or null","matter_ref":"","member_ref":"",
"matter_type":"divorce|conveyancing|criminal|labour|debt|estate|civil|contract|road|tenancy|advice|other",
"fee_earner":"","basis":"hourly|fixed|disb|other","units":null,"rate":null,"amount":0,
"matter_description":""}],"notes":"anything unclear"}

Rules:
- amount is required and must be a number that appears in the text.
- units x rate should equal amount where all three are present; if they do not, keep what
  the document says and mention it in notes. Do NOT silently correct it.
- matter_type: infer from the wording (divorce/custody/maintenance -> divorce; transfer/
  deed/bond -> conveyancing; bail/charge/plea -> criminal). Use "other" when unsure.
- Never output a person's name in member_ref — use the scheme/member code only."""


def structure_lines(text: str, feature='bonu_invoice_ingest'):
    """Text -> draft lines. Local Ollama first (free), then the cheap cloud tier.

    Returns (lines, notes, engine_error). Every amount is checked back against the text;
    a line whose figure is not in the document is dropped, because a payable built on a
    hallucinated number is worse than no automation at all.
    """
    from core.ai_assist import reasoning_complete
    if not (text or '').strip():
        return [], 'nothing to structure', None

    body = text[:14000]
    try:
        raw = reasoning_complete(STRUCTURE_PROMPT + '\n\nINVOICE TEXT:\n' + body, feature=feature)
    except BaseException as exc:      # noqa: BLE001
        return [], '', f'{type(exc).__name__}: {exc}'[:160]

    t = (raw or '').strip()
    if t.startswith('```'):
        t = t.strip('`')
        t = t.split('\n', 1)[1] if '\n' in t else t
        t = t.rsplit('```', 1)[0]
    try:
        data = json.loads(t[t.find('{'):t.rfind('}') + 1])
    except BaseException:             # noqa: BLE001
        return [], 'the model did not return usable JSON', 'unparseable'

    # Ground every amount in the document text.
    present = {str(_money(x)) for x in MONEY.findall(body)}
    present |= {str(_money(x)).rstrip('0').rstrip('.') for x in MONEY.findall(body)}
    kept, dropped = [], 0
    for l in (data.get('lines') or []):
        amt = _money(l.get('amount'))
        if amt is None:
            dropped += 1
            continue
        if str(amt) not in present and str(amt).rstrip('0').rstrip('.') not in present:
            dropped += 1
            continue
        kept.append(l)
    notes = (data.get('notes') or '')
    if dropped:
        notes = (notes + ' | ' if notes else '') + (
            f'{dropped} line(s) were dropped because the amount could not be found in the '
            f'document — nothing invented.')
    return kept, notes, None


def parse_invoice(filename: str, blob: bytes):
    """The whole ladder, in one call. Returns a DRAFT for a human to confirm."""
    text, method, err = extract_text(filename, blob)
    result = {
        'filename': filename,
        'extraction_method': method,
        'extraction_error': err,
        'needs_vision_model': method in ('pdf-scanned', 'image'),
        'header': guess_header(text) if text else {},
        'lines': [],
        'notes': '',
        'ai_error': None,
        'text_preview': (text or '')[:1200],
        'confirm_required': True,      # nothing is ever saved from here automatically
    }
    if not text:
        return result
    lines, notes, ai_err = structure_lines(text)
    result['lines'] = lines
    result['notes'] = notes
    result['ai_error'] = ai_err
    lt = sum(float(_money(l.get('amount')) or 0) for l in lines)
    result['lines_total'] = round(lt, 2)
    hdr_total = (result['header'] or {}).get('total')
    if hdr_total:
        result['foots'] = abs(lt - float(hdr_total)) <= 0.05
        result['header_total'] = hdr_total
    return result
