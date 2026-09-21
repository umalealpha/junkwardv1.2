"""equity/tests.py — cap-table/ESOP register, self-service, vesting.

Runs on Postgres (fabe-pg / CI). Each test that guards a rule is written so it
would fail if that rule regressed (access gate, holder-count scoping, per-user
isolation, vesting maths).
"""
from __future__ import annotations

import datetime

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from payroll.models import Employee
from equity.models import EsopGrant, ShareHolding, Stakeholder, VestingTranche
from equity import services
from equity.api_views import build_capital_story, _my_equity_data

User = get_user_model()
TODAY = datetime.date.today()


class RegisterAccessTests(APITestCase):
    def setUp(self):
        self.viewer = User.objects.create_user('fin', 'finance@alphadirect.co.bw', 'x', is_staff=True)
        self.outsider = User.objects.create_user('joe', 'joe@alphadirect.co.bw', 'x')

    def test_outsider_blocked(self):
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get('/api/v1/equity/stakeholders/').status_code, 403)
        self.assertEqual(
            self.client.post('/api/v1/equity/stakeholders/', {'name': 'X'}, format='json').status_code, 403)

    def test_viewer_can_crud(self):
        self.client.force_authenticate(self.viewer)
        r = self.client.post('/api/v1/equity/stakeholders/',
                             {'name': 'Acme Ventures', 'kind': 'entity'}, format='json')
        self.assertEqual(r.status_code, 201)
        sid = r.json()['id']
        r = self.client.post('/api/v1/equity/holdings/',
                             {'stakeholder': sid, 'klass': 'ORA', 'shares': 1000, 'usd_invested': '500.00'},
                             format='json')
        self.assertEqual(r.status_code, 201)
        self.assertTrue(ShareHolding.objects.filter(stakeholder_id=sid, shares=1000).exists())


class CapitalStoryPayloadTests(APITestCase):
    def setUp(self):
        # Start from a clean register — migration 0002 seeds the real 35-holder
        # book, which these counting tests deliberately don't want.
        Stakeholder.objects.all().delete()

    def test_holder_count_excludes_former_and_empty(self):
        # two current holders WITH shares → counted
        for n in ('A', 'B'):
            s = Stakeholder.objects.create(name=n, is_current=True)
            ShareHolding.objects.create(stakeholder=s, klass='ORB', shares=100)
        # a former holder with shares → NOT counted
        f = Stakeholder.objects.create(name='Gone', is_current=False)
        ShareHolding.objects.create(stakeholder=f, klass='ORB', shares=100)
        # a current holder with nothing → NOT counted
        Stakeholder.objects.create(name='Empty', is_current=True)

        p = build_capital_story()
        self.assertEqual(p['register_source'], 'live-db')
        # The fee-tier count excludes the former holder and the empty one → 2.
        self.assertEqual(p['security_holder_count'], 2)
        # But their SHARES stay on the cap table until formally bought back
        # (Finance deletes the holding then). A soft flag must never silently
        # drop real issued shares from the ownership total. So 3 rows / 300.
        self.assertEqual(len(p['cap_table']), 3)
        self.assertEqual(p['cap_table_total']['shares'], 300)

    def test_option_grant_worth_and_counts(self):
        s = Stakeholder.objects.create(name='Holder')
        EsopGrant.objects.create(stakeholder=s, units=1000, status='active')
        p = build_capital_story()
        g = [g for g in p['esop']['grants'] if g['grantee'] == 'Holder'][0]
        price = services.current_share_price_usd()
        self.assertAlmostEqual(g['worth_usd'], round(1000 * price, 2), places=2)
        self.assertEqual(p['esop']['option_holders'], 1)


