"""Seed a demo Nexus driver with real, engine-scored trips (idempotent)."""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from nexus.models import NexusDriver, NexusRewardLedger, NexusTrip
from nexus.services import record_trip

DRIVER = "Thato Mononi"

# (label, distance_km, duration_min, idle_min, harsh_brakes, speeding) — varied
TRIPS = [
    ("Gaborone -> Tlokweng", 12.4, 21, 1, 0, 0),
    ("Airport run",          14.0, 19, 1, 1, 0),
    ("CBD loop",              6.2, 17, 4, 0, 0),
    ("Mogoditshane",          9.1, 15, 1, 2, 1),
    ("Phakalane evening",    11.7, 23, 2, 0, 0),
    ("Game City shop",        5.3, 14, 3, 1, 0),
    ("Lobatse highway",      42.6, 41, 1, 0, 0),
    ("School pickup",         7.8, 26, 8, 0, 0),
]


class Command(BaseCommand):
    help = "Seed a demo Nexus driver + engine-scored trips."

    def handle(self, *args, **opts):
        driver, _ = NexusDriver.objects.get_or_create(full_name=DRIVER)
        if driver.trips.exists():
            self.stdout.write(f"{DRIVER} already seeded ({driver.trips.count()} trips, "
                              f"{driver.total_points} pts). Nothing to do.")
            return
        now = timezone.now()
        for i, (label, dist, dur, idle, hb, sp) in enumerate(TRIPS):
            record_trip(driver, started_at=now - timedelta(days=i, hours=2),
                        distance_km=dist, duration_minutes=dur, idle_minutes=idle,
                        harsh_brakes=hb, speeding_events=sp, label=label)
        driver.refresh_from_db()
        self.stdout.write(self.style.SUCCESS(
            f"Seeded {DRIVER}: {driver.trips.count()} trips, {driver.total_points} points, "
            f"{NexusRewardLedger.objects.filter(driver=driver).count()} ledger entries."))
