"""Pull one month of LIVE RealPay collections for ALL beneficiary books.

Iterates the beneficiary books configured in settings.REALPAY_LIVE_BOOKS
(comma list of ``id:label`` pairs) and pulls the given month for each,
persisting/refreshing its RealPayMonthlyReport and AI commentary.

This is the job the nightly cron runs (default = current month, re-pulled so
the running month stays fresh) and is also the go-live verification tool.

Usage:
  python manage.py pull_realpay_live                 # current month, all books
  python manage.py pull_realpay_live --month 2026-08 # a specific month
  python manage.py pull_realpay_live --book 16244    # just one book (verify)
"""
from __future__ import annotations

import datetime

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from realpay.services import pull_month, generate_commentary


def _books() -> list[tuple[str, str]]:
    raw = (getattr(settings, 'REALPAY_LIVE_BOOKS', '') or '').strip()
    books: list[tuple[str, str]] = []
    for part in raw.split(','):
        part = part.strip()
        if not part:
            continue
        bid, _, label = part.partition(':')
        bid = bid.strip()
        if bid:
            books.append((bid, label.strip()))
    return books


class Command(BaseCommand):
    help = 'Pull one month of LIVE RealPay collections for all configured beneficiary books.'

    def add_arguments(self, parser):
        parser.add_argument('--month', default='',
                            help='Target month YYYY-MM (default: current month).')
        parser.add_argument('--book', default='',
                            help='Limit to a single beneficiary user id (default: all).')

    def handle(self, *args, **opts):
        if opts['month']:
            try:
                d = datetime.date.fromisoformat(opts['month'] + '-01')
            except ValueError as exc:
                raise CommandError(f'--month must be YYYY-MM: {exc}')
            year, month = d.year, d.month
        else:
            today = timezone.localdate()   # Botswana time, not the UTC container clock
            year, month = today.year, today.month

        books = _books()
        if not books:
            raise CommandError(
                'No beneficiary books configured. Set REALPAY_LIVE_BOOKS '
                '(e.g. "16244:Alpha Direct Insurance,24936:Alpha Direct '
                'Instant Insurance,19111:Alpha Direct Third Parties").'
            )
        if opts['book']:
            books = [(b, lbl) for b, lbl in books if b == opts['book'].strip()]
            if not books:
                raise CommandError(f'Book {opts["book"]!r} not in REALPAY_LIVE_BOOKS.')

        failures = []
        for bid, label in books:
            # One book failing (network/vendor) must not stop the others — this
            # is the nightly job. Record it and fail the run at the end so cron
            # surfaces a non-zero exit.
            try:
                report = pull_month(year=year, month=month,
                                    beneficiary_user_id=bid, beneficiary_label=label)
            except Exception as exc:  # noqa: BLE001 — isolate per book
                failures.append(bid)
                self.stderr.write(f'  {label or bid} FAILED: {exc}')
                continue
            try:
                generate_commentary(report)
            except Exception as exc:  # noqa: BLE001 — commentary is best-effort
                self.stderr.write(f'  commentary failed for {bid}: {exc}')
            self.stdout.write(self.style.SUCCESS(
                f'{label or bid} {year}-{month:02d}: '
                f'collected={getattr(report, "amount_collected", "?")} '
                f'failed={getattr(report, "amount_failed", "?")} '
                f'status={report.status}'
            ))
        ok = len(books) - len(failures)
        self.stdout.write(self.style.SUCCESS(
            f'Live pull complete: {ok}/{len(books)} book(s) for {year}-{month:02d}.'))
        if failures:
            raise CommandError(
                f'{len(failures)} book(s) failed: {", ".join(failures)}')
