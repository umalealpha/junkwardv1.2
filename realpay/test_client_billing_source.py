"""Bokani, bug 6a48367f — RealPay Client Billing as the collections source.

The bug in one line: RealPay collected the money, Graphite's outcome feed never
heard about it, and the dashboard reported off Graphite. These tests pin the
three properties that make the fix trustworthy:

  1. the two-report combine (an August debit that only settles in September's
     report still counts in August, and counts ONCE);
  2. re-uploading a file cannot move a money total;
  3. the dashboard reports off the Client Billing rows when they exist, and
     falls back to Graphite — unchanged — when they do not.
"""
from __future__ import annotations

import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from realpay.billing_import import merge_rows, parse_client_billing
from realpay.models import RealPayTransaction

AUG = datetime.date(2026, 8, 31)
SEP = datetime.date(2026, 9, 3)


def _csv(*lines: str) -> bytes:
    header = ('Product,BeneficiaryNumber,ClientNumber,ContractSequence,'
              'InstallmentSequence,Tracking Start Date,AmountRequested,'
              'AmountCollected,Result\n')
    return (header + '\n'.join(lines) + '\n').encode()


class ParseClientBillingTests(TestCase):
    def test_reads_tracking_start_date_not_the_report_month(self):
        """The date that puts a row in a month is the TRACKING start date.

        Without this the September report's August debits land in September and
        August is understated — which is the whole reason Finance does the
        combine by hand.
        """
        parsed = parse_client_billing(
            _csv('RTFNBBW,24936,MIS2020003845,7,24,2026-08-01 03:58,49,49,00'),
            'billing.csv')
        self.assertEqual(parsed['used_date_column'], 'tracking start date')
        self.assertEqual(parsed['rows'][0]['tracking_start_date'],
                         datetime.date(2026, 8, 1))

    def test_falls_back_to_transaction_date_when_no_tracking_column(self):
        data = ('Product,ClientNumber,ContractSequence,InstallmentSequence,'
                'Transaction Date,AmountCollected,Result\n'
                'RTFNBBW,MIS2020003845,7,24,2026-08-01 03:58,49,00\n').encode()
        parsed = parse_client_billing(data, 'billing.csv')
        self.assertEqual(parsed['used_date_column'],
                         'transaction date (fallback)')
        self.assertEqual(parsed['rows'][0]['tracking_start_date'],
                         datetime.date(2026, 8, 1))

    def test_strips_the_stray_tab_on_a_policy_number(self):
        """Bokani's own export carries 'COM2019000027\\t'. Untrimmed, the same
        instalment keys twice and the total doubles."""
        parsed = parse_client_billing(
            _csv('RTFNBBW,24936,COM2019000027\t,3,91,2026-08-31 03:51,3422.25,3422.25,00'),
            'billing.csv')
        self.assertEqual(parsed['rows'][0]['client_number'], 'COM2019000027')

    def test_a_row_with_no_date_is_skipped_not_guessed(self):
        parsed = parse_client_billing(
            _csv('RTFNBBW,24936,MIS2020003845,7,24,,49,49,00',
                 'RTFNBBW,24936,MIS2020004192,7,22,2026-08-01,49,49,00'),
            'billing.csv')
        self.assertEqual(len(parsed['rows']), 1)
        self.assertEqual(parsed['skipped'], 1)

    def test_refuses_a_file_with_no_client_number(self):
        data = b'Product,Tracking Start Date,AmountCollected\nRTFNBBW,2026-08-01,49\n'
        with self.assertRaises(ValueError):
            parse_client_billing(data, 'billing.csv')


