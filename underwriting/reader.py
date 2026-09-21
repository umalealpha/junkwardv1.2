"""
underwriting/reader.py

Smart reader — "drop a broker instruction, it fills the form."

COST-DISCIPLINE CASCADE (CFO 2026-07-08), cheapest tier first:
  Tier 0  regex rules (extract_rules.rule_extract)  free · on-prem · instant
          → if CONFIDENT, return immediately and NEVER call a paid API.
  Tier 2  DeepSeek → Gemini (text) / Gemini vision (images)  paid, only the
          documents the rules couldn't confidently read.
  Local text extraction (core.doc_parse) runs first for every non-trivial file
  so most documents stay on-prem regardless of tier.

Everything the model/rules return is normalised the same way (ISO dates, no "P"
prefix) and validated before it reaches the form. A human confirms before issuing.
"""
from __future__ import annotations

import base64
import logging

from .extract_rules import rule_extract, normalize_date, strip_currency

log = logging.getLogger(__name__)

# Field ids per doctype. NOTE the signatory fields (cn_signame/phone/email) are
# INTENTIONALLY EXCLUDED — they are Alpha's internal signatory (the form keeps
# its own defaults); letting the reader fill them would stamp the BROKER's
# signatory onto a binding Alpha Direct cover note (Fable audit 2026-07-08).
FIELDS = {
    'wca':  ['w_policy', 'w_agency', 'w_name', 'w_addr', 'w_from', 'w_to',
             'w_date', 'w_emp', 'w_earn'],
    'cn':   ['cn_date', 'cn_client', 'cn_policy', 'cn_addr', 'cn_class', 'cn_risk',
             'cn_sum', 'cn_from', 'cn_to', 'cn_terr'],
    'cnfi': ['cn_date', 'cn_client', 'cn_policy', 'cn_from', 'cn_to', 'cn_terr',
             'fi_class', 'fi_reg', 'fi_make', 'fi_value', 'fi_bank'],
}
_DATE_KEYS = {'w_from', 'w_to', 'w_date', 'cn_from', 'cn_to', 'cn_date'}
_MONEY_KEYS = {'w_earn', 'cn_sum', 'fi_value'}

_SYSTEM = (
    'You read an insurance broker instruction and extract fields for an Alpha '
    'Direct underwriting document. First decide the document type: "wca" '
    '(Worker\'s Compensation certificate), "cn" (a plain cover note, e.g. '
    'Homeowners), or "cnfi" (a cover note for a financed vehicle/asset with a '
    'bank interest). Then return ONLY JSON of the shape '
    '{"doctype":"wca|cn|cnfi","fields":{...}} using EXACTLY these field keys for '
    'the chosen doctype:\n'
    f'  wca:  {FIELDS["wca"]}\n'
    f'  cn:   {FIELDS["cn"]}\n'
    f'  cnfi: {FIELDS["cnfi"]}\n'
    'Meanings — w_policy/cn_policy: policy number; w_agency: broker/agency; '
    'w_name/cn_client: insured name; w_addr/cn_addr: insured address; '
    'w_from/w_to/cn_from/cn_to: cover period start/end; w_date/cn_date: issue '
    'date; w_emp: number of employees (free text); w_earn: estimated annual '
    'earnings; cn_class: class of cover; cn_risk: risk address; cn_sum: sum '
    'insured; cn_terr: territorial limits; fi_class/fi_reg/fi_make/fi_value/'
    'fi_bank: financed vehicle cover line / reg / make / value / financing bank. '
    'Dates as YYYY-MM-DD. Money as a plain number string (no "P"). If a field is '
    'absent, use "". Return JSON only.'
)
_VISION_PROMPT = 'This image is an insurance broker instruction. ' + _SYSTEM


def _coerce(raw) -> dict | None:
    """Parse a model reply into {doctype, fields}, keeping only known keys.
    Returns None on anything that isn't a usable dict for a known doctype."""
    import json
    if not raw:
        return None
    txt = raw.strip() if isinstance(raw, str) else raw
    if isinstance(txt, str):
        if txt.startswith('```'):
            txt = txt.strip('`')
            txt = txt.split('\n', 1)[-1] if '\n' in txt else txt
        try:
            data = json.loads(txt)
        except (ValueError, TypeError):
            i, j = txt.find('{'), txt.rfind('}')
            if i < 0 or j <= i:
                return None
            try:
                data = json.loads(txt[i:j + 1])
            except (ValueError, TypeError):
                return None
    else:
        data = txt
    if not isinstance(data, dict):
        return None
    doctype = str(data.get('doctype') or '').strip().lower()
    if doctype not in FIELDS:
        return None                      # don't silently force 'wca' (false success)
    src = data.get('fields')
    if not isinstance(src, dict):
        src = {}
    fields = {}
    for k in FIELDS[doctype]:
        v = src.get(k, '')
        fields[k] = str(v).strip() if isinstance(v, (str, int, float)) else ''
    return {'doctype': doctype, 'fields': fields}


