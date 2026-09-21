"""commissions/amend_audit.py — audit trail for staff amendments to a
commission submission's lines (CFO 2026-07-17).

When a staff member disagrees with a premium/rate and edits the collected
amount, rate or commission on their DRAFT/REJECTED submission, we record ONE
CommissionAmendment row: who, before/after line snapshots, gross before/after,
and an Aria (DeepSeek) plain-English note. The note is best-effort — a
deterministic fallback is used if the AI is unavailable, so the trail is NEVER
blocked on DeepSeek.
"""
from __future__ import annotations

import logging
from decimal import Decimal

log = logging.getLogger("commissions")

# The line fields a staff amendment can touch — snapshot these for the trail.
_FIELDS = ("policy_number", "client_name", "transaction_type",
           "amount_collected", "annualised_premium", "commission_rate",
           "amount_applicable", "commission_amount")


def snapshot_lines(submission) -> list[dict]:
    """Stable, JSON-safe snapshot of a submission's lines for before/after diff.
    Decimals -> str so equality compares exactly and the value stores cleanly."""
    out = []
    for ln in submission.lines.all().order_by("created_at"):
        row = {}
        for f in _FIELDS:
            v = getattr(ln, f)
            row[f] = str(v) if isinstance(v, Decimal) else ("" if v is None else v)
        out.append(row)
    return out


def _changed_fields(old: list[dict], new: list[dict]) -> list[str]:
    """Human list of what moved, matched by policy number then by position."""
    notes = []
    by_pol_old = {r.get("policy_number") or f"#{i}": r for i, r in enumerate(old)}
    by_pol_new = {r.get("policy_number") or f"#{i}": r for i, r in enumerate(new)}
    for key, nrow in by_pol_new.items():
        orow = by_pol_old.get(key)
        label = (nrow.get("client_name") or nrow.get("policy_number") or key)
        if orow is None:
            notes.append(f"added line '{label}' (commission {nrow.get('commission_amount')})")
            continue
        for f in ("amount_collected", "commission_rate", "commission_amount", "amount_applicable"):
            if str(orow.get(f)) != str(nrow.get(f)):
                notes.append(f"'{label}' {f.replace('_', ' ')} {orow.get(f)} → {nrow.get(f)}")
    for key, orow in by_pol_old.items():
        if key not in by_pol_new:
            label = (orow.get("client_name") or orow.get("policy_number") or key)
            notes.append(f"removed line '{label}' (was {orow.get('commission_amount')})")
    return notes


def _fallback_note(agent_name, old, new, old_gross, new_gross) -> str:
    changes = _changed_fields(old, new)
    head = f"{agent_name} amended their commission (gross {old_gross} → {new_gross})."
    return head + (" " + "; ".join(changes) if changes else "")


def _aria_note(agent_name, old, new, old_gross, new_gross) -> str:
    """Ask Aria (DeepSeek) for a one-line plain-English audit note. Falls back to
    the deterministic summary on any failure — never raises."""
    fallback = _fallback_note(agent_name, old, new, old_gross, new_gross)
    try:
        from core.ai_assist import deepseek_complete
        prompt = (
            "You are Aria, writing a one-line audit-trail note for an insurance "
            "commission system. A sales agent has edited their monthly commission. "
            f"Agent: {agent_name}. Gross commission before: {old_gross}, after: {new_gross}.\n"
            f"Line items BEFORE: {old}\nLine items AFTER: {new}\n"
            "Write ONE short, factual sentence stating exactly what the agent "
            "changed (amounts collected, rates, or commission), in plain English. "
            "No preamble, no advice — just the change."
        )
        note = deepseek_complete(
            prompt,
            system_prompt="You write terse, factual financial audit notes.",
            max_tokens=120,
            timeout=20.0,
        )
        note = (note or "").strip()
        return note or fallback
    except Exception:
        log.info("Aria amend-note unavailable; using deterministic fallback", exc_info=True)
        return fallback


def record_amendment(submission, actor, old_lines, new_lines, old_gross, new_gross):
    """Write one CommissionAmendment row. Called only when the lines actually
    changed. Best-effort note; the row itself is always written."""
    from .models import CommissionAmendment
    agent_name = submission.agent.name if submission.agent_id else "Agent"
    note = _aria_note(agent_name, old_lines, new_lines, old_gross, new_gross)
    return CommissionAmendment.objects.create(
        submission=submission,
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        old_lines=old_lines,
        new_lines=new_lines,
        old_gross=old_gross or Decimal("0.00"),
        new_gross=new_gross or Decimal("0.00"),
        note=note[:2000],
    )
