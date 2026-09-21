"""Broker Commission C3 leftovers, C4, C5, C7 (CFO bug-board close, 19-Sep-2026).

Graphite is mocked throughout — the replica is unreachable from a test run.
Every class here was proven RED against origin/main before the change.
"""
from __future__ import annotations

import datetime as _dt
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from commissions import brokers as svc
from commissions.broker_views import BROKER_COMMISSION_PERMISSION, BROKER_COMMISSION_ROLE
from commissions.commission_calc import Rates, compute
from commissions.models import (Broker, BrokerAlias, BrokerCommissionRate, BrokerCompliance,
                                BrokerMonthClose, BrokerPayableChange, BrokerPolicy,
                                BrokerStatusHistory)
from core.models import Permission, Role, UserRoleAssignment

GF = 'realpay.graphite_feed.'
REPORTING = {'reporting': True, 'outcomes': 40, 'no_outcome': 3}
DEAD_FEED = {'reporting': False, 'outcomes': 0, 'no_outcome': 312}
HEALTHY = {'reporting': True, 'resolved': 40, 'awaiting': 3, 'window_rows': 43, 'reason': ''}
GRAPHITE_ROW = {'policy_number': 'COMG2024101598', 'insured_name': 'A Test Client',
                'agency': 'Kgare', 'premium': 100, 'annual_premium': 100}


def _full_access_user(username='rose'):
    role = Role.objects.filter(code=BROKER_COMMISSION_ROLE).first()
    if role is None:
        role = Role.objects.create(code=BROKER_COMMISSION_ROLE,
                                   name='Broker Commission - Full Access', level=3)
        perm, _ = Permission.objects.get_or_create(
            code=BROKER_COMMISSION_PERMISSION,
            defaults={'category': 'commissions', 'is_active': True})
        role.permissions.add(perm)
    user = User.objects.create_user(username, f'{username}@test.example', 'x')
    UserRoleAssignment.objects.create(user=user, role=role, justification='[C8] test')
    return user


class Base(APITestCase):

    @classmethod
    def setUpTestData(cls):
        cls.redhill = Broker.objects.create(name='Redhill')
        cls.kgare = Broker.objects.create(name='Kgare')
        cls.gone = Broker.objects.create(name='Gone Broking', is_active=False)
        BrokerAlias.objects.create(broker=cls.redhill, graphite_agency_name='Hilrange t/a Redhill')
        BrokerAlias.objects.create(broker=cls.kgare, graphite_agency_name='Kgare Insurance')
        cls.user = User.objects.create_superuser('root', 'root@test.example', 'x')
        cls.plain = User.objects.create_user('clerk', 'clerk@test.example', 'x')

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)


# ─── C3 ──────────────────────────────────────────────────────────────────────

class C3InactiveBrokersAreHidden(Base):

    @mock.patch(GF + 'broker_book', return_value={'configured': True, 'rows': []})
    def test_the_broker_list_hides_an_inactive_broker(self, _bb):
        names = [b['name'] for b in self.client.get('/api/v1/commissions/brokers/').json()['brokers']]
        self.assertIn('Redhill', names)
        self.assertNotIn('Gone Broking', names)

    @mock.patch(GF + 'broker_book', return_value={'configured': True, 'rows': []})
    def test_include_inactive_shows_it_on_purpose(self, _bb):
        r = self.client.get('/api/v1/commissions/brokers/', {'include_inactive': '1'})
        self.assertIn('Gone Broking', [b['name'] for b in r.json()['brokers']])

    @mock.patch(GF + 'policy_lookup', return_value=GRAPHITE_ROW)
    def test_a_policy_cannot_be_added_to_an_inactive_broker(self, _lk):
        r = self.client.post(f'/api/v1/commissions/brokers/{self.gone.id}/policies/',
                             {'policy_number': 'COMG2024101598'}, format='json')
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn('inactive', r.json()['detail'])
        self.assertFalse(BrokerPolicy.objects.filter(broker=self.gone).exists())


