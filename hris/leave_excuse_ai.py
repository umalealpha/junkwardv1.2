"""
hris/leave_excuse_ai.py

Aria's read of the day's leave excuses for the Leave Excuse Response dashboard
(CFO 2026-07-22). Follows the deepseek_insight precedent
(integrations/timedoctor_recon.py): ANONYMISE first (Person N, reason, hours,
their words — NO names, NO PII), run it through is_safe_for_ai(), then the
cheapest-capable reasoning engine (reasoning_complete), cached 6h.

Returns per-person one-line summaries with a weak/vague/repeated flag, plus 3-5
overall bullets. Never raises — a flaky AI degrades to {'ok': False, ...} and the
dashboard just hides the Aria card.
"""

from __future__ import annotations

import hashlib
import json

_CACHE_TTL = 60 * 60 * 6   # 6 hours

_SYSTEM = (
    "You are Aria, Alpha Direct Insurance's workplace analyst. You are given an "
    "ANONYMISED list of staff explanations for a day of low or no productive "
    "hours — no names, only 'Person N', a reason code, the hours, and their own "
    "words. Judge each explanation and the day as a whole.\n\n"
    "Respond ONLY with valid JSON of the exact form:\n"
    '{"people":[{"n":1,"summary":"<one short line>","flag":"weak|vague|repeated|ok"}],'
    '"overall":["<bullet>", "<bullet>", "<bullet>"]}\n\n'
    "Rules: one line per person, under 120 chars. flag='weak' for a poor reason "
    "(e.g. a power cut at home), 'vague' for no real detail, 'repeated' if it "
    "echoes a common excuse across the group, 'ok' otherwise. Give 3-5 overall "
    "bullets: the themes, any pattern worth a manager's attention, and one "
    "concrete action. No names. No preamble."
)


def aria_analysis(rows, *, date_str: str = '') -> dict:
    """Anonymised Aria commentary on the day's excuse rows.

    Returns {'ok': True, 'items': {'people': [...], 'overall': [...]}, 'text': ''}
    on success, or {'ok': False, 'reason': str, 'items': None, 'text': ''}.
    Cached 6h on success (keyed by day + a hash of the anonymised content)."""
    from django.core.cache import cache

    if not rows:
        return {'ok': False, 'reason': 'no excuses to analyse', 'items': None, 'text': ''}

    anon = [{
        'n': i,
        'reason': (r.get('reason') or 'none'),
        'hours': r.get('productive_hours'),
        'text': (r.get('explanation') or '')[:500],
    } for i, r in enumerate(rows, start=1)]

    fingerprint = hashlib.md5(json.dumps(anon, sort_keys=True).encode()).hexdigest()[:12]
    ck = f'leave_excuse_aria:{date_str or (rows[0].get("date") or "")}:{fingerprint}'
    hit = cache.get(ck)
    if hit is not None:
        return hit

    from core.ai_assist import (reasoning_complete, is_safe_for_ai,
                                DeepSeekUnavailable, GeminiUnavailable)

    lines = '\n'.join(
        f"Person {a['n']} | reason={a['reason']} | productive_hours={a['hours']} | "
        f"says: {a['text'] or '(no explanation given)'}"
        for a in anon
    )
    rep = is_safe_for_ai(lines)
    if not getattr(rep, 'safe', True):
        # Do NOT cache a safety refusal — let it retry once the input changes.
        return {'ok': False, 'reason': 'input failed PII safety check',
                'items': None, 'text': ''}

    prompt = ("Anonymised staff explanations for a day of low/no productive hours "
              "(no names):\n\n" + (rep.redacted_text or lines) +
              "\n\nReturn the JSON object described in the system prompt.")

    try:
        raw = reasoning_complete(
            prompt, system_prompt=_SYSTEM, response_format='json_object',
            feature='leave_excuse', timeout=30.0, max_tokens=800,
        )
        parsed = json.loads(raw)
        items = {
            'people': (parsed.get('people') or [])[:200],
            'overall': (parsed.get('overall') or [])[:5],
        }
        out = {'ok': True, 'items': items, 'text': ''}
    except (DeepSeekUnavailable, GeminiUnavailable) as exc:
        return {'ok': False, 'reason': str(exc), 'items': None, 'text': ''}
    except (ValueError, TypeError, KeyError) as exc:
        return {'ok': False, 'reason': f'AI response not parseable: {exc}',
                'items': None, 'text': ''}
    except Exception as exc:    # noqa: BLE001 — AI must never break the dashboard
        return {'ok': False, 'reason': f'AI error: {exc}', 'items': None, 'text': ''}

    cache.set(ck, out, _CACHE_TTL)
    return out
