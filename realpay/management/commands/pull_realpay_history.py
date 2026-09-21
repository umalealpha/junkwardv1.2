"""Backfill RealPay monthly reports from a start date to today.

Usage:
  python manage.py pull_realpay_history \
      --from 2025-07-01 \
      --beneficiary-user 19413 \
      --label "Genric Integration (BW UAT)"
"""

from __future__ import annotations

import datetime

from django.core.management.base import BaseCommand, CommandError

from realpay.services import backfill


class Command(BaseCommand):
    help = 'Backfill RealPay monthly reports from a start date to today.'

    def add_arguments(self, parser):
        parser.add_argument('--from', dest='from_date',
                            default='2025-07-01',
                            help='Start date YYYY-MM-DD (default: 2025-07-01)')
        parser.add_argument('--beneficiary-user', required=True,
                            help='RealPay beneficiary user ID (e.g. 19413)')
        parser.add_argument('--label', default='',
                            help='Optional display label for this beneficiary')

    def handle(self, *args, **opts):
        try:
            d = datetime.date.fromisoformat(opts['from_date'])
        except ValueError as exc:
            raise CommandError(f'--from must be YYYY-MM-DD: {exc}')
        reports = backfill(
            from_year=d.year, from_month=d.month,
            beneficiary_user_id=opts['beneficiary_user'],
            beneficiary_label=opts['label'],
        )
        self.stdout.write(self.style.SUCCESS(
            f'Backfilled {len(reports)} months for beneficiary '
            f'{opts["beneficiary_user"]}'
        ))
