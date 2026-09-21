"""
reporting/test_ma_pl.py — MA P&L correctness tests.

Two test classes:
  1. MAPLFormulaTests — synthetic JEs, asserts build_ma_pl arithmetic.
     Runs in any environment, must pass on every CI build.
  2. FrozenNumbersSmokeTest — runs against live data, asserts the FROZEN
     NUMBERS for ADIC FY25 and FY26-9M per CFO directive 2026-05-16.
     Skipped unless FROZEN_NUMBERS_DB=1 is set in the environment, because
     it requires a database with the Odoo backfill complete. Currently
     blocked by the un-run `migrate_odoo --commit` (P0 item on the
     handover).
"""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal

from unittest import skipUnless

from django.contrib.auth.models import User
from django.test import TestCase

from core.models import Currency
from ledger.models import Account, FiscalPeriod, JournalEntry, JournalEntryLine
from reporting.ma_pl import build_ma_pl
from reporting.reports import build_management_pack


# ---------------------------------------------------------------------------
# 1. Formula tests — synthetic data, arithmetic correctness
# ---------------------------------------------------------------------------

class MAPLFormulaTests(TestCase):
    """
    Verifies build_ma_pl arithmetic on a minimal set of synthetic JEs.

    Uses Odoo-style 6-digit codes that match ma_pl_spec MA_LINES exactly.
    The numbers are chosen so subtotals are easy to eyeball.
    """

    @classmethod
    def setUpTestData(cls):
        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'},
        )
        cls.user = User.objects.create_user(username='ma_pl_tester')

        # Open fiscal periods covering the synthetic JE dates
        for ym in [(2024, 12), (2025, 1), (2025, 2)]:
            FiscalPeriod.objects.create(
                period_name=f'{ym[0]}-{ym[1]:02d}',
                start_date=date(ym[0], ym[1], 1),
                end_date=date(ym[0], ym[1], 28),
            )

        # Counterparty accounts — anything Dr/Cr counter, type doesn't matter
        # for the MA P&L formulas (only the 100xxx-coded lines do).
        cls.bank    = Account.objects.create(
            code='110000', name='Bank',  account_type='asset',
            sub_type='bank', is_bank_account=True,
        )
        cls.payable = Account.objects.create(
            code='200000', name='Payable', account_type='liability',
            sub_type='current_liability',
        )

        # MA P&L accounts referenced in MA_LINES (subset — only what the
        # synthetic JEs below touch). Using one representative code per
        # line; the spec sums all codes in each line so this is sufficient.
        cls.gwp           = Account.objects.create(code='100001', name='GWP — Motor',     account_type='revenue', sub_type='operating_revenue')
        cls.ceded         = Account.objects.create(code='101000', name='RI Premium Ceded',account_type='revenue', sub_type='operating_revenue')
        cls.upr           = Account.objects.create(code='102001', name='Change in UPR',   account_type='revenue', sub_type='operating_revenue')
        cls.gross_claims  = Account.objects.create(code='103000', name='Gross Claims',    account_type='expense', sub_type='cost_of_insurance')
        cls.ri_recovered  = Account.objects.create(code='104000', name='RI Claims Recovered', account_type='revenue', sub_type='other_revenue')
        cls.commission_in = Account.objects.create(code='106000', name='Commission From Reinsurers', account_type='revenue', sub_type='other_revenue')
        cls.commission_out= Account.objects.create(code='107000', name='Commissions Paid',account_type='expense', sub_type='cost_of_insurance')
        cls.employee      = Account.objects.create(code='110004', name='Employee Costs', account_type='expense', sub_type='operating_expense')
        cls.depreciation  = Account.objects.create(code='122000', name='Depreciation',   account_type='expense', sub_type='operating_expense')
        cls.finance_cost  = Account.objects.create(code='111002', name='Finance Cost',   account_type='expense', sub_type='operating_expense')
        cls.taxation      = Account.objects.create(code='119001', name='Taxation',       account_type='expense', sub_type='operating_expense')

    def _post(self, entry_date, lines):
        je = JournalEntry.objects.create(
            entry_date=entry_date,
            description='ma_pl test',
            journal_type=JournalEntry.JournalType.GENERAL,
            status=JournalEntry.Status.POSTED,
            currency_code_id='BWP',
            created_by=self.user,
            is_related_party=False,
        )
        for account, debit, credit in lines:
            d = Decimal(str(debit))
            c = Decimal(str(credit))
            JournalEntryLine.objects.create(
                journal_entry=je, account=account,
                debit_amount=d, credit_amount=c,
                debit_bwp=d,    credit_bwp=c,
            )
        return je

    def test_gwp_is_credit_balance_on_4100_code(self):
        """GWP line aggregates Cr balances on 100001 accounts."""
        # Post: customer billed for 1000 of premium
        self._post(date(2025, 1, 15), [
            (self.bank, 1000, 0),
            (self.gwp,  0, 1000),
        ])
        ma = build_ma_pl(date(2025, 1, 1), date(2025, 1, 31))
        self.assertEqual(Decimal(ma['totals']['gross_written_premium']), Decimal('1000.00'))

    def test_net_earned_premium_formula(self):
        """NEP = GWP - Ceded + Change in UPR (all signed correctly)."""
        # GWP: 5000 written
        self._post(date(2025, 1, 15), [(self.bank, 5000, 0), (self.gwp, 0, 5000)])
        # Ceded to reinsurance: 1500 (Dr the ceded account)
        self._post(date(2025, 1, 20), [(self.ceded, 1500, 0), (self.payable, 0, 1500)])
        # Change in UPR: 300 (Cr the UPR P&L account — UPR decreased, recognising prior unearned)
        self._post(date(2025, 1, 25), [(self.bank, 300, 0), (self.upr, 0, 300)])

        ma = build_ma_pl(date(2025, 1, 1), date(2025, 1, 31))
        # GWP = 5000 (Cr), Ceded = 1500 (expense sign: Dr - Cr = 1500), UPR = 300 (income sign: Cr - Dr = 300)
        self.assertEqual(Decimal(ma['totals']['gross_written_premium']), Decimal('5000.00'))
        self.assertEqual(Decimal(ma['totals']['premiums_ceded']),        Decimal('1500.00'))
        self.assertEqual(Decimal(ma['totals']['change_in_upr']),         Decimal('300.00'))
        # NEP = 5000 - 1500 + 300 = 3800
        self.assertEqual(Decimal(ma['totals']['net_earned_premium']),    Decimal('3800.00'))

    def test_net_claim_incurred_subtracts_recoveries(self):
        """Net Claim Incurred = -Gross Claims + RI Recovered + Subrog/Salvage."""
        # Gross claims paid: 2000
        self._post(date(2025, 1, 10), [(self.gross_claims, 2000, 0), (self.bank, 0, 2000)])
        # Recovered from RI: 800
        self._post(date(2025, 1, 12), [(self.bank, 800, 0), (self.ri_recovered, 0, 800)])

        ma = build_ma_pl(date(2025, 1, 1), date(2025, 1, 31))
        self.assertEqual(Decimal(ma['totals']['gross_claims']),       Decimal('2000.00'))
        self.assertEqual(Decimal(ma['totals']['ri_claims_recovered']),Decimal('800.00'))
        # Net Claim incurred (insurance: negative = cost) = -2000 + 800 + 0 = -1200
        self.assertEqual(Decimal(ma['totals']['net_claim_incurred']), Decimal('-1200.00'))

    def test_ebitda_to_pat_chain(self):
        """The roll-up EBITDA → EBIT → PBT → PAT subtracts each below-line cost."""
        # Set up: 10000 GWP, 0 ceded, 0 UPR change → NEP = 10000
        self._post(date(2025, 1, 5), [(self.bank, 10000, 0), (self.gwp, 0, 10000)])
        # 2000 gross claims, 0 recoveries → Net Claim Incurred = -2000
        self._post(date(2025, 1, 6), [(self.gross_claims, 2000, 0), (self.bank, 0, 2000)])
        # 500 commission paid, 0 received → Net Acquisition = -500 (cost)
        self._post(date(2025, 1, 7), [(self.commission_out, 500, 0), (self.bank, 0, 500)])
        # 1000 employee costs (operating expense)
        self._post(date(2025, 1, 8), [(self.employee, 1000, 0), (self.bank, 0, 1000)])
        # No provisions, no other income
        # 200 depreciation, 100 finance cost, 150 tax
        self._post(date(2025, 1, 9),  [(self.depreciation, 200, 0), (self.bank, 0, 200)])
        self._post(date(2025, 1, 10), [(self.finance_cost, 100, 0), (self.bank, 0, 100)])
        self._post(date(2025, 1, 11), [(self.taxation,     150, 0), (self.bank, 0, 150)])

        ma = build_ma_pl(date(2025, 1, 1), date(2025, 1, 31))

        # Gross Profit = NEP + Net Claim Incurred + Net Acquisition
        #              = 10000 + (-2000) + (-500) = 7500
        self.assertEqual(Decimal(ma['totals']['gross_profit']), Decimal('7500.00'))

        # EBITDA = Gross Profit + Other Income - Opex - Provisions
        #        = 7500 + 0 - 1000 - 0 = 6500
        self.assertEqual(Decimal(ma['totals']['ebitda']), Decimal('6500.00'))

        # EBIT = EBITDA - Depreciation = 6500 - 200 = 6300
        self.assertEqual(Decimal(ma['totals']['ebit']), Decimal('6300.00'))

        # PBT = EBIT - Finance Cost = 6300 - 100 = 6200
        self.assertEqual(Decimal(ma['totals']['pbt']), Decimal('6200.00'))

        # PAT = PBT - Taxation = 6200 - 150 = 6050
        self.assertEqual(Decimal(ma['totals']['pat']), Decimal('6050.00'))


