"""
Management command: revalue_period

Wraps `fx.services.revalue_period` so the CFO can run a period-end
unrealised FX revaluation from the shell.

Usage::

    python manage.py revalue_period --company ADIC --period-end 2026-03-31

The company argument is the Company.code (case-insensitive). period-end
must be ISO-8601 YYYY-MM-DD.
"""

from __future__ import annotations

from datetime import datetime

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = (
        'Run an unrealised FX revaluation for one company at a given '
        'period-end date. Idempotent on (company, period_end).'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--company', required=True,
            help='Company.code (e.g. ADIC). Case-insensitive.',
        )
        parser.add_argument(
            '--period-end', required=True,
            help='Period-end date in ISO-8601 form (e.g. 2026-03-31).',
        )
        parser.add_argument(
            '--user', default=None,
            help='Username to attribute the JE to. Defaults to the "system" user.',
        )

    def handle(self, *args, **opts):
        from core.models import Company
        from django.contrib.auth.models import User

        from fx.services import revalue_period

        # ---- Parse args ----------------------------------------------------
        try:
            period_end = datetime.strptime(opts['period_end'], '%Y-%m-%d').date()
        except ValueError as exc:
            raise CommandError(f"Invalid --period-end: {exc}")

        try:
            company = Company.objects.get(code__iexact=opts['company'])
        except Company.DoesNotExist:
            raise CommandError(
                f"Company '{opts['company']}' not found. "
                f"Available: {list(Company.objects.values_list('code', flat=True))}"
            )

        user = None
        if opts.get('user'):
            try:
                user = User.objects.get(username=opts['user'])
            except User.DoesNotExist:
                raise CommandError(f"User '{opts['user']}' not found.")

        self.stdout.write(self.style.NOTICE(
            f"revalue_period: company={company.code} period_end={period_end}"
        ))

        run = revalue_period(period_end, company.id, user=user)

        if run.journal_entry_id is None:
            self.stdout.write(self.style.WARNING(
                f"No revaluation JE posted (total_delta={run.total_delta_bwp})."
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"Posted JE {run.journal_entry.entry_number} — "
                f"total_delta_bwp={run.total_delta_bwp}"
            ))