class C3GraphiteVerifiedIsStored(Base):

    def _add(self):
        return self.client.post(f'/api/v1/commissions/brokers/{self.redhill.id}/policies/',
                                {'policy_number': 'COMG2024101598'}, format='json')

    @mock.patch(GF + 'policy_lookup', return_value=GRAPHITE_ROW)
    def test_a_verified_row_is_stored_verified_with_a_time(self, _lk):
        self.assertEqual(self._add().status_code, 201)
        row = BrokerPolicy.objects.get(policy_number='COMG2024101598')
        self.assertTrue(row.graphite_verified)
        self.assertIsNotNone(row.graphite_verified_at)

    @mock.patch(GF + 'policy_lookup', return_value=None)
    def test_a_row_added_while_graphite_was_down_is_stored_unverified(self, _lk):
        self.assertEqual(self._add().status_code, 201)
        row = BrokerPolicy.objects.get(policy_number='COMG2024101598')
        self.assertFalse(row.graphite_verified)
        self.assertIsNone(row.graphite_verified_at)


class C3bUploadAndAbsorbRespectTheHolder(Base):

    SHEET = [['Policy No', 'Insured Name', 'Premium', 'Commission Payable'],
             ['COMG2024101598', 'Client', '100', '10']]

    def test_an_upload_does_not_put_another_brokers_policy_on_this_broker(self):
        BrokerPolicy.objects.create(broker=self.kgare, policy_number='comg2024101598')
        out = svc.import_workbook(None, period_label='2026-08', commit=True,
                                  _sheets=[('Redhill', self.SHEET)])
        self.assertEqual(out['rows_written'], 0)
        self.assertEqual(out['refused'][0]['held_by'], 'Kgare')
        self.assertFalse(BrokerPolicy.objects.filter(broker=self.redhill).exists())

    def test_the_preview_reports_the_refusal_too(self):
        BrokerPolicy.objects.create(broker=self.kgare, policy_number='COMG2024101598')
        out = svc.import_workbook(None, commit=False, _sheets=[('Redhill', self.SHEET)])
        self.assertEqual(len(out['refused']), 1)

    def test_two_tabs_of_one_workbook_cannot_both_claim_a_policy(self):
        out = svc.import_workbook(None, commit=True,
                                  _sheets=[('Redhill', self.SHEET), ('Kgare', self.SHEET)])
        self.assertEqual(out['rows_written'], 1)
        self.assertEqual(BrokerPolicy.objects.filter(policy_number='COMG2024101598').count(), 1)

    def test_the_same_broker_re_uploading_its_own_policy_is_fine(self):
        BrokerPolicy.objects.create(broker=self.redhill, policy_number='COMG2024101598',
                                    period_label='2026-08')
        out = svc.import_workbook(None, period_label='2026-08', commit=True,
                                  _sheets=[('Redhill', self.SHEET)])
        self.assertEqual(out['refused'], [])
        self.assertEqual(out['rows_written'], 1)

    def test_absorb_refuses_to_move_a_policy_a_third_broker_holds(self):
        third = Broker.objects.create(name='Third Broker')
        BrokerPolicy.objects.create(broker=self.kgare, policy_number='COMG2024101598')
        BrokerPolicy.objects.create(broker=third, policy_number='comg2024101598')
        r = self.client.post(f'/api/v1/commissions/brokers/{self.redhill.id}/absorb/',
                             {'other_id': str(self.kgare.id)}, format='json')
        self.assertEqual(r.status_code, 409, r.content)
        self.assertEqual(r.json()['held_by']['name'], 'Third Broker')
        self.assertTrue(Broker.objects.filter(pk=self.kgare.pk).exists(), 'nothing may move')

    def test_absorb_treats_case_variants_as_the_same_policy(self):
        BrokerPolicy.objects.create(broker=self.redhill, policy_number='COMG2024101598')
        BrokerPolicy.objects.create(broker=self.kgare, policy_number='comg2024101598')
        out = svc.absorb(self.redhill, self.kgare)
        self.assertEqual(out['rows_dropped_duplicate'], 1)
        self.assertEqual(BrokerPolicy.objects.filter(broker=self.redhill).count(), 1)


