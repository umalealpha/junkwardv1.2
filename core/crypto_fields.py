"""
core/crypto_fields.py — transparent field-level encryption at rest (DPA audit S-5).

EncryptedCharField stores its value Fernet-encrypted (via core.vault_crypto) but is
transparent to callers — they read and write plaintext. It is READ-TOLERANT: a value
without the `enc::` marker (a not-yet-migrated plaintext row) is returned as-is, so
deploying the field never breaks existing data, and the back-fill can run gradually.

NOTE: Fernet is non-deterministic, so encrypted fields cannot be used in exact-match
filters / DRF search. Only apply to fields that are never filtered/searched (verified
for payroll national_id + bank_account_no). CFO directive 2026-07-20.
"""
from __future__ import annotations

from django.db import models

from core import vault_crypto

_PREFIX = 'enc::'


def is_encrypted(value) -> bool:
    return isinstance(value, str) and value.startswith(_PREFIX)


class EncryptedCharField(models.CharField):
    """CharField encrypted at rest. Transparent + read-tolerant (see module doc)."""

    def _decrypt(self, value):
        if is_encrypted(value):
            return vault_crypto.decrypt(value[len(_PREFIX):])
        return value  # legacy plaintext — pass through unchanged

    def from_db_value(self, value, expression, connection):
        if value is None:
            return value
        return self._decrypt(value)

    def to_python(self, value):
        if value is None:
            return value
        return self._decrypt(value)

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value is None or value == '' or is_encrypted(value):
            return value
        return _PREFIX + vault_crypto.encrypt(value)
