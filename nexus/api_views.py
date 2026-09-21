"""Nexus API — what the customer app consumes + a trip-ingest endpoint."""
from __future__ import annotations

from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response

from .models import NexusDriver
from .services import driver_summary, record_trip


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def nexus_summary(request):
    """GET /api/v1/nexus/summary/?driver=<id>  (defaults to the first driver)."""
    ref = request.query_params.get("driver")
    driver = None
    if ref:
        driver = NexusDriver.objects.filter(id=ref).first() or \
                 NexusDriver.objects.filter(full_name__icontains=ref).first()
    driver = driver or NexusDriver.objects.first()
    if driver is None:
        return Response({"detail": "No drivers yet. Seed with manage.py seed_nexus."}, status=404)
    return Response(driver_summary(driver))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def nexus_drivers(request):
    rows = [{"id": str(d.id), "name": d.full_name, "points": d.total_points}
            for d in NexusDriver.objects.all()[:200]]
    return Response({"drivers": rows})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def nexus_ingest_trip(request):
    """POST /api/v1/nexus/trips/ — ingest one trip; scores + accrues live."""
    d = request.data
    ref = d.get("driver")
    driver = (NexusDriver.objects.filter(id=ref).first() if ref else None) \
        or NexusDriver.objects.filter(full_name__iexact=str(d.get("driver_name", "")).strip()).first()
    if driver is None and d.get("driver_name"):
        driver = NexusDriver.objects.create(full_name=str(d["driver_name"]).strip())
    if driver is None:
        return Response({"detail": "driver or driver_name required"}, status=400)
    try:
        trip = record_trip(
            driver,
            started_at=d.get("started_at") or timezone.now(),
            distance_km=d.get("distance_km", 0),
            duration_minutes=d.get("duration_minutes", 0),
            idle_minutes=d.get("idle_minutes", 0),
            harsh_brakes=d.get("harsh_brakes", 0),
            speeding_events=d.get("speeding_events", 0),
            label=d.get("label", ""),
        )
    except (ValueError, TypeError) as e:
        return Response({"detail": f"bad trip input: {e}"}, status=400)
    return Response({"trip_id": str(trip.id), "score": trip.score, "points": trip.points,
                     "driver_total": driver.total_points}, status=201)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def nexus_live_vehicles(request):
    """GET /api/v1/nexus/live/ — current WebFleet vehicle position(s).
    Cached 60s to respect the WebFleet rate limit (errorCode 8011)."""
    from django.core.cache import cache
    from . import webfleet
    if not webfleet.is_configured():
        return Response({"configured": False, "vehicles": [], "count": 0})
    data = cache.get("nexus_live_vehicles")
    if data is None:
        try:
            objs = webfleet.objects()
        except Exception as e:  # noqa: BLE001
            return Response({"configured": True, "error": str(e)[:140],
                             "vehicles": cache.get("nexus_live_vehicles_last", []),
                             "count": 0})

        def _mdeg(v):
            try:
                return round(float(v) / 1_000_000.0, 6)
            except (TypeError, ValueError):
                return None
        data = [{
            "name": o.get("objectname"),
            "lat": _mdeg(o.get("latitude_mdeg")),
            "lng": _mdeg(o.get("longitude_mdeg")),
            "speed": o.get("speed"),
            "moving": str(o.get("standstill")) not in ("1", "true", "True"),
            "ignition_on": str(o.get("ignition")) in ("1", "true", "True"),
            "where": o.get("postext") or o.get("postext_short") or "",
            "last_seen": o.get("pos_time") or o.get("msgtime"),
            "odometer_km": o.get("odometer"),
        } for o in objs]
        cache.set("nexus_live_vehicles", data, 60)
        cache.set("nexus_live_vehicles_last", data, 3600)
    vehicles = list(data) + _phone_devices_live()
    return Response({"configured": True, "vehicles": vehicles, "count": len(vehicles)})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def fleet_vehicles(request):
    """GET /api/v1/nexus/fleet/ — Alpha Direct's own company cars (Cartrack).

    Reads the synced nexus.FleetVehicle register (refreshed by
    `manage.py pull_cartrack` on a cron), so the page is fast and works even if
    the Cartrack API is briefly down. `configured` is False until the Cartrack
    credentials are set; any demo rows are flagged so they can't be mistaken for
    real vehicles."""
    from . import cartrack
    from .models import FleetVehicle
    rows = list(FleetVehicle.objects.all())
    vehicles = [{
        "registration": v.registration,
        "make": v.make,
        "model": v.model,
        "description": v.description,
        "lat": v.last_lat,
        "lng": v.last_lng,
        "speed": v.last_speed_kmh,
        "moving": v.moving,
        "ignition_on": v.ignition_on,
        "odometer_km": v.odometer_km,
        "where": v.where,
        "driver": v.driver_name,
        "last_seen": v.last_seen,
        "is_demo": v.is_demo,
        "updated_at": v.updated_at.isoformat() if v.updated_at else None,
    } for v in rows]
    return Response({
        "configured": cartrack.is_configured(),
        "count": len(vehicles),
        "demo": any(v["is_demo"] for v in vehicles),
        "vehicles": vehicles,
    })


def _phone_devices_live():
    """Latest ping per phone device — personal phones (GPSLogger) feeding Nexus."""
    from .models import PhonePing
    out, seen = [], set()
    for p in PhonePing.objects.all()[:300]:
        if p.device in seen:
            continue
        seen.add(p.device)
        when = p.recorded_at or p.received_at
        out.append({
            "name": p.device, "lat": p.lat, "lng": p.lng,
            "speed": round(p.speed_kmh or 0),
            "moving": (p.speed_kmh or 0) > 3,
            "ignition_on": (p.speed_kmh or 0) > 0,
            "where": "", "source": "phone",
            "last_seen": when.strftime("%d/%m/%Y %H:%M") if when else None,
            "odometer_km": None,
        })
    return out


@api_view(["POST"])
@permission_classes([AllowAny])
def nexus_phone_ping(request):
    """POST /api/v1/nexus/phone-ping/?token=<NEXUS_PHONE_TOKEN>
    Receives a live location ping from a phone telematics app (e.g. GPSLogger
    on the CFO's Samsung). Token-gated (the phone can't do SSO). Body (JSON):
      {device, lat, lon|lng, speed (m/s, GPSLogger %SPD), time}.
    """
    from django.conf import settings
    from django.utils.dateparse import parse_datetime
    from .models import PhonePing
    token = getattr(settings, "NEXUS_PHONE_TOKEN", "") or ""
    given = (request.query_params.get("token")
             or request.headers.get("X-Nexus-Token") or "")
    if not token or given != token:
        return Response({"detail": "bad or missing token"}, status=403)
    d = request.data if isinstance(request.data, dict) else {}

    def _f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    lat = _f(d.get("lat"))
    lng = _f(d.get("lng") if d.get("lng") is not None else d.get("lon"))
    spd_ms = _f(d.get("speed"))
    speed_kmh = round(spd_ms * 3.6, 1) if spd_ms is not None else 0  # GPSLogger %SPD is m/s
    rec = parse_datetime(str(d.get("time"))) if d.get("time") else None
    PhonePing.objects.create(
        device=str(d.get("device") or "phone")[:80],
        lat=lat, lng=lng, speed_kmh=speed_kmh, recorded_at=rec, raw=d,
    )
    return Response({"ok": True}, status=201)
