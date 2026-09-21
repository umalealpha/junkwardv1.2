"""Seed the 8 CFO-authoritative frozen figures for ADIC FY25 + FY26-9M.

Source: CFO directive 2026-05-17 (handover § P1).

Idempotent — re-running updates existing rows by (period, line_label).
"""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

FROZEN = [
    # FY25 (1 Jul 2024 → 30 Jun 2025), full year
    ('FY25_Jun2025',  'GWP',          Decimal('125149000.00')),
    ('FY25_Jun2025',  'Total Assets', Decimal( '51975000.00')),
    ('FY25_Jun2025',  'Cash & Bank',  Decimal( '12884000.00')),
    ('FY25_Jun2025',  'PAT',          Decimal(   '292000.00')),

    # FY26-9M (1 Jul 2025 → 31 Mar 2026), 9-month YTD
    ('FY26_Mar2026', 'GWP',           Decimal( '96177000.00')),
    ('FY26_Mar2026', 'Total Assets',  Decimal( '47566000.00')),
    ('FY26_Mar2026', 'Cash & Bank',   Decimal( '10398000.00')),
    ('FY26_Mar2026', 'PAT',           Decimal(   '950000.00')),
]


class Command(BaseCommand):
    help = (
        'Seed/refresh the 8 CFO-authoritative FrozenFigure rows for ADIC. '
        'Idempotent: re-running updates existing rows by (period, line_label).'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--tolerance', type=str, default='1.00',
            help='Default tolerance percentage to assign on insert. Existing rows keep their tolerance.',
        )

    def handle(self, *args, **opt):
        from ledger.models import FrozenFigure

        default_tol = Decimal(opt['tolerance'])
        created, updated = 0, 0

        with transaction.atomic():
            for period, label, value in FROZEN:
                obj, was_created = FrozenFigure.objects.update_or_create(
                    period=period,
                    line_label=label,
                    defaults={
                        'value_bwp': value,
                        'is_active': True,
                    },
                )
                # Only set tolerance on initial create — never overwrite an
                # admin's manual adjustment on a subsequent re-seed.
                if was_created:
                    obj.tolerance_pct = default_tol
                    obj.save(update_fields=['tolerance_pct', 'updated_at'])
                    created += 1
                else:
                    updated += 1

        self.stdout.write(self.style.SUCCESS(
            f'FrozenFigure seed complete: {created} created, {updated} updated.'
        ))
        for ff in FrozenFigure.objects.filter(is_active=True).order_by('period', 'line_label'):
            self.stdout.write(f'  {ff}')
