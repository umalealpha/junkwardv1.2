"""payroll/management/commands/feed_severance.py - Severance Connect (Build Spec B11, marker AUTO-SEVERANCE).

Usage:  python manage.py feed_severance --period 2026-10 [--company ADIC] [--dry-run]

Creates a PENDING payroll amendment batch. It never applies it, never posts a
journal and never moves money — the monthly close reviews and applies, and
dual sign-off still pays.
"""
from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = "Put this period's terminations in front of the payroll close."

    def add_arguments(self, parser):
        parser.add_argument('--period', required=True,
                            help='Payroll period name, e.g. 2026-10')
        parser.add_argument('--company', default=None,
                            help='Limit to one entity by company code.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Roll everything back at the end; print what would happen.')

    def handle(self, *args, **opts):
        from core.models import Company
        from payroll.severance_feed import feed_period

        company = None
        if opts['company']:
            company = Company.objects.filter(code__iexact=opts['company']).first()
            if company is None:
                self.stderr.write(f"No company with code {opts['company']}.")
                return

        def _run():
            return feed_period(period_label=opts['period'], company=company)

        if opts['dry_run']:
            try:
                with transaction.atomic():
                    res = _run()
                    self.stdout.write(f'DRY RUN — rolling back: {res}')
                    raise _Rollback()
            except _Rollback:
                return
        else:
            res = _run()
            self.stdout.write(str(res))


class _Rollback(Exception):
    pass
