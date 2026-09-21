"""
core/calendar_service.py — Omni writes a calendar invite into the signed-in
staff member's OWN Outlook calendar. Nothing else.

Built to the CFO's build request of 8-Sep-2026. The Microsoft side was already
done and tested by IT (M365) before a line of this existed:

  * a DEDICATED app registration, "Omni Calendar" — deliberately NOT the
    "Omni ERP Mail Sender" app, so calendar access cannot ride in on the
    mail app's consent. That is why this module reads its own
    OMNI_CALENDAR_* settings and never falls back to MICROSOFT_*;
  * an Exchange Application Access Policy scoped to the mail-enabled security
    group omni-calendar-allowed@alphadirect.co.bw, verified Granted for a
    member and Denied for a non-member. Mailboxes are added by changing GROUP
    membership, never the policy.

THE SECURITY QUESTION IT ASKED, AND THE ANSWER IN CODE
  The Access Policy caps WHICH mailboxes the app can reach at all. It does not
  stop the app writing into any mailbox inside the group. So the per-user
  guarantee has to live here:

    create_event() takes the organiser mailbox as a keyword argument and the
    view supplies it from request.user.email — the mailbox of the person whose
    session is making the call. There is no code path that lets a caller name
    a mailbox: core/calendar_views.py rejects a request that even mentions
    one, and this module refuses an organiser that is empty or, when an
    allowlist is configured, one that is not on it.

  So the mailbox written to is always the caller's own. Two staff members
  calling the same endpoint write into two different calendars.

ATTENDEES ARE NOT AN EXCEPTION TO THAT
  The event is created in the organiser's own calendar with attendees on it.
  Exchange then sends each attendee the invitation from the organiser's
  mailbox, and it lands in their calendar because THEY accept it — Omni never
  writes into an attendee's calendar and never needs their mailbox in the
  allowed group.

OMNI KEEPS WORKING WITHOUT THIS
  Unconfigured or unreachable, this module raises CalendarUnavailable and the
  view answers 503 with a plain-English sentence. Nothing imports it at
  start-up, no migration depends on it, and no other feature calls it.
"""

from __future__ import annotations

import logging
import time
from urllib.parse import quote
from typing import Any, Dict, Iterable, List, Optional

import requests
from django.conf import settings

log = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
DEFAULT_TZ = "Africa/Gaborone"

_TOKEN_CACHE: Dict[str, Dict[str, Any]] = {}


class CalendarUnavailable(Exception):
    """Calendar sending is off, misconfigured, or Microsoft did not answer."""


class CalendarMailboxRefused(Exception):
    """The organiser mailbox is empty or outside the allowed set."""


def _cfg(name: str, default: str = "") -> str:
    return (getattr(settings, f"OMNI_CALENDAR_{name}", default) or "").strip()


def is_configured() -> bool:
    """True when the Omni Calendar app registration is fully wired."""
    return bool(_cfg("TENANT_ID") and _cfg("CLIENT_ID") and _cfg("CLIENT_SECRET"))


def allowed_mailboxes() -> List[str]:
    """Optional second fence in front of the Exchange Access Policy.

    Left empty (the default) the Access Policy is the only gate, which is what
    IT configured. Set OMNI_CALENDAR_ALLOWED_MAILBOXES while the group is
    still seeded with one mailbox and a bug cannot reach further than the list.
    """
    raw = getattr(settings, "OMNI_CALENDAR_ALLOWED_MAILBOXES", "") or ""
    if isinstance(raw, str):
        parts: Iterable[str] = raw.split(",")
    else:
        parts = raw
    return [p.strip().lower() for p in parts if str(p).strip()]


def check_mailbox(organiser_upn: str) -> str:
    """Normalise and authorise the organiser mailbox, or refuse."""
    upn = (organiser_upn or "").strip().lower()
    if not upn or "@" not in upn:
        raise CalendarMailboxRefused(
            "Your Omni account has no work email address on it, so there is no "
            "calendar to write the invite into. Ask IT to add it to your profile."
        )
    allowed = allowed_mailboxes()
    if allowed and upn not in allowed:
        raise CalendarMailboxRefused(
            "Your mailbox is not on the approved list for Omni calendar "
            "invites yet. Ask IT to add you to omni-calendar-allowed."
        )
    return upn


