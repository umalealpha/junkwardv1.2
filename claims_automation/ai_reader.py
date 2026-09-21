"""claims_automation/ai_reader.py — Wave 3, the AI claim reader.

When the customer's form arrives, write a short summary and a suggested next
step for the claims handler. Same rules as integrations/claim_summary_ai.py:

  * The AI writes WORDS only. The triage verdict and every flag are decided by
    rule (`triage()` below); the AI may never turn an exception into a
    straight-through claim.
  * Identity is stripped before anything leaves the box (name, policy number,
    registration, licence, police reference, phone, email), then
    is_safe_for_ai() sweeps the rest, and reasoning_complete() tries the local
    model first. Unsafe text = no AI call at all.
  * Degrades to nothing. An AI outage returns empty words; the rule-based
    triage and flags still stand, and no claim is ever held up by it.
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

_MAX_WORDS = 150

# The only customer-form answers the AI may read (Graphite ClaimFormPrefill keys).
_AI_SAFE_ANSWER_KEYS = (
    "loss_date", "loss_time", "loss_place", "reported_police",
    "damage_description", "glass_item", "glass_cause", "items_lost", "ownership_proof",
)

_SYSTEM = (
    "You are a claims assistant at a Botswana insurer. You are given the facts of "
    "one claim with the customer's identity removed. Write a plain-English summary "
    "of at most 150 words for the claims handler, then ONE suggested next step. "
    "Use ONLY the facts given; never invent amounts, names or dates. You never "
    "decide a claim. Return strict JSON with exactly these keys: "
    '{"summary": str, "next_step": str}.'
)


def triage(premium_light: str, flags: list, missing_answers: list) -> tuple[str, list]:
    """Rule-based. Straight through ONLY when the premium is confirmed green,
    no policy-breach flag fired and nothing required is missing."""
    reasons = []
    if (premium_light or "").lower() != "green":
        reasons.append(
            "Premium not confirmed as paid up"
            + (f" ({premium_light})" if premium_light else "")
        )
    for f in flags or []:
        reasons.append(f.get("label") or f.get("code") or "Policy flag")
    if missing_answers:
        reasons.append("Missing on the form: " + ", ".join(missing_answers[:6]))
    return ("exception" if reasons else "straight_through"), reasons


def _strip_identity(text: str, facts: dict) -> str:
    insured = facts.get("insured") or {}
    policy = facts.get("policy") or {}
    vehicle = facts.get("vehicle") or {}
    answers = facts.get("answers") or {}
    secrets = [
        (insured.get("name"), "the insured"),
        (insured.get("email"), "[email]"),
        (insured.get("phone"), "[phone]"),
        (policy.get("number"), "the policy"),
        (vehicle.get("registration"), "the vehicle"),
        (vehicle.get("chassis"), "the vehicle"),
        (answers.get("driver_name"), "the driver"),
        (answers.get("driver_licence"), "[licence]"),
        (answers.get("police_ref"), "[police reference]"),
    ]
    for value, repl in secrets:
        v = str(value or "").strip()
        if len(v) >= 3:
            text = re.sub(re.escape(v), repl, text, flags=re.IGNORECASE)
    return text


def build_facts_text(facts: dict, flags: list) -> str:
    claim = facts.get("claim") or {}
    policy = facts.get("policy") or {}
    vehicle = facts.get("vehicle") or {}
    premium = facts.get("premium") or {}
    answers = facts.get("answers") or {}
    parts = [
        f"Claim type: {claim.get('type') or 'unknown'}.",
        f"Date of loss: {claim.get('date_of_loss') or 'not given'}; reported {claim.get('reported_on') or 'unknown'}.",
        f"Product: {policy.get('product') or 'unknown'}; cover {policy.get('inception') or '?'} to {policy.get('expiry') or '?'}.",
        f"Sum insured: {policy.get('sum_insured') or 'not recorded'}; excess: {policy.get('excess') or 'not recorded'}.",
        f"Premium check: {premium.get('light') or 'not run'} — {premium.get('status') or ''}.",
    ]
    if vehicle.get("make") or vehicle.get("model"):
        parts.append(
            f"Vehicle: {vehicle.get('make') or ''} {vehicle.get('model') or ''} {vehicle.get('year') or ''}."
        )
    if claim.get("description"):
        parts.append(f"Description of loss: {claim['description']}")
    # ALLOW-list, never a deny-list: answers that exist to collect OTHER people's
    # details (third_party, injuries, vehicle_location, driver_name ...) are PII by
    # construction, and is_safe_for_ai masks neither names nor 8-digit local
    # phones (Fable review 19-Sep-2026, checklist H104).
    for k in _AI_SAFE_ANSWER_KEYS:
        v = answers.get(k)
        if v not in (None, ""):
            parts.append(f"{k.replace('_', ' ')}: {str(v)[:600]}")
    if flags:
        parts.append("Rule flags: " + "; ".join(f.get("label", "") for f in flags))
    return "\n".join(parts)


def _clamp(text: str, limit: int = _MAX_WORDS) -> str:
    words = (text or "").split()
    return (
        (text or "").strip() if len(words) <= limit else " ".join(words[:limit]) + "…"
    )


def read_claim(facts: dict, flags: list) -> tuple[str, str, str]:
    """(summary, next_step, engine). ('', '', '') on any failure — never raises."""
    from core.ai_assist import is_safe_for_ai, reasoning_complete
    text = _strip_identity(build_facts_text(facts, flags), facts)
    report = is_safe_for_ai(text)
    if not report.safe:
        logger.info("claims_automation: facts not safe for AI — skipped")
        return "", "", ""
    try:
        out = reasoning_complete(
            "Read this claim and help the handler.\n\n" + report.redacted_text,
            system_prompt=_SYSTEM,
            feature="claims_automation_reader",
            response_format="json_object",
            max_tokens=500,
        )
    except Exception as exc:  # noqa: BLE001 — AI down never blocks a claim
        logger.warning("claims_automation reader failed: %s", exc)
        return "", "", ""
    summary, step = "", ""
    try:
        data = json.loads(out)
        summary = str(data.get("summary") or "").strip()
        step = str(data.get("next_step") or "").strip()
    except (ValueError, TypeError):
        summary = (out or "").strip()
    summary = _clamp(summary)
    if not summary:
        return "", "", ""
    return summary, step[:400], "reasoning_complete"
