"""Chart of Accounts importer — Odoo account.account → ledger.Account."""

from __future__ import annotations

import logging

from django.db import transaction

from core.models import Currency
from ledger.models import Account
from ops.migrations.odoo.client import OdooClient
from ops.migrations.odoo.mapping import (
    ImportResult,
    make_extref,
    map_internal_group,
    adic_exclude_domain_term,
)

logger = logging.getLogger(__name__)

ODOO_MODEL = 'account.account'
# Modern Odoo (v14+): account_type (Selection) + internal_group (Char).
# internal_group is the direct 1:1 to Omni's AccountType.
ODOO_FIELDS = [
    'id', 'code', 'name', 'account_type', 'internal_group',
    'currency_id', 'deprecated', 'company_id',
]


def _coerce_id(value) -> int | None:
    """Odoo many2one returns [id, name] or False."""
    if isinstance(value, (list, tuple)) and value:
        return int(value[0])
    return None


def _coerce_name(value) -> str:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return str(value[1])
    return ''


# Map Odoo's fine-grained `account_type` Selection values to the canonical
# sub_type names omni's reports / CoA page expect. Without this, expense rows
# imported as "other_asset" or asset rows as "asset_current" land in the
# wrong BS/PL bucket and the CoA page shows the wrong sub-type label.
_ODOO_SUBTYPE_MAP = {
    'asset_receivable':       'current_asset',
    'asset_cash':             'bank',
    'asset_current':          'current_asset',
    'asset_non_current':      'fixed_asset',
    'asset_fixed':            'fixed_asset',
    'asset_prepayments':      'current_asset',
    'liability_payable':      'current_liability',
    'liability_credit_card':  'current_liability',
    'liability_current':      'current_liability',
    'liability_non_current':  'long_term_liability',
    'equity':                 'equity',
    'equity_unaffected':      'retained_earnings',
    'income':                 'operating_revenue',
    'income_other':           'other_revenue',
    'expense':                'operating_expense',
    'expense_depreciation':   'expense_depreciation',
    'expense_direct_cost':    'expense_direct_cost',
    'off_balance':            'other',
}


def _canonical_subtype(raw: str, account_type: str) -> str:
    """Normalise an Odoo sub_type to omni's canonical vocabulary.

    Falls back to the per-account_type default if the value is empty
    or unrecognised so the CoA page never shows a wrong-family label.
    """
    if raw and raw in _ODOO_SUBTYPE_MAP:
        return _ODOO_SUBTYPE_MAP[raw]
    defaults = {
        'asset':     'current_asset',
        'liability': 'current_liability',
        'equity':    'equity',
        'revenue':   'operating_revenue',
        'expense':   'operating_expense',
    }
    return defaults.get(account_type, raw or 'imported')


def import_accounts(
    client: OdooClient,
    *,
    dry_run: bool = True,
    adic_company_id: int | None = None,
) -> ImportResult:
    """Pull every non-ADIC chart-of-accounts entry from Odoo."""
    result = ImportResult(model=ODOO_MODEL)
    if adic_company_id is None:
        domain = []
    else:
        domain = [adic_exclude_domain_term(adic_company_id)]

    for record in client.search_read(ODOO_MODEL, domain, ODOO_FIELDS):
        result.fetched += 1

        # Safety belt — never trust the filter.
        if adic_company_id is not None and _coerce_id(record.get('company_id')) == adic_company_id:
            result.skipped_adic += 1
            continue

        external_ref = make_extref(ODOO_MODEL, record['id'])
        code = (record.get('code') or '').strip()
        if not code:
            result.skipped_other += 1
            continue

        # Idempotency — code wins over external_ref. Our seeded CoA stays.
        existing = Account.objects.filter(code=code).first()
        if existing:
            if dry_run:
                result.skipped_duplicate += 1
                continue
            if not existing.external_ref:
                existing.external_ref = external_ref
                existing.save(update_fields=['external_ref'])
            result.skipped_duplicate += 1
            continue

        # Modern Odoo: internal_group is the direct AccountType signal.
        internal_group = record.get('internal_group')
        account_type, recognised = map_internal_group(internal_group)
        if not recognised:
            logger.warning(
                "Account %s: unrecognised Odoo internal_group '%s' — defaulted to '%s'",
                code, internal_group, account_type,
            )

        # Currency on account is optional; the company's default applies if blank.
        currency_code = _coerce_name(record.get('currency_id')) or 'BWP'

        if dry_run:
            result.imported += 1
            continue

        try:
            with transaction.atomic():
                currency, _ = Currency.objects.get_or_create(
                    code=currency_code,
                    defaults={'name': currency_code, 'symbol': currency_code},
                )
                # Normalise Odoo's finer account_type Selection ('asset_current',
                # 'asset_receivable', etc.) to omni's canonical sub_type names so
                # the CoA page and BS/PL reports bucket the account correctly.
                raw_subtype = (record.get('account_type') or '')[:50]
                sub_type = _canonical_subtype(raw_subtype, account_type)[:50]
                Account.objects.create(
                    code=code,
                    name=(record.get('name') or code)[:200],
                    account_type=account_type,
                    sub_type=sub_type,
                    currency_code=currency,
                    is_active=not record.get('deprecated', False),
                    description=f"[Migrated from Odoo on 2026-05-14] internal_group={internal_group}",
                    external_ref=external_ref,
                )
                result.imported += 1
        except Exception as exc:  # noqa: BLE001
            result.failed += 1
            result.add_error(f"account {code}: {exc}")
            logger.exception("Failed to import account %s", code)

    return result
