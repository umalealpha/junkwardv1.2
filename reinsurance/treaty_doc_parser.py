"""
reinsurance/treaty_doc_parser.py — Drop a PDF / Word / Excel reinsurance
treaty document, get a structured list of treaties back.

Two-step UX:

    POST /api/v1/reinsurance/treaties/upload-parse/   multipart, file=...
        ↓
    {  "candidates": [
            { "treaty_number": "QS-MOTOR-FY26",
              "description":   "Motor QS — Africa Re",
              "reinsurer_code":"AFRICA_RE",
              "reinsurer_name":"Africa Reinsurance Corp.",
              "treaty_type":   "quota_share",
              "line_of_business":"Motor",
              "inception_date": "2025-07-01",
              "expiry_date":    "2026-06-30",
              "cession_share_percent": "65.0",
              "commission_percent":    "25.0",
              "retention_amount": null,
              "limit_amount":     null,
              "currency_code": "BWP",
              "notes": "Sliding scale commission 22.5%-27.5%",
              "confidence": 0.92,
              "warnings": ["Sliding-scale commission not modelled in v1"]
            },
            ...
        ],
        "raw_text_preview": "...",
        "doc_kind": "pdf",
        "deepseek_used": true
    }
        ↓ CFO eyeballs, edits in modal, then …
    POST /api/v1/reinsurance/treaties/upload-commit/  json
        { "candidates": [ ...edited candidates... ] }
        ↓
    Creates ReinsuranceTreaty rows, returns the saved list with their ids.

Reinsurer matching:
  - reinsurer_code first (exact, case-insensitive) — the same `short_code`
    column used everywhere else.
  - Fall back to reinsurer_name fuzzy (icontains) if code missed.
  - If neither hits, the candidate is returned with reinsurer=null and
    needs_reinsurer=True — the UI surfaces a dropdown so the CFO picks
    from existing reinsurers (we deliberately don't auto-create
    counterparties — that's a control point).

DeepSeek failure modes (no key, timeout, non-JSON):
  We still return the raw extracted text and a coarse regex fall-back
  attempt so the operator can fix it manually. The flag `deepseek_used`
  in the response tells the UI which mode produced the candidates.
"""
from __future__ import annotations

import io
import json
import logging
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from core.ai_assist import DeepSeekUnavailable, deepseek_complete

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------
def extract_text(filename: str, file_bytes: bytes) -> tuple[str, str]:
    """Extract plain text from an uploaded document.

    Returns (text, kind). kind ∈ {'pdf', 'docx', 'xlsx', 'csv', 'txt', 'unknown'}.
    """
    name = (filename or '').lower()
    if name.endswith('.pdf'):
        return _extract_pdf(file_bytes), 'pdf'
    if name.endswith('.docx'):
        return _extract_docx(file_bytes), 'docx'
    if name.endswith('.xlsx') or name.endswith('.xls'):
        return _extract_xlsx(file_bytes), 'xlsx'
    if name.endswith('.csv'):
        return file_bytes.decode('utf-8', errors='replace'), 'csv'
    # Fallback: try plain text decode
    try:
        return file_bytes.decode('utf-8'), 'txt'
    except UnicodeDecodeError:
        return '', 'unknown'


def _extract_pdf(b: bytes) -> str:
    try:
        import pdfplumber  # noqa: WPS433
    except ImportError:
        log.warning('pdfplumber not installed; cannot extract PDF.')
        return ''
    text_parts: list[str] = []
    with pdfplumber.open(io.BytesIO(b)) as pdf:
        for page in pdf.pages:
            t = page.extract_text() or ''
            if t.strip():
                text_parts.append(t)
    return '\n\n'.join(text_parts)


