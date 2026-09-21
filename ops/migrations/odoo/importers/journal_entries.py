"""Journal entries importer — Odoo account.move + account.move.line → ledger.JournalEntry / JournalEntryLine."""

from __future__ import annotations

import logging
import uuid
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import transaction
from django.utils.dateparse import parse_date

from billing.models import Contact
from core.models import Currency, Company
from ledger.models import Account, JournalEntry, JournalEntryLine
from ops.migrations.odoo.client import OdooClient
from ops.migrations.odoo.mapping import (
    CUTOFF_DATE,
    CUTOFF_DATE_DOMAIN_TERM,
    ImportResult,
    POSTED_DOMAIN_TERM,
    adic_exclude_domain_term,
    make_extref,
)

logger = logging.getLogger(__name__)

MOVE_MODEL = 'account.move'
LINE_MODEL = 'account.move.line'

MOVE_FIELDS = [
    'id', 'name', 'date', 'company_id', 'currency_id',
    'journal_id', 'ref', 'narration',
]
LINE_FIELDS = [
    'id', 'move_id', 'account_id', 'partner_id',
    'name', 'debit', 'credit', 'currency_id',
]

ZERO = Decimal('0.00')


def _coerce_id(value):
    if isinstance(value, (list, tuple)) and value:
        return int(value[0])
    return None


def _coerce_name(value) -> str:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return str(value[1])
    return ''


def _coerce_decimal(value) -> Decimal:
    if value is None or value == '' or value is False:
        return ZERO
    return Decimal(str(value))


def import_journal_entries(
    client: OdooClient,
    *,
    dry_run: bool = True,
    run_user: User,
    company_lookup: dict[int, Company] | None = None,
    adic_company_id: int | None = None,
) -> ImportResult:
    """Pull every posted, non-ADIC, on-or-before-cutoff journal entry."""
    result = ImportResult(model=MOVE_MODEL)
    company_lookup = company_lookup or {}

    domain: list = [POSTED_DOMAIN_TERM, CUTOFF_DATE_DOMAIN_TERM]
    if adic_company_id is not None:
        domain.append(adic_exclude_domain_term(adic_company_id))

    for move in client.search_read(MOVE_MODEL, domain, MOVE_FIELDS):
        result.fetched += 1

        # Safety belts — never trust the filter.
        if adic_company_id is not None and _coerce_id(move.get('company_id')) == adic_company_id:
            result.skipped_adic += 1
            continue
        move_date = parse_date(move['date']) if move.get('date') else None
        if move_date is None or move_date > CUTOFF_DATE:
            result.skipped_post_cutoff += 1
            continue

        external_ref = make_extref(MOVE_MODEL, move['id'])
        # ledger.JournalEntry.source_id is UUIDField. Django coerces an
        # integer to UUID('00000000-...-N') on write, but filter(source_id=int)
        # does NOT match the same row back. Build the UUID once and use it
        # for both the duplicate check and the create.
        source_uuid = uuid.UUID(int=int(move['id']))
        if JournalEntry.objects.filter(
            source_type='odoo_import', source_id=source_uuid,
        ).exists():
            result.skipped_duplicate += 1
            continue

        if dry_run:
            # In dry-run we don't fetch lines — saves bandwidth.
            result.imported += 1
            continue

        # ------------------------------------------------------------------
        # Build the move + its lines
        # ------------------------------------------------------------------
        try:
            with transaction.atomic():
                lines = list(client.search_read(
                    LINE_MODEL,
                    [('move_id', '=', move['id'])],
                    LINE_FIELDS,
                ))
                if not lines:
                    result.skipped_other += 1
                    continue

                # Resolve currency
                currency_code = _coerce_name(move.get('currency_id')) or 'BWP'
                currency, _ = Currency.objects.get_or_create(
                    code=currency_code,
                    defaults={'name': currency_code, 'symbol': currency_code},
                )

                company = None
                odoo_company_id = _coerce_id(move.get('company_id'))
                if odoo_company_id:
                    company = company_lookup.get(odoo_company_id)

                # Resolve every line's account first — fail closed if any missing
                line_accounts: dict[int, Account] = {}
                for ln in lines:
                    acct_code = _coerce_name(ln.get('account_id'))
                    # account_id name is "<code> <name>" in Odoo — split
                    code_token = acct_code.split(' ', 1)[0] if acct_code else ''
                    acct = Account.objects.filter(code=code_token).first()
                    if acct is None:
                        raise ValueError(
                            f"Account code '{code_token}' not in alpha-finance CoA. "
                            "Run CoA importer first."
                        )
                    line_accounts[ln['id']] = acct

                # Create the JE in DRAFT
                je = JournalEntry.objects.create(
                    entry_number='',     # auto-generated
                    entry_date=move_date,
                    description=(move.get('narration') or move.get('ref')
                                 or move.get('name') or f"Odoo move {move['id']}")[:500],
                    source_type='odoo_import',
                    source_id=source_uuid,
                    journal_type=JournalEntry.JournalType.GENERAL,
                    currency_code=currency,
                    exchange_rate=Decimal('1.00000000'),
                    company=company,
                    created_by=run_user,
                    status=JournalEntry.Status.DRAFT,
                    is_related_party=False,
                    notes=f"[Migrated from Odoo on 2026-05-14] move name={move.get('name')}",
                )

                # Create lines
                for ln in lines:
                    debit  = _coerce_decimal(ln.get('debit'))
                    credit = _coerce_decimal(ln.get('credit'))
                    partner_id = _coerce_id(ln.get('partner_id'))
                    contact = None
                    if partner_id:
                        contact = Contact.objects.filter(
                            external_ref__startswith=make_extref('res.partner', partner_id),
                        ).first()

                    JournalEntryLine.objects.create(
                        journal_entry=je,
                        account=line_accounts[ln['id']],
                        description=(ln.get('name') or '')[:255],
                        debit_amount=debit,
                        credit_amount=credit,
                        debit_bwp=debit,        # FX delta out of scope for v1
                        credit_bwp=credit,
                        contact=contact,
                    )

                je.post(user=run_user, _allow_direct=True)
                result.imported += 1
        except Exception as exc:  # noqa: BLE001
            result.failed += 1
            result.add_error(f"move {move['id']}: {exc}")
            logger.exception("Failed to import move %s", move['id'])

    return result
