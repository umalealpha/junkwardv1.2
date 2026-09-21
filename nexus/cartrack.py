"""
nexus/cartrack.py — Cartrack Fleet REST API client (company-vehicle tracking).

Read-only feed of Alpha Direct's OWN company cars (the vehicles fitted with
Cartrack units), so Omni can show the fleet + live position. Credentials come
from the environment (settings.CARTRACK_*), NEVER hard-coded or in chat.

API (developer.cartrack.com/docs/fleet-api):
  Base : https://fleetapi-<region>.cartrack.com/rest/   (region = ISO alpha-2,
         e.g. 'bw' Botswana, 'za' South Africa — set CARTRACK_REGION)
  Auth : HTTP Basic (username:password / API token), HTTPS only.
  GET /vehicles          → the fleet (registration, id, make, model, …)
  GET /vehicles/status   → latest status per vehicle (location, speed,
                           ignition, odometer, driver, timestamp). Max 60/min.

Cartrack does not publish the exact status field names, so normalise_status()
maps defensively across the common variants and keeps the raw row, so a real
account can be reconciled without a code change.
"""
from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

logger = logging.getLogger("nexus.cartrack")


class CartrackError(Exception):
    def __init__(self, code, msg):
        self.code = code
        self.msg = msg
        super().__init__(f"Cartrack error {code}: {msg}")


class CartrackNotConfigured(Exception):
    pass


def _cfg():
    user = getattr(settings, "CARTRACK_USERNAME", "") or ""
    pw = getattr(settings, "CARTRACK_PASSWORD", "") or ""
    region = (getattr(settings, "CARTRACK_REGION", "") or "bw").strip().lower()
    if not (user and pw):
        raise CartrackNotConfigured("CARTRACK_USERNAME / CARTRACK_PASSWORD not set.")
    return user, pw, region


def is_configured() -> bool:
    try:
        _cfg()
        return True
    except CartrackNotConfigured:
        return False


def _base(region: str) -> str:
    return f"https://fleetapi-{region}.cartrack.com/rest"


def _call(path: str, params: dict | None = None, timeout: int = 30):
    user, pw, region = _cfg()
    url = f"{_base(region)}/{path.lstrip('/')}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    auth = "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()
    req = urllib.request.Request(
        url, headers={"Authorization": auth, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200] if e.fp else ""
        raise CartrackError(e.code, detail or e.reason)
    except urllib.error.URLError as e:
        raise CartrackError("network", str(e.reason))
    try:
        data = json.loads(body)
    except ValueError:
        raise CartrackError("parse", body[:200])
    # Cartrack wraps list payloads as {"data": [...]} on most endpoints.
    if isinstance(data, dict):
        if "data" in data and isinstance(data["data"], list):
            return data["data"]
        if "vehicles" in data and isinstance(data["vehicles"], list):
            return data["vehicles"]
        return [data]
    return data if isinstance(data, list) else []


def vehicles() -> list[dict]:
    """The fleet — one row per company vehicle."""
    return _call("vehicles")


def vehicle_status() -> list[dict]:
    """Latest status per vehicle: location, speed, ignition, odometer, driver."""
    return _call("vehicles/status")


def _first(row: dict, *keys):
    for k in keys:
        if k in row and row[k] not in (None, ""):
            return row[k]
    return None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def normalise_status(row: dict) -> dict:
    """Map a Cartrack status row to Omni's fleet shape, defensively (field names
    vary by account/version). Raw row kept by the caller for reconciliation."""
    lat = _num(_first(row, "latitude", "lat", "gps_latitude", "position_latitude"))
    lng = _num(_first(row, "longitude", "lng", "lon", "gps_longitude", "position_longitude"))
    ign = _first(row, "ignition", "ignition_on", "engine_on", "engine")
    ign_on = str(ign).strip().lower() in ("1", "true", "on", "yes") if ign is not None else None
    speed = _num(_first(row, "speed", "speed_kmh", "gps_speed", "current_speed"))
    moving = (speed or 0) > 3 if speed is not None else None
    return {
        "registration": _first(row, "registration", "reg", "vehicle_registration",
                                "plate", "number_plate", "licence_plate"),
        "cartrack_id": str(_first(row, "vehicle_id", "id", "vehicleId", "terminal_id",
                                  "unit_id") or "").strip(),
        "lat": lat,
        "lng": lng,
        "speed": speed,
        "moving": moving,
        "ignition_on": ign_on,
        "odometer_km": _num(_first(row, "odometer", "odometer_km", "mileage", "distance_odometer")),
        "driver_name": _first(row, "driver_name", "driver", "current_driver"),
        "where": _first(row, "address", "location", "location_text", "geocode", "position_description") or "",
        "last_seen": _first(row, "event_ts", "position_time", "timestamp", "gps_datetime",
                            "event_datetime", "last_update", "datetime"),
    }
