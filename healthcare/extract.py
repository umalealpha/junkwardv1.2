"""Text extraction + DeepSeek life-detection for the health quick-quote.

Inputs supported: PDF, PNG/JPG (only if text layer / OCR is available
in the image), XLSX/CSV, DOCX, plain text.
"""
from __future__ import annotations

import io
import json
import logging
import re
from datetime import date
from decimal import Decimal

from django.conf import settings

from core.ai_assist import deepseek_complete, DeepSeekUnavailable
from django.utils import timezone

log = logging.getLogger(__name__)


# --- 1. Text extraction ------------------------------------------------------

def extract_text(file_bytes: bytes, mime: str, filename: str = '') -> str:
    """Best-effort text extraction. Returns plain string."""
    mime = (mime or '').lower()
    name = (filename or '').lower()
    if 'pdf' in mime or name.endswith('.pdf'):
        return _extract_pdf(file_bytes)
    if name.endswith(('.xlsx', '.xlsm', '.xlsb', '.xls', '.ods')) or 'spreadsheetml' in mime:
        return _extract_xlsx(file_bytes, filename)
    if name.endswith('.csv') or mime == 'text/csv':
        return file_bytes.decode('utf-8', errors='replace')
    if name.endswith('.docx') or 'wordprocessingml' in mime:
        return _extract_docx(file_bytes)
    if mime.startswith('image/') or name.endswith(('.png', '.jpg', '.jpeg')):
        return _extract_image(file_bytes)
    # Fallback: treat as text
    try:
        return file_bytes.decode('utf-8', errors='replace')
    except Exception:                                # noqa: BLE001
        return ''


def _extract_pdf(b: bytes) -> str:
    try:
        import pdfplumber
        out = []
        with pdfplumber.open(io.BytesIO(b)) as pdf:
            for page in pdf.pages[:30]:              # cap 30 pages
                out.append(page.extract_text() or '')
        return '\n'.join(out)
    except Exception as e:                           # noqa: BLE001
        log.warning('pdfplumber failed: %s', e)
        return ''


def _extract_xlsx(b: bytes, filename: str = '') -> str:
    # Local-first: python-calamine (xlsx/xlsb/xls/ods, one fast path) when
    # available; falls back to openpyxl so prod can't regress before the dep
    # ships. CFO 2026-06-21 local-first parsing.
    try:
        from core.doc_parse import xlsx_md
        if xlsx_md.is_available():
            return xlsx_md.xlsx_to_markdown(b, filename)['markdown'][:32000]
    except Exception as e:                           # noqa: BLE001
        log.warning('calamine path unavailable, falling back to openpyxl: %s', e)
    try:
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(b), read_only=True, data_only=True)
        rows = []
        for ws in wb.worksheets:
            rows.append(f'### sheet: {ws.title}')
            for r in ws.iter_rows(values_only=True):
                vals = [str(c) if c is not None else '' for c in r]
                rows.append('\t'.join(vals))
        return '\n'.join(rows)[:32000]
    except Exception as e:                           # noqa: BLE001
        log.warning('openpyxl failed: %s', e)
        return ''


def _extract_docx(b: bytes) -> str:
    try:
        from docx import Document
        doc = Document(io.BytesIO(b))
        return '\n'.join(p.text for p in doc.paragraphs if p.text)
    except Exception as e:                           # noqa: BLE001
        log.warning('python-docx failed: %s', e)
        return ''


def _extract_image(b: bytes) -> str:
    """Local-first OCR: RapidOCR (ONNX, offline, CoreML on M4 / CPU on EC2,
    per-line confidence) when available; falls back to pytesseract. CFO
    2026-06-21 — this path returned '' before, so scanned vendor docs went
    straight to the cloud; now they're OCR'd locally first."""
    try:
        from core.doc_parse import ocr
        if ocr.is_available():
            res = ocr.ocr_image(b)
            if res['ok'] and res['text'].strip():
                return res['text']
    except Exception as e:                           # noqa: BLE001
        log.warning('RapidOCR path unavailable, falling back to tesseract: %s', e)
    try:
        import pytesseract
        from PIL import Image
        img = Image.open(io.BytesIO(b))
        return pytesseract.image_to_string(img, lang='eng')
    except Exception as e:                           # noqa: BLE001
        log.warning('image OCR unavailable: %s', e)
        return ''


# --- 2. DeepSeek life extraction --------------------------------------------

