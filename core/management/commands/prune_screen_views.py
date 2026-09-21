"""Delete screen-usage rows older than 180 days (CFO 2026-09-03 — telemetry is
kept for trend, not forever). Weekly from cron; idempotent.

  python manage.py prune_screen_views
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

RETENTION_DAYS = 180


class Command(BaseCommand):
    help = f"Delete ScreenView rows older than {RETENTION_DAYS} days."

    def handle(self, *args, **opts):
        from core.screen_view_models import ScreenView
        cutoff = timezone.now() - timezone.timedelta(days=RETENTION_DAYS)
        deleted, _ = ScreenView.objects.filter(minute__lt=cutoff).delete()
        self.stdout.write(f"Pruned {deleted} screen view(s) older than {cutoff:%Y-%m-%d}.")
