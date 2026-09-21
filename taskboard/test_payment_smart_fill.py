"""taskboard/test_payment_smart_fill.py — the paste→lines smart-fill endpoint.

CFO 2026-07-29: "a place they copy-paste their junk data and DeepSeek nicely
formats it to fill these fields." This reuses omni's existing cleanup engine
(core.ai_assist) — the tests mock that engine so nothing leaves the box.

The rules that must never regress:
  - it is ADVISORY: it writes nothing and creates no PaymentRequest;
  - the PII firewall runs BEFORE anything is sent to the model;
  - an AI outage or a bad response degrades to ok:false, never a 500;
  - the model's output is treated as untrusted and normalised.
"""
from unittest import mock

from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework.test import APITestCase

from taskboard.models import PaymentRequest


class SmartFillTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user('lthebe', email='lthebe@alphadirect.co.bw')
        self.client.force_authenticate(self.user)
        self.url = reverse('v1-payment-request-parse')

    def test_requires_auth(self):
        self.client.force_authenticate(user=None)
        r = self.client.post(self.url, {'text': 'anything'}, format='json')
        self.assertIn(r.status_code, (401, 403))

    def test_empty_text_is_refused(self):
        r = self.client.post(self.url, {'text': '   '}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_paste_is_parsed_into_lines(self):
        fake = ('{"payee":"Carfil Services","lines":[{"description":"Panel repair",'
                '"amount":"5,307.32","invoice_number":"KA-40118",'
                '"invoice_date":"2026-04-24","claim_number":"G2026004287"}]}')
        with mock.patch('core.ai_assist.reasoning_complete',
                        return_value=fake) as rc:
            r = self.client.post(self.url, {'text': 'Carfil claim G2026004287 ...'},
                                 format='json')
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertTrue(body['ok'])
        self.assertEqual(body['payee'], 'Carfil Services')
        self.assertEqual(len(body['lines']), 1)
        ln = body['lines'][0]
        self.assertEqual(ln['amount'], '5307.32')          # commas stripped, numeric
        self.assertEqual(ln['claim_number'], 'G2026004287')
        self.assertEqual(ln['invoice_number'], 'KA-40118')
        # ADVISORY: absolutely nothing was created.
        self.assertFalse(PaymentRequest.objects.exists())
        rc.assert_called_once()

    def test_account_number_is_redacted_but_the_paste_still_goes(self):
        """The redact-then-send path: a realistic paste with a bank account keeps
        enough real text to send, and the account number is scrubbed first."""
        captured = {}
        def _capture(text, **kw):
            captured['sent'] = text
            return '{"payee":"Carfil Services","lines":[]}'
        paste = ('Please pay Carfil Services for the panel-beating on claim '
                 'G2026004287, their bank account is 4071234567, invoice KA-40118.')
        with mock.patch('core.ai_assist.reasoning_complete',
                        side_effect=_capture) as rc:
            r = self.client.post(self.url, {'text': paste}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        rc.assert_called_once()
        self.assertIn('sent', captured)                       # the model WAS called
        self.assertNotIn('4071234567', captured['sent'])      # …but not with the account
        self.assertIn('[NUMBER-REDACTED]', captured['sent'])

    def test_a_paste_that_is_mostly_account_numbers_is_refused_without_a_call(self):
        with mock.patch('core.ai_assist.reasoning_complete') as rc:
            r = self.client.post(self.url, {'text': 'pay 4071234567 to vendor'},
                                 format='json')
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()['ok'])
        rc.assert_not_called()                                # nothing left the box

    def test_wrong_shaped_json_degrades_not_500(self):
        """'json_object' guarantees valid JSON, not a JSON object. A cheap tier
        can hand back an array or a scalar — none of these may 500 (Fable review)."""
        for bad in ('[]', '123', '"hello"', '{"lines": {"not": "a list"}}'):
            with self.subTest(shape=bad):
                with mock.patch('core.ai_assist.reasoning_complete', return_value=bad):
                    r = self.client.post(self.url, {'text': 'something'}, format='json')
                self.assertEqual(r.status_code, 200, f'{bad} -> {r.status_code}')
                # never a server error; either ok:false or ok:true with no lines
                if r.json().get('ok'):
                    self.assertEqual(r.json().get('lines'), [])

    def test_ai_outage_degrades_not_500(self):
        from core.ai_assist import DeepSeekUnavailable
        with mock.patch('core.ai_assist.reasoning_complete',
                        side_effect=DeepSeekUnavailable('down')):
            r = self.client.post(self.url, {'text': 'something'}, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()['ok'])

    def test_malformed_ai_response_degrades_not_500(self):
        with mock.patch('core.ai_assist.reasoning_complete',
                        return_value='not json at all'):
            r = self.client.post(self.url, {'text': 'something'}, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()['ok'])

    def test_junk_lines_are_dropped_and_output_is_capped(self):
        fake = ('{"payee":"","lines":[{"description":"","amount":""},'
                '{"description":"Real line","amount":"100"}]}')
        with mock.patch('core.ai_assist.reasoning_complete',
                        return_value=fake):
            r = self.client.post(self.url, {'text': 'x'}, format='json')
        lines = r.json()['lines']
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]['description'], 'Real line')
