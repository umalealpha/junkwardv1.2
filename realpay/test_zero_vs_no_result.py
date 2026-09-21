"""A zero and a blank are not the same fact.

The Collections Dashboard printed "BWP 0.00" for Corporate and Personal lines
all through the RealPay->Graphite outcome outage that began in June 2026 —
telling Finance the corporate book collected nothing, when the truth was that
nobody had told us what happened. Debits WERE raised: 1,226 in August alone.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient


def _url():
    from django.urls import reverse
    return reverse('v1-realpay-collections-dashboard')


#: One prefix collecting normally, one raised-but-silent — the real September shape.
LIVE = {'configured': True, 'rows': [
    {'pref': 'MIS',  'attempts': 6382, 'collected_lines': 2279,
     'awaiting_result': 0,    'collected_amount': 156678.32},
    {'pref': 'COMG', 'attempts': 0,    'collected_lines': 0,
     'awaiting_result': 1226, 'collected_amount': 0.0},
]}


class ZeroVersusNoResultTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.boss = User.objects.create_superuser('zz_zvr', 'zz_zvr@x.co', 'x')
        self.api = APIClient()
        self.api.force_authenticate(self.boss)

    def _get(self):
        with patch('realpay.graphite_feed.collections_by_group', return_value=LIVE):
            return self.api.get(_url())

    def test_a_raised_but_silent_group_is_flagged_not_reported_as_zero(self):
        r = self._get()
        self.assertEqual(r.status_code, 200)
        by = {g['key']: g for g in r.data['groups']}
        corp = by['CORPORATE']
        self.assertTrue(corp['no_result_received'],
                        'debits were raised and nothing came back — that is not a zero')
        self.assertEqual(corp['awaiting_result'], 1226)

    def test_a_group_that_really_collected_is_NOT_flagged(self):
        """The guard has to fail in both directions, or it is just decoration."""
        by = {g['key']: g for g in self._get().data['groups']}
        self.assertFalse(by['INSTANT']['no_result_received'])
        self.assertEqual(by['INSTANT']['collected_lines'], 2279)

    def test_a_genuinely_empty_group_is_NOT_flagged_as_a_missing_feed(self):
        """No debits raised and none collected really is zero — say zero."""
        empty = {'configured': True, 'rows': [
            {'pref': 'MIS', 'attempts': 10, 'collected_lines': 10,
             'awaiting_result': 0, 'collected_amount': 100.0},
            {'pref': 'DOMG', 'attempts': 0, 'collected_lines': 0,
             'awaiting_result': 0, 'collected_amount': 0.0},
        ]}
        with patch('realpay.graphite_feed.collections_by_group', return_value=empty):
            r = self.api.get(_url())
        by = {g['key']: g for g in r.data['groups']}
        self.assertFalse(by['PERSONAL']['no_result_received'])

    def test_the_page_is_told_which_groups_are_blind_and_how_many_debits(self):
        d = self._get().data
        self.assertEqual(d['groups_with_no_result'], ['Corporate lines'])
        self.assertEqual(d['awaiting_result_total'], 1226)
