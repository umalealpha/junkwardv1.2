"""
Telegram CFO-bot payroll intent — added 2026-07-16 (CFO request:
"ask how much we paid Pako last month and it should answer").

Covers:
  1. Local intent detection — the employee NAME is classified + extracted
     on-box and never shipped to DeepSeek.
  2. The staff_pay service — name match (single / none / many), period
     resolution, and the net-pay figure.
  3. The FAIL-CLOSED whitelist gate in bot._handle_query — staff pay is
     released only to explicitly authorised Telegram IDs.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest import mock

from django.test import TestCase

from core.ai_assist import DeepSeekUnavailable
from core.telegram_bot import bot as tg_bot
from core.telegram_bot import intent as tg_intent


def _payslip(full_name, period_name, net, *, emp_no,
             gross='18000.00', paye='3200.00', status=None):
    from payroll.models import Employee, PayrollPeriod, Payslip
    period, _ = PayrollPeriod.objects.get_or_create(
        period_name=period_name,
        defaults={'start_date': date(int(period_name[:4]), int(period_name[5:7]), 1),
                  'end_date': date(int(period_name[:4]), int(period_name[5:7]), 28)},
    )
    emp = Employee.objects.create(employee_number=emp_no, full_name=full_name)
    Payslip.objects.create(
        employee=emp, period=period,
        gross_amount=Decimal(gross), paye_amount=Decimal(paye),
        net_amount=Decimal(net),
        status=status or Payslip.Status.PAID,
    )
    return emp, period


class PayrollIntentDetectionTests(TestCase):
    """parse_intent must classify pay questions locally and pull name/period."""

    def test_paid_x_last_month(self):
        r = tg_intent.parse_intent('how much did we pay Pako last month')
        self.assertEqual(r['intent'], 'payroll')
        self.assertIn('pako', r['employee_query'].lower())
        self.assertTrue(r['want_last_month'])

    def test_possessive_salary_with_period(self):
        r = tg_intent.parse_intent("what is Pako's salary for 2026-05")
        self.assertEqual(r['intent'], 'payroll')
        self.assertIn('pako', r['employee_query'].lower())
        self.assertEqual(r['period_name'], '2026-05')

    def test_salary_keyword_without_verb(self):
        r = tg_intent.parse_intent('salary for Bokani')
        self.assertEqual(r['intent'], 'payroll')
        self.assertIn('bokani', r['employee_query'].lower())

    def test_month_name_period_and_clean_name(self):
        # Exact screenshot query (CFO 2026-07-16): old bot answered Payables.
        r = tg_intent.parse_intent(
            'What is Prathap Ganesharajahs salary for June 2026')
        self.assertEqual(r['intent'], 'payroll')
        self.assertEqual(r['period_name'], '2026-06')
        self.assertIn('prathap', r['employee_query'].lower())
        self.assertNotIn('june', r['employee_query'].lower())

    def test_gross_salary_routes_payroll_not_payables(self):
        r = tg_intent.parse_intent(
            'What is Prathap Ganesharajahs gross salary for June 2026')
        self.assertEqual(r['intent'], 'payroll')
        self.assertEqual(r['period_name'], '2026-06')

    @mock.patch('core.telegram_bot.intent.deepseek_complete',
                side_effect=DeepSeekUnavailable('offline in tests'))
    def test_premium_pay_is_not_payroll(self, _ds):
        # An insurance "pay" question must fall through, not hijack payroll.
        r = tg_intent.parse_intent('how much premium did we pay to reinsurers')
        self.assertNotEqual(r['intent'], 'payroll')

    @mock.patch('core.telegram_bot.intent.deepseek_complete',
                side_effect=DeepSeekUnavailable('offline in tests'))
    def test_bills_to_pay_is_not_payroll(self, _ds):
        r = tg_intent.parse_intent('what bills do we have to pay')
        self.assertNotEqual(r['intent'], 'payroll')


class StaffPayServiceTests(TestCase):

    def setUp(self):
        _payslip('Pako Kago', '2026-05', '14800.00', emp_no='E001')

    def test_returns_net_for_named_employee(self):
        from core.telegram_bot import services
        out = services.staff_pay('pako', period_name='2026-05')
        self.assertIn('Pako Kago', out)
        self.assertIn('14,800.00', out)

    def test_no_match(self):
        from core.telegram_bot import services
        out = services.staff_pay('zznobody')
        self.assertIn('No staff member', out)

    def test_multiple_matches_ask_which(self):
        from core.telegram_bot import services
        from payroll.models import Employee
        Employee.objects.create(employee_number='E002', full_name='Pako Moyo')
        out = services.staff_pay('pako')
        self.assertIn('which one', out.lower())
        self.assertIn('Pako Kago', out)
        self.assertIn('Pako Moyo', out)

    def test_unknown_period_uses_latest_and_labels_it(self):
        from core.telegram_bot import services
        out = services.staff_pay('pako', period_name='2099-01')
        # No 2099-01 period exists → resolves to the latest slip; the reply
        # header names the ACTUAL period used, so nothing is misrepresented.
        self.assertIn('2026-05', out)
        self.assertIn('14,800.00', out)

    def test_national_id_never_leaks(self):
        from core.telegram_bot import services
        from payroll.models import Employee
        Employee.objects.filter(full_name='Pako Kago').update(
            national_id='123456789')
        out = services.staff_pay('pako', period_name='2026-05')
        self.assertNotIn('123456789', out)


class PayrollGateTests(TestCase):

    def setUp(self):
        _payslip('Pako Kago', '2026-05', '14800.00', emp_no='E001')

    def test_denied_without_whitelist(self):
        out = tg_bot._handle_query(
            'how much did we pay Pako last month', allow_sensitive=False)
        self.assertIn('restricted', out.lower())
        self.assertNotIn('14,800', out)          # figure must NOT leak

    def test_allowed_with_whitelist(self):
        out = tg_bot._handle_query(
            "Pako's salary 2026-05", allow_sensitive=True)
        self.assertIn('Pako Kago', out)
        self.assertIn('14,800.00', out)


# ===========================================================================
# Time Doctor / Fleet / Assets — "everything" domains (CFO 2026-07-16)
# ===========================================================================

def _asset(tag, name, custodian, location, cost):
    from django.contrib.auth.models import User
    from core.models import Currency, Company
    from ledger.models import Account
    from assets.models import Asset, AssetCategory
    seed_user, _ = User.objects.get_or_create(username='asset_seed')
    Currency.objects.get_or_create(
        code='BWP', defaults={'name': 'Pula', 'symbol': 'P'})
    co, _ = Company.objects.get_or_create(
        code='ADI', defaults={'name': 'Alpha Direct'})

    def _acc(code, atype):
        return Account.objects.get_or_create(
            code=code, defaults={'name': code, 'account_type': atype,
                                 'currency_code_id': 'BWP'})[0]

    cat, _ = AssetCategory.objects.get_or_create(
        code='IT', defaults={
            'name': 'IT Equipment',
            'cost_account': _acc('1500', 'asset'),
            'accum_depr_account': _acc('1510', 'asset'),
            'depreciation_expense_account': _acc('7500', 'expense'),
        })
    return Asset.objects.create(
        tag_number=tag, name=name, company=co, category=cat,
        cost=Decimal(cost), custodian=custodian, location=location,
        status=Asset.Status.ACTIVE,
        useful_life_months=60,
        purchase_date=date(2025, 1, 1), in_service_date=date(2025, 1, 1),
        created_by=seed_user)


class TimeDoctorTests(TestCase):

    def setUp(self):
        # The person is resolved against the STAFF ROSTER and the figure is
        # fetched through the confirmed Time Doctor account link -- never by
        # matching the feed's own name text (checklist L19). This setUp used to
        # create only the snapshot, which is why it stopped passing when the
        # lookup was corrected on 2026-09-09.
        from hris.models import HRISProfile, TrackingDirective
        from payroll.models import Employee
        from integrations.models import (TimeDoctorDailySnapshot,
                                         TimeDoctorUserMap)
        for num, name, email, uid in (
                ('TD-1', 'Pako Kago', 'pkago@x', 'u-pako'),
                ('TD-2', 'Bokani M', 'bmakosha@x', 'u-bokani')):
            emp = Employee.objects.create(
                employee_number=num, full_name=name, email=email,
                status='active')
            HRISProfile.objects.create(employee=emp)
            TrackingDirective.objects.create(employee=emp,
                                             expected_to_track=True)
            TimeDoctorUserMap.objects.create(
                td_user_id=uid, td_name=name, td_email=email,
                employee=emp, confirmed=True)
        TimeDoctorDailySnapshot.objects.create(
            company_id='ADI', as_of=date(2026, 7, 15),
            totals={'active_users': 2, 'total_hours': 14.5,
                    'productive_pct': 81.0},
            payload=[
                {'user_id': 'u-pako',
                 'name': 'Pako Kago', 'email': 'pkago@x',
                 'hours_tracked': 7.5, 'productive_pct': 83.0,
                 'tracked_today': True, 'last_seen': '2026-07-15T16:00'},
                {'user_id': 'u-bokani',
                 'name': 'Bokani M', 'email': 'bmakosha@x',
                 'hours_tracked': 7.0, 'productive_pct': 79.0,
                 'tracked_today': True},
            ])

    def test_person_hours(self):
        from core.telegram_bot import services
        out = services.time_doctor_query('pako')
        self.assertIn('Pako Kago', out)
        self.assertIn('7.5', out)
        self.assertIn('83', out)

    def test_workforce_rollup(self):
        from core.telegram_bot import services
        out = services.time_doctor_query('')
        self.assertIn('14.5', out)
        self.assertIn('active', out.lower())

    def test_intent_routes_hours(self):
        r = tg_intent.parse_intent('how many hours did Pako work today')
        self.assertEqual(r['intent'], 'time_doctor')
        self.assertIn('pako', r['employee_query'].lower())

    def test_gate_blocks_hours_without_whitelist(self):
        out = tg_bot._handle_query(
            'how many hours did Pako work', allow_sensitive=False)
        self.assertIn('restricted', out.lower())


class FleetTests(TestCase):

    def setUp(self):
        from nexus.models import FleetVehicle
        FleetVehicle.objects.create(
            registration='B 123 ABC', make='Toyota', model='Hilux',
            driver_name='Arun Iyer', where='Gaborone CBD', moving=True,
            last_speed_kmh=60, odometer_km=45000)
        FleetVehicle.objects.create(
            registration='B 999 XYZ', make='Ford', model='Ranger',
            driver_name='Arjun P', where='Airport', moving=False)

    def test_list_all(self):
        from core.telegram_bot import services
        out = services.fleet_query('')
        self.assertIn('B 123 ABC', out)
        self.assertIn('B 999 XYZ', out)

    def test_detail_by_driver(self):
        from core.telegram_bot import services
        out = services.fleet_query('arun')
        self.assertIn('B 123 ABC', out)
        self.assertIn('Hilux', out)
        self.assertIn('Gaborone', out)

    def test_intent_where_is_plate(self):
        r = tg_intent.parse_intent('where is B 123 ABC')
        self.assertEqual(r['intent'], 'fleet')

    @mock.patch('core.telegram_bot.intent.deepseek_complete',
                side_effect=DeepSeekUnavailable('offline in tests'))
    def test_where_is_cash_is_not_fleet(self, _ds):
        r = tg_intent.parse_intent('where is the cash')
        self.assertNotEqual(r['intent'], 'fleet')


class AssetTests(TestCase):

    def setUp(self):
        _asset('ADI-IT-0042', 'MacBook Pro 16', 'Arun Iyer',
               'HQ 3rd floor', '35000')

    def test_detail_by_tag(self):
        from core.telegram_bot import services
        out = services.asset_query('ADI-IT-0042')
        self.assertIn('MacBook', out)
        self.assertIn('Arun Iyer', out)
        self.assertIn('35,000', out)

    def test_lookup_by_custodian_lists(self):
        from core.telegram_bot import services
        _asset('ADI-IT-0043', 'Dell Monitor', 'Arun Iyer', 'HQ', '4000')
        out = services.asset_query('arun')
        self.assertIn('ADI-IT-0042', out)
        self.assertIn('ADI-IT-0043', out)

    def test_intent_routes_assets(self):
        r = tg_intent.parse_intent('what assets does Arun hold')
        self.assertEqual(r['intent'], 'assets')
        self.assertIn('arun', r['query'].lower())


class AiFallbackTests(TestCase):
    """Off-menu questions → DeepSeek over a company-level bundle (no PII)."""

    def test_long_digit_scrub(self):
        from core.telegram_bot.services import _LONG_DIGITS
        self.assertEqual(
            _LONG_DIGITS.sub('[x]', 'acct 620111222 end'), 'acct [x] end')
        self.assertEqual(
            _LONG_DIGITS.sub('[x]', 'P 46,380 net'), 'P 46,380 net')  # short kept

    @mock.patch('core.ai_assist.deepseek_complete',
                return_value='Gross profit is P 46.38m.')
    def test_ai_answer_wraps_deepseek(self, _ds):
        from core.telegram_bot import services
        out = services.ai_answer('are we profitable')
        self.assertIn('46.38m', out)
        self.assertIn('Omni', out)          # disclaimer footer present

    @mock.patch('core.telegram_bot.services.ai_answer', return_value='AI SAYS X')
    @mock.patch('core.telegram_bot.intent.deepseek_complete',
                side_effect=DeepSeekUnavailable('offline in tests'))
    def test_unknown_question_routes_to_ai(self, _ds, _ai):
        out = tg_bot._handle_query('ponder the cosmos', allow_sensitive=False)
        self.assertEqual(out, 'AI SAYS X')
        _ai.assert_called_once()

    @mock.patch('core.ai_assist.deepseek_complete')
    def test_ai_context_excludes_individual_pay(self, ds):
        # Capture what would be sent to DeepSeek; assert no salary roster leaks.
        from core.telegram_bot import services
        ds.side_effect = lambda prompt, **kw: prompt          # echo the prompt
        _payslip_seed()
        sent = services.ai_answer('summarise the business')
        self.assertNotIn('Pako Kago', sent)                   # no per-person pay
        self.assertNotIn('14,800', sent)

    @mock.patch('core.ai_assist.deepseek_complete')
    def test_frozen_figures_lead_context(self, ds):
        from core.telegram_bot import services
        from ledger.models import FrozenFigure
        FrozenFigure.objects.create(period='FY25_Jun2025', line_label='GWP',
                                    value_bwp=Decimal('125149000'))
        FrozenFigure.objects.create(period='FY25_Jun2025', line_label='PAT',
                                    value_bwp=Decimal('292000'))
        ds.side_effect = lambda prompt, **kw: prompt          # echo the prompt
        sent = services.ai_answer('what is our GWP and PAT')
        self.assertIn('AUTHORITATIVE FROZEN', sent)
        self.assertIn('125,149,000', sent)                    # commas dodge scrub
        self.assertIn('292,000', sent)


def _payslip_seed():
    """A paid payslip that must NOT appear in the AI context bundle."""
    from datetime import date as _d
    from decimal import Decimal as _D
    from payroll.models import Employee, PayrollPeriod, Payslip
    p, _ = PayrollPeriod.objects.get_or_create(
        period_name='2026-06',
        defaults={'start_date': _d(2026, 6, 1), 'end_date': _d(2026, 6, 30)})
    e = Employee.objects.create(employee_number='Z9', full_name='Pako Kago')
    Payslip.objects.create(employee=e, period=p, gross_amount=_D('18000'),
                           paye_amount=_D('3200'), net_amount=_D('14800'),
                           status=Payslip.Status.PAID)
