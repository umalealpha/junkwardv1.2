"""Reconciliation Hub — Phase 1 tests.

Covers: metric->posted GL math, variance/tolerance classification, ageing bucket
alignment, a full run (matched / breach / partial), Graphite-offline degradation,
the no-PII persistence guarantee, and API permission gating.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APITestCase, APIClient

from core.models import Currency, Company, UserProfile
from ledger.models import Account, JournalEntry, JournalEntryLine

from reconciliation_hub import services
from reconciliation_hub.constants import (
    MetricKey, FlowType, Unit, SourceSystem, LineStatus, RunStatus,
    CANONICAL_BUCKETS,
)
from reconciliation_hub.models import (
    MetricSourceMap, SourceFigure, ReconciliationRun,
    ReconciliationLine, AgeingTieOut,
)


FAKE_GRAPHITE = {
    'source': 'fake graphite replica',
    'summary': {
        'total_balance': 1000.00,
        'policy_count': 12,
        'buckets': {'0-30': 400.0, '31-60': 300.0, '61-90': 200.0, '120+': 100.0},
    },
    'rows': [],
}


def _base_data(cls):
    Currency.objects.get_or_create(code='BWP', defaults={'name': 'Pula', 'symbol': 'P'})
    cls.user = User.objects.create_user(username='recon_tester')
    cur = Currency.objects.get(code='BWP')
    cls.adic = Company.objects.create(code='ADIC', name='ADIC Ltd', base_currency=cur)

    cls.gwp = Account.objects.create(code='4100', name='GWP', account_type='revenue',
                                     sub_type='operating_revenue')
    cls.ar = Account.objects.create(code='1210', name='Premium Receivable',
                                    account_type='asset', sub_type='current_asset',
                                    is_receivable=True)
    cls.claims = Account.objects.create(code='5100', name='Claims', account_type='expense',
                                        sub_type='cost_of_insurance')
    cls.bank = Account.objects.create(code='1110', name='Bank', account_type='asset',
                                      sub_type='bank', is_bank_account=True)


def _post(cls, entry_date, lines, company=None):
    je = JournalEntry.objects.create(
        entry_date=entry_date,
        description='recon test',
        journal_type=JournalEntry.JournalType.GENERAL,
        status=JournalEntry.Status.POSTED,
        currency_code_id='BWP',
        created_by=cls.user,
        company=company or cls.adic,
        is_related_party=False,
    )
    for account, debit, credit in lines:
        d, c = Decimal(str(debit)), Decimal(str(credit))
        JournalEntryLine.objects.create(
            journal_entry=je, account=account,
            debit_amount=d, credit_amount=c, debit_bwp=d, credit_bwp=c,
        )
    return je


class PostedMathTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _base_data(cls)
        cls.gwp_map = MetricSourceMap.objects.create(
            metric_key=MetricKey.GWP, label='GWP', unit=Unit.BWP,
            flow_type=FlowType.PERIOD, source_system=SourceSystem.REGISTER)
        cls.gwp_map.accounts.set([cls.gwp])
        cls.deb_map = MetricSourceMap.objects.create(
            metric_key=MetricKey.PREMIUM_DEBTORS, label='Debtors', unit=Unit.BWP,
            flow_type=FlowType.BALANCE, include_all_receivable=True,
            source_system=SourceSystem.GRAPHITE_RDS)

    def test_gwp_period_is_credit_signed(self):
        _post(self, date(2026, 5, 15), [(self.bank, 1000, 0), (self.gwp, 0, 1000)])
        val = services.posted_for_metric(self.gwp_map, date(2026, 5, 1),
                                         date(2026, 5, 31), self.adic.id)
        self.assertEqual(val, Decimal('1000.00'))

    def test_period_excludes_out_of_range(self):
        _post(self, date(2026, 6, 15), [(self.bank, 500, 0), (self.gwp, 0, 500)])
        val = services.posted_for_metric(self.gwp_map, date(2026, 5, 1),
                                         date(2026, 5, 31), self.adic.id)
        self.assertEqual(val, Decimal('0.00'))

    def test_debtors_balance_cumulative(self):
        _post(self, date(2026, 4, 1), [(self.ar, 300, 0), (self.bank, 0, 300)])
        _post(self, date(2026, 5, 10), [(self.ar, 200, 0), (self.bank, 0, 200)])
        val = services.posted_for_metric(self.deb_map, None,
                                         date(2026, 5, 31), self.adic.id)
        self.assertEqual(val, Decimal('500.00'))  # asset Dr - Cr, cumulative

    def test_company_scoped(self):
        other = Company.objects.create(code='OTHER', name='Other',
                                       base_currency=Currency.objects.get(code='BWP'))
        _post(self, date(2026, 5, 15), [(self.bank, 999, 0), (self.gwp, 0, 999)], company=other)
        val = services.posted_for_metric(self.gwp_map, date(2026, 5, 1),
                                         date(2026, 5, 31), self.adic.id)
        self.assertEqual(val, Decimal('0.00'))  # other company excluded


class ClassifyTests(TestCase):
    def test_within_tolerance_matched(self):
        st, var, pct = services.classify(Decimal('1000'), Decimal('1040'), Decimal('5'))
        self.assertEqual(st, LineStatus.MATCHED)
        self.assertEqual(var, Decimal('40.00'))
        self.assertEqual(pct, Decimal('4.0000'))

    def test_beyond_tolerance_breach(self):
        st, var, pct = services.classify(Decimal('1000'), Decimal('1200'), Decimal('5'))
        self.assertEqual(st, LineStatus.BREACH)
        self.assertEqual(pct, Decimal('20.0000'))

    def test_no_source(self):
        st, var, pct = services.classify(None, Decimal('1000'), Decimal('5'))
        self.assertEqual(st, LineStatus.NO_SOURCE)
        self.assertIsNone(var)

    def test_no_omni(self):
        st, var, pct = services.classify(Decimal('1000'), None, Decimal('5'))
        self.assertEqual(st, LineStatus.NO_OMNI)

    def test_zero_source_nonzero_posted_is_breach(self):
        st, var, pct = services.classify(Decimal('0'), Decimal('50'), Decimal('5'))
        self.assertEqual(st, LineStatus.BREACH)
        self.assertEqual(pct, Decimal('100.0000'))

    def test_zero_both_matched(self):
        st, var, pct = services.classify(Decimal('0'), Decimal('0'), Decimal('5'))
        self.assertEqual(st, LineStatus.MATCHED)


class RunTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _base_data(cls)
        m = MetricSourceMap.objects.create(
            metric_key=MetricKey.GWP, label='GWP', unit=Unit.BWP,
            flow_type=FlowType.PERIOD, source_system=SourceSystem.REGISTER)
        m.accounts.set([cls.gwp])

    def _seed_source(self, value):
        SourceFigure.objects.create(
            company=self.adic, period_label='2026-05', period_end=date(2026, 5, 31),
            metric_key=MetricKey.GWP, source_system=SourceSystem.REGISTER,
            source_value=Decimal(str(value)))

    @patch.object(services, 'build_graphite_age_analysis', return_value=FAKE_GRAPHITE)
    def test_run_matched(self, _mock):
        _post(self, date(2026, 5, 15), [(self.bank, 1000, 0), (self.gwp, 0, 1000)])
        self._seed_source(1000)
        run = services.run_reconciliation(
            period_label='2026-05', period_end=date(2026, 5, 31),
            period_start=date(2026, 5, 1), company=self.adic, user=self.user)
        line = run.lines.get(metric_key=MetricKey.GWP)
        self.assertEqual(line.status, LineStatus.MATCHED)
        self.assertEqual(line.omni_posted, Decimal('1000.00'))
        self.assertEqual(line.variance, Decimal('0.00'))

    @patch.object(services, 'build_graphite_age_analysis', return_value=FAKE_GRAPHITE)
    def test_run_breach(self, _mock):
        _post(self, date(2026, 5, 15), [(self.bank, 1200, 0), (self.gwp, 0, 1200)])
        self._seed_source(1000)
        run = services.run_reconciliation(
            period_label='2026-05', period_end=date(2026, 5, 31),
            period_start=date(2026, 5, 1), company=self.adic, user=self.user)
        line = run.lines.get(metric_key=MetricKey.GWP)
        self.assertEqual(line.status, LineStatus.BREACH)
        self.assertEqual(line.variance_pct, Decimal('20.0000'))

    @patch.object(services, 'build_graphite_age_analysis', return_value=FAKE_GRAPHITE)
    def test_run_no_source_is_partial(self, _mock):
        _post(self, date(2026, 5, 15), [(self.bank, 1000, 0), (self.gwp, 0, 1000)])
        run = services.run_reconciliation(
            period_label='2026-05', period_end=date(2026, 5, 31),
            period_start=date(2026, 5, 1), company=self.adic, user=self.user)
        self.assertEqual(run.status, RunStatus.PARTIAL)
        self.assertEqual(run.lines.get(metric_key=MetricKey.GWP).status, LineStatus.NO_SOURCE)

    @patch.object(services, 'build_graphite_age_analysis', side_effect=RuntimeError('replica down'))
    def test_graphite_offline_degrades(self, _mock):
        _post(self, date(2026, 5, 15), [(self.bank, 1000, 0), (self.gwp, 0, 1000)])
        self._seed_source(1000)
        # Should not raise despite Graphite being down.
        run = services.run_reconciliation(
            period_label='2026-05', period_end=date(2026, 5, 31),
            period_start=date(2026, 5, 1), company=self.adic, user=self.user)
        self.assertEqual(run.lines.get(metric_key=MetricKey.GWP).status, LineStatus.MATCHED)


class AgeingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _base_data(cls)
        MetricSourceMap.objects.create(
            metric_key=MetricKey.PREMIUM_DEBTORS, label='Debtors', unit=Unit.BWP,
            flow_type=FlowType.BALANCE, include_all_receivable=True,
            source_system=SourceSystem.GRAPHITE_RDS)

    @patch.object(services, 'build_ar_aging')
    @patch.object(services, 'build_graphite_age_analysis', return_value=FAKE_GRAPHITE)
    def test_ageing_buckets_and_total(self, _g, _ar):
        _ar.return_value = {'as_of': '2025-01-31', 'currency_code': 'BWP', 'customers': [],
                            'totals': {'current': '400.00', 'days_31_60': '300.00',
                                       'days_61_90': '200.00', 'days_91_120': '50.00',
                                       'over_120': '50.00', 'total_outstanding': '1000.00'},
                            'buckets': []}
        run = services.run_reconciliation(
            period_label='2026-05', period_end=date(2026, 5, 31),
            company=self.adic, user=self.user)
        rows = {a.bucket: a for a in run.ageing_tieouts.all()}
        # canonical buckets + total row present
        for b in CANONICAL_BUCKETS + ['total']:
            self.assertIn(b, rows)
        # omni 91-120 + over-120 fold into 90+
        self.assertEqual(rows['90+'].omni_total, Decimal('100.00'))
        self.assertEqual(rows['0-30'].graphite_total, Decimal('400.00'))
        self.assertEqual(rows['total'].graphite_total, Decimal('1000.00'))
        self.assertEqual(rows['total'].omni_total, Decimal('1000.00'))
        self.assertEqual(rows['total'].status, LineStatus.MATCHED)


class NoPIITests(TestCase):
    """The hub persists totals + counts only — never policyholder identity."""
    FORBIDDEN = ('first_name', 'firstname', 'last_name', 'lastname', 'client_name',
                 'customer_name', 'email', 'cellphone', 'phone', 'dob',
                 'date_of_birth', 'address', 'id_number', 'omang', 'national_id',
                 'policy_number', 'policyholder', 'account_number')

    def test_no_identity_fields_on_persisted_models(self):
        for model in (SourceFigure, ReconciliationRun, ReconciliationLine, AgeingTieOut):
            names = {f.name.lower() for f in model._meta.get_fields()}
            leaked = names & set(self.FORBIDDEN)
            self.assertEqual(leaked, set(),
                             f'{model.__name__} must not persist PII fields: {leaked}')


class ApiPermissionTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        _base_data(cls)

    def test_dashboard_requires_auth(self):
        client = APIClient()
        resp = client.get('/api/v1/recon/dashboard/')
        self.assertIn(resp.status_code, (401, 403))

    def test_dashboard_ok_for_finance(self):
        cfo = User.objects.create_user(username='recon_cfo')
        UserProfile.objects.update_or_create(
            user=cfo, defaults={'title': UserProfile.Title.CFO,
                                'role': UserProfile.Role.FINANCE_ADMIN,
                                'is_administrator': True, 'is_active': True})
        client = APIClient()
        client.force_authenticate(user=cfo)
        resp = client.get('/api/v1/recon/dashboard/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('rows', resp.data)
        self.assertIn('scope_note', resp.data)
