"""The phone flow: one sentence in, the right document out (CFO 2026-09-04).

    POST /api/v1/underwriting/parse-text/   plain English -> {doctype, fields | draft}
    POST /api/v1/underwriting/quotes/{id}/email/   send an ISSUED quotation's PDF

Every test goes through the URL layer. The doctype router, the money and the
signatory are the three things a phone user cannot see and must never get wrong.
"""
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.core import mail
from django.test import override_settings
from rest_framework.test import APITestCase

from core.models import Company
from underwriting.models import Quote


PARSE = '/api/v1/underwriting/parse-text/'

# The tool's own defaults (templates/underwriting/tool.html) — the ONLY source
# allowed for the signatory on a cover note. Never the text, never a model.
SIGNATORY = {'cn_signame': 'Gaolebale S. Machobane',
             'cn_sigphone': '+267 3702714',
             'cn_sigemail': 'gmachobane@alphadirect.co.bw'}

LABELLED_CN = (
    'Cover note\n'
    'Policy Number: COMG2026001234\n'
    'Insured: Thabo Motors (Pty) Ltd\n'
    'Address: Plot 12, Broadhurst, Gaborone\n'
    'Class of cover: Motor Comprehensive\n'
    'Sum insured: P320,000\n'
    'Period: 2026-09-04 to 2026-10-03\n'
)


def _fake_draft(text):
    """draft_quote with the model call replaced — the MONEY is still price()."""
    from underwriting.quote_parse import price
    return {'ok': True, 'source': 'Aria',
            'draft': {'client_name': 'Kgalagadi Builders', 'client_attn': '',
                      'class_of_business': 'Motor Fleet', 'period': '12 months',
                      'broker': '', 'sections': [
                          {'group': '', 'name': 'Motor Fleet', 'note': '',
                           'sum_insured': '1,800,000', 'basis': 'Retail value', 'excess': '7,500'}]},
            'money': price('96400'), 'premium_is_suggested': False,
            'premium_basis': '', 'warnings': []}


