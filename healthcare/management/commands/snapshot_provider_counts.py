"""Capture today's provider-network headline numbers into ProviderDailySnapshot.

Schedule AFTER the evening dashboard send so it records the same day's data:
    python manage.py snapshot_provider_counts

Idempotent: running twice on the same day updates the row, never duplicates.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.utils import timezone

from healthcare import provider_registry as reg
from healthcare.models import ProviderDailySnapshot, ServiceProvider


class Command(BaseCommand):
    help = "Snapshot today's ADH provider-network counts for trend tracking."

    def handle(self, *args, **opts):
        today = timezone.localdate()
        c = reg.dashboard_counts()
        providers = list(ServiceProvider.objects.filter(is_active=True))
        qc_count = sum(1 for p in providers if p.qc_confirmed_flag)

        obj, created = ProviderDailySnapshot.objects.update_or_create(
            date=today,
            defaults={
                "total": c["total"],
                "afa_registered": c["afa_registered"],
                "afa_pending": c["afa_pending"],
                "adh_ready": c["adh_ready"],
                "qc_confirmed": qc_count,
                "mismatches": c["mismatches"],
                "pending_applications": c["pending_applications"],
            },
        )
        verb = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(
            f"{verb} snapshot for {today}: {c['adh_ready']} ready / {c['total']} total"))
