"""
integrations/management/commands/pull_graphite_claims.py

Mirror the Graphite V2 claims register into omni (integrations.GraphiteClaim).
Read-only feed — no GL posting. Idempotent: re-running upserts on graphite_id,
so claim status changes are picked up on the next sweep.

Usage:
  python manage.py pull_graphite_claims            # full sweep
  python manage.py pull_graphite_claims --dry-run  # count only, no writes
  python manage.py pull_graphite_claims --max-pages 5

Exits SKIPPED (non-fatal for crons) if GRAPHITE_FINANCE_API_BASE / _TOKEN unset.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

import time

from integrations.graphite_claims import (
    GraphiteClaimsClient, claims_configured, ingest_row, enrich_claim,
)


class Command(BaseCommand):
    help = 'Pull the claims register from Graphite V2 into omni (integrations.GraphiteClaim).'

    def add_arguments(self, parser):
        parser.add_argument('--max-pages', type=int, default=1000,
                            help='Safety cap on pages swept (default 1000).')
        parser.add_argument('--dry-run', action='store_true',
                            help='Count rows without writing.')
        parser.add_argument('--details', action='store_true',
                            help='Also fetch each claim detail (total reserve / '
                                 'payment / balance / date of loss). Heavier — one '
                                 'API call per claim, with 429 backoff + delay.')
        parser.add_argument('--detail-only', action='store_true',
                            help='Skip the list sweep; only enrich existing claims.')
        parser.add_argument('--detail-delay', type=float, default=0.3,
                            help='Seconds between detail calls (default 0.3).')
        parser.add_argument('--missing-only', action='store_true',
                            help='With --details: only enrich claims not yet '
                                 'detail-synced (resumable backfill).')

    def handle(self, *args, **opts):
        if not claims_configured():
            self.stdout.write(self.style.WARNING(
                'SKIPPED: Graphite Finance API not configured '
                '(GRAPHITE_FINANCE_API_BASE / GRAPHITE_FINANCE_API_TOKEN).'))
            return

        client = GraphiteClaimsClient()
        dry = opts['dry_run']
        seen = created = updated = 0

        if not opts['detail_only']:
            try:
                for row in client.iter_claims(max_pages=opts['max_pages']):
                    seen += 1
                    if dry:
                        continue
                    _obj, was_created = ingest_row(row)
                    if was_created:
                        created += 1
                    else:
                        updated += 1
            except Exception as e:  # noqa: BLE001 — report, don't crash a cron
                self.stderr.write(self.style.ERROR(
                    f'claims pull FAILED after seen={seen}: {type(e).__name__}: {e}'))
                raise
            self.stdout.write(self.style.SUCCESS(
                f'claims list pull done: seen={seen} created={created} '
                f'updated={updated} dry_run={dry}'))

        # Detail enrichment — one API call per claim (total reserve / payment /
        # balance / date of loss). Heavier; paced + 429-backed-off.
        if (opts['details'] or opts['detail_only']) and not dry:
            from integrations.models import GraphiteClaim
            qs = GraphiteClaim.objects.all()
            if opts['missing_only']:
                qs = qs.filter(detail_synced_at__isnull=True)
            ids = list(qs.values_list('graphite_id', 'pk'))
            self.stdout.write(f'enriching {len(ids)} claim details '
                              f'(missing_only={opts["missing_only"]})…')
            enriched = errors = 0
            delay = opts['detail_delay']
            for gid, pk in ids:
                obj = GraphiteClaim(pk=pk, graphite_id=gid)
                try:
                    if enrich_claim(client, obj):
                        enriched += 1
                except Exception as e:  # noqa: BLE001 — skip a bad claim, keep going
                    errors += 1
                    if errors <= 5:
                        self.stderr.write(f'  detail {gid} failed: {type(e).__name__}: {e}')
                if delay:
                    time.sleep(delay)
            self.stdout.write(self.style.SUCCESS(
                f'claims detail enrich done: enriched={enriched} errors={errors}'))
