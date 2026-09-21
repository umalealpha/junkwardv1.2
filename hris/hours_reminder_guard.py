"""AI sanity guard for the intraday / weekly "you're behind on hours" emails
(CFO directive 2026-08-27).

Before Omni tells anyone they are behind on hours, two independent
off-subscription engines (DeepSeek + Gemini) sanity-check each recipient: does the
low reading look like a GENUINE shortfall, or a DATA ERROR — an unconfirmed Time
Doctor account, a name mismatch, or hours that simply have not uploaded yet?
Anyone who looks like an error is HELD (never emailed) and surfaced to the CFO.

This is the SECOND net behind the map-based hours fix
(`integrations.td_matching.hours_by_employee`): the resolver makes the number
right by using the confirmed account link; this guard makes sure a wrong number
can never reach a person even if a brand-new failure mode slips through.

Design (CFO: "so it won't send wrong information to people"):
  * FAIL-CLOSED. If the engines cannot run, or return nothing usable, everyone is
    HELD rather than emailed.
  * An UNCONFIRMED Time Doctor account is held outright — we do not trust an
    unverified identity enough to tell that person they are behind.
  * For confirmed people, HOLD when EITHER engine says the low reading looks like
    a data error; SEND only a shortfall neither engine doubts.
  * PII-firewalled: only reference numbers + hours go to the engines, never names.

Reuses core.ai_assist (off the Claude subscription). Nothing metered by Claude.
"""
from __future__ import annotations

import json
import logging
import re

log = logging.getLogger(__name__)


def _parse_verdicts(text: str) -> dict:
    """{ref:int -> verdict:str} from an engine reply. Tolerant of prose/fences."""
    if not text:
        return {}
    out = {}
    try:
        m = re.search(r'\{.*\}', text, re.S)
        data = json.loads(m.group(0)) if m else json.loads(text)
        for v in (data.get('verdicts') or []):
            try:
                out[int(v['ref'])] = str(v.get('verdict', '')).strip().lower()
            except (KeyError, ValueError, TypeError):
                continue
    except Exception:    # noqa: BLE001 - a garbled reply must never crash the send
        pass
    return out


def hold_suspect_reminders(items):
    """Decide who is safe to nudge.

    items: list of dicts, each:
      {'ref': int,            # opaque handle (NOT a name) the caller maps back
       'hours': float,        # today's/this-week's reading that triggered the nudge
       'threshold': float,    # the bar they fell under
       'typical': float|None, # their recent typical tracked hours (None = unknown)
       'mapped': bool,        # is their Time Doctor account CONFIRMED-linked?
       'arrival': str|None}   # first activity time today, if any

    Returns (held_refs:set[int], reason_by_ref:dict[int,str], ai_ran:bool).
    A ref in held_refs must NOT be emailed.
    """
    held, reason = set(), {}
    if not items:
        return held, reason, False

    # 1) Rule gate — an unconfirmed identity is never trusted to accuse.
    ai_items = []
    for it in items:
        if not it.get('mapped'):
            held.add(it['ref'])
            reason[it['ref']] = 'Time Doctor account not confirmed-linked — held for map confirmation'
        else:
            ai_items.append(it)

    if not ai_items:
        return held, reason, False

    # 2) AI gate — two independent engines, off the Claude subscription.
    try:
        from core.ai_assist import deepseek_complete, gemini_complete, is_safe_for_ai
    except Exception:    # noqa: BLE001
        # AI layer unavailable → FAIL-CLOSED: hold every remaining recipient.
        for it in ai_items:
            held.add(it['ref'])
            reason[it['ref']] = 'AI guard unavailable — held (fail-closed, no unverified send)'
        return held, reason, False

    lines = []
    for it in ai_items:
        typ = '?' if it.get('typical') is None else f"{it['typical']:.1f}"
        arr = it.get('arrival') or 'none seen'
        lines.append(f"ref {it['ref']}: today={it['hours']:.1f}h, expected>={it['threshold']:.0f}h, "
                     f"typical_day={typ}h, first_activity={arr}")
    body = '\n'.join(lines)

    system = (
        "You screen Time Doctor 'you are behind on hours' reminders before they email a real "
        "employee. For each ref decide if the LOW reading is a GENUINE shortfall or a likely "
        "DATA ERROR (hours not uploaded yet, or a tracking/identity glitch). A person whose "
        "typical day is much higher than today, or who has no first_activity time, is more "
        "likely a data error — HOLD those. If it looks like a real slow start, it is genuine. "
        'Reply ONLY JSON: {"verdicts":[{"ref":N,"verdict":"genuine|data_error|uncertain"}]}')
    prompt = ("Screen these reminders. genuine = safe to send; data_error = do NOT send:\n\n"
              + body)

    safety = is_safe_for_ai(prompt)
    if not safety.safe or not safety.redacted_text:
        for it in ai_items:
            held.add(it['ref'])
            reason[it['ref']] = 'AI guard could not safely evaluate — held (fail-closed)'
        return held, reason, False
    safe_prompt = safety.redacted_text

    def _run(engine):
        try:
            parsed = _parse_verdicts(engine(safe_prompt, system_prompt=system, timeout=30.0))
            # An empty/garbage reply is NOT a usable answer — treat it as the engine
            # being down, so two garbage replies fail CLOSED (hold), never open.
            return parsed or None
        except Exception:    # noqa: BLE001
            return None

    ds = _run(deepseek_complete)
    gm = _run(gemini_complete)

    if ds is None and gm is None:
        # Both engines down → FAIL-CLOSED.
        for it in ai_items:
            held.add(it['ref'])
            reason[it['ref']] = 'AI guard engines unreachable — held (fail-closed)'
        return held, reason, False

    for it in ai_items:
        verdicts = {(ds or {}).get(it['ref'], ''), (gm or {}).get(it['ref'], '')}
        # Fail-closed: SEND only when an engine POSITIVELY clears it (genuine) and
        # NEITHER flags a data error. 'uncertain', a missing verdict, or a data-error
        # → HOLD. Never email a "you're behind" the engines could not vouch for.
        if 'data_error' in verdicts:
            held.add(it['ref'])
            reason[it['ref']] = 'AI guard flagged a likely data error (hours look wrong)'
        elif 'genuine' not in verdicts:
            held.add(it['ref'])
            reason[it['ref']] = 'AI guard could not positively confirm a real shortfall — held'
    return held, reason, True
