"""agent_portal/tests.py — the money rules that must never regress.

Covers the SOP §4 stream gates, the never-pay-twice cross-cycle guard (scoped to
one-shot streams), the sign-off lock, and the lines endpoint. Run in CI (needs a
DB): manage.py test agent_portal
"""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from .commission_engine import compute_row
from .models import Agent, CommissionCycle, CommissionLine
from . import service


class EngineGateTests(TestCase):
    def test_motor_flat_pays_on_success(self):
        # cols: agent,policy,holder,product,book,premium,kyc,status,source,ref,pgs,paid,pdate
        r = ['Amantle', 'MIS1', 'H', 'Motor', 'MIS', '99', 'Approved', 'Activated', 'RealPay', 'TXN', 'SUCCESS', '99', '']
        res = compute_row('motor', r)
        self.assertTrue(res['pay'])
        self.assertEqual(res['commission'], Decimal('20.00'))

    def test_motor_no_pay_when_cancelled(self):
        r = ['Amantle', 'MIS1', 'H', 'Motor', 'MIS', '99', 'Approved', 'Cancelled', 'RealPay', 'TXN', 'SUCCESS', '99', '']
        self.assertFalse(compute_row('motor', r)['pay'])

    def test_conversion_70pct_and_3month_cutoff(self):
        base = ['A', 'MIS1', 'H', 'Motor', 'MIS', '100', 'Approved', 'Activated', '', 'RealPay', 'TXN', 'SUCCESS', '100', '']
        old = base[:]; old[8] = '15/01/2026'
        res = compute_row('conversion', old, {'cutoff': date(2026, 3, 23)})
        self.assertTrue(res['pay'])
        self.assertEqual(res['commission'], Decimal('70.00'))
        new = base[:]; new[8] = '20/05/2026'
        self.assertFalse(compute_row('conversion', new, {'cutoff': date(2026, 3, 23)})['pay'])

    def test_collection_20pct_requires_equal_collected(self):
        ok = ['A', 'P', 'H', 'M', 'MIS', '158', 'Approved', 'Activated', '01/06', '996.44', 'RealPay', 'TXN', 'SUCCESS', '996.44', '']
        res = compute_row('collection', ok)
        self.assertTrue(res['pay'])
        self.assertEqual(res['commission'], Decimal('199.29'))   # 996.44 * 0.20
        bad = ok[:]; bad[13] = '900'
        self.assertFalse(compute_row('collection', bad)['pay'])

    def test_incentive_pays_approved_amount(self):
        res = compute_row('incentives', ['Lone', 'HelpDesk', 'MEMO', '5000', 'Bharath'])
        self.assertTrue(res['pay'])
        self.assertEqual(res['commission'], Decimal('5000.00'))


def _row(*cells):
    return '\t'.join(cells)


MOTOR_ROW = _row('A', 'POL9', 'H', 'Motor', 'MIS', '99', 'Approved', 'Activated', 'RealPay', 'TXN', 'SUCCESS', '99', '')


class ServiceGuardTests(TestCase):
    def _cycle(self, label, start, end):
        return CommissionCycle.objects.create(label=label, start_date=start, end_date=end)

    def test_never_pay_twice_across_cycles_motor(self):
        c1 = self._cycle('May', date(2026, 5, 1), date(2026, 5, 31))
        c2 = self._cycle('Jun', date(2026, 6, 1), date(2026, 6, 30))
        r1 = service.ingest_stream_csv(c1, 'motor', MOTOR_ROW)
        self.assertEqual(r1['approved'], 1)
        r2 = service.ingest_stream_csv(c2, 'motor', MOTOR_ROW)   # same policy POL9
        self.assertEqual(r2['approved'], 0)
        line = CommissionLine.objects.get(cycle=c2, policy_ref='POL9')
        self.assertFalse(line.payable)
        self.assertIn('never-pay-twice', line.reason)

    def test_locked_cycle_blocks_ingest(self):
        c = self._cycle('Jun', date(2026, 6, 1), date(2026, 6, 30))
        service.approve_cycle(c)
        with self.assertRaises(ValueError):
            service.ingest_stream_csv(c, 'motor', MOTOR_ROW)

    def test_approve_then_reopen(self):
        c = self._cycle('Jun', date(2026, 6, 1), date(2026, 6, 30))
        service.approve_cycle(c)
        c.refresh_from_db()
        self.assertEqual(c.status, CommissionCycle.Status.APPROVED)
        self.assertIsNotNone(c.approved_at)
        service.reopen_cycle(c)
        c.refresh_from_db()
        self.assertEqual(c.status, CommissionCycle.Status.OPEN)
        self.assertIsNone(c.approved_at)
        # reopened -> ingest allowed again
        self.assertEqual(service.ingest_stream_csv(c, 'motor', MOTOR_ROW)['approved'], 1)


