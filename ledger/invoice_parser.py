"""
ledger/invoice_parser.py — PDF invoice → balanced JE proposal.

Lifted from the ADSA pipeline (invoice_parser.py) on 2026-05-18.

Smart Entry already accepts PDF uploads and runs a generic AI extract.
This module sits ALONGSIDE that path and handles the easy case — clean,
computer-generated supplier PDFs where regex + vendor-keyword routing
is faster, deterministic, and free.

The two paths complement each other:
  * Regex parser (this file): HIGH confidence on known templates.
  * AI extract: handles scans, photos, and odd layouts.

The Smart Entry view tries the regex parser first; if confidence drops
below MEDIUM the AI takes over.

DEPENDENCIES
------------
pdfplumber is the preferred extractor (better at layout-preserving text);
pypdf is the fallback when pdfplumber isn't installed. Both are pure
Python so they ship with the rest of the backend container.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional


# ── Vendor → expense-account routing ──────────────────────────────────
# Same shape as fnb.autocode.AutocodeRule — first match wins.
VENDOR_RULES: tuple[tuple[str, str], ...] = (
    ('TELKOM',        '630010'),   # comms
    ('MTN',           '630010'),
    ('CELL C',        '630010'),
    ('VODACOM',       '630010'),
    ('ESKOM',         '630020'),   # utilities
    ('CITY OF',       '630020'),
    ('JOHANNESBURG',  '630020'),
    ('GABORONE',      '630020'),
    ('UBER',          '600002'),   # transport
    ('BOLT',          '600002'),
    ('PWC',           '660010'),   # professional fees
    ('DELOITTE',      '660010'),
    ('KPMG',          '660010'),
    ('EY',            '660010'),
    ('AON',           '300100'),   # reinsurance brokers
    ('MARSH',         '300100'),
    ('OFFICE',        '620020'),   # generic office expenses
    ('AMAZON',        '620020'),
    ('GOOGLE',        '630030'),   # IT subscriptions
    ('MICROSOFT',     '630030'),
    ('AWS',           '630030'),
    ('CLAUDE',        '630030'),
    ('OPENAI',        '630030'),
)

DEFAULT_EXPENSE_ACCOUNT = '620030'   # General Expenses (sundry)
VAT_INPUT_ACCOUNT       = '132000'
TRADE_PAYABLES_ACCOUNT  = '211000'


@dataclass
class ParsedInvoice:
    vendor:           str
    invoice_number:   str
    invoice_date:     str          # YYYY-MM-DD; '' if unparsable
    subtotal:         Decimal
    vat_amount:       Decimal
    total:            Decimal
    suggested_expense: str         # GL account code
    confidence:       str          # HIGH | MEDIUM | LOW
    raw_text:         str

    def to_balanced_je(self) -> list[dict]:
        """Return three JE lines (Dr Expense, Dr VAT Input, Cr Payables)."""
        return [
            {'account_code': self.suggested_expense,
             'debit':  float(self.subtotal),  'credit': 0.0,
             'description': f'{self.vendor} — {self.invoice_number}'},
            {'account_code': VAT_INPUT_ACCOUNT,
             'debit':  float(self.vat_amount), 'credit': 0.0,
             'description': f'VAT input — {self.invoice_number}'},
            {'account_code': TRADE_PAYABLES_ACCOUNT,
             'debit':  0.0, 'credit': float(self.total),
             'description': f'Trade payable — {self.vendor} {self.invoice_number}'},
        ]


# ── Extractors ────────────────────────────────────────────────────────

def _pdf_text(pdf_bytes: bytes) -> str:
    """Best-effort full-text extract. Tries pdfplumber, falls back to pypdf."""
    try:
        import pdfplumber                                      # type: ignore
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            return '\n'.join((p.extract_text() or '') for p in pdf.pages)
    except Exception:
        pass
    try:
        from pypdf import PdfReader                            # type: ignore
        return '\n'.join((p.extract_text() or '') for p in PdfReader(io.BytesIO(pdf_bytes)).pages)
    except Exception:
        return ''


# Regex patterns — every one returns a single capturing group.
_INV_NUMBER_RX = re.compile(r'(?i)(?:invoice|inv|tax\s+invoice)\s*(?:no\.?|#|number)\s*[:\-]?\s*([A-Z0-9\-/]+)')
_INV_DATE_RX   = re.compile(r'(?i)(?:invoice\s+date|date)\s*[:\-]?\s*([0-9]{1,2}[\-/\. ][0-9]{1,2}[\-/\. ][0-9]{2,4})')
_TOTAL_RX      = re.compile(r'(?i)(?:total\s*(?:amount\s+due|due|incl\.?\s*vat|inclusive)?|grand\s+total)\s*[:\-]?\s*R?\s*([0-9][0-9,\. ]*)')
_SUBTOTAL_RX   = re.compile(r'(?i)(?:sub\s*total|amount\s+excl\.?\s*vat)\s*[:\-]?\s*R?\s*([0-9][0-9,\. ]*)')
_VAT_RX        = re.compile(r'(?i)(?:vat|tax)\s*(?:@?\s*15%?)?\s*[:\-]?\s*R?\s*([0-9][0-9,\. ]*)')


def _money(s: str) -> Decimal:
    cleaned = (s or '').replace(',', '').replace(' ', '').strip()
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return Decimal('0')


def _vendor_from_top(text: str) -> str:
    """First non-empty, non-numeric line at the top of the PDF."""
    for line in text.splitlines()[:8]:
        line = line.strip()
        if not line: continue
        if line.lower().startswith(('invoice', 'tax invoice', 'date', 'vat', 'reg')): continue
        if re.fullmatch(r'[0-9\s./,-]+', line): continue
        return line[:80]
    return ''


def _normalise_date(raw: str) -> str:
    """Convert assorted formats to ISO. '12/05/2026' → '2026-05-12'."""
    m = re.match(r'(\d{1,2})[\-/\. ](\d{1,2})[\-/\. ](\d{2,4})', raw)
    if not m: return ''
    a, b, c = m.groups()
    year = int(c) if len(c) == 4 else 2000 + int(c)
    return f"{year:04d}-{int(b):02d}-{int(a):02d}"


def _route_vendor(vendor: str) -> tuple[str, str]:
    """(account_code, confidence) for vendor → expense GL routing."""
    upper = (vendor or '').upper()
    for kw, code in VENDOR_RULES:
        if kw in upper:
            return code, 'HIGH'
    return DEFAULT_EXPENSE_ACCOUNT, 'LOW'


def parse_invoice(pdf_bytes: bytes) -> Optional[ParsedInvoice]:
    """Return a ParsedInvoice or None if the PDF is unreadable."""
    text = _pdf_text(pdf_bytes)
    if not text.strip():
        return None

    vendor       = _vendor_from_top(text)
    inv_no_m     = _INV_NUMBER_RX.search(text)
    inv_dt_m     = _INV_DATE_RX.search(text)
    total_m      = _TOTAL_RX.search(text)
    subtotal_m   = _SUBTOTAL_RX.search(text)
    vat_m        = _VAT_RX.search(text)

    total    = _money(total_m.group(1))    if total_m    else Decimal('0')
    subtotal = _money(subtotal_m.group(1)) if subtotal_m else Decimal('0')
    vat      = _money(vat_m.group(1))      if vat_m      else Decimal('0')

    # If only one of {subtotal, total} parsed, derive the other via VAT.
    if total and not subtotal:
        subtotal = (total / Decimal('1.15')).quantize(Decimal('0.01'))
        if not vat: vat = total - subtotal
    if subtotal and not total:
        if not vat: vat = (subtotal * Decimal('0.15')).quantize(Decimal('0.01'))
        total = subtotal + vat

    account, vendor_conf = _route_vendor(vendor)
    if total == 0 or subtotal == 0:
        confidence = 'LOW'
    elif abs((subtotal + vat) - total) > Decimal('1'):
        confidence = 'MEDIUM'           # numbers don't tie; flag for review
    else:
        confidence = vendor_conf

    return ParsedInvoice(
        vendor           = vendor,
        invoice_number   = (inv_no_m.group(1) if inv_no_m else '').strip(),
        invoice_date     = _normalise_date(inv_dt_m.group(1)) if inv_dt_m else '',
        subtotal         = subtotal,
        vat_amount       = vat,
        total            = total,
        suggested_expense= account,
        confidence       = confidence,
        raw_text         = text,
    )
