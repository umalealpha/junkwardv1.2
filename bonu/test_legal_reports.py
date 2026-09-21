"""The two office reports (Monthly Fee Note, Quarterly Savings & Bonus Report).

Written against the ways the figures could be quietly wrong, and against the
documents failing to generate:

* the monthly in-house saving is external cost less ALL matter billing,
* every legal TASK is compared against one flat panel rate (BWP 1,900), mapped
  or not; a disbursement is compared at its own cost and carries no saving,
* the quarterly bonus is 2% of the WHOLE quarter's saving once the trigger is
  reached, and zero below it,
* every format (Word and PDF, both reports) actually builds and downloads.
"""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from bonu import legal_reports as reports
from bonu.models import (LegalAdvisoryEntry, LegalFeeNote, LegalInvoiceSaving,
                         LegalMonthlyBonus, LegalRateMapping, LegalSettings)
from core.models import Company, UserProfile

D = Decimal


def _seed():
    LegalRateMapping.objects.create(fee_description='Initial consultation',
                                    calc_basis='flat', external_rate=D('500'),
                                    internal_rate=D('200'), is_disbursement=False)
    LegalRateMapping.objects.create(fee_description='Time based',
                                    calc_basis='per_hour', external_rate=D('2250'),
                                    internal_rate=D('35'), is_disbursement=False)
    LegalFeeNote.objects.create(date=date(2026, 7, 9), client='Kabo Modise',
                                description='Initial consultation', rate=D('200'), qty=D('1'))
    LegalFeeNote.objects.create(date=date(2026, 7, 9), client='Kabo Modise',
                                description='Time based', rate=D('35'), qty=D('2'))
    LegalFeeNote.objects.create(date=date(2026, 7, 10), client='Kabo Modise',
                                description='Mystery service', rate=D('100'), qty=D('1'))
    LegalInvoiceSaving.objects.create(date_reviewed=date(2026, 7, 15), invoice_ref='INV-1',
                                      external_attorney='Firm A',
                                      original_amount=D('10000'), agreed_amount=D('6000'))
    LegalInvoiceSaving.objects.create(date_reviewed=date(2026, 8, 15), invoice_ref='INV-2',
                                      external_attorney='Firm B',
                                      original_amount=D('60000'), agreed_amount=D('10000'))
    LegalAdvisoryEntry.objects.create(date=date(2026, 7, 20), description='Board query',
                                      hours=D('3'))
    LegalMonthlyBonus.objects.create(month='2026-07', amount=D('7000'))


def _settings():
    s = LegalSettings.solo()
    s.quarterly_threshold = D('50000')
    s.quarterly_bonus_pct = D('0.02')
    s.monthly_bonus_cap = D('6500')
    s.external_hourly_rate = D('2250')
    s.save()
    return s


class FeeNoteModelTests(TestCase):
    def setUp(self):
        _seed()
        self.settings = _settings()

    def _model(self):
        return reports.feenote_model(
            '2026-07', list(LegalFeeNote.objects.all()),
            list(LegalInvoiceSaving.objects.all()), list(LegalRateMapping.objects.all()),
            LegalMonthlyBonus.objects.get(month='2026-07').amount, self.settings)

    def test_matter_total_counts_every_line(self):
        self.assertEqual(self._model()['matter_total'], D('370'))  # 200 + 70 + 100

    def test_external_total_is_the_flat_rate_for_every_task(self):
        # 3 tasks (none are disbursements), each at the flat 1,900.
        self.assertEqual(self._model()['ext_total'], D('5700'))  # 3 × 1900

    def test_in_house_saving_is_external_less_all_billing(self):
        self.assertEqual(self._model()['in_house_saving'], D('5330'))  # 5700 - 370

    def test_monthly_bonus_is_held_to_the_cap(self):
        self.assertEqual(self._model()['bonus'], D('6500'))  # 7000 entered, capped

    def test_an_unmapped_task_is_still_compared(self):
        # The old behaviour dropped an unmapped line; now it is a task at 1,900.
        row = next(r for r in self._model()['diff_rows'] if r['description'] == 'Mystery service')
        self.assertEqual(row['external'], D('1900'))
        self.assertEqual(row['saving'], D('1800'))   # 1900 - 100


class QuarterlyModelTests(TestCase):
    def setUp(self):
        _seed()
        self.settings = _settings()

    def _model(self):
        return reports.quarterly_model(
            '2026-Q3', list(LegalFeeNote.objects.all()),
            list(LegalInvoiceSaving.objects.all()), list(LegalAdvisoryEntry.objects.all()),
            list(LegalRateMapping.objects.all()), self.settings)

    def test_quarter_total_sums_the_three_months(self):
        self.assertEqual(self._model()['q_total'], D('54000'))  # 4000 + 50000

    def test_bonus_is_two_percent_once_the_trigger_is_met(self):
        m = self._model()
        self.assertTrue(m['q_met'])
        self.assertEqual(m['q_bonus'], D('1080.00'))  # 54000 * 2%

    def test_no_bonus_below_the_trigger(self):
        LegalInvoiceSaving.objects.filter(invoice_ref='INV-2').delete()  # leaves 4000 only
        m = self._model()
        self.assertFalse(m['q_met'])
        self.assertEqual(m['q_bonus'], D('0'))

    def test_illustrative_saving_compares_every_task(self):
        # 3 tasks at 1900 (ext 5700) less all billing (370) = 5330.
        self.assertEqual(self._model()['illustrative'], D('5330'))

    def test_total_delivered_adds_the_three_strands(self):
        # 54000 (contractual) + 5330 (illustrative) + 6750 (advisory 3h * 2250)
        self.assertEqual(self._model()['total_delivered'], D('66080'))


