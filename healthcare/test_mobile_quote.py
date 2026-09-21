"""Mobile health quote (Omni Mobile) — live price + email-to-client endpoints.

Red-first: the price endpoint reuses the same rater the saved quotes use; the
email endpoint reuses build_quote_pdf + the shared mailer.
"""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from healthcare.models import HealthQuote

User = get_user_model()


class MobileHealthQuoteTests(TestCase):
    def setUp(self):
        self.u = User.objects.create_user('hq', email='hq@x', password='x')
        self.c = APIClient()
        self.c.force_authenticate(user=self.u)

    def test_price_returns_premium(self):
        r = self.c.post('/api/v1/health/quotes/price/',
                        {'tier': 'AD_ESSENTIAL', 'gender': 'F', 'age': 34}, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['currency'], 'BWP')
        self.assertGreater(float(r.data['premium_incl']), 0)
        # incl = excl + vat, to the cent
        self.assertAlmostEqual(float(r.data['premium_incl']),
                               float(r.data['premium_excl']) + float(r.data['vat']), places=2)

    def test_price_unknown_plan_is_400_not_500(self):
        r = self.c.post('/api/v1/health/quotes/price/',
                        {'tier': 'NOPE', 'gender': 'F', 'age': 34}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_price_requires_auth(self):
        r = APIClient().post('/api/v1/health/quotes/price/',
                             {'tier': 'AD_ESSENTIAL', 'gender': 'F', 'age': 34}, format='json')
        self.assertIn(r.status_code, (401, 403))

    @mock.patch('healthcare.quote_pdf.build_quote_pdf', return_value=b'%PDF-1.4 test')
    @mock.patch('core.notifications.send_html_with_cfo_cc', return_value=1)
    def test_email_sends_and_stamps(self, m_send, m_pdf):
        from healthcare.models import HealthQuoteMember
        q = HealthQuote.objects.create(ref='HQ-TEST-1', client_name='Acme',
                                       contact_email='hr@acme.co.bw',
                                       gross_excl=100, subtotal_excl=100, total_incl=114)
        HealthQuoteMember.objects.create(quote=q, full_name='Main', member_type='main',
                                         gender='F', age=34, tier='AD_ESSENTIAL',
                                         premium_excl=100, vat=14, premium_incl=114)
        r = self.c.post(f'/api/v1/health/quotes/{q.id}/email/', {'to': 'buyer@acme.co.bw'}, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data['emailed'])
        q.refresh_from_db()
        self.assertEqual(q.emailed_to, 'buyer@acme.co.bw')
        self.assertIsNotNone(q.emailed_at)
        m_send.assert_called_once()
        # customer mail: never cc the EXCO board
        self.assertFalse(m_send.call_args.kwargs.get('cc_cfo', False))

    def test_email_invalid_address_400(self):
        q = HealthQuote.objects.create(ref='HQ-TEST-2', client_name='Acme')
        r = self.c.post(f'/api/v1/health/quotes/{q.id}/email/', {'to': 'notanemail'}, format='json')
        self.assertEqual(r.status_code, 400)
