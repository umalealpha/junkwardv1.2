"""Omni staff phone app — 30-day per-device sessions (CFO 2026-09-03).

Desktop keeps the 15h DRF token (core/token_auth.py). The phone app gets its
own hashed, revocable, 30-day session so staff stay signed in. Every test here
must go RED when its fix is reverted.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from core.device_session_models import StaffDeviceSession

PROBE_URL = '/api/v1/notifications/pending/'   # any authenticated GET


def _user(name='phone.person'):
    return get_user_model().objects.create_user(
        username=name, email=f'{name}@example.invalid', password='x')


class StaffDeviceSessionModelTests(APITestCase):
    def test_issue_returns_raw_token_and_stores_only_hash(self):
        u = _user()
        raw, sess = StaffDeviceSession.issue(u, device_label='Test phone', user_agent='UA')
        self.assertEqual(len(raw), 64)
        self.assertNotEqual(sess.token_hash, raw)
        self.assertEqual(sess.token_hash, StaffDeviceSession.hash_token(raw))
        self.assertAlmostEqual(
            (sess.expires_at - sess.created_at).total_seconds(),
            30 * 24 * 3600, delta=5)
        self.assertIsNone(sess.revoked_at)


class StaffDeviceAuthTests(APITestCase):
    def setUp(self):
        self.user = _user('auth.person')
        self.raw, self.sess = StaffDeviceSession.issue(self.user, device_label='iPhone')

    def _get(self, raw):
        return self.client.get(PROBE_URL, HTTP_AUTHORIZATION=f'Bearer {raw}')

    def test_live_device_token_authenticates(self):
        r = self._get(self.raw)
        self.assertEqual(r.status_code, 200, r.content[:200])

    def test_expired_device_token_is_401(self):
        StaffDeviceSession.objects.filter(pk=self.sess.pk).update(
            expires_at=timezone.now() - timedelta(minutes=1))
        r = self._get(self.raw)
        self.assertEqual(r.status_code, 401, r.content[:200])
        self.assertIn('sign in again', r.json().get('detail', ''))

    def test_revoked_device_token_is_401(self):
        self.sess.revoke()
        r = self._get(self.raw)
        self.assertEqual(r.status_code, 401, r.content[:200])

    def test_inactive_user_is_401(self):
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        r = self._get(self.raw)
        self.assertEqual(r.status_code, 401, r.content[:200])

    def test_unknown_bearer_falls_through_to_other_authenticators(self):
        # Not ours, not a customer token, not a JWT → chain ends unauthenticated.
        r = self._get('f' * 64)
        self.assertEqual(r.status_code, 401, r.content[:200])

    def test_jwt_shaped_bearer_is_skipped_without_db_lookup(self):
        from core.device_auth import StaffDeviceAuthentication
        from django.test import RequestFactory
        req = RequestFactory().get(PROBE_URL, HTTP_AUTHORIZATION='Bearer a.b.c')
        self.assertIsNone(StaffDeviceAuthentication().authenticate(req))

    def test_last_seen_is_stamped(self):
        self._get(self.raw)
        self.sess.refresh_from_db()
        self.assertIsNotNone(self.sess.last_seen_at)

    def test_class_is_listed_before_azure_jwt(self):
        from django.conf import settings
        classes = settings.REST_FRAMEWORK['DEFAULT_AUTHENTICATION_CLASSES']
        self.assertIn('core.device_auth.StaffDeviceAuthentication', classes)
        self.assertLess(classes.index('core.device_auth.StaffDeviceAuthentication'),
                        classes.index('core.azure_auth.AzureJWTAuthentication'))


from unittest import mock
from rest_framework.authtoken.models import Token


class MintOnLoginTests(APITestCase):
    """The existing login endpoints mint a device session when the client
    says device=phone; desktop calls (no device field) are untouched."""

    def _verified_user(self):
        u = _user('login.person')
        u.set_password('NotTheDefault!23')
        u.save()
        return u

    def _patch_code_ok(self, user):
        # Bypass the emailed-code dance: pretend the code record matched.
        from core import staff_login_views as v
        rec = mock.Mock(expires_at=timezone.now() + timedelta(minutes=5),
                        attempts=0, code_hash=v._hash('123456'), consumed=False)
        rec.save = mock.Mock()
        qs = mock.Mock()
        qs.order_by.return_value.first.return_value = rec
        p1 = mock.patch.object(v.EmailLoginCode.objects, 'filter', return_value=qs)
        p2 = mock.patch.object(v, '_get_user', return_value=user)
        return p1, p2

    def test_verify_with_device_phone_mints_device_session_not_drf_token(self):
        user = self._verified_user()
        p1, p2 = self._patch_code_ok(user)
        with p1, p2:
            r = self.client.post('/api/v1/auth/staff/verify/',
                                 {'email': user.email, 'code': '123456',
                                  'device': 'phone', 'device_label': 'Test iPhone'},
                                 format='json', HTTP_USER_AGENT='TestUA/1.0')
        self.assertEqual(r.status_code, 200, r.content[:300])
        body = r.json()
        self.assertEqual(body.get('token_type'), 'device')
        self.assertEqual(len(body['token']), 64)
        self.assertEqual(StaffDeviceSession.objects.filter(user=user).count(), 1)
        self.assertFalse(Token.objects.filter(user=user).exists())
        sess = StaffDeviceSession.objects.get(user=user)
        self.assertEqual(sess.device_label, 'Test iPhone')
        self.assertEqual(sess.user_agent, 'TestUA/1.0')

    def test_verify_without_device_keeps_desktop_drf_token(self):
        user = self._verified_user()
        p1, p2 = self._patch_code_ok(user)
        with p1, p2:
            r = self.client.post('/api/v1/auth/staff/verify/',
                                 {'email': user.email, 'code': '123456'}, format='json')
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertTrue(Token.objects.filter(user=user).exists())
        self.assertEqual(StaffDeviceSession.objects.filter(user=user).count(), 0)
        self.assertNotIn('token_type', r.json())

    def test_wants_device_session_only_for_literal_phone(self):
        from core.device_session_service import wants_device_session
        self.assertTrue(wants_device_session({'device': 'phone'}))
        self.assertFalse(wants_device_session({'device': 'PHONE '}))
        self.assertFalse(wants_device_session({'device': 'tablet'}))
        self.assertFalse(wants_device_session({}))


class DevicesEndpointTests(APITestCase):
    def setUp(self):
        self.me = _user('me.person')
        self.other = _user('other.person')
        self.raw_a, self.a = StaffDeviceSession.issue(self.me, device_label='My iPhone')
        self.raw_b, self.b = StaffDeviceSession.issue(self.me, device_label='My Android')
        self.raw_o, self.o = StaffDeviceSession.issue(self.other, device_label='Not mine')
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {self.raw_a}'}

    def test_list_shows_only_my_live_devices_and_marks_current(self):
        r = self.client.get('/api/v1/auth/devices/', **self.auth)
        self.assertEqual(r.status_code, 200, r.content[:300])
        rows = r.json()['devices']
        ids = {d['id'] for d in rows}
        self.assertEqual(ids, {str(self.a.pk), str(self.b.pk)})
        cur = [d for d in rows if d['current']]
        self.assertEqual(len(cur), 1)
        self.assertEqual(cur[0]['label'], 'My iPhone')
        for d in rows:
            self.assertNotIn('token_hash', d)

    def test_revoke_own_other_device_kills_it(self):
        r = self.client.delete(f'/api/v1/auth/devices/{self.b.pk}/', **self.auth)
        self.assertEqual(r.status_code, 200, r.content[:300])
        r2 = self.client.get(PROBE_URL, HTTP_AUTHORIZATION=f'Bearer {self.raw_b}')
        self.assertEqual(r2.status_code, 401)
        # Device A (the one I used) still works.
        r3 = self.client.get(PROBE_URL, **self.auth)
        self.assertEqual(r3.status_code, 200)

    def test_cannot_revoke_someone_elses_device(self):
        r = self.client.delete(f'/api/v1/auth/devices/{self.o.pk}/', **self.auth)
        self.assertEqual(r.status_code, 404, r.content[:300])
        self.o.refresh_from_db()
        self.assertIsNone(self.o.revoked_at)

    def test_desktop_drf_token_can_also_list_and_revoke(self):
        tok = Token.objects.create(user=self.me)
        r = self.client.get('/api/v1/auth/devices/', HTTP_AUTHORIZATION=f'Token {tok.key}')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()['devices']), 2)
        self.assertFalse(any(d['current'] for d in r.json()['devices']))


class ReadOnlyIdentityOnPhoneTests(APITestCase):
    """The locked read-only identities (screenshot robot / QA view) must stay
    read-only on EVERY credential — including a phone device session."""

    def test_read_only_identity_cannot_write_with_device_token(self):
        from core.screenshot_bot import READ_ONLY_USERNAMES
        name = sorted(READ_ONLY_USERNAMES)[0]
        u = get_user_model().objects.create_user(
            username=name, email='robot@example.invalid', password='x')
        raw, _ = StaffDeviceSession.issue(u, device_label='robot phone')
        r = self.client.post('/api/v1/adoption/screen-view/',
                             {'screen': '/app', 'surface': 'app'}, format='json',
                             HTTP_AUTHORIZATION=f'Bearer {raw}')
        self.assertEqual(r.status_code, 401, r.content[:200])
        self.assertIn('read-only', r.json().get('detail', ''))
        g = self.client.get(PROBE_URL, HTTP_AUTHORIZATION=f'Bearer {raw}')
        self.assertNotEqual(g.status_code, 401, g.content[:200])


class ForgotResetOnPhoneTests(APITestCase):
    """Forgot-password on the phone must end signed in for 30 days like the
    other two phone paths — otherwise a staff member who never set an Omni
    password (Microsoft users) could reset it and still be handed a 15h token."""

    def test_forgot_reset_with_device_phone_mints_device_session(self):
        user = _user('reset.person')
        from core import staff_login_views as v
        rec = mock.Mock(expires_at=timezone.now() + timedelta(minutes=5),
                        attempts=0, code_hash=v._hash('654321'), consumed=False)
        rec.save = mock.Mock()
        qs = mock.Mock(); qs.order_by.return_value.first.return_value = rec
        with mock.patch.object(v.EmailLoginCode.objects, 'filter', return_value=qs), \
             mock.patch.object(v, '_get_user', return_value=user):
            r = self.client.post('/api/v1/auth/staff/forgot-reset/',
                                 {'email': user.email, 'code': '654321',
                                  'new_password': 'Fresh-Password-77', 'device': 'phone',
                                  'device_label': 'Test phone'}, format='json')
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertEqual(r.json().get('token_type'), 'device')
        self.assertEqual(StaffDeviceSession.objects.filter(user=user).count(), 1)
        self.assertFalse(Token.objects.filter(user=user).exists())
        user.refresh_from_db()
        self.assertTrue(user.check_password('Fresh-Password-77'))
