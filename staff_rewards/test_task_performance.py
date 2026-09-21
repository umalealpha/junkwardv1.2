"""Task performance -> Staff Rewards score (CFO directive 2026-07-13).

Covers award_task_performance (service) + the TaskFeedbackView hook:
  * Done earns points into Business Impact,
  * Partial earns a completion-scaled share,
  * a first Not-done earns nothing (never negative),
  * flipping Done -> Not-done forfeits exactly what it earned,
  * re-saving the same decision is idempotent (no double-count),
  * a user with no payroll record is a safe no-op,
  * Employee resolves by linked user OR by email.
"""
from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework.test import APITestCase

from core.models import OmniTask
from payroll.models import Employee
from staff_rewards.models import StaffPointsAccount, StaffPointsTransaction
from staff_rewards.points_rules import POINTS_RULES
from staff_rewards.service import award_task_performance

DONE_PTS = POINTS_RULES['task_performance']['done']          # 40 (placeholder)


class AwardTaskPerformanceTests(APITestCase):
    def setUp(self):
        self.manager = User.objects.create_user('mgr', password='x')
        self.staff = User.objects.create_user(
            'staff', email='worker@alphadirect.co.bw', password='x')
        self.emp = Employee.objects.create(
            employee_number='E-PERF-1', full_name='Worker One',
            email='worker@alphadirect.co.bw', user=self.staff)
        self.task = OmniTask.objects.create(
            assigner=self.manager, assignee=self.staff, title='Ship the report')

    def _acct(self):
        return StaffPointsAccount.objects.get(employee=self.emp)

    # ---- service-level ----------------------------------------------------

    def test_done_credits_business_impact(self):
        delta = award_task_performance(self.task, 'done')
        self.assertEqual(delta, DONE_PTS)
        a = self._acct()
        self.assertEqual(a.points_balance, DONE_PTS)
        self.assertEqual(a.business_impact_points, DONE_PTS)
        self.assertEqual(a.innovation_points, 0)
        self.task.refresh_from_db()
        self.assertEqual(self.task.performance_points_awarded, DONE_PTS)
        tx = StaffPointsTransaction.objects.get(account=a)
        self.assertEqual(tx.points, DONE_PTS)
        self.assertEqual(tx.pillar, 'business_impact')
        self.assertEqual(tx.kind, StaffPointsTransaction.Kind.ADJUST)

    def test_partial_scales_by_completion(self):
        delta = award_task_performance(self.task, 'partial', completion_pct=50)
        self.assertEqual(delta, round(DONE_PTS * 0.5))
        self.assertEqual(self._acct().business_impact_points, round(DONE_PTS * 0.5))

    def test_first_not_done_earns_nothing_no_negative(self):
        delta = award_task_performance(self.task, 'not_done')
        self.assertIsNone(delta)                       # target 0 == prior 0
        self.assertFalse(StaffPointsAccount.objects
                         .filter(employee=self.emp, points_balance__lt=0).exists())

    def test_flip_done_to_not_done_forfeits(self):
        award_task_performance(self.task, 'done')
        self.task.refresh_from_db()
        delta = award_task_performance(self.task, 'not_done')
        self.assertEqual(delta, -DONE_PTS)
        a = self._acct()
        self.assertEqual(a.points_balance, 0)
        self.assertEqual(a.business_impact_points, 0)
        self.assertEqual(StaffPointsTransaction.objects.filter(account=a).count(), 2)

    def test_re_award_same_decision_is_idempotent(self):
        award_task_performance(self.task, 'done')
        self.task.refresh_from_db()
        delta = award_task_performance(self.task, 'done')
        self.assertIsNone(delta)                       # no second credit
        self.assertEqual(self._acct().points_balance, DONE_PTS)
        self.assertEqual(StaffPointsTransaction.objects.count(), 1)

    def test_no_payroll_record_is_safe_noop(self):
        loner = User.objects.create_user('loner', password='x')
        t = OmniTask.objects.create(
            assigner=self.manager, assignee=loner, title='Orphan task')
        self.assertIsNone(award_task_performance(t, 'done'))
        self.assertEqual(StaffPointsAccount.objects.count(), 0)

    def test_resolves_employee_by_email_when_no_linked_user(self):
        u = User.objects.create_user('bymail', email='m@x.bw', password='x')
        Employee.objects.create(
            employee_number='E-PERF-2', full_name='By Mail', email='m@x.bw')
        t = OmniTask.objects.create(
            assigner=self.manager, assignee=u, title='Email match')
        self.assertEqual(award_task_performance(t, 'done'), DONE_PTS)

    # ---- endpoint wiring (TaskFeedbackView hook) --------------------------

    def test_feedback_endpoint_awards_and_shows_in_recent(self):
        self.client.force_authenticate(user=self.manager)
        url = reverse('v1-taskboard-feedback', args=[self.task.id])
        r = self.client.post(url, {'body': 'well done', 'status': 'done'},
                             format='json')
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self._acct().business_impact_points, DONE_PTS)
        # appears in the dashboard's Business Impact "recent" list
        self.client.force_authenticate(user=self.staff)
        dash = self.client.get(reverse('v1-staff-rewards-dashboard'))
        self.assertEqual(dash.status_code, 200)
        bi = next(p for p in dash.data['pillars'] if p['key'] == 'business_impact')
        self.assertTrue(any('Task performance' in a['detail'] for a in bi['recent']))
