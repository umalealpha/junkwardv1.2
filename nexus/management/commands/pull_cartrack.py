"""
python manage.py pull_cartrack            # sync company fleet from Cartrack
python manage.py pull_cartrack --demo      # seed 3 clearly-labelled sample cars
python manage.py pull_cartrack --clear-demo  # remove the sample cars

Refreshes nexus.FleetVehicle from the Cartrack Fleet API (company vehicles +
latest position). Idempotent: upsert on registration. Read-only against
Cartrack. Credentials from settings.CARTRACK_* (env), never chat.

Run a sparing cron (Cartrack allows 60 status calls/min; one sweep per run is
plenty for a small company fleet).
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from nexus import cartrack
from nexus.models import FleetVehicle

# Clearly-fake sample cars around Gaborone — only for previewing the /fleet page
# before the real Cartrack key is connected. Removed by --clear-demo or replaced
# by the first real sync.
DEMO_CARS = [
    dict(registration="DEMO B 001", make="Toyota", model="Hilux", last_lat=-24.6282,
         last_lng=25.9231, last_speed_kmh=54.0, moving=True, ignition_on=True,
         odometer_km=84213.0, where="Western Bypass, Gaborone", driver_name="(sample)"),
    dict(registration="DEMO B 002", make="Ford", model="Ranger", last_lat=-24.6545,
         last_lng=25.9086, last_speed_kmh=0.0, moving=False, ignition_on=False,
         odometer_km=120340.0, where="Head Office, CBD", driver_name="(sample)"),
    dict(registration="DEMO B 003", make="VW", model="Polo", last_lat=-24.5580,
         last_lng=25.9100, last_speed_kmh=31.0, moving=True, ignition_on=True,
         odometer_km=45120.0, where="Mogoditshane", driver_name="(sample)"),
]


class Command(BaseCommand):
    help = "Sync company fleet vehicles + positions from Cartrack."

    def add_arguments(self, parser):
        parser.add_argument("--demo", action="store_true",
                            help="Seed 3 clearly-labelled sample cars (UI preview).")
        parser.add_argument("--clear-demo", action="store_true",
                            help="Remove the sample cars.")

    def handle(self, *args, **opts):
        if opts["clear_demo"]:
            n = FleetVehicle.objects.filter(is_demo=True).delete()[0]
            self.stdout.write(self.style.SUCCESS(f"Removed {n} demo vehicle(s)."))
            return

        if opts["demo"]:
            for c in DEMO_CARS:
                FleetVehicle.objects.update_or_create(
                    registration=c["registration"],
                    defaults={**c, "is_demo": True, "last_seen": "sample"})
            self.stdout.write(self.style.SUCCESS(
                f"Seeded {len(DEMO_CARS)} demo vehicle(s). Clear with --clear-demo."))
            return

        if not cartrack.is_configured():
            self.stdout.write(self.style.WARNING(
                "Cartrack not configured (CARTRACK_USERNAME / CARTRACK_PASSWORD "
                "not set). Nothing synced. Add the API credentials to go live, "
                "or run --demo to preview the page."))
            return

        try:
            fleet = cartrack.vehicles()
            statuses = cartrack.vehicle_status()
        except cartrack.CartrackError as e:
            self.stderr.write(self.style.ERROR(f"Cartrack API error: {e}"))
            return

        # Index status by vehicle id and by registration for a robust join.
        by_id, by_reg = {}, {}
        for row in statuses:
            s = cartrack.normalise_status(row)
            s["raw"] = row
            if s.get("cartrack_id"):
                by_id[str(s["cartrack_id"])] = s
            if s.get("registration"):
                by_reg[str(s["registration"]).upper()] = s

        created = updated = 0
        for v in fleet:
            reg = cartrack._first(v, "registration", "reg", "vehicle_registration",
                                  "plate", "number_plate", "licence_plate")
            vid = str(cartrack._first(v, "vehicle_id", "id", "vehicleId", "terminal_id") or "").strip()
            if not reg and vid in by_id:
                reg = by_id[vid].get("registration")
            if not reg:
                continue
            s = by_id.get(vid) or by_reg.get(str(reg).upper()) or {}
            defaults = dict(
                cartrack_id=vid,
                make=cartrack._first(v, "manufacturer", "make", "brand") or "",
                model=cartrack._first(v, "model", "vehicle_model") or "",
                description=cartrack._first(v, "description", "name", "fleet_number") or "",
                last_lat=s.get("lat"), last_lng=s.get("lng"),
                last_speed_kmh=s.get("speed"), odometer_km=s.get("odometer_km"),
                ignition_on=s.get("ignition_on"), moving=s.get("moving"),
                where=(s.get("where") or "")[:200], driver_name=(s.get("driver_name") or "")[:120],
                last_seen=str(s.get("last_seen") or "")[:40],
                is_demo=False, raw=s.get("raw", {}),
            )
            _, was_created = FleetVehicle.objects.update_or_create(
                registration=str(reg)[:32], defaults=defaults)
            created += int(was_created)
            updated += int(not was_created)

        self.stdout.write(self.style.SUCCESS(
            f"Cartrack fleet sync: {created} created, {updated} updated, "
            f"{FleetVehicle.objects.filter(is_demo=False).count()} live vehicle(s)."))