NS_M1 = _row('A', 'POLNS1', 'H', 'Motor', 'MIS', '99', 'Approved', 'Activated',
             '01/05/2026', '01/05/2026', '1st', 'RealPay', 'TXN', 'SUCCESS', '99', '')
NS_M2 = _row('A', 'POLNS1', 'H', 'Motor', 'MIS', '99', 'Approved', 'Activated',
             '01/05/2026', '01/05/2026', '2nd', 'DPO', 'TXN', 'SUCCESS', '99', '')


def _coll(amt):
    return _row('A', 'POLC1', 'H', 'M', 'MIS', '99', 'Approved', 'Activated', '01/06',
                amt, 'RealPay', 'TXN', 'SUCCESS', amt, '')


HOSP = _row('A', 'POLH1', 'H', 'Hospital', 'MIS', '99', '237', '138', 'Approved',
            'Activated', 'Pay', 'RealPay', 'TXN', 'SUCCESS', '')
CONV = _row('A', 'POLV1', 'H', 'M', 'MIS', '100', 'Approved', 'Activated', '01/01/2025',
            'RealPay', 'TXN', 'SUCCESS', '100', '')


class NeverPayTwiceScopeTests(TestCase):
    """The guard must block ONE-SHOT repeats (conversion, motor) but never the
    SOP's legitimate recurrences: new-sales month 2, collection, hospital."""

    def _cycles(self):
        c1 = CommissionCycle.objects.create(label='May', start_date=date(2026, 5, 1), end_date=date(2026, 5, 31))
        c2 = CommissionCycle.objects.create(label='Jun', start_date=date(2026, 6, 1), end_date=date(2026, 6, 30))
        return c1, c2

    def test_new_sales_month2_still_pays(self):
        c1, c2 = self._cycles()
        self.assertEqual(service.ingest_stream_csv(c1, 'new_sales_mis', NS_M1)['approved'], 1)
        r = service.ingest_stream_csv(c2, 'new_sales_mis', NS_M2)
        self.assertEqual(r['approved'], 1, 'month-2 of the same policy is a legitimate second payment')
        line = CommissionLine.objects.get(cycle=c2, stream='new_sales_mis')
        self.assertEqual(line.commission, Decimal('49.50'))   # 50% DPO

    def test_new_sales_same_month_repeat_blocked(self):
        c1, c2 = self._cycles()
        service.ingest_stream_csv(c1, 'new_sales_mis', NS_M1)
        r = service.ingest_stream_csv(c2, 'new_sales_mis', NS_M1)   # 1st month AGAIN
        self.assertEqual(r['approved'], 0, 'same commission-month repeat must block')

    def test_collection_recurs_across_cycles(self):
        c1, c2 = self._cycles()
        self.assertEqual(service.ingest_stream_csv(c1, 'collection', _coll('100'))['approved'], 1)
        r = service.ingest_stream_csv(c2, 'collection', _coll('250'))
        self.assertEqual(r['approved'], 1, 'collection is recurring by nature (SOP manual check)')

    def test_hospital_recurs_across_cycles(self):
        c1, c2 = self._cycles()
        self.assertEqual(service.ingest_stream_csv(c1, 'hospital', HOSP)['approved'], 1)
        r = service.ingest_stream_csv(c2, 'hospital', HOSP)
        self.assertEqual(r['approved'], 1, 'dependant premium recurs monthly')

    def test_conversion_repeat_blocked(self):
        c1, c2 = self._cycles()
        self.assertEqual(service.ingest_stream_csv(c1, 'conversion', CONV)['approved'], 1)
        r = service.ingest_stream_csv(c2, 'conversion', CONV)
        self.assertEqual(r['approved'], 0, 'conversion is one-shot per policy')


