"""The staff-loan receivable account when the config row is MISSING.

payroll/config.py carries the CFO's decision (121010) and every normal call
reads it from there. This test covers the one path that does not: the fallback
default inside `_receivable_account_code()`, which fires only when the config
row has gone — a fresh company, a wiped settings table, a bad restore. Exactly
the moment nobody is watching.

It shipped as '121000'. In Omni's live chart that is "Provision for Bad Debts
- ECL"; 121010 is the account named "Staff Loan". `_ensure_account()` resolves
purely by code and returns whatever already holds it, so the fallback would
have handed back the bad-debt provision and every payslip loan deduction would
have posted there. (13-Sep-2026, from the Build Spec review.)
"""
from unittest.mock import patch

from django.test import TestCase

from staff_loans.services import _receivable_account_code


class TheFallbackAccountIsTheStaffLoanAccount(TestCase):

    def test_with_no_config_row_it_still_resolves_to_121010(self):
        with patch('payroll.config.get_setting', side_effect=lambda k, d=None: d):
            self.assertEqual(
                _receivable_account_code(), '121010',
                'with the config row gone the fallback must be the account '
                'NAMED "Staff Loan" (121010), never the bad-debt provision '
                '(121000)')

    def test_the_config_row_still_wins_when_it_is_there(self):
        with patch('payroll.config.get_setting', return_value='999999'):
            self.assertEqual(_receivable_account_code(), '999999')
