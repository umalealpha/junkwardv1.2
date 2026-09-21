"""Tests for the Nexus staff bridge (CFO 2026-07-14): a customer-app token
maps to the matching ACTIVE staff User — and only then."""
from datetime import timedelta

from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from rewards import customer_auth
from rewards.models import RewardMember


def _member(email: str) -> RewardMember:
    return RewardMember.objects.create(customer_name='T member', email=email, is_active=True)


class StaffBridgeTests(APITestCase):
    URL = '/api/v1/my-approvals/'

    def _get(self, token: str):
        return self.client.get(self.URL, HTTP_AUTHORIZATION=f'Bearer {token}')

    def test_staff_email_token_reaches_omni_api(self):
        User.objects.create_user('kago', 'ktshutlhedi@alphadirect.co.bw', 'x')
        m = _member('ktshutlhedi@alphadirect.co.bw')
        token = customer_auth.start_session(m)
        r = self._get(token)
        self.assertEqual(r.status_code, 200)
        self.assertIn('streams', r.json())

    def _assert_denied(self, resp):
        # DRF signals "not signed in" as 401 or 403 depending on which
        # authenticator speaks last — either way the door is closed.
        self.assertIn(resp.status_code, (401, 403))

    def test_plain_customer_token_gets_401(self):
        m = _member('someone@gmail.com')      # no matching staff User
        token = customer_auth.start_session(m)
        self._assert_denied(self._get(token))

    def test_disabled_staff_gets_401(self):
        User.objects.create_user('gone', 'gone@alphadirect.co.bw', 'x', is_active=False)
        m = _member('gone@alphadirect.co.bw')
        token = customer_auth.start_session(m)
        self._assert_denied(self._get(token))

    def test_revoked_session_gets_401(self):
        User.objects.create_user('kago2', 'k2@alphadirect.co.bw', 'x')
        m = _member('k2@alphadirect.co.bw')
        token = customer_auth.start_session(m)
        m.sessions.update(revoked=True)
        self._assert_denied(self._get(token))

    def test_expired_session_gets_401(self):
        User.objects.create_user('kago3', 'k3@alphadirect.co.bw', 'x')
        m = _member('k3@alphadirect.co.bw')
        token = customer_auth.start_session(m)
        m.sessions.update(expires_at=timezone.now() - timedelta(minutes=1))
        self._assert_denied(self._get(token))

    def test_garbage_token_still_401_not_500(self):
        self._assert_denied(self._get('not-a-real-token'))
