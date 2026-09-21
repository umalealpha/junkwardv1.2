"""taskboard/test_payment_load_window.py — payment loading window ABOLISHED.

CFO 2026-09-02: "abolish the 9.15 window, but it puts a negative review point for
the loaders in their monthly performance feedback that they didn't plan the task
well."

The 08:00-09:15 morning window (introduced 2026-09-01, PAY-WIN-02) is gone:
  * a payment can be RAISED at any time — there is NO 403 block;
  * a non-CFO who raises OUTSIDE the old window is recorded (loaded_off_window),
    and the count surfaces as a planning concern in their monthly feedback;
  * the CFO is exempt — never flagged;
  * the override-request route is retired (returns 400 — there is nothing to
    override); the model + queue stay only as the audit trail of the window
    that WAS;
  * sign-off was never windowed, and still is not.

This exercises the REAL create endpoint at a patched clock, so criterion 1+2
(no block + the flag) fail without their fix.

Run: manage.py test taskboard.test_payment_load_window
"""
from datetime import date, datetime
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from taskboard.models import PaymentLoadOverride, PaymentRequest

GABS = ZoneInfo('Africa/Gaborone')


def at(hour: int, minute: int = 0):
    """Patch the clock the view + helpers read (timezone.localtime)."""
    stamp = datetime(2026, 9, 1, hour, minute, tzinfo=GABS)
    return mock.patch('taskboard.payment_views.timezone.localtime',
                      return_value=stamp)


@override_settings(
    PAYMENT_FIRST_APPROVER_EMAILS=['fin-a@example.invalid', 'fin-b@example.invalid'],
    PAYMENT_LOAD_WINDOW_OPEN='08:00',
    PAYMENT_LOAD_WINDOW_CLOSE='09:15',
)
class PaymentWindowAbolishedTests(APITestCase):
    def setUp(self):
        # Invented fixtures — no real staff address in a test file. A superuser is
        # what _cfo_user() falls back to, i.e. the CFO here. Two approvers so the
        # raiser is never the ONLY approver (that 500s as "no finance approver").
        self.cfo = User.objects.create_superuser(
            'cfo-win', 'cfo-win@example.invalid', 'x')
        self.approver_a = User.objects.create_user('fin-a', email='fin-a@example.invalid')
        self.approver_b = User.objects.create_user('fin-b', email='fin-b@example.invalid')
        self.clerk = User.objects.create_user('clerk-w', email='clerk-w@example.invalid')
        self.list_url = reverse('v1-payment-requests')
        self.override_url = reverse('v1-payment-load-override')

    def _post(self, user, amount='1000.00'):
        self.client.force_authenticate(user)
        return self.client.post(self.list_url, {
            'subject': 'Claims payable batch',
            'category': PaymentRequest.Category.OTHER,
            'payee': 'Gaborone Panel Beaters',
            'account_name': 'Gaborone Panel Beaters',
            'bank_name': 'FNB Botswana',
            'account_number': 'TEST-ACCT-NOT-REAL',
            'line_items': [{'description': 'Vendor A', 'amount': amount}],
        }, format='json')

    # ── no block: a payment can be raised at any time ────────────────────
    def test_afternoon_raise_is_allowed_and_flagged(self):
        with at(14, 0):
            r = self._post(self.clerk)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertTrue(PaymentRequest.objects.get().loaded_off_window)

    def test_before_open_is_allowed_and_flagged(self):
        with at(7, 59):
            r = self._post(self.clerk)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertTrue(PaymentRequest.objects.get().loaded_off_window)

    def test_one_minute_after_close_is_allowed_and_flagged(self):
        with at(9, 16):
            r = self._post(self.clerk)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertTrue(PaymentRequest.objects.get().loaded_off_window)

    # ── in-window: allowed, and NOT flagged ──────────────────────────────
    def test_inside_the_window_is_not_flagged(self):
        with at(8, 30):
            r = self._post(self.clerk)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertFalse(PaymentRequest.objects.get().loaded_off_window)

    def test_open_edge_0800_is_not_flagged(self):
        with at(8, 0):
            self.assertEqual(self._post(self.clerk).status_code, 201)
        self.assertFalse(PaymentRequest.objects.get().loaded_off_window)

    def test_close_edge_0915_is_not_flagged(self):
        with at(9, 15):
            self.assertEqual(self._post(self.clerk).status_code, 201)
        self.assertFalse(PaymentRequest.objects.get().loaded_off_window)

    # ── the CFO is exempt — never flagged ────────────────────────────────
    def test_cfo_off_window_is_allowed_and_not_flagged(self):
        with at(22, 30):
            r = self._post(self.cfo)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertFalse(PaymentRequest.objects.get().loaded_off_window)

    # ── the override-request route is retired ────────────────────────────
    def test_requesting_an_override_is_retired(self):
        with at(14, 0):
            self.client.force_authenticate(self.clerk)
            r = self.client.post(self.override_url, {'reason': 'urgent supplier'},
                                 format='json')
        self.assertEqual(r.status_code, 400, r.content)
        self.assertTrue(r.json().get('abolished'))
        self.assertEqual(PaymentLoadOverride.objects.count(), 0)   # no dead row

    def test_window_status_reports_abolished_and_honest(self):
        with at(14, 0):
            self.client.force_authenticate(self.clerk)
            body = self.client.get(self.override_url).json()
        self.assertTrue(body['window']['abolished'])
        self.assertFalse(body['window']['is_open'])   # honest, not a hardcoded True

    def test_only_the_cfo_can_decide_an_override(self):
        # Kept: a legacy pending row can still only be decided by the CFO.
        o = PaymentLoadOverride.objects.create(
            requested_by=self.clerk, reason='legacy row', for_date=date(2026, 9, 1),
            status=PaymentLoadOverride.Status.PENDING)
        self.client.force_authenticate(self.approver_a)
        r = self.client.post(reverse('v1-payment-load-override-decide', args=[o.id]),
                             {'decision': 'approve'}, format='json')
        self.assertEqual(r.status_code, 403, r.content)

    # ── sign-off was never windowed, and still is not ────────────────────
    def test_finance_can_sign_off_in_the_afternoon(self):
        with at(8, 30):
            r = self._post(self.clerk)
        self.assertEqual(r.status_code, 201, r.content)
        pr = PaymentRequest.objects.get()
        self.client.force_authenticate(self.approver_a)
        with at(14, 0):
            dec = self.client.post(f'{self.list_url}{pr.pk}/decide/',
                                   {'decision': 'approve'}, format='json')
        self.assertEqual(dec.status_code, 200, dec.content)
        pr.refresh_from_db()
        self.assertEqual(pr.status, PaymentRequest.Status.PENDING_CFO)
