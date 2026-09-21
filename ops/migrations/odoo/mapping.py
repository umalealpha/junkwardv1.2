"""
Field-level mappers and global filters for the Odoo migration.

The constants here are the single source of truth for:
  - which Odoo company to skip (ADIC) — resolved by NAME at runtime,
    no longer hard-coded to id=4 (Odoo company IDs are environment-specific)
  - the date cutoff
  - Odoo internal_group → Omni AccountType translation (modern Odoo schema)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

# ---------------------------------------------------------------------------
# Global filters — applied in every importer's domain
# ---------------------------------------------------------------------------

# Alpha Direct Insurance Company — match by name pattern, not hard-coded ID.
# The CFO's brief said company_id=4 but the actual ID in this Odoo instance
# is environment-specific (id=1 is Quantum Insurance Holdings, not ADIC).
# `resolve_adic_id(client)` looks it up at runtime.
ADIC_NAME_PATTERNS = (
    'alpha direct insurance company',
    'alpha direct insurance co',
    'alpha direct insurance',
)

# Legacy default. Overridden at runtime by `resolve_adic_id`. Kept for any
# back-compat caller; importers should pull the resolved ID from the runner.
ADIC_COMPANY_ID_DEFAULT = 4

def adic_exclude_domain_term(adic_id: int):
    """Build the company_id 'not in' filter using the resolved ADIC id."""
    return ('company_id', 'not in', [adic_id])

# Back-compat alias; importers should now read from the resolved ID.
EXCLUDE_ADIC_DOMAIN_TERM = ('company_id', 'not in', [ADIC_COMPANY_ID_DEFAULT])
ADIC_COMPANY_ID = ADIC_COMPANY_ID_DEFAULT  # legacy

# 31 March 2026 inclusive — CFO directive.
CUTOFF_DATE = date(2026, 3, 31)
CUTOFF_DATE_DOMAIN_TERM = ('date', '<=', CUTOFF_DATE.isoformat())

# Posted-only journal entries.
POSTED_DOMAIN_TERM = ('state', '=', 'posted')

# Active partners.
ACTIVE_PARTNER_DOMAIN_TERM = ('active', '=', True)

# External-ref prefix.
EXTREF_PREFIX = 'odoo'

def make_extref(model: str, odoo_id: int, *suffix: str) -> str:
    """Format: 'odoo:<model>:<id>[:suffix]'."""
    parts = [EXTREF_PREFIX, model, str(odoo_id), *suffix]
    return ':'.join(parts)


def resolve_adic_id(client) -> int | None:
    """
    Look up the ADIC company id from Odoo res.company by name.
    Returns None if no match — caller should treat that as "no ADIC in this
    Odoo" (fail-safe — better to skip nothing than to silently let it through).
    """
    records = client._models.execute_kw(
        client.db, client._uid, client._password,
        'res.company', 'search_read', [[]],
        {'fields': ['id', 'name'], 'limit': 100, 'order': 'id asc'},
    )
    for r in records:
        name = (r.get('name') or '').strip().lower()
        for pat in ADIC_NAME_PATTERNS:
            if pat in name:
                return int(r['id'])
    return None


# ---------------------------------------------------------------------------
# Account type mapping
# ---------------------------------------------------------------------------
# Modern Odoo (v14+) exposes two relevant fields on account.account:
#   internal_group : 'asset' | 'liability' | 'equity' | 'income' | 'expense'
#                    — direct 1:1 to Omni AccountType
#   account_type   : finer-grained Selection (e.g. 'asset_current',
#                    'liability_payable', 'income_other') — kept for sub_type
# We use internal_group for the Omni AccountType assignment.

INTERNAL_GROUP_MAP: dict[str, str] = {
    'asset':     'asset',
    'liability': 'liability',
    'equity':    'equity',
    'income':    'revenue',
    'expense':   'expense',
}

# Legacy user_type_id mapping retained for any pre-v14 Odoo fallback callers.
ACCOUNT_TYPE_MAP: dict[str, str] = {
    'bank and cash':                'asset',
    'receivable':                   'asset',
    'current assets':               'asset',
    'non-current assets':           'asset',
    'prepayments':                  'asset',
    'fixed assets':                 'asset',
    'payable':                      'liability',
    'credit card':                  'liability',
    'current liabilities':          'liability',
    'non-current liabilities':      'liability',
    'equity':                       'equity',
    'current year earnings':        'equity',
    'income':                       'revenue',
    'other income':                 'revenue',
    'expenses':                     'expense',
    'depreciation':                 'expense',
    'cost of revenue':              'expense',
}


def map_internal_group(internal_group: str | None) -> tuple[str, bool]:
    """Modern Odoo → Omni AccountType. Returns (type, recognised)."""
    if not internal_group:
        return ('asset', False)
    key = internal_group.strip().lower()
    if key in INTERNAL_GROUP_MAP:
        return (INTERNAL_GROUP_MAP[key], True)
    return ('asset', False)


def map_account_type(odoo_user_type: str | None) -> tuple[str, bool]:
    """Legacy fallback for pre-v14 Odoo (user_type_id name)."""
    if not odoo_user_type:
        return ('asset', False)
    key = odoo_user_type.strip().lower()
    if key in ACCOUNT_TYPE_MAP:
        return (ACCOUNT_TYPE_MAP[key], True)
    for known, value in ACCOUNT_TYPE_MAP.items():
        if known in key:
            return (value, True)
    return ('asset', False)


# ---------------------------------------------------------------------------
# Result dataclass shared by all importers
# ---------------------------------------------------------------------------

@dataclass
class ImportResult:
    """Per-model summary of one import run."""

    model: str
    fetched: int = 0
    skipped_duplicate: int = 0
    skipped_adic: int = 0
    skipped_post_cutoff: int = 0
    skipped_other: int = 0
    imported: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    def add_error(self, message: str) -> None:
        if len(self.errors) < 10:
            self.errors.append(message)

    def summary(self) -> dict:
        return {
            'model':              self.model,
            'fetched':            self.fetched,
            'imported':           self.imported,
            'skipped_duplicate':  self.skipped_duplicate,
            'skipped_adic':       self.skipped_adic,
            'skipped_post_cutoff': self.skipped_post_cutoff,
            'skipped_other':      self.skipped_other,
            'failed':             self.failed,
            'first_errors':       self.errors,
        }
