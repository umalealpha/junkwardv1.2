"""The four defects Manus found on production on 2026-08-09, second pass.

Every one is the same shape: the server said 200 and the caller believed it.
An ignored filter, a hidden result set and a pointer to a 404 all look like
success from the outside, which is why they survived a green test suite.
"""
import re

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import ApiKey
from taskboard.models import PaymentRequest
from taskboard.payment_views import _line_rows

User = get_user_model()
PLAINTEXT = 'qcround2key_testonly_0123456789'


class ClaimHiddenInTheDescriptionTests(TestCase):
    """CARFIL rows carry claim_number '' with the claim inside the payee text."""

    def _rows(self, line):
        pr = PaymentRequest(ref='X', line_items=[line])
        return _line_rows(pr)[0]

    def test_the_claim_is_pulled_out_of_the_description(self):
        row = self._rows({'description': 'G2026004287 CARFIL SERVICES',
                          'amount': '5307.32'})
        self.assertEqual(row['claim_no'], 'G2026004287')
        self.assertEqual(row['payee'], 'CARFIL SERVICES',
                         'the residual payee must be exposed without the claim token')

    def test_a_real_claim_field_still_wins(self):
        row = self._rows({'claim_number': 'G2026009999',
                          'description': 'G2026004287 CARFIL SERVICES'})
        self.assertEqual(row['claim_no'], 'G2026009999')

    def test_ordinary_descriptions_are_untouched(self):
        row = self._rows({'description': 'Office rent August'})
        self.assertEqual(row['claim_no'], '')
        self.assertEqual(row['payee'], 'Office rent August')


class SettledRequestsAreVisibleToTheQcReaderTests(TestCase):
    """`?lines=1` alone returned an empty 200 — every line-carrying request is PAID."""

    @classmethod
    def setUpTestData(cls):
        cls.raiser = User.objects.create_user('craiser', 'cr@example.invalid', 'x')
        cls.svc = User.objects.create_user('dqcsvc2', 'qc2@example.invalid', 'x')
        PaymentRequest.objects.create(
            ref='PR-PAID-1', entity='ADIC', status=PaymentRequest.Status.PAID,
            payee='Settled payee', currency='BWP', total='100.00',
            created_by=cls.raiser,
            line_items=[{'description': 'G2026004287 CARFIL SERVICES',
                         'amount': '5307.32'}])
        ApiKey.objects.create(
            label='QC round 2', key_prefix=PLAINTEXT[:12],
            key_hash=make_password(PLAINTEXT), service_user=cls.svc,
            allowed_scopes=['read-only'], is_active=True)

    def _c(self):
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f'ApiKey {PLAINTEXT}')
        return c

    def test_lines_alone_no_longer_returns_an_empty_list(self):
        res = self._c().get('/api/v1/payment-requests/?lines=1')
        self.assertEqual(res.status_code, 200, res.content[:300])
        body = res.json()
        self.assertTrue(body['requests'],
                        'a settled request must reach the QC reader without &all=1')
        self.assertEqual(body['requests'][0]['lines'][0]['claim_no'], 'G2026004287')

    def test_the_envelope_says_what_it_is_showing(self):
        body = self._c().get('/api/v1/payment-requests/?lines=1').json()
        self.assertEqual(body['showing'], 'all requests')
        self.assertTrue(body['includes_settled'],
                        'an empty list must never be mistakable for "nothing exists"')


class ReportsPointerTests(TestCase):
    def test_the_listed_paths_do_not_have_a_doubled_api_segment(self):
        u = User.objects.create_superuser('erepadmin', 'ra@example.invalid', 'x')
        c = APIClient()
        c.force_authenticate(u)
        res = c.get('/api/v1/reports/')
        self.assertEqual(res.status_code, 200)
        for path in res.json()['reports']:
            self.assertNotIn('/api/v1/api/', path,
                             f'{path} points at a 404 — the doubled segment is back')
            self.assertTrue(path.startswith('/'), path)
