"""
Tests for Omni calendar invites (core/calendar_service.py, core/calendar_views.py).

The point of these is the security question IT asked before the mailbox group
is allowed to grow past the CFO's own: can Omni write into
somebody else's calendar? Every test below is an attempt to make it do that.

No real Microsoft call is made — requests.post is replaced, and the tests
assert on the URL it was handed, because the URL is where the mailbox is.
"""

from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from core import calendar_service as cal
from core.models import CalendarInviteLog

INVITES = "/api/v1/calendar/invites/"
STATUS = "/api/v1/calendar/status/"

CONFIGURED = dict(
    OMNI_CALENDAR_TENANT_ID="t-1",
    OMNI_CALENDAR_CLIENT_ID="c-1",
    OMNI_CALENDAR_CLIENT_SECRET="s-1",
    OMNI_CALENDAR_ALLOWED_MAILBOXES="",
)

GOOD = {
    "subject": "Health dashboard walkthrough",
    "start": "2026-09-09T09:00:00",
    "end": "2026-09-09T09:30:00",
    "attendees": ["medu@example.test"],
    "purpose": "health-dashboard",
}


class _Resp:
    def __init__(self, status_code=201, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text or ""
        self.content = b"{}" if payload is None else b'{"x":1}'

    def json(self):
        return self._payload


def _token_ok(*a, **k):
    return _Resp(200, {"access_token": "tok", "expires_in": 3600})


class _Graph:
    """Stand-in for requests.post: first call is the token, rest are Graph."""

    def __init__(self, event_resp=None):
        self.calls = []
        self.event_resp = event_resp or _Resp(
            201, {"id": "evt-1", "webLink": "https://outlook/evt-1"}
        )

    def __call__(self, url, *args, **kwargs):
        self.calls.append({"url": url, "json": kwargs.get("json")})
        if "login.microsoftonline.com" in url:
            return _token_ok()
        return self.event_resp

    @property
    def event_urls(self):
        return [c["url"] for c in self.calls if "graph.microsoft.com" in c["url"]]


@override_settings(**CONFIGURED)
class CalendarIsolationTests(TestCase):
    """Can a caller reach a mailbox that is not their own? No."""

    def setUp(self):
        cal._TOKEN_CACHE.clear()
        self.medu = User.objects.create_user("medu", "medu@example.test", "x")
        self.cfo = User.objects.create_user(
            "approver", "cfo@example.test", "x"
        )
        self.client = APIClient()

    def _post(self, user, body):
        self.client.force_authenticate(user)
        return self.client.post(INVITES, body, format="json")

    def test_the_invite_is_written_into_the_callers_own_mailbox(self):
        graph = _Graph()
        with patch("core.calendar_service.requests.post", graph):
            r = self._post(self.medu, GOOD)
        self.assertEqual(r.status_code, 201, r.content[:300])
        self.assertEqual(r.json()["written_to"], "medu@example.test")
        self.assertEqual(
            graph.event_urls,
            ["https://graph.microsoft.com/v1.0/users/medu@example.test/events"],
        )

    def test_a_different_signed_in_user_writes_to_a_different_calendar(self):
        graph = _Graph()
        with patch("core.calendar_service.requests.post", graph):
            self._post(self.medu, GOOD)
            self._post(self.cfo, GOOD)
        self.assertEqual(
            graph.event_urls,
            [
                "https://graph.microsoft.com/v1.0/users/medu@example.test/events",
                "https://graph.microsoft.com/v1.0/users/cfo@example.test/events",
            ],
        )

    def test_naming_someone_elses_mailbox_is_refused_before_microsoft_is_called(self):
        for field in (
            "mailbox",
            "organiser",
            "organizer",
            "upn",
            "userPrincipalName",
            "user",
            "owner",
            "on_behalf_of",
            "calendar_id",
            "email",
            "from_email",
            "impersonate",
            "as_user",
        ):
            graph = _Graph()
            body = dict(GOOD)
            body[field] = "cfo@example.test"
            with patch("core.calendar_service.requests.post", graph):
                r = self._post(self.medu, body)
            self.assertEqual(r.status_code, 400, f"{field} was not refused")
            self.assertIn(field, r.json().get("refused_fields", []), field)
            self.assertEqual(graph.calls, [], f"{field} still reached Microsoft")

    def test_an_unknown_field_is_refused_rather_than_quietly_ignored(self):
        graph = _Graph()
        with patch("core.calendar_service.requests.post", graph):
            r = self._post(self.medu, {**GOOD, "writeInto": "someone@else.com"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(graph.calls, [])

    def test_the_service_itself_refuses_an_empty_organiser(self):
        with self.assertRaises(cal.CalendarMailboxRefused):
            cal.create_event(organiser_upn="", subject="s", start="a", end="b")

    def test_a_user_with_no_work_email_gets_a_plain_english_400(self):
        nobody = User.objects.create_user("nobody", "", "x")
        graph = _Graph()
        with patch("core.calendar_service.requests.post", graph):
            r = self._post(nobody, GOOD)
        self.assertEqual(r.status_code, 400)
        self.assertIn("no work email", r.json()["detail"])
        self.assertEqual(graph.calls, [])

    @override_settings(
        **{
            **CONFIGURED,
            "OMNI_CALENDAR_ALLOWED_MAILBOXES": "cfo@example.test",
        }
    )
    def test_the_allowlist_blocks_a_mailbox_outside_the_group(self):
        graph = _Graph()
        with patch("core.calendar_service.requests.post", graph):
            refused = self._post(self.medu, GOOD)
            allowed = self._post(self.cfo, GOOD)
        self.assertEqual(refused.status_code, 400)
        self.assertIn("omni-calendar-allowed", refused.json()["detail"])
        self.assertEqual(allowed.status_code, 201)
        self.assertEqual(
            graph.event_urls,
            [
                "https://graph.microsoft.com/v1.0/users/cfo@example.test/events"
            ],
        )

    def test_sign_in_is_required(self):
        anon = APIClient()
        self.assertIn(anon.post(INVITES, GOOD, format="json").status_code, (401, 403))
        self.assertIn(anon.get(STATUS).status_code, (401, 403))


@override_settings(**CONFIGURED)
class CalendarPayloadTests(TestCase):
    def setUp(self):
        cal._TOKEN_CACHE.clear()
        self.medu = User.objects.create_user("medu", "medu@example.test", "x")
        self.client = APIClient()
        self.client.force_authenticate(self.medu)

    def test_attendees_ride_on_the_organisers_event_not_a_second_write(self):
        graph = _Graph()
        with patch("core.calendar_service.requests.post", graph):
            r = self.client.post(
                INVITES,
                {
                    **GOOD,
                    "attendees": [
                        "medu@example.test",
                        "colleague@example.test",
                    ],
                },
                format="json",
            )
        self.assertEqual(r.status_code, 201)
        # ONE calendar written to, two people invited.
        self.assertEqual(len(graph.event_urls), 1)
        payload = [c["json"] for c in graph.calls if "graph.microsoft.com" in c["url"]][
            0
        ]
        self.assertEqual(
            [a["emailAddress"]["address"] for a in payload["attendees"]],
            ["medu@example.test", "colleague@example.test"],
        )

    def test_gaborone_is_the_default_timezone(self):
        graph = _Graph()
        with patch("core.calendar_service.requests.post", graph):
            self.client.post(INVITES, GOOD, format="json")
        payload = [c["json"] for c in graph.calls if "graph.microsoft.com" in c["url"]][
            0
        ]
        self.assertEqual(payload["start"]["timeZone"], "Africa/Gaborone")
        self.assertEqual(payload["end"]["timeZone"], "Africa/Gaborone")

    def test_teams_link_only_when_asked_for(self):
        self.assertNotIn(
            "isOnlineMeeting", cal.build_event(subject="s", start="a", end="b")
        )
        ev = cal.build_event(subject="s", start="a", end="b", online_meeting=True)
        self.assertTrue(ev["isOnlineMeeting"])
        self.assertEqual(ev["onlineMeetingProvider"], "teamsForBusiness")

    def test_missing_subject_or_times_is_refused(self):
        for drop in ("subject", "start", "end"):
            body = {k: v for k, v in GOOD.items() if k != drop}
            r = self.client.post(INVITES, body, format="json")
            self.assertEqual(r.status_code, 400, drop)
            self.assertIn(drop, r.json()["detail"])

    def test_a_non_address_attendee_is_refused(self):
        r = self.client.post(
            INVITES, {**GOOD, "attendees": ["not-an-address"]}, format="json"
        )
        self.assertEqual(r.status_code, 400)


class CalendarDegradationTests(TestCase):
    """Omni must keep working when this is off or Microsoft is down."""

    def setUp(self):
        cal._TOKEN_CACHE.clear()
        self.medu = User.objects.create_user("medu", "medu@example.test", "x")
        self.client = APIClient()
        self.client.force_authenticate(self.medu)

    @override_settings(
        OMNI_CALENDAR_TENANT_ID="",
        OMNI_CALENDAR_CLIENT_ID="",
        OMNI_CALENDAR_CLIENT_SECRET="",
    )
    def test_not_configured_answers_503_in_plain_english(self):
        r = self.client.post(INVITES, GOOD, format="json")
        self.assertEqual(r.status_code, 503)
        self.assertIn("not switched on yet", r.json()["detail"])
        self.assertFalse(cal.is_configured())

    @override_settings(
        OMNI_CALENDAR_TENANT_ID="",
        OMNI_CALENDAR_CLIENT_ID="",
        OMNI_CALENDAR_CLIENT_SECRET="",
    )
    def test_status_says_it_is_off_rather_than_pretending(self):
        r = self.client.get(STATUS)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["configured"])
        self.assertEqual(r.json()["writes_only_to"], "medu@example.test")

    @override_settings(**CONFIGURED)
    def test_a_mailbox_not_in_the_group_gets_the_group_instruction(self):
        graph = _Graph(event_resp=_Resp(403, text="ErrorAccessDenied"))
        with patch("core.calendar_service.requests.post", graph):
            r = self.client.post(INVITES, GOOD, format="json")
        self.assertEqual(r.status_code, 502)
        self.assertIn("omni-calendar-allowed", r.json()["detail"])

    @override_settings(**CONFIGURED)
    def test_a_microsoft_outage_answers_502_not_a_500(self):
        import requests as rq

        def boom(*a, **k):
            raise rq.ConnectionError("down")

        with patch("core.calendar_service.requests.post", boom):
            r = self.client.post(INVITES, GOOD, format="json")
        self.assertEqual(r.status_code, 502)
        self.assertIn("did not answer", r.json()["detail"])


@override_settings(**CONFIGURED)
class CalendarLogTests(TestCase):
    def setUp(self):
        cal._TOKEN_CACHE.clear()
        self.medu = User.objects.create_user("medu", "medu@example.test", "x")
        self.client = APIClient()
        self.client.force_authenticate(self.medu)

    def test_a_success_is_logged_against_the_mailbox_written_to(self):
        with patch("core.calendar_service.requests.post", _Graph()):
            self.client.post(INVITES, GOOD, format="json")
        row = CalendarInviteLog.objects.get()
        self.assertEqual(row.organiser_upn, "medu@example.test")
        self.assertEqual(row.status, CalendarInviteLog.Status.CREATED)
        self.assertEqual(row.graph_event_id, "evt-1")
        self.assertEqual(row.attendee_count, 1)
        self.assertEqual(row.purpose, "health-dashboard")
        self.assertEqual(row.requested_by, self.medu)

    def test_a_failure_is_logged_too(self):
        with patch(
            "core.calendar_service.requests.post",
            _Graph(event_resp=_Resp(500, text="boom")),
        ):
            self.client.post(INVITES, GOOD, format="json")
        row = CalendarInviteLog.objects.get()
        self.assertEqual(row.status, CalendarInviteLog.Status.FAILED)
        self.assertTrue(row.error)

    def test_a_refused_mailbox_is_logged_as_refused(self):
        nobody = User.objects.create_user("nobody2", "", "x")
        self.client.force_authenticate(nobody)
        with patch("core.calendar_service.requests.post", _Graph()):
            self.client.post(INVITES, GOOD, format="json")
        self.assertEqual(
            CalendarInviteLog.objects.get().status, CalendarInviteLog.Status.REFUSED
        )

    def test_the_log_never_stores_the_body(self):
        with patch("core.calendar_service.requests.post", _Graph()):
            self.client.post(
                INVITES, {**GOOD, "body": "Confidential agenda text"}, format="json"
            )
        row = CalendarInviteLog.objects.get()
        for field in row._meta.get_fields():
            value = getattr(row, field.name, None) if hasattr(row, field.name) else None
            if isinstance(value, str):
                self.assertNotIn("Confidential agenda", value)