class ParseTextRoutesEachDoctypeTests(APITestCase):

    def setUp(self):
        self.company = Company.objects.create(code='PTX', name='Parse Text Co')
        self.user = User.objects.create_user('uw_parse', password='x',
                                             is_superuser=True, is_staff=True)
        self.client.force_authenticate(self.user)

    def _post(self, text, **extra):
        body = {'text': text}
        body.update(extra)
        return self.client.post(PARSE, body, format='json')

    def test_anonymous_is_refused(self):
        self.client.force_authenticate(None)
        r = self.client.post(PARSE, {'text': LABELLED_CN}, format='json')
        self.assertIn(r.status_code, (401, 403), r.content[:200])

    def test_too_long_is_a_400_not_a_model_call(self):
        with mock.patch('underwriting.reader._map_text') as ai, \
                mock.patch('underwriting.api_views.draft_quote') as dq:
            r = self._post('cover note ' * 1000)
        self.assertEqual(r.status_code, 400, r.content[:200])
        ai.assert_not_called()
        dq.assert_not_called()

    def test_empty_text_is_a_400(self):
        r = self._post('   ')
        self.assertEqual(r.status_code, 400, r.content[:200])

    def test_labelled_cover_note_is_read_by_the_free_tier_without_ai(self):
        with mock.patch('underwriting.reader._map_text') as ai:
            r = self._post(LABELLED_CN)
        self.assertEqual(r.status_code, 200, r.content[:300])
        ai.assert_not_called()
        self.assertTrue(r.data['ok'])
        self.assertEqual(r.data['doctype'], 'cn')
        self.assertEqual(r.data['via'], 'rules')
        f = r.data['fields']
        self.assertEqual(f['cn_policy'], 'COMG2026001234')
        self.assertEqual(f['cn_client'], 'Thabo Motors (Pty) Ltd')
        self.assertEqual(f['cn_sum'], '320,000')
        self.assertEqual(f['cn_from'], '2026-09-04')
        self.assertEqual(f['cn_to'], '2026-10-03')

    def test_signatory_always_comes_from_the_tool_defaults(self):
        # Even when the sentence names somebody else as the signatory.
        text = LABELLED_CN + 'Signatory: Some Broker Person, broker@example.com\n'
        with mock.patch('underwriting.reader._map_text') as ai:
            r = self._post(text)
        ai.assert_not_called()
        for k, v in SIGNATORY.items():
            self.assertEqual(r.data['fields'][k], v)

    def test_auto_detects_a_wca_sentence_and_falls_to_ai_when_rules_are_thin(self):
        text = ('WCA certificate for Kgalagadi Builders, 24 employees, annual '
                'earnings 1.9m, 1 Oct 2026 to 30 Sep 2027')
        ai_fields = {'w_policy': '', 'w_agency': '', 'w_name': 'Kgalagadi Builders',
                     'w_addr': '', 'w_from': '2026-10-01', 'w_to': '2027-09-30',
                     'w_date': '', 'w_emp': '24', 'w_earn': '1,900,000'}
        with mock.patch('underwriting.reader._map_text',
                        return_value=({'doctype': 'wca', 'fields': ai_fields}, 'deepseek')) as ai:
            r = self._post(text)
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertEqual(r.data['doctype'], 'wca')
        ai.assert_called_once()
        self.assertEqual(r.data['via'], 'deepseek')
        self.assertEqual(r.data['fields']['w_name'], 'Kgalagadi Builders')
        self.assertEqual(r.data['fields']['w_earn'], '1,900,000')
        self.assertNotIn('cn_signame', r.data['fields'])

    def test_ai_cannot_change_the_doctype_the_sentence_named(self):
        # The user said WCA; a model answering "cn" must not flip the document.
        text = 'WCA certificate for Kgalagadi Builders, 24 employees, earnings 1.9m'
        with mock.patch('underwriting.reader._map_text',
                        return_value=({'doctype': 'cn', 'fields': {'cn_client': 'X'}}, 'deepseek')):
            r = self._post(text)
        self.assertEqual(r.data['doctype'], 'wca')
        self.assertFalse(r.data['ok'])
        self.assertTrue(r.data['warnings'])

    def test_auto_detects_a_financed_cover_note(self):
        text = ('Cover note for Naledi Dube, Toyota Hilux B123ABC financed by '
                'Stanbic Bank, value 320k, 4 Sep 2026 to 3 Oct 2026')
        ai_fields = {'cn_client': 'Naledi Dube', 'fi_reg': 'B123ABC', 'fi_make': 'Toyota Hilux',
                     'fi_value': '320,000', 'fi_bank': 'Stanbic Bank',
                     'cn_from': '2026-09-04', 'cn_to': '2026-10-03'}
        with mock.patch('underwriting.reader._map_text',
                        return_value=({'doctype': 'cnfi', 'fields': ai_fields}, 'gemini')):
            r = self._post(text)
        self.assertEqual(r.data['doctype'], 'cnfi')
        self.assertEqual(r.data['fields']['fi_bank'], 'Stanbic Bank')
        for k, v in SIGNATORY.items():
            self.assertEqual(r.data['fields'][k], v)

    def test_auto_detects_a_quote_and_the_money_is_computed_not_modelled(self):
        text = 'Quote: fleet of 6 bakkies for Kgalagadi Builders, premium 96,400'
        with mock.patch('underwriting.api_views.draft_quote', side_effect=_fake_draft) as dq, \
                mock.patch('underwriting.reader._map_text') as ai:
            r = self._post(text)
        self.assertEqual(r.status_code, 200, r.content[:300])
        dq.assert_called_once_with(text)
        ai.assert_not_called()
        self.assertEqual(r.data['doctype'], 'quote')
        self.assertEqual(r.data['draft']['client_name'], 'Kgalagadi Builders')
        # price('96400') — VAT 14%, half-up. Never from the model, never from the phone.
        self.assertEqual(r.data['premium'], '96400.00')
        self.assertEqual(r.data['vat'], '13496.00')
        self.assertEqual(r.data['total'], '109896.00')
        self.assertFalse(r.data['premium_is_suggested'])

    def test_an_explicit_doctype_is_respected_over_the_keywords(self):
        # Words say "quote"; the chip says cover note. The chip wins.
        text = 'Quote a cover note for Thabo Motors premium 500'
        with mock.patch('underwriting.api_views.draft_quote') as dq, \
                mock.patch('underwriting.reader._map_text', return_value=(None, '')):
            r = self._post(text, doctype='cn')
        dq.assert_not_called()
        self.assertEqual(r.data['doctype'], 'cn')

    def test_an_unknown_doctype_is_a_400(self):
        r = self._post(LABELLED_CN, doctype='invoice')
        self.assertEqual(r.status_code, 400, r.content[:200])

    def test_a_sentence_with_no_clue_asks_for_the_doctype(self):
        with mock.patch('underwriting.api_views.draft_quote') as dq, \
                mock.patch('underwriting.reader._map_text') as ai:
            r = self._post('Thabo Motors, Plot 12 Broadhurst, from 4 Sep to 3 Oct, 320k')
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertFalse(r.data['ok'])
        self.assertTrue(r.data['needs_doctype'])
        self.assertEqual(set(r.data['options']), {'quote', 'cn', 'cnfi', 'wca'})
        dq.assert_not_called()
        ai.assert_not_called()

    def test_when_every_tier_fails_the_screen_gets_the_blank_form_not_a_500(self):
        text = 'WCA certificate for Kgalagadi Builders'
        with mock.patch('underwriting.reader._map_text', return_value=(None, '')):
            r = self._post(text)
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertFalse(r.data['ok'])
        self.assertEqual(r.data['doctype'], 'wca')
        self.assertIn('w_name', r.data['fields'])
        self.assertTrue(r.data['warnings'])


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   NOTIFICATIONS_ENABLED=True)
class QuoteEmailTests(APITestCase):

    def setUp(self):
        self.company = Company.objects.create(code='QEM', name='Quote Email Co')
        self.user = User.objects.create_user('uw_email', password='x',
                                             is_superuser=True, is_staff=True)
        self.client.force_authenticate(self.user)
        self.quote = Quote.objects.create(client_name='Kgalagadi Builders',
                                          premium=Decimal('96400'), company=self.company)

    def _email(self, quote, to):
        return self.client.post(f'/api/v1/underwriting/quotes/{quote.id}/email/',
                                {'to': to}, format='json')

    def _issue(self):
        self.quote.issue(self.user)
        self.quote.pdf_bytes, self.quote.pdf_size = b'%PDF-1.4 fake', 13
        self.quote.save()

    def test_a_draft_cannot_be_emailed(self):
        r = self._email(self.quote, 'client@example.com')
        self.assertEqual(r.status_code, 409, r.content[:300])
        self.assertEqual(len(mail.outbox), 0)

    def test_a_bad_address_is_a_400(self):
        self._issue()
        r = self._email(self.quote, 'not-an-email')
        self.assertEqual(r.status_code, 400, r.content[:300])
        self.assertEqual(len(mail.outbox), 0)

    def test_an_issued_quote_is_emailed_with_the_pdf_and_stamped(self):
        self._issue()
        r = self._email(self.quote, 'client@example.com')
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertTrue(r.data['emailed'])
        self.assertEqual(r.data['to'], 'client@example.com')
        self.assertEqual(len(mail.outbox), 1)
        m = mail.outbox[0]
        self.assertEqual(m.to, ['client@example.com'])
        # Customer mail: never the internal EXCO copy, never the do-not-reply banner.
        self.assertEqual(m.cc, [])
        self.assertIn(self.quote.quote_number, m.subject)
        self.assertEqual(len(m.attachments), 1)
        self.assertEqual(m.attachments[0][0], f'Quotation-{self.quote.quote_number}.pdf')
        self.assertEqual(m.attachments[0][2], 'application/pdf')
        html = m.alternatives[0][0]
        self.assertNotIn('do not reply', html.lower())
        self.quote.refresh_from_db()
        self.assertEqual(self.quote.emailed_to, 'client@example.com')
        self.assertIsNotNone(self.quote.emailed_at)

    def test_a_mailer_failure_is_a_502_and_nothing_is_stamped(self):
        self._issue()
        with mock.patch('core.notifications.EmailMultiAlternatives.send',
                        side_effect=RuntimeError('smtp down')):
            r = self._email(self.quote, 'client@example.com')
        self.assertEqual(r.status_code, 502, r.content[:300])
        self.quote.refresh_from_db()
        self.assertEqual(self.quote.emailed_to, '')
        self.assertIsNone(self.quote.emailed_at)
        self.assertEqual(self.quote.status, Quote.Status.ISSUED)

    def test_another_entitys_quote_is_a_404(self):
        from core.models import UserCompanyAccess
        self._issue()
        theirs = Company.objects.create(code='QEB', name='Other Entity')
        walled = User.objects.create_user('uw_email_walled', password='x', is_staff=True)
        UserCompanyAccess.objects.create(user=walled, company=theirs)
        self.client.force_authenticate(walled)
        r = self._email(self.quote, 'client@example.com')
        self.assertEqual(r.status_code, 404, r.content[:300])
        self.assertEqual(len(mail.outbox), 0)


