"""
nexus/management/commands/aggregate_phone_trips.py

Turn a phone's raw GPS ping stream (PhonePing rows, e.g. the CFO's Samsung
POSTing to /api/v1/nexus/phone-ping/) into SCORED Nexus trips. Today the phone
feed only shows a live position; this segments the pings into trips, computes
per-trip telematics metrics, scores each trip and ingests it through the same
scoring/ledger engine (services.record_trip) the WebFleet feed uses.

  python manage.py aggregate_phone_trips                       # all devices, last 7 days
  python manage.py aggregate_phone_trips --device GPSLogger    # one device
  python manage.py aggregate_phone_trips --since 2026-06-01    # explicit window
  python manage.py aggregate_phone_trips --dry-run             # segment + print, write nothing

Segmentation: pings for one device, ordered by recorded_at (fallback received_at),
are split into trips at any gap > TRIP_GAP_MINUTES of silence. A segment is kept
only if it has at least MIN_PINGS pings AND covers at least MIN_DISTANCE_KM —
this drops parked-phone jitter and single stray pings.

Metrics per trip:
  distance_km      haversine sum over consecutive (lat,lng)
  duration_minutes first -> last recorded_at
  max/avg speed    from speed_kmh on the pings
  speeding_events  count of pings over SPEED_LIMIT_KMH (a constant)
  harsh_brakes     always 0 — see note below
  idle_minutes     time spent in near-stationary pings (< IDLE_SPEED_KMH)

harsh_brakes is hard-zeroed: a phone GPS sample (typically every 10-60 s) is far
too coarse and too noisy to detect a real harsh-braking deceleration event —
inferring it from GPS speed deltas produces mostly false positives, so we report
0 rather than fabricate events. (Same stance as the WebFleet feed, which needs
the vehicle's OptiDrive report for real harsh-braking counts.)

Scoring: reuses scoring.compute_trip_score (the same engine as WebFleet and the
customer app) over the computed metrics — start 100, deduct per speeding event
and for idle above the grace allowance. No telematics-vendor score is available
from a phone, so we always pass the computed score.

Idempotent: each trip gets external_trip_id = f'phone-{device}-{start:%Y%m%d-%H%M}'
(capped to the 64-char column). Re-runs skip any trip whose external_trip_id
already exists, so running this repeatedly never double-counts.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone as _tz

from django.core.management.base import BaseCommand

from core.date_format import format_dt
from nexus.models import NexusDriver, NexusTrip, PhonePing
from nexus.services import record_trip

# --- tunable constants (CFO-tunable later via config) -----------------------
TRIP_GAP_MINUTES = 5        # > this much silence between pings starts a new trip
MIN_PINGS = 3               # ignore segments with fewer pings than this
MIN_DISTANCE_KM = 0.3       # ignore segments shorter than this (parked jitter)
SPEED_LIMIT_KMH = 120.0     # a ping faster than this counts as a speeding event
IDLE_SPEED_KMH = 5.0        # at/below this a ping is treated as stationary (idle)
EXTERNAL_ID_MAX = 64        # NexusTrip.external_trip_id column length


def _haversine_km(lat1, lng1, lat2, lng2) -> float:
    """Great-circle distance between two lat/lng points, in kilometres."""
    r = 6371.0088  # mean Earth radius (km)
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2)
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _ping_time(p: PhonePing):
    """Device timestamp if present, else server receive time (never None for
    a saved row — received_at is auto_now_add)."""
    return p.recorded_at or p.received_at


def segment_pings(pings, gap_minutes=TRIP_GAP_MINUTES):
    """Split an already-time-ordered list of PhonePing into trip segments.
    A gap of more than `gap_minutes` of silence ends the current segment."""
    gap = timedelta(minutes=gap_minutes)
    segments, current, prev_t = [], [], None
    for p in pings:
        t = _ping_time(p)
        if t is None:
            continue
        if prev_t is not None and (t - prev_t) > gap:
            if current:
                segments.append(current)
            current = []
        current.append(p)
        prev_t = t
    if current:
        segments.append(current)
    return segments


def trip_metrics(segment):
    """Compute trip metrics from a segment of pings (>=2 with coords assumed).
    Returns a dict, or None if the segment has no usable geo points."""
    geo = [p for p in segment if p.lat is not None and p.lng is not None]
    if len(geo) < 2:
        return None

    distance_km = 0.0
    for a, b in zip(geo, geo[1:]):
        distance_km += _haversine_km(a.lat, a.lng, b.lat, b.lng)

    times = [_ping_time(p) for p in geo]
    started_at = times[0]
    duration_minutes = max(0, round((times[-1] - times[0]).total_seconds() / 60.0))

    speeds = [float(p.speed_kmh or 0) for p in geo]
    max_speed = max(speeds) if speeds else 0.0
    avg_speed = (sum(speeds) / len(speeds)) if speeds else 0.0
    speeding_events = sum(1 for s in speeds if s > SPEED_LIMIT_KMH)

    # idle_minutes: prorate trip duration by the fraction of pings that are
    # near-stationary (phone GPS gives no per-ping dwell, so fraction-of-samples
    # is the honest estimate).
    idle_fraction = (sum(1 for s in speeds if s <= IDLE_SPEED_KMH) / len(speeds)) if speeds else 0.0
    idle_minutes = int(round(duration_minutes * idle_fraction))

    return {
        "started_at": started_at,
        "distance_km": round(distance_km, 2),
        "duration_minutes": duration_minutes,
        "idle_minutes": idle_minutes,
        "max_speed": round(max_speed, 1),
        "avg_speed": round(avg_speed, 1),
        "speeding_events": speeding_events,
        "harsh_brakes": 0,  # phone GPS cannot reliably detect — see module docstring
        "ping_count": len(geo),
    }


def _label(started_at) -> str:
    """Human label: 'Tue 24 Jun · 4:30 pm' — mirrors the WebFleet feed."""
    return (format_dt(started_at, "%a %-d %b · %-I:%M %p")
            .replace("AM", "am").replace("PM", "pm"))[:120]


def _external_id(device, started_at) -> str:
    """Stable dedupe key. Trim the device portion so the whole id fits the
    64-char column even for long device names."""
    stamp = started_at.strftime("%Y%m%d-%H%M")
    suffix = f"-{stamp}"                       # e.g. "-20260625-1430"
    room = EXTERNAL_ID_MAX - len("phone-") - len(suffix)
    dev = (device or "")[:max(0, room)]
    return f"phone-{dev}{suffix}"


class Command(BaseCommand):
    help = "Aggregate phone GPS pings into scored Nexus trips (dedupe on external_trip_id)."

    def add_arguments(self, parser):
        parser.add_argument("--device", default=None,
                            help="Only process this PhonePing.device (default: all devices).")
        parser.add_argument("--since", default=None,
                            help="ISO date/datetime; only pings at/after this. Default: last 7 days.")
        parser.add_argument("--dry-run", action="store_true",
                            help="Segment + print what would be created; write nothing.")

    def handle(self, *args, **opts):
        dry = opts["dry_run"]
        since = self._parse_since(opts["since"])

        qs = PhonePing.objects.filter(received_at__gte=since)
        if opts["device"]:
            qs = qs.filter(device=opts["device"])

        devices = sorted({p.device for p in qs.only("device")})
        if not devices:
            self.stdout.write(self.style.WARNING(
                f"No phone pings since {since:%Y-%m-%d %H:%M} "
                f"{'for device ' + opts['device'] if opts['device'] else '(any device)'}."))
            return

        self.stdout.write(
            f"Phone pings since {since:%Y-%m-%d %H:%M}: {len(devices)} device(s) "
            f"{'[' + ', '.join(devices) + ']' if len(devices) <= 6 else ''}")

        total_seen = total_new = total_skipped = 0
        for device in devices:
            pings = list(
                PhonePing.objects.filter(device=device, received_at__gte=since)
                .order_by("recorded_at", "received_at")
            )
            segments = segment_pings(pings)
            self.stdout.write(f"\n  {device}: {len(pings)} pings -> {len(segments)} candidate trip(s)")

            for seg in segments:
                m = trip_metrics(seg)
                if m is None:
                    continue
                # drop noise: too few pings or too short to be a real trip
                if m["ping_count"] < MIN_PINGS or m["distance_km"] < MIN_DISTANCE_KM:
                    self.stdout.write(
                        f"    skip noise: {m['ping_count']} pings, {m['distance_km']} km "
                        f"@ {m['started_at']:%Y-%m-%d %H:%M}")
                    continue

                total_seen += 1
                ext_id = _external_id(device, m["started_at"])
                if NexusTrip.objects.filter(external_trip_id=ext_id).exists():
                    total_skipped += 1
                    continue

                score = self._score(m)
                self.stdout.write(
                    f"    trip {_label(m['started_at'])}: {m['distance_km']} km, "
                    f"{m['duration_minutes']} min, max {m['max_speed']} km/h, "
                    f"avg {m['avg_speed']} km/h, idle {m['idle_minutes']} min, "
                    f"speeding={m['speeding_events']} -> score {score}  [{ext_id}]")

                if dry:
                    total_new += 1
                    continue

                driver = self._driver_for(device)
                trip = record_trip(
                    driver,
                    started_at=m["started_at"],
                    distance_km=m["distance_km"],
                    duration_minutes=m["duration_minutes"],
                    idle_minutes=m["idle_minutes"],
                    harsh_brakes=m["harsh_brakes"],
                    speeding_events=m["speeding_events"],
                    label=_label(m["started_at"]),
                    score=score,
                )
                NexusTrip.objects.filter(pk=trip.pk).update(external_trip_id=ext_id)
                total_new += 1

        self.stdout.write(self.style.SUCCESS(
            f"\nNexus phone aggregate: seen={total_seen} new={total_new} "
            f"already_present={total_skipped} dry_run={dry}"))

    # --- helpers -------------------------------------------------------------
    def _parse_since(self, raw):
        if not raw:
            return datetime.now(_tz.utc) - timedelta(days=7)
        from django.utils.dateparse import parse_datetime, parse_date
        d = parse_datetime(raw)
        if d is None:
            day = parse_date(raw)
            if day is not None:
                d = datetime(day.year, day.month, day.day)
        if d is None:
            raise SystemExit(f"--since: could not parse '{raw}' (use ISO date or datetime)")
        return d if d.tzinfo else d.replace(tzinfo=_tz.utc)

    def _score(self, m) -> int:
        # Reuse the shared engine — same scoring as WebFleet + the customer app.
        # No phone-side telematics score exists, so always compute from metrics.
        from nexus import scoring
        return scoring.compute_trip_score(
            distance_km=m["distance_km"],
            idle_minutes=m["idle_minutes"],
            duration_minutes=m["duration_minutes"],
            harsh_brakes=m["harsh_brakes"],
            speeding_events=m["speeding_events"],
        )

    def _driver_for(self, device) -> NexusDriver:
        # One Nexus driver per phone device, keyed on external_ref for stability.
        ref = f"phone:{device}"[:64]
        driver, _ = NexusDriver.objects.get_or_create(
            external_ref=ref, defaults={"full_name": f"Phone — {device}"[:120]})
        return driver
