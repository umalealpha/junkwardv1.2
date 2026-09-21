"""commissions/ai_review.py — Aria's plain-English second opinion on ONE
commission, on demand, for a reviewer (CFO 2026-08-22, "make the checkers' life
better" — the AI pre-check).

Grounded in the DETERMINISTIC review_flags: Aria only turns those signals (and a
redacted shape of the sheet) into one short human sentence. She never chooses a
commission amount and never changes any data — the numbers and the approval stay
100% deterministic. Best-effort: if the AI is down/disabled, we return a note
built straight from the flags, so the reviewer always gets something useful and
Omni never depends on the AI.
"""
from __future__ import annotations

import logging

log = logging.getLogger('commissions')


def _fallback(flags) -> str:
    items = flags.get('items') or []
    if flags.get('level') == 'clean':
        return ('The figures look consistent — the total ties to the lines, no '
                'duplicate policies, and it is in line with last month.')
    if items:
        return 'Worth a look before approving: ' + '; '.join(items) + '.'
    return 'Checks did not run for this one — open the lines and review manually.'


def ai_opinion(sub) -> dict:
    """{'note': str, 'source': 'aria'|'checks'}. Never raises."""
    try:
        from .review_flags import review_flags
        flags = review_flags(sub)
        fallback = _fallback(flags)
    except Exception:
        log.info('commission ai_opinion: flags failed for %s', getattr(sub, 'id', '?'), exc_info=True)
        return {'note': 'Open the lines and review this one manually.', 'source': 'checks'}
    try:
        from core.ai_assist import reasoning_complete, is_safe_for_ai

        lines = list(sub.lines.all())
        # a compact, low-PII shape of the sheet — counts + the flag sentences
        # (which already say what's odd). No client names sent deliberately.
        txn_mix = {}
        for l in lines:
            txn_mix[l.transaction_type] = txn_mix.get(l.transaction_type, 0) + 1
        shape = (f"Agent commission for {sub.period_label}. "
                 f"{len(lines)} policy line(s); gross {sub.gross_commission}. "
                 f"Transaction mix: {txn_mix}. "
                 f"Automated checks: {'; '.join(flags.get('items') or []) or 'all clean'}.")
        report = is_safe_for_ai(shape)
        if not report.safe:
            return {'note': fallback, 'source': 'checks'}
        sample = (report.redacted_text or '').strip()
        if not sample:
            return {'note': fallback, 'source': 'checks'}

        prompt = (
            "You are Aria, giving an insurance commission reviewer a quick second "
            "opinion BEFORE they approve a monthly commission. Here is what the "
            "system already knows (some values redacted for privacy):\n"
            f"{sample}\n\n"
            "In ONE or TWO short plain-English sentences, tell the reviewer whether "
            "it looks fine to approve or what to check first. Base it ONLY on the "
            "information above — do NOT invent policy numbers or amounts, and do "
            "NOT state a commission figure. No preamble."
        )
        note = reasoning_complete(
            prompt,
            system_prompt="You are a terse, practical finance reviewer's assistant.",
            max_tokens=120, timeout=15.0,
        )
        note = (note or '').strip().replace('\n', ' ')
        return {'note': note[:400], 'source': 'aria'} if note else {'note': fallback, 'source': 'checks'}
    except Exception:
        log.info('commission ai_opinion unavailable for %s', getattr(sub, 'id', '?'), exc_info=True)
        return {'note': fallback, 'source': 'checks'}
