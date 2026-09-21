"""core/request_tracker_ai.py — the plain-English "where is it / what's next"
line for the unified request tracker (CFO directive 2026-08-04, DeepSeek option A).

Design:
  * A DETERMINISTIC template line is built first — it is always correct, instant
    and free, and needs no network.
  * DeepSeek (via core.ai_assist.reasoning_complete, which cascades
    Ollama→DeepSeek→Gemini→…) then only *rephrases* that line into one warm,
    plain sentence. On any hiccup the template line is returned unchanged.

Data protection: the text handed to the AI carries NO names and NO amounts — only
the request kind, the current stage, how long it has waited, and what is next. It
is additionally run through is_safe_for_ai() before send, so nothing identifiable
can leave even if a caller passes richer facts by mistake.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def template_line(
    *,
    kind_label: str,
    status_label: str,
    holder_label: str = '',
    days_waiting: int | None = None,
    next_label: str = '',
    done: bool = False,
    rejected: bool = False,
) -> str:
    """Deterministic one-liner from structured, non-sensitive facts."""
    if rejected:
        return f'{kind_label} was rejected.'
    if done:
        return f'{kind_label} is done.'
    if holder_label:
        waited = ''
        if days_waiting is not None and days_waiting >= 0:
            waited = (' today' if days_waiting == 0
                      else f' for {days_waiting} day{"s" if days_waiting != 1 else ""}')
        nxt = f' {next_label} is next after that.' if next_label else ''
        return f'With {holder_label}{waited}.{nxt}'.replace(' .', '.')
    return status_label or f'{kind_label} is in progress.'


def plain_status_line(
    *,
    kind_label: str,
    status_label: str,
    holder_label: str = '',
    days_waiting: int | None = None,
    next_label: str = '',
    done: bool = False,
    rejected: bool = False,
    use_ai: bool = True,
) -> str:
    """Return one friendly plain-English status sentence. Template first, DeepSeek
    polish second (PII-free), template fallback on any failure."""
    base = template_line(
        kind_label=kind_label, status_label=status_label, holder_label=holder_label,
        days_waiting=days_waiting, next_label=next_label, done=done, rejected=rejected,
    )
    if not use_ai:
        return base

    try:
        from core.ai_assist import reasoning_complete, is_safe_for_ai, DeepSeekUnavailable
    except Exception:                       # noqa: BLE001 — ai layer unavailable
        return base

    safety = is_safe_for_ai(base)
    if not safety.safe:
        return base

    try:
        out = reasoning_complete(
            'Rewrite this internal approval-status note as ONE short, warm, '
            'plain-English sentence for the staff member who submitted the '
            'request. Under 20 words. No jargon, no emoji. State only what is '
            f'given — invent nothing:\n"{safety.redacted_text}"',
            system_prompt=(
                'You turn internal approval-workflow status notes into one short, '
                'friendly sentence for a non-technical staff member. Botswana/'
                'British English. Never add facts that are not in the input.'
            ),
            max_tokens=60,
            feature='request_tracker_status',
        )
    except DeepSeekUnavailable:
        return base
    except Exception:                       # noqa: BLE001 — never break the tracker
        log.debug('request_tracker_ai: polish failed, using template', exc_info=True)
        return base

    line = (out or '').strip().strip('"').strip()
    # Guard against a chatty model: keep it to a single sentence, else template.
    if not line or len(line) > 200:
        return base
    return line