class MAPLManagementPackTests(TestCase):
    """
    Verifies build_management_pack uses MA P&L for its insurance KPIs.

    The previous implementation set earned_premium = total_revenue (raw GWP),
    making loss/expense/combined ratios mathematically wrong for any
    insurance company. This test pins the corrected behaviour.
    """

    @classmethod
    def setUpTestData(cls):
        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'},
        )
        cls.user = User.objects.create_user(username='mgmt_pack_tester')
        FiscalPeriod.objects.create(
            period_name='2025-03',
            start_date=date(2025, 3, 1), end_date=date(2025, 3, 31),
        )
        cls.bank  = Account.objects.create(code='110000', name='Bank',           account_type='asset',     sub_type='bank', is_bank_account=True)
        cls.gwp   = Account.objects.create(code='100001', name='GWP — Motor',    account_type='revenue',   sub_type='operating_revenue')
        cls.ceded = Account.objects.create(code='101000', name='RI Ceded',       account_type='revenue',   sub_type='operating_revenue')
        cls.gc    = Account.objects.create(code='103000', name='Gross Claims',   account_type='expense',   sub_type='cost_of_insurance')
        cls.emp   = Account.objects.create(code='110004', name='Employee Costs', account_type='expense',   sub_type='operating_expense')
        cls.payable = Account.objects.create(code='200000', name='Payable',      account_type='liability', sub_type='current_liability')

    def _post(self, entry_date, lines):
        je = JournalEntry.objects.create(
            entry_date=entry_date, description='mgmt pack test',
            journal_type=JournalEntry.JournalType.GENERAL,
            status=JournalEntry.Status.POSTED,
            currency_code_id='BWP', created_by=self.user,
            is_related_party=False,
        )
        for account, debit, credit in lines:
            d = Decimal(str(debit)); c = Decimal(str(credit))
            JournalEntryLine.objects.create(
                journal_entry=je, account=account,
                debit_amount=d, credit_amount=c,
                debit_bwp=d, credit_bwp=c,
            )
        return je

    def test_earned_premium_is_net_earned_premium_not_gwp(self):
        """REGRESSION: management pack must compute ratios off NEP, not GWP."""
        # GWP 10000, Ceded 4000 → NEP = 6000 (not 10000)
        self._post(date(2025, 3, 1),  [(self.bank, 10000, 0), (self.gwp, 0, 10000)])
        self._post(date(2025, 3, 5),  [(self.ceded, 4000, 0), (self.payable, 0, 4000)])
        # Claims 1500 → loss ratio against NEP = 1500/6000 = 25%, against GWP = 15% (WRONG)
        self._post(date(2025, 3, 10), [(self.gc, 1500, 0), (self.bank, 0, 1500)])
        # Opex 600 → expense ratio against NEP = 600/6000 = 10%
        self._post(date(2025, 3, 12), [(self.emp, 600, 0), (self.bank, 0, 600)])

        pack = build_management_pack(date(2025, 3, 1), date(2025, 3, 31))
        kpis = pack['insurance_kpis']

        self.assertEqual(Decimal(kpis['gross_written_premium']), Decimal('10000.00'))
        self.assertEqual(Decimal(kpis['earned_premium']),         Decimal('6000.00'))
        # Loss ratio = 1500 / 6000 = 25.00% (was 15.00% under the old GWP-based bug)
        self.assertEqual(Decimal(kpis['loss_ratio']),    Decimal('25.00'))
        self.assertEqual(Decimal(kpis['expense_ratio']), Decimal('10.00'))
        self.assertEqual(Decimal(kpis['combined_ratio']),Decimal('35.00'))

    def test_ma_pl_payload_is_embedded(self):
        """build_management_pack exposes the full MA P&L payload at .ma_pl."""
        self._post(date(2025, 3, 1), [(self.bank, 1000, 0), (self.gwp, 0, 1000)])
        pack = build_management_pack(date(2025, 3, 1), date(2025, 3, 31))
        self.assertIn('ma_pl', pack)
        self.assertIn('totals', pack['ma_pl'])
        self.assertIn('sections', pack['ma_pl'])
        self.assertEqual(
            Decimal(pack['ma_pl']['totals']['gross_written_premium']),
            Decimal('1000.00'),
        )


