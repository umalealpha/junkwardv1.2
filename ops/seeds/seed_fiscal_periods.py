#!/usr/bin/env python
"""
seed_fiscal_periods.py

Seeds the 24 monthly FiscalPeriod records covering FY25 (1 Jul 2024 - 30 Jun 2025)
and FY26 (1 Jul 2025 - 30 Jun 2026) into production. After running, the
Period Close dropdown and any other "select fiscal period" filter will list
every month across both fiscal years.

Botswana fiscal year runs July-June. Period naming convention: YYYY-MM
matching what's already in the DB (e.g. 2024-07, 2025-06).

Run from alpha-finance repo root on production:
    python manage.py shell < seed_fiscal_periods.py

Idempotent: skips any month that already exists by start_date.
"""
from calendar import monthrange
from datetime import date

from ledger.models import FiscalPeriod


def _month_iter(start_year, start_month, count):
    y, m = start_year, start_month
    for _ in range(count):
        yield y, m
        m += 1
        if m == 13:
            m = 1
            y += 1


def main():
    # FY25 starts July 2024 = 12 months; FY26 starts July 2025 = 12 months
    created = 0
    skipped = 0
    for y, m in _month_iter(2024, 7, 24):
        start = date(y, m, 1)
        end   = date(y, m, monthrange(y, m)[1])
        name  = f'{y:04d}-{m:02d}'
        if FiscalPeriod.objects.filter(start_date=start).exists():
            skipped += 1
            print(f'  skip {name} (already exists)')
            continue
        FiscalPeriod.objects.create(
            period_name=name,
            start_date=start,
            end_date=end,
            status='open',
        )
        created += 1
        print(f'  +    {name}  {start} to {end}')
    print()
    print(f'Done. Created {created} period(s), skipped {skipped}.')
    print(f'Total fiscal periods in DB now: {FiscalPeriod.objects.count()}')


main()
