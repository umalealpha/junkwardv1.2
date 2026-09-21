"""core/access_parse.py — Aria: plain-English → access-grant proposal.

Turns a request like "give Leone and the claims clerks the system tester role"
into a structured proposal: the best-matching role + the people named. Used by
the Bulk Assign screen so the CFO can describe a grant in words; the model only
PROPOSES — the actual grant is the separate, human-confirmed bulk-assign action.

Hard rules (mirror budgets/advisor.py):
  - Never grants anything; output is a proposal only.
  - Never writes to the DB.
  - The model call goes through core.ai_assist.deepseek_complete, so it inherits
    the PII firewall, the key vault and the Gemini fall-back. Never call the
    provider directly from here — an access request is mostly staff names, and
    a hand-rolled request is exactly how this file used to skip the firewall.
  - If the model is unavailable, a deterministic keyword fallback runs so the
    screen still works (just less clever).
  - User-facing name is "Aria advisor" — never "DeepSeek".
"""
from __future__ import annotations

import json
import re

# No urllib, no API base, no model name here any more: the call goes through
# core.ai_assist.deepseek_complete, which owns the endpoint, the key and the
# PII firewall.


def _fallback(text: str, roles: list) -> dict:
    """Deterministic parse when Aria's model is unavailable: keyword-match the
    role by word overlap, and pull out capitalised name-like phrases."""
    t = (text or '').lower()
    best, best_score = None, 0
    for r in roles:
        if r['code'].lower() in t:
            score = 5
        else:
            words = [w for w in re.split(r'[^a-z]+', r['name'].lower()) if len(w) >= 4]
            score = sum(1 for w in words if w in t)
        if score > best_score:
            best, best_score = r, score
    role_code = best['code'] if (best and best_score > 0) else ''
    role_words = set()
    if best:
        role_words = {w.lower() for w in re.split(r'[^A-Za-z]+', best['name']) if w}
    names = re.findall(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\b', text or '')
    names = [n for n in names if n.lower() not in role_words and len(n) > 2]
    return {'role_code': role_code, 'names': names, 'department': '', 'source': 'Aria advisor (offline)'}


def parse_access_request(text: str, roles: list) -> dict:
    """roles = [{'id','code','name'}]. Returns {role_code, names[], department, source}."""
    role_lines = "\n".join(f"- {r['code']}: {r['name']}" for r in roles)
    system = (
        "You convert a plain-English staff access request into JSON for an "
        "insurance ERP. Pick the SINGLE best-matching role from this list and "
        "return its code:\n" + role_lines +
        "\nAlso extract the people named (person names only — never the role or "
        "department words). If a team/department is named, put it in 'department'. "
        "Return ONLY compact JSON, no prose, no code fences: "
        '{"role_code": "...", "names": ["..."], "department": ""}'
    )
    # Go through core.ai_assist, NOT a hand-rolled request. This function used to
    # post straight to api.deepseek.com with urllib, which meant it was the one
    # caller that skipped the @firewall decorator — and the text it sends is an
    # access request, so it is mostly staff names. It also missed the key vault
    # and the Gemini fall-back. Routing it here fixes all three at once.
    try:
        from core.ai_assist import deepseek_complete
        content = deepseek_complete(text or '', system_prompt=system,
                                    response_format='json_object',
                                    max_tokens=400, timeout=25)
        content = re.sub(r'^```(?:json)?|```$', '', (content or '').strip(),
                         flags=re.M).strip()
        data = json.loads(content)
        return {
            'role_code':  str(data.get('role_code') or ''),
            'names':      [str(n) for n in (data.get('names') or []) if str(n).strip()],
            'department': str(data.get('department') or ''),
            'source':     'Aria advisor',
        }
    except Exception:  # noqa: BLE001 — any failure degrades to the deterministic parse
        return _fallback(text, roles)
