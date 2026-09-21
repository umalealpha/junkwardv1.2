"""procurement/test_claims_review_check.py — Aria's pre-send checks (feature #4)."""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import Company
from procurement.claims_models import ClaimsAssessment


def _report():
    return {
        'claim_no': 'G1', 'repairer': 'Carfil', 'currency': 'BWP',
        'summary': {'Parts': 2000.0, 'Labour': 5000.0, 'Paint': 1000.0,
                    'Sundries': 0.0, 'Excess': 0.0},
        'groups': [
            {'kind': 'parts', 'supplier_label': 'Motor Centre',
             'subtotal_excl': 2000.0,
             'lines': [{'description': 'Bumper', 'qty': 1.0,
                        'unit_price': 2000.0, 'total': 2000.0, 'markup': 0.0}]},
            {'kind': 'labour', 'supplier_label': 'Carfil',
             'subtotal_excl': 5000.0,
             'lines': [{'description': 'Labour', 'units': 1.0,
                        'rate': 5000.0, 'total': 5000.0}]},
        ],
    }


class ReviewCheckTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(code='TC', name='Test Co')
        cls.user = User.objects.create_user('u', password='x', is_superuser=True)

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _mk(self, **kw):
        d = dict(company=self.company,
                 status=ClaimsAssessment.Status.READY_FOR_REVIEW,
                 report_json=_report())
        d.update(kw)
        return ClaimsAssessment.objects.create(**d)

    def _url(self, a):
        return f'/api/v1/claims-po/{a.pk}/review-check/'

    def test_requires_auth(self):
        a = self._mk()
        self.assertIn(APIClient().post(self._url(a)).status_code, (401, 403))

    def test_excess_over_repair_flags_high(self):
        a = self._mk(excess_amount=Decimal('999999'))
        resp = self.client.post(self._url(a))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('high', [f['severity'] for f in resp.json()['flags']])

    def test_markup_ceiling_warns(self):
        a = self._mk(markup_pct=Decimal('75'))
        resp = self.client.post(self._url(a))
        self.assertEqual(resp.status_code, 200)
        joined = ' '.join(f['message'] for f in resp.json()['flags']).lower()
        self.assertIn('markup', joined)

    def test_runs_and_returns_flags(self):
        resp = self.client.post(self._url(self._mk()))
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body['checks_ran'])
        self.assertIsInstance(body['flags'], list)
        self.assertTrue(body['flags'])

    def test_unparsed_never_500s(self):
        a = self._mk(report_json={},
                     status=ClaimsAssessment.Status.PARSE_FAILED)
        resp = self.client.post(self._url(a))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()['checks_ran'])
