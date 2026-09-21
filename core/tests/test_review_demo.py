"""The locked app-store reviewer identity (CFO 2026-09-06).

Apple/Google reviewers must sign in and browse the phone app without seeing one
real salary, approval or customer name, and without being able to change
anything. Every test here must go RED when its fix is reverted.
"""
import secrets

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APITestCase

from core.device_session_models import StaffDeviceSession
from core.models import EmailLoginCode, UserProfile
from core.review_demo import HOME, REVIEW_USERNAME
from core.screen_view_models import ScreenView

User = get_user_model()

REVIEW_EMAIL = 'omni-app-review@alphadirect.co.bw'
# Generated per run, never a literal: nothing that looks like a credential
# belongs in the repo, not even a throwaway one for an in-memory test database.
REVIEW_PW = 'Rv-' + secrets.token_urlsafe(12)
NORMAL_PW = 'Np-' + secrets.token_urlsafe(12)
REVIEW_CODE = '246810'

ARMED = dict(OMNI_REVIEW_MODE=True, OMNI_REVIEW_EMAIL=REVIEW_EMAIL,
             OMNI_REVIEW_CODE=REVIEW_CODE)
DISARMED = dict(OMNI_REVIEW_MODE=False, OMNI_REVIEW_EMAIL=REVIEW_EMAIL,
                OMNI_REVIEW_CODE=REVIEW_CODE)

START = '/api/v1/auth/staff/start/'
VERIFY = '/api/v1/auth/staff/verify/'
HOME_URL = '/api/v1/mobile/home/'
BEACON = '/api/v1/adoption/screen-view/'


def _review_user():
    u = User.objects.create_user(username=REVIEW_USERNAME, email=REVIEW_EMAIL,
                                 password=REVIEW_PW, is_staff=False, is_superuser=False)
    return u


def _normal_user(name='real.person'):
    email = f'{name}@alphadirect.co.bw'
    u = User.objects.create_user(username=name, email=email, password=NORMAL_PW,
                                 first_name='Boitumelo')
    UserProfile.objects.create(user=u, role=UserProfile.Role.OPERATIONS_STAFF,
                               title=UserProfile.Title.OPERATIONS, is_active=True)
    return u