def _extract_docx(b: bytes) -> str:
    try:
        import docx  # noqa: WPS433  # python-docx
    except ImportError:
        log.warning('python-docx not installed; cannot extract DOCX.')
        return ''
    doc = docx.Document(io.BytesIO(b))
    parts: list[str] = []
    for para in doc.paragraphs:
        if para.text.strip():
            parts.append(para.text)
    # Pull table content too — treaty docs often have term tables
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                parts.append(' | '.join(cells))
    return '\n'.join(parts)


def _extract_xlsx(b: bytes) -> str:
    try:
        import openpyxl  # noqa: WPS433
    except ImportError:
        return ''
    wb = openpyxl.load_workbook(io.BytesIO(b), data_only=True, read_only=True)
    parts: list[str] = []
    for sheet in wb.worksheets:
        parts.append(f'### Sheet: {sheet.title}')
        for row in sheet.iter_rows(values_only=True):
            cells = ['' if v is None else str(v) for v in row]
            if any(cells):
                parts.append('\t'.join(cells))
    return '\n'.join(parts)


# ---------------------------------------------------------------------------
# AI extraction
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are an insurance treaty extraction assistant for
Alpha Direct Insurance. The user will paste the text of a reinsurance
treaty document (PDF, Word, or Excel exported as text). Your job is to
identify EVERY distinct treaty in the document and return them as a JSON
object with this exact shape:

{
  "candidates": [
    {
      "treaty_number": "<short identifier, e.g. QS-MOTOR-FY26>",
      "description":   "<one-line description>",
      "reinsurer_code":"<short code if visible, else null>",
      "reinsurer_name":"<full legal name>",
      "treaty_type":   "<quota_share|surplus|xl|stop_loss|facultative>",
      "line_of_business":"<Motor|Property|Liability|Marine|All Lines|...>",
      "inception_date":"YYYY-MM-DD",
      "expiry_date":   "YYYY-MM-DD",
      "cession_share_percent": "<number 0-100 or null>",
      "commission_percent":    "<number 0-100 or null>",
      "retention_amount":      "<number or null>",
      "limit_amount":          "<number or null>",
      "currency_code":         "<ISO 4217 e.g. BWP, USD, ZAR>",
      "notes":                 "<free-text notes, including sliding-scale,
                                event limits, special exclusions>",
      "confidence": <float 0-1>,
      "warnings":  ["<short string per anomaly>"]
    }
  ]
}

RULES:
- treaty_type must be exactly one of: quota_share, surplus, xl,
  stop_loss, facultative. Map common terms: "QS" → quota_share,
  "S/L" → stop_loss, "XL" or "XoL" → xl, "Fac" → facultative.
- Dates must be ISO YYYY-MM-DD. If only a year/month is visible, use
  the first of that month and add a warning.
- Percentages are numbers, not strings with %.
- Numeric monetary fields are bare numbers (no commas, no currency).
- If a field is genuinely missing, return null — never invent.
- Add a warning like "no inception date in source" so the human knows.
- confidence reflects how clearly the source supports the extracted
  fields. 1.0 = explicit table; 0.5 = inferred from prose; 0.2 = barely
  supported.
