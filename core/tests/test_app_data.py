"""Your data — the staff member's own controls (Play/App Store deletion rule).

Alpha Nexus was rejected under Apple 5.1.1(v) in July 2026 for having no
deletion route. These prove the staff-app equivalent actually works, and that
it does NOT quietly destroy employment records.
"""
import secrets

from django.contrib.auth import get_user_model
from django.core import mail
from django.utils import timezone
from rest_framework.test import APITestCase

from core.device_session_models import StaffDeviceSession

User = get_user_model()
PW = 'Dt-' + secrets.token_urlsafe(12)
SIGN_OUT_ALL = '/api/v1/auth/devices/sign-out-everywhere/'
DATA_REQ = '/api/v1/app/data-request/'


class SignOutEverywhereTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='thabo.m', email='thabo.m@alphadirect.co.bw',
                                             password=PW, first_name='Thabo')
        self.raw, _ = StaffDeviceSession.issue(self.user, device_label='Phone A')
        StaffDeviceSession.issue(self.user, device_label='Phone B')

    def _auth(self):
        return {'HTTP_AUTHORIZATION': f'Bearer {self.raw}'}

    def test_signs_out_every_phone(self):
        r = self.client.post(SIGN_OUT_ALL, {}, format='json', **self._auth())
        self.assertEqual(r.status_code, 200, r.content[:200])
        self.assertEqual(r.json()['signed_out'], 2)
        live = StaffDeviceSession.objects.filter(user=self.user, revoked_at__isnull=True,
                                                 expires_at__gt=timezone.now()).count()
        self.assertEqual(live, 0)

    def test_the_token_is_dead_afterwards(self):
        self.client.post(SIGN_OUT_ALL, {}, format='json', **self._auth())
        r = self.client.get('/api/v1/auth/devices/', **self._auth())
        self.assertEqual(r.status_code, 401)

    def test_anonymous_cannot_sign_anyone_out(self):
        r = self.client.post(SIGN_OUT_ALL, {}, format='json')
        self.assertIn(r.status_code, (401, 403))
        self.assertEqual(StaffDeviceSession.objects.filter(user=self.user,
                                                           revoked_at__isnull=True).count(), 2)

    def test_one_persons_request_never_touches_another(self):
        other = User.objects.create_user(username='lorato.s', email='lorato.s@alphadirect.co.bw',
                                         password=PW)
        StaffDeviceSession.issue(other, device_label='Her phone')
        self.client.post(SIGN_OUT_ALL, {}, format='json', **self._auth())
        self.assertEqual(StaffDeviceSession.objects.filter(user=other,
                                                           revoked_at__isnull=True).count(), 1)


class DataDeletionRequestTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='neo.k', email='neo.k@alphadirect.co.bw',
                                             password=PW, first_name='Neo', last_name='Kgosi')
        self.raw, _ = StaffDeviceSession.issue(self.user, device_label='Phone')
        mail.outbox = []

    def _auth(self):
        return {'HTTP_AUTHORIZATION': f'Bearer {self.raw}'}

    def test_confirm_word_is_required(self):
        r = self.client.post(DATA_REQ, {'confirm': 'yes'}, format='json', **self._auth())
        self.assertEqual(r.status_code, 400)
        self.assertEqual(len(mail.outbox), 0)

    def test_request_reaches_it(self):
        r = self.client.post(DATA_REQ, {'confirm': 'DELETE', 'note': 'Please remove what you can.'},
                             format='json', **self._auth())
        self.assertEqual(r.status_code, 200, r.content[:200])
        self.assertTrue(r.json()['received'])
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('it@alphadirect.co.bw', mail.outbox[0].to)
        self.assertIn('Neo', mail.outbox[0].body)

    def test_it_does_not_delete_the_employment_record(self):
        # The whole point: a request, not a wipe. The person must still exist.
        self.client.post(DATA_REQ, {'confirm': 'DELETE'}, format='json', **self._auth())
        self.assertTrue(User.objects.filter(username='neo.k').exists())

    def test_anonymous_cannot_request(self):
        r = self.client.post(DATA_REQ, {'confirm': 'DELETE'}, format='json')
        self.assertIn(r.status_code, (401, 403))
        self.assertEqual(len(mail.outbox), 0)
