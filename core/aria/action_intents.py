"""
core/aria/action_intents.py — short-lived, single-use server-side confirmation
tokens for Aria action tools. A tool can inspect a record and issue a token
without changing anything. The real action is only allowed when that exact
token is consumed against the *same* user, action, reference, and record
fingerprint.
"""

from __future__ import annotations

import json
import secrets

from django.core.cache import cache
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner

INTENT_TTL_SECONDS = 120

_signer = TimestampSigner(salt='aria-action-intent')


def issue_intent(*, user, action: str, ref: str, fingerprint: str) -> str:
    """Sign a short-lived confirmation token for a prospective action.

    The token does not change anything. It only proves that the system
    inspected the record and that the user confirmed the displayed details.
    """
    payload = {
        'u': user.pk,
        'a': action,
        'r': ref,
        'f': fingerprint,
        'n': secrets.token_hex(8),
    }
    return _signer.sign(json.dumps(payload, separators=(',', ':')))


def consume_intent(token: str, *, user, action: str, ref: str,
                   fingerprint: str) -> str | None:
    """Validate and consume a confirmation token.

    Returns ``None`` when the token is valid and has been successfully
    consumed. Otherwise returns a plain-English error string and does NOT
    consume the token.
    """
    if not token:
        return 'No confirmation token was supplied.'

    try:
        raw = _signer.unsign(token, max_age=INTENT_TTL_SECONDS)
    except SignatureExpired:
        return 'Your confirmation token has expired — please confirm again.'
    except BadSignature:
        return 'Invalid confirmation token — please confirm again.'

    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return 'Invalid confirmation token payload.'

    if not isinstance(payload, dict):
        return 'Invalid confirmation token payload.'

    if payload.get('u') != user.pk:
        return 'This confirmation token belongs to another user.'

    if payload.get('a') != action:
        return 'This confirmation token is not for this action.'

    if payload.get('r') != ref:
        return 'This confirmation token is for a different payment.'

    if payload.get('f') != fingerprint:
        return 'Payment has changed since you confirmed — look again.'

    nonce = payload.get('n')
    if not nonce:
        return 'Invalid confirmation token payload.'

    # Single-use: cache.add is atomic at the cache layer. The lifetime is twice
    # the token lifetime so a used token cannot be reused after expiry races.
    if not cache.add(f'aria-intent:{nonce}', 1, INTENT_TTL_SECONDS * 2):
        return 'This confirmation token has already been used.'

    return None


def payment_fingerprint(p, text: str = '') -> str:
    """Status, total, payee, bank account, last-change time and a hash of the
    reason/notes shown on the card. Any approval or edit changes it, so a token
    can never be replayed onto a payment that has moved on — this, under the row
    lock, is the duplicate guard that holds across worker processes (the cache
    check above is per-process on LocMemCache). The text hash means the reason
    recorded is the one the user saw."""
    import hashlib
    bank = getattr(p, 'bank_account_number', '') or getattr(p, 'account_number', '') or ''
    h = hashlib.sha256((text or '').encode()).hexdigest()[:16]
    return f"{p.status}|{p.total}|{p.payee}|{bank}|{p.updated_at.isoformat()}|{h}"
