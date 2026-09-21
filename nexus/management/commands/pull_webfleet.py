"""
nexus/management/commands/pull_webfleet.py

Pull live WebFleet trips into Nexus: upsert drivers, ingest each new trip
through the scoring/ledger engine (services.record_trip), dedupe on the WebFleet
tripid so re-runs don't double-count. Read-only against WebFleet.

  python manage.py pull_webfleet                 # this month (m0)
  python manage.py pull_webfleet --range m-1     # last month
  python manage.py pull_webfleet --dry-run

Units (WEBFLEET.connect): distance = METERS, duration/idle_time = SECONDS.
Harsh-braking event counts need the OptiDrive report enabled on the account
(not available now) — so harsh_brakes defaults to 0; speeding comes from
speeding_indicator. WebFleet rate-limits (errorCode 8011) — run sparingly.
"""
from __future__ import annotations

from datetime import datetime, timezone as _tz

from django.core.management.base import BaseCommand
from django.utils.dateparse import parse_datetime

from core.date_format import format_dt
from nexus import webfleet
from nexus.models import NexusDriver, NexusTrip
from nexus.services import record_trip


def _dt(s):
    if not s:
        return None
    d = parse_datetime(str(s))
    if d:
        return d if d.tzinfo else d.replace(tzinfo=_tz.utc)
    for f in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
              "%d/%m/%Y %H:%M:%S", "%d-%m-%Y %H:%M:%S"):
        try:
            return datetime.strptime(str(s), f).replace(tzinfo=_tz.utc)
        except ValueError:
            continue
    return None


def _num(v):
    try:
        return float(str(v).replace(",", ".") or 0)
    except (TypeError, ValueError):
        return 0.0


class Command(BaseCommand):
    help = "Pull live WebFleet trips into Nexus (drivers + scored trips)."

    def add_arguments(self, parser):
        parser.add_argument("--range", default="m0",
                            help="WebFleet range_pattern: m0 (this month), m-1, w0, w-1, d-1.")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        if not webfleet.is_configured():
            self.stdout.write(self.style.WARNING(
                "SKIPPED: WEBFLEET_ACCOUNT / _USERNAME / _PASSWORD / _APIKEY not set."))
            return
        rng = opts["range"]
        dry = opts["dry_run"]

        try:
            drv = webfleet.drivers()
        except Exception as e:  # noqa: BLE001
            self.stderr.write(self.style.WARNING(f"driver fetch failed ({e}); continuing"))
            drv = []
        name_by_uid = {}
        for d in drv:
            uid = str(d.get("driveruid") or d.get("driverno") or "")
            nm = (d.get("name1") or "").strip() or (f"Driver {d.get('driverno')}" if d.get("driverno") else "")
            if uid and nm:
                name_by_uid[uid] = nm

        try:
            trips = webfleet.trips(rng)
        except webfleet.WebfleetError as e:
            self.stderr.write(self.style.ERROR(f"trip fetch failed: {e}"))
            return
        self.stdout.write(f"WebFleet: {len(drv)} drivers, {len(trips)} trips in range {rng}")

        seen = new = 0
        logged = False
        for t in trips:
            seen += 1
            tid = str(t.get("tripid") or "")
            if tid and NexusTrip.objects.filter(external_trip_id=tid).exists():
                continue
            distance_km = round(_num(t.get("distance")) / 1000.0, 2)   # m -> km
            duration_minutes = int(round(_num(t.get("duration")) / 60.0))   # s -> min
            idle_minutes = int(round(_num(t.get("idle_time")) / 60.0))      # s -> min
            speeding = int(_num(t.get("speeding_indicator")))
            opti = t.get("optidrive_indicator")
            uid = str(t.get("driveruid") or t.get("driverno") or "")
            nm = ((t.get("drivername") or "").strip() or name_by_uid.get(uid)
                  or (f"Driver {t.get('driverno')}" if t.get("driverno") else "Unknown driver"))

            if not logged:
                self.stdout.write(
                    f"  sample raw: distance={t.get('distance')} duration={t.get('duration')} "
                    f"idle_time={t.get('idle_time')} speeding_indicator={t.get('speeding_indicator')} "
                    f"optidrive={opti}  ->  {distance_km}km {duration_minutes}min idle={idle_minutes}min")
                logged = True
            if dry:
                continue

            if uid:
                driver, _ = NexusDriver.objects.get_or_create(
                    external_ref=uid, defaults={"full_name": nm})
            else:
                driver, _ = NexusDriver.objects.get_or_create(full_name=nm)
            if nm and not nm.startswith("Driver ") and driver.full_name != nm:
                driver.full_name = nm
                driver.save(update_fields=["full_name"])

            started = _dt(t.get("start_time")) or datetime.now(_tz.utc)
            # Human label: "Tue 24 Jun · 4:30 pm" — no vendor name, no raw metric.
            label = (format_dt(started, "%a %-d %b · %-I:%M %p")
                     .replace("AM", "am").replace("PM", "pm"))
            # Score = WebFleet's OptiDrive driving-quality (0-1) ×100 — the real
            # telematics score — when present; else the computed fallback.
            wf_score = None
            try:
                ov = float(opti)
                if 0.0 <= ov <= 1.0:
                    wf_score = round(ov * 100)
            except (TypeError, ValueError):
                wf_score = None
            trip = record_trip(
                driver,
                started_at=started,
                distance_km=distance_km, duration_minutes=duration_minutes,
                idle_minutes=idle_minutes, harsh_brakes=0, speeding_events=speeding,
                label=label[:120], score=wf_score,
            )
            if tid:
                NexusTrip.objects.filter(pk=trip.pk).update(external_trip_id=tid)
            new += 1

        self.stdout.write(self.style.SUCCESS(
            f"Nexus WebFleet pull: seen={seen} new={new} dry_run={dry}"))
