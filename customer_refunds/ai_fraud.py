"""
customer_refunds/ai_fraud.py — DeepSeek advisory fraud reviewer.

An ADVISORY reasoning layer on top of the deterministic rule engine (fraud.py).
The rules already flag + block the risky cases; this asks DeepSeek (via omni's
reasoning_complete cascade — local Ollama → DeepSeek → …) to reason over the
signals and give a risk opinion + why, to help the human reviewer.

PII RULE (AD-POL-AI-GOV-001): only PII-FREE data leaves — signal CODES +
severities, an amount BAND (not the exact figure), the segment, and a short
reason category. NEVER the customer name, account number, policy number, or the
signal DETAIL strings (which can carry last-4 / policy). Belt-and-braces:
is_safe_for_ai() must clear the prompt, and the provider is @firewall-wrapped.
"""
from __future__ import annotations

import json
from decimal import Decimal

PROMPT = (
    "You are a fraud reviewer for an insurance company's CUSTOMER REFUNDS. "
    "Refunds pay money OUT to a customer bank account. Below are the automated "
    "fraud SIGNALS already computed for one refund (codes + severity), plus its "
    "segment, amount band and reason category. No personal data is included. "
    "Give an independent opinion. Respond ONLY as JSON: "
    '{"risk":"low|medium|high","reasons":["short bullet",...],'
    '"recommend":"approve|review|hold"}. Signals:\n'
)


def _band(amount) -> str:
    a = Decimal(str(amount or 0))
    if a < 100: return '<P100'
    if a < 1000: return 'P100–1k'
    if a < 10000: return 'P1k–10k'
    if a < 50000: return 'P10k–50k'
    return '>P50k'


def deepseek_fraud_review(refund) -> dict:
    """Run the AI reviewer over the refund's rule-based signals. PII-free.
    Returns {ok, risk?, reasons?, recommend?, engine?, reason?} and stamps
    refund.ai_fraud_review. Never raises — AI down / unsafe → ok:False."""
    from core.ai_assist import DeepSeekUnavailable, is_safe_for_ai, reasoning_complete
    from django.utils import timezone

    flags = refund.fraud_flags or []
    payload = {
        'segment': refund.segment,
        'reason_category': (refund.reason or '')[:60],
        'amount_band': _band(refund.refund_amount),
        'signals': [{'code': f.get('code'), 'severity': f.get('severity')} for f in flags],
        'signal_count': len(flags),
    }
    prompt = PROMPT + json.dumps(payload)

    # Guard: never send anything the firewall flags as PII (should be clean).
    if not is_safe_for_ai(prompt).safe:
        review = {'ok': False, 'reason': 'blocked_pii'}
        _stash(refund, review, timezone)
        return review

    try:
        out = reasoning_complete(prompt, response_format={'type': 'json_object'},
                                 feature='refund_fraud')
    except DeepSeekUnavailable:
        review = {'ok': False, 'reason': 'ai_unavailable'}
        _stash(refund, review, timezone)
        return review

    try:
        data = json.loads(out)
    except (ValueError, TypeError):
        review = {'ok': False, 'reason': 'unparseable'}
        _stash(refund, review, timezone)
        return review

    review = {
        'ok': True,
        'risk': data.get('risk'),
        'reasons': data.get('reasons') or [],
        'recommend': data.get('recommend'),
        'engine': 'reasoning_complete',
    }
    _stash(refund, review, timezone)
    return review


def _stash(refund, review, timezone):
    refund.ai_fraud_review = {**review, 'at': timezone.now().isoformat()}
    if refund.pk:
        refund.save(update_fields=['ai_fraud_review', 'updated_at'])
