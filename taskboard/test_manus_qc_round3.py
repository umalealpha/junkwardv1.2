"""Manus round 3, 2026-08-09 — three residual defects and the control gap that
let BWP 86,470.94 be paid twice on a single authorisation.

The theme is unchanged: every one of these returned a 200 and looked like it
worked.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import ApiKey
from taskboard.models import PaymentRequest
from taskboard.payment_duplicates import compare_lines
from taskboard.payment_views import _line_rows

User = get_user_model()
PLAINTEXT = 'qcround3key_testonly_0123456789'


class SameRequestDuplicateWithNoReferenceTests(TestCase):
    """'FAC Premium – May', claim 'N/A' — no token survives, so the two lines
    were never compared. 86,470.94 was paid twice on PAY/ADIC/2026/08/06/0003."""

    def test_identical_untokenised_lines_are_caught(self):
        lines = [
            {'description': 'FAC Premium - May', 'claim_number': 'N/A', 'amount': '86470.94'},
            {'description': 'FAC Premium - May', 'claim_number': 'N/A', 'amount': '86470.94'},
        ]
        hard = compare_lines(lines, [], currency='BWP')['hard']
        self.assertTrue(hard, 'two identical lines on one pack must block')
        self.assertEqual(hard[0]['clash_kind'], 'same_request')

    def test_a_different_amount_is_not_a_duplicate(self):
        lines = [
            {'description': 'FAC Premium - May', 'claim_number': 'N/A', 'amount': '86470.94'},
            {'description': 'FAC Premium - May', 'claim_number': 'N/A', 'amount': '12000.00'},
        ]
        self.assertEqual(compare_lines(lines, [], currency='BWP')['hard'], [])

    def test_different_text_same_amount_is_not_a_duplicate(self):
        lines = [
            {'description': 'Rent July', 'claim_number': 'N/A', 'amount': '5000.00'},
            {'description': 'Utilities July', 'claim_number': 'N/A', 'amount': '5000.00'},
        ]
        self.assertEqual(compare_lines(lines, [], currency='BWP')['hard'], [])


class LineKindTests(TestCase):
    def _kind(self, line):
        return _line_rows(PaymentRequest(ref='X', line_items=[line]))[0]['line_kind']

    def test_each_kind(self):
        self.assertEqual(self._kind({'claim_number': 'G2026004718'}), 'claim')
        self.assertEqual(self._kind({'invoice_number': 'INV-2201'}), 'supplier')
        self.assertEqual(self._kind({'description': 'Office rent August'}), 'internal')


class SweepFiltersTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.raiser = User.objects.create_user('hraiser3', 'h3@example.invalid', 'x')
        cls.svc = User.objects.create_user('iqcsvc3', 'qc3@example.invalid', 'x')
        PaymentRequest.objects.create(
            ref='PR-R3-PAID', entity='ADIC', status=PaymentRequest.Status.PAID,
            payee='A', currency='BWP', total='1.00', created_by=cls.raiser)
        PaymentRequest.objects.create(
            ref='PR-R3-REJ', entity='ADIC', status=PaymentRequest.Status.REJECTED,
            payee='B', currency='BWP', total='2.00', created_by=cls.raiser)
        ApiKey.objects.create(
            label='QC r3', key_prefix=PLAINTEXT[:12],
            key_hash=make_password(PLAINTEXT), service_user=cls.svc,
            allowed_scopes=['read-only'], is_active=True)

    def _c(self):
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f'ApiKey {PLAINTEXT}')
        return c

    def test_status_isolates_paid_from_rejected(self):
        res = self._c().get('/api/v1/payment-requests/?status=paid')
        self.assertEqual(res.status_code, 200, res.content[:200])
        self.assertEqual([r['ref'] for r in res.json()['requests']], ['PR-R3-PAID'])

    def test_an_unknown_status_is_refused_not_ignored(self):
        res = self._c().get('/api/v1/payment-requests/?status=nonsense')
        self.assertEqual(res.status_code, 400)

    def test_window_takes_a_number_of_days(self):
        self.assertEqual(self._c().get('/api/v1/payment-requests/?window=7').status_code, 200)
        self.assertEqual(self._c().get('/api/v1/payment-requests/?window=abc').status_code, 400)


class ReportsIndexShowsOnlyOpenableDoorsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.svc = User.objects.create_user('jqcsvc4', 'qc4@example.invalid', 'x')
        ApiKey.objects.create(
            label='QC r3b', key_prefix=PLAINTEXT[:12],
            key_hash=make_password(PLAINTEXT), service_user=cls.svc,
            allowed_scopes=['read-only'], is_active=True)

    def test_every_listed_path_can_actually_be_opened_by_this_key(self):
        from core.api_key_auth import _path_allowed_by_scopes
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f'ApiKey {PLAINTEXT}')
        res = c.get('/api/v1/reports/')
        self.assertEqual(res.status_code, 200, res.content[:200])
        body = res.json()
        self.assertTrue(body['reports'], 'a reader that can open none of them is not a reader')
        for path in body['reports']:
            self.assertTrue(_path_allowed_by_scopes(path, ['read-only'], 'GET'),
                            f'{path} is listed but would 401 — a locked door in the index')
