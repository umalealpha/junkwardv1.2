"""
integrations/management/commands/pull_graphite_payments.py

Pull payment transactions from the Graphite V2 Finance API into omni's local
mirror (integrations.GraphitePaymentTransaction). Read-only feed — no GL
posting. Idempotent: re-running a window upserts rows on graphite_id, so late
status changes / refund flips are picked up on the next sweep.

Usage:
  # last 30 days (default)
  python manage.py pull_graphite_payments

  # explicit window (auto-chunked into 30-day slabs)
  python manage.py pull_graphite_payments --from 2026-01-01 --to 2026-06-15

  # last N days
  python manage.py pull_graphite_payments --days 7

  # scope to one partner / status
  python manage.py pull_graphite_payments --days 7 --partner DPO --status success

  # see what would happen without writing
  python manage.py pull_graphite_payments --days 7 --dry-run

Requires (else exits SKIPPED, non-fatal for crons):
  GRAPHITE_FINANCE_API_BASE, GRAPHITE_FINANCE_API_TOKEN
"""

from __future__ import annotations

import datetime

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from integrations.graphite_finance import PARTNERS


class Command(BaseCommand):
    help = 'Pull payment transactions from the Graphite V2 Finance API into omni.'

    def add_arguments(self, parser):
        parser.add_argument('--from', dest='from_date',
                            help='Window start YYYY-MM-DD.')
        parser.add_argument('--to', dest='to_date',
                            help='Window end YYYY-MM-DD (default: today).')
        parser.add_argument('--days', type=int,
                            help='Pull the last N days (alternative to --from/--to).')
        parser.add_argument('--partner', default='',
                            help=f'Scope to one payment partner. One of: {", ".join(PARTNERS)}')
        parser.add_argument('--status', default='',
                            help='Scope to one status (case-insensitive).')
        parser.add_argument('--limit', type=int, default=500,
                            help='Page size (max 1000, default 500).')
        parser.add_argument('--dry-run', action='store_true',
                            help='Fetch and count, but do not write.')

    def handle(self, *args, **opts):
        # Import here so the placeholder import above never bites us.
        from integrations.graphite_finance import (
            GraphiteFinanceClient,
            GraphiteFinanceNotConfigured,
            GraphiteFinanceAPIError,
            iter_windows,
            ingest_row,
        )
        from integrations.models import GraphitePaymentSyncRun

        date_from, date_to = self._resolve_window(opts)
        partner  = (opts.get('partner') or '').strip()
        status   = (opts.get('status') or '').strip()
        limit    = max(1, min(int(opts.get('limit') or 500), 1000))
        dry_run  = bool(opts.get('dry_run'))

        if partner and partner not in PARTNERS:
            self.stdout.write(self.style.WARNING(
                f'--partner "{partner}" not in known set {PARTNERS}; sending anyway.'
            ))

        run = GraphitePaymentSyncRun.objects.create(
            status=GraphitePaymentSyncRun.Status.RUNNING,
            window_from=date_from, window_to=date_to,
            partner=partner, dry_run=dry_run,
            started_at=timezone.now(),
        )

        client = GraphiteFinanceClient()
        if not client.cfg.is_configured:
            run.status = GraphitePaymentSyncRun.Status.SKIPPED
            run.error_message = ('GRAPHITE_FINANCE_API_BASE / '
                                 'GRAPHITE_FINANCE_API_TOKEN not set.')
            run.finished_at = timezone.now()
            run.save(update_fields=['status', 'error_message', 'finished_at'])
            raise CommandError(
                'Graphite Finance API not configured. Set '
                'GRAPHITE_FINANCE_API_BASE and GRAPHITE_FINANCE_API_TOKEN, '
                'then re-run.'
            )

        filters = {}
        if partner:
            filters['partner'] = partner
        if status:
            filters['status'] = status

        seen = created = updated = pages = 0
        window_errors: list[str] = []

        for win_from, win_to in iter_windows(date_from, date_to):
            try:
                cursor = None
                while True:
                    page = client.list_payments(
                        win_from, win_to, limit=limit, cursor=cursor, **filters,
                    )
                    pages += 1
                    rows = page.get('data', []) or []
                    if not dry_run and rows:
                        with transaction.atomic():
                            for row in rows:
                                _, was_created = ingest_row(row, sync_run=run)
                                if was_created:
                                    created += 1
                                else:
                                    updated += 1
                    seen += len(rows)
                    meta = page.get('meta', {}) or {}
                    if not meta.get('has_more'):
                        break
                    cursor = meta.get('next_cursor')
                    if not cursor:
                        break
                self.stdout.write(
                    f'  {win_from} → {win_to}: {seen} rows so far '
                    f'({pages} pages)'
                )
            except (GraphiteFinanceAPIError, GraphiteFinanceNotConfigured) as e:
                msg = f'{win_from}→{win_to}: {e}'
                window_errors.append(msg)
                self.stderr.write(self.style.ERROR(f'  {msg}'))

        # Finalise run audit
        if window_errors and (created or updated or seen):
            run.status = GraphitePaymentSyncRun.Status.PARTIAL
        elif window_errors:
            run.status = GraphitePaymentSyncRun.Status.FAILED
        else:
            run.status = GraphitePaymentSyncRun.Status.SUCCESS
        run.pages_fetched = pages
        run.rows_seen     = seen
        run.rows_created  = created
        run.rows_updated  = updated
        run.error_message = '\n'.join(window_errors)[:4000]
        run.finished_at   = timezone.now()
        run.save()

        verb = 'Would import' if dry_run else 'Imported'
        style = self.style.SUCCESS if run.status == run.Status.SUCCESS else self.style.WARNING
        self.stdout.write(style(
            f'{verb} {seen} rows ({created} new, {updated} updated) '
            f'across {pages} pages for {date_from}→{date_to} '
            f'[run {run.id}, status={run.status}]'
        ))
        if window_errors:
            raise CommandError(
                f'{len(window_errors)} window(s) failed — see run {run.id}.'
            )

    # ---- helpers -----------------------------------------------------------

    def _resolve_window(self, opts) -> tuple[datetime.date, datetime.date]:
        today = timezone.localdate()
        if opts.get('days'):
            days = int(opts['days'])
            if days < 1:
                raise CommandError('--days must be >= 1')
            return today - datetime.timedelta(days=days - 1), today

        from_raw = opts.get('from_date')
        to_raw   = opts.get('to_date')
        if not from_raw and not to_raw:
            # default: last 30 days
            return today - datetime.timedelta(days=29), today
        if not from_raw:
            raise CommandError('--to given without --from')
        try:
            d_from = datetime.date.fromisoformat(from_raw)
        except ValueError as e:
            raise CommandError(f'--from must be YYYY-MM-DD: {e}')
        if to_raw:
            try:
                d_to = datetime.date.fromisoformat(to_raw)
            except ValueError as e:
                raise CommandError(f'--to must be YYYY-MM-DD: {e}')
        else:
            d_to = today
        if d_to < d_from:
            raise CommandError('--to must be >= --from')
        return d_from, d_to
