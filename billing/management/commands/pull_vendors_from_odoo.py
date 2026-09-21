"""
Pull vendors (and optionally customers) from Odoo per legal entity and
stamp them onto billing.Contact with the correct company FK.

CFO directive 2026-05-18: every subsidiary has its own supplier list.
Don't ask the CFO to upload xlsx files — pull straight from Odoo where
the data already lives.

  python manage.py pull_vendors_from_odoo --company UNI
  python manage.py pull_vendors_from_odoo --company GCX --commit
  python manage.py pull_vendors_from_odoo --all                 # dry-run for every mapped entity
  python manage.py pull_vendors_from_odoo --all --commit
  python manage.py pull_vendors_from_odoo --include-customers   # also pull customer_rank > 0

Behaviour:
  - Uses the existing company-name → Company.code map in
    ops/migrations/odoo/runner.py (so adding a new mapping there
    feeds this command automatically).
  - Idempotent on external_ref = make_extref('res.partner', odoo_id, 'vendor'|'customer').
  - ADIC is intentionally excluded — CFO confirmed the 1,464 already
    loaded are the source of truth for ADIC and re-pulling would risk
    double-mapping. Pass --include-adic to override.
"""
from __future__ import annotations

from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from billing.models import Contact
from core.models import Company, Currency
from ops.migrations.odoo.client import OdooClient
from ops.migrations.odoo.mapping import make_extref, resolve_adic_id
from ops.migrations.odoo.runner import DEFAULT_COMPANY_MAP_BY_NAME


PARTNER_FIELDS = [
    'id', 'name', 'is_company', 'supplier_rank', 'customer_rank',
    'email', 'phone', 'mobile', 'vat',
    'street', 'street2', 'city', 'country_id', 'currency_id',
    'company_id',
]


def _coerce_id(v):
    if isinstance(v, (list, tuple)) and v:
        return int(v[0])
    return None


def _coerce_name(v) -> str:
    if isinstance(v, (list, tuple)) and len(v) >= 2:
        return str(v[1])
    return ''


def _address(record) -> str:
    parts = [record.get('street') or '', record.get('street2') or '', record.get('city') or '']
    return ', '.join(p for p in parts if p)


def _country(record) -> str:
    name = _coerce_name(record.get('country_id'))
    if name == 'Botswana':
        return 'BW'
    return (name[:2].upper() if name else 'BW')