class CoverFamilyFoldTests(APITestCase):
    """The model reads a vehicle cover note as 'cnfi'; the caller asked for 'cn'.
    The read must be kept and folded, never thrown away for the regex tier."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        self.u = get_user_model().objects.create_user('fold.person', password='x')
        self.client.force_authenticate(self.u)

    def test_cnfi_read_is_folded_into_a_plain_cover_note(self):
        from unittest import mock
        ai = {'doctype': 'cnfi', 'fields': {
            'cn_client': 'Sample Motors (Pty) Ltd', 'cn_policy': 'COMG2026009999',
            'cn_from': '2026-09-04', 'cn_to': '2026-10-03',
            'fi_class': 'Motor Comprehensive', 'fi_reg': 'B999XYZ', 'fi_make': 'Toyota Hilux', 'fi_value': '320000'}}
        with mock.patch('underwriting.reader._map_text', return_value=(ai, 'deepseek')), \
             mock.patch('underwriting.extract_rules.rule_extract',
                        return_value={'ok': False, 'doctype': 'cn', 'fields': {}, 'confidence': 0.2, 'via': 'rules'}):
            r = self.client.post('/api/v1/underwriting/parse-text/',
                                 {'text': 'Cover note for Sample Motors, Toyota Hilux B999XYZ, 320000, 4 Sep to 3 Oct', 'doctype': 'cn'},
                                 format='json')
        self.assertEqual(r.status_code, 200, r.content[:300])
        b = r.json()
        self.assertTrue(b['ok'], b)
        self.assertEqual(b['doctype'], 'cn')
        self.assertEqual(b['via'], 'deepseek')
        f = b['fields']
        self.assertEqual(f['cn_client'], 'Sample Motors (Pty) Ltd')
        self.assertEqual(f['cn_class'], 'Motor Comprehensive')
        self.assertEqual(f['cn_sum'], '320000')
        self.assertIn('B999XYZ', f['cn_risk'])
        self.assertEqual(f['cn_from'], '2026-09-04')
        self.assertNotIn('fi_reg', f)
        self.assertEqual(f['cn_signame'], 'Gaolebale S. Machobane')
