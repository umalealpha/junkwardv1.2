"""iso_compliance/classify.py — DeepSeek document classifier for the SOP Bank.

CFO directive 2026-07-27 (Unami's Corporate Governance request): when an
empowered staff member uploads a document, DeepSeek reads it and SUGGESTS which
department it belongs to and whether it is an SOP or a Policy. The uploader then
confirms (or corrects) in one tap — the AI never files silently, so a wrong
guess can't misfile a governance document.

Reuses core.ai_assist (the CFO-authorised, PII-scrubbing DeepSeek client). Only
a bounded excerpt of the document text is sent, and only after is_safe_for_ai()
has redacted any ID / bank / phone / e-mail / money patterns. Never raises:
if DeepSeek is unavailable or the text is unclassifiable, it returns a low-
confidence blank so the uploader simply picks manually.
"""
from __future__ import annotations

import json
import logging

from core.ai_assist import DeepSeekUnavailable, deepseek_complete, is_safe_for_ai

log = logging.getLogger(__name__)

# How much of the document text to send. The department + kind are almost always
# clear from the title + opening lines; keeping this tight bounds both cost and
# the amount of document body that ever leaves for the (PII-scrubbed) AI call.
_EXCERPT_CHARS = 1200

_SYSTEM = (
    'You are a records officer at Alpha Direct Insurance (Botswana). You are '
    'given the file name and the opening text of a company document, plus the '
    'list of existing departments in the document library. Decide:\n'
    '1. department — pick the BEST match from the provided list; only invent a '
    'new short department name if none fit.\n'
    '2. doc_type — "policy" if it sets rules/standards/governance the company '
    'must follow (e.g. leave policy, code of conduct, IT/security policy, '
    'disciplinary policy); "sop" if it is a step-by-step procedure for how to '
    'perform a task.\n'
    '3. title — a clean human title (no file extension, no version noise).\n'
    'Respond ONLY with valid JSON: '
    '{"department":"...","doc_type":"sop|policy","title":"...",'
    '"confidence":0.0-1.0}. No prose.'
)


def classify_document(text: str, filename: str, departments: list[str]) -> dict:
    """Return {department, doc_type, title, confidence, source}.

    `source` is 'deepseek' on a real classification, 'fallback' when DeepSeek
    is unavailable / unsafe / unparseable. On fallback, department is '' (the
    uploader picks) and doc_type defaults to 'sop'; title falls back to the
    filename stem. Never raises.
    """
    stem = (filename or '').rsplit('/', 1)[-1]
    for ext in ('.docx', '.pdf', '.doc', '.pptx', '.xlsx'):
        if stem.lower().endswith(ext):
            stem = stem[: -len(ext)]
            break
    fallback = {'department': '', 'doc_type': 'sop', 'title': stem,
                'confidence': 0.0, 'source': 'fallback'}

    excerpt = (text or '')[:_EXCERPT_CHARS]
    safety = is_safe_for_ai(f'{filename}\n\n{excerpt}')
    if not safety.safe:
        log.info('classify_document: safety filter refused payload for %r', filename)
        return fallback

    user_prompt = (
        f'File name: {filename}\n'
        f'Existing departments: {json.dumps(sorted(set(departments)))}\n\n'
        f'Document text (excerpt):\n{safety.redacted_text}\n\n'
        'Classify per the system prompt. JSON only.'
    )

    try:
        raw = deepseek_complete(user_prompt, system_prompt=_SYSTEM,
                                response_format='json_object', timeout=20.0)
    except DeepSeekUnavailable as exc:
        log.info('classify_document: DeepSeek unavailable (%s)', exc)
        return fallback

    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return fallback

    doc_type = (parsed.get('doc_type') or 'sop').strip().lower()
    if doc_type not in ('sop', 'policy'):
        doc_type = 'sop'
    try:
        confidence = max(0.0, min(1.0, float(parsed.get('confidence', 0) or 0)))
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        'department': (parsed.get('department') or '').strip()[:80],
        'doc_type': doc_type,
        'title': (parsed.get('title') or stem).strip()[:255] or stem,
        'confidence': confidence,
        'source': 'deepseek',
    }