LIFE_EXTRACTION_SYSTEM = (
    "You are a life-extraction service for an insurance ERP. You will be "
    "given OCR'd or parsed text from a document. Identify every distinct "
    "human life mentioned, with their date of birth or age, gender, and "
    "implied role (main member, spouse / adult dependant, or child "
    "dependant). NEVER fabricate a life or a DOB. If a field is uncertain, "
    "return null and lower confidence. "
    'Return STRICT JSON shape: {"lives": [ {"first_name":string|null, '
    '"last_name":string|null, "dob_iso":string|null, '
    '"age_years":integer|null, "gender":"M"|"F"|"U", '
    '"life_category":"MAIN"|"ADULT_DEP"|"CHILD_DEP", '
    '"confidence":number} ] }. If no lives identified, return '
    '{"lives": []}.'
)


def extract_lives(text: str) -> list[dict]:
    """Call DeepSeek to identify lives in free-form text."""
    text = (text or '').strip()
    if not text:
        return []
    if len(text) > 24000:
        text = text[:24000] + '\n…[truncated for prompt budget]'

    user_msg = (
        "DOCUMENT TEXT:\n---\n" + text + "\n---\n"
        "Return the JSON array now. No prose."
    )

    try:
        raw = deepseek_complete(
            user_prompt   = user_msg,
            system_prompt = LIFE_EXTRACTION_SYSTEM,
            response_format = 'json_object',
            timeout       = 45.0,
        )
    except DeepSeekUnavailable as e:
        log.warning('DeepSeek not configured: %s', e)
        return _regex_fallback(text)
    except Exception as e:                           # noqa: BLE001
        log.warning('DeepSeek call failed: %s', e)
        return _regex_fallback(text)

    return _parse_lives_json(raw)


def _parse_lives_json(raw: str) -> list[dict]:
    if not raw:
        return []
    s = raw.strip()
    if s.startswith('```'):
        s = re.sub(r'^```[a-zA-Z]*', '', s).rstrip('`').strip()
    # Try full-object parse first (json_object mode returns an object)
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            lives = obj.get('lives')
            if isinstance(lives, list):
                return lives
            return []
        if isinstance(obj, list):
            return obj
    except Exception:                                # noqa: BLE001
        pass
    # Fallback: pull the first [...] block out of any prose
    m = re.search(r'\[.*\]', s, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, list) else []
    except Exception:                                # noqa: BLE001
        return []


def _regex_fallback(text: str) -> list[dict]:
    """Naive regex fallback when DeepSeek is unavailable.

    Picks up patterns like 'age 38' or 'DOB 1988-04-12' and emits a single
    MAIN life so the rest of the pipeline still produces a quote.
    """
    lives: list[dict] = []
    m_age = re.search(r'age[^\d]{0,5}(\d{1,2})', text, re.I)
    m_dob = re.search(r'(?:dob|date of birth)[^\d]{0,5}'
                      r'(\d{4}-\d{2}-\d{2})', text, re.I)
    m_gender = re.search(r'\b(male|female|man|woman|M|F)\b', text, re.I)
    g = 'U'
    if m_gender:
        v = m_gender.group(1).upper()
        g = 'F' if v.startswith(('F', 'WOM')) else ('M' if v in ('M', 'MAN', 'MALE') else 'U')

    if m_age or m_dob:
        lives.append({
            'first_name': None, 'last_name': None,
            'dob_iso':     m_dob.group(1) if m_dob else None,
            'age_years':   int(m_age.group(1)) if m_age else None,
            'gender':      g,
            'life_category': 'MAIN',
            'confidence':  0.5,
        })
    return lives


# --- 3. Life normalisation ---------------------------------------------------

def normalise_lives(lives: list[dict], quote_date: date | None = None) -> list[dict]:
    """Compute age from dob if missing; clamp life_category; coerce types."""
    qd = quote_date or timezone.localdate()
    out = []
    for raw in lives or []:
        if not isinstance(raw, dict):
            continue
        age = raw.get('age_years')
        dob = raw.get('dob_iso')
        if age is None and dob:
            try:
                dob_d = date.fromisoformat(dob[:10])
                age = (qd - dob_d).days // 365
            except Exception:                        # noqa: BLE001
                age = None
        if age is None:
            continue                                  # cannot price without age
        try:
            age = int(age)
        except Exception:                            # noqa: BLE001
            continue
        if age < 0 or age > 120:
            continue
        gender = (raw.get('gender') or 'U')[:1].upper()
        if gender not in ('M', 'F', 'U'):
            gender = 'U'
        lc = (raw.get('life_category') or '').upper()
        if lc not in ('MAIN', 'ADULT_DEP', 'CHILD_DEP'):
            lc = 'CHILD_DEP' if age < 18 else 'MAIN'
        out.append({
            'first_name':     raw.get('first_name'),
            'last_name':      raw.get('last_name'),
            'dob':            dob[:10] if isinstance(dob, str) else None,
            'age_years':      age,
            'gender':         gender,
            'life_category':  lc,
            'extraction_confidence': float(raw.get('confidence') or 0.0),
        })
    return out
