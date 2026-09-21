"""CEO Monitor escalate endpoint — GET is safe (no side effect), POST acts.

Locks in the mail-scanner-safety fix: a Microsoft 365 SafeLinks GET pre-fetch of
an escalate link must NOT create a task; only a human POST (the Confirm button)
does. Also covers bad/expired tokens and idempotency.
"""
from django.contrib.auth import get_user_model
from django.core import signing
from django.test import TestCase, Client, override_settings

from core.models import OmniTask
from core.ceo_monitor_views import make_escalation_token, _SALT

User = get_user_model()
_HOST = "omni.alphadirect.co.bw"
_URL = "/api/ceo-monitor/escalate/"


@override_settings(ALLOWED_HOSTS=[_HOST])
class CeoMonitorEscalateTests(TestCase):
    def setUp(self):
        self.client = Client(HTTP_HOST=_HOST)
        # The CEO (recorded as assigner) and a target exec (assignee).
        self.ceo = User.objects.create(username="aiyer", email="aiyer@alphadirect.co.bw",
                                       is_active=True)
        self.target = User.objects.create(username="wmoses", email="wmoses@alphadirect.co.bw",
                                          is_active=True)

    def _token(self, to="wmoses"):
        return make_escalation_token(to=to, party="Acme Ltd", ref="CLM-1",
                                     matter="Test matter", why="Test why")

    def test_get_creates_nothing(self):
        """A GET (link open / mail-scanner pre-fetch) must NOT create a task."""
        r = self.client.get(_URL, {"t": self._token()})
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Confirm", r.content)
        self.assertEqual(OmniTask.objects.filter(source__startswith="ceomon:").count(), 0)

    def test_post_creates_once(self):
        """The Confirm POST creates exactly one task for the target exec."""
        r = self.client.post(_URL, {"t": self._token()})
        self.assertEqual(r.status_code, 200)
        tasks = OmniTask.objects.filter(source__startswith="ceomon:")
        self.assertEqual(tasks.count(), 1)
        t = tasks.first()
        self.assertEqual(t.assignee_id, self.target.id)
        self.assertEqual(t.assigner_id, self.ceo.id)

    def test_post_twice_is_idempotent(self):
        self.client.post(_URL, {"t": self._token()})
        self.client.post(_URL, {"t": self._token()})
        self.assertEqual(OmniTask.objects.filter(source__startswith="ceomon:").count(), 1)

    def test_bad_token_400_no_task(self):
        r = self.client.get(_URL, {"t": "not-a-real-token"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(OmniTask.objects.count(), 0)

    def test_expired_token_400(self):
        tok = self._token()
        with override_settings():
            # max_age=0 forces the signer to treat any age as expired.
            from core import ceo_monitor_views as v
            old = v._MAX_AGE
            v._MAX_AGE = -1
            try:
                r = self.client.post(_URL, {"t": tok})
            finally:
                v._MAX_AGE = old
        self.assertEqual(r.status_code, 400)
        self.assertEqual(OmniTask.objects.count(), 0)

    def test_unknown_recipient_400(self):
        r = self.client.post(_URL, {"t": self._token(to="nobody")})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(OmniTask.objects.count(), 0)