class MyEquityTests(APITestCase):
    def setUp(self):
        Stakeholder.objects.all().delete()
        self.ua = User.objects.create_user('ua', 'a@alphadirect.co.bw', 'x')
        self.ub = User.objects.create_user('ub', 'b@alphadirect.co.bw', 'x')
        self.ea = Employee.objects.create(full_name='Alice A', user=self.ua, employee_number='EMP-A')
        self.eb = Employee.objects.create(full_name='Bob B', user=self.ub, employee_number='EMP-B')
        self.sa = Stakeholder.objects.create(name='Alice A', employee=self.ea)
        self.sb = Stakeholder.objects.create(name='Bob B', employee=self.eb)
        EsopGrant.objects.create(stakeholder=self.sa, units=500, status='active')
        EsopGrant.objects.create(stakeholder=self.sb, units=999, status='active')

    def test_isolation_only_own_equity(self):
        self.client.force_authenticate(self.ua)
        r = self.client.get('/api/v1/equity/my-equity/')
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data['has_equity'])
        self.assertEqual(data['holder'], 'Alice A')
        self.assertEqual(len(data['grants']), 1)
        self.assertEqual(data['grants'][0]['units'], 500)  # would be 999 if it leaked Bob's

    def test_email_link_when_no_staff_record(self):
        u = User.objects.create_user('ext', 'investor@example.com', 'x')
        Stakeholder.objects.create(name='Investor', email='investor@example.com')
        data = _my_equity_data(u)
        self.assertIsNotNone(data)
        self.assertEqual(data['holder'], 'Investor')

    def test_unmatched_login_is_plain_empty(self):
        u = User.objects.create_user('nobody', 'nobody@alphadirect.co.bw', 'x')
        self.client.force_authenticate(u)
        r = self.client.get('/api/v1/equity/my-equity/')
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()['has_equity'])

    def test_statement_pdf(self):
        self.client.force_authenticate(self.ua)
        r = self.client.get('/api/v1/equity/my-equity/statement.pdf')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/pdf')
        self.assertTrue(r.content[:4] == b'%PDF')

    def test_statement_pdf_404_when_unmatched(self):
        u = User.objects.create_user('nb2', 'nb2@alphadirect.co.bw', 'x')
        self.client.force_authenticate(u)
        self.assertEqual(self.client.get('/api/v1/equity/my-equity/statement.pdf').status_code, 404)


class VestingTests(APITestCase):
    def setUp(self):
        Stakeholder.objects.all().delete()
        self.s = Stakeholder.objects.create(name='V')
        self.g = EsopGrant.objects.create(stakeholder=self.s, units=1200,
                                          grant_date=datetime.date(2020, 1, 1), status='active')

    def test_no_schedule_reports_none(self):
        vs = services.vesting_summary(self.g)
        self.assertFalse(vs['has_schedule'])
        self.assertIsNone(vs['pct_vested'])

    def test_scheduled_split(self):
        VestingTranche.objects.create(grant=self.g, vest_date=TODAY - datetime.timedelta(days=1), units=300)
        VestingTranche.objects.create(grant=self.g, vest_date=TODAY + datetime.timedelta(days=30), units=900)
        vs = services.vesting_summary(self.g)
        self.assertTrue(vs['has_schedule'])
        self.assertEqual(vs['vested_units'], 300)
        self.assertEqual(vs['unvested_units'], 900)
        self.assertEqual(vs['pct_vested'], 25.0)
        self.assertIsNotNone(vs['next_vest_date'])

    def test_generate_sums_to_units(self):
        tr = services.generate_standard_tranches(self.g)
        self.assertTrue(tr)
        self.assertEqual(sum(t['units'] for t in tr), self.g.units)

    def test_apply_schedule_endpoint(self):
        viewer = User.objects.create_user('fin2', 'finance2@alphadirect.co.bw', 'x', is_staff=True)
        self.client.force_authenticate(viewer)
        r = self.client.post(f'/api/v1/equity/grants/{self.g.id}/apply-schedule/',
                             {'tranches': [{'vest_date': '2021-01-01', 'units': 600},
                                           {'vest_date': '2022-01-01', 'units': 600}]}, format='json')
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.g.tranches.count(), 2)

    def test_apply_schedule_blocked_for_outsider(self):
        u = User.objects.create_user('out', 'out@alphadirect.co.bw', 'x')
        self.client.force_authenticate(u)
        r = self.client.post(f'/api/v1/equity/grants/{self.g.id}/apply-schedule/',
                             {'tranches': []}, format='json')
        self.assertEqual(r.status_code, 403)
