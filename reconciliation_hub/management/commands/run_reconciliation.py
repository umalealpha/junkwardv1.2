"""Run a reconciliation pass for a period.

    python manage.py run_reconciliation --period FY25 --to 2025-06-30 [--from 2024-07-01]
                                        [--company ADIC] [--tolerance 5]

Intended for the nightly cron (~05:00, after Graphite/source overnight loads).
"""

from datetime import date

from django.core.management.base import BaseCommand, CommandError

from core.models import Company
from reconciliation_hub.services import run_reconciliation


class Command(BaseCommand):
    help = 'Run a source-to-ledger reconciliation pass for a period.'

    def add_arguments(self, parser):
        parser.add_argument('--period', required=True, help='Period label, e.g. FY25 or 2026-03')
        parser.add_argument('--to', required=True, help='Period end date YYYY-MM-DD')
        parser.add_argument('--from', dest='from_', default=None, help='Period start YYYY-MM-DD')
        parser.add_argument('--company', default=None, help='Company code (default: ADIC scope)')
        parser.add_argument('--tolerance', type=float, default=None, help='Variance tolerance %%')

    def handle(self, *args, **opts):
        try:
            period_end = date.fromisoformat(opts['to'])
            period_start = date.fromisoformat(opts['from_']) if opts['from_'] else None
        except ValueError as exc:
            raise CommandError(f'Bad date: {exc}')

        company = None
        if opts['company']:
            company = Company.objects.filter(code__iexact=opts['company']).first()
            if company is None:
                raise CommandError(f"Company code '{opts['company']}' not found.")

        run = run_reconciliation(
            period_label=opts['period'],
            period_end=period_end,
            period_start=period_start,
            company=company,
            tolerance_pct=opts['tolerance'],
        )

        self.stdout.write(self.style.SUCCESS(
            f'Run {run.id} [{run.status}] {run.company.code} {run.period_label}'))
        for line in run.lines.all():
            self.stdout.write(
                f'  {line.metric_key:18} src={line.source_total} '
                f'gl={line.omni_posted} var%={line.variance_pct} [{line.status}]')
