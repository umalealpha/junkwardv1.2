"""Local-first document parse cascade.

    parse(file_bytes, mime, filename, doc_type) -> ParseResult

Tier 0  route by type — spreadsheet → python-calamine; born-digital PDF →
        pdfplumber; DOCX → python-docx; CSV/text → decode. Deterministic, no LLM.
Tier 1  image / scanned → RapidOCR (local, per-line confidence).
Gate    rule-based; sets ParseResult.escalate when the local result is weak.

The CALLER (e.g. healthcare.vendor_extract) escalates to a local LLM (Ollama)
then DeepSeek (text, after is_safe_for_ai redaction) then Gemini (vision) ONLY
when result.escalate is True — so clean docs never leave the box.

Django-free + import-guarded: every optional dep is probed, never hard-required.
"""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field

from . import gate as _gate

log = logging.getLogger(__name__)

# doc types whose spreadsheets are formula-heavy → force a LibreOffice recalc
_RECALC_DOC_TYPES = {'tb', 'gl', 'trial_balance', 'general_ledger', 'ma', 'management_pack'}
_SPREADSHEET_EXT = ('.xlsx', '.xlsm', '.xlsb', '.xls', '.ods')


@dataclass
class ParseResult:
    text: str = ''
    markdown: str = ''
    tables: list = field(default_factory=list)
    confidence: float = 0.0
    tier_used: str = ''
    signals: dict = field(default_factory=dict)
    escalate: bool = True
    reasons: list = field(default_factory=list)


def _route(mime: str, name: str) -> str:
    mime = (mime or '').lower(); name = (name or '').lower()
    if name.endswith(_SPREADSHEET_EXT) or 'spreadsheet' in mime or 'excel' in mime:
        return 'spreadsheet'
    if name.endswith('.csv') or mime == 'text/csv':
        return 'csv'
    if 'pdf' in mime or name.endswith('.pdf'):
        return 'pdf'
    if name.endswith('.docx') or 'wordprocessingml' in mime:
        return 'docx'
    if mime.startswith('image/') or name.endswith(('.png', '.jpg', '.jpeg', '.tiff', '.bmp', '.webp')):
        return 'image'
    return 'text'


def _born_digital_pdf(b: bytes) -> tuple[str, list]:
    try:
        import pdfplumber
        text, tables = [], []
        with pdfplumber.open(io.BytesIO(b)) as pdf:
            for page in pdf.pages[:30]:
                text.append(page.extract_text() or '')
                for t in (page.extract_tables() or []):
                    tables.append(t)
        return '\n'.join(text), tables
    except Exception as e:    # noqa: BLE001
        log.warning('pdfplumber failed: %s', e)
        return '', []


def parse(file_bytes: bytes, mime: str = '', filename: str = '',
          doc_type: str | None = None) -> ParseResult:
    route = _route(mime, filename)

    # --- Tier 0: spreadsheet (deterministic, local, no OCR/LLM) ---
    if route == 'spreadsheet':
        from . import xlsx_md
        if xlsx_md.is_available():
            recalc = (doc_type or '').lower() in _RECALC_DOC_TYPES
            r = xlsx_md.xlsx_to_markdown(file_bytes, filename, recalc=recalc)
            sig = dict(r['signals']); sig['expect_table'] = True
            sig['table_cells'] = sum(s['n_rows'] * s['n_cols'] for s in r['sheets'])
            g = _gate.assess(r['markdown'], signals=sig, doc_type=doc_type)
            return ParseResult(text=r['markdown'], markdown=r['markdown'], tables=r['sheets'],
                               confidence=g['confidence'], tier_used='t0:calamine',
                               signals=sig, escalate=g['escalate'], reasons=g['reasons'])

    # --- Tier 0: CSV ---
    if route == 'csv':
        text = file_bytes.decode('utf-8', errors='replace')
        g = _gate.assess(text, doc_type=doc_type)
        return ParseResult(text=text, markdown=text, confidence=g['confidence'],
                           tier_used='t0:csv', escalate=g['escalate'], reasons=g['reasons'])

    # --- Tier 0: DOCX ---
    if route == 'docx':
        try:
            from docx import Document
            text = '\n'.join(p.text for p in Document(io.BytesIO(file_bytes)).paragraphs if p.text)
        except Exception as e:    # noqa: BLE001
            log.warning('python-docx failed: %s', e); text = ''
        g = _gate.assess(text, doc_type=doc_type)
        return ParseResult(text=text, markdown=text, confidence=g['confidence'],
                           tier_used='t0:docx', escalate=g['escalate'], reasons=g['reasons'])

    # --- Tier 0/1: PDF (born-digital first, then OCR if it's a scan) ---
    if route == 'pdf':
        text, tables = _born_digital_pdf(file_bytes)
        if text.strip():
            g = _gate.assess(text, signals={'expect_table': bool(tables), 'table_cells': len(tables) or 1},
                             doc_type=doc_type)
            return ParseResult(text=text, markdown=text, tables=tables, confidence=g['confidence'],
                               tier_used='t0:pdfplumber', escalate=g['escalate'], reasons=g['reasons'])
        # empty text ⇒ scanned PDF; OCR per page handled in v2 (needs rasteriser).
        return ParseResult(text='', tier_used='t1:scanned_pdf', escalate=True,
                           reasons=['scanned_pdf_needs_ocr'], confidence=0.0)

    # --- Tier 1: image OCR (RapidOCR) ---
    if route == 'image':
        from . import ocr
        o = ocr.ocr_image(file_bytes)
        g = _gate.assess(o['text'], mean_conf=o['mean_conf'] if o['ok'] else None,
                         min_conf=o['min_conf'] if o['ok'] else None, doc_type=doc_type,
                         is_ocr=True)
        return ParseResult(text=o['text'], markdown=o['text'], confidence=g['confidence'],
                           tier_used='t1:rapidocr', signals={'mean_conf': o['mean_conf'],
                           'min_conf': o['min_conf'], 'n_lines': o['n_lines'], 'ocr_ok': o['ok']},
                           escalate=g['escalate'], reasons=g['reasons'])

    # --- text fallback ---
    text = file_bytes.decode('utf-8', errors='replace')
    g = _gate.assess(text, doc_type=doc_type)
    return ParseResult(text=text, markdown=text, confidence=g['confidence'],
                       tier_used='t0:text', escalate=g['escalate'], reasons=g['reasons'])
