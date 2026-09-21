"""core/aria_knowledge.py — SOP-bank grounding for the "Ask Omni" chat bot.

Answers staff "how do I…" / policy questions from the company's OWN SOP / policy
bank (iso_compliance.SOPDocument.content_text) so people stop asking these in the
team room (CFO 2026-08-31 — the team chat was being used as a help desk).

Deliberately the SOP bank ONLY — NOT the CFO↔Claude NotebookPage, which carries
internal management facts (financial figures, role corrections) that are not for
every staff member. Plain keyword search over the already-extracted plain text
(no embeddings yet); returns short snippets the LLM answers FROM, never invents.
"""
import re

# Words that carry no topic signal — dropped before matching.
_STOP = {
    'the', 'a', 'an', 'and', 'or', 'to', 'of', 'in', 'on', 'for', 'how', 'do',
    'does', 'my', 'is', 'are', 'was', 'what', 'when', 'where', 'which', 'who',
    'can', 'could', 'would', 'you', 'me', 'please', 'omni', 'it', 'this', 'that',
    'with', 'need', 'want', 'get', 'have', 'from', 'about', 'there', 'here',
    'should', 'will', 'the', 'any', 'all',
}


def _keywords(message: str) -> list:
    """Significant lower-case words from the question (deduped, capped)."""
    seen, out = set(), []
    for w in re.findall(r'[a-zA-Z]{3,}', (message or '').lower()):
        if w in _STOP or w in seen:
            continue
        seen.add(w)
        out.append(w)
    return out[:6]


def knowledge_snippets(message: str, *, limit: int = 2, chars: int = 900) -> list:
    """Up to `limit` relevant SOP snippets for the message.

    Returns a list of "[Department — Title] …passage…" strings, best first.
    Empty on no keywords, no match, or ANY error — the caller treats this as
    best-effort grounding, never a hard dependency.
    """
    try:
        from django.db.models import Q
        from iso_compliance.models import SOPDocument
    except Exception:  # noqa: BLE001
        return []

    kws = _keywords(message)
    if not kws:
        return []

    try:
        q = Q()
        for w in kws:
            q |= Q(title__icontains=w) | Q(content_text__icontains=w)
        qs = (SOPDocument.objects
              .filter(q, status=SOPDocument.STATUS_ACTIVE)
              .exclude(content_text='')
              .only('title', 'department', 'content_text')[:12])

        scored = []
        for d in qs:
            hay = (d.title + ' ' + (d.content_text or '')).lower()
            score = sum(hay.count(w) for w in kws)
            if score:
                scored.append((score, d))
        scored.sort(key=lambda x: -x[0])

        out = []
        for _, d in scored[:limit]:
            body = (d.content_text or '').strip()
            low = body.lower()
            hits = [low.find(w) for w in kws if low.find(w) >= 0]
            pos = min(hits) if hits else 0
            start = max(0, pos - 150)
            snippet = ' '.join(body[start:start + chars].split())
            out.append(f'[{d.department} — {d.title}] {snippet}')
        return out
    except Exception:  # noqa: BLE001 — grounding is a bonus, never an error
        return []