class LinesEndpointCapTests(TestCase):
    """The lines API must not silently truncate a big cycle (June ~900 policies
    x several streams > 1000 lines) — that understates money on screen."""

    def test_no_silent_truncation(self):
        cycle = CommissionCycle.objects.create(label='Big', start_date=date(2026, 6, 1), end_date=date(2026, 6, 30))
        rows = '\n'.join(
            _row('A%d' % (i % 7), 'POL%d' % i, 'H', 'Motor', 'MIS', '99', 'Approved',
                 'Activated', 'RealPay', 'TXN', 'SUCCESS', '99', '')
            for i in range(1200)
        )
        service.ingest_stream_csv(cycle, 'motor', rows)
        self.assertEqual(CommissionLine.objects.filter(cycle=cycle).count(), 1200)
        u = User.objects.create_user('t', password='x', is_staff=True, email='pganesharajah@alphadirect.co.bw')
        cl = APIClient()
        cl.force_authenticate(u)
        resp = cl.get(f'/api/v1/agent-portal/cycles/{cycle.pk}/lines/')
        self.assertEqual(len(resp.json()), 1200, 'endpoint silently truncated the cycle')

    def test_agent_filter(self):
        cycle = CommissionCycle.objects.create(label='F', start_date=date(2026, 6, 1), end_date=date(2026, 6, 30))
        service.ingest_stream_csv(cycle, 'motor', '\n'.join([
            _row('Alpha One', 'P1', 'H', 'Motor', 'MIS', '99', 'Approved', 'Activated', 'RealPay', 'TXN', 'SUCCESS', '99', ''),
            _row('Beta Two', 'P2', 'H', 'Motor', 'MIS', '99', 'Approved', 'Activated', 'RealPay', 'TXN', 'SUCCESS', '99', ''),
        ]))
        u = User.objects.create_user('t2', password='x', email='bmhusiwa@alphadirect.co.bw')
        cl = APIClient()
        cl.force_authenticate(u)
        a = Agent.objects.get(name='Alpha One')
        resp = cl.get(f'/api/v1/agent-portal/cycles/{cycle.pk}/lines/', {'agent': str(a.pk)})
        body = resp.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]['agent_name'], 'Alpha One')


class V31StreamTests(TestCase):
    """v31 alignment: conversion split by book; Bank Confirmation Collection."""

    def test_conversion_split_same_math(self):
        row = ['A', 'MIS1', 'H', 'M', 'MIS', '100', 'Approved', 'Activated', '15/01/2026',
               'RealPay', 'TXN', 'SUCCESS', '100', '']
        for key in ('conversion_mis', 'conversion_liberty', 'conversion'):
            res = compute_row(key, row, {'cutoff': date(2026, 3, 23)})
            self.assertTrue(res['pay'], key)
            self.assertEqual(res['commission'], Decimal('70.00'), key)

    def test_bank_confirmation_is_collection_math(self):
        row = ['A', 'P', 'H', 'M', 'MIS', '99', 'Approved', 'Activated', '01/06', '500',
               'RealPay', 'TXN', 'SUCCESS', '500', '']
        res = compute_row('bank_confirmation', row)
        self.assertTrue(res['pay'])
        self.assertEqual(res['commission'], Decimal('100.00'))   # 20% of 500
        bad = row[:]; bad[13] = '499'
        self.assertFalse(compute_row('bank_confirmation', bad)['pay'])

    def test_conversion_family_never_pay_twice(self):
        c1 = CommissionCycle.objects.create(label='May', start_date=date(2026, 5, 1), end_date=date(2026, 5, 31))
        c2 = CommissionCycle.objects.create(label='Jun', start_date=date(2026, 6, 1), end_date=date(2026, 6, 30))
        conv = _row('A', 'POLV9', 'H', 'M', 'MIS', '100', 'Approved', 'Activated', '01/01/2025',
                    'RealPay', 'TXN', 'SUCCESS', '100', '')
        self.assertEqual(service.ingest_stream_csv(c1, 'conversion_mis', conv)['approved'], 1)
        # same policy re-submitted under the OTHER conversion key next cycle -> blocked (family)
        self.assertEqual(service.ingest_stream_csv(c2, 'conversion_liberty', conv)['approved'], 0)

    def test_bank_confirmation_recurs(self):
        c1 = CommissionCycle.objects.create(label='May2', start_date=date(2026, 5, 1), end_date=date(2026, 5, 31))
        c2 = CommissionCycle.objects.create(label='Jun2', start_date=date(2026, 6, 1), end_date=date(2026, 6, 30))
        mk = lambda amt: _row('A', 'POLB1', 'H', 'M', 'MIS', '99', 'Approved', 'Activated', '01/06',
                              amt, 'RealPay', 'TXN', 'SUCCESS', amt, '')
        self.assertEqual(service.ingest_stream_csv(c1, 'bank_confirmation', mk('100'))['approved'], 1)
        self.assertEqual(service.ingest_stream_csv(c2, 'bank_confirmation', mk('250'))['approved'], 1)