# ---------------------------------------------------------------------------
# 2. FROZEN NUMBERS smoke test — runs against live data, skipped by default
# ---------------------------------------------------------------------------

@skipUnless(
    os.environ.get('FROZEN_NUMBERS_DB') == '1',
    'FROZEN NUMBERS smoke test requires live ADIC data after Odoo backfill. '
    'Set FROZEN_NUMBERS_DB=1 to run.',
)
class FrozenNumbersSmokeTest(TestCase):
    """
    CFO directive 2026-05-16: omni must produce these exact figures for ADIC
    standalone or it is wrong. Pinned here so any regression after the Odoo
    backfill (which closes the 26M GWP gap) is caught in CI.

    To enable: FROZEN_NUMBERS_DB=1 python manage.py test reporting.test_ma_pl

    Pre-conditions:
      - Live database with Odoo backfill complete (migrate_odoo --commit)
      - ADIC company seeded (Company.code='ADIC')
    """

    FY25_FROM = date(2024, 7, 1)
    FY25_TO   = date(2025, 6, 30)

    FY26_9M_FROM = date(2025, 7, 1)
    FY26_9M_TO   = date(2026, 3, 31)

    def _adic_company_id(self):
        from core.models import Company
        return Company.objects.get(code='ADIC').id

    def test_fy25_adic_frozen_pl(self):
        ma = build_ma_pl(self.FY25_FROM, self.FY25_TO, company_id=self._adic_company_id())
        # Tolerance: 5,000 BWP per line — accounts for rounding in MA Excel
        TOL = Decimal('5000.00')
        def _close(actual, expected_mn, label):
            expected = Decimal(expected_mn) * Decimal('1000000')
            diff = abs(actual - expected)
            self.assertLessEqual(
                diff, TOL,
                f"{label}: expected {expected} ± {TOL}, got {actual} (diff {diff})",
            )
        t = ma['totals']
        _close(Decimal(t['gross_written_premium']), '125.15', 'GWP')
        _close(Decimal(t['premiums_ceded']),        '74.02',  'Ceded')
        _close(Decimal(t['net_earned_premium']),    '52.48',  'NEP')
        _close(Decimal(t['net_claim_incurred']),    '-15.71', 'Net Claim Incurred')
        _close(Decimal(t['pat']),                   '0.292',  'PAT')

    def test_fy26_9m_adic_frozen_pat(self):
        ma = build_ma_pl(self.FY26_9M_FROM, self.FY26_9M_TO, company_id=self._adic_company_id())
        TOL = Decimal('5000.00')
        expected_pat = Decimal('0.950') * Decimal('1000000')
        self.assertLessEqual(
            abs(Decimal(ma['totals']['pat']) - expected_pat), TOL,
            f"FY26-9M PAT: expected {expected_pat} ± {TOL}, got {ma['totals']['pat']}",
        )
