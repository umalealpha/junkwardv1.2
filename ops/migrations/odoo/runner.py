"""
Orchestrates the four importers in dependency order:

    accounts → vendors → customers → journal_entries

CoA must finish first because journal entries reference accounts.
Partners must finish before JEs so line.partner_id resolves.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path

from django.contrib.auth.models import User

from core.models import Company
from ops.migrations.odoo.client import OdooClient
from ops.migrations.odoo.importers import (
    import_accounts, import_customers, import_journal_entries, import_vendors,
)
from ops.migrations.odoo.mapping import ImportResult, resolve_adic_id

logger = logging.getLogger(__name__)

RUNS_DIR = Path(__file__).parent / 'runs'

# Odoo company_id → alpha-finance Company code mapping. Hard-coded for v1
# because the company set is stable and small. Update here when companies
# are added on either side.
DEFAULT_COMPANY_MAP_BY_NAME: dict[str, str] = {
    # Odoo company name (lowercased) → alpha-finance Company.code
    # ADIC is excluded — see resolve_adic_id() in mapping.py — so don't add it
    # here. The carrier's books are already authoritative in alpha-finance and
    # the CFO's directive ("It is done, so don't touch it") forbids re-import.
    'alpha direct insurance holdings':              'ADIH',
    'alpha direct holdings':                        'ADIH',
    'alpha direct global':                          'ADG',
    'quantum insurance holdings':                   'QIH',

    # Subsidiaries — every Odoo company_id observed on prod 2026-05-16, mapped
    # to its alpha-finance Company.code. Add new rows here as soon as a new
    # Odoo res.company appears (mapping is the only thing standing between
    # the importer and a "No alpha-finance Company mapping" skip).
    'veritas capital management pty ltd':           'VCM',
    'risk software africa pty ltd':                 'RSA',
    'alpha direct south africa':                    'ADSA',
    'gaborone coin exchange proprietary limited':   'GCX',
    'unicoin proprietary limited':                  'UNI',
    'alpha direct insurtech pte ltd':               'ADIPL',
    'alpha insurtech zambia':                       'AIZ',
    'alpha direct life insurance pty ltd':          'ADIL',
    'adrisk global solutions private limited':      'ADRG',
}


def _build_company_lookup(
    client: OdooClient,
    adic_company_id: int | None,
) -> dict[int, Company]:
    """Map Odoo res.company.id → alpha-finance Company instance."""
    lookup: dict[int, Company] = {}
    try:
        domain = [] if adic_company_id is None else [('id', '!=', adic_company_id)]
        records = list(client.search_read('res.company', domain, ['id', 'name']))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not load Odoo res.company list: %s", exc)
        return lookup

    for rec in records:
        name = (rec.get('name') or '').strip().lower()
        code = DEFAULT_COMPANY_MAP_BY_NAME.get(name)
        if not code:
            logger.warning(
                "No alpha-finance Company mapping for Odoo company id=%s name='%s' — skipping",
                rec['id'], rec.get('name'),
            )
            continue
        company = Company.objects.filter(code=code).first()
        if company is None:
            logger.warning(
                "Mapped alpha-finance Company code '%s' not in DB — skipping Odoo id=%s",
                code, rec['id'],
            )
            continue
        lookup[rec['id']] = company
    return lookup


def run(
    *,
    dry_run: bool = True,
    models: list[str] | None = None,
    run_user: User,
) -> dict:
    """
    Execute the migration. Returns a summary dict + writes it to disk
    at ops/migrations/odoo/runs/<run_id>.json.
    """
    run_id = str(uuid.uuid4())
    started = datetime.utcnow().isoformat() + 'Z'
    logger.info("Odoo migration run %s started (dry_run=%s, models=%s)",
                run_id, dry_run, models or 'all')

    client = OdooClient()
    client.authenticate()

    # Resolve ADIC by name (Odoo company IDs are environment-specific).
    adic_id = resolve_adic_id(client)
    if adic_id is None:
        logger.warning(
            "[%s] ADIC company not found in res.company — proceeding WITHOUT exclusion. "
            "Verify the ADIC_NAME_PATTERNS in mapping.py.", run_id,
        )
    else:
        logger.info("[%s] ADIC resolved: Odoo company_id=%s", run_id, adic_id)

    all_models = ['accounts', 'vendors', 'customers', 'journal_entries']
    selected = models or all_models

    results: list[ImportResult] = []

    if 'accounts' in selected:
        logger.info("[%s] importing accounts", run_id)
        results.append(import_accounts(client, dry_run=dry_run, adic_company_id=adic_id))

    company_lookup = _build_company_lookup(client, adic_company_id=adic_id)

    if 'vendors' in selected:
        logger.info("[%s] importing vendors", run_id)
        results.append(import_vendors(client, dry_run=dry_run, adic_company_id=adic_id))

    if 'customers' in selected:
        logger.info("[%s] importing customers", run_id)
        results.append(import_customers(client, dry_run=dry_run, adic_company_id=adic_id))

    if 'journal_entries' in selected:
        logger.info("[%s] importing journal entries", run_id)
        results.append(import_journal_entries(
            client, dry_run=dry_run,
            run_user=run_user, company_lookup=company_lookup,
            adic_company_id=adic_id,
        ))

    ended = datetime.utcnow().isoformat() + 'Z'

    summary = {
        'run_id':           run_id,
        'started':          started,
        'ended':            ended,
        'dry_run':          dry_run,
        'models':           selected,
        'adic_company_id':  adic_id,
        'results':          [r.summary() for r in results],
        'totals':   {
            'fetched':  sum(r.fetched for r in results),
            'imported': sum(r.imported for r in results),
            'failed':   sum(r.failed for r in results),
            'skipped':  sum(
                r.skipped_duplicate + r.skipped_adic
                + r.skipped_post_cutoff + r.skipped_other
                for r in results
            ),
        },
    }

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RUNS_DIR / f"{run_id}.json"
    out_path.write_text(json.dumps(summary, indent=2))
    logger.info("Run summary written to %s", out_path)

    return summary
