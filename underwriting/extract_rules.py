"""
underwriting/extract_rules.py

Tier 0 of the reader — a free, instant, on-prem REGEX extractor for the common
labelled broker instruction ("Policy Number: …", "Insured: …", "Period … to …").

Cost discipline (CFO 2026-07-08): this runs first, inside the server's Python,
for every upload. When it is confident (enough core fields found for the guessed
doctype) the reader RETURNS its result and NEVER calls DeepSeek/Gemini — zero
external spend. Only low-confidence / messy documents escalate to the paid
tier. Tune/extend the patterns from real samples over time.

Also exports `normalize_date` + `strip_currency`, reused by the cloud path so
DeepSeek/Gemini output is cleaned the same way (ISO dates, no "P" prefix).
"""
from __future__ import annotations

import re
from datetime import date

_MONTHS = {m.lower(): i for i, m in enumerate(
    ['January', 'February', 'March', 'April', 'May', 'June', 'July',
     'August', 'September', 'October', 'November', 'December'], 1)}
_MON3 = {m[:3].lower(): i for m, i in _MONTHS.items()}


def normalize_date(raw: str) -> str:
    """Best-effort → 'YYYY-MM-DD'. Returns '' when unparseable (never guesses)."""
    s = (raw or '').strip()
    if not s:
        return ''
    # already ISO
    m = re.match(r'^(\d{4})-(\d{1,2})-(\d{1,2})$', s)
    if m:
        y, mo, d = map(int, m.groups())
    else:
        # dd/mm/yyyy or dd-mm-yyyy or dd.mm.yyyy
        m = re.match(r'^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})$', s)
        if m:
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if y < 100:
                y += 2000
        else:
            # "1 July 2026" / "1st Jul 2026" / "July 1, 2026"
            m = re.match(r'^(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,})\.?,?\s+(\d{4})$', s)
            if m:
                d, mon, y = int(m.group(1)), m.group(2).lower(), int(m.group(3))
                mo = _MONTHS.get(mon) or _MON3.get(mon[:3])
                if not mo:
                    return ''
            else:
                m = re.match(r'^([A-Za-z]{3,})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})$', s)
                if not m:
                    return ''
                mon, d, y = m.group(1).lower(), int(m.group(2)), int(m.group(3))
                mo = _MONTHS.get(mon) or _MON3.get(mon[:3])
                if not mo:
                    return ''
    try:
        return date(y, mo, d).isoformat()
    except (ValueError, TypeError):
        return ''


def strip_currency(raw: str) -> str:
    """'P1,480,000.00' / 'BWP 1 114 654' → '1,480,000.00' (keep digits/commas/dot)."""
    s = (raw or '').strip()
    s = re.sub(r'^(?:bwp|pula|p)\s*', '', s, flags=re.I)
    s = s.replace(' ', '')
    m = re.match(r'^[\d,]+(?:\.\d+)?', s)
    return m.group(0) if m else s


def _find(patterns, text):
    for p in patterns:
        m = re.search(p, text, re.I)
        if m:
            return (m.group(1) if m.groups() else m.group(0)).strip().rstrip('.,;')
    return ''


def _guess_doctype(t: str) -> str:
    if re.search(r"worker'?s?\s+compensation|\bWCA\b|cap\.?\s*47:03|section\s*32", t, re.I):
        return 'wca'
    if re.search(r'financed|financial\s+interest|vehicle|\breg(?:istration)?\b|make\s*[/:]|wesbank|noted\b', t, re.I) \
       and re.search(r'\b[A-Z]\d{2,3}\s?[A-Z]{2,3}\b', t):   # a BW-style plate
        return 'cnfi'
    return 'cn'


def _period(text: str) -> tuple[str, str]:
    # \bto\b — a bare 'to' matched INSIDE "Motor" on a "Class of cover: Motor
    # Comprehensive" line, so the real "Period: … to …" line below it was never
    # reached. And a bare '-' separator split an ISO date ("2026-09-04 to …" →
    # from='2026'), so every labelled note fell through to the paid tier. The
    # dash must now stand alone between spaces.
    m = re.search(r'(?:period(?:\s+of\s+insurance)?|cover(?:\s+period)?)\s*[:\-]?\s*'
                  r'(?:from\s*)?(.+?)\s*(?:\bto\b|\buntil\b|\s[\-–]\s)\s*(.+?)(?:\n|$)', text, re.I)
    if not m:
        return '', ''
    return normalize_date(m.group(1)), normalize_date(m.group(2))


