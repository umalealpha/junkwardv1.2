"""
payroll/management/commands/setup_tax_brackets.py

Seeds Botswana resident-individual PAYE brackets as the CFO published them
historically. THE CFO MUST VERIFY THESE AGAINST THE CURRENT BURS SCHEDULE
before running a real payroll — BURS publishes new bands periodically and
this command only seeds defaults so the calculator works at all.

Run:
    python manage.py setup_tax_brackets

To replace the active bands with a freshly-edited set, mark all existing
TaxBracket rows is_active=False in the admin first, then re-run this with
--add-only to add the new ones.
"""

from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand

from payroll.models import TaxBracket


# These are placeholder defaults the CFO should verify against the live BURS
# Income Tax Act schedule before running a real period. Stored configurably so
# they can be amended without code changes.
DEFAULT_BRACKETS = [
    # (lower_bound, upper_bound, base_amount, rate_pct)
    (Decimal('0'),       Decimal('48000'),  Decimal('0'),     Decimal('0')),
    (Decimal('48000'),   Decimal('84000'),  Decimal('0'),     Decimal('12')),
    (Decimal('84000'),   Decimal('120000'), Decimal('4320'),  Decimal('18')),
    (Decimal('120000'),  Decimal('156000'), Decimal('10800'), Decimal('25')),
    (Decimal('156000'),  None,              Decimal('19800'), Decimal('25')),
]


class Command(BaseCommand):
    help = ('Seed Botswana PAYE tax brackets as defaults. CFO must verify '
            'against the current BURS Income Tax Act schedule.')

    def add_arguments(self, parser):
        parser.add_argument('--effective-from', default='2024-07-01',
                            help='ISO date the seeded bands apply from.')
        parser.add_argument('--name-prefix', default='Resident individual (default — VERIFY against BURS)',
                            help='Human label written into each bracket row.')

    def handle(self, *args, **opts):
        effective = date.fromisoformat(opts['effective_from'])
        name_prefix = opts['name_prefix']

        created = 0
        skipped = 0
        for lo, hi, base, rate in DEFAULT_BRACKETS:
            obj, was_created = TaxBracket.objects.get_or_create(
                effective_from=effective,
                lower_bound=lo,
                defaults={
                    'name': name_prefix,
                    'upper_bound': hi,
                    'base_amount': base,
                    'rate_pct': rate,
                    'is_active': True,
                },
            )
            if was_created:
                created += 1
                cap = hi if hi is not None else '∞'
                self.stdout.write(self.style.SUCCESS(f'  + {lo}–{cap} @ {rate}%'))
            else:
                skipped += 1

        self.stdout.write(self.style.WARNING(
            '\n!! These are DEFAULT placeholder bands. The CFO must verify them '
            'against the latest BURS Income Tax Act schedule before running '
            'a real payroll. Edit via the admin or /api/v1/tax-brackets/.'
        ))
        self.stdout.write(self.style.SUCCESS(f'\nDone. {created} created, {skipped} already present.'))
