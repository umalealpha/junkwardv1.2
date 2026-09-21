"""
The two premium lines the report reads from Omni instead of a workbook.

The CFO's instruction (31-Aug-2026): Union Legal from the BONU premiums
schedule, Health from the health premium bordereaux, rather than typed in by
hand. These prove each is read by month and mapped onto the reporting period —
and that a hand-typed figure still overrides the Omni one, because someone who
types a number meant to.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from bonu.models import BonuScheduleRow, BonuScheduleSheet
from finance_report.api_views import build_finance_report
from finance_report.engine import HEALTH_INSURANCE, UNION_LEGAL
from finance_report.omni_sources import health_rows, union_legal_rows
from finance_report.test_api import PREMIUM_HEADER, upload
from healthcare.models import HealthcareUpload


class UnionLegalFromBonuTests(TestCase):

    def _schedule(self, month_amounts):
        sheet = BonuScheduleSheet.objects.create(
            key='premiums', title='Premiums',
            columns=['Invoice Month', 'Total Premium'], amount_column='Total Premium')
        for i, (month, amount) in enumerate(month_amounts):
            BonuScheduleRow.objects.create(
                sheet=sheet, position=i,
                cells={'Invoice Month': month, 'Total Premium': str(amount)})

    def test_bonu_months_map_onto_the_reporting_period(self):
        self._schedule([('Jul', 125000), ('Aug', 130000)])
        rows, meta = union_legal_rows(['2025-07', '2025-08', '2025-09'])
        by_month = {r['month']: r['amount'] for r in rows}
        self.assertEqual(by_month['2025-07'], '125000.00')
        self.assertEqual(by_month['2025-08'], '130000.00')
        self.assertEqual(meta['months_matched'], 2)
        self.assertTrue(all(r['line'] == UNION_LEGAL for r in rows))

    def test_no_schedule_is_not_an_error(self):
        rows, meta = union_legal_rows(['2025-07'])
        self.assertEqual(rows, [])
        self.assertFalse(meta['available'])


class HealthFromBordereauxTests(TestCase):

    def _revenue(self, year, month, gross):
        HealthcareUpload.objects.create(
            kind=HealthcareUpload.Kind.REVENUE,
            status=HealthcareUpload.Status.PARSED,
            file_name=f'{year}-{month}.xlsx',
            period_year=year, period_month=month, gross_amount=Decimal(str(gross)))

    def test_revenue_uploads_sum_by_month(self):
        self._revenue(2025, 7, 90000)
        self._revenue(2025, 7, 8000)     # a second upload in the same month adds
        self._revenue(2025, 8, 95000)
        rows, meta = health_rows(['2025-07', '2025-08'])
        by_month = {r['month']: r['amount'] for r in rows}
        self.assertEqual(by_month['2025-07'], '98000.00')
        self.assertEqual(by_month['2025-08'], '95000.00')
        self.assertTrue(all(r['line'] == HEALTH_INSURANCE for r in rows))

    def test_a_superseded_upload_is_left_out(self):
        self._revenue(2025, 7, 90000)
        HealthcareUpload.objects.create(
            kind=HealthcareUpload.Kind.REVENUE, status=HealthcareUpload.Status.PARSED,
            file_name='dupe.xlsx', period_year=2025, period_month=7,
            gross_amount=Decimal('50000'), superseded=True)
        rows, _ = health_rows(['2025-07'])
        self.assertEqual(rows[0]['amount'], '90000.00')


class HandEntryOverridesOmniTests(TestCase):

    def setUp(self):
        self.rf = APIRequestFactory(SERVER_NAME='omni.alphadirect.co.bw')
        self.user = get_user_model().objects.create_superuser(
            username='ov-tester', email='ov@example.com', password='x' * 24)
        BonuScheduleSheet.objects.create(
            key='premiums', title='Premiums',
            columns=['Invoice Month', 'Total Premium'], amount_column='Total Premium')
        BonuScheduleRow.objects.create(
            sheet=BonuScheduleSheet.objects.get(key='premiums'), position=0,
            cells={'Invoice Month': 'Jul', 'Total Premium': '999999'})

    def test_a_typed_union_figure_wins_over_the_bonu_schedule(self):
        premium = upload('Premium bord.xlsx', PREMIUM_HEADER,
                         [['COMG1', '2025-07-15 00:00:00', 1140, 1000, 'Motor']])
        req = self.rf.post('/api/v1/finance-report/build/',
                           {'premium_board': premium, 'months': '2025-07',
                            'union_legal': '2025-07:12345'},
                           format='multipart', secure=True)
        force_authenticate(req, user=self.user)
        body = build_finance_report(req).data
        t1 = {r['name']: r['ytd'] for r in body['premium']['tables'][0]['rows']}
        self.assertEqual(t1[UNION_LEGAL], '12345.00')          # typed, not 999,999
        self.assertNotIn('union_legal', body['omni_sources'])   # Omni skipped for it