class TwoReportCombineTests(TestCase):
    """The August figure = August's report + September's August-dated rows."""

    def _merge(self, data: bytes, batch: str):
        return merge_rows(parse_client_billing(data, 'b.csv')['rows'], batch)

    def test_august_debit_settling_in_the_september_report_counts_in_august(self):
        # August's own report: the debit is tracked but nothing collected yet.
        self._merge(_csv(
            'RTFNBBW,24936,MIS2020003845,7,24,2026-08-01,49,0,W'), 'aug')
        # September's report carries the SAME instalment, now settled, still
        # tracked 1 August.
        self._merge(_csv(
            'RTFNBBW,24936,MIS2020003845,7,24,2026-08-01,49,49,00'), 'sep')

        rows = RealPayTransaction.objects.filter(
            source=RealPayTransaction.Source.BILLING)
        self.assertEqual(rows.count(), 1, 'the instalment must not be counted twice')
        row = rows.get()
        self.assertEqual(row.collected_amount, Decimal('49.00'))
        self.assertEqual(row.tracking_start_date, datetime.date(2026, 8, 1))

    def test_reuploading_the_same_file_does_not_move_the_total(self):
        body = _csv('RTFNBBW,24936,MIS2020003845,7,24,2026-08-01,49,49,00',
                    'RTFNBBW,24936,COM2017000229,66,67,2026-08-07,1010.58,1010.58,00')
        first = self._merge(body, 'run1')
        second = self._merge(body, 'run2')
        self.assertEqual(first['created'], 2)
        self.assertEqual(second['created'], 0)
        self.assertEqual(second['updated'], 2)
        total = sum(r.collected_amount for r in
                    RealPayTransaction.objects.filter(
                        source=RealPayTransaction.Source.BILLING))
        self.assertEqual(total, Decimal('1059.58'))

    def test_duplicate_rows_inside_one_file_are_collapsed(self):
        line = 'RTFNBBW,24936,MIS2020003845,7,24,2026-08-01,49,49,00'
        result = self._merge(_csv(line, line), 'dup')
        self.assertEqual(result['in_file_duplicates'], 1)
        self.assertEqual(result['rows_merged'], 1)
        self.assertEqual(RealPayTransaction.objects.count(), 1)

    def test_different_instalments_on_one_policy_both_count(self):
        result = self._merge(_csv(
            'RTFNBBW,24936,MIS2020003845,7,24,2026-08-01,49,49,00',
            'RTFNBBW,24936,MIS2020003845,7,25,2026-08-29,49,49,00'), 'two')
        self.assertEqual(result['created'], 2)


class DashboardSourceTests(TestCase):
    """Client Billing wins where it is loaded; Graphite stands where it is not."""

    def setUp(self):
        self.user = User.objects.create_superuser(
            'billing-src', 'billing-src@alphadirect.co.bw', 'x')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _get(self, **params):
        return self.client.get('/api/v1/realpay/collections/dashboard/', params)

    def test_dashboard_reports_the_client_billing_figure(self):
        merge_rows(parse_client_billing(_csv(
            'RTFNBBW,24936,MIS2020003845,7,24,2026-08-01,49,49,00',
            'RTFNBBW,24936,COM2017000229,66,67,2026-08-07,1010.58,1010.58,00',
        ), 'b.csv')['rows'], 'aug')

        r = self._get(start='2026-08-01', end='2026-08-31')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['source'], 'client-billing')
        self.assertEqual(Decimal(r.data['total_collected']), Decimal('1059.58'))
        groups = {g['key']: Decimal(g['collected']) for g in r.data['groups']}
        self.assertEqual(groups['INSTANT'], Decimal('49.00'))
        self.assertEqual(groups['CORPORATE'], Decimal('1010.58'))

    def test_a_month_with_no_billing_rows_does_not_claim_client_billing(self):
        merge_rows(parse_client_billing(_csv(
            'RTFNBBW,24936,MIS2020003845,7,24,2026-08-01,49,49,00',
        ), 'b.csv')['rows'], 'aug')

        r = self._get(start='2026-07-01', end='2026-07-31')
        self.assertEqual(r.status_code, 200)
        self.assertNotEqual(r.data.get('source'), 'client-billing')

    def test_rows_loaded_before_the_field_existed_are_not_silently_adopted(self):
        """Legacy billing rows carry no tracking date. Reading them as though
        they did would restate months Finance has already reported."""
        RealPayTransaction.objects.create(
            source=RealPayTransaction.Source.BILLING,
            txn_date=datetime.date(2026, 8, 5), tracking_start_date=None,
            client_number='MIS2020009999', collected_amount=Decimal('999.00'))

        r = self._get(start='2026-08-01', end='2026-08-31')
        self.assertEqual(r.status_code, 200)
        self.assertNotEqual(r.data.get('source'), 'client-billing')


