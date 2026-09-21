"""
core/calendar_views.py — the two endpoints for Omni calendar invites.

  GET  /api/v1/calendar/status/    is this switched on, and for whom
  POST /api/v1/calendar/invites/   put ONE invite in MY OWN calendar

THE ISOLATION GUARANTEE LIVES IN THIS FILE
  The organiser mailbox is taken from request.user.email and from nowhere
  else. A request that so much as names a mailbox is refused with 400 before
  any Microsoft call is made, so a caller cannot write into a colleague's
  calendar by adding a field — not by guessing a field name either, because
  the refusal is a denylist of every spelling plus an allowlist of the fields
  this endpoint accepts.

  Attendees are a different thing from the organiser: they are invited, and
  the invitation reaches their calendar only when they accept it. Omni never
  writes into an attendee's calendar.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from rest_framework import status as http
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core import calendar_service as cal
from core.models import CalendarInviteLog

log = logging.getLogger(__name__)

# Anything that could be read as "whose calendar" — refused outright.
MAILBOX_FIELDS = {
    "mailbox",
    "organiser",
    "organizer",
    "organiser_upn",
    "organizer_upn",
    "upn",
    "userprincipalname",
    "user_principal_name",
    "user",
    "user_id",
    "owner",
    "on_behalf_of",
    "onbehalfof",
    "calendar",
    "calendar_id",
    "calendarid",
    "email",
    "from",
    "from_email",
    "as_user",
    "impersonate",
}

ACCEPTED_FIELDS = {
    "subject",
    "start",
    "end",
    "timezone",
    "tz",
    "attendees",
    "body",
    "body_html",
    "location",
    "online_meeting",
    "onlineMeeting",
    "reminder_minutes",
    "purpose",
}


def _bad(message: str, **extra) -> Response:
    return Response({"detail": message, **extra}, status=http.HTTP_400_BAD_REQUEST)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def calendar_status(request):
    """Honest state, so a screen never offers a button that cannot work."""
    me = (getattr(request.user, "email", "") or "").strip().lower()
    allowed = cal.allowed_mailboxes()
    return Response(
        {
            "configured": cal.is_configured(),
            "my_mailbox": me or None,
            "allowlist_in_use": bool(allowed),
            "i_am_allowed": bool(me and (not allowed or me in allowed)),
            "writes_only_to": me or None,
            "note": (
                "Omni writes the invite into your own Outlook calendar only. "
                "People you invite receive an invitation and it appears in "
                "their calendar when they accept it."
            ),
        }
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_invite(request):
    data: Dict[str, Any] = request.data if isinstance(request.data, dict) else {}

    # 1. Refuse any attempt to choose a calendar. Checked first, so a request
    #    that tries it never reaches Microsoft.
    named = sorted(
        k for k in data if str(k).strip().lower().replace("-", "_") in MAILBOX_FIELDS
    )
    if named:
        return _bad(
            "Omni only ever writes into your own calendar, so this request "
            "cannot name a mailbox. Remove " + ", ".join(named) + " and send it again.",
            refused_fields=named,
        )
    unknown = sorted(k for k in data if k not in ACCEPTED_FIELDS)
    if unknown:
        return _bad(
            "Unexpected field(s): " + ", ".join(unknown) + ".",
            accepted_fields=sorted(ACCEPTED_FIELDS),
        )

    subject = str(data.get("subject") or "").strip()
    start = str(data.get("start") or "").strip()
    end = str(data.get("end") or "").strip()
    missing = [
        n for n, v in (("subject", subject), ("start", start), ("end", end)) if not v
    ]
    if missing:
        return _bad("Please give a " + ", ".join(missing) + ".")

    raw_attendees = data.get("attendees") or []
    if isinstance(raw_attendees, str):
        raw_attendees = [a for a in raw_attendees.split(",")]
    attendees: List[str] = [str(a).strip() for a in raw_attendees if str(a).strip()]
    bad_addrs = [a for a in attendees if "@" not in a]
    if bad_addrs:
        return _bad("These are not email addresses: " + ", ".join(bad_addrs) + ".")

    # 2. The organiser mailbox — from the session, never from the request.
    organiser = (getattr(request.user, "email", "") or "").strip().lower()

    kwargs = dict(
        subject=subject,
        start=start,
        end=end,
        tz=str(data.get("timezone") or data.get("tz") or cal.DEFAULT_TZ),
        attendees=attendees,
        body_html=str(data.get("body_html") or data.get("body") or ""),
        location=str(data.get("location") or ""),
        online_meeting=bool(data.get("online_meeting") or data.get("onlineMeeting")),
    )
    if data.get("reminder_minutes") is not None:
        try:
            kwargs["reminder_minutes"] = int(data["reminder_minutes"])
        except (TypeError, ValueError):
            return _bad("reminder_minutes must be a whole number of minutes.")

    def _log(status: str, event_id: str = "", error: str = "") -> None:
        try:
            CalendarInviteLog.objects.create(
                organiser_upn=organiser,
                requested_by=request.user,
                subject=subject[:255],
                purpose=str(data.get("purpose") or "")[:64],
                starts_at=start[:64],
                attendee_count=len(attendees),
                attendees=", ".join(attendees)[:4000],
                graph_event_id=event_id[:255],
                status=status,
                error=error[:2000],
            )
        except Exception:  # noqa: BLE001 - oversight never blocks
            log.warning("calendar invite log write failed", exc_info=False)

    try:
        result = cal.create_event(organiser_upn=organiser, **kwargs)
    except cal.CalendarMailboxRefused as exc:
        _log(CalendarInviteLog.Status.REFUSED, error=str(exc))
        return _bad(str(exc))
    except cal.CalendarUnavailable as exc:
        _log(CalendarInviteLog.Status.FAILED, error=str(exc))
        code = (
            http.HTTP_503_SERVICE_UNAVAILABLE
            if not cal.is_configured()
            else http.HTTP_502_BAD_GATEWAY
        )
        return Response({"detail": str(exc)}, status=code)

    _log(CalendarInviteLog.Status.CREATED, event_id=result.get("id", ""))
    return Response(
        {
            "created": True,
            "event_id": result.get("id", ""),
            "web_link": result.get("web_link", ""),
            "written_to": result.get("organiser"),
            "invited": attendees,
        },
        status=http.HTTP_201_CREATED,
    )