# ─── C4 ──────────────────────────────────────────────────────────────────────

def _st(code, date='2026-08-05', amount=500.0):
    return {'status': code, 'status_label': code, 'amount': amount,
            'action_date': date, 'reason': ''}


class C4StatusByBroker(Base):

    URL = '/api/v1/commissions/brokers/{}/status/'
    BOOK = [{'policy_number': p} for p in
            ('DOMG0000000001', 'DOMG0000000002', 'DOMG0000000003', 'DOMG0000000004',
             'DOMG0000000005', 'DOMG0000000006', 'DOMG0000000007', 'DOMG0000000008',
             'DOMG0000000009/2025')]
    SEEN = {'DOMG0000000001': _st('S'), 'DOMG0000000002': _st('F'),
            'DOMG0000000003': _st('E'), 'DOMG0000000004': _st('R'),
            'DOMG0000000005': _st('W'), 'DOMG0000000006': _st('A'),
            'DOMG0000000007': _st('I'), 'DOMG0000000009': _st('F')}

    def _get(self, feed=REPORTING, paid=None, period='2026-08'):
        with mock.patch('realpay.failed_debits.book_is_reporting', return_value=feed) as fb, \
             mock.patch(GF + 'broker_policies', return_value=self.BOOK), \
             mock.patch(GF + 'policy_statuses', return_value=self.SEEN) as ps, \
             mock.patch(GF + 'payments_in_window', return_value=paid or {}) as pw:
            r = self.client.get(self.URL.format(self.redhill.id), {'period': period})
        return r, fb, ps, pw

    def test_every_graphite_code_maps_to_its_status_and_absent_is_not_found(self):
        r, *_ = self._get()
        self.assertEqual(r.status_code, 200, r.content)
        got = {row['policy_number']: row['status'] for row in r.json()['rows']}
        self.assertEqual(got, {
            'DOMG0000000001': 'SUCCESSFUL', 'DOMG0000000002': 'FAILED',
            'DOMG0000000003': 'ERROR', 'DOMG0000000004': 'PROCESSING',
            'DOMG0000000005': 'PROCESSING', 'DOMG0000000006': 'NO RESULT',
            'DOMG0000000007': 'CANCELLED', 'DOMG0000000008': 'NOT FOUND',
            'DOMG0000000009': 'FAILED'})

    def test_a_failed_debit_paid_by_eft_that_month_is_eft_success(self):
        r, _, _, pw = self._get(paid={'DOMG0000000002': {'count': 1, 'amount': Decimal('0.01')},
                                      'DOMG0000000003': {'count': 1, 'amount': Decimal('900')}})
        got = {row['policy_number']: row for row in r.json()['rows']}
        self.assertEqual(got['DOMG0000000002']['status'], 'EFT SUCCESS',
                         'no amount threshold: any payment counts')
        self.assertEqual(got['DOMG0000000003']['status'], 'EFT SUCCESS')
        self.assertEqual(got['DOMG0000000003']['eft_amount'], '900.00')
        self.assertEqual(got['DOMG0000000009']['status'], 'FAILED')
        # Only FAILED / ERROR are cross-checked — never SUCCESSFUL.
        asked = set(pw.call_args[0][0])
        self.assertEqual(asked, {'DOMG0000000002', 'DOMG0000000003', 'DOMG0000000009'})

    def test_the_dead_domcom_feed_refuses_to_produce_a_result(self):
        r, fb, ps, _ = self._get(feed=DEAD_FEED)
        self.assertEqual(r.status_code, 409, r.content)
        body = r.json()
        self.assertTrue(body['refused'])
        self.assertIn('3 June', body['detail'])
        self.assertNotIn('rows', body)
        ps.assert_not_called()
        # The guard window is the debit month, ending on its last day.
        self.assertEqual(fb.call_args.kwargs, {'days': 31, 'today': _dt.date(2026, 8, 31)})

    def test_an_unreachable_graphite_is_503_not_an_answer(self):
        from realpay.failed_debits import GraphiteUnavailable
        with mock.patch('realpay.failed_debits.book_is_reporting',
                        side_effect=GraphiteUnavailable('down')):
            r = self.client.get(self.URL.format(self.redhill.id), {'period': '2026-08'})
        self.assertEqual(r.status_code, 503)
        self.assertTrue(r.json()['refused'])

    def test_someone_without_the_full_access_role_is_refused(self):
        self.client.force_authenticate(self.plain)
        r, *_ = self._get()
        self.assertEqual(r.status_code, 403)


