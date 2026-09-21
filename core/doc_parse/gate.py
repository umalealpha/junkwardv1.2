"""Rule-based confidence gate — decides whether a LOCAL parse is good enough
or must escalate to an external LLM. Deliberately NOT an LLM (an LLM gate would
defeat the cost/PII purpose). Pure Python, instant, free.

Escalate if ANY signal trips. Returns a 0-1 composite confidence too.
"""
from __future__ import annotations

import re

# Per-doc-type required patterns: a clean OCR of the WRONG document still trips
# the gate when the expected identifiers are absent. Botswana-specific.
REQUIRED_PATTERNS = {
    'company_reg': [r'\bCO\s?\d{4}\s?/\s?\d{3,7}\b', r'\b(?:TIN|VAT|BURS)\b', r'\b(?:PTY|LTD|LIMITED)\b'],
    'tax_clearance': [r'\bTIN\b', r'\b(?:tax\s*clearance|valid\s*until)\b'],
    'bank_letter': [r'\b(?:account|acc|a/c)\b', r'\b(?:branch|sort|swift)\b'],
}

_KNOWN_TOKENS = re.compile(r'\b(?:BWP|PULA|PTY|LTD|LIMITED|VAT|TIN|CO\d{4}|INVOICE|TOTAL|DATE|\d{4}-\d{2}-\d{2})\b', re.I)


def _garble_ratio(text: str) -> float:
    if not text:
        return 1.0
    # `|` `#` `*` are structural Markdown (tables/headers), not garble.
    allowed = ".,/-:()&%'#|*+=[]{}\""
    non_alnum = sum(1 for c in text if not (c.isalnum() or c.isspace() or c in allowed))
    return non_alnum / max(len(text), 1)


def assess(text: str, *, mean_conf: float | None = None, min_conf: float | None = None,
           signals: dict | None = None, doc_type: str | None = None,
           min_chars: int = 20, conf_floor: float = 0.70, is_ocr: bool = False) -> dict:
    """Return {confidence: float, escalate: bool, reasons: [str]}."""
    text = text or ''
    signals = signals or {}
    reasons: list[str] = []
    confidence = 1.0

    # 1. empty / too short
    stripped = text.strip()
    if len(stripped) < min_chars:
        reasons.append('empty_or_short')
        confidence = min(confidence, 0.0)

    # 2. OCR mean/min confidence below floor
    if mean_conf is not None:
        confidence = min(confidence, float(mean_conf))
        if mean_conf < conf_floor:
            reasons.append(f'low_mean_conf({mean_conf:.2f}<{conf_floor})')
    if min_conf is not None and min_conf < (conf_floor - 0.2):
        # localized garble (e.g. on the ID/Omang line) even if the mean looks ok
        reasons.append(f'low_min_conf({min_conf:.2f})')

    # 3. garbled text (mojibake / OCR noise)
    if stripped:
        g = _garble_ratio(stripped)
        if g > 0.40:
            reasons.append(f'garbled({g:.2f})')
            confidence = min(confidence, 0.3)
        # known-token requirement only for OCR/scanned text (catches garbled
        # OCR). Deterministic tables/CSV legitimately carry none of these.
        if is_ocr:
            token_hits = len(_KNOWN_TOKENS.findall(stripped))
            if len(stripped) > 80 and token_hits == 0:
                reasons.append('no_known_tokens')
                confidence = min(confidence, 0.5)

    # 4. required-field / pattern miss for the doc type
    pats = REQUIRED_PATTERNS.get((doc_type or '').lower())
    if pats and stripped:
        if not any(re.search(p, stripped, re.I) for p in pats):
            reasons.append(f'required_pattern_miss({doc_type})')
            confidence = min(confidence, 0.5)

    # 5. table-structure signals (TB/GL/spreadsheet)
    if signals.get('expect_table'):
        if signals.get('table_cells', 1) == 0:
            reasons.append('no_table_cells')
            confidence = min(confidence, 0.2)
        nd = signals.get('min_numeric_density')
        if nd is not None and nd < 0.15:
            reasons.append(f'low_numeric_density({nd})')
            confidence = min(confidence, 0.4)
    if signals.get('tb_balance_failed'):
        reasons.append('tb_balance_failed')
        confidence = min(confidence, 0.3)

    return {'confidence': round(confidence, 4), 'escalate': bool(reasons), 'reasons': reasons}
