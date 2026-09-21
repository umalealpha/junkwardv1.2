"""The explain / request loop (CFO 2026-09-05).

Finance asks a cardholder to explain a charge; the cardholder answers on their
phone with a receipt and >=25 words; the nudge task auto-closes. Also: the photo
is never gated on the 25 words (snap now, say it later), and a receipt can only
be previewed by its owner or Finance.
"""
import datetime as dt

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APITestCase

from company_cards import services as svc
from company_cards.models import CardSpend, CompanyCard
from core.models import Company, OmniTask, UserProfile

SPENDS = '/api/v1/company-cards/spends/'
OPEN = '/api/v1/company-cards/my-open-items/'
NUDGE = '/api/v1/company-cards/nudge/'

# A real 25+ word explanation (the BICA example), and a one-liner that is not.
LONG = ('Alcohol for the Alpha Direct table at the BICA annual dinner dance, one '
        'Glenfiddich twelve year whisky and one Magic Moments vodka, bought at '
        'Sefalana for client and industry hospitality on the night.')
SHORT = 'Client lunch'


def _png(name='r.png'):
    raw = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
           b'\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00'
           b'\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82')
    return SimpleUploadedFile(name, raw, content_type='image/png')


class Base(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.co = Company.objects.create(code='CCE', name='ADIC (card explain)')
        cls.cfo = User.objects.create_user('ce_cfo', 'pganesharajah@alphadirect.co.bw', 'x')
        cls.ceo = User.objects.create_user('ce_ceo', 'aiyer@alphadirect.co.bw', 'x',
                                           first_name='Arun', last_name='Iyer')
        cls.nobody = User.objects.create_user('ce_nobody', 'nobody@alphadirect.co.bw', 'x')
        cls.acct = User.objects.create_user('ce_acct', 'acct@alphadirect.co.bw', 'x')
        UserProfile.objects.update_or_create(
            user=cls.acct,
            defaults={'title': UserProfile.Title.SENIOR_ACCOUNTANT, 'is_active': True})
        cls.cfo_card = CompanyCard.objects.create(
            label='CFO Card', last4='4821', holder=cls.cfo, company=cls.co)
        cls.ceo_card = CompanyCard.objects.create(
            label='CEO Card', last4='7733', holder=cls.ceo, company=cls.co)

    def make_spend(self, holder, card, *, what_for=SHORT, receipt=True):
        s = CardSpend.objects.create(
            card=card, uploaded_by=holder,
            spent_on=timezone.localdate() - dt.timedelta(days=1),
            merchant='Sefalana', amount='958.97', what_for=what_for)
        if receipt:
            s.receipt.save('r.png', _png(), save=True)
        return s


class TwentyFiveWordsTest(Base):
    def test_is_explained_property(self):
        self.assertTrue(self.make_spend(self.cfo, self.cfo_card, what_for=LONG,
                                        receipt=False).is_explained)
        self.assertFalse(self.make_spend(self.cfo, self.cfo_card, what_for=SHORT,
                                         receipt=False).is_explained)

    def test_explain_rejects_fewer_than_25_words(self):
        s = self.make_spend(self.ceo, self.ceo_card)
        self.client.force_authenticate(self.ceo)
        r = self.client.post(f'{SPENDS}{s.id}/explain/', {'what_for': SHORT},
                             format='multipart')
        self.assertEqual(r.status_code, 400, r.data)
        self.assertIn('25 words', r.data['detail'])

    def test_explain_accepts_25_words_and_clears_query(self):
        s = self.make_spend(self.ceo, self.ceo_card)
        s.status = CardSpend.Status.QUERIED
        s.save(update_fields=['status'])
        self.client.force_authenticate(self.ceo)
        r = self.client.post(f'{SPENDS}{s.id}/explain/', {'what_for': LONG},
                             format='multipart')
        self.assertEqual(r.status_code, 200, r.data)
        s.refresh_from_db()
        self.assertEqual(s.status, CardSpend.Status.UNCODED)
        self.assertTrue(s.is_explained)

    def test_photo_upload_is_never_gated_on_the_words(self):
        """Snap now, say it later: a short note + a photo still saves (201),
        and lands in the exec's bills-to-explain queue."""
        self.client.force_authenticate(self.ceo)
        r = self.client.post(SPENDS, {
            'card': str(self.ceo_card.id), 'amount': '958.97',
            'spent_on': (timezone.localdate() - dt.timedelta(days=1)).isoformat(),
            'what_for': SHORT, 'receipt': _png()}, format='multipart')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertFalse(r.data['is_explained'])
        self.assertEqual(len(svc.open_spends_for(self.ceo)), 1)


class TheRequestLoopTest(Base):
    def test_a_finance_query_raises_a_task_for_the_cardholder(self):
        s = self.make_spend(self.ceo, self.ceo_card)
        self.client.force_authenticate(self.acct)
        r = self.client.post(f'{SPENDS}{s.id}/code/',
                             {'queried': True, 'note': 'Who was this for?'},
                             format='json')
        self.assertEqual(r.status_code, 200, r.data)
        task = OmniTask.objects.filter(assignee=self.ceo, source='card_explain',
                                       status=OmniTask.Status.PENDING).first()
        self.assertIsNotNone(task, 'the cardholder got no bills-to-explain task')

    def test_answering_closes_the_task(self):
        s = self.make_spend(self.ceo, self.ceo_card)
        self.client.force_authenticate(self.acct)
        self.client.post(f'{SPENDS}{s.id}/code/',
                         {'queried': True, 'note': 'Who?'}, format='json')
        self.assertTrue(OmniTask.objects.filter(
            assignee=self.ceo, source='card_explain',
            status=OmniTask.Status.PENDING).exists())
        self.client.force_authenticate(self.ceo)
        self.client.post(f'{SPENDS}{s.id}/explain/', {'what_for': LONG},
                         format='multipart')
        self.assertFalse(OmniTask.objects.filter(
            assignee=self.ceo, source='card_explain',
            status=OmniTask.Status.PENDING).exists(),
            'the task should close once the exec has answered')

    def test_nudge_is_finance_only(self):
        self.make_spend(self.ceo, self.ceo_card)  # an open item exists
        self.client.force_authenticate(self.ceo)
        self.assertEqual(self.client.post(NUDGE).status_code, 403)
        self.client.force_authenticate(self.acct)
        r = self.client.post(NUDGE)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertGreaterEqual(r.data['nudged'], 1)


class OpenItemsScopingTest(Base):
    def test_i_see_only_my_own_open_items(self):
        self.make_spend(self.ceo, self.ceo_card)   # CEO short note -> open
        self.make_spend(self.cfo, self.cfo_card)   # CFO short note -> open
        self.client.force_authenticate(self.ceo)
        r = self.client.get(OPEN)
        self.assertEqual(r.status_code, 200, r.data)
        cards = {row['card_label'] for row in r.data['spends']}
        self.assertEqual(cards, {'CEO Card ••••7733'})

    def test_an_explained_uncoded_spend_is_not_open(self):
        self.make_spend(self.ceo, self.ceo_card, what_for=LONG)
        self.client.force_authenticate(self.ceo)
        r = self.client.get(OPEN)
        self.assertEqual(r.data['count'], 0)


class ReceiptPreviewAuthTest(Base):
    def test_owner_and_finance_can_preview_but_a_stranger_cannot(self):
        s = self.make_spend(self.ceo, self.ceo_card)
        url = f'{SPENDS}{s.id}/receipt/'
        self.client.force_authenticate(self.ceo)          # owner
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.force_authenticate(self.acct)         # finance
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.force_authenticate(self.cfo)          # another cardholder
        self.assertEqual(self.client.get(url).status_code, 403)
