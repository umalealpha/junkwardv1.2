"""
nexus/webfleet.py — WEBFLEET.connect API client (TomTom Webfleet).

Live telematics feed for Project Nexus (Charmaine 2026-06-25 sent the account +
apikey + login for account 'digital-veg'). Read-only. Credentials come from the
environment (settings.WEBFLEET_*), never hard-coded.

Endpoint: GET https://csv.webfleet.com/extern
Auth: HTTP Basic (username:password) for report actions; account + apikey are
      URL params. (Some actions reject URL credentials → errorCode 1180.)
Date ranges: showTripReportExtern uses range_pattern enums: d-1 (yesterday),
      w0/w-1 (this/last week), m0/m-1 (this/last month). NOT free dates.
Rate limit: the account is quota-limited (errorCode 8011) — call sparingly
      (one trip sweep per run), so a cron should run a few times a day at most.
"""
from __future__ import annotations

import base64
import json
import logging
import urllib.parse
import urllib.request

from django.conf import settings

logger = logging.getLogger("nexus.webfleet")
BASE = "https://csv.webfleet.com/extern"


class WebfleetError(Exception):
    def __init__(self, code, msg):
        self.code = code
        self.msg = msg
        super().__init__(f"WebFleet error {code}: {msg}")


class WebfleetNotConfigured(Exception):
    pass


def _cfg():
    acct = getattr(settings, "WEBFLEET_ACCOUNT", "") or ""
    user = getattr(settings, "WEBFLEET_USERNAME", "") or ""
    pw = getattr(settings, "WEBFLEET_PASSWORD", "") or ""
    key = getattr(settings, "WEBFLEET_APIKEY", "") or ""
    if not (acct and user and pw and key):
        raise WebfleetNotConfigured(
            "WEBFLEET_ACCOUNT / _USERNAME / _PASSWORD / _APIKEY not set."
        )
    return acct, user, pw, key


def is_configured() -> bool:
    try:
        _cfg()
        return True
    except WebfleetNotConfigured:
        return False


def _call(action: str, extra: dict | None = None, timeout: int = 30):
    acct, user, pw, key = _cfg()
    params = {"lang": "en", "account": acct, "apikey": key,
              "action": action, "outputformat": "json"}
    if extra:
        params.update(extra)
    url = f"{BASE}?{urllib.parse.urlencode(params)}"
    auth = "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()
    req = urllib.request.Request(url, headers={"Authorization": auth,
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8", "replace")
    data = json.loads(body)
    # WebFleet returns a dict {errorCode, errorMsg} on failure, a list on success.
    if isinstance(data, dict) and "errorCode" in data:
        raise WebfleetError(data.get("errorCode"), data.get("errorMsg", ""))
    return data if isinstance(data, list) else []


def drivers() -> list[dict]:
    """Fleet drivers (driverno, driveruid, name1, email, telmobile, …)."""
    return _call("showDriverReportExtern")


def objects() -> list[dict]:
    """Vehicles + last position (objectno, latitude, longitude, odometer, …)."""
    return _call("showObjectReportExtern")


def trips(range_pattern: str = "m0") -> list[dict]:
    """Trips for a named range. Fields: tripid, objectno/uid, driverno/uid,
    drivername, start_time, end_time, distance, duration, idle_time, avg_speed,
    max_speed, speeding_indicator, optidrive_indicator, …
    range_pattern: d-1 yesterday | w0 this week | m0 this month | m-1 last month."""
    return _call("showTripReportExtern", {"range_pattern": range_pattern})