# ─── C5 ──────────────────────────────────────────────────────────────────────

class C5CloseMonth(Base):

    URL = '/api/v1/commissions/brokers/close-month/'

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        BrokerCommissionRate.objects.create(effective_from=_dt.date(2026, 1, 1))
        cls.role_user = _full_access_user()

    def _collected(self, amount):
        return {'Hilrange t/a Redhill': {'motor': 0.0, 'non_motor': amount,
                                         'amount': amount, 'count': 1}}

    def _close(self, period, statuses, collected=1000.0, feed=REPORTING, health=HEALTHY,
               book=({'policy_number': 'DOMG0000000001'},)):
        def statuses_for(keys, start=None, end=None):
            return statuses(start) if callable(statuses) else statuses
        with mock.patch('realpay.failed_debits.book_is_reporting', return_value=feed), \
             mock.patch(GF + 'outcome_feed_health', return_value=health), \
             mock.patch(GF + 'broker_policies',
                        side_effect=lambda names, **kw: list(book)
                        if 'Hilrange t/a Redhill' in names else []), \
             mock.patch(GF + 'policy_statuses', side_effect=statuses_for), \
             mock.patch(GF + 'payments_in_window', return_value={}), \
             mock.patch(GF + 'collected_by_agency',
                        side_effect=lambda s, e: self._collected(
                            collected(s) if callable(collected) else collected)):
            return self.client.post(self.URL, {'period': period}, format='json')

    def test_close_snapshots_payable_and_records_every_status(self):
        r = self._close('2026-08', {'DOMG0000000001': _st('S')})
        self.assertEqual(r.status_code, 200, r.content)
        snap = BrokerMonthClose.objects.get(broker=self.redhill, period='2026-08')
        rates = Rates.from_model(BrokerCommissionRate.objects.get())
        self.assertEqual(snap.current_payable, compute(0, Decimal('1000'), rates).current_payable)
        h = BrokerStatusHistory.objects.get(broker=self.redhill, debit_month='2026-08')
        self.assertEqual((h.policy_number, h.status, h.source),
                         ('DOMG0000000001', 'SUCCESSFUL', 'close'))
        # Kgare has no policies but its month is closed too (payable 0.00).
        self.assertTrue(BrokerMonthClose.objects.filter(broker=self.kgare, period='2026-08').exists())
        self.assertFalse(BrokerMonthClose.objects.filter(broker=self.gone).exists(),
                         'an inactive broker is not closed')

    def test_closing_the_same_month_twice_does_nothing_the_second_time(self):
        self._close('2026-08', {'DOMG0000000001': _st('S')})
        before = (BrokerMonthClose.objects.count(), BrokerStatusHistory.objects.count())
        r = self._close('2026-08', {'DOMG0000000001': _st('F')}, collected=5000.0)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()['already_closed'])
        self.assertEqual((BrokerMonthClose.objects.count(), BrokerStatusHistory.objects.count()),
                         before)
        self.assertEqual(BrokerStatusHistory.objects.get().status, 'SUCCESSFUL')

    def test_the_dead_feed_refuses_the_close_and_writes_nothing(self):
        r = self._close('2026-08', {'DOMG0000000001': _st('S')}, feed=DEAD_FEED)
        self.assertEqual(r.status_code, 409, r.content)
        self.assertFalse(BrokerMonthClose.objects.exists())
        self.assertFalse(BrokerStatusHistory.objects.exists())

    def test_incomplete_collections_refuse_the_close(self):
        r = self._close('2026-08', {}, health={'reporting': False, 'reason': '300 of 312 awaiting'})
        self.assertEqual(r.status_code, 409)
        self.assertIn('300 of 312', r.json()['detail'])
        self.assertFalse(BrokerMonthClose.objects.exists())

    def test_a_month_that_has_not_finished_cannot_be_closed(self):
        this_month = timezone.localdate().strftime('%Y-%m')
        r = self._close(this_month, {'DOMG0000000001': _st('S')})
        self.assertEqual(r.status_code, 409)
        self.assertFalse(BrokerMonthClose.objects.exists())

    def test_late_processing_that_resolves_writes_month_success_and_logs_the_payable(self):
        july, august = _dt.date(2026, 7, 1), _dt.date(2026, 8, 1)
        # July closes with the debit still PROCESSING and P1,000 collected.
        self._close('2026-07', {'DOMG0000000001': _st('W', '2026-07-05')})
        old = BrokerMonthClose.objects.get(broker=self.redhill, period='2026-07').current_payable
        # By August's close July's debit came back SUCCESSFUL: P2,000 collected in July.
        r = self._close('2026-08',
                        lambda start: {'DOMG0000000001': _st('S', str(start))},
                        collected=lambda s: 2000.0 if s == july else 1000.0)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['late_resolved'], 1)
        late = BrokerStatusHistory.objects.get(debit_month='2026-07', source='late')
        self.assertEqual(late.status, 'JULY SUCCESS')
        snap = BrokerMonthClose.objects.get(broker=self.redhill, period='2026-07')
        rates = Rates.from_model(BrokerCommissionRate.objects.get())
        self.assertEqual(snap.current_payable, compute(0, Decimal('2000'), rates).current_payable)
        change = BrokerPayableChange.objects.get(month_close=snap)
        self.assertEqual((change.old_payable, change.new_payable), (old, snap.current_payable))
        # August's Previous Status now reads the resolved value.
        with mock.patch('realpay.failed_debits.book_is_reporting', return_value=REPORTING), \
             mock.patch(GF + 'broker_policies', return_value=[{'policy_number': 'DOMG0000000001'}]), \
             mock.patch(GF + 'policy_statuses', return_value={'DOMG0000000001': _st('S')}), \
             mock.patch(GF + 'payments_in_window', return_value={}):
            rows = self.client.get(f'/api/v1/commissions/brokers/{self.redhill.id}/status/',
                                   {'period': '2026-08'}).json()['rows']
        self.assertEqual(rows[0]['previous_status'], 'JULY SUCCESS')
        self.assertIsNotNone(august)

    def test_a_late_failure_stays_failed_and_moves_no_money(self):
        self._close('2026-07', {'DOMG0000000001': _st('W', '2026-07-05')})
        self._close('2026-08', {'DOMG0000000001': _st('F')})
        late = BrokerStatusHistory.objects.get(debit_month='2026-07', source='late')
        self.assertEqual(late.status, 'FAILED')
        self.assertFalse(BrokerPayableChange.objects.exists())

    def test_a_full_access_member_may_close(self):
        self.client.force_authenticate(self.role_user)
        self.assertEqual(self._close('2026-08', {'DOMG0000000001': _st('S')}).status_code, 200)

    def test_someone_without_the_full_access_role_may_not_close(self):
        self.client.force_authenticate(self.plain)
        r = self._close('2026-08', {'DOMG0000000001': _st('S')})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(BrokerMonthClose.objects.exists())

    def test_the_summary_previous_payable_is_the_closed_snapshot(self):
        BrokerMonthClose.objects.create(broker=self.redhill, period='2026-07',
                                        commission_excl_vat=Decimal('1'), wht=Decimal('0'),
                                        vat=Decimal('0'), current_payable=Decimal('123.45'))
        from commissions import broker_summary
        with mock.patch(GF + 'outcome_feed_health', return_value=HEALTHY), \
             mock.patch(GF + 'collected_by_agency', return_value=self._collected(1000.0)):
            out = broker_summary.build('2026-08')
        row = next(r for r in out['rows'] if r['broker'] == 'Redhill')
        self.assertEqual(row['previous_payable'], 123.45)
        self.assertEqual(row['previous_payable_source'], 'closed')
        self.assertTrue(out['preview'])
        self.assertFalse(out['signed_off'])


