"""Budget Library (BudgetPack) API — CFO 2026-06-27: a place to keep budgets."""
import datetime as dt

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APITestCase

from core.models import Company, UserProfile
from budgets.models import BudgetPack, BudgetPackFile

PACKS = '/api/v1/budgets/packs/'


class BudgetPackApiTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.co = Company.objects.create(code='ADIC', name='Alpha Direct')
        cls.fin = User.objects.create_user('kago', 'kago@x.com', 'x')
        UserProfile.objects.create(user=cls.fin, is_administrator=True)   # _can_manage_budget
        cls.staff = User.objects.create_user('nobody', 'n@x.com', 'x')    # not finance

    def _payload(self):
        return {'fy_label': 'FY2026/27', 'company': 'ADIC',
                'period_start': '2026-07-01', 'period_end': '2027-06-30',
                'status': 'draft', 'scenario': 'Base case',
                'gwp_target': '151.6', 'ebitda': '2.9', 'pat': '1.0',
                'notes': 'GWP up 13.9%', 'source': 'Kago Tshutlhedi'}

    def test_finance_creates_pack_with_headline_figures(self):
        self.client.force_authenticate(self.fin)
        r = self.client.post(PACKS, self._payload(), format='json')
        self.assertEqual(r.status_code, 201, r.content)
        p = BudgetPack.objects.get(fy_label='FY2026/27')
        self.assertEqual(str(p.gwp_target), '151.60')
        self.assertEqual(str(p.pat), '1.00')
        self.assertEqual(p.company.code, 'ADIC')
        self.assertEqual(p.period_end, dt.date(2027, 6, 30))

    def test_non_finance_cannot_create(self):
        self.client.force_authenticate(self.staff)
        r = self.client.post(PACKS, self._payload(), format='json')
        self.assertEqual(r.status_code, 403, r.content)

    def test_list_returns_packs_and_files(self):
        p = BudgetPack.objects.create(fy_label='FY2025/26', period_start=dt.date(2025, 7, 1),
                                      period_end=dt.date(2026, 6, 30), gwp_target=133.1)
        BudgetPackFile.objects.create(pack=p, kind='model', label='Model',
                                      file=SimpleUploadedFile('m.xlsx', b'x'))
        self.client.force_authenticate(self.staff)   # reads open to any authed user
        r = self.client.get(PACKS)
        self.assertEqual(r.status_code, 200)
        packs = r.json()['packs']
        self.assertEqual(len(packs), 1)
        self.assertEqual(len(packs[0]['files']), 1)
        self.assertIn('download_url', packs[0]['files'][0])

    def test_finance_uploads_a_file(self):
        p = BudgetPack.objects.create(fy_label='FY2026/27', period_start=dt.date(2026, 7, 1),
                                      period_end=dt.date(2027, 6, 30))
        self.client.force_authenticate(self.fin)
        f = SimpleUploadedFile('FY27 Budget PROFIT.xlsx', b'binary', content_type='application/octet-stream')
        r = self.client.post(f'{PACKS}{p.id}/files/', {'file': f, 'kind': 'model', 'label': 'FY27 model'}, format='multipart')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(p.files.count(), 1)
        self.assertEqual(p.files.first().kind, 'model')


class BudgetAdvisorApiTest(APITestCase):
    """DeepSeek budget advisor — advisory only, graceful fallback (CFO 2026-06-28)."""

    @classmethod
    def setUpTestData(cls):
        cls.u = User.objects.create_user('viewer', 'v@x.com', 'x')

    def _payload(self):
        return {'levers': {'gwp': 151.55, 'lossRatio': 0.5168, 'cession': 0.7598, 'opex': 38.7},
                'outputs': {'ebitda': 5.52, 'pat': 1.01, 'nepMargin': 0.1376, 'nep': 40.08,
                            'netClaims': 18.48, 'commission': 39.05, 'grossProfit': 41.57}}

    def test_requires_auth(self):
        r = self.client.post('/api/v1/budgets/advisor/', self._payload(), format='json')
        self.assertIn(r.status_code, (401, 403), r.content)

    def test_returns_advice(self):
        # Works whether DeepSeek is reachable or not (ask() falls back on any failure).
        self.client.force_authenticate(self.u)
        r = self.client.post('/api/v1/budgets/advisor/', self._payload(), format='json')
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertTrue(body['ok'])
        self.assertGreater(len(body['text']), 10)

    def test_fallback_flags_aggressive_loss_ratio(self):
        from budgets.advisor import _fallback
        out = _fallback({'levers': {'lossRatio': 0.45, 'cession': 0.76},
                         'outputs': {'pat': 0.5, 'ebitda': 4.0, 'nepMargin': 0.1}})
        self.assertTrue(out['ok'])
        self.assertIn('loss ratio', out['text'].lower())

    def test_advisor_never_needs_a_key_to_respond(self):
        from budgets import advisor
        out = advisor.ask({'levers': {'gwp': 151.55, 'lossRatio': 0.52, 'cession': 0.76, 'opex': 38.7},
                           'outputs': {'pat': 1.0, 'ebitda': 5.5, 'nepMargin': 0.14}})
        self.assertTrue(out['ok'])
        self.assertIn('text', out)


class SpendAmountParsingTest(APITestCase):
    """A spend request's amount box is free text, so people type "15,000",
    "P 15 000", "BWP15,000.00". The old parser rejected all of these as
    "enter a valid amount", so a staff member could not submit (CFO 2026-08-24).
    """

    def test_human_typed_amounts_parse(self):
        from decimal import Decimal
        from budgets.spend_views import _dec
        self.assertEqual(_dec('15,000'), Decimal('15000.00'))
        self.assertEqual(_dec('P 15 000'), Decimal('15000.00'))
        self.assertEqual(_dec('BWP15,000.00'), Decimal('15000.00'))
        self.assertEqual(_dec('1,234.50'), Decimal('1234.50'))

    def test_plain_numbers_still_parse(self):
        from decimal import Decimal
        from budgets.spend_views import _dec
        self.assertEqual(_dec('5000'), Decimal('5000.00'))
        self.assertEqual(_dec(5000), Decimal('5000.00'))

    def test_junk_is_rejected_as_zero(self):
        from decimal import Decimal
        from budgets.spend_views import _dec
        # rejected downstream by `amount <= 0` — never created as a real amount
        self.assertEqual(_dec('abc'), Decimal('0.00'))
        self.assertEqual(_dec(''), Decimal('0.00'))
