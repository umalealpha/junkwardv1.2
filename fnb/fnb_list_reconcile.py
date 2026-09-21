"""fnb/fnb_list_reconcile.py — reconcile Omni's open payment queue against the FNB
"Batch Payments" authorisation list the CFO downloads (CFO directive 2026-09-06).

The rule (the CFO's, stated plainly): the FNB list is everything still waiting for
his signature in the bank. So anything in Omni's queue that is NOT on that list has
already been dealt with, and must drop out of the queue.

This module is the shared engine behind the "upload FNB list" button and is kept
free of Django DB imports so the matching is unit-testable. It reuses the same
reference/amount matchers the FNB email auto-close uses, so a payment is matched to
a list entry exactly the way the rest of the system matches.

Safety: `parse_fnb_pending_list` RAISES if it cannot read any entries — the caller
must never treat an unreadable/short list as "nothing is pending" (that would close
the whole queue). The web flow always previews (how many would close) before it
applies, so a mis-uploaded file is caught by eye.
"""
from __future__ import annotations

import io
import re

from .email_reconcile import _reference_overlaps, _batch_matches, _to_amount

# One export row, as pdfplumber extracts it (verified on the real FNB Batch Payments
# PDF, 2026-09-06): "<name> <date> <amount> <status>", all on one line, name first.
_ROW = re.compile(r'^(?P<name>.+?)\s+\d{4}/\d{2}/\d{2}\s+(?P<amt>[\d,]+\.\d{2})\s+[A-Za-z].*$')
# A line that carries BOTH a date and an amount looks like a payment row. If it
# does not match _ROW we must NOT silently skip it (a skipped row = a wrongly
# closed request) — we raise instead (Fable #7, 2026-09-06).
_DATE_AND_AMT = re.compile(r'\d{4}/\d{2}/\d{2}.*?[\d,]+\.\d{2}')
_HEADERS = {'name details total status', 'api batches', 'batch payments',
            'past batches', "today's batches", 'same day', 'name', 'details',
            'total', 'status'}
# The unique Omni sequence tail an Omni-loaded batch carries in FNB, e.g.
# old format: "000064 (O)" / "000064-2 (O)"
# new format: "64 (UNI)" / "64-2 (UNI)"
# Both yield the base number as a string for matching.
_ONUM_LEGACY = re.compile(r'(\d{1,6})(?:-\d+)?\s*\([A-Z]{1,4}\)')
_ONUM_NEW = re.compile(r'PAY-(\d+)(?:-\d+)?\s*\([A-Z]{1,4}\)')


def _onum(text: str) -> str | None:
    t = text or ''
    m = _ONUM_NEW.search(t) or _ONUM_LEGACY.search(t)
    if not m:
        return None
    return m.group(1).lstrip('0') or '0'


def parse_fnb_pending_list(pdf_bytes: bytes) -> list[dict]:
    """Read the FNB 'Batch Payments' PDF into [{name, amount, onum}] entries.

    Layout (verified on real exports via pdfplumber): each row is a single line,
        <Name ... 000NNN (O)> <date> <amount> <Status>
    name first. Raises ValueError if nothing parses — never return an empty list
    silently (the caller would then close everything).
    """
    import pdfplumber

    text_parts: list[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text_parts.append(page.extract_text() or '')
    full = '\n'.join(text_parts)
    if not full.strip():
        raise ValueError('That file has no readable text — is it the FNB Batch '
                         'Payments PDF?')
    return _entries_from_text(full)


def _entries_from_text(full: str) -> list[dict]:
    """The pure line-parser behind parse_fnb_pending_list (no PDF lib) — kept
    separate so it is unit-testable against the exact FNB export text. Each row is
    "<name> <date> <amount> <status>"; the name is everything before the date.
    Raises ValueError if nothing parses (never return an empty list silently)."""
    entries: list[dict] = []
    unparsed = 0
    for ln in (full or '').splitlines():
        ln = ln.strip()
        if not ln or ln.lower() in _HEADERS:
            continue
        m = _ROW.match(ln)
        if not m:
            # A line that looks like a payment row (has a date AND an amount) but
            # didn't parse means the format shifted — count it so we can refuse
            # rather than drop it (and wrongly close that request).
            if _DATE_AND_AMT.search(ln):
                unparsed += 1
            continue
        name = m.group('name').strip()
        if not name or name.lower() in _HEADERS:
            continue
        entries.append({'name': name, 'amount': m.group('amt').replace(',', ''),
                        'onum': _onum(name)})

    if unparsed:
        raise ValueError(f'{unparsed} line(s) in that PDF look like payments but could '
                         f'not be read. The FNB layout may have changed — do not close '
                         f'from this file; send it to IT to check.')
    if not entries:
        raise ValueError('Could not read any payments from that PDF. Please upload '
                         'the FNB "Batch Payments" list (the same export as before).')
    return entries


def request_in_list(request_md: dict, entries: list[dict]) -> bool:
    """True if this open payment request is still on the FNB list (so it stays open).

    Two ways it can match, mirroring how the rest of the system matches:
      1. Omni-loaded batch — its unique Omni sequence tail (000NNN) equals a list
         entry's (O) number. Robust to FNB truncating the payee name.
      2. Any batch — exact amount AND the entry name overlaps this request's
         reference / payee / narration (never amount alone).
    """
    md_onum = _onum(request_md.get('batch_key') or '')
    total = _to_amount(request_md.get('total'))
    for e in entries:
        name = e.get('name', '')
        if md_onum and e.get('onum') and md_onum == e['onum']:
            return True
        # An older/other batch id (e.g. an ALPHA-EFT key) printed in the FNB name.
        if _batch_matches(name, request_md):
            return True
        if total is not None and _to_amount(e.get('amount')) == total \
                and _reference_overlaps(name, request_md):
            return True
    return False
