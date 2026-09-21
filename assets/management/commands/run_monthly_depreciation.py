"""
assets/management/commands/run_monthly_depreciation.py

Bulk depreciate every active asset for one fiscal period.

Usage:
    python manage.py run_monthly_depreciation 2026-04
    python manage.py run_monthly_depreciation 2026-04 --dry-run
    python manage.py run_monthly_depreciation 2026-04 --company ADI
"""

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from assets.services import run_monthly_depreciation
from core.models import Company
from ledger.models import FiscalPeriod


class Command(BaseCommand):
    help = 'Post one month of depreciation for every active asset.'

    def add_arguments(self, parser):
        parser.add_argument('period', help='Fiscal period name, e.g. 2026-04')
        parser.add_argument('--company', help='Optional company code to scope to.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Compute without persisting (rolls back transaction).')
        parser.add_argument('--user', default='admin',
                            help='Username to record as the posting user (default admin).')

    def handle(self, *args, **opts):
        period = FiscalPeriod.objects.filter(period_name=opts['period']).first()
        if not period:
            raise CommandError(f'No fiscal period named {opts["period"]}.')

        company = None
        if opts.get('company'):
            company = Company.objects.filter(code=opts['company']).first()
            if not company:
                raise CommandError(f'Unknown company code: {opts["company"]}')

        try:
            user = User.objects.get(username=opts['user'])
        except User.DoesNotExist:
            user = User.objects.filter(is_superuser=True).first()
            if user is None:
                raise CommandError('No superuser found to post as.')

        result = run_monthly_depreciation(
            period, user, company=company, dry_run=opts['dry_run'],
        )

        self.stdout.write(self.style.SUCCESS(
            f'\nDepreciation run for {result.period} '
            f'{"(DRY RUN — rolled back)" if opts["dry_run"] else ""}'
        ))
        self.stdout.write(f'  Considered:        {result.assets_considered}')
        self.stdout.write(f'  Depreciated:       {result.assets_depreciated}')
        self.stdout.write(f'  Skipped:           {result.assets_skipped}')
        self.stdout.write(f'  Fully depreciated: {result.assets_fully_depreciated}')
        self.stdout.write(f'  Total amount:      {result.total_amount}')
        if result.errors:
            self.stdout.write(self.style.WARNING(f'  Errors: {len(result.errors)}'))
            for e in result.errors[:10]:
                self.stdout.write(f'    - {e}')
