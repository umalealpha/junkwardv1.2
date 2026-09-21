"""Import the legacy motor-liquidators SQLite data into Postgres.

One-off, idempotent. Re-running is safe — rows already present (matched
by natural key) are skipped.

Default source path is the frozen copy shipped under
.claude/specs/salvage-portal/source/salvage.db. Pass --source to point
at a different file.

Usage:
    python manage.py import_motor_liquidators
    python manage.py import_motor_liquidators --source /path/to/salvage.db --dry-run
"""
import os
import sqlite3
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

DEFAULT_SOURCE = Path('.claude/specs/salvage-portal/source/salvage.db')


class Command(BaseCommand):
    help = 'Import motor-liquidators SQLite data into salvage Postgres tables.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--source', default=str(DEFAULT_SOURCE),
            help='Path to salvage.db (default: %(default)s)',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report what would be imported without writing.',
        )
        parser.add_argument(
            '--company', default='VCM',
            help='Company code for SalvageItem.company (default: VCM)',
        )

    def handle(self, *args, **options):
        from core.models import Company
        from salvage.models import (
            BuyerQuote, PartCategory, Sale, SalvageItem,
            VehicleBrand, VehicleModel,
        )

        src = Path(options['source'])
        if not src.exists():
            raise CommandError(f'Source file not found: {src}')

        dry = options['dry_run']
        company_code = options['company']
        try:
            company = Company.objects.get(code=company_code)
        except Company.DoesNotExist:
            raise CommandError(f'Company {company_code!r} not found')

        conn = sqlite3.connect(str(src))
        conn.row_factory = sqlite3.Row

        counts = {'categories': 0, 'brands': 0, 'models': 0, 'items': 0,
                  'quotes': 0, 'sales': 0}
        skipped = {'categories': 0, 'brands': 0, 'models': 0, 'items': 0,
                   'quotes': 0, 'sales': 0}

        with transaction.atomic():
            sp = transaction.savepoint()

            # ── part_categories ────────────────────────────────────────
            # Legacy schema only has (id, name) — `description` was an
            # aspirational column in the spec, not actually present in the
            # shipped SQLite file. Tolerate either shape.
            pc_cols = {row[1] for row in conn.execute('PRAGMA table_info(part_categories)')}
            pc_select = (
                'SELECT name, description FROM part_categories'
                if 'description' in pc_cols
                else 'SELECT name, NULL AS description FROM part_categories'
            )
            for row in conn.execute(pc_select):
                obj, was_created = PartCategory.objects.get_or_create(
                    name=row['name'],
                    defaults={'description': (row['description'] or '') if 'description' in pc_cols else ''},
                )
                if was_created:
                    counts['categories'] += 1
                else:
                    skipped['categories'] += 1

            # ── vehicle_brands ────────────────────────────────────────
            brand_map = {}  # sqlite_id -> django pk
            for row in conn.execute('SELECT id, name FROM vehicle_brands'):
                obj, was_created = VehicleBrand.objects.get_or_create(
                    name=row['name'].strip(),
                )
                brand_map[row['id']] = obj.pk
                if was_created:
                    counts['brands'] += 1
                else:
                    skipped['brands'] += 1

            # ── vehicle_models ────────────────────────────────────────
            model_map = {}
            for row in conn.execute(
                'SELECT id, brand_id, name FROM vehicle_models'
            ):
                brand_pk = brand_map.get(row['brand_id'])
                if not brand_pk:
                    continue
                obj, was_created = VehicleModel.objects.get_or_create(
                    brand_id=brand_pk, name=row['name'].strip(),
                )
                model_map[row['id']] = obj.pk
                if was_created:
                    counts['models'] += 1
                else:
                    skipped['models'] += 1

            # ── salvage_items ─────────────────────────────────────────
            cat_by_name = {c.name: c.pk for c in PartCategory.objects.all()}
            # build sqlite-id-keyed category map too
            cat_sqlite_map = {
                row['id']: cat_by_name.get(row['name'])
                for row in conn.execute('SELECT id, name FROM part_categories')
            }

            # Detect optional columns that the v2 SQLite schema may have.
            si_cols = {row[1] for row in conn.execute('PRAGMA table_info(salvage_items)')}
            extra_cols = [c for c in (
                'yard_section', 'shelf_row', 'received_date',
                'sold_date', 'disposed_date', 'status', 'notes',
            ) if c in si_cols]
            base_cols = [
                'id', 'item_code', 'claim_number', 'policy_number',
                'category_id', 'part_name', 'part_description', 'quantity',
                'vehicle_brand_id', 'vehicle_model_id', 'vehicle_year',
                'vehicle_colour', 'vin_number',
                'condition', 'asking_price', 'reserve_price',
            ]
            select_cols = ', '.join(base_cols + extra_cols)

            # SQLite item-id → Django pk, so buyer_quotes / sales can be
            # re-linked below.
            item_map: dict[int, int] = {}

            for row in conn.execute(f'SELECT {select_cols} FROM salvage_items'):
                existing = SalvageItem.objects.filter(item_code=row['item_code']).first()
                if existing:
                    item_map[row['id']] = existing.pk
                    skipped['items'] += 1
                    continue

                # motor-liq statuses: in_stock, quoted, reserved, sold,
                # written_off, disposed. Map → alpha-finance Status enum.
                src_status = (row['status'] or 'in_stock') if 'status' in extra_cols else 'in_stock'
                status_map = {
                    'in_stock':    SalvageItem.Status.AVAILABLE,
                    'quoted':      SalvageItem.Status.QUOTED,
                    'reserved':    SalvageItem.Status.RESERVED,
                    'sold':        SalvageItem.Status.SOLD,
                    'written_off': SalvageItem.Status.WRITTEN_OFF,
                    'disposed':    SalvageItem.Status.DISPOSED,
                }
                af_status = status_map.get(src_status, SalvageItem.Status.AVAILABLE)

                created = SalvageItem.objects.create(
                    item_code        = row['item_code'],
                    claim_number     = row['claim_number'] or '',
                    policy_number    = row['policy_number'] or '',
                    category_id      = cat_sqlite_map.get(row['category_id']),
                    part_name        = row['part_name'] or '',
                    part_description = row['part_description'] or '',
                    quantity         = row['quantity'] or 1,
                    vehicle_brand_id = brand_map.get(row['vehicle_brand_id']),
                    vehicle_model_id = model_map.get(row['vehicle_model_id']),
                    vehicle_year     = row['vehicle_year'],
                    vehicle_colour   = row['vehicle_colour'] or '',
                    vin_number       = row['vin_number'] or '',
                    condition        = row['condition'] or 'fair',
                    asking_price     = row['asking_price'] or 0,
                    reserve_price    = row['reserve_price'] or 0,
                    status           = af_status,
                    yard_section     = (row['yard_section'] if 'yard_section' in extra_cols else '') or '',
                    shelf_row        = (row['shelf_row']    if 'shelf_row'    in extra_cols else '') or '',
                    received_date    = (row['received_date'] if 'received_date' in extra_cols else None) or None,
                    sold_date        = (row['sold_date']     if 'sold_date'     in extra_cols else None) or None,
                    disposed_date    = (row['disposed_date'] if 'disposed_date' in extra_cols else None) or None,
                    notes            = (row['notes']         if 'notes'         in extra_cols else '') or '',
                    company          = company,
                )
                item_map[row['id']] = created.pk
                counts['items'] += 1

            # ── buyer_quotes ──────────────────────────────────────────
            bq_cols = {row[1] for row in conn.execute('PRAGMA table_info(buyer_quotes)')} \
                      if conn.execute("SELECT name FROM sqlite_master "
                                       "WHERE type='table' AND name='buyer_quotes'").fetchone() else set()
            if bq_cols:
                # status alias map (motor-liq → alpha-finance)
                bq_status_map = {
                    'pending': 'pending', 'under_review': 'under_review',
                    'accepted': 'accepted', 'rejected': 'rejected',
                    'countered': 'countered',
                }
                # external_id de-dupe via (item_id, buyer_phone, offered_price, created_at)
                quote_map: dict[int, int] = {}
                for row in conn.execute('''
                    SELECT id, salvage_item_id, buyer_name, buyer_email,
                           buyer_phone, buyer_company, offered_price,
                           message, status, review_notes, created_at
                      FROM buyer_quotes
                '''):
                    item_pk = item_map.get(row['salvage_item_id'])
                    if not item_pk:
                        continue
                    # de-dupe on natural key
                    natural_match = BuyerQuote.objects.filter(
                        item_id=item_pk,
                        buyer_phone=row['buyer_phone'] or '',
                        offered_price=row['offered_price'] or 0,
                    ).first()
                    if natural_match:
                        quote_map[row['id']] = natural_match.pk
                        skipped['quotes'] += 1
                        continue
                    q = BuyerQuote.objects.create(
                        item_id       = item_pk,
                        buyer_name    = (row['buyer_name'] or '')[:200],
                        buyer_email   = (row['buyer_email'] or '')[:254],
                        buyer_phone   = (row['buyer_phone'] or '')[:40],
                        buyer_company = (row['buyer_company'] or '')[:200],
                        offered_price = row['offered_price'] or 0,
                        message       = row['message'] or '',
                        status        = bq_status_map.get(row['status'] or 'pending', 'pending'),
                        review_notes  = row['review_notes'] or '',
                    )
                    quote_map[row['id']] = q.pk
                    counts['quotes'] += 1
            else:
                quote_map = {}

            # ── sales ────────────────────────────────────────────────
            sales_exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='sales'"
            ).fetchone()
            if sales_exists:
                pm_map = {
                    'cash': 'cash', 'eft': 'eft', 'cheque': 'cheque',
                    'mobile_money': 'mobile_money',
                }
                for row in conn.execute('''
                    SELECT id, salvage_item_id, buyer_quote_id,
                           buyer_name, buyer_phone, sale_price,
                           payment_method, payment_ref, sale_date, notes
                      FROM sales
                '''):
                    item_pk = item_map.get(row['salvage_item_id'])
                    if not item_pk:
                        continue
                    natural_match = Sale.objects.filter(
                        item_id=item_pk,
                        buyer_phone=row['buyer_phone'] or '',
                        sale_price=row['sale_price'] or 0,
                        sale_date=row['sale_date'] or None,
                    ).first()
                    if natural_match:
                        skipped['sales'] += 1
                        continue
                    Sale.objects.create(
                        item_id        = item_pk,
                        buyer_quote_id = quote_map.get(row['buyer_quote_id']),
                        buyer_name     = (row['buyer_name'] or '')[:200],
                        buyer_phone    = (row['buyer_phone'] or '')[:40],
                        sale_price     = row['sale_price'] or 0,
                        payment_method = pm_map.get(row['payment_method'] or 'cash', 'cash'),
                        payment_ref    = (row['payment_ref'] or '')[:120],
                        sale_date      = row['sale_date'] or '1970-01-01',
                        notes          = row['notes'] or '',
                    )
                    counts['sales'] += 1

            if dry:
                transaction.savepoint_rollback(sp)
            else:
                transaction.savepoint_commit(sp)

        conn.close()

        marker = ' (DRY RUN — rolled back)' if dry else ''
        self.stdout.write(self.style.SUCCESS(f'SALVAGE IMPORT{marker}'))
        for k in counts:
            self.stdout.write(
                f'  {k}: created={counts[k]} skipped_existing={skipped[k]}'
            )
        self.stdout.write(f'  target company: {company_code}')