class ReviewLoginBypassTests(APITestCase):
    def setUp(self):
        cache.clear()                      # the login throttle is 10/min per IP
        self.user = _review_user()
        mail.outbox = []

    # 1 — kill switch off: no bypass at all.
    @override_settings(**DISARMED)
    def test_off_the_review_email_gets_the_normal_flow(self):
        r = self.client.post(START, {'email': REVIEW_EMAIL, 'password': REVIEW_PW},
                             format='json')
        self.assertEqual(r.status_code, 200, r.content[:300])
        # The normal flow ran: a code row was written and an email went out.
        self.assertEqual(EmailLoginCode.objects.filter(email=REVIEW_EMAIL).count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        # And the fixed code is worthless while the switch is off.
        v = self.client.post(VERIFY, {'email': REVIEW_EMAIL, 'code': REVIEW_CODE},
                             format='json')
        self.assertEqual(v.status_code, 400, v.content[:300])
        self.assertEqual(StaffDeviceSession.objects.count(), 0)

    # 2 — armed: start sends nothing and writes no code row.
    @override_settings(**ARMED)
    def test_on_start_skips_the_code_and_the_email(self):
        r = self.client.post(START, {'email': REVIEW_EMAIL, 'password': REVIEW_PW},
                             format='json')
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertIs(r.json().get('otp_sent'), True)
        self.assertEqual(EmailLoginCode.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(**ARMED)
    def test_on_start_still_refuses_a_wrong_password(self):
        r = self.client.post(START, {'email': REVIEW_EMAIL, 'password': 'not-the-password'},
                             format='json')
        self.assertEqual(r.status_code, 400, r.content[:300])
        self.assertEqual(EmailLoginCode.objects.count(), 0)

    # 3 — armed: the fixed code mints a 30-day device session.
    @override_settings(**ARMED)
    def test_on_fixed_code_mints_a_device_session(self):
        r = self.client.post(VERIFY, {'email': REVIEW_EMAIL, 'code': REVIEW_CODE,
                                      'device': 'phone', 'device_label': 'Review iPhone'},
                             format='json')
        self.assertEqual(r.status_code, 200, r.content[:300])
        body = r.json()
        self.assertEqual(body.get('token_type'), 'device')
        self.assertEqual(len(body['token']), 64)
        self.assertIs(body.get('change_required'), False)
        self.assertEqual(StaffDeviceSession.objects.filter(user=self.user).count(), 1)

    # 4 — armed: a wrong code is still refused.
    # The bypass must key on the locked IDENTITY, not just the configured email.
    # If OMNI_REVIEW_EMAIL were ever pointed at a real member of staff, their login
    # would silently drop to a static code.
    @override_settings(**ARMED)
    def test_bypass_refuses_an_email_that_is_not_the_review_identity(self):
        self.user.username = 'someone.real'
        self.user.save(update_fields=['username'])
        r = self.client.post(START, {'email': REVIEW_EMAIL, 'password': REVIEW_PW}, format='json')
        self.assertEqual(r.status_code, 400, r.content[:300])
        self.assertNotIn('otp_sent', r.json())

    @override_settings(**ARMED)
    def test_on_wrong_code_is_rejected(self):
        r = self.client.post(VERIFY, {'email': REVIEW_EMAIL, 'code': '000000',
                                      'device': 'phone'}, format='json')
        self.assertEqual(r.status_code, 400, r.content[:300])
        self.assertEqual(StaffDeviceSession.objects.count(), 0)

    # 5 — a normal person's login is untouched while review mode is armed.
    @override_settings(**ARMED)
    def test_on_normal_user_login_is_unaffected(self):
        other = _normal_user()
        r = self.client.post(START, {'email': other.email, 'password': NORMAL_PW},
                             format='json')
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertEqual(EmailLoginCode.objects.filter(email=other.email).count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        # The reviewer's fixed code is not a skeleton key for anyone else.
        v = self.client.post(VERIFY, {'email': other.email, 'code': REVIEW_CODE},
                             format='json')
        self.assertEqual(v.status_code, 400, v.content[:300])
        self.assertEqual(StaffDeviceSession.objects.count(), 0)


class ReviewDemoMiddlewareTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.review = _review_user()
        self.raw_review, _ = StaffDeviceSession.issue(self.review, device_label='Review iPhone')
        self.normal = _normal_user()
        self.raw_normal, _ = StaffDeviceSession.issue(self.normal, device_label='Staff phone')

    def _hdr(self, raw):
        return {'HTTP_AUTHORIZATION': f'Bearer {raw}'}

    # 6 — the reviewer sees the invented fixture, never the database.
    @override_settings(**ARMED)
    def test_review_user_get_returns_the_fixture(self):
        r = self.client.get(HOME_URL, **self._hdr(self.raw_review))
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertEqual(r.json(), HOME)
        self.assertEqual(r.json()['first_name'], 'Neo')

    @override_settings(**DISARMED)
    def test_kill_switch_off_stops_the_demo_data(self):
        r = self.client.get(HOME_URL, **self._hdr(self.raw_review))
        self.assertNotEqual(r.content.decode()[:400], '')
        if r.status_code == 200:
            self.assertNotEqual(r.json().get('first_name'), 'Neo')

    # 6b — an API path that is NOT in the fixture map must still never reach a
    # real view. This is the control that stops a reviewer seeing real data on
    # any screen nobody thought to list.
    @override_settings(**ARMED)
    def test_unlisted_api_path_is_denied_not_passed_through(self):
        r = self.client.get('/api/v1/underwriting/quotes/templates/',
                            **self._hdr(self.raw_review))
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertEqual(r.json(), {})

    # 6c — the deny is limited to data. Pages and assets still load, or the
    # reviewer sees a blank app.
    @override_settings(**ARMED)
    def test_non_api_path_still_reaches_the_real_view(self):
        r = self.client.get('/api-not-a-prefix/', **self._hdr(self.raw_review))
        self.assertNotEqual(r.status_code, 200)

    # 6d — H96: the middleware must recognise EVERY credential shape this identity
    # can hold, not just the phone device token. A DRF token slipped past it and
    # reached real views, so the reviewer's login could read the real phonebook.
    @override_settings(**ARMED)
    def test_review_user_with_a_drf_token_is_also_intercepted(self):
        from rest_framework.authtoken.models import Token as DRFToken
        tok, _ = DRFToken.objects.get_or_create(user=self.review)
        r = self.client.get(HOME_URL, HTTP_AUTHORIZATION=f'Token {tok.key}')
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertEqual(r.json(), HOME)

    @override_settings(**ARMED)
    def test_review_user_with_a_drf_token_cannot_read_a_real_endpoint(self):
        from rest_framework.authtoken.models import Token as DRFToken
        tok, _ = DRFToken.objects.get_or_create(user=self.review)
        r = self.client.get('/api/v1/staff/phonebook/', HTTP_AUTHORIZATION=f'Token {tok.key}')
        # Denied by default: no fixture for this path, so nothing real comes back.
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertEqual(r.json(), {})

    # 7 — a write gets a canned success and reaches nothing.
    @override_settings(**ARMED)
    def test_review_user_post_is_swallowed(self):
        before = ScreenView.objects.count()
        r = self.client.post(BEACON, {'screen': '/app', 'surface': 'app'},
                             format='json', **self._hdr(self.raw_review))
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertIs(r.json().get('ok'), True)
        self.assertIn('nothing was changed', r.json().get('detail', ''))
        self.assertEqual(ScreenView.objects.count(), before)

    # 8 — everyone else is completely untouched.
    @override_settings(**ARMED)
    def test_normal_user_request_runs_the_real_view(self):
        r = self.client.get(HOME_URL, **self._hdr(self.raw_normal))
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertEqual(r.json()['first_name'], 'Boitumelo')

    @override_settings(**ARMED)
    def test_normal_user_write_still_reaches_the_real_view(self):
        before = ScreenView.objects.count()
        r = self.client.post(BEACON, {'screen': '/app', 'surface': 'app'},
                             format='json', **self._hdr(self.raw_normal))
        self.assertIn(r.status_code, (200, 201), r.content[:300])
        self.assertEqual(ScreenView.objects.count(), before + 1)


class ReviewIdentityLockTests(APITestCase):
    def test_review_name_is_in_the_read_only_backstop(self):
        from core.screenshot_bot import READ_ONLY_USERNAMES
        self.assertIn(REVIEW_USERNAME, READ_ONLY_USERNAMES)

    @override_settings(**DISARMED)
    def test_read_only_backstop_refuses_a_write_when_the_middleware_is_inert(self):
        u = _review_user()
        raw, _ = StaffDeviceSession.issue(u, device_label='Review iPhone')
        r = self.client.post(BEACON, {'screen': '/app', 'surface': 'app'},
                             format='json', HTTP_AUTHORIZATION=f'Bearer {raw}')
        self.assertEqual(r.status_code, 401, r.content[:300])
        self.assertIn('read-only', r.json().get('detail', ''))

    @override_settings(OMNI_REVIEW_MODE=True, OMNI_REVIEW_EMAIL='', OMNI_REVIEW_CODE='')
    def test_half_configured_switch_stays_off(self):
        from core.review_demo import review_mode_on
        self.assertFalse(review_mode_on())


class EnsureReviewAccountCommandTests(APITestCase):
    @override_settings(**ARMED)
    def test_command_creates_a_powerless_account(self):
        import os
        from io import StringIO
        from django.core.management import call_command
        os.environ['OMNI_REVIEW_PASSWORD'] = REVIEW_PW
        try:
            call_command('ensure_review_account', stdout=StringIO())
        finally:
            os.environ.pop('OMNI_REVIEW_PASSWORD', None)
        u = User.objects.get(username=REVIEW_USERNAME)
        self.assertEqual(u.email, REVIEW_EMAIL)
        self.assertTrue(u.is_active)
        self.assertFalse(u.is_staff)
        self.assertFalse(u.is_superuser)
        self.assertTrue(u.check_password(REVIEW_PW))

    @override_settings(**ARMED)
    def test_command_refuses_without_a_password(self):
        import os
        from django.core.management import call_command
        from django.core.management.base import CommandError
        os.environ.pop('OMNI_REVIEW_PASSWORD', None)
        with self.assertRaises(CommandError):
            call_command('ensure_review_account')
        self.assertFalse(User.objects.filter(username=REVIEW_USERNAME).exists())
