"""core/vault_crypto.py — Fernet encryption for the CFO Secrets Vault.

Secrets stored in the vault (HRIS password, paygate-portal logins, integration
credentials, etc.) are encrypted at rest with Fernet (AES-128-CBC + HMAC).
Plaintext is NEVER written to the DB and is only returned by the explicit,
audited reveal endpoint.

Key source (in priority order):
  1. env VAULT_FERNET_KEY  — a urlsafe-base64 32-byte Fernet key (preferred;
     set it in /etc/alpha-finance/.env for a blast-radius separate from
     SECRET_KEY).
  2. settings.VAULT_FERNET_KEY.
  3. Derived deterministically from settings.SECRET_KEY (sha256 -> urlsafe
     base64). Works with zero ops, at the cost of sharing SECRET_KEY's blast
     radius. Rotating to a dedicated key later requires re-encrypting rows
     (decrypt with old, encrypt with new).
"""
from __future__ import annotations

import base64
import functools
import hashlib
import os

from django.conf import settings
from cryptography.fernet import Fernet, InvalidToken


@functools.lru_cache(maxsize=1)
def _fernet() -> Fernet:
    raw = (
        os.environ.get('VAULT_FERNET_KEY')
        or getattr(settings, 'VAULT_FERNET_KEY', '')
        or ''
    )
    raw = raw.strip() if isinstance(raw, str) else raw
    if raw:
        return Fernet(raw.encode() if isinstance(raw, str) else raw)
    # Deterministic fallback derived from SECRET_KEY.
    derived = base64.urlsafe_b64encode(
        hashlib.sha256(settings.SECRET_KEY.encode()).digest()
    )
    return Fernet(derived)


def encrypt(plaintext: str) -> str:
    """Encrypt a plaintext secret → Fernet token (str). Empty in → empty out."""
    if plaintext is None:
        plaintext = ''
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    """Decrypt a Fernet token → plaintext. Returns '' on tamper/empty/bad-key."""
    if not token:
        return ''
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError, TypeError):
        return ''
