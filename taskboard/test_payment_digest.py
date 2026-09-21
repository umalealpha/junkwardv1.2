"""The daily payment digest — one email, every request, numbers never from a model."""
from __future__ import annotations

from decimal import Decimal
from io import StringIO
from unittest import mock

from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.test import TestCase

from taskboard import payment_digest
from taskboard.models import PaymentRequest


class PaymentDigestTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.loader = User.objects.create_user('kago', 'kago@example.invalid', 'x')
        cls.other = User.objects.create_user('pako', 'pako@example.invalid', 'x')

    def _pr(self, ref, status, total, **kw):
        return PaymentRequest.objects.create(
            ref=ref, status=status, currency='BWP', total=Decimal(total),
            entity=kw.pop('entity', 'ADIC'), payee=kw.pop('payee', 'A Supplier'),
            inputter=kw.pop('inputter', 'Kago'), created_by=cls_or(self).loader, **kw)

    def test_it_counts_what_is_waiting_on_the_cfo_separately(self):
        self._pr('P1', PaymentRequest.Status.PENDING_CFO, '1000.00')
        self._pr('P2', PaymentRequest.Status.PENDING_FINANCE, '250.00')
        d = payment_digest.collect()
        self.assertEqual(len(d['waiting_cfo']), 1)
        self.assertEqual(len(d['waiting_finance']), 1)
        self.assertEqual(d['total_waiting_cfo'], Decimal('1000.00'))
        self.assertEqual(d['total_waiting_finance'], Decimal('250.00'))

    def test_paid_and_rejected_are_not_counted_as_open(self):
        self._pr('P3', PaymentRequest.Status.PAID, '9999.00')
        self._pr('P4', PaymentRequest.Status.REJECTED, '8888.00')
        d = payment_digest.collect()
        self.assertEqual(d['open_count'], 0)

    def test_an_uncountersigned_override_is_singled_out(self):
        self._pr('P5', PaymentRequest.Status.PENDING_CFO, '13662.67',
                 duplicate_override_reason='FNB rejected the first attempt on 28 July.')
        d = payment_digest.collect()
        self.assertEqual(len(d['overrides']), 1)
        self.assertFalse(d['overrides'][0]['countersigned'])

    def test_a_countersigned_override_is_marked_as_such(self):
        self._pr('P6', PaymentRequest.Status.PENDING_CFO, '500.00',
                 duplicate_override_reason='FNB rejected the first attempt on 28 July.',
                 duplicate_override_approved_by=self.other)
        d = payment_digest.collect()
        self.assertTrue(d['overrides'][0]['countersigned'])

    def test_the_email_still_goes_out_when_the_ai_is_down(self):
        self._pr('P7', PaymentRequest.Status.PENDING_CFO, '1000.00')
        mail.outbox = []
        with mock.patch('core.ai_assist.reasoning_complete', side_effect=RuntimeError('down')):
            call_command('payment_daily_digest', '--to', 'cfo@example.invalid',
                         stdout=StringIO(), stderr=StringIO())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('waiting on you', mail.outbox[0].alternatives[0][0])

    def test_it_sends_even_when_nothing_is_open(self):
        # Silence must never be mistaken for a clean day.
        mail.outbox = []
        call_command('payment_daily_digest', '--to', 'cfo@example.invalid',
                     stdout=StringIO(), stderr=StringIO())
        self.assertEqual(len(mail.outbox), 1)

    def test_the_ai_never_supplies_a_number(self):
        # The model is handed finished figures; if it hallucinates a total the
        # email must still show Omni's own arithmetic.
        self._pr('P8', PaymentRequest.Status.PENDING_CFO, '1234.56')
        mail.outbox = []
        with mock.patch('core.ai_assist.reasoning_complete',
                        return_value='Everything is fine, only 1.00 is waiting.'):
            call_command('payment_daily_digest', '--to', 'cfo@example.invalid',
                         stdout=StringIO(), stderr=StringIO())
        body = mail.outbox[0].alternatives[0][0]
        self.assertIn('1,234.56', body)      # Omni's figure is present regardless


def cls_or(self):
    return self


class PaymentSummaryIsForApproversOnlyTests(TestCase):
    """The summary returns the WHOLE payment estate — it is not a staff screen.

    Shipped on 2026-08-09 with IsAuthenticated only, on a page every accountant
    opens. Any of the seven loaders could read everyone else's requests and the
    company-wide totals across all entities, straight through the SEC-02 entity
    walls. Found by Fable the same afternoon.
    """

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user('anaccountant', 'a@example.invalid', 'x')
        cls.boss = User.objects.create_superuser('thecfo', 'c@example.invalid', 'x')

    def test_an_ordinary_accountant_is_refused(self):
        from rest_framework.test import APIClient
        c = APIClient(); c.force_authenticate(self.staff)
        r = c.get('/api/v1/payment-requests/summary/')
        self.assertEqual(r.status_code, 403, r.content[:200])

    def test_the_cfo_gets_it(self):
        from rest_framework.test import APIClient
        c = APIClient(); c.force_authenticate(self.boss)
        r = c.get('/api/v1/payment-requests/summary/')
        self.assertEqual(r.status_code, 200, r.content[:200])

    def test_the_prompt_sent_to_the_ai_is_put_through_the_pii_firewall(self):
        # It was not, and it fired on every page render, not just at 09:30.
        from unittest import mock
        from taskboard import payment_digest
        d = payment_digest.collect()
        with mock.patch('core.ai_assist.is_safe_for_ai') as safe, \
             mock.patch('core.ai_assist.reasoning_complete', return_value='ok') as send:
            safe.return_value = mock.Mock(redacted_text='SCRUBBED', redactions_made=0)
            payment_digest.narrative(d)
        self.assertTrue(safe.called, 'the prompt went out without the PII firewall')
        self.assertEqual(send.call_args[0][0], 'SCRUBBED',
                         'the UNREDACTED text was sent to the external model')


