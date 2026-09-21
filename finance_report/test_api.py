"""
End-to-end: real .xlsx files posted at the endpoint, real report back.

The unit tests above feed the engine normalised rows, which proves the
arithmetic and nothing about the file handling. This posts workbooks written by
openpyxl through the actual view — the layer where a renamed column or a date
read as text turns a correct engine into a report full of zeros.
"""
import io
from decimal import Decimal

import openpyxl
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from finance_report.api_views import build_finance_report


def workbook(header, rows) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(list(header))
    for r in rows:
        ws.append(list(r))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def upload(name, header, rows):
    return SimpleUploadedFile(
        name, workbook(header, rows),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


PREMIUM_HEADER = ['Policy No.', 'Booking Date', 'Total Premium (BWP incl VAT)',
                  'Total Premium (BWP excl VAT)', 'Regulatory Class (NBFIRA)']
MOM_HEADER = ['POLICY NUMBER', 'Amount Collected (P)', 'Amount Collected Exc Vat',
              'Product', 'Product type', 'Paygate', 'Year/Date']
CLAIMS_HEADER = ['claim_number', 'policyNumber', 'claimType', 'productName',
                 'reportedDate', 'reserveAmt', 'regulatoryMapping', 'paymentAmt']


class FinanceReportEndpointTests(TestCase):

    def setUp(self):
        self.rf = APIRequestFactory(SERVER_NAME='omni.alphadirect.co.bw')
        self.user = get_user_model().objects.create_superuser(
            username='report-tester', email='report-tester@example.com',
            password='x' * 24)

    def post(self, **files):
        req = self.rf.post('/api/v1/finance-report/build/', files, format='multipart',
                           secure=True)
        force_authenticate(req, user=self.user)
        return build_finance_report(req)

    def test_three_workbooks_in_three_tabs_out(self):
        premium = upload('Premium bord.xlsx', PREMIUM_HEADER, [
            ['COMG2024099515', '2026-07-15 00:00:00', 1140, 1000, 'Motor'],
            ['COMG2024099516', '2026-07-20 00:00:00', 456, 400, 'Property'],
            ['DOMG2025000001', '2026-07-02 00:00:00', 684, 600, 'Motor'],
        ])
        mom = upload('Month on month.xlsx', MOM_HEADER, [
            ['MIS1', 342, 300, 'Third Party Car Insurance', 'Instant Insurance',
             'VCS', '2026-07-01 02:00:00'],
            ['MIS2', 114, 100, 'Accidental Death Insurance', 'Instant Insurance',
             'VCS', '2026-07-03 02:00:00'],
            ['MIS3', 570, 500, 'Motor Comprehensive', 'Motor Comprehensive',
             'VCS', '2026-07-04 02:00:00'],
        ])
        claims = upload('claims.xlsx', CLAIMS_HEADER, [
            ['G1', 'COMG2024099515', 'Accident Damage', 'Commercial',
             '2026-07-11 00:00:00', -500, 'Motor', -120],
            ['G2', 'MIS2020007130', 'Accident Damage', 'Motor Comprehensive',
             '2026-07-12 00:00:00', -250, 'Motor', 0],
        ])

        res = self.post(premium_board=premium, month_on_month=mom,
                        claims_as_on_date=claims,
                        union_legal='2026-07:1200', months='2026-07')
        self.assertEqual(res.status_code, 200, res.data)
        body = res.data

        # Premium: 1000 + 400 + 600 + 300 + 100 + 500 + 1200 union legal = 4100
        # (union_legal is a hand-typed override, so Omni auto-sourcing is skipped)
        t1 = body['premium']['tables'][0]
        self.assertEqual(t1['total']['ytd'], '4100.00')
        self.assertTrue(body['premium']['reconciliation']['balanced'],
                        body['premium']['reconciliation'])
        self.assertTrue(body['claims']['reserve']['reconciliation']['balanced'])
        self.assertTrue(body['claims']['paid']['reconciliation']['balanced'])

        # Motor: 1000 corporate + 600 personal + 500 motor comp + 300 third party
        t3 = {r['name']: r['ytd'] for r in body['premium']['tables'][2]['rows']}
        self.assertEqual(t3['Motor'], '2400.00')

        # The MIS claim reached Motor Comprehensive, not Corporate (reserve side).
        c1 = {r['name']: r['ytd'] for r in body['claims']['reserve']['tables'][0]['rows']}
        self.assertEqual(c1['Motor Comprehensive'], '-250.00')
        self.assertEqual(c1['Corporate Lines'], '-500.00')

        cp = {r['name']: r['ytd'] for r in body['claims']['paid']['tables'][0]['rows']}
        self.assertEqual(cp['Corporate Lines'], '-120.00')

        self.assertEqual(len(body['loss_ratio']), 3)
        self.assertEqual(body['sources']['premium_board']['rows_used'], 3)

    def test_a_renamed_premium_column_is_named_in_the_error(self):
        """A report that totals zero looks finished. This must refuse instead."""
        broken = upload('Premium bord.xlsx',
                        ['Policy No.', 'Total Premium (BWP excl VAT)'],
                        [['COMG1', 100]])
        res = self.post(premium_board=broken)
        self.assertEqual(res.status_code, 400)
        self.assertIn('booking date', res.data['detail'].lower())

    def test_a_malformed_period_is_refused_not_silently_emptied(self):
        """'2026-7' matches no month, so every table would render at zero and
        the reconciliation would call it balanced. A finished-looking report of
        nothing is the worst answer this endpoint can give."""
        premium = upload('Premium bord.xlsx', PREMIUM_HEADER,
                         [['COMG1', '2026-07-15 00:00:00', 1140, 1000, 'Motor']])
        res = self.post(premium_board=premium, months='2026-7')
        self.assertEqual(res.status_code, 400)
        self.assertIn('2026-7', res.data['detail'])

    def test_an_unreadable_file_gets_a_truthful_400_not_a_500(self):
        """A file that is neither a valid xlsx nor xlsb (calamine and the
        pure-Python fallback both fail) must come back as a clear 400 naming the
        file, not a 500 the page would show as "the server took too long"."""
        junk = SimpleUploadedFile('premium.xlsx', b'this is not a workbook at all',
                                  content_type='application/octet-stream')
        res = self.post(premium_board=junk)
        self.assertEqual(res.status_code, 400)
        self.assertIn('Premium Board', res.data['detail'])

    def test_no_files_is_refused_rather_than_returning_an_empty_report(self):
        res = self.post()
        self.assertEqual(res.status_code, 400)

    def test_reader_returns_header_rows_truncated(self):
        """The upload reader (calamine) returns the header, the data rows and a
        truncation flag. This is the layer that was too slow before — a real read
        must still come back with the right shape."""
        from finance_report.api_views import _read
        from django.core.files.uploadedfile import SimpleUploadedFile
        f = SimpleUploadedFile('x.xlsx', workbook(
            ['A', 'B'], [['1', '2'], ['3', '4']]),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        header, rows, truncated = _read(f)
        self.assertEqual([str(h) for h in header], ['A', 'B'])
        self.assertEqual(len(rows), 2)
        self.assertFalse(truncated)

    def test_a_manual_amount_in_the_wrong_shape_is_refused(self):
        premium = upload('Premium bord.xlsx', PREMIUM_HEADER,
                         [['COMG1', '2026-07-15 00:00:00', 1140, 1000, 'Motor']])
        res = self.post(premium_board=premium, union_legal='1200')  # no month
        self.assertEqual(res.status_code, 400)
        self.assertIn('month:amount', res.data['detail'])
