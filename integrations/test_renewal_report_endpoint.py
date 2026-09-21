"""The renewal report over HTTP — the gate, the two downloads, the refusals.

The builder is tested in `test_renewal_report.py`. This file is about the door
and the files, because a report that renders perfectly for the wrong person, or
hands over a CSV whose rows do not match the Excel, fails in a way no unit test
on the builder can see.

SERVER_NAME + secure=True mirror the other integrations tests: production
settings force an HTTPS redirect and a 301 would make every assertion vacuous.
"""
import csv
import io
from datetime import date
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from integrations import renewal_report
from integrations.renewal_report_views import COLUMNS, renewal_list

_ROW = {
    'policy_number': 'COMG2024103628', 'insured_name': 'Kalahari Trading',
    'broker_name': 'Testbrook Brokers', 'agent_name': 'A Agent',
    'product_name': 'Commercial Insurance', 'freq_code': '3', 'status_code': 1,
    'sum_insured': '', 'annual_premium': 12000,
    'anniv_from': date(2023, 10, 4), 'anniv_to': date(2024, 10, 3),
    'anniv_premium': 12000, 'newbus_from': date(2022, 10, 4),
    'newbus_to': date(2023, 10, 3),
}


class RenewalEndpointTests(TestCase):

    def setUp(self):
        self.rf = APIRequestFactory(SERVER_NAME='omni.alphadirect.co.bw')
        User = get_user_model()
        self.finance = User.objects.create_superuser(
            username='renewals-tester', email='renewals@example.com',
            password='x' * 24)
        self.plain = User.objects.create_user(
            username='plain', email='plain@alphadirect.co.bw', password='x' * 24)

    def call(self, user=None, **params):
        request = self.rf.get('/api/v1/graphite/renewals/', params, secure=True)
        force_authenticate(request, user=user or self.finance)
        with mock.patch.object(renewal_report, 'is_configured', return_value=True), \
             mock.patch.object(renewal_report, 'query', return_value=[dict(_ROW)]):
            return renewal_list(request)

    # ---- the door -------------------------------------------------------

    def test_a_signed_out_visitor_is_refused(self):
        request = self.rf.get('/api/v1/graphite/renewals/', {'month': 10}, secure=True)
        self.assertIn(renewal_list(request).status_code, (401, 403))

    def test_an_ordinary_staff_login_is_refused(self):
        """The list carries policyholder names, so it is not an all-staff screen."""
        self.assertEqual(self.call(user=self.plain, month=10).status_code, 403)

    # ---- the inputs -----------------------------------------------------

    def test_the_read_only_qc_identity_is_refused_and_never_reads(self):
        """The nightly screenshot bots are superusers.

        So the financial permission lets them in, and a scheduled run would
        capture a page of policyholder names. `graphite_search` refuses them for
        exactly this reason; the report has to as well — and the Graphite read
        must not even happen.
        """
        from core.models import UserProfile
        from core.screenshot_bot import READ_ONLY_USERNAMES
        name = sorted(READ_ONLY_USERNAMES)[0]
        bot = get_user_model().objects.create_user(
            name, email=f'{name}@example.com', password='x' * 24)
        UserProfile.objects.get_or_create(
            user=bot, defaults={'title': UserProfile.Title.ACCOUNTANT,
                                'is_active': True})
        request = self.rf.get('/api/v1/graphite/renewals/', {'month': 10},
                              secure=True)
        force_authenticate(request, user=bot)
        # is_configured is False under test settings, so without this patch the
        # "never reads" assertion would pass even with the refusal removed.
        with mock.patch.object(renewal_report, 'is_configured', return_value=True), \
             mock.patch.object(renewal_report, 'query') as read:
            response = renewal_list(request)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(read.call_count, 0)

    def test_no_month_is_a_plain_english_400(self):
        response = self.call(month='')
        self.assertEqual(response.status_code, 400)
        self.assertIn('month', response.data['error'].lower())

    def test_a_month_outside_1_to_12_is_refused(self):
        self.assertEqual(self.call(month=13).status_code, 400)

    def test_an_unknown_line_of_business_is_refused(self):
        self.assertEqual(self.call(month=10, lob='instant').status_code, 400)

    # ---- the payloads ---------------------------------------------------

    def test_json_carries_the_rows_and_the_column_headings(self):
        response = self.call(month=10)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['columns'], [h for _, h in COLUMNS])

    def test_the_csv_is_the_table_and_nothing_else(self):
        """Notes and exceptions must not be appended as trailing rows.

        They were, and it meant the row count stopped matching the policy count
        and any import choked on the trailer — while the acceptance test says
        the CSV and the Excel carry the same rows.
        """
        response = self.call(month=10, download='csv')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')
        rows = list(csv.reader(io.StringIO(response.content.decode())))
        self.assertEqual(rows[0], [h for _, h in COLUMNS])
        self.assertEqual(len(rows), 2, 'header plus exactly one policy row')
        self.assertEqual(rows[1][0], 'COMG2024103628')

    def test_the_excel_is_a_spreadsheet_named_for_the_month(self):
        response = self.call(month=10, download='xlsx')
        self.assertEqual(response.status_code, 200)
        self.assertIn('spreadsheetml', response['Content-Type'])
        self.assertIn('renewals-October.xlsx', response['Content-Disposition'])

    def test_a_download_when_graphite_is_down_fails_loudly(self):
        """An empty file would look like a month with no renewals."""
        request = self.rf.get('/api/v1/graphite/renewals/',
                              {'month': 10, 'download': 'csv'}, secure=True)
        force_authenticate(request, user=self.finance)
        with mock.patch.object(renewal_report, 'is_configured', return_value=False):
            self.assertEqual(renewal_list(request).status_code, 503)
