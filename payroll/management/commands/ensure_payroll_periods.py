"""ensure_payroll_periods — idempotent monthly seed.

Creates PayrollPeriod rows for every month from earliest existing period
through 2 months past today. Safe to run daily.

BUG-4 fix (CFO 2026-06-09): May/June 2026 had no PayrollPeriod row, so
the HRIS Payroll page dropdown was stuck at 2026-04.

Usage:
    python manage.py ensure_payroll_periods            # do it
    python manage.py ensure_payroll_periods --dry-run  # preview only
"""
import calendar
from datetime import date
from django.core.management.base import BaseCommand
from payroll.models import PayrollPeriod
from django.utils import timezone


def _month_iter(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m += 1
        if m == 13:
            m = 1; y += 1


class Command(BaseCommand):
    help = 'Ensure a PayrollPeriod row exists for every month up to current_month + 2.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **opts):
        dry = bool(opts.get('dry_run'))
        earliest = PayrollPeriod.objects.order_by('start_date').first()
        if not earliest:
            self.stdout.write('No PayrollPeriod rows yet — nothing to extend.')
            return
        today = timezone.localdate()
        # 2 months past today
        last_y, last_m = today.year, today.month + 2
        if last_m > 12:
            last_m -= 12; last_y += 1
        end_marker = date(last_y, last_m, 1)
        created = 0
        for y, m in _month_iter(earliest.start_date.replace(day=1), end_marker):
            if PayrollPeriod.objects.filter(start_date__year=y, start_date__month=m).exists():
                continue
            start = date(y, m, 1)
            end   = date(y, m, calendar.monthrange(y, m)[1])
            label = f'{y}-{m:02d}'
            if dry:
                self.stdout.write(f'  would create {label} ({start}..{end})')
            else:
                PayrollPeriod.objects.create(
                    period_name=label, start_date=start, end_date=end, status='open',
                )
                self.stdout.write(self.style.SUCCESS(f'  created {label}'))
            created += 1
        self.stdout.write(self.style.SUCCESS(
            f'\n{("would create" if dry else "created")} {created} period(s).'
        ))