class SeedBanksCommandTests(TestCase):
    def test_seed_idempotent_and_skips_blank(self):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command('seed_agent_banks', '--commit', stdout=out)
        first = out.getvalue()
        self.assertIn('created=90', first)
        # '0'-account rows stay missing (coverage stays truthful)
        self.assertIn('no_account_skipped=9', first)
        from .models import AgentBankAccount
        self.assertFalse(AgentBankAccount.objects.filter(account_number__in=['0', '']).exists())
        out2 = StringIO()
        call_command('seed_agent_banks', '--commit', stdout=out2)
        self.assertIn('created=0', out2.getvalue())


class ReportSubmissionTests(TestCase):
    """Every Daily-tab upload must be kept on record (who/what/when + counts)."""

    def _cycle(self):
        return CommissionCycle.objects.create(label='Jul', start_date=date(2026, 7, 1), end_date=date(2026, 7, 31))

    def test_ingest_records_submission(self):
        from .models import ReportSubmission
        c = self._cycle()
        u = User.objects.create_user('uploader', password='x')
        service.ingest_stream_csv(c, 'motor', MOTOR_ROW, submitted_by=u,
                                  agent_name='Amantle Ntshweu', report_date=date(2026, 7, 6), source='sheet')
        sub = ReportSubmission.objects.get(cycle=c)
        self.assertEqual(sub.stream, 'motor')
        self.assertEqual(sub.agent_name, 'Amantle Ntshweu')
        self.assertEqual(sub.source, 'sheet')
        self.assertEqual(sub.rows_count, 1)
        self.assertEqual(sub.approved_count, 1)
        self.assertEqual(sub.payable_bwp, Decimal('20.00'))
        self.assertEqual(sub.submitted_by, u)
        self.assertIn('POL9', sub.raw_text)

    def test_all_policies_records_three_submissions(self):
        from .models import ReportSubmission
        c = self._cycle()
        text = _row('A', 'MIS1', 'H', 'Motor', 'MIS', '99', 'Approved', 'Activated',
                    '01/07/2026', '01/07/2026', '1st', 'RealPay', 'TXN', 'SUCCESS', '99', '')
        service.ingest_all_policies_csv(c, text, agent_name='Import run')
        subs = ReportSubmission.objects.filter(cycle=c)
        self.assertEqual(subs.count(), 3)
        self.assertTrue(all(x.source == 'all_policies' for x in subs))

    def test_submissions_endpoint(self):
        c = self._cycle()
        u = User.objects.create_user('viewer', password='x', email='bbalasubramanian@alphadirect.co.bw')
        service.ingest_stream_csv(c, 'motor', MOTOR_ROW, submitted_by=u, agent_name='A', source='paste')
        cl = APIClient()
        cl.force_authenticate(u)
        resp = cl.get(f'/api/v1/agent-portal/cycles/{c.pk}/submissions/')
        body = resp.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]['rows_count'], 1)
        self.assertEqual(body[0]['submitted_by_name'], 'viewer')


