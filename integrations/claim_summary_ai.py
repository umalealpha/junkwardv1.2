"""
integrations/claim_summary_ai.py — the WORDS half of the claim insight
(CFO 2026-08-31, Claim-Description-Spec). A <=200-word plain-English summary of a
claim plus a SOFT pay/hold opinion, written by our own AI over PII-redacted facts.

Hard rules (Claim-Description-Spec + AD-POL-AI-GOV-001):
  * AI writes WORDS only. Every number and every hard flag is computed by rule in
    integrations/claim_insight.py; the AI never computes an amount and never
    decides a claim — it may only SUGGEST "PAY" or "HOLD and check" with a reason.
  * PII never leaves un-anonymised. The facts go through is_safe_for_ai() first
    (local-first cascade: reasoning_complete tries Ollama before any cloud engine,
    and only the redacted text is ever sent). If the text cannot be made safe, we
    skip the AI and store nothing — the deterministic facts still show.
  * Degrades gracefully. Any AI outage returns ('', '', '', '') so the summary is
    simply absent; the facts box is never affected and a payment is never blocked.
"""
from __future__ import annotations

import json
import logging
import re

from integrations.claim_insight import build_facts_text, _hard_flags

logger = logging.getLogger(__name__)

_MAX_WORDS = 200

_SYSTEM = (
    "You are a claims assistant for an insurer, helping a finance reviewer decide "
    "a claim payment. You are given only the claim's facts. Write a clear, plain-"
    "English summary of at most 200 words, then give a SOFT suggestion: \"PAY\" or "
    "\"HOLD\" (hold-and-check), with a one-line reason. You NEVER make the decision "
    "— the finance manager decides; your suggestion is advisory. Use ONLY the facts "
    "given; never invent figures, names or dates. Return strict JSON with exactly "
    "these keys: {\"summary\": str, \"suggestion\": \"PAY\"|\"HOLD\", \"reason\": str}."
)


def _clamp_words(text: str, limit: int = _MAX_WORDS) -> str:
    words = (text or '').split()
    if len(words) <= limit:
        return (text or '').strip()
    return ' '.join(words[:limit]).rstrip() + '…'


def _facts_for_ai(claim, currency: str) -> str:
    """The claim facts + deterministic risk flags, as the grounding the AI
    narrates — with claimant IDENTITY stripped so no name / policy number / handler
    can reach an engine on the Ollama-down cloud-fallback path. This is deliberate
    and load-bearing: is_safe_for_ai() is regex-only (national ID / long digits /
    email / phone) and does NOT mask names, and core.pii_firewall only tokenises names it
    knows (payroll + rewards), never Graphite claimants. Identity adds nothing to a
    PAY/HOLD narrative; the real name stays in build_facts_text() for the Omni UI /
    line description, which never leaves the box. Residual free-text (damage_cause)
    is still covered by is_safe_for_ai()'s structured-PII sweep in summarise_claim.
    No amounts are asked of the AI — they are stated here."""
    text = build_facts_text(claim, currency)
    # Case-INsensitive: damage_cause is routinely ALL-CAPS, so a claimant name that
    # appears there in a different case than customer_name must still be stripped.
    if claim.customer_name:
        text = re.sub(re.escape(claim.customer_name), 'the insured', text, flags=re.IGNORECASE)
    if claim.claim_handler:
        text = re.sub(re.escape(claim.claim_handler), 'the claim handler', text, flags=re.IGNORECASE)
    if claim.policy_number:
        text = re.sub(re.escape(claim.policy_number), 'the policy', text, flags=re.IGNORECASE)
    flags = _hard_flags(claim, line_amount=None, currency=currency, entity='', exclude_pk=None)
    if flags:
        text += ' Flags: ' + ' '.join(f['label'] for f in flags)
    return text


def summarise_claim(claim, currency: str = 'BWP') -> tuple[str, str, str, str]:
    """Return (summary, suggestion, reason, engine). ('', '', '', '') on any
    failure or unsafe text — never raises."""
    from core.ai_assist import is_safe_for_ai, reasoning_complete, DeepSeekUnavailable

    facts = _facts_for_ai(claim, currency)
    report = is_safe_for_ai(facts)
    if not report.safe:
        logger.info('claim_summary: facts not safe to send for %s — skipping AI', claim.claim_number)
        return '', '', '', ''

    prompt = (
        'Summarise this claim for a finance reviewer and give a soft PAY/HOLD '
        'suggestion with a one-line reason.\n\nClaim facts:\n' + report.redacted_text
    )
    try:
        out = reasoning_complete(
            prompt, system_prompt=_SYSTEM, feature='claim_summary',
            response_format='json_object', max_tokens=400,
        )
    except DeepSeekUnavailable as exc:
        logger.warning('claim_summary: all AI engines down (%s)', exc)
        return '', '', '', ''
    except Exception as exc:  # noqa: BLE001 — any AI failure -> no summary
        logger.warning('claim_summary failed for %s: %s', claim.claim_number, exc)
        return '', '', '', ''

    summary, suggestion, reason = '', '', ''
    try:
        data = json.loads(out)
        summary = str(data.get('summary', '') or '').strip()
        suggestion = str(data.get('suggestion', '') or '').strip().upper()
        reason = str(data.get('reason', '') or '').strip()
    except (ValueError, TypeError):
        # Model ignored JSON mode — keep the prose as the summary, no opinion.
        summary = (out or '').strip()

    if suggestion not in ('PAY', 'HOLD'):
        suggestion = ''
    summary = _clamp_words(summary)
    reason = reason[:200]
    if not summary:
        return '', '', '', ''
    return summary, suggestion, reason, 'reasoning_complete'
