"""Graphite Aware — server-side extraction of uploaded files (PDF/Excel/Word/
images) via the local doc_parse cascade. No external API; PII stays on the box.

parse() (core.doc_parse) does: born-digital PDF, Excel (calamine), CSV, DOCX,
and image OCR (RapidOCR, per-line confidence). Scanned PDFs come back empty
with escalate=True — we surface that as "couldn't read, send a clearer copy",
never silently pass.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

from core.doc_parse import parse

# Policy-number shapes seen in Graphite: DOMG/COMG/MIS/ATC + digits, POL refs,
# claim numbers G<year><seq>, and bare 6-12 digit runs.
_POL_RE = re.compile(
    r'\b(?:(?:DOMG|COMG|MIS|ATC)\d{4,}|G20\d{6,}|POL[-/ ]?\d{4,}|\d{6,12})\b', re.I)

# Botswana Omang = 9 digits. Passport = 1-2 letters + 6-8 digits (loose).
_OMANG_RE = re.compile(r'\b(\d{9})\b')
_PASSPORT_RE = re.compile(r'\b([A-Z]{1,2}\d{6,8})\b')

MAX_FILES = 5
MAX_BYTES = 10 * 1024 * 1024  # 10 MB / file


def extract_upload(file_bytes: bytes, filename: str, mime: str = '') -> Dict[str, Any]:
    """Local text extract. Returns {filename, text, confidence, tier, escalate,
    reasons}. Never calls an LLM."""
    try:
        r = parse(file_bytes=file_bytes, mime=mime, filename=filename, doc_type=None)
        text = (getattr(r, 'text', '') or getattr(r, 'markdown', '') or '')
        return {
            'filename': filename,
            'text': text,
            'confidence': getattr(r, 'confidence', 0.0),
            'tier': getattr(r, 'tier_used', ''),
            'escalate': getattr(r, 'escalate', False),
            'reasons': getattr(r, 'reasons', []),
        }
    except Exception as e:  # noqa: BLE001
        return {'filename': filename, 'text': '', 'confidence': 0.0,
                'tier': 'error', 'escalate': True, 'reasons': [f'parse_failed: {e}']}


def detect_policy_numbers(*texts: str) -> List[str]:
    seen: set = set()
    out: List[str] = []
    for t in texts:
        for m in _POL_RE.findall(t or ''):
            k = m.strip().upper()
            if k not in seen:
                seen.add(k)
                out.append(k)
    return out


def detect_id_numbers(text: str) -> Dict[str, List[str]]:
    """Pull candidate Omang (9-digit) + passport numbers from OCR'd ID text.
    Values are used ONLY for the server-side match — never surfaced or sent to
    the LLM."""
    omang = list(dict.fromkeys(_OMANG_RE.findall(text or '')))
    passport = list(dict.fromkeys(_PASSPORT_RE.findall((text or '').upper())))
    return {'omang': omang, 'passport': passport}
