"""
ledger/management/commands/seed_fiscal_years.py

CFO directive 2026-05-20 (post-Manus-TB-audit). For each Company, create
the surrounding FiscalYears based on `Company.fy_end_month`. Default
window: 2 years before the current FY through 2 years after, giving
FY24-FY28 for Botswana (June-end) entities. Idempotent.

Also (best-effort) back-links existing monthly FiscalPeriod rows to the
parent FiscalYear they fall inside, so the new schema is fully wired
without re-importing.

USAGE
-----
Dry-run:
    python manage.py seed_fiscal_years

Commit:
    python manage.py seed_fiscal_years --commit

Options:
    --years-before N    Default 2.  Number of fiscal years to seed before
                        the FY containing today.
    --years-after N     Default 2.  Number of fiscal years to seed after.
    --company CODE      Only seed for the given Company.code. May be
                        repeated.
"""

from __future__ import annotations

import calendar
from datetime import date
from typing import Iterable, Tuple

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone


def _last_day_of_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _fiscal_year_end_for(d: date, fy_end_month: int) -> date:
    """Return the date the fiscal year containing `d` ends on."""
    if d.month > fy_end_month:
        end_year = d.year + 1
    else:
        end_year = d.year
    return date(end_year, fy_end_month, _last_day_of_month(end_year, fy_end_month))


def _fiscal_year_start_for(end_date: date, fy_end_month: int) -> date:
    """The first day of the fiscal year that ends on `end_date`."""
    if fy_end_month == 12:
        return date(end_date.year, 1, 1)
    return date(end_date.year - 1, fy_end_month + 1, 1)


def _label_for(end_date: date) -> str:
    """Botswana convention: 'FY{YY}' from end_date.year."""
    return f'FY{end_date.year % 100:02d}'


class Command(BaseCommand):
    help = 'Seed FiscalYear rows + back-link existing FiscalPeriods. CFO directive 2026-05-20.'

    def add_arguments(self, parser):
        parser.add_argument('--years-before', type=int, default=2)
        parser.add_argument('--years-after', type=int, default=2)
        parser.add_argument('--company', action='append', default=[],
                            help='Only seed for this Company.code. Repeatable.')
        parser.add_argument('--commit', action='store_true',
                            help='Apply changes. Without this flag the command is a dry-run.')

    def handle(self, *args, **opts):
        from core.models import Company
        from ledger.models import FiscalPeriod, FiscalYear

        ybefore = opts['years_before']
        yafter  = opts['years_after']
        commit  = opts['commit']
        wanted  = [c.strip().upper() for c in opts['company'] if c.strip()]

        companies = Company.objects.all()
        if wanted:
            companies = companies.filter(code__in=wanted)

        today = timezone.localdate()
        report = []

        with transaction.atomic():
            for c in companies.order_by('code'):
                fy_end_month = c.fy_end_month or 6
                current_end = _fiscal_year_end_for(today, fy_end_month)
                # current_end.year is the "FY label year" — offset around it.
                for offset in range(-ybefore, yafter + 1):
                    fy_end_year = current_end.year + offset
                    end_date = date(
                        fy_end_year, fy_end_month,
                        _last_day_of_month(fy_end_year, fy_end_month),
                    )
                    start_date = _fiscal_year_start_for(end_date, fy_end_month)
                    label = _label_for(end_date)

                    fy, was = FiscalYear.objects.get_or_create(
                        company=c, label=label,
                        defaults={
                            'start_date': start_date,
                            'end_date':   end_date,
                            'status':     FiscalYear.Status.OPEN,
                        },
                    )
                    if not was:
                        changed = []
                        if fy.start_date != start_date:
                            fy.start_date = start_date; changed.append('start_date')
                        if fy.end_date != end_date:
                            fy.end_date = end_date; changed.append('end_date')
                        if changed:
                            fy.save(update_fields=changed)
                    report.append(
                        f'[{c.code:8}] {label}  {start_date} → {end_date}  '
                        f'{"CREATED" if was else "EXISTS"}'
                    )

                # Back-link existing FiscalPeriod rows for this company. Look
                # at rows whose company FK is NULL (legacy) AND whose date
                # range falls inside this company's fiscal years.
                for fy in FiscalYear.objects.filter(company=c):
                    n = FiscalPeriod.objects.filter(
                        company__isnull=True,
                        start_date__gte=fy.start_date,
                        end_date__lte=fy.end_date,
                    ).count()
                    if n and commit:
                        # Materialise per-company copies of these monthly
                        # periods so each entity has its own lock state.
                        from ledger.models import FiscalPeriod as FP
                        for legacy in FP.objects.filter(
                            company__isnull=True,
                            start_date__gte=fy.start_date,
                            end_date__lte=fy.end_date,
                        ):
                            FP.objects.get_or_create(
                                company=c, period_name=legacy.period_name,
                                defaults={
                                    'start_date':  legacy.start_date,
                                    'end_date':    legacy.end_date,
                                    'status':      legacy.status,
                                    'fiscal_year': fy,
                                },
                            )
                    if n:
                        report.append(
                            f'[{c.code:8}] {fy.label}  back-linked '
                            f'{n} legacy FiscalPeriod row(s)'
                        )

            if not commit:
                transaction.set_rollback(True)

        for line in report:
            self.stdout.write(line)
        if not commit:
            self.stdout.write(self.style.WARNING(
                'DRY RUN — re-run with --commit to apply.'
            ))
        else:
            self.stdout.write(self.style.SUCCESS('Applied.'))