class LineLevelPaymentApiTests(TestCase):
    """The duplicate sweep needs line detail; the API returned only the envelope."""

    @classmethod
    def setUpTestData(cls):
        cls.cfo = User.objects.create_superuser('apicfo', 'apicfo@example.invalid', 'x')
        PaymentRequest.objects.create(
            ref='PAY/LINE/0001', status=PaymentRequest.Status.PENDING_CFO,
            currency='BWP', total=Decimal('13662.67'), entity='ADIC',
            payee='A Supplier', inputter='Kago', created_by=cls.cfo,
            # The SHAPE live data actually uses — claim_number / invoice_number.
            # The first version of this test used the names the code guessed, so
            # it passed while production returned empty strings (2026-08-09).
            line_items=[{'ref': 'G2026009999 SOME SUPPLIER',
                         'description': 'G2026009999 SOME SUPPLIER',
                         'claim_number': 'g2026009999', 'invoice_number': ' 60-8 ',
                         'amount': '13662.67'}])

    def _get(self, qs=''):
        from rest_framework.test import APIClient
        c = APIClient(); c.force_authenticate(self.cfo)
        return c.get('/api/v1/payment-requests/' + qs)

    def test_lines_are_absent_unless_asked_for(self):
        row = self._get('?all=1').data['requests'][0]
        self.assertNotIn('lines', row)

    def test_lines_carry_what_the_sweep_matches_on(self):
        row = [r for r in self._get('?all=1&lines=1').data['requests']
               if r['ref'] == 'PAY/LINE/0001'][0]
        ln = row['lines'][0]
        self.assertEqual(ln['claim_no'], 'G2026009999')     # uppercased
        self.assertEqual(ln['invoice_key'], '608')          # punctuation stripped
        self.assertEqual(ln['payee'], 'SOME SUPPLIER')      # claim token removed
        self.assertEqual(ln['amount'], '13662.67')

    def test_loader_and_due_date_are_returned(self):
        row = [r for r in self._get('?all=1').data['requests']
               if r['ref'] == 'PAY/LINE/0001'][0]
        self.assertEqual(row['loader'], 'Kago')
        self.assertIn('due_date', row)

    def test_since_filters_and_a_bad_value_is_a_clean_400(self):
        self.assertEqual(len(self._get('?all=1&since=2020-01-01').data['requests']), 1)
        self.assertEqual(len(self._get('?all=1&since=2030-01-01').data['requests']), 0)
        self.assertEqual(self._get('?all=1&since=rubbish').status_code, 400)

    def test_the_reports_index_no_longer_404s(self):
        from rest_framework.test import APIClient
        c = APIClient(); c.force_authenticate(self.cfo)
        r = c.get('/api/v1/reports/')
        self.assertEqual(r.status_code, 200, r.content[:150])
        self.assertIn('reports', r.data)


class PaymentDigestRecipientsTests(TestCase):
    """Who the 09:30 digest goes to.

    CFO 2026-08-12: *"I want this email to go to pako and kago, legakwa, and
    keetile also"* — widened from the CFO alone to Finance, so the people
    raising the payments see the same one email he does.

    Guarded by a test because the recipient list is the whole point of the
    feature: if it silently falls back to the CFO alone again, nobody notices —
    the email still arrives every day, just to one person.
    """

    def test_finance_are_on_the_default_distribution(self):
        from django.conf import settings
        to = [a.strip() for a in settings.PAYMENT_DIGEST_TO.split(',') if a.strip()]
        for who in (
            'pganesharajah@alphadirect.co.bw',   # CFO
            'pkago@alphadirect.co.bw',           # Pako Kago
            'ktshutlhedi@alphadirect.co.bw',     # Kago Tshutlhedi
            'lntabeni@alphadirect.co.bw',        # Legakwa Ntabeni
            'kmokhendo@alphadirect.co.bw',       # Keetile Mokhendo
        ):
            self.assertIn(who, to)

    def test_the_digest_actually_goes_to_all_of_them(self):
        mail.outbox = []
        call_command('payment_daily_digest', stdout=StringIO(), stderr=StringIO())
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(len(mail.outbox[0].to), 5)
        self.assertIn('kmokhendo@alphadirect.co.bw', mail.outbox[0].to)

    def test_an_explicit_to_still_overrides_the_setting(self):
        # The flag has to keep working for one-off resends and dry runs.
        mail.outbox = []
        call_command('payment_daily_digest', '--to', 'someone@example.invalid',
                     stdout=StringIO(), stderr=StringIO())
        self.assertEqual(mail.outbox[0].to, ['someone@example.invalid'])
