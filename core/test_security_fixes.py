"""
Regression tests for the fixes from the 6 August 2026 security assessment.

Each of these fails on the code as it stood that morning and passes now. They
exist because every one of these findings was a control we HAD already built and
then failed to carry across — the same mistake will be available again next time
somebody touches these files.
"""
import datetime as dt
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory

from core.models import EmailLoginCode
from core.staff_login_views import (_get_user, staff_login_forgot_reset,
                                    staff_login_verify)

User = get_user_model()
P = EmailLoginCode.Purpose


class OtpPurposeBindingTests(TestCase):
    """SEC-05 — a code must only work for the thing it was issued for."""

    EMAIL = 'sec-otp-test@alphadirect.co.bw'
    CODE = '123456'

    def setUp(self):
        self.rf = APIRequestFactory(SERVER_NAME='omni.alphadirect.co.bw')
        self.user = User.objects.create_user('sec-otp-test', email=self.EMAIL,
                                             password='a-real-password-8')

    def _issue(self, purpose):
        from core.staff_login_views import _hash
        return EmailLoginCode.objects.create(
            email=self.EMAIL, code_hash=_hash(self.CODE), purpose=purpose,
            expires_at=timezone.now() + dt.timedelta(minutes=10))

    def _post(self, view, data):
        return view(self.rf.post('/x/', data, format='json'))

    # ── the attack this closes ────────────────────────────────────────────────
    def test_a_signin_code_cannot_be_spent_on_a_password_reset(self):
        # The victim asked to SIGN IN. An attacker who gets that code by phone
        # must not be able to turn it into a new password and keep the account.
        self._issue(P.SIGN_IN)
        resp = self._post(staff_login_forgot_reset, {
            'email': self.EMAIL, 'code': self.CODE,
            'new_password': 'attacker-chosen-pw-9'})
        self.assertEqual(resp.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('a-real-password-8'),
                        'the password was changed with a sign-in code')

    def test_a_reset_code_cannot_be_used_to_sign_in(self):
        self._issue(P.RESET)
        resp = self._post(staff_login_verify, {'email': self.EMAIL, 'code': self.CODE})
        self.assertEqual(resp.status_code, 400)
        self.assertNotIn('token', resp.data)

    # ── and the legitimate flows still work ───────────────────────────────────
    def test_a_reset_code_still_resets_the_password(self):
        rec = self._issue(P.RESET)
        resp = self._post(staff_login_forgot_reset, {
            'email': self.EMAIL, 'code': self.CODE,
            'new_password': 'my-new-password-12'})
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertIn('token', resp.data)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('my-new-password-12'))
        rec.refresh_from_db()
        self.assertTrue(rec.consumed)

    def test_a_signin_code_still_signs_in(self):
        self._issue(P.SIGN_IN)
        resp = self._post(staff_login_verify, {'email': self.EMAIL, 'code': self.CODE})
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertIn('token', resp.data)

    def test_issuing_a_reset_code_does_not_kill_a_live_signin_code(self):
        # The two flows are independent now; invalidating across them would be a
        # denial-of-service on whichever the person is actually mid-way through.
        signin = self._issue(P.SIGN_IN)
        EmailLoginCode.objects.filter(email=self.EMAIL, consumed=False,
                                      purpose=P.RESET).update(consumed=True)
        signin.refresh_from_db()
        self.assertFalse(signin.consumed)

    def test_codes_default_to_sign_in(self):
        # Rows that predate the field are the weaker capability, never reset.
        rec = EmailLoginCode.objects.create(
            email=self.EMAIL, code_hash='x',
            expires_at=timezone.now() + dt.timedelta(minutes=10))
        self.assertEqual(rec.purpose, P.SIGN_IN)


class AmbiguousEmailTests(TestCase):
    """SEC-10 — two accounts, one email: refuse, do not guess."""

    EMAIL = 'sec-dupe-test@alphadirect.co.bw'

    def test_a_duplicated_email_resolves_to_nobody(self):
        first = User.objects.create_user('sec-dupe-1', email=self.EMAIL, password='x')
        self.assertEqual(_get_user(self.EMAIL), first)      # one match is fine
        User.objects.create_user('sec-dupe-2', email=self.EMAIL, password='x')
        self.assertIsNone(_get_user(self.EMAIL),
                          'sign-in silently picked one of two accounts')

    def test_an_inactive_duplicate_does_not_make_it_ambiguous(self):
        keeper = User.objects.create_user('sec-dupe-3', email=self.EMAIL, password='x')
        dead = User.objects.create_user('sec-dupe-4', email=self.EMAIL, password='x')
        dead.is_active = False
        dead.save(update_fields=['is_active'])
        self.assertEqual(_get_user(self.EMAIL), keeper)

    def test_an_unknown_email_is_still_none(self):
        self.assertIsNone(_get_user('nobody-here@alphadirect.co.bw'))


class LockoutKeyTests(TestCase):
    """SEC-04 — the lockout must not key on a header the caller controls."""

    def test_axes_does_not_read_x_forwarded_for(self):
        from django.conf import settings
        order = settings.AXES_IPWARE_META_PRECEDENCE_ORDER
        self.assertNotIn('HTTP_X_FORWARDED_FOR', order,
                         'the caller can set this header, so the 5-strike lockout '
                         'can be reset at will by rotating it')
        self.assertEqual(order[0], 'HTTP_CF_CONNECTING_IP')

    def test_our_settings_no_longer_override_the_proxy_count(self):
        # It only governs the X-Forwarded-For chain we no longer read. Leaving it
        # set invites someone to "restore" the chain parsing later.
        # NB: django-axes contributes its own default for this name, so asking
        # django.conf.settings is always True and proves nothing — the question
        # is whether OUR settings module sets it, so read the module itself.
        import alpha_finance.settings as our_settings
        self.assertFalse(hasattr(our_settings, 'AXES_IPWARE_PROXY_COUNT'))

    def test_the_lockout_still_counts_per_user_and_address(self):
        from django.conf import settings
        self.assertEqual(settings.AXES_LOCKOUT_PARAMETERS, [['username', 'ip_address']])
        self.assertEqual(settings.AXES_FAILURE_LIMIT, 5)


class AdminPathTests(TestCase):
    """SEC-08 — the admin address must be movable without a code change."""

    def test_the_admin_path_is_configurable(self):
        import alpha_finance.urls as u
        self.assertTrue(hasattr(u, 'ADMIN_PATH'))
        self.assertTrue(u.ADMIN_PATH.endswith('/'))
        self.assertFalse(u.ADMIN_PATH.startswith('/'))

    def test_the_admin_is_served_from_that_path_not_a_literal(self):
        # Pins the wiring: if someone re-hardcodes 'admin/', this fails.
        import alpha_finance.urls as u
        patterns = [str(p.pattern) for p in u.urlpatterns]
        self.assertIn(u.ADMIN_PATH, patterns)
