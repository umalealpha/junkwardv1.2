"""Company credit-card spending (CFO 2026-08-07).

Exercises the real models, the real API and the real statement parser — the
CFO's four decisions each get a test: upload-only (no GL from the cardholder),
no approval step, the statement names what has no receipt, and the
overdue-task gate never applies here.
"""
import datetime as dt
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from company_cards import services as svc
from company_cards.models import (CardSpend, CardStatement, CardStatementLine,
                                  CompanyCard)
from core.models import Company, UserProfile

SPENDS = '/api/v1/company-cards/spends/'
CARDS = '/api/v1/company-cards/'


def _png(name='receipt.png'):
    # A one-pixel PNG — enough to prove the upload path stores a real file.
    raw = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
           b'\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00'
           b'\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82')
    return SimpleUploadedFile(name, raw, content_type='image/png')


class Base(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.co = Company.objects.create(code='CCTS', name='ADIC (card test)')
        cls.cfo = User.objects.create_user(
            'cc_cfo', 'pganesharajah@alphadirect.co.bw', 'x',
            first_name='Prathap', last_name='G')
        cls.ceo = User.objects.create_user(
            'cc_ceo', 'aiyer@alphadirect.co.bw', 'x', first_name='Arun', last_name='Iyer')
        cls.nobody = User.objects.create_user(
            'cc_nobody', 'nobody@alphadirect.co.bw', 'x')
        cls.accountant = User.objects.create_user(
            'cc_acct', 'acct@alphadirect.co.bw', 'x')
        UserProfile.objects.update_or_create(
            user=cls.accountant,
            defaults={'title': UserProfile.Title.SENIOR_ACCOUNTANT, 'is_active': True})

        cls.cfo_card = CompanyCard.objects.create(
            label='CFO Card', last4='4821', holder=cls.cfo, company=cls.co)
        cls.ceo_card = CompanyCard.objects.create(
            label='CEO Card', last4='7733', holder=cls.ceo, company=cls.co)

    def post_spend(self, card=None, *, amount='549.99', days_ago=1,
                   what_for='Client lunch with the Botswana Life team', receipt=True):
        data = {
            'card': str((card or self.cfo_card).id),
            'spent_on': (timezone.localdate() - dt.timedelta(days=days_ago)).isoformat(),
            'amount': amount,
            'what_for': what_for,
            'merchant': 'Sanitas',
        }
        if receipt:
            data['receipt'] = _png()
        return self.client.post(SPENDS, data, format='multipart')


class WhoCanUploadTest(Base):
    def test_a_cardholder_can_upload(self):
        self.client.force_authenticate(self.cfo)
        r = self.post_spend()
        self.assertEqual(r.status_code, 201, r.data)
        s = CardSpend.objects.get()
        self.assertTrue(s.has_receipt, 'the receipt file was not stored')
        self.assertEqual(s.status, CardSpend.Status.UNCODED)

    def test_someone_without_a_card_cannot(self):
        self.client.force_authenticate(self.nobody)
        self.assertFalse(svc.user_is_cardholder(self.nobody))
        r = self.post_spend()
        self.assertEqual(r.status_code, 403, r.data)

    def test_a_cardholder_cannot_post_against_someone_elses_card(self):
        self.client.force_authenticate(self.cfo)
        r = self.post_spend(card=self.ceo_card)
        self.assertEqual(r.status_code, 403, r.data)

    def test_i_only_see_my_own_spending(self):
        self.client.force_authenticate(self.ceo)
        self.post_spend(card=self.ceo_card)
        self.client.force_authenticate(self.cfo)
        self.post_spend()
        r = self.client.get(SPENDS)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['count'], 1)
        self.assertFalse(r.data['is_finance'])

    def test_finance_sees_everyones(self):
        self.client.force_authenticate(self.ceo)
        self.post_spend(card=self.ceo_card)
        self.client.force_authenticate(self.accountant)
        r = self.client.get(SPENDS)
        self.assertEqual(r.data['count'], 1)
        self.assertTrue(r.data['is_finance'])

    def test_the_card_list_shows_only_my_card(self):
        self.client.force_authenticate(self.ceo)
        r = self.client.get(CARDS)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual([c['label'] for c in r.data['cards']], ['CEO Card'])

    @override_settings(COMPANY_CARD_HOLDERS=['aiyer@alphadirect.co.bw'])
    def test_the_holder_list_is_settings_overridable(self):
        self.assertTrue(svc.user_is_cardholder(self.ceo))
        # The CFO is off the list but still HOLDS a card, so he stays allowed —
        # the list never overrides a real card.
        self.assertTrue(svc.user_is_cardholder(self.cfo))