class SourceReportTests(TestCase):
    """Reports tab: control reports stored + policy-status check verdicts."""

    def _cycle(self):
        return CommissionCycle.objects.create(label='Jul', start_date=date(2026, 7, 1), end_date=date(2026, 7, 31))

    def test_store_and_status_check(self):
        c = self._cycle()
        # two payable lines: POLA active in report, POLB cancelled, POLC not in report
        for pol in ('POLA', 'POLB', 'POLC'):
            service.ingest_stream_csv(
                c, 'motor',
                _row('A', pol, 'H', 'Motor', 'MIS', '99', 'Approved', 'Activated', 'RealPay', 'T', 'SUCCESS', '99', ''),
                replace=False)
        report = '\n'.join([
            'policyNumber,policyStatus,premium',
            'POLA,Activated,99',
            'POLB,Cancelled,99',
        ])
        rep = service.add_source_report(c, 'all_policies', report)
        self.assertEqual(rep.rows_count, 3)
        out = service.policy_status_check(c)
        self.assertTrue(out['available'])
        self.assertEqual(out['ok'], 1)
        self.assertEqual(out['cancelled'], 1)
        self.assertEqual(out['not_in_report'], 1)
        verdicts = {i['policy']: i['verdict'] for i in out['issues']}
        self.assertEqual(verdicts['POLB'], 'cancelled')
        self.assertEqual(verdicts['POLC'], 'not_in_report')

    def test_premium_differs_flag(self):
        c = self._cycle()
        service.ingest_stream_csv(
            c, 'new_sales_mis',
            _row('A', 'POLP', 'H', 'M', 'MIS', '99', 'Approved', 'Activated',
                 '01/07/2026', '01/07/2026', '1st', 'RealPay', 'T', 'SUCCESS', '99', ''))
        service.add_source_report(c, 'all_policies', 'policyNumber,policyStatus,premium\nPOLP,Activated,79')
        out = service.policy_status_check(c)
        self.assertEqual(out['premium_differs'], 1)
        self.assertIn('report 79.00', out['issues'][0]['note'])

    def test_no_report_yet(self):
        c = self._cycle()
        out = service.policy_status_check(c)
        self.assertFalse(out['available'])

    def test_bad_kind_rejected(self):
        c = self._cycle()
        with self.assertRaises(ValueError):
            service.add_source_report(c, 'nonsense', 'a,b')


class CollectionExactRepeatTests(TestCase):
    """Motlatsi 2026-07-07: Collection 'not paid before' — same policy + same
    amount in another cycle is blocked; a NEW amount still pays."""

    def test_same_amount_blocked_new_amount_pays(self):
        c1 = CommissionCycle.objects.create(label='May', start_date=date(2026, 5, 1), end_date=date(2026, 5, 31))
        c2 = CommissionCycle.objects.create(label='Jun', start_date=date(2026, 6, 1), end_date=date(2026, 6, 30))
        self.assertEqual(service.ingest_stream_csv(c1, 'collection', _coll('100'))['approved'], 1)
        r_same = service.ingest_stream_csv(c2, 'collection', _coll('100'))   # exact repeat
        self.assertEqual(r_same['approved'], 0)
        line = CommissionLine.objects.get(cycle=c2, stream='collection')
        self.assertIn('never-pay-twice', line.reason)
        r_new = service.ingest_stream_csv(c2, 'collection', _coll('250'))    # new arrears
        self.assertEqual(r_new['approved'], 1)