# ─── C7 ──────────────────────────────────────────────────────────────────────

class C7Compliance(Base):

    def _set(self, text='Compliant', period='2026-08', broker=None):
        return self.client.post(
            f'/api/v1/commissions/brokers/{(broker or self.redhill).id}/compliance/',
            {'period': period, 'compliance': text}, format='json')

    def test_a_full_access_member_sets_it_and_the_summary_shows_it(self):
        self.client.force_authenticate(_full_access_user())
        r = self._set('Tax clearance on file')
        self.assertEqual(r.status_code, 200, r.content)
        from commissions import broker_summary
        with mock.patch(GF + 'outcome_feed_health', return_value=HEALTHY), \
             mock.patch(GF + 'collected_by_agency', return_value={}):
            out = broker_summary.build('2026-08')
        row = next(x for x in out['rows'] if x['broker'] == 'Redhill')
        self.assertEqual(row['compliance'], 'Tax clearance on file')
        self.assertTrue(out['preview'], 'the PREVIEW label must stay until Finance signs off')

    def test_editing_it_again_replaces_it(self):
        self._set('A'); self._set('B')
        self.assertEqual(BrokerCompliance.objects.get(broker=self.redhill).compliance, 'B')

    def test_a_signed_in_user_without_the_role_cannot_edit_it(self):
        self.client.force_authenticate(self.plain)
        self.assertEqual(self._set().status_code, 403)
        self.assertFalse(BrokerCompliance.objects.exists())

    def test_a_commission_reviewer_without_the_role_cannot_edit_it(self):
        reviewer = User.objects.create_user('tchimidza', 'tchimidza@alphadirect.co.bw', 'x',
                                            first_name='Tlamelo', last_name='Chimidza')
        from commissions.access import is_reviewer
        self.assertTrue(is_reviewer(reviewer), 'fixture must genuinely be a reviewer')
        self.client.force_authenticate(reviewer)
        self.assertEqual(self._set().status_code, 403)
        self.assertFalse(BrokerCompliance.objects.exists())

    def test_a_bad_month_is_refused(self):
        self.assertEqual(self._set(period='2026-8').status_code, 400)