class Command(BaseCommand):
    help = 'Pull Odoo vendors (and optionally customers) per company into billing.Contact.'

    def add_arguments(self, parser):
        parser.add_argument('--company', help='alpha-finance Company.code (e.g. UNI, GCX, RSA). Mutually exclusive with --all.')
        parser.add_argument('--all', action='store_true', help='Process every mapped subsidiary.')
        parser.add_argument('--include-customers', action='store_true',
                            help='Also pull customer_rank > 0 rows alongside vendors.')
        parser.add_argument('--include-adic', action='store_true',
                            help='Allow ADIC pull (off by default — see CFO directive).')
        parser.add_argument('--commit', action='store_true', help='Actually write rows.')

    def handle(self, *args, **opts):
        if not (opts['company'] or opts['all']):
            raise CommandError('Pass --company <CODE> or --all.')
        if opts['company'] and opts['all']:
            raise CommandError('--company and --all are mutually exclusive.')

        commit = opts['commit']
        include_customers = opts['include_customers']

        # ── Authenticate against Odoo once ───────────────────────────
        try:
            client = OdooClient()
            client.authenticate()
        except Exception as e:  # noqa: BLE001
            raise CommandError(f'Odoo authentication failed: {e}')

        # ── Build code → Odoo company id map by joining DEFAULT_COMPANY_MAP_BY_NAME
        #    with the live res.company list. ───────────────────────────
        odoo_companies = list(client.search_read(
            'res.company', [], ['id', 'name'],
        ))
        adic_odoo_id = resolve_adic_id(client)

        code_to_odoo: dict[str, int] = {}
        for rec in odoo_companies:
            nm = (rec.get('name') or '').strip().lower()
            code = DEFAULT_COMPANY_MAP_BY_NAME.get(nm)
            if code:
                code_to_odoo[code.upper()] = int(rec['id'])

        # Pick the entities to process
        if opts['all']:
            target_codes = sorted(code_to_odoo.keys())
        else:
            target_codes = [opts['company'].upper()]

        if not opts['include_adic']:
            target_codes = [c for c in target_codes if c != 'ADIC']

        if not target_codes:
            self.stdout.write(self.style.WARNING(
                'No target entities — refused to process ADIC and nothing else matched.'
            ))
            return

        # ── Run per-entity ────────────────────────────────────────────
        totals = defaultdict(int)
        for code in target_codes:
            odoo_id = code_to_odoo.get(code)
            if odoo_id is None:
                self.stdout.write(self.style.ERROR(
                    f"  {code}: no Odoo company id mapped (update DEFAULT_COMPANY_MAP_BY_NAME)."
                ))
                continue
            af_company = Company.objects.filter(code__iexact=code).first()
            if not af_company:
                self.stdout.write(self.style.ERROR(f'  {code}: no alpha-finance Company row.'))
                continue
            if adic_odoo_id is not None and odoo_id == adic_odoo_id and not opts['include_adic']:
                self.stdout.write(self.style.WARNING(f'  {code}: matches ADIC Odoo id — skipping (use --include-adic to override).'))
                continue

            self.stdout.write(self.style.MIGRATE_HEADING(
                f'\n{code} (Odoo id={odoo_id}) → alpha-finance Company {af_company.pk}'
            ))
            stats = self._pull_one(client, af_company, odoo_id, include_customers, commit)
            for k, v in stats.items():
                totals[f'{code}.{k}'] = v
                totals[f'TOTAL.{k}'] = totals.get(f'TOTAL.{k}', 0) + v

        # ── Final summary ────────────────────────────────────────────
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            'PER-ENTITY ODOO VENDOR PULL' + ('' if commit else ' (DRY RUN)')
        ))
        for k in sorted(totals.keys()):
            self.stdout.write(f'  {k:<35} {totals[k]}')

    # ------------------------------------------------------------------
    def _pull_one(self, client, af_company, odoo_company_id, include_customers, commit):
        """Pull vendors (and optionally customers) for one Odoo company.

        Odoo's multi-company partner model is split: most partners are
        SHARED (company_id=False) and visible to every legal entity. A
        smaller set are company-specific (company_id=<id>). Pulling shared
        partners for every entity would just duplicate the ADIC list nine
        times — they're the same legal counterparties.

        So we ONLY pull company-specific partners here. Shared partners
        already exist as ADIC contacts from the prior backfill and can be
        re-tagged to other entities manually via the UI if needed.
        """
        stats = {'fetched': 0, 'imported_vendor': 0, 'imported_customer': 0,
                 'skipped_duplicate': 0, 'skipped_other': 0, 'failed': 0}

        # Strict: partners with company_id explicitly set to this Odoo company.
        domain_v = [
            ('supplier_rank', '>', 0),
            ('company_id', '=', odoo_company_id),
        ]
        for record in client.search_read('res.partner', domain_v, PARTNER_FIELDS):
            self._upsert(record, af_company, Contact.ContactType.VENDOR, commit, stats)

        if include_customers:
            domain_c = [
                ('customer_rank', '>', 0),
                ('company_id', '=', odoo_company_id),
            ]
            for record in client.search_read('res.partner', domain_c, PARTNER_FIELDS):
                self._upsert(record, af_company, Contact.ContactType.CUSTOMER, commit, stats)

        for k, v in stats.items():
            self.stdout.write(f'    {k:<22} {v}')
        return stats

    def _upsert(self, record, af_company, contact_type, commit, stats):
        stats['fetched'] += 1
        name = (record.get('name') or '').strip()
        if not name:
            stats['skipped_other'] += 1
            return

        external_ref = make_extref('res.partner', record['id'], contact_type)
        existing = Contact.objects.filter(external_ref=external_ref).first()
        if existing:
            # If the row was previously pulled without a company, patch it.
            if existing.company_id is None and commit:
                existing.company = af_company
                existing.save(update_fields=['company'])
            stats['skipped_duplicate'] += 1
            return

        if not commit:
            stats[f'imported_{contact_type}'] += 1
            return

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
                    company=af_company,
                    external_ref=external_ref,
                )
                stats[f'imported_{contact_type}'] += 1
        except Exception as exc:  # noqa: BLE001
            stats['failed'] += 1
            self.stdout.write(self.style.ERROR(
                f"    ! partner {record['id']} {name[:60]!r}: {exc}"
            ))
