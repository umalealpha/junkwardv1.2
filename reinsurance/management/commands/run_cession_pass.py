"""
reinsurance/management/commands/run_cession_pass.py

Auto-cession pass — bulk-generate draft Cession rows for every posted
customer invoice in `--period` and every active treaty covering it.

Usage:
    python manage.py run_cession_pass --company ADIC --period 2026-04
    python manage.py run_cession_pass --company ADIC --period FY26_9M
    python manage.py run_cession_pass --period 2026-04                # all companies

The pass is idempotent — re-running it skips any (invoice, treaty) pair
that already has a Cession row.
"""

from django.core.management.base import BaseCommand, CommandError

from core.models import Company
from reinsurance.cession_service import run_cession_pass


class Command(BaseCommand):
    help = (
        'Generate draft Cession rows for every posted customer invoice in '
        'the period × every active treaty that covers it. Idempotent.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--period', required=True,
            help='Fiscal period name (e.g. 2026-04) or label (e.g. FY26_9M).',
        )
        parser.add_argument(
            '--company', default=None,
            help='Optional company code (e.g. ADIC). Omit to run for all companies.',
        )

    def handle(self, *args, **opts):
        company_id = None
        if opts.get('company'):
            company = Company.objects.filter(code=opts['company']).first()
            if not company:
                raise CommandError(f'Unknown company code: {opts["company"]}')
            company_id = company.id

        result = run_cession_pass(opts['period'], company_id=company_id)

        self.stdout.write(self.style.SUCCESS(
            f'\nAuto-cession pass — {result.period}'
            f'{" (company=" + opts["company"] + ")" if opts.get("company") else ""}'
        ))
        self.stdout.write(f'  Treaties considered:   {result.treaties_considered}')
        self.stdout.write(f'  Invoices processed:    {result.invoices_processed}')
        self.stdout.write(f'  Cessions created:      {result.cessions_created}')
        self.stdout.write(f'  Cessions skipped:      {result.cessions_skipped}')