class C4PaymentsInWindow(APITestCase):
    """The EFT read itself: an unread ledger must never look like "no payment"."""

    def test_not_configured_is_none_not_empty(self):
        from realpay import graphite_feed
        with mock.patch('integrations.graphite_ro.is_configured', return_value=False):
            self.assertIsNone(graphite_feed.payments_in_window(['DOMG1'], _dt.date(2026, 8, 1),
                                                               _dt.date(2026, 9, 1)))

    def test_a_query_failure_raises(self):
        from realpay import graphite_feed
        with mock.patch('integrations.graphite_ro.is_configured', return_value=True), \
             mock.patch('integrations.graphite_ro.query', side_effect=RuntimeError('down')):
            with self.assertRaises(RuntimeError):
                graphite_feed.payments_in_window(['DOMG1'], _dt.date(2026, 8, 1),
                                                 _dt.date(2026, 9, 1))

    def test_rows_come_back_as_decimal_money_keyed_by_policy(self):
        from realpay import graphite_feed
        with mock.patch('integrations.graphite_ro.is_configured', return_value=True), \
             mock.patch('integrations.graphite_ro.query',
                        return_value=[{'pn': 'DOMG1 ', 'n': 2, 'amt': '150.50'}]) as q:
            out = graphite_feed.payments_in_window(['DOMG1/2025'], _dt.date(2026, 8, 1),
                                                   _dt.date(2026, 9, 1))
        self.assertEqual(out, {'DOMG1': {'count': 2, 'amount': Decimal('150.50')}})
        sql, params = q.call_args[0][0], q.call_args[0][1]
        self.assertIn('payment_transactions', sql)
        self.assertIn('DOMG1', params)
        self.assertIn('2026-08-01', params)
        self.assertIn('2026-09-01', params)
