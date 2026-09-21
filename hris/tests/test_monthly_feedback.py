"""Monthly manager-feedback layer — targets, auto/actual logic, decision-panel
draft, and the monthly cycle (prompt idempotency + never-stomp-human + flag gate).

CFO directive 2026-07-20. Exercises the real models + command (no mocks).
Mirrors hris/tests/test_monthly_performance.py setup conventions.
"""
import datetime as dt
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings

from core.models import Company, OmniTask
from hris.models import HRISProfile
from hris.performance_feedback_models import MonthlyCheckIn, PerformanceCheckRating
from hris.performance_target_models import PerformanceTarget, PerformanceTargetResult
from hris.perf_target_source import is_achieved
from hris.perf_panel import month_bounds, draft_feedback
from hris.perf_monthly_views import _clean_period
from payroll.models import Employee


class PureLogicTest(SimpleTestCase):
    def test_is_achieved(self):
        t = PerformanceTarget(target_value=Decimal('30000'))
        self.assertTrue(is_achieved(t, Decimal('31000')))
        self.assertTrue(is_achieved(t, Decimal('30000')))   # meets == hit
        self.assertFalse(is_achieved(t, Decimal('29999')))
        self.assertIsNone(is_achieved(t, None))

    def test_clean_period_bounds(self):
        self.assertEqual(_clean_period(2026, 7, None), (2026, 7))
        self.assertIsNone(_clean_period(2026, 13, None))    # bad month
        self.assertIsNone(_clean_period(99999, 1, None))    # out of range
        self.assertIsNone(_clean_period('x', 1, None))      # non-int
        self.assertEqual(_clean_period(None, None, (2026, 7)), (2026, 7))

    def test_month_bounds(self):
        first, last, s, n = month_bounds(2026, 2)
        self.assertEqual(first, dt.date(2026, 2, 1))
        self.assertEqual(last, dt.date(2026, 2, 28))
        self.assertEqual((n - s).days, 28)

    def test_draft_feedback_from_facts(self):
        panel = {'tasks_completed': 7, 'tasks_on_time': 6, 'tasks_on_time_pct': 86, 'absent_days': 1}
        targets = [{'metric': 'New sales', 'target_value': 30000, 'unit': 'BWP',
                    'actual_value': 28500, 'achieved': False}]
        d = draft_feedback(panel, targets)
        self.assertIn('6/7', d['strengths'])
        self.assertIn('Missed target', d['concerns'])
        self.assertIn('BWP 30,000', d['support_provided'])


class TargetModelTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.co = Company.objects.create(code='ADIA', name='ADIC (test)')
        cls.emp = Employee.objects.create(employee_number='E1', full_name='Gosego Makone', company=cls.co)
        cls.profile = HRISProfile.objects.create(employee=cls.emp)

    def test_create_and_result_unique(self):
        t = PerformanceTarget.objects.create(profile=self.profile, metric='New sales',
                                             target_value=Decimal('30000'), unit='BWP')
        PerformanceTargetResult.objects.create(profile=self.profile, target=t,
                                               period_year=2026, period_month=7,
                                               target_value=t.target_value)
        # unique per target+period
        from django.db import IntegrityError, transaction
        with self.assertRaises(IntegrityError), transaction.atomic():
            PerformanceTargetResult.objects.create(profile=self.profile, target=t,
                                                   period_year=2026, period_month=7,
                                                   target_value=t.target_value)


@override_settings(ELRA_PERF_ENABLED=True)
class CycleCommandTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.co = Company.objects.create(code='ADIA', name='ADIC (test)')
        cls.mgr_user = User.objects.create(username='medu', email='mtlagae@alphadirect.co.bw')
        cls.mgr = Employee.objects.create(employee_number='M1', full_name='Medu T', company=cls.co, user=cls.mgr_user)
        cls.rep_emp = Employee.objects.create(employee_number='R1', full_name='Gosego Makone', company=cls.co)
        cls.rep = HRISProfile.objects.create(employee=cls.rep_emp, manager=cls.mgr)

    def _run(self):
        call_command('monthly_feedback_cycle', '--commit', '--year', '2026', '--month', '6')

    def test_prompts_manager_and_is_idempotent(self):
        self._run()
        tasks = OmniTask.objects.filter(assignee=self.mgr_user, source='monthly_feedback:2026-06')
        self.assertEqual(tasks.count(), 1)
        self._run()   # re-run must not duplicate
        self.assertEqual(OmniTask.objects.filter(assignee=self.mgr_user, source='monthly_feedback:2026-06').count(), 1)

    def test_no_prompt_when_checkin_exists(self):
        MonthlyCheckIn.objects.create(profile=self.rep, period_year=2026, period_month=6,
                                      overall_rating=PerformanceCheckRating.MEETS,
                                      conversation_date=dt.date(2026, 6, 15))
        self._run()
        self.assertFalse(OmniTask.objects.filter(
            assignee=self.mgr_user, source='monthly_feedback:2026-06').exists())

    def test_does_not_stomp_human_confirmed_result(self):
        t = PerformanceTarget.objects.create(profile=self.rep, metric='New sales',
                                             target_value=Decimal('30000'), source='health_quotes')
        res = PerformanceTargetResult.objects.create(
            profile=self.rep, target=t, period_year=2026, period_month=6,
            target_value=Decimal('30000'), actual_value=Decimal('31000'),
            achieved=True, recorded_by=self.mgr_user, source_used='manual')
        self._run()
        res.refresh_from_db()
        self.assertEqual(res.actual_value, Decimal('31000'))   # human value preserved
        self.assertTrue(res.achieved)
        self.assertEqual(res.recorded_by_id, self.mgr_user.id)

    @override_settings(ELRA_PERF_ENABLED=False)
    def test_gated_off_does_nothing(self):
        self._run()
        self.assertFalse(OmniTask.objects.filter(source='monthly_feedback:2026-06').exists())
