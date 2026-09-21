"""agent_portal/bank_unlock.py — password gate on REVEALING agent bank account
numbers.

Motlatsi Molefe 2026-07-22: "Can we create a password to restrict access to the
account numbers so that only authorized users can view them?"

This sits ON TOP of the manager gate (agent_portal.access.IsAgentPortalManager):
only the five named managers reach the bank endpoints at all. This adds a SECOND
factor — even a manager must enter the reveal password before full account
numbers are returned; otherwise they see only the masked form (••••1234).

Fail-closed: if OMNI_AGENT_BANK_PASSWORD is unset, numbers stay masked for
everyone — there is NO hardcoded fallback (mirrors core/hris_unlock.py after the
2026-06-09 audit). The unlock sticks for a short window (default 4h) per user,
stored on UserProfile.agent_bank_unlocked_until, then auto-expires.
"""
from __future__ import annotations

import hmac
import os
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

DEFAULT_UNLOCK_HOURS = 4   # bank numbers are more sensitive than HRIS → shorter window

# CFO directive 2026-07-22: revealing full account numbers is restricted to the
# CFO, CEO and COO — even the other portal managers see only the masked form.
# Env-overridable (AGENT_BANK_REVEAL_EMAILS, comma-separated) for no-deploy change.
DEFAULT_REVEAL_EMAILS = {
    'pganesharajah@alphadirect.co.bw',   # CFO — Prathap Ganesharajah
    'aiyer@alphadirect.co.bw',           # CEO — Arun Iyer
    'arjuniyer@alphadirect.co.bw',       # COO — Arjun Iyer
}


def reveal_emails() -> set:
    base = {e.lower() for e in DEFAULT_REVEAL_EMAILS}
    extra = os.environ.get('AGENT_BANK_REVEAL_EMAILS', '')
    base |= {e.strip().lower() for e in extra.split(',') if e.strip()}
    return base


def can_reveal(user) -> bool:
    """Only the CFO / CEO / COO may unmask account numbers."""
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    return (getattr(user, 'email', '') or '').strip().lower() in reveal_emails()


def _password_from_vault() -> str:
    """Read the reveal password from the CFO Secrets Vault (entry named
    'AGENT_BANK_PASSWORD', Fernet-encrypted) so the CFO can set/rotate it
    in-browser (Settings → Vault) instead of editing the server env — same
    pattern as TIMEDOCTOR_TOKEN. Never raises; a miss yields '' (fail-closed)."""
    try:
        from core.models import VaultSecret
        s = VaultSecret.objects.filter(name__iexact='AGENT_BANK_PASSWORD').first()
        return (s.reveal() if s else '') or ''
    except Exception:    # noqa: BLE001
        return ''


def bank_password() -> str:
    """Live reveal password. Env OMNI_AGENT_BANK_PASSWORD wins, then
    settings.AGENT_BANK_PASSWORD, then the CFO Secrets Vault entry
    'AGENT_BANK_PASSWORD'. Empty = fail-closed (nothing ever unlocks)."""
    env = (os.environ.get('OMNI_AGENT_BANK_PASSWORD') or '').strip()
    if env:
        return env
    s = (getattr(settings, 'AGENT_BANK_PASSWORD', '') or '').strip()
    if s:
        return s
    return _password_from_vault()


def unlock_hours() -> int:
    raw = (os.environ.get('OMNI_AGENT_BANK_UNLOCK_HOURS')
           or getattr(settings, 'AGENT_BANK_UNLOCK_HOURS', '') or DEFAULT_UNLOCK_HOURS)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return DEFAULT_UNLOCK_HOURS


def reveal_configured() -> bool:
    return bool(bank_password())


def _profile_for(user):
    from core.models import UserProfile
    p = getattr(user, 'profile', None)
    if p is None:
        p, _ = UserProfile.objects.get_or_create(user=user)
    return p


def is_bank_unlocked(user) -> bool:
    """True only if this user is reveal-authorised (CFO/CEO/COO) AND entered the
    reveal password within the window. No role bypass on the password — even the
    CFO types it (that is the point of the request)."""
    if not can_reveal(user):
        return False
    p = getattr(user, 'profile', None)
    if p is None:
        return False
    until = getattr(p, 'agent_bank_unlocked_until', None)
    return bool(until and until > timezone.now())


def password_ok(submitted) -> bool:
    expected = bank_password()
    if not expected:
        return False   # fail-closed: no server password configured
    return hmac.compare_digest(str(submitted or '').strip(), expected)


def unlock(user):
    p = _profile_for(user)
    until = timezone.now() + timedelta(hours=unlock_hours())
    p.agent_bank_unlocked_until = until
    p.save(update_fields=['agent_bank_unlocked_until'])
    return until


def lock(user):
    p = _profile_for(user)
    p.agent_bank_unlocked_until = None
    p.save(update_fields=['agent_bank_unlocked_until'])


def mask_account(acc) -> str:
    """Mask a bank account number to its last 4 digits: '••••1234'."""
    acc = (acc or '').strip()
    if not acc:
        return ''
    if len(acc) <= 4:
        return '••••'
    return '••••' + acc[-4:]
