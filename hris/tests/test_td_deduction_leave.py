"""
hris/tests/test_td_deduction_leave.py

Time Doctor Deduction leave type (Unami Butale, HR, 2026-08-25): staff whose
tracked hours fall short must APPLY via Omni stating this reason, so the shortfall
is recorded rather than a silent payroll adjustment.

Proves the type is seeded, is UNPAID, appears in the staff-facing dropdown, and —
critically — does NOT leak into the accrual/balance engine (it is not paid leave
and must not earn or consume a balance).
"""
from django.core.management import call_command
from django.test import TestCase

from hris.models import LeaveType


class TdDeductionLeaveTypeTest(TestCase):

    def test_the_migration_seeded_it(self):
        """0075 runs on migrate, so it is present without a manual command."""
        lt = LeaveType.objects.filter(code='td_deduct').first()
        self.assertIsNotNone(lt, 'td_deduct leave type not seeded by the migration')
        self.assertTrue(lt.is_active)

    def test_it_is_unpaid(self):
        """A deduction is not paid leave."""
        lt = LeaveType.objects.get(code='td_deduct')
        self.assertFalse(lt.is_paid)
        self.assertEqual(lt.paid_pct, 0)
        self.assertEqual(lt.default_annual_days, 0)

    def test_it_carries_no_accrual_or_cert(self):
        lt = LeaveType.objects.get(code='td_deduct')
        self.assertEqual(lt.carry_over_cap_days, 0)
        self.assertEqual(lt.max_carry_over_years, 0)
        self.assertFalse(lt.requires_medical_cert)

    def test_it_is_a_selectable_leave_type_in_the_apply_flow(self):
        """CFO 2026-08-25: employees self-apply, same pattern as every other
        leave type — so it MUST be in the rules the apply form offers and
        validates against."""
        from hris.feature_views import get_leave_rules
        self.assertIn('td_deduct', get_leave_rules(),
                      'staff cannot select td_deduct if it is not in the rules')

    def test_the_rule_is_unpaid_and_zero_entitlement(self):
        from hris.feature_views import get_leave_rules
        r = get_leave_rules()['td_deduct']
        self.assertEqual(r['paid_pct'], 0)
        self.assertEqual(r['days'], 0)

    def test_the_seeded_code_is_lowercase_to_match_the_apply_path(self):
        """apply_leave lowercases the type and get_or_creates a LeaveType. If the
        seeded row were UPPERCASE it would not match, and a DUPLICATE row would be
        auto-created with the model default paid_pct=100 — silently making the
        deduction PAID. The seed code must be the lowercase the apply path uses."""
        self.assertTrue(LeaveType.objects.filter(code='td_deduct').exists())
        self.assertFalse(LeaveType.objects.filter(code='TD_DEDUCT').exists())

    def test_it_never_auto_creates_a_paid_duplicate_on_apply(self):
        """The whole point of seeding it: an application must reuse the unpaid
        seeded row, not spawn a paid one. Simulate the apply-path get_or_create."""
        before = LeaveType.objects.filter(code__iexact='td_deduct').count()
        lt, made = LeaveType.objects.get_or_create(
            code='td_deduct',
            defaults={'name': 'Td_deduct Leave', 'default_annual_days': 0})
        self.assertFalse(made, 'apply-path get_or_create created a duplicate row')
        self.assertFalse(lt.is_paid, 'the reused row must be the unpaid seeded one')
        self.assertEqual(LeaveType.objects.filter(code__iexact='td_deduct').count(),
                         before)

    def test_it_shows_no_phantom_balance_card(self):
        """A zero-entitlement type produces a 0/0d balance via the STANDARD
        builder — never a negative or a crash, however many deductions are
        recorded (available = max(0, accrued - used))."""
        from hris.leave_balance import balances_for_profile
        from hris.models import HRISProfile
        from payroll.models import Employee
        from core.models import Company
        co = Company.objects.create(code='TDX', name='TD Test Co')
        emp = Employee.objects.create(employee_number='TD1', full_name='Td Tester',
                                      job_title='Clerk', company=co)
        prof = HRISProfile.objects.create(employee=emp)
        td = [b for b in balances_for_profile(prof) if b['code'] == 'td_deduct']
        self.assertTrue(td, 'td_deduct should render a balance card like the others')
        self.assertEqual(td[0]['days'], 0)
        self.assertGreaterEqual(td[0]['available'], 0)

    def test_the_seed_command_is_idempotent(self):
        """Re-running the seeder must not duplicate or change the row."""
        before = LeaveType.objects.filter(code='td_deduct').count()
        call_command('seed_leave_types')
        self.assertEqual(LeaveType.objects.filter(code='td_deduct').count(), before)
        lt = LeaveType.objects.get(code='td_deduct')
        self.assertFalse(lt.is_paid)

    def test_the_real_apply_endpoint_accepts_td_deduct_as_a_valid_type(self):
        """H71: drive the ACTUAL apply_leave flow, not a simulation. An unknown
        type is rejected at the type-validation gate; td_deduct passes it (and
        then hits the standard downstream checks every leave type hits). Proves
        it is genuinely selectable in the self-service flow."""
        from django.contrib.auth.models import User
        from rest_framework.test import APIClient
        from core.models import Company, UserProfile
        from payroll.models import Employee
        from hris.models import HRISProfile

        co = Company.objects.create(code='TDA', name='TD Apply Co')
        u = User.objects.create_user('td.applicant', email='a@x.co', password='x')
        UserProfile.objects.create(user=u, title=UserProfile.Title.ACCOUNTANT,
                                   is_active=True)
        emp = Employee.objects.create(employee_number='TDA1', full_name='Applicant',
                                      job_title='Clerk', company=co, user=u)
        HRISProfile.objects.create(employee=emp)

        c = APIClient()
        c.force_authenticate(user=u)
        common = {'start_date': '2026-09-01', 'end_date': '2026-09-01',
                  'start_day_type': 'full', 'end_day_type': 'full'}

        unknown = c.post('/hris/api/leave-requests/',
                         {**common, 'type': 'nonsense_xyz'})
        self.assertIn('Unknown leave type', unknown.data.get('detail', ''),
                      'the gate must reject an unknown type')

        td = c.post('/hris/api/leave-requests/', {**common, 'type': 'td_deduct'})
        # It must NOT be rejected as unknown — it passed the type gate. It may
        # still stop at the standard manager/approver check that EVERY leave type
        # hits, which is not a td_deduct concern.
        self.assertNotIn('Unknown leave type', str(td.data),
                         'td_deduct must be accepted as a valid leave type')
