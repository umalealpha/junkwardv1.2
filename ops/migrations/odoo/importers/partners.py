"""Vendor + customer importer — Odoo res.partner → billing.Contact."""

from __future__ import annotations

import logging

from django.db import transaction

from billing.models import Contact
from core.models import Currency
from ops.migrations.odoo.client import OdooClient
from ops.migrations.odoo.mapping import (
    ACTIVE_PARTNER_DOMAIN_TERM,
    ImportResult,
    adic_exclude_domain_term,
    make_extref,
)

logger = logging.getLogger(__name__)

ODOO_MODEL = 'res.partner'
ODOO_FIELDS = [
    'id', 'name', 'is_company', 'supplier_rank', 'customer_rank',
    'email', 'phone', 'mobile', 'vat',
    'street', 'street2', 'city', 'country_id', 'currency_id',
    'company_id',
]


def _coerce_id(value):
    if isinstance(value, (list, tuple)) and value:
        return int(value[0])
    return None


def _coerce_name(value) -> str:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return str(value[1])
    return ''


def _address(record) -> str:
    parts = [
        record.get('street') or '',
        record.get('street2') or '',
        record.get('city') or '',
    ]
    return ', '.join(p for p in parts if p)


def _country(record) -> str:
    name = _coerce_name(record.get('country_id'))
    if name == 'Botswana':
        return 'BW'
    return name[:2].upper() if name else 'BW'


def _import_partner_role(
    client: OdooClient,
    contact_type: str,
    rank_field: str,
    *,
    dry_run: bool,
    adic_company_id: int | None = None,
) -> ImportResult:
    """Shared loop used by vendor + customer importers."""
    model_label = f"{ODOO_MODEL}:{contact_type}"
    result = ImportResult(model=model_label)
    domain: list = [(rank_field, '>', 0)]
    if adic_company_id is not None:
        domain += [
            '|',
                adic_exclude_domain_term(adic_company_id),
                ('company_id', '=', False),
        ]

    for record in client.search_read(ODOO_MODEL, domain, ODOO_FIELDS):
        result.fetched += 1

        if adic_company_id is not None and _coerce_id(record.get('company_id')) == adic_company_id:
            result.skipped_adic += 1
            continue

        external_ref = make_extref(ODOO_MODEL, record['id'], contact_type)

        existing = Contact.objects.filter(external_ref=external_ref).first()
        if existing:
            result.skipped_duplicate += 1
            continue

        if dry_run:
            result.imported += 1
            continue

        name = (record.get('name') or '').strip()
        if not name:
            result.skipped_other += 1
            continue

        try:
            with transaction.atomic():
                currency_code = _coerce_name(record.get('currency_id')) or 'BWP'
                currency, _ = Currency.objects.get_or_create(
                    code=currency_code,
                    defaults={'name': currency_code, 'symbol': currency_code},
                )
                country_code = _country(record)
                Contact.objects.create(
                    name=name[:300],
                    contact_type=contact_type,
                    email=(record.get('email') or '')[:254] or None,
                    phone=(record.get('phone') or record.get('mobile') or '')[:50] or None,
                    tax_id=(record.get('vat') or '')[:50] or None,
                    address=_address(record) or None,
                    currency_code=currency,
                    is_resident=(country_code == 'BW'),
                    is_active=True,
                    external_ref=external_ref,
                )
                result.imported += 1
        except Exception as exc:  # noqa: BLE001
            result.failed += 1
            result.add_error(f"partner {record['id']} {name}: {exc}")
            logger.exception("Failed to import partner %s", record['id'])

    return result


def import_vendors(
    client: OdooClient, *, dry_run: bool = True, adic_company_id: int | None = None,
) -> ImportResult:
    """Pull every non-ADIC vendor partner from Odoo."""
    return _import_partner_role(
        client, contact_type=Contact.ContactType.VENDOR,
        rank_field='supplier_rank', dry_run=dry_run,
        adic_company_id=adic_company_id,
    )


def import_customers(
    client: OdooClient, *, dry_run: bool = True, adic_company_id: int | None = None,
) -> ImportResult:
    """Pull every non-ADIC customer partner from Odoo."""
    return _import_partner_role(
        client, contact_type=Contact.ContactType.CUSTOMER,
        rank_field='customer_rank', dry_run=dry_run,
        adic_company_id=adic_company_id,
    )