class DocumentTests(TestCase):
    def setUp(self):
        _seed()
        self.settings = _settings()

    def test_all_four_documents_generate(self):
        fees = list(LegalFeeNote.objects.all())
        savings = list(LegalInvoiceSaving.objects.all())
        adv = list(LegalAdvisoryEntry.objects.all())
        maps = list(LegalRateMapping.objects.all())
        fn = reports.feenote_model('2026-07', fees, savings, maps, D('7000'), self.settings)
        q = reports.quarterly_model('2026-Q3', fees, savings, adv, maps, self.settings)
        self.assertEqual(reports.feenote_docx(fn)[:2], b'PK')
        self.assertEqual(reports.quarterly_docx(q)[:2], b'PK')
        self.assertEqual(reports.feenote_pdf(fn)[:4], b'%PDF')
        self.assertEqual(reports.quarterly_pdf(q)[:4], b'%PDF')

    def test_quarter_summary_keeps_the_demos_three_group_rows(self):
        """The demo's Quarter Summary groups the split into Internal costs /
        External finances / Net savings. A flat table loses the 'clear split'
        the office asked for."""
        import io
        import zipfile
        q = reports.quarterly_model(
            '2026-Q3', list(LegalFeeNote.objects.all()), list(LegalInvoiceSaving.objects.all()),
            list(LegalAdvisoryEntry.objects.all()), list(LegalRateMapping.objects.all()), self.settings)
        text = zipfile.ZipFile(io.BytesIO(reports.quarterly_docx(q))).read(
            'word/document.xml').decode('utf-8', 'replace')
        for group in ('Internal costs', 'External finances (illustrative exposure)', 'Net savings'):
            self.assertIn(group, text)


class ReportEndpointTests(TestCase):
    def setUp(self):
        Company.objects.filter(code='ADIC').first() or Company.objects.create(code='ADIC', name='ADIC')
        self.user = User.objects.create_user('legalrep', email='legal.rep@example.test', password='x')
        UserProfile.objects.update_or_create(
            user=self.user, defaults={'title': UserProfile.Title.ACCOUNTANT, 'is_active': True})
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        _seed()
        _settings()

    def test_feenote_docx_downloads_as_word(self):
        r = self.client.get('/api/v1/bonu/legal/report/feenote/docx/?month=2026-07')
        self.assertEqual(r.status_code, 200)
        self.assertIn('wordprocessingml', r['Content-Type'])
        self.assertIn('attachment', r['Content-Disposition'])
        self.assertEqual(bytes(r.content)[:2], b'PK')

    def test_feenote_pdf_downloads(self):
        r = self.client.get('/api/v1/bonu/legal/report/feenote/pdf/?month=2026-07')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/pdf')
        self.assertEqual(bytes(r.content)[:4], b'%PDF')

    def test_quarterly_docx_downloads(self):
        r = self.client.get('/api/v1/bonu/legal/report/quarterly/docx/?quarter=2026-Q3')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(bytes(r.content)[:2], b'PK')

    def test_quarterly_pdf_downloads(self):
        r = self.client.get('/api/v1/bonu/legal/report/quarterly/pdf/?quarter=2026-Q3')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(bytes(r.content)[:4], b'%PDF')

    def test_unknown_report_is_404_not_a_broken_download(self):
        self.assertEqual(self.client.get('/api/v1/bonu/legal/report/feenote/xls/').status_code, 404)
        self.assertEqual(self.client.get('/api/v1/bonu/legal/report/nonsense/pdf/').status_code, 404)

    def test_a_report_route_is_not_swallowed_by_the_register_create(self):
        # 'report' must not be read as a register slug by the POST create route.
        r = self.client.get('/api/v1/bonu/legal/report/feenote/pdf/')
        self.assertEqual(r.status_code, 200)

    def test_the_screen_figure_matches_the_downloaded_report(self):
        """The dashboard's in-house saving must equal the Monthly Fee Note's, or
        the screen shows one number over a download button that produces another
        (H74, 20 Aug 2026 — the office's original 'a second calculation drifts')."""
        s = self.client.get('/api/v1/bonu/legal/?month=2026-07').json()['summary']
        model = reports.feenote_model(
            '2026-07', list(LegalFeeNote.objects.all()), list(LegalInvoiceSaving.objects.all()),
            list(LegalRateMapping.objects.all()),
            LegalMonthlyBonus.objects.get(month='2026-07').amount, LegalSettings.solo())
        self.assertEqual(D(s['in_house_saving']), model['in_house_saving'])
        self.assertEqual(D(s['in_house_saving']), D('5330'))  # 5700 − 370
