"""
Telegram CFO-bot — leave + claims + "my …" self-resolution (CFO 2026-08-12:
"that bot gets full access of salaries, leave, PO, claims, financials … my
leave days").

Covers:
  1. Local intent detection for leave + claims (names/numbers stay on-box,
     never shipped to DeepSeek).
  2. leave_query — reuses hris.leave_balance (single source of truth):
     single match, no HR profile, no match, many matches.
  3. claims_query — over the GraphiteClaim mirror: register summary, specific
     claim detail, no match.
  4. The fail-closed whitelist gate: leave + claims are released only to
     authorised IDs.
  5. "my leave / my salary / my hours" resolves to the sender via
     TELEGRAM_BOT_SELF_NAME.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest import mock

from django.test import TestCase

from core.ai_assist import DeepSeekUnavailable
from core.telegram_bot import bot as tg_bot
from core.telegram_bot import intent as tg_intent


def _employee_with_profile(full_name, emp_no):
    from payroll.models import Employee
    from hris.models import HRISProfile
    emp = Employee.objects.create(employee_number=emp_no, full_name=full_name)
    HRISProfile.objects.create(employee=emp)
    return emp


def _claim(graphite_id, claim_number, status, customer, *,
           reserve='0', paid='0', balance='0', policy='', ctype='Motor'):
    from integrations.models import GraphiteClaim
    return GraphiteClaim.objects.create(
        graphite_id=graphite_id, claim_number=claim_number, status=status,
        customer_name=customer, claim_type=ctype, policy_number=policy,
        total_reserve=Decimal(reserve), total_payment=Decimal(paid),
        balance=Decimal(balance),
        registered_date=date(2026, 7, 1))


# ===========================================================================
# Intent routing
# ===========================================================================

class LeaveClaimsIntentTests(TestCase):

    def test_my_leave_days_routes_leave_with_self_token(self):
        r = tg_intent.parse_intent('my leave days')
        self.assertEqual(r['intent'], 'leave')
        self.assertIn(r['employee_query'].strip().lower(),
                      {'my', 'me', 'mine'})

    def test_leave_for_named_person(self):
        r = tg_intent.parse_intent('leave for Pako')
        self.assertEqual(r['intent'], 'leave')
        self.assertIn('pako', r['employee_query'].lower())
        self.assertNotIn('leave', r['employee_query'].lower())

    def test_sick_leave_balance_named(self):
        r = tg_intent.parse_intent('sick leave balance for Kago')
        self.assertEqual(r['intent'], 'leave')
        self.assertIn('kago', r['employee_query'].lower())

    def test_claims_summary(self):
        r = tg_intent.parse_intent('claims')
        self.assertEqual(r['intent'], 'claims')
        self.assertEqual(r['query'].strip(), '')

    def test_claims_register_not_swallowed_by_assets(self):
        # 'register' is an asset keyword — claims must still win (Fable FIX 1).
        r = tg_intent.parse_intent('claims register')
        self.assertEqual(r['intent'], 'claims')

    def test_leave_register_not_swallowed_by_assets(self):
        r = tg_intent.parse_intent('leave register for Kago')
        self.assertEqual(r['intent'], 'leave')
        self.assertIn('kago', r['employee_query'].lower())

    def test_claim_by_number(self):
        r = tg_intent.parse_intent('claim COMG2024109949')
        self.assertEqual(r['intent'], 'claims')
        self.assertIn('comg2024109949', r['query'].lower())

    @mock.patch('core.telegram_bot.intent.deepseek_complete',
                side_effect=DeepSeekUnavailable('offline in tests'))
    def test_everyday_leave_verb_is_not_leave(self, _ds):
        # "leave it with me" must not hit the HR leave intent.
        r = tg_intent.parse_intent('leave it with me for now')
        self.assertNotEqual(r['intent'], 'leave')


# ===========================================================================
# leave_query service
# ===========================================================================

class LeaveServiceTests(TestCase):

    def setUp(self):
        _employee_with_profile('Pako Kago', 'E001')

    def test_returns_balances_for_named_employee(self):
        from core.telegram_bot import services
        out = services.leave_query('pako')
        self.assertIn('Pako Kago', out)
        self.assertIn('left', out.lower())          # e.g. "Annual Leave 18.0 left"

    def test_no_hr_profile(self):
        from core.telegram_bot import services
        from payroll.models import Employee
        Employee.objects.create(employee_number='E777', full_name='Naked Payroll')
        out = services.leave_query('naked')
        self.assertIn('no HR profile', out)

    def test_no_match(self):
        from core.telegram_bot import services
        out = services.leave_query('zznobody')
        self.assertIn('No staff member', out)

    def test_multiple_matches_ask_which(self):
        from core.telegram_bot import services
        _employee_with_profile('Pako Moyo', 'E002')
        out = services.leave_query('pako')
        self.assertIn('which one', out.lower())
        self.assertIn('Pako Kago', out)
        self.assertIn('Pako Moyo', out)


# ===========================================================================
# claims_query service
# ===========================================================================

class ClaimsServiceTests(TestCase):

    def setUp(self):
        _claim(1001, 'COMG2024109949', 'open', 'V.I.P Dental Care',
               reserve='50000', paid='10000', balance='40000',
               policy='COMG2024109949')
        _claim(1002, 'MISG2024100001', 'closed', 'John Doe',
               reserve='8000', paid='8000', balance='0')

    def test_register_summary(self):
        from core.telegram_bot import services
        out = services.claims_query('')
        self.assertIn('2 claims', out)
        self.assertIn('open', out.lower())
        self.assertIn('closed', out.lower())
        self.assertIn('40,000', out)                # outstanding total

    def test_specific_claim_detail(self):
        from core.telegram_bot import services
        out = services.claims_query('COMG2024109949')
        self.assertIn('V.I.P Dental Care', out)
        self.assertIn('40,000', out)                # outstanding
        self.assertIn('10,000', out)                # paid

    def test_no_match(self):
        from core.telegram_bot import services
        out = services.claims_query('ZZZ9999')
        self.assertIn('No claim matches', out)


# ===========================================================================
# Whitelist gate + self-resolution
# ===========================================================================

class LeaveClaimsGateTests(TestCase):

    def setUp(self):
        _employee_with_profile('Pako Kago', 'E001')
        _claim(1001, 'COMG2024109949', 'open', 'V.I.P Dental Care',
               balance='40000')

    def test_leave_denied_without_whitelist(self):
        out = tg_bot._handle_query('leave for Pako', allow_sensitive=False)
        self.assertIn('restricted', out.lower())

    def test_claims_denied_without_whitelist(self):
        out = tg_bot._handle_query('claims', allow_sensitive=False)
        self.assertIn('restricted', out.lower())

    def test_leave_allowed_with_whitelist(self):
        out = tg_bot._handle_query('leave for Pako', allow_sensitive=True)
        self.assertIn('Pako Kago', out)
        self.assertIn('left', out.lower())

    def test_my_leave_resolves_to_self(self):
        out = tg_bot._handle_query('my leave days', allow_sensitive=True,
                                   self_name='Pako Kago')
        self.assertIn('Pako Kago', out)
        self.assertIn('left', out.lower())

    def test_my_leave_without_self_name_asks_who(self):
        # No TELEGRAM_BOT_SELF_NAME configured → the pronoun must NOT be fed to
        # the name matcher (would fuzzy-match Amy/Myra/Smyth). Ask instead.
        out = tg_bot._handle_query('my leave days', allow_sensitive=True,
                                   self_name='')
        self.assertNotIn('restricted', out.lower())
        self.assertIn('my', out.lower())
        self.assertIn("isn't set up", out)

    def test_my_leave_pronoun_never_reaches_name_match(self):
        # An employee whose name contains "my" must NOT be returned for "my …".
        _employee_with_profile('Amy Molefe', 'E900')
        out = tg_bot._handle_query('my leave days', allow_sensitive=True,
                                   self_name='')
        self.assertNotIn('Amy Molefe', out)
