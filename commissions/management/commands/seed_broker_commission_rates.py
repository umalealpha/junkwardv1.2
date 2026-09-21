"""Seed the settled broker commission rates (CFO 2026-09-17).

MOTOR 12.5 / NON-MOTOR 20 / VAT 14 / ADMIN 8 / WHT 10.

`effective_from` is the FIRST DAY OF THE CURRENT MONTH, not today. The summary
picks the rate row in force on the first of the period it is showing, so a row
dated the 17th would leave the month it was created in with no rates at all —
every broker blocked, on the very month somebody was trying to look at.

The date is taken in Gaborone time. `timezone.now().date()` is the UTC date: at
00:30 CAT on the 1st it still reads the previous month and would seed the wrong
period.
"""
from __future__ import annotations

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from commissions.models import BrokerCommissionRate

SETTLED = {
    'motor_pct': Decimal('12.500'),
    'non_motor_pct': Decimal('20.000'),
    'vat_pct': Decimal('14.000'),
    'admin_pct': Decimal('8.000'),
    'wht_pct': Decimal('10.000'),
    'note': 'Settled rates, CFO 2026-09-17. Not yet reconciled to a Finance workbook.',
}


class Command(BaseCommand):
    help = 'Seed the settled broker commission rates, effective from the 1st of this month.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--effective-from', default='',
            help='YYYY-MM-DD to seed a specific date instead of the 1st of this month.')

    def handle(self, *args, **options):
        raw = (options.get('effective_from') or '').strip()
        if raw:
            import datetime as _dt
            effective_from = _dt.date.fromisoformat(raw)
        else:
            today = timezone.localtime().date()
            effective_from = today.replace(day=1)

        row, created = BrokerCommissionRate.objects.get_or_create(
            effective_from=effective_from, defaults=SETTLED)

        if created:
            self.stdout.write(self.style.SUCCESS(
                f'Seeded commission rates effective {effective_from:%Y-%m-%d}.'))
        else:
            # Never overwrite: an existing row may be a deliberate rate change,
            # and silently restating it would restate every past payable.
            self.stdout.write(self.style.WARNING(
                f'Rates already exist from {effective_from:%Y-%m-%d} — left untouched '
                f'(motor {row.motor_pct}%, non-motor {row.non_motor_pct}%).'))