def _finalize(doctype: str, fields: dict, via: str) -> dict | None:
    """Normalise (ISO dates, strip currency), drop empties, reject if nothing
    real was extracted (so a blank parse escalates / asks for manual entry)."""
    out = {}
    for k in FIELDS.get(doctype, []):
        v = (fields.get(k) or '').strip()
        if k in _DATE_KEYS and v:
            v = normalize_date(v)          # non-ISO → '' (never feed a bad date)
        elif k in _MONEY_KEYS and v:
            v = strip_currency(v)
        out[k] = v
    if not any(out.values()):
        return None
    return {'ok': True, 'via': via, 'doctype': doctype, 'fields': out,
            'message': f'Filled from the document ({via}). Please check every field before issuing.'}


def _local_text(file_bytes: bytes, filename: str, content_type: str) -> str:
    """Local parse → text (born-digital PDF / CSV / XLSX / DOCX / image OCR).
    Never raises: optional-dep ImportError degrades to ''."""
    try:
        from core.doc_parse import cascade
        r = cascade.parse(file_bytes, mime=content_type or '', filename=filename or '')
        return (getattr(r, 'text', '') or '').strip()
    except Exception as e:   # noqa: BLE001
        log.warning('reader: local parse failed (%s)', e)
        return ''


def _map_text(text: str) -> tuple[dict | None, str]:
    """DeepSeek first, Gemini text on failure. Coercion OUTSIDE the try so a
    real parsing bug isn't misreported as an engine failure."""
    from core.ai_assist import (deepseek_complete, gemini_complete,
                                 DeepSeekUnavailable, GeminiUnavailable)
    prompt = 'Broker instruction text:\n\n' + text[:12000]
    raw = None
    try:
        raw = deepseek_complete(prompt, system_prompt=_SYSTEM,
                                response_format='json_object', timeout=30.0, max_tokens=2000)
    except DeepSeekUnavailable as e:
        log.warning('reader: deepseek unavailable (%s)', e)
    except Exception as e:   # noqa: BLE001
        log.warning('reader: deepseek error (%s)', e)
    parsed = _coerce(raw)
    if parsed:
        return parsed, 'deepseek'
    raw = None
    try:
        raw = gemini_complete(prompt, system_prompt=_SYSTEM,
                              response_format='json_object', timeout=30.0, max_tokens=2000)
    except GeminiUnavailable as e:
        log.warning('reader: gemini unavailable (%s)', e)
    except Exception as e:   # noqa: BLE001
        log.warning('reader: gemini error (%s)', e)
    parsed = _coerce(raw)
    if parsed:
        return parsed, 'gemini'
    return None, ''


def _map_image(file_bytes: bytes, content_type: str) -> tuple[dict | None, str]:
    from core.ai_assist import vision_complete, VisionUnavailable
    b64 = base64.b64encode(file_bytes).decode()
    url = f'data:{content_type or "image/jpeg"};base64,{b64}'
    try:
        raw = vision_complete(_VISION_PROMPT, url, timeout=45.0)
        return _coerce(raw), 'gemini-vision'
    except (VisionUnavailable, Exception) as e:   # noqa: BLE001
        log.warning('reader: vision failed (%s)', e)
        return None, ''


def extract_fields(file_bytes: bytes, filename: str, content_type: str) -> dict:
    """Return {ok, fields, doctype, via, message}. Tier 0 (rules) → Tier 2 (cloud)."""
    if not file_bytes:
        return {'ok': False, 'fields': {}, 'doctype': '', 'message': 'Empty file.'}
    ctype = (content_type or '')
    fname = (filename or '')
    is_pdf = ctype.startswith('application/pdf') or fname.lower().endswith('.pdf')

    # Local text for everything (images too — OCR via cascade). On-prem, free.
    text = _local_text(file_bytes, fname, ctype)

    # --- Tier 0: regex rules (free) ---
    rules = rule_extract(text) if text else {'ok': False, 'fields': {}, 'doctype': ''}
    if rules.get('ok'):
        done = _finalize(rules['doctype'], rules['fields'], 'rules')
        if done:
            return done

    # --- Tier 2: paid cloud, only when the rules weren't confident ---
    parsed, via = (None, '')
    if len(text) >= 25:
        parsed, via = _map_text(text)
    elif ctype.startswith('image/'):
        parsed, via = _map_image(file_bytes, ctype)
    elif is_pdf:
        return {'ok': False, 'fields': {}, 'doctype': '',
                'message': ('This looks like a scanned PDF with no text layer. '
                            'Save the page as an image (JPG/PNG) and drop that in, '
                            'or fill the form manually.')}

    if parsed:
        done = _finalize(parsed['doctype'], parsed['fields'], via)
        if done:
            if len(text) > 12000:
                done['message'] += ' (Long document was truncated — double-check the tail fields.)'
            return done

    # --- Tier 0 partial: rules found SOMETHING but weren't "confident" ---
    if rules.get('fields') and any(rules['fields'].values()):
        done = _finalize(rules['doctype'], rules['fields'], 'rules (partial — please check)')
        if done:
            return done

    return {'ok': False, 'fields': {}, 'doctype': '',
            'message': 'Could not read that document automatically — please fill the form manually.'}