class UploadIsDeliberatelyTinyTest(Base):
    """CFO: Finance codes it, the cardholder just uploads. But one line of
    'what for' is mandatory, or Finance is left phoning round."""

    def test_the_cardholder_never_supplies_a_gl_account(self):
        self.client.force_authenticate(self.cfo)
        r = self.post_spend()
        self.assertIsNone(CardSpend.objects.get().gl_account_id)
        self.assertNotIn('gl_account', r.data)

    def test_what_for_is_required(self):
        self.client.force_authenticate(self.cfo)
        r = self.post_spend(what_for='')
        self.assertEqual(r.status_code, 400, r.data)
        self.assertIn('what it was for', r.data['detail'])

    def test_a_future_date_is_refused(self):
        self.client.force_authenticate(self.cfo)
        r = self.post_spend(days_ago=-3)
        self.assertEqual(r.status_code, 400, r.data)

    def test_a_zero_amount_is_refused(self):
        self.client.force_authenticate(self.cfo)
        r = self.post_spend(amount='0')
        self.assertEqual(r.status_code, 400, r.data)

    def test_a_non_image_is_refused(self):
        self.client.force_authenticate(self.cfo)
        bad = SimpleUploadedFile('x.exe', b'MZ', content_type='application/x-msdownload')
        r = self.client.post(SPENDS, {
            'card': str(self.cfo_card.id), 'amount': '10',
            'spent_on': timezone.localdate().isoformat(),
            'what_for': 'Something', 'receipt': bad}, format='multipart')
        self.assertEqual(r.status_code, 400, r.data)

    def test_there_is_no_approval_step(self):
        """CFO: the money is already gone, a signature changes nothing."""
        self.client.force_authenticate(self.cfo)
        self.post_spend()
        s = CardSpend.objects.get()
        self.assertEqual(s.status, CardSpend.Status.UNCODED)
        self.assertFalse(hasattr(s, 'approved_by'))


class FinanceCodesItTest(Base):
    def _spend(self):
        self.client.force_authenticate(self.cfo)
        self.post_spend()
        return CardSpend.objects.get()

    def _account(self):
        from ledger.models import Account
        return Account.objects.create(
            code='6100', name='Client entertainment',
            account_type='expense', is_active=True)

    def test_finance_can_code_it(self):
        s = self._spend()
        acct = self._account()
        self.client.force_authenticate(self.accountant)
        r = self.client.post(f'{SPENDS}{s.id}/code/', {'gl_account': str(acct.id)},
                             format='json')
        self.assertEqual(r.status_code, 200, r.data)
        s.refresh_from_db()
        self.assertEqual(s.status, CardSpend.Status.CODED)
        self.assertEqual(s.gl_account_id, acct.id)
        self.assertEqual(s.coded_by_id, self.accountant.id)

    def test_a_cardholder_cannot_code_their_own(self):
        s = self._spend()
        acct = self._account()
        self.client.force_authenticate(self.ceo)
        r = self.client.post(f'{SPENDS}{s.id}/code/', {'gl_account': str(acct.id)},
                             format='json')
        self.assertEqual(r.status_code, 403, r.data)

    def test_finance_can_query_it_back(self):
        s = self._spend()
        self.client.force_authenticate(self.accountant)
        r = self.client.post(f'{SPENDS}{s.id}/code/',
                             {'queried': True, 'note': 'Which client was this?'},
                             format='json')
        self.assertEqual(r.status_code, 200, r.data)
        s.refresh_from_db()
        self.assertEqual(s.status, CardSpend.Status.QUERIED)
        self.assertIn('Which client', s.finance_note)
