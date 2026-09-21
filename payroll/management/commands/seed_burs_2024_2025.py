"""
seed_burs_2024_2025 — Botswana BURS PAYE tax brackets for FY2024-25.

CFO directive 2026-05-21 (post-Manus full-system test): replace the
placeholder brackets (which still carried the pre-2021 rates of
12/18/25 %) with the live BURS schedule.

Source: Botswana Income Tax (Amendment) Act 2021, sustained through
FY2024-25 per BURS Tax Card 2024 / PwC Worldwide Tax Summary / Deloitte
Botswana Highlights 2024.

Schedule (annual taxable income, BWP):

  Resident individual:
    0 – 48,000             0 %
    48,001 – 84,000        5 % over 48,000              (base 0)
    84,001 – 120,000       12.5 % over 84,000           (base 1,800)
    120,001 – 156,000      18.75 % over 120,000         (base 6,300)
    156,001 +              25 % over 156,000            (base 13,050)

  Non-resident individual (no tax-free band):
    0 – 84,000             5 % over 0                   (base 0)
    84,001 – 120,000       12.5 % over 84,000           (base 4,200)
    120,001 – 156,000      18.75 % over 120,000         (base 8,700)
    156,001 +              25 % over 156,000            (base 15,450)

Effective 1 July 2024 (FY ending 30 June 2025).

Run:
    python manage.py seed_burs_2024_2025
    python manage.py seed_burs_2024_2025 --dry-run
"""

from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from payroll.models import TaxBracket


EFFECTIVE_FROM = date(2024, 7, 1)

RESIDENT_BRACKETS = [
    # (lower, upper, base, rate_pct)
    (Decimal('0'),       Decimal('48000'),  Decimal('0'),       Decimal('0')),
    (Decimal('48000'),   Decimal('84000'),  Decimal('0'),       Decimal('5')),
    (Decimal('84000'),   Decimal('120000'), Decimal('1800'),    Decimal('12.5')),
    (Decimal('120000'),  Decimal('156000'), Decimal('6300'),    Decimal('18.75')),
    (Decimal('156000'),  None,              Decimal('13050'),   Decimal('25')),
]

NON_RESIDENT_BRACKETS = [
    (Decimal('0'),       Decimal('84000'),  Decimal('0'),       Decimal('5')),
    (Decimal('84000'),   Decimal('120000'), Decimal('4200'),    Decimal('12.5')),
    (Decimal('120000'),  Decimal('156000'), Decimal('8700'),    Decimal('18.75')),
    (Decimal('156000'),  None,              Decimal('15450'),   Decimal('25')),
]


class Command(BaseCommand):
    help = ('Seed BURS 2024-25 PAYE tax brackets (resident + non-resident). '
            'Deactivates older bands so the new schedule is the only active set.')

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument(
            '--keep-old', action='store_true',
            help='Do NOT deactivate older brackets. Default deactivates them.',
        )

    def handle(self, *args, **opts):
        dry = opts['dry_run']

        existing_active = TaxBracket.objects.filter(is_active=True).count()

        with transaction.atomic():
            if not opts['keep_old']:
                touched = TaxBracket.objects.filter(is_active=True).exclude(
                    effective_from=EFFECTIVE_FROM,
                ).update(is_active=False)
                self.stdout.write(self.style.WARNING(
                    f'Deactivated {touched} pre-existing active bands '
                    f'(use --keep-old to preserve them).'
                ))

            created_r = self._seed(
                RESIDENT_BRACKETS,
                'BURS Resident Individual — FY2024-25 (eff 1 Jul 2024)',
            )
            created_n = self._seed(
                NON_RESIDENT_BRACKETS,
                'BURS Non-Resident Individual — FY2024-25 (eff 1 Jul 2024)',
            )

            if dry:
                transaction.set_rollback(True)

        new_active = TaxBracket.objects.filter(is_active=True).count()

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'{"DRY RUN  " if dry else ""}'
            f'Created/updated bands — resident: {created_r}, non-resident: {created_n}.'
        ))
        self.stdout.write(
            f'Active TaxBracket rows: {existing_active} → {new_active}.'
        )
        if dry:
            self.stdout.write(self.style.WARNING('No DB changes (--dry-run).'))

    def _seed(self, bands, label):
        count = 0
        for lo, hi, base, rate in bands:
            obj, created = TaxBracket.objects.update_or_create(
                effective_from=EFFECTIVE_FROM,
                lower_bound=lo,
                name=label,
                defaults={
                    'upper_bound': hi,
                    'base_amount': base,
                    'rate_pct':    rate,
                    'is_active':   True,
                },
            )
            cap = hi if hi is not None else '∞'
            tag = '+' if created else '~'
            self.stdout.write(f'  {tag} [{label.split(" — ")[0]}] {lo}–{cap} @ {rate}% (base {base})')
            count += 1
        return count