def rule_extract(text: str) -> dict:
    """Return {ok, doctype, fields, confidence, via}. `ok` True only when the
    regex is CONFIDENT (enough core fields) so the reader can skip the cloud."""
    t = text or ''
    if len(t.strip()) < 20:
        return {'ok': False, 'doctype': '', 'fields': {}, 'confidence': 0.0, 'via': 'rules'}
    dt = _guess_doctype(t)
    p_from, p_to = _period(t)

    policy = _find([r'policy\s*(?:number|no\.?)?\s*[:\-]?\s*([A-Z]{2,}\w[\w\-/]+)',
                    r'\b(COMG\w+)\b'], t)
    name = _find([r'(?:insured(?:\s*/\s*employer)?|employer|client)\s*(?:name)?\s*[:\-]?\s*(.+?)(?:\n|$)'], t)
    agency = _find([r'(?:agency|broker)\s*[:\-]?\s*(.+?)(?:\n|$)'], t)
    addr = _find([r'(?:address|risk\s+address)\s*[:\-]?\s*(.+?)(?:\n|$)'], t)

    fields: dict[str, str] = {}
    if dt == 'wca':
        emp = _find([r'(?:no\.?\s*of\s*employees|employees)\s*[:\-]?\s*(.+?)(?:\n|$)',
                     r'\b(all\s+\d+\s+employees)\b', r'\b(\d+)\s+employees\b'], t)
        earn = _find([r'(?:est(?:imated)?\.?\s*annual\s*earnings|annual\s*earnings|earnings)\s*[:\-]?\s*(.+?)(?:\n|$)'], t)
        issue = _find([r'(?:date\s+of\s+issue|issue\s+date|issued|date)\s*[:\-]?\s*(.+?)(?:\n|$)'], t)
        fields = {'w_policy': policy, 'w_agency': agency, 'w_name': name, 'w_addr': addr,
                  'w_from': p_from, 'w_to': p_to, 'w_date': normalize_date(issue),
                  'w_emp': emp, 'w_earn': strip_currency(earn)}
        core = [policy, name, p_from, p_to]
    else:
        issue = _find([r'(?:issue\s+date|date\s+of\s+issue|date)\s*[:\-]?\s*(.+?)(?:\n|$)'], t)
        fields = {'cn_policy': policy, 'cn_client': name, 'cn_addr': addr,
                  'cn_from': p_from, 'cn_to': p_to, 'cn_date': normalize_date(issue)}
        if dt == 'cnfi':
            fields.update({
                'fi_reg':  _find([r'(?:reg(?:istration)?\s*(?:no\.?|number)?)\s*[:\-]?\s*([A-Z0-9 ]{5,12})',
                                  r'\b([A-Z]\d{2,3}\s?[A-Z]{2,3})\b'], t),
                'fi_make': _find([r'(?:make(?:\s*/\s*model)?|vehicle)\s*[:\-]?\s*(.+?)(?:\n|$)'], t),
                'fi_value': strip_currency(_find([r'(?:vehicle\s*value|value)\s*[:\-]?\s*(.+?)(?:\n|$)'], t)),
                'fi_bank': _find([r'(?:financ\w*\s*(?:interest)?(?:\s*(?:of|noted))?|bank)\s*[:\-]?\s*(.+?)(?:\n|$)'], t),
            })
            core = [policy, name, fields['fi_reg']]
        else:
            fields.update({
                'cn_class': _find([r'(?:class(?:\s*of\s*cover)?)\s*[:\-]?\s*(.+?)(?:\n|$)'], t),
                'cn_risk':  _find([r'(?:risk\s+address)\s*[:\-]?\s*(.+?)(?:\n|$)']) if False else addr,
                'cn_sum':   strip_currency(_find([r'(?:sum\s+insured)\s*[:\-]?\s*(.+?)(?:\n|$)'], t)),
            })
            core = [policy, name, p_from, p_to]

    fields = {k: (v or '').strip() for k, v in fields.items()}
    found = sum(1 for c in core if c)
    confidence = found / len(core) if core else 0.0
    # Confident = at least 3 core anchors present (or all of a 3-anchor set).
    ok = found >= 3
    return {'ok': ok, 'doctype': dt, 'fields': fields, 'confidence': round(confidence, 2), 'via': 'rules'}