def _token() -> str:
    if not is_configured():
        raise CalendarUnavailable(
            "Omni calendar invites are not switched on yet — the Omni Calendar "
            "app details are missing on the server."
        )
    tenant, cid, secret = _cfg("TENANT_ID"), _cfg("CLIENT_ID"), _cfg("CLIENT_SECRET")

    cached = _TOKEN_CACHE.get(cid)
    if cached and cached["expires_at"] > time.time() + 30:
        return cached["token"]

    try:
        resp = requests.post(
            f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
            data={
                "client_id": cid,
                "client_secret": secret,
                "scope": "https://graph.microsoft.com/.default",
                "grant_type": "client_credentials",
            },
            timeout=15,
        )
    except requests.RequestException as exc:
        log.warning("calendar token request failed: %s", exc.__class__.__name__)
        raise CalendarUnavailable(
            "Microsoft did not answer. Please try again."
        ) from exc

    if resp.status_code != 200:
        # Never log the response body — it can echo the client secret back.
        log.warning("calendar token rejected: HTTP %s", resp.status_code)
        raise CalendarUnavailable(
            "Microsoft would not let Omni in to write the invite "
            f"(sign-in returned {resp.status_code}). IT can check the Omni "
            "Calendar app details."
        )
    body = resp.json()
    _TOKEN_CACHE[cid] = {
        "token": body["access_token"],
        "expires_at": time.time() + int(body.get("expires_in", 3600)),
    }
    return body["access_token"]


def _attendee(address: str, required: bool = True) -> Dict[str, Any]:
    return {
        "emailAddress": {"address": address.strip()},
        "type": "required" if required else "optional",
    }


def build_event(
    *,
    subject: str,
    start: str,
    end: str,
    tz: str = DEFAULT_TZ,
    attendees: Optional[List[str]] = None,
    body_html: str = "",
    location: str = "",
    online_meeting: bool = False,
    reminder_minutes: Optional[int] = None,
) -> Dict[str, Any]:
    """The Graph event payload. Pure — no network, so it is unit-testable."""
    event: Dict[str, Any] = {
        "subject": subject,
        "start": {"dateTime": start, "timeZone": tz},
        "end": {"dateTime": end, "timeZone": tz},
        "attendees": [_attendee(a) for a in (attendees or []) if str(a).strip()],
    }
    if body_html:
        event["body"] = {"contentType": "HTML", "content": body_html}
    if location:
        event["location"] = {"displayName": location}
    if online_meeting:
        event["isOnlineMeeting"] = True
        event["onlineMeetingProvider"] = "teamsForBusiness"
    if reminder_minutes is not None:
        event["reminderMinutesBeforeStart"] = int(reminder_minutes)
        event["isReminderOn"] = True
    return event


def create_event(*, organiser_upn: str, **event_kwargs) -> Dict[str, Any]:
    """Create one event in `organiser_upn`'s OWN calendar and return
    {'id', 'web_link', 'organiser'}.

    `organiser_upn` is the only mailbox touched. It comes from the caller's
    session, never from request data — see core/calendar_views.py.
    """
    upn = check_mailbox(organiser_upn)
    payload = build_event(**event_kwargs)
    token = _token()

    try:
        resp = requests.post(
            f"{GRAPH_BASE}/users/{quote(upn, safe=chr(64))}/events",
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
    except requests.RequestException as exc:
        log.warning("calendar create failed: %s", exc.__class__.__name__)
        raise CalendarUnavailable(
            "Microsoft did not answer. Please try again."
        ) from exc

    if resp.status_code not in (200, 201):
        detail = resp.text[:300]
        log.warning(
            "calendar create rejected HTTP %s for one mailbox", resp.status_code
        )
        if resp.status_code in (403, 404):
            raise CalendarUnavailable(
                "Microsoft would not write into your calendar. Your mailbox is "
                "probably not in the omni-calendar-allowed group yet — IT adds "
                "it by changing that group, not the policy."
            )
        raise CalendarUnavailable(
            f"Microsoft refused the invite ({resp.status_code}). {detail}"
        )

    data = resp.json() if resp.content else {}
    log.info(
        "calendar event created for one mailbox (%s attendee(s))",
        len(payload.get("attendees") or []),
    )
    return {
        "id": data.get("id", ""),
        "web_link": data.get("webLink", ""),
        "organiser": upn,
    }
