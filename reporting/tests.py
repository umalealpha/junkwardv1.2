"""reporting/tests.py — financial report correctness."""

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase

from core.models import Currency
from ledger.models import Account, FiscalPeriod, JournalEntry, JournalEntryLine
from reporting.reports import build_balance_sheet


class BalanceSheetPriorYearPLTests(TestCase):
    """
    Regression: the BS must include prior-year revenue/expense balances in equity.

    close_period() only flips a status flag — it does NOT post a year-end JE
    rolling revenue/expense into retained earnings. Without summing prior-year
    P&L into equity, the BS does not balance after the first fiscal year.
    """

    @classmethod
    def setUpTestData(cls):
        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'},
        )

        cls.user = User.objects.create_user(username='tester')

        cls.bank      = Account.objects.create(code='1110', name='FNB BWP', account_type='asset',     sub_type='bank',              is_bank_account=True)
        cls.payable   = Account.objects.create(code='2140', name='Vendor payable', account_type='liability', sub_type='current_liability')
        cls.equity    = Account.objects.create(code='3200', name='Retained earnings', account_type='equity', sub_type='equity')
        cls.revenue   = Account.objects.create(code='4100', name='Gross written premium', account_type='revenue', sub_type='operating_revenue')
        cls.expense   = Account.objects.create(code='6100', name='Salaries', account_type='expense', sub_type='operating_expense')

        # Open fiscal periods covering both the prior-year entry date and the current-year entry date.
        FiscalPeriod.objects.create(period_name='2024-12', start_date=date(2024, 12, 1), end_date=date(2024, 12, 31))
        FiscalPeriod.objects.create(period_name='2025-09', start_date=date(2025,  9, 1), end_date=date(2025,  9, 30))
        FiscalPeriod.objects.create(period_name='2026-05', start_date=date(2026,  5, 1), end_date=date(2026,  5, 31))

    def setUp(self):
        # build_balance_sheet is wrapped in _cache_report (20s TTL) and the cache
        # key is just (as_of, company_id) — which is identical across the tests in
        # this class. The default LocMemCache lives for the whole test process, so
        # without this the second test to run reads the first one's cached dict
        # (rolled-back data, wrong totals) instead of recomputing from the GL.
        cache.clear()

    def _post_je(self, entry_date, lines):
        """Create a JournalEntry already in POSTED state, with balanced lines."""
        je = JournalEntry.objects.create(
            entry_date=entry_date,
            description='test',
            journal_type=JournalEntry.JournalType.GENERAL,
            status=JournalEntry.Status.POSTED,
            currency_code_id='BWP',
            created_by=self.user,
            is_related_party=False,
        )
        for account, debit, credit in lines:
            amt_dr = Decimal(debit)
            amt_cr = Decimal(credit)
            JournalEntryLine.objects.create(
                journal_entry=je, account=account,
                debit_amount=amt_dr, credit_amount=amt_cr,
                debit_bwp=amt_dr, credit_bwp=amt_cr,
            )
        return je

    def test_bs_balances_with_prior_year_revenue(self):
        """A prior-year revenue entry (dated 2024-12, before FY25/26 start) must flow into equity."""
        # Prior fiscal year (FY 2024/25, ending June 2025): revenue of 100 settled into bank
        self._post_je(date(2024, 12, 15), [
            (self.bank,    100, 0),    # DR bank 100
            (self.revenue,   0, 100),  # CR revenue 100
        ])
        # Current fiscal year (started July 2025): expense of 30 paid from bank
        self._post_je(date(2025, 9, 10), [
            (self.expense, 30, 0),     # DR expense 30
            (self.bank,     0, 30),    # CR bank 30
        ])

        bs = build_balance_sheet(date(2026, 5, 12))

        self.assertTrue(bs['totals']['balanced'],
                        f"BS did not balance: {bs['totals']}")
        self.assertEqual(Decimal(bs['totals']['total_assets']), Decimal('70.00'))
        self.assertEqual(Decimal(bs['equity']['prior_year_pl']), Decimal('100.00'),
                         "Prior-year P&L must include the 2024-12 revenue entry.")
        self.assertEqual(Decimal(bs['equity']['current_year_pl']), Decimal('-30.00'),
                         "Current-year P&L is the 2025-09 expense.")
        # Equity total = 0 permanent + 100 prior + (-30) current = 70
        self.assertEqual(Decimal(bs['equity']['total']), Decimal('70.00'))

    def test_bs_balances_with_only_current_year_activity(self):
        """Sanity: when there is no prior-year activity, BS still balances."""
        self._post_je(date(2026, 5, 1), [
            (self.bank,    200, 0),
            (self.revenue,   0, 200),
        ])
        bs = build_balance_sheet(date(2026, 5, 12))
        self.assertTrue(bs['totals']['balanced'])
        self.assertEqual(Decimal(bs['equity']['prior_year_pl']), Decimal('0.00'))
        self.assertEqual(Decimal(bs['equity']['current_year_pl']), Decimal('200.00'))


class V1CashFlowRouteTests(TestCase):
    """CF-404-01 (Manus QC R2, 2026-08-15). The frontend Cash Flow page calls
    /api/v1/reports/cash-flow/ — everything under /api/v1/ — but that route
    was only mounted at /api/reports/cash-flow/, so the page rendered a raw
    'HTTP 404:' error. Lock the v1 mount in.
    """

    def test_v1_cash_flow_route_is_registered(self):
        from django.urls import NoReverseMatch, reverse
        try:
            path = reverse('v1-cash-flow')
        except NoReverseMatch:
            self.fail("CF-404-01 regressed: /api/v1/reports/cash-flow/ is not "
                      "mounted. Re-add the path in alpha_finance/api_router.py.")
        self.assertEqual(path, '/api/v1/reports/cash-flow/')

    def test_v1_cash_flow_route_does_not_404(self):
        # Auth is enforced by DRF; anonymous should get 401/403 — the point is
        # that the URL resolves at all (not 404). Any status other than 404 is
        # proof the route is mounted; the report body is tested elsewhere.
        from django.test import Client
        response = Client().get('/api/v1/reports/cash-flow/')
        self.assertNotEqual(
            response.status_code, 404,
            "CF-404-01 regressed: /api/v1/reports/cash-flow/ returned 404. "
            "The frontend page will render a raw 'HTTP 404:' error.")
