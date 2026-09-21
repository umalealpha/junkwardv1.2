"""core/tests/test_team_glance.py — the phone Team tab's one-glance endpoint.

GET /api/v1/team/glance/ must: give a user with no reports an EMPTY 200 (it is
the "do I manage people" probe); show a manager ONLY their own reports; carry
no PII beyond name / job title / status; and mark on_leave_today from an
APPROVED leave spanning today (never from a pending one).

Run: python manage.py test core.tests.test_team_glance
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from core.models import Company, OmniTask, OnlinePresence
from hris.models import HRISProfile, LeaveRequest, LeaveType, WorkdayJustification
from payroll.models import Employee

User = get_user_model()

ALLOWED_KEYS = {'employee_id', 'user_id', 'name', 'job_title', 'on_leave_today',
                'leave_type', 'online', 'dark_days_7', 'short_days_7', 'awaiting_review_7',
                'open_tasks', 'overdue_tasks'}
# counts: the four original keys stay (older app builds read them) plus the
# plain-English split the card needs — PEOPLE with overdue work, and the
# explanations waiting on THIS manager (CFO 2026-09-20: "15 overdue" was tasks
# while the seven names were people; "dark" hid the manager's own backlog).
COUNT_KEYS = {'in', 'on_leave', 'dark', 'overdue', 'overdue_people', 'awaiting_me'}


class TeamGlanceTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.co = Company.objects.create(code='TG', name='Team Glance Co.')
        cls.url = reverse('v1-team-glance')

        cls.mgr_user = User.objects.create_user('tg_mgr', email='tg_mgr@alphadirect.co.bw')
        cls.other_user = User.objects.create_user('tg_other', email='tg_other@alphadirect.co.bw')
        cls.lonely_user = User.objects.create_user('tg_lonely', email='tg_lonely@alphadirect.co.bw')
        cls.rep_user = User.objects.create_user('tg_rep', email='tg_rep@alphadirect.co.bw')

        cls.mgr = Employee.objects.create(
            employee_number='TG-M1', full_name='Glance Manager', company=cls.co,
            email='tg_mgr@alphadirect.co.bw', user=cls.mgr_user, job_title='Manager')
        cls.other = Employee.objects.create(
            employee_number='TG-O1', full_name='Other Manager', company=cls.co,
            email='tg_other@alphadirect.co.bw', user=cls.other_user)
        Employee.objects.create(
            employee_number='TG-L1', full_name='Lonely Staffer', company=cls.co,
            email='tg_lonely@alphadirect.co.bw', user=cls.lonely_user)

        cls.rep = Employee.objects.create(
            employee_number='TG-R1', full_name='Report One', company=cls.co,
            email='tg_rep@alphadirect.co.bw', user=cls.rep_user,
            job_title='Claims Officer', national_id='123456789',
            bank_account_no='62012345678')
        cls.rep2 = Employee.objects.create(
            employee_number='TG-R2', full_name='Report Two', company=cls.co,
            job_title='Underwriter')
        cls.theirs = Employee.objects.create(
            employee_number='TG-X1', full_name='Someone Elses Report', company=cls.co)
        cls.gone = Employee.objects.create(
            employee_number='TG-G1', full_name='Left Already', company=cls.co,
            status=Employee.Status.TERMINATED)
        cls.bot = Employee.objects.create(
            employee_number='TG-B1', full_name='QA Robot', company=cls.co,
            is_test_record=True)

        HRISProfile.objects.create(employee=cls.mgr)
        cls.rep_profile = HRISProfile.objects.create(employee=cls.rep, manager=cls.mgr)
        cls.rep2_profile = HRISProfile.objects.create(employee=cls.rep2, manager=cls.mgr)
        HRISProfile.objects.create(employee=cls.theirs, manager=cls.other)
        HRISProfile.objects.create(employee=cls.gone, manager=cls.mgr)
        HRISProfile.objects.create(employee=cls.bot, manager=cls.mgr)

        cls.annual = LeaveType.objects.create(code='TG_ANNUAL', name='Annual leave')

    def _get(self, user):
        self.client.force_authenticate(user)
        return self.client.get(self.url)

    def _names(self, res):
        return sorted(r['name'] for r in res.json()['reports'])

    # -- no reports ----------------------------------------------------------
    def test_user_with_no_reports_gets_empty_200(self):
        res = self._get(self.lonely_user)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['reports'], [])
        self.assertEqual(res.json()['counts'], {'in': 0, 'on_leave': 0, 'dark': 0, 'overdue': 0,
                                                'overdue_people': 0, 'awaiting_me': 0})
        self.assertIn('is_workday', res.json())
        self.assertIn('day_name', res.json())

    def test_user_with_no_employee_record_gets_empty_200(self):
        ghost = User.objects.create_user('tg_ghost', email='tg_ghost@alphadirect.co.bw')
        res = self._get(ghost)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()['reports'], [])

    def test_anonymous_is_refused(self):
        res = self.client.get(self.url)
        self.assertIn(res.status_code, (401, 403))

    # -- scope ----------------------------------------------------------------
    def test_manager_sees_only_own_active_real_reports(self):
        res = self._get(self.mgr_user)
        self.assertEqual(res.status_code, 200)
        # Their own two; NOT the other manager's person, NOT the leaver, NOT the QA row.
        self.assertEqual(self._names(res), ['Report One', 'Report Two'])

    def test_other_manager_sees_only_theirs(self):
        res = self._get(self.other_user)
        self.assertEqual(self._names(res), ['Someone Elses Report'])

    # -- PII ------------------------------------------------------------------
    def test_report_rows_carry_no_pii(self):
        res = self._get(self.mgr_user)
        body = res.content.decode()
        for row in res.json()['reports']:
            self.assertEqual(set(row.keys()), ALLOWED_KEYS)
        self.assertNotIn('123456789', body)        # Omang
        self.assertNotIn('62012345678', body)      # bank account
        self.assertNotIn('salary', body)
        self.assertNotIn('tg_rep@alphadirect', body)
        rows = {r['name']: r for r in res.json()['reports']}
        self.assertEqual(rows['Report One']['user_id'], self.rep_user.pk)
        self.assertIsNone(rows['Report Two']['user_id'])

    # -- leave today ----------------------------------------------------------
    def test_on_leave_today_from_approved_leave_spanning_today(self):
        today = timezone.localdate()
        LeaveRequest.objects.create(
            profile=self.rep_profile, leave_type=self.annual,
            start_date=today - timedelta(days=1), end_date=today + timedelta(days=1),
            days=Decimal('3'), status=LeaveRequest.Status.APPROVED)
        # A merely PENDING request on the second report must NOT count.
        LeaveRequest.objects.create(
            profile=self.rep2_profile, leave_type=self.annual,
            start_date=today, end_date=today, days=Decimal('1'),
            status=LeaveRequest.Status.PENDING)
        res = self._get(self.mgr_user)
        rows = {r['name']: r for r in res.json()['reports']}
        self.assertTrue(rows['Report One']['on_leave_today'])
        self.assertEqual(rows['Report One']['leave_type'], 'Annual leave')
        self.assertFalse(rows['Report Two']['on_leave_today'])
        self.assertEqual(res.json()['counts']['on_leave'], 1)

    def test_leave_that_ended_yesterday_does_not_count(self):
        today = timezone.localdate()
        LeaveRequest.objects.create(
            profile=self.rep_profile, leave_type=self.annual,
            start_date=today - timedelta(days=3), end_date=today - timedelta(days=1),
            days=Decimal('3'), status=LeaveRequest.Status.APPROVED)
        res = self._get(self.mgr_user)
        rows = {r['name']: r for r in res.json()['reports']}
        self.assertFalse(rows['Report One']['on_leave_today'])

    # -- online / dark / overdue ---------------------------------------------
    def test_online_dark_and_overdue_counts(self):
        today = timezone.localdate()
        OnlinePresence.objects.create(user=self.rep_user, last_seen=timezone.now())
        for back in (1, 2):
            WorkdayJustification.objects.create(
                profile=self.rep_profile, work_date=today - timedelta(days=back),
                required_hours=Decimal('8'), tracked_hours=Decimal('2'),
                status=WorkdayJustification.Status.UNJUSTIFIED)
        # An accounted-for day and an old one must not count.
        WorkdayJustification.objects.create(
            profile=self.rep_profile, work_date=today - timedelta(days=3),
            required_hours=Decimal('8'), tracked_hours=Decimal('8'),
            status=WorkdayJustification.Status.MET)
        WorkdayJustification.objects.create(
            profile=self.rep_profile, work_date=today - timedelta(days=20),
            required_hours=Decimal('8'), tracked_hours=Decimal('0'),
            status=WorkdayJustification.Status.UNJUSTIFIED)
        OmniTask.objects.create(assigner=self.mgr_user, assignee=self.rep_user,
                                title='Late thing', due_at=today - timedelta(days=2))
        OmniTask.objects.create(assigner=self.mgr_user, assignee=self.rep_user,
                                title='Future thing', due_at=today + timedelta(days=2))
        OmniTask.objects.create(assigner=self.mgr_user, assignee=self.rep_user,
                                title='Approve payment X', due_at=today - timedelta(days=2))
        res = self._get(self.mgr_user)
        rows = {r['name']: r for r in res.json()['reports']}
        one = rows['Report One']
        self.assertTrue(one['online'])
        self.assertEqual(one['dark_days_7'], 2)
        self.assertEqual(one['open_tasks'], 2)       # the approval item is not a task
        self.assertEqual(one['overdue_tasks'], 1)
        self.assertFalse(rows['Report Two']['online'])
        counts = res.json()['counts']
        self.assertEqual(counts, {'in': 1, 'on_leave': 0, 'dark': 1, 'overdue': 1,
                                  'overdue_people': 1, 'awaiting_me': 0})

    # -- the plain-English split (CFO 2026-09-20) ----------------------------
    def test_short_days_are_split_from_explanations_waiting_on_the_manager(self):
        today = timezone.localdate()
        # Report One: 2 unanswered short days + 1 explained (waiting on the manager).
        for back, status in ((1, WorkdayJustification.Status.UNJUSTIFIED),
                             (2, WorkdayJustification.Status.PENDING),
                             (3, WorkdayJustification.Status.EXPLAINED)):
            WorkdayJustification.objects.create(
                profile=self.rep_profile, work_date=today - timedelta(days=back),
                required_hours=Decimal('6.5'), tracked_hours=Decimal('1'), status=status)
        # Report Two: only an explanation waiting — NOT a short day nobody answered.
        WorkdayJustification.objects.create(
            profile=self.rep2_profile, work_date=today - timedelta(days=1),
            required_hours=Decimal('6.5'), tracked_hours=Decimal('2'),
            status=WorkdayJustification.Status.EXPLAINED)
        res = self._get(self.mgr_user)
        rows = {r['name']: r for r in res.json()['reports']}
        self.assertEqual(rows['Report One']['short_days_7'], 2)
        self.assertEqual(rows['Report One']['awaiting_review_7'], 1)
        self.assertEqual(rows['Report One']['dark_days_7'], 3)       # unchanged meaning: the sum
        self.assertEqual(rows['Report Two']['short_days_7'], 0)
        self.assertEqual(rows['Report Two']['awaiting_review_7'], 1)
        counts = res.json()['counts']
        self.assertEqual(counts['dark'], 2)
        self.assertEqual(counts['awaiting_me'], 2)

    def test_overdue_counts_tasks_and_overdue_people_counts_people(self):
        today = timezone.localdate()
        for n in range(3):
            OmniTask.objects.create(assigner=self.mgr_user, assignee=self.rep_user,
                                    title=f'Late {n}', due_at=today - timedelta(days=1))
        res = self._get(self.mgr_user)
        counts = res.json()['counts']
        self.assertEqual(counts['overdue'], 3)          # tasks
        self.assertEqual(counts['overdue_people'], 1)   # people
        self.assertEqual(set(counts.keys()), COUNT_KEYS)

    def test_is_workday_is_false_on_a_public_holiday_and_on_sunday(self):
        from hris.models import PublicHoliday
        today = timezone.localdate()
        res = self._get(self.mgr_user)
        expected = today.weekday() != 6
        self.assertEqual(res.json()['is_workday'], expected)
        self.assertEqual(res.json()['day_name'], today.strftime('%A'))
        PublicHoliday.objects.create(country_code='BW', holiday_date=today, name='Test Day')
        res = self._get(self.mgr_user)
        self.assertFalse(res.json()['is_workday'])
