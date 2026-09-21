"""Tests for Leave Encashment — self-service application + 4-step chain
(CFO 2026-07-21).

Chain: employee applies → CFO → HR → (FC or FM) → paid.

Covers: server-side valuation (BASIC ÷ 24 × days, never client input),
self-service apply + days validation, day RESERVATION against the balance
while in flight, the full approval chain advancing stage by stage, stage
authority (wrong role blocked), either FC or FM signing the finance leg,
segregation of duties (applicant + no-double-signing), rejection releasing
the reserved days, payment gating, and the salary-register access gate.
"""
import datetime as dt
from decimal import Decimal

from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from core.models import Company, UserProfile
from hris.leave_balance import balances_for_profile
from hris.models import HRISProfile, LeaveOpeningBalance
from payroll.models import (Employee, Payslip, PayslipComponent, PayslipLine,
                            PayrollPeriod)

# Every fixture below is hired well before the current leave year, so the
# accrual maths these tests assert on is unaffected by hire-date bounding
# (`leave_balance.accrual_start`) and `apply_encashment` does not refuse
# them for a missing start date. CFO 2026-09-07.
_HIRED = dt.date(2020, 1, 1)

URL = '/hris/api/leave-encashment/'


def _annual(profile) -> Decimal:
    for b in balances_for_profile(profile):
        if b['code'] == 'annual':
            return Decimal(str(b['available']))
    return Decimal('0')


class LeaveEncashmentChainTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(code='TEST', name='Test Co.')

        # Applicant — a plain employee (ess) with 20 annual days available.
        cls.emp = Employee.objects.create(
            employee_number='E300', full_name='Naledi Test',
            department='Claims', email='naledi@test.example',
            company=cls.company, status='active', hire_date=_HIRED)
        cls.applicant = User.objects.create_user('naledi', 'naledi@test.example', 'x')
        cls.emp.user = cls.applicant
        cls.emp.save(update_fields=['user'])
        cls.profile = HRISProfile.objects.create(employee=cls.emp)
        # as-at TODAY (not 30 days back). Annual leave accrues one twelfth per
        # whole month completed since the as-at date, so an opening dated a month
        # ago added ~1 day of accrual and the available balance drifted with the
        # calendar (20 read as 21.66 at a month-end). As-at today = available is
        # the opening figure exactly, which is what every assertion here expects.
        LeaveOpeningBalance.objects.create(
            profile=cls.profile, leave_type_code='annual',
            entitlement_days=Decimal('20'), opening_balance_days=Decimal('20'),
            accrued_days=Decimal('20'),
            as_at_date=timezone.localdate())

        # BASIC 11,000 → daily rate 458.33 (÷24). Draft slip carries real figures.
        basic = PayslipComponent.objects.create(
            code='BASIC', name='Basic Salary', kind='earning', is_taxable=True)
        period = PayrollPeriod.objects.create(
            period_name='2099-01', start_date=dt.date(2099, 1, 1),
            end_date=dt.date(2099, 1, 31))
        slip = Payslip.objects.create(
            employee=cls.emp, period=period, company=cls.company,
            status=Payslip.Status.DRAFT)
        PayslipLine.objects.create(payslip=slip, component=basic,
                                   amount=Decimal('11000.00'))

        # Approvers.
        cls.cfo = User.objects.create_user(
            'prathap', 'pganesharajah@alphadirect.co.bw', 'x')
        cls.hr = User.objects.create_user('unami', 'ubutale@alphadirect.co.bw', 'x')
        cls.fm = User.objects.create_user('fm', 'fm@alphadirect.co.bw', 'x')
        UserProfile.objects.update_or_create(
            user=cls.fm, defaults={'title': UserProfile.Title.FINANCE_MANAGER, 'is_active': True})
        cls.fc = User.objects.create_user('fc', 'fc@alphadirect.co.bw', 'x')
        UserProfile.objects.update_or_create(
            user=cls.fc, defaults={'title': UserProfile.Title.FINANCIAL_CONTROLLER, 'is_active': True})
        cls.other = User.objects.create_user('other', 'other@test.example', 'x')

    def _client(self, user):
        c = APIClient(); c.force_authenticate(user); return c

    # Encashment now requires a ≥50-word motivation (CFO 2026-07-22). Every
    # apply in these tests sends a valid one so we exercise the real chain; a
    # test that wants to check the rule itself passes reason='' via **extra.
    _REASON = (
        'I am requesting to encash a portion of my accrued annual leave because '
        'I have unavoidable personal financial commitments this month and I will '
        'not be able to take the leave as time off given the current operational '
        'workload in my department, so I would prefer to convert these specific '
        'days into cash rather than carry them forward to the next leave cycle '
        'where they might otherwise be lost or forfeited entirely.')

    def _apply(self, days='4', user=None, **extra):
        return self._client(user or self.applicant).post(
            URL, {'days': days, 'reason': self._REASON, **extra}, format='json')

    def test_short_reason_rejected(self):
        # The ≥50-word motivation rule (CFO 2026-07-22) must be enforced.
        r = self._apply(days='4', reason='too short')
        self.assertEqual(r.status_code, 400)
        self.assertIn('word', r.json()['detail'].lower())

    # ── valuation + apply ──────────────────────────────────────────────────
    def test_valuation_server_computed(self):
        r = self._apply(days='4', amount='999999', daily_rate='1')
        self.assertEqual(r.status_code, 201, r.content)
        b = r.json()
        self.assertEqual(b['basic_salary'], '11000.00')
        self.assertEqual(b['daily_rate'], '458.33')   # 11000/24
        self.assertEqual(b['amount'], '1833.32')       # 458.33×4
        self.assertEqual(b['status'], 'pending_cfo')
        self.assertTrue(b['is_own'])

    def test_days_validation(self):
        self.assertEqual(self._apply(days='0').status_code, 400)
        self.assertEqual(self._apply(days='2.3').status_code, 400)   # not half-day
        self.assertEqual(self._apply(days='25').status_code, 400)    # > 20 avail

    def test_nan_days_rejected(self):
        # NaN / Infinity parse as Decimal but must not 500 (Fable 5 review)
        self.assertEqual(self._apply(days='NaN').status_code, 400)
        self.assertEqual(self._apply(days='Infinity').status_code, 400)

    def test_profile_required(self):
        # An employee with no HRISProfile cannot apply (else nothing reserves
        # the days) — Fable 5 review.
        emp = Employee.objects.create(
            employee_number='E399', full_name='No Profile', company=self.company,
            status='active', hire_date=_HIRED, user=User.objects.create_user('np', 'np@test.example', 'x'))
        PayslipLine.objects.create(
            payslip=Payslip.objects.create(
                employee=emp, period=PayrollPeriod.objects.get(period_name='2099-01'),
                company=self.company, status=Payslip.Status.DRAFT),
            component=PayslipComponent.objects.get(code='BASIC'),
            amount=Decimal('9000.00'))
        r = self._apply(days='1', user=emp.user)
        self.assertEqual(r.status_code, 400)
        self.assertIn('profile', r.json()['detail'].lower())

    def test_apply_reserves_days_immediately(self):
        self.assertEqual(_annual(self.profile), Decimal('20'))
        self._apply(days='5')
        # pending application already reserves the days
        self.assertEqual(_annual(self.profile), Decimal('15'))

    def test_quote_shown_to_applicant(self):
        d = self._client(self.applicant).get(URL).json()
        self.assertTrue(d['quote']['has_record'])
        self.assertEqual(d['quote']['daily_rate'], '458.33')
        self.assertEqual(Decimal(d['quote']['available_days']), Decimal('20'))

    # ── the chain ──────────────────────────────────────────────────────────
    def _advance(self, enc_id, user):
        return self._client(user).post(f'{URL}{enc_id}/approve/', {}, format='json')

    def test_full_chain_cfo_hr_finance_pay(self):
        enc_id = self._apply(days='4').json()['id']

        # wrong-stage authority blocked: HR / finance can't do the CFO leg
        self.assertEqual(self._advance(enc_id, self.hr).status_code, 400)
        self.assertEqual(self._advance(enc_id, self.fm).status_code, 400)

        # CFO leg
        r = self._advance(enc_id, self.cfo)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['status'], 'pending_hr')

        # HR leg (CFO can't do it — not HR)
        self.assertEqual(self._advance(enc_id, self.cfo).status_code, 400)
        self.assertEqual(self._advance(enc_id, self.hr).json()['status'], 'pending_finance')

        # Finance leg — either FC or FM; here FM
        r = self._advance(enc_id, self.fm)
        self.assertEqual(r.json()['status'], 'approved')

        # days stay reserved through the whole chain
        self.assertEqual(_annual(self.profile), Decimal('16'))

        # payment: only finance, only after approved
        self.assertEqual(self._client(self.other).post(f'{URL}{enc_id}/mark-paid/', {}, format='json').status_code, 400)
        r = self._client(self.fc).post(f'{URL}{enc_id}/mark-paid/', {}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['status'], 'paid')
        self.assertTrue(r.json()['paid']['is_paid'])
        # paid days are gone for good
        self.assertEqual(_annual(self.profile), Decimal('16'))

    def test_applicant_cannot_pay_own(self):
        # An FC who applies for their own encashment can't mark it paid, even
        # though FC can normally pay (Fable 5 review).
        emp = Employee.objects.create(
            employee_number='E398', full_name='Pako Applies', company=self.company,
            status='active', hire_date=_HIRED, user=self.fc, email='fc@alphadirect.co.bw')
        HRISProfile.objects.create(employee=emp)
        LeaveOpeningBalance.objects.create(
            profile=emp.hris_profile, leave_type_code='annual',
            entitlement_days=Decimal('20'), opening_balance_days=Decimal('20'),
            accrued_days=Decimal('20'), as_at_date=timezone.localdate())
        PayslipLine.objects.create(
            payslip=Payslip.objects.create(
                employee=emp, period=PayrollPeriod.objects.get(period_name='2099-01'),
                company=self.company, status=Payslip.Status.DRAFT),
            component=PayslipComponent.objects.get(code='BASIC'),
            amount=Decimal('30000.00'))
        enc_id = self._apply(days='1', user=self.fc).json()['id']
        self._advance(enc_id, self.cfo)
        self._advance(enc_id, self.hr)
        # the OTHER finance person approves (fc is the applicant, blocked by SoD)
        self.assertEqual(self._advance(enc_id, self.fm).json()['status'], 'approved')
        # fc is the applicant → cannot mark their own paid
        r = self._client(self.fc).post(f'{URL}{enc_id}/mark-paid/', {}, format='json')
        self.assertEqual(r.status_code, 400)
        # a different finance person can
        self.assertEqual(
            self._client(self.fm).post(f'{URL}{enc_id}/mark-paid/', {}, format='json').status_code, 200)

    def test_finance_leg_by_fc(self):
        enc_id = self._apply(days='2').json()['id']
        self._advance(enc_id, self.cfo)
        self._advance(enc_id, self.hr)
        self.assertEqual(self._advance(enc_id, self.fc).json()['status'], 'approved')

    def test_sod_applicant_cannot_approve(self):
        # applicant who happens to be the CFO cannot approve their own
        emp = Employee.objects.create(
            employee_number='E301', full_name='Boss Self', company=self.company,
            status='active', hire_date=_HIRED, user=self.cfo, email='pganesharajah@alphadirect.co.bw')
        HRISProfile.objects.create(employee=emp)
        LeaveOpeningBalance.objects.create(
            profile=emp.hris_profile, leave_type_code='annual',
            entitlement_days=Decimal('20'), opening_balance_days=Decimal('20'),
            accrued_days=Decimal('20'), as_at_date=timezone.localdate())
        PayslipLine.objects.create(
            payslip=Payslip.objects.create(
                employee=emp, period=PayrollPeriod.objects.get(period_name='2099-01'),
                company=self.company, status=Payslip.Status.DRAFT),
            component=PayslipComponent.objects.get(code='BASIC'),
            amount=Decimal('22000.00'))
        enc_id = self._apply(days='1', user=self.cfo).json()['id']
        # CFO is the applicant → cannot sign the CFO leg
        self.assertEqual(self._advance(enc_id, self.cfo).status_code, 400)

    # ── CFO-applicant self-leg skip (CFO directive 2026-08-24) ──────────────
    def _cfo_applicant_setup(self, basic='70000.00'):
        """Give the CFO (self.cfo) their own employee record so they can apply
        for their own leave encashment — the 2026-08-24 case."""
        emp = Employee.objects.create(
            employee_number='E777', full_name='CFO Self', company=self.company,
            status='active', hire_date=_HIRED, user=self.cfo,
            email='pganesharajah@alphadirect.co.bw')
        HRISProfile.objects.create(employee=emp)
        LeaveOpeningBalance.objects.create(
            profile=emp.hris_profile, leave_type_code='annual',
            entitlement_days=Decimal('40'), opening_balance_days=Decimal('40'),
            accrued_days=Decimal('40'),
            as_at_date=timezone.localdate())
        PayslipLine.objects.create(
            payslip=Payslip.objects.create(
                employee=emp,
                period=PayrollPeriod.objects.get(period_name='2099-01'),
                company=self.company, status=Payslip.Status.DRAFT),
            component=PayslipComponent.objects.get(code='BASIC'),
            amount=Decimal(basic))
        return emp

    def test_cfo_applicant_skips_own_cfo_leg(self):
        # The CFO cannot sign their own CFO leg, and the only other CFO login is
        # a backup super-admin — so the self-leg is skipped and the request
        # starts at the HR leg. WITHOUT the fix it would start at pending_cfo and
        # freeze whenever that backup is unavailable (the 2026-08-24 incident).
        self._cfo_applicant_setup()
        r = self._apply(days='2', user=self.cfo)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['status'], 'pending_hr')

    def test_cfo_applicant_signed_by_hr_then_finance(self):
        # Two independent legs still sign; the CFO/applicant approves nothing.
        self._cfo_applicant_setup()
        enc_id = self._apply(days='2', user=self.cfo).json()['id']
        # the CFO (applicant) is barred at the HR leg too
        self.assertEqual(self._advance(enc_id, self.cfo).status_code, 400)
        self.assertEqual(
            self._advance(enc_id, self.hr).json()['status'], 'pending_finance')
        self.assertEqual(
            self._advance(enc_id, self.fm).json()['status'], 'approved')
        from hris.leave_encash_models import LeaveEncashment
        enc = LeaveEncashment.objects.get(pk=enc_id)
        self.assertIsNone(enc.cfo_approver_id)          # CFO leg never signed
        self.assertIsNotNone(enc.hr_approver_id)
        self.assertIsNotNone(enc.finance_approver_id)

    def test_normal_applicant_still_starts_at_cfo(self):
        # Regression: a non-CFO applicant's chain is unchanged.
        self.assertEqual(self._apply(days='2').json()['status'], 'pending_cfo')

    def test_reject_releases_reserved_days(self):
        before = _annual(self.profile)
        enc_id = self._apply(days='3').json()['id']
        self.assertEqual(_annual(self.profile), before - Decimal('3'))
        r = self._client(self.cfo).post(
            f'{URL}{enc_id}/reject/', {'notes': 'not now'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['status'], 'rejected')
        self.assertEqual(r.json()['rejected']['stage'], 'cfo')
        # rejected → days released
        self.assertEqual(_annual(self.profile), before)

    # ── access ───────────────────────────────────────────────────────────────
    def test_provision_register_gated(self):
        # ess applicant cannot see the salary register
        self.assertEqual(
            self._client(self.applicant).get(URL + 'provision/').status_code, 403)
        # CFO can
        self.assertEqual(
            self._client(self.cfo).get(URL + 'provision/').status_code, 200)

    def test_ess_sees_only_own(self):
        self._apply(days='1')                       # applicant's own
        self._client(self.cfo)                       # (no-op)
        # another employee applies
        emp2 = Employee.objects.create(
            employee_number='E302', full_name='Other Emp', company=self.company,
            status='active', hire_date=_HIRED, user=self.other, email='other@test.example')
        HRISProfile.objects.create(employee=emp2)
        LeaveOpeningBalance.objects.create(
            profile=emp2.hris_profile, leave_type_code='annual',
            entitlement_days=Decimal('20'), opening_balance_days=Decimal('20'),
            accrued_days=Decimal('20'), as_at_date=timezone.localdate())
        PayslipLine.objects.create(
            payslip=Payslip.objects.create(
                employee=emp2, period=PayrollPeriod.objects.get(period_name='2099-01'),
                company=self.company, status=Payslip.Status.DRAFT),
            component=PayslipComponent.objects.get(code='BASIC'),
            amount=Decimal('5000.00'))
        self._apply(days='1', user=self.other)
        # applicant sees only their own row; CFO sees both
        self.assertEqual(len(self._client(self.applicant).get(URL).json()['requests']), 1)
        self.assertEqual(len(self._client(self.cfo).get(URL).json()['requests']), 2)