class BillingUploadEndpointTests(TestCase):
    """The upload path is the ONLY writer of tracking_start_date.

    Without a route, nothing in prod ever sets the field, `use_billing` is
    always False, and the whole billing basis is dead code behind a green
    deploy. These tests exist so that can never be true again.
    """

    URL = '/api/v1/realpay/collections/billing-upload/'

    def setUp(self):
        self.user = User.objects.create_superuser(
            'billing-up', 'billing-up@alphadirect.co.bw', 'x')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _upload(self, payload: bytes, name='billing.csv'):
        return self.client.post(
            self.URL, {'file': SimpleUploadedFile(name, payload)},
            format='multipart')

    def test_an_upload_loads_the_rows_and_the_dashboard_then_uses_them(self):
        res = self._upload(_csv(
            'RTFNBBW,24936,MIS2020003845,7,24,2026-08-01,49,49,00',
            'RTFNBBW,24936,COM2017000229,66,67,2026-08-07,1010.58,1010.58,00',
        ))
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(
            RealPayTransaction.objects.filter(
                source=RealPayTransaction.Source.BILLING,
                tracking_start_date__isnull=False).count(), 2)

        dash = self.client.get('/api/v1/realpay/collections/dashboard/',
                               {'start': '2026-08-01', 'end': '2026-08-31'})
        self.assertEqual(dash.data['source'], 'client-billing')
        self.assertEqual(Decimal(dash.data['total_collected']), Decimal('1059.58'))

    def test_uploading_the_same_file_twice_does_not_double_the_month(self):
        payload = _csv('RTFNBBW,24936,MIS2020003845,7,24,2026-08-01,49,49,00')
        self.assertEqual(self._upload(payload).status_code, 200)
        self.assertEqual(self._upload(payload).status_code, 200)

        dash = self.client.get('/api/v1/realpay/collections/dashboard/',
                               {'start': '2026-08-01', 'end': '2026-08-31'})
        self.assertEqual(Decimal(dash.data['total_collected']), Decimal('49.00'))

    def test_a_file_with_no_usable_rows_is_refused_in_words(self):
        res = self._upload(b'not,a,billing,file\n1,2,3,4\n')
        self.assertEqual(res.status_code, 400)
        self.assertTrue(str(res.data['detail']).strip())

    def test_a_user_without_finance_access_cannot_upload(self):
        client = APIClient()
        client.force_authenticate(
            User.objects.create_user('nofin', 'nofin@alphadirect.co.bw', 'x'))
        res = client.post(
            self.URL,
            {'file': SimpleUploadedFile('b.csv', _csv(
                'RTFNBBW,24936,MIS2020003845,7,24,2026-08-01,49,49,00'))},
            format='multipart')
        self.assertEqual(res.status_code, 403)


class LegacyUndatedRowsAreReportedTests(TestCase):
    """A billing row with no tracking date must never just disappear.

    Once a month switches to the billing basis, a legacy row (tracking date
    NULL, txn_date in the window) is in no total at all. Not counted, not
    flagged — gone. Money silently out of a figure is the worst outcome here,
    so the dashboard has to say how many rows it left out.
    """

    def setUp(self):
        self.user = User.objects.create_superuser(
            'legacy-gap', 'legacy-gap@alphadirect.co.bw', 'x')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_a_legacy_row_in_a_billing_month_is_counted_and_explained(self):
        merge_rows(parse_client_billing(_csv(
            'RTFNBBW,24936,MIS2020003845,7,24,2026-08-01,49,49,00',
        ), 'b.csv')['rows'], 'aug')
        RealPayTransaction.objects.create(
            source=RealPayTransaction.Source.BILLING,
            txn_date=datetime.date(2026, 8, 5), tracking_start_date=None,
            client_number='MIS2020009999', collected_amount=Decimal('999.00'))

        r = self.client.get('/api/v1/realpay/collections/dashboard/',
                            {'start': '2026-08-01', 'end': '2026-08-31'})
        self.assertEqual(r.data['source'], 'client-billing')
        # The 999.00 is NOT in the total — that is deliberate — but the row is
        # accounted for rather than vanished.
        self.assertEqual(Decimal(r.data['total_collected']), Decimal('49.00'))
        self.assertEqual(r.data['legacy_undated_billing_rows'], 1)
        self.assertIn('not in this total', r.data['legacy_undated_note'])

    def test_a_clean_billing_month_reports_no_gap(self):
        merge_rows(parse_client_billing(_csv(
            'RTFNBBW,24936,MIS2020003845,7,24,2026-08-01,49,49,00',
        ), 'b.csv')['rows'], 'aug')

        r = self.client.get('/api/v1/realpay/collections/dashboard/',
                            {'start': '2026-08-01', 'end': '2026-08-31'})
        self.assertEqual(r.data['legacy_undated_billing_rows'], 0)
        self.assertIsNone(r.data['legacy_undated_note'])