- Return AT LEAST one candidate. If the doc lists 12 treaties, return 12.
- Reply with valid JSON ONLY — no surrounding prose.
"""

# Cap input length so DeepSeek doesn't reject the request. Big PDFs are
# trimmed; the warning surfaces in the response.
MAX_INPUT_CHARS = 60_000


def parse_with_ai(text: str) -> tuple[list[dict], bool, str]:
    """Send the extracted text to DeepSeek and parse the JSON reply.

    Returns (candidates, deepseek_used, raw_reply).
    """
    if not text or not text.strip():
        return [], False, ''

    if len(text) > MAX_INPUT_CHARS:
        text = text[:MAX_INPUT_CHARS] + '\n\n[... TRUNCATED FOR AI INPUT ...]'

    try:
        reply = deepseek_complete(
            user_prompt=text,
            system_prompt=SYSTEM_PROMPT,
            response_format='json_object',
            timeout=45.0,
        )
    except DeepSeekUnavailable as e:
        log.warning('DeepSeek unavailable for treaty parse: %s', e)
        return _regex_fallback(text), False, ''
    except Exception as e:  # noqa: BLE001
        log.exception('DeepSeek call failed for treaty parse')
        return _regex_fallback(text), False, ''

    try:
        data = json.loads(reply)
    except json.JSONDecodeError:
        # Try to dig the first JSON object out of the reply
        m = re.search(r'\{.*\}', reply, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                return _regex_fallback(text), False, reply
        else:
            return _regex_fallback(text), False, reply

    candidates = data.get('candidates') or []
    if not isinstance(candidates, list):
        return _regex_fallback(text), False, reply

    cleaned = [_clean_candidate(c) for c in candidates if isinstance(c, dict)]
    return cleaned, True, reply


def _clean_candidate(c: dict) -> dict:
    """Tidy AI output — coerce numerics, normalise enum values."""
    out = dict(c)

    # treaty_type normalisation
    tt = (out.get('treaty_type') or '').lower().strip()
    aliases = {
        'qs': 'quota_share', 'quota share': 'quota_share',
        'surplus_treaty': 'surplus',
        'xol': 'xl', 'excess of loss': 'xl',
        's/l': 'stop_loss', 'stop loss': 'stop_loss',
        'fac': 'facultative',
    }
    out['treaty_type'] = aliases.get(tt, tt) or None

    # currency_code uppercase
    if out.get('currency_code'):
        out['currency_code'] = str(out['currency_code']).upper().strip()

    # Numeric coercion
    for field in ('cession_share_percent', 'commission_percent',
                  'retention_amount', 'limit_amount'):
        v = out.get(field)
        if v in (None, '', 'null'):
            out[field] = None
            continue
        try:
            out[field] = str(Decimal(str(v).replace(',', '').strip()))
        except (InvalidOperation, AttributeError):
            out[field] = None

    # Date normalisation
    for field in ('inception_date', 'expiry_date'):
        v = out.get(field)
        if not v:
            out[field] = None
            continue
        d = _parse_date(v)
        out[field] = d.isoformat() if d else None

    # Confidence default
    try:
        out['confidence'] = float(out.get('confidence') or 0.5)
    except (TypeError, ValueError):
        out['confidence'] = 0.5

    # warnings as list of strings
    w = out.get('warnings')
    if not isinstance(w, list):
        out['warnings'] = []
    else:
        out['warnings'] = [str(x) for x in w]
    return out


def _parse_date(v: Any) -> Optional[date]:
    if isinstance(v, date):
        return v
    if isinstance(v, datetime):
        return v.date()
    s = str(v).strip().split('T', 1)[0]
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%Y/%m/%d', '%d %b %Y',
                '%d %B %Y', '%b %Y', '%B %Y'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Regex fallback (no AI / AI failed)
# ---------------------------------------------------------------------------
def _regex_fallback(text: str) -> list[dict]:
    """Crude heuristic: pull treaty-number-looking strings + dates.
    Always low-confidence; the UI surfaces a warning."""
    out: list[dict] = []

    # Look for lines like "Treaty No: QS-MOTOR-FY26" or "Treaty Number QS..."
    pat_num = re.compile(
        r'treaty(?:\s+(?:no|number))?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-_/]{2,30})',
        re.IGNORECASE,
    )
    nums = list({m.group(1).strip() for m in pat_num.finditer(text)})

    for n in nums[:10]:
        out.append({
            'treaty_number': n,
            'description': '',
            'reinsurer_code': None,
            'reinsurer_name': None,
            'treaty_type': None,
            'line_of_business': '',
            'inception_date': None,
            'expiry_date': None,
            'cession_share_percent': None,
            'commission_percent': None,
            'retention_amount': None,
            'limit_amount': None,
            'currency_code': 'BWP',
            'notes': 'Extracted by regex fallback — AI parse unavailable.',
            'confidence': 0.2,
            'warnings': ['No AI available; many fields blank.'],
        })
    return out
