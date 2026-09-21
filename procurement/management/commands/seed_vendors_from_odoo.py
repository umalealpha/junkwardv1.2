"""
Management command: seed_vendors_from_odoo

Imports a curated vendor list (CSV) into the Contact table as
contact_type='vendor'. The CSV is produced from the Odoo res.partner export
by an off-line classifier — see data/vendors_from_odoo.csv.

CSV columns (utf-8-sig):
    name, phone, email, city, country, created_on, classifier_reason

Behaviour:
  - idempotent: if a Contact with the same case-insensitive name AND
    contact_type='vendor' already exists, the row is skipped (or updated
    when --update is supplied).
  - never overwrites an existing wht_exempt or is_related_party flag.
  - non-resident vendors (country ≠ Botswana) are flagged is_resident=False.
  - default currency: BWP for Botswana suppliers, USD for US, ZAR for SA.

Usage:
    python manage.py seed_vendors_from_odoo
    python manage.py seed_vendors_from_odoo --csv data/vendors_from_odoo.csv
    python manage.py seed_vendors_from_odoo --dry-run
    python manage.py seed_vendors_from_odoo --update    # update phone/email/city
                                                          # if blank on existing
                                                          # vendor
    python manage.py seed_vendors_from_odoo --limit 10  # first 10 only
"""

from __future__ import annotations

import csv
import datetime
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from billing.models import Contact
from core.models import Currency


# Map ISO-ish country names from the CSV onto a default currency code
COUNTRY_TO_CURRENCY = {
    'botswana':       'BWP',
    'south africa':   'ZAR',
    'united states':  'USD',
    'usa':            'USD',
    'singapore':      'USD',
    'india':          'USD',
    'mauritius':      'USD',
    'kenya':          'USD',
    'united kingdom': 'USD',
    'zambia':         'USD',
    'zimbabwe':       'USD',
}


def _coerce_currency(country_name):
    if not country_name:
        return 'BWP'
    return COUNTRY_TO_CURRENCY.get(country_name.strip().lower(), 'BWP')


def _is_resident(country_name):
    if not country_name:
        return True
    return country_name.strip().lower() == 'botswana'


class Command(BaseCommand):
    help = 'Seed vendors from a classified Odoo res.partner CSV.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--csv',
            default=str(Path(settings.BASE_DIR) / 'data' / 'vendors_from_odoo.csv'),
            help='Path to the vendor CSV. Defaults to data/vendors_from_odoo.csv.',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Show what would be imported without writing to the database.',
        )
        parser.add_argument(
            '--update', action='store_true',
            help='Fill in missing phone/email/city on existing vendors '
                 '(does NOT overwrite non-empty fields).',
        )
        parser.add_argument(
            '--limit', type=int, default=0,
            help='Only process the first N rows (for spot testing).',
        )

    def handle(self, *args, **options):
        csv_path = Path(options['csv'])
        if not csv_path.is_file():
            raise CommandError(f'CSV not found at {csv_path}')

        dry_run = options['dry_run']
        update  = options['update']
        limit   = options['limit']

        # Ensure currency rows referenced by the import exist
        for code, name in [('BWP', 'Botswana Pula'),
                           ('ZAR', 'South African Rand'),
                           ('USD', 'US Dollar')]:
            Currency.objects.get_or_create(
                code=code, defaults={'name': name, 'symbol': code, 'decimal_places': 2},
            )

        created = updated = skipped = 0
        processed = 0

        with csv_path.open(encoding='utf-8-sig', newline='') as f:
            reader = csv.DictReader(f)

            with transaction.atomic():
                sid = transaction.savepoint() if dry_run else None

                for row in reader:
                    if limit and processed >= limit:
                        break
                    processed += 1

                    name    = (row.get('name') or '').strip()
                    if not name:
                        continue
                    phone   = (row.get('phone') or '').strip()
                    email   = (row.get('email') or '').strip()
                    city    = (row.get('city') or '').strip()
                    country = (row.get('country') or '').strip()

                    currency_code = _coerce_currency(country)
                    is_resident   = _is_resident(country)

                    # Idempotent lookup — case-insensitive name match within vendor type
                    existing = Contact.objects.filter(
                        contact_type=Contact.ContactType.VENDOR,
                        name__iexact=name,
                    ).first()

                    if existing:
                        if update:
                            changed = False
                            if not existing.phone and phone:
                                existing.phone = phone; changed = True
                            if not existing.email and email:
                                existing.email = email; changed = True
                            if not existing.address and city:
                                existing.address = (
                                    f"{city}, {country}" if country else city
                                )
                                changed = True
                            if changed:
                                existing.save()
                                updated += 1
                            else:
                                skipped += 1
                        else:
                            skipped += 1
                        continue

                    Contact.objects.create(
                        contact_type=Contact.ContactType.VENDOR,
                        name=name,
                        phone=phone or None,
                        email=email or None,
                        address=(f"{city}, {country}" if city or country
                                 else None),
                        currency_code_id=currency_code,
                        is_resident=is_resident,
                        is_active=True,
                        payment_terms_days=30,
                    )
                    created += 1

                if dry_run and sid is not None:
                    transaction.savepoint_rollback(sid)

        verb = 'WOULD HAVE' if dry_run else 'DID'
        self.stdout.write(self.style.SUCCESS(
            f'\n{verb} create:  {created} new vendors\n'
            f'{verb} update:  {updated} existing vendors\n'
            f'   skipped:        {skipped} (already present)\n'
            f'   processed:      {processed} CSV rows\n'
        ))
        if dry_run:
            self.stdout.write(self.style.WARNING(
                'Dry run — no database changes were saved.\n'
            ))
