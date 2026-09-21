"""
ledger/locks.py — Historical-period hard lock.

CFO directive (CRITICAL_INSTRUCTION_FOR_CLAUDE_CODE.docx, 2026-05-17):
The Alpha Direct Insurance Company financial figures for FY25 (full year
ending June 2025) and FY26-9M (July 2025 → March 2026) are LOCKED. They
match the authoritative MA workbook + Odoo TB and must NOT be altered by
the UI, the API, ad-hoc migrations, or anyone else without the override
password.

A small whitelist of system actions can bypass the lock:
- The CSV importer itself (`source_type='tb_csv_import'`).
- Any caller that has set `OMNI_FINANCIAL_LOCK_OVERRIDE` in env and passes
  that same value (there is NO hardcoded default — fail-closed).
- Any caller wrapped in `with financial_lock_override(value): ...` that
  passes the same override.
"""

from __future__ import annotations

import contextvars
import os
from contextlib import contextmanager
from datetime import date
from typing import Iterable

from django.core.exceptions import ValidationError


LOCK_COMPANY_CODE = 'ADIC'

# (start_inclusive, end_inclusive, label)
LOCKED_PERIODS: list[tuple[date, date, str]] = [
    (date(2024, 7, 1), date(2025, 6, 30), 'FY25 (Jul 2024 – Jun 2025)'),
    (date(2025, 7, 1), date(2026, 3, 31), 'FY26-9M (Jul 2025 – Mar 2026)'),
]

# Source types that bypass the lock — these are the system-owned writers
# that produced the locked values in the first place. Adding to this list
# requires CFO sign-off.
WHITELISTED_SOURCE_TYPES: set[str] = {
    'tb_csv_import',
}

# Override password — supplied ONLY via the OMNI_FINANCIAL_LOCK_OVERRIDE env
# var in production (set in /etc/alpha-finance/.env), so it can be rotated
# without a code deploy. SECURITY (2026-07-17 audit): there is deliberately
# NO hardcoded fallback — a value committed to the repo is public and would
# let anyone with source access bypass the historical-financials lock. If the
# env var is unset the lock is fully enforced (fail-closed) and no password
# can unlock it.
import hmac


_override_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    'omni_financial_lock_override', default=None,
)


def _required_override() -> str | None:
    val = os.environ.get('OMNI_FINANCIAL_LOCK_OVERRIDE', '')
    return val or None


def _override_is_supplied(override_value: str | None) -> bool:
    """True if the caller has supplied the correct override value.

    Fail-closed: if no override is configured in the environment, NO value
    can satisfy the lock (returns False for every input)."""
    required = _required_override()
    if required is None:
        return False
    if override_value is None:
        override_value = _override_ctx.get()
    if override_value is None:
        # Fall back to env-level override (rare — usually set at request scope)
        env_supplied = os.environ.get('OMNI_FINANCIAL_LOCK_OVERRIDE_USE')
        if env_supplied is None:
            return False
        return hmac.compare_digest(env_supplied, required)
    return hmac.compare_digest(str(override_value), required)


@contextmanager
def financial_lock_override(value: str):
    """Open a context that allows lock-protected writes.

    Usage:
        with financial_lock_override(os.environ['OMNI_FINANCIAL_LOCK_OVERRIDE']):
            je.save()
    """
    token = _override_ctx.set(value)
    try:
        yield
    finally:
        _override_ctx.reset(token)


def _entry_date_is_locked(entry_date: date) -> tuple[bool, str | None]:
    for start, end, label in LOCKED_PERIODS:
        if start <= entry_date <= end:
            return True, label
    return False, None


def _bypass_active() -> bool:
    """CFO directive 2026-05-19 — global kill switch for the historical lock.

    `OMNI_FINANCIAL_LOCK_BYPASS=1` disables the lock entirely for the
    Manus AI re-import window. Must be reverted to '' once the upload
    completes.
    """
    return (os.environ.get('OMNI_FINANCIAL_LOCK_BYPASS') or '').strip() == '1'


def assert_can_write_journal_entry(
    *,
    entry_date: date,
    company_code: str | None,
    source_type: str,
    override_value: str | None = None,
) -> None:
    """Raise ValidationError if the JE write is blocked by the historical lock.

    Pass-through cases (no error):
    - OMNI_FINANCIAL_LOCK_BYPASS=1 in env (CFO directive 2026-05-19).
    - Company is not ADIC (lock only protects ADIC).
    - entry_date is outside every LOCKED_PERIODS range.
    - source_type is in WHITELISTED_SOURCE_TYPES.
    - override_value (or context / env) equals the configured override.
    """
    if _bypass_active():
        import logging
        logging.getLogger(__name__).warning(
            'OMNI_FINANCIAL_LOCK_BYPASS=1 — model-level lock skipped '
            '(entry_date=%s company=%s source=%s).',
            entry_date, company_code, source_type,
        )
        return
    if company_code != LOCK_COMPANY_CODE:
        return
    locked, label = _entry_date_is_locked(entry_date)
    if not locked:
        return
    if source_type in WHITELISTED_SOURCE_TYPES:
        return
    if _override_is_supplied(override_value):
        return

    raise ValidationError(
        f'LOCKED: Historical financial data for Alpha Direct Insurance '
        f'Company cannot be modified without the override password. '
        f'Period locked: {label}. '
        f'Set OMNI_FINANCIAL_LOCK_OVERRIDE or use the '
        f'`financial_lock_override("...")` context manager.'
    )