class AccessLockTests(TestCase):
    """CFO 2026-07-07: only the five named managers may manage the portal;
    everyone else is confined to their own profile."""

    def setUp(self):
        self.mgr = User.objects.create_user('mgr', email='pganesharajah@alphadirect.co.bw', password='x')
        self.other = User.objects.create_user('other', email='someone@alphadirect.co.bw', password='x')
        self.cycle = CommissionCycle.objects.create(
            label='Jun', start_date=date(2026, 6, 1), end_date=date(2026, 6, 30))

    def test_manager_sees_cycles(self):
        c = APIClient(); c.force_authenticate(self.mgr)
        self.assertEqual(c.get('/api/v1/agent-portal/cycles/').status_code, 200)

    def test_non_manager_blocked_from_cycles(self):
        c = APIClient(); c.force_authenticate(self.other)
        self.assertEqual(c.get('/api/v1/agent-portal/cycles/').status_code, 403)

    def test_non_manager_blocked_from_ingest(self):
        c = APIClient(); c.force_authenticate(self.other)
        r = c.post(f'/api/v1/agent-portal/cycles/{self.cycle.id}/ingest/',
                   {'stream': 'motor', 'text': 'x'}, format='json')
        self.assertEqual(r.status_code, 403)

    def test_non_manager_blocked_from_agents(self):
        c = APIClient(); c.force_authenticate(self.other)
        self.assertEqual(c.get('/api/v1/agent-portal/agents/').status_code, 403)

    def test_access_endpoint_open_and_truthful(self):
        cm = APIClient(); cm.force_authenticate(self.mgr)
        self.assertTrue(cm.get('/api/v1/agent-portal/cycles/access/').json()['is_manager'])
        co = APIClient(); co.force_authenticate(self.other)
        self.assertFalse(co.get('/api/v1/agent-portal/cycles/access/').json()['is_manager'])

    def test_my_profile_open_to_non_manager(self):
        c = APIClient(); c.force_authenticate(self.other)
        r = c.get('/api/v1/agent-portal/cycles/my-profile/')
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()['matched'])   # not a matched agent -> empty, never someone else's data


class GraphiteCheckTests(TestCase):
    """Live-verification service, with the replica stubbed (no network in CI)."""

    def _line(self, cycle, agent, ref, stream='new_sales_mis', basis='99', payable=True):
        return CommissionLine.objects.create(cycle=cycle, agent=agent, stream=stream,
                                             basis=Decimal(basis), commission=Decimal('99'),
                                             payable=payable, policy_ref=ref)

    def setUp(self):
        self.cycle = CommissionCycle.objects.create(
            label='Jun', start_date=date(2026, 6, 1), end_date=date(2026, 6, 30))
        self.a = Agent.objects.create(name='Amantle')

    def test_ok_mismatch_notfound_skipped(self):
        from agent_portal import graphite_check as gc
        from datetime import datetime
        good = self._line(self.cycle, self.a, 'MIS100', basis='99')
        badp = self._line(self.cycle, self.a, 'MIS200', basis='99')     # premium differs
        inact = self._line(self.cycle, self.a, 'MIS300', basis='49')    # not active
        gone = self._line(self.cycle, self.a, 'MIS404', basis='49')     # not found
        junk = self._line(self.cycle, self.a, '1', basis='49')          # no policy number

        def fake_fetch(refs):
            pol = {
                'MIS100': {'policyNumber': 'MIS100', 'status': 1, 'premium': Decimal('99'), 'created_at': datetime(2019, 1, 1)},
                'MIS200': {'policyNumber': 'MIS200', 'status': 1, 'premium': Decimal('79'), 'created_at': datetime(2019, 1, 1)},
                'MIS300': {'policyNumber': 'MIS300', 'status': 0, 'premium': Decimal('49'), 'created_at': datetime(2019, 1, 1)},
            }
            kyc = {'MIS100': 1, 'MIS200': 1, 'MIS300': 1}
            return pol, kyc
        gc._fetch = fake_fetch
        run = gc.verify_cycle(self.cycle)
        self.assertEqual(run.ok_count, 1)
        self.assertEqual(run.mismatch_count, 2)     # premium differs + not active
        self.assertEqual(run.not_found_count, 1)
        self.assertEqual(run.skipped_count, 1)
        good.refresh_from_db(); gone.refresh_from_db(); junk.refresh_from_db()
        self.assertEqual(good.graphite_status, 'ok')
        self.assertEqual(gone.graphite_status, 'not_found')
        self.assertEqual(junk.graphite_status, 'skipped')

    def test_replica_down_marks_unavailable_never_raises(self):
        from agent_portal import graphite_check as gc
        self._line(self.cycle, self.a, 'MIS100')
        def boom(refs): raise RuntimeError('replica down')
        gc._fetch = boom
        run = gc.verify_cycle(self.cycle)   # must NOT raise
        self.assertTrue(run.unavailable)
        line = CommissionLine.objects.get(cycle=self.cycle, policy_ref='MIS100')
        self.assertEqual(line.graphite_status, 'unavailable')
