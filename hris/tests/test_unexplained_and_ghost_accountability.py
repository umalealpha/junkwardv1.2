"""Accountability in the daily manager email (CFO 2026-07-30).

Two demands, two mechanisms:

  1. "nail the people who don't track … you have the manager and the department,
     ask the manager why" — unexplained absence is grouped under the MANAGER who
     owes the answer, with each person's department against their name.
  2. "ghost employees in payroll and not in time doctor, put a task in omni …
     3 days after HR doesn't respond or fix, make fun of HR also" — every ghost
     becomes an HR task with a deadline, and HR is named in the same email once
     the deadline passes.
"""
from __future__ import annotations

import datetime
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from core.models import OmniTask
from hris import exceptions_report as er
from hris import ghost_payroll
from hris.models import HRISProfile
from payroll.models import Employee

DAY = datetime.date(2026, 7, 29)          # a Wednesday


class _Matcher:
    """Minimal stand-in for TDMatcher — only employee_for_uid is read."""

    def __init__(self, mapping):
        self.employee_for_uid = mapping
        self.unmatched_employees = []


class UnexplainedByManagerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.mgr = Employee.objects.create(full_name='Kabo Manager', department='Claims',
                                          employee_number='T-MGR1')
        cls.other_mgr = Employee.objects.create(full_name='Neo Manager', department='Finance',
                                                employee_number='T-MGR2')
        cls.a = Employee.objects.create(full_name='Absent Aaron', department='Claims',
                                        employee_number='T-A')
        cls.b = Employee.objects.create(full_name='Silent Sara', department='Claims',
                                        employee_number='T-B')
        cls.c = Employee.objects.create(full_name='Quiet Kabelo', department='Finance',
                                        employee_number='T-C')
        cls.orphan = Employee.objects.create(full_name='Nobody Owns Me', department='',
                                             employee_number='T-ORPH')
        HRISProfile.objects.create(employee=cls.a, manager=cls.mgr)
        HRISProfile.objects.create(employee=cls.b, manager=cls.mgr)
        HRISProfile.objects.create(employee=cls.c, manager=cls.other_mgr)
        HRISProfile.objects.create(employee=cls.orphan)          # no manager on file
        cls.matcher = _Matcher({'u1': cls.a, 'u2': cls.b, 'u3': cls.c, 'u4': cls.orphan})

    def test_groups_under_the_manager_with_department_and_streak(self):
        groups = er._unexplained_by_manager(
            self.matcher,
            ['Absent Aaron', 'Silent Sara', 'Quiet Kabelo'],
            {'u1': 4, 'u2': 1, 'u3': 2},
            {'u2': DAY - datetime.timedelta(days=1)})
        self.assertEqual(list(groups), ['Kabo Manager', 'Neo Manager'])   # worst streak first
        kabo = groups['Kabo Manager']
        self.assertEqual([r['name'] for r in kabo], ['Absent Aaron', 'Silent Sara'])
        self.assertEqual(kabo[0]['days_dark'], 4)
        self.assertEqual(kabo[0]['dept'], 'Claims')
        self.assertIsNone(kabo[0]['last_tracked'])                 # never tracked in lookback
        self.assertEqual(kabo[1]['last_tracked'], 'Tue 28 Jul')

    def test_board_bucket_alone_asks_no_manager_questions(self):
        """With only the CEO unexplained there is no manager on the hook — the
        email must not print "0 managers — answer these today"."""
        ceo = Employee.objects.create(full_name='Arun Iyer', department='C-Suite',
                                      employee_number='T-CEO2')
        HRISProfile.objects.create(employee=ceo, manager=self.mgr)
        groups = er._unexplained_by_manager(_Matcher({'u9': ceo}), ['Arun Iyer'], {'u9': 6}, {})
        html = er.build_html({
            'day': DAY,
            'summary': {'tracked': 1, 'did_not_track': 1, 'roster': 79, 'total_h': 5.0,
                        'avg_h': 5.0, 'prod_h': 4.0},
            'did_not_track': ['Arun Iyer'], 'on_leave': [], 'planned': [], 'alarm': [],
            'critical': [], 'low': [], 'below6': [], 'late': [], 'unproductive': [],
            'ghosts': [], 'day_map': {}, 'momentum': {}, 'leaderboard': [], 'lb_hidden': 0,
            'focus': {}, 'shortfall': [], 'unexplained': groups})
        self.assertNotIn('answer these today', html)
        self.assertNotIn('must answer', html)
        self.assertIn('Dear Board of Alpha Direct', html)
        self.assertIn('79 people are asked to clock in', html)

    def test_person_with_no_manager_is_shown_not_dropped(self):
        groups = er._unexplained_by_manager(
            self.matcher, ['Nobody Owns Me'], {'u4': 3}, {})
        self.assertEqual(list(groups), ['No manager on file'])
        self.assertEqual(groups['No manager on file'][0]['dept'], 'no department set')

    def test_unowned_bucket_sorts_last(self):
        groups = er._unexplained_by_manager(
            self.matcher, ['Absent Aaron', 'Nobody Owns Me'], {'u1': 2, 'u4': 9}, {})
        # 'No manager on file' has the WORSE streak but must not lead the section.
        self.assertEqual(list(groups)[-1], 'No manager on file')

    def test_people_who_explained_are_never_here(self):
        """The input list is already leave/justification-filtered — anyone absent
        WITH notice must not reach a manager's accountability block."""
        groups = er._unexplained_by_manager(self.matcher, [], {}, {})
        self.assertEqual(groups, {})

    def test_executives_are_never_filed_under_a_staff_manager(self):
        """First live run filed the CEO under Unami Butale as "1 to answer for" —
        telling the CHCO to explain the CEO's tracking. That is the false
        escalation that paused the accountability crons on 28 July."""
        ceo = Employee.objects.create(full_name='Arun Iyer', department='C-Suite',
                                      employee_number='T-CEO')
        HRISProfile.objects.create(employee=ceo, manager=self.mgr)
        matcher = _Matcher({'u1': self.a, 'u9': ceo})
        groups = er._unexplained_by_manager(
            matcher, ['Absent Aaron', 'Arun Iyer'], {'u1': 1, 'u9': 6}, {})
        self.assertNotIn('Arun Iyer', [r['name'] for r in groups['Kabo Manager']])
        self.assertEqual([r['name'] for r in groups[er.EXEC_KEY]], ['Arun Iyer'])
        # The CEO must not inflate "managers must answer", and the exec bucket
        # sits last even with the worst streak.
        self.assertEqual(list(groups)[-1], er.EXEC_KEY)
        html = er.build_html({
            'day': DAY,
            'summary': {'tracked': 1, 'did_not_track': 2, 'roster': 3, 'total_h': 5.0,
                        'avg_h': 5.0, 'prod_h': 4.0},
            'did_not_track': ['Absent Aaron', 'Arun Iyer'], 'on_leave': [], 'planned': [],
            'alarm': [], 'critical': [], 'low': [], 'below6': [], 'late': [],
            'unproductive': [], 'ghosts': [], 'day_map': {}, 'momentum': {},
            'leaderboard': [], 'lb_hidden': 0, 'focus': {}, 'shortfall': [],
            'unexplained': groups})
        self.assertIn('1 manager — answer these today', html)   # Kabo only, not 2
        self.assertIn('Dear Board of Alpha Direct', html)       # CEO answers to the board
        self.assertIn('an employee cannot ask this of a CEO', html)
        self.assertNotIn('Unami', html)                         # never his item

    def test_section_renders_manager_department_and_the_questions(self):
        data = {
            'day': DAY,
            'summary': {'tracked': 2, 'did_not_track': 2, 'roster': 4, 'total_h': 9.0,
                        'avg_h': 4.5, 'prod_h': 7.0},
            'did_not_track': ['Absent Aaron', 'Silent Sara'], 'on_leave': [], 'planned': [],
            'alarm': [], 'critical': [], 'low': [], 'below6': [], 'late': [],
            'unproductive': [], 'ghosts': [], 'day_map': {},
            'momentum': {}, 'leaderboard': [], 'lb_hidden': 0, 'focus': {}, 'shortfall': [],
            'unexplained': er._unexplained_by_manager(
                self.matcher, ['Absent Aaron', 'Silent Sara'], {'u1': 4, 'u2': 1}, {}),
        }
        html = er.build_html(data)
        self.assertIn('Kabo Manager', html)                  # the manager is named
        self.assertIn('Absent Aaron', html)
        self.assertIn('(Claims)', html)                      # department against the name
        self.assertIn('4 days no Time Doctor', html)
        self.assertIn('1 manager', html)                     # singular, one manager on the hook
        self.assertIn('why was leave not applied for', html)


class GhostPayrollTaskTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cfo = User.objects.create_user('cfo', email='pganesharajah@alphadirect.co.bw')
        cls.hr = User.objects.create_user('unami', email='ubutale@alphadirect.co.bw',
                                          first_name='Unami', last_name='Butale')

    def test_one_task_per_ghost_assigned_to_hr_with_a_deadline(self):
        created, skipped = ghost_payroll.raise_ghost_tasks(['Ghost One', 'Ghost Two'], DAY)
        self.assertEqual(created, ['Ghost One', 'Ghost Two'])
        self.assertEqual(skipped, 0)
        t = OmniTask.objects.get(title__contains='Ghost One')
        self.assertEqual(t.assignee, self.hr)
        self.assertEqual(t.assigner, self.cfo)
        self.assertEqual(t.source, 'ghost_payroll')
        self.assertEqual(t.due_at, DAY + datetime.timedelta(days=3))
        self.assertEqual(t.priority, OmniTask.Priority.HIGH)
        # The task must not read as an instruction to cut pay on its own.
        self.assertIn('CFO-AUTHORISED', t.body)
        self.assertIn('tracker is the ghost', t.body)

    def test_does_not_re_raise_while_a_task_is_still_open(self):
        ghost_payroll.raise_ghost_tasks(['Ghost One'], DAY)
        created, skipped = ghost_payroll.raise_ghost_tasks(['Ghost One'], DAY + datetime.timedelta(days=1))
        self.assertEqual(created, [])
        self.assertEqual(skipped, 1)
        self.assertEqual(OmniTask.objects.filter(source='ghost_payroll').count(), 1)

    def test_a_closed_task_lets_a_returning_ghost_be_raised_again(self):
        ghost_payroll.raise_ghost_tasks(['Ghost One'], DAY)
        OmniTask.objects.filter(source='ghost_payroll').update(status=OmniTask.Status.DONE)
        created, _ = ghost_payroll.raise_ghost_tasks(['Ghost One'], DAY + datetime.timedelta(days=30))
        self.assertEqual(created, ['Ghost One'])

    def test_hr_overdue_only_after_the_deadline_passes(self):
        ghost_payroll.raise_ghost_tasks(['Ghost One'], DAY)
        due = DAY + datetime.timedelta(days=3)
        self.assertEqual(ghost_payroll.hr_overdue(due), [])          # on the day itself: not late
        rows = ghost_payroll.hr_overdue(due + datetime.timedelta(days=2))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['person'], 'Ghost One')
        self.assertEqual(rows[0]['hr'], 'Unami Butale')
        self.assertEqual(rows[0]['days_late'], 2)

    def test_a_dont_track_directive_closes_the_ghost_task(self):
        """CFO 2026-09-11 — Moemedi Mositiemang, a driver with no computer.

        HR was named five days past deadline for a man nobody expects to track.
        Once the don't-track directive is on record the question is answered.
        """
        ghost_payroll.raise_ghost_tasks(['Moemedi Mositiemang'], DAY)
        with patch.object(ghost_payroll, '_exempt_from_tracking',
                          return_value={'moemedi mositiemang'}):
            closed = ghost_payroll.resolve_recovered_ghost_tasks([], DAY, current_ghosts=[])
        self.assertEqual(closed, ['Moemedi Mositiemang'])
        t = OmniTask.objects.get(source='ghost_payroll')
        self.assertEqual(t.status, OmniTask.Status.CANCELLED)
        self.assertEqual(ghost_payroll.hr_overdue(DAY + datetime.timedelta(days=10)), [])

    def test_a_genuine_ghost_task_is_never_closed_by_the_exemption_path(self):
        ghost_payroll.raise_ghost_tasks(['Real Ghost'], DAY)
        with patch.object(ghost_payroll, '_exempt_from_tracking',
                          return_value={'moemedi mositiemang'}):
            closed = ghost_payroll.resolve_recovered_ghost_tasks([], DAY, current_ghosts=[])
        self.assertEqual(closed, [])
        self.assertEqual(OmniTask.objects.get(source='ghost_payroll').status,
                         OmniTask.Status.PENDING)

    def test_only_a_deliberate_directive_exempts_never_a_missing_payslip(self):
        """A 'no-payslip' person stays a ghost — closing that hides a real leak."""
        roster = [{'name': 'No Payslip Person', 'expected': False, 'source': 'no-payslip'},
                  {'name': 'Directed Person', 'expected': False, 'source': 'directive'},
                  {'name': 'Tracked Person', 'expected': True, 'source': 'payslip'}]
        with patch('hris.eligibility.tracking_roster', return_value=roster):
            self.assertEqual(ghost_payroll._exempt_from_tracking(), {'directed person'})

    def test_answered_task_stops_naming_hr(self):
        ghost_payroll.raise_ghost_tasks(['Ghost One'], DAY)
        OmniTask.objects.filter(source='ghost_payroll').update(status=OmniTask.Status.DONE)
        self.assertEqual(ghost_payroll.hr_overdue(DAY + datetime.timedelta(days=10)), [])

    # The ghost section (and with it the HR-overdue block) is behind
    # WORKFORCE_GHOSTS_IN_EMAIL, switched OFF on 5 Aug 2026 so a
    # mismatched-but-working person is not named to every manager. This test
    # exercises the builder with the section ON — without the override it was
    # asserting behaviour the CFO had deliberately turned off, and had been
    # failing since that change.
    @override_settings(WORKFORCE_GHOSTS_IN_EMAIL=True)
    def test_email_names_hr_when_overdue(self):
        data = {
            'day': DAY, 'summary': {'tracked': 1, 'did_not_track': 0, 'roster': 1,
                                    'total_h': 6.0, 'avg_h': 6.0, 'prod_h': 5.0},
            'did_not_track': [], 'on_leave': [], 'planned': [], 'alarm': [], 'critical': [],
            'low': [], 'below6': [], 'late': [], 'unproductive': [], 'day_map': {},
            'ghosts': ['Ghost One'], 'momentum': {}, 'leaderboard': [], 'lb_hidden': 0,
            'focus': {}, 'shortfall': [], 'unexplained': {},
            'ghost_tasks_new': ['Ghost One'], 'ghost_tasks_open': 0,
            'hr_overdue': [{'hr': 'Unami Butale', 'person': 'Ghost One',
                            'due': DAY, 'days_late': 4}],
        }
        html = er.build_html(data)
        self.assertIn('HR has not answered', html)
        self.assertIn('Unami Butale', html)
        self.assertIn('4 days past HR', html)
        self.assertIn('ghosts are winning 4–0', html)
        self.assertIn('new task(s) raised with HR', html)

    def test_no_hr_section_when_nothing_is_overdue(self):
        data = {
            'day': DAY, 'summary': {'tracked': 1, 'did_not_track': 0, 'roster': 1,
                                    'total_h': 6.0, 'avg_h': 6.0, 'prod_h': 5.0},
            'did_not_track': [], 'on_leave': [], 'planned': [], 'alarm': [], 'critical': [],
            'low': [], 'below6': [], 'late': [], 'unproductive': [], 'day_map': {},
            'ghosts': [], 'momentum': {}, 'leaderboard': [], 'lb_hidden': 0,
            'focus': {}, 'shortfall': [], 'unexplained': {}, 'hr_overdue': [],
        }
        self.assertNotIn('HR has not answered', er.build_html(data))


class HrChasingHasItsOwnSwitchTests(TestCase):
    """Naming HR must survive the ghost list being switched off.

    Names in THIS class are invented. The fixtures above still carry a real
    colleague's name: they resolve it from seeded HR data, so renaming them
    breaks the assertions and is a separate job, not something to sweep into
    this change.

    On 5 Aug 2026 the CFO switched the ghost LIST off so a mismatched-but-working
    person is not named to every manager. That was right — but the HR-overdue
    block sat inside the same flag, so it silently stopped HR being chased for
    unanswered tasks, which was the CFO's own request of 30 July. A side effect,
    not a decision. Separated on 9 Aug; this pins the combination.
    """

    DATA = {
        'day': DAY,
        'summary': {'tracked': 1, 'did_not_track': 0, 'roster': 1,
                    'total_h': 6.0, 'avg_h': 6.0, 'prod_h': 5.0},
        'did_not_track': [], 'on_leave': [], 'planned': [], 'alarm': [],
        'critical': [], 'low': [], 'below6': [], 'late': [], 'unproductive': [],
        'day_map': {}, 'ghosts': ['Ghost One'], 'momentum': {}, 'leaderboard': [],
        'lb_hidden': 0, 'focus': {}, 'shortfall': [], 'unexplained': {},
        'ghost_tasks_new': ['Ghost One'], 'ghost_tasks_open': 0,
        'hr_overdue': [{'hr': 'Thato Fictional', 'person': 'Ghost One',
                        'due': DAY, 'days_late': 4}],
    }

    @override_settings(WORKFORCE_GHOSTS_IN_EMAIL=False,
                       WORKFORCE_HR_OVERDUE_IN_EMAIL=True)
    def test_hr_is_still_named_when_the_ghost_list_is_off(self):
        html = er.build_html(self.DATA)
        self.assertIn('HR has not answered', html)
        self.assertIn('Thato Fictional', html)
        # ...and the STAFF member is NOT named to every manager, which is the
        # whole reason the ghost list was switched off.
        self.assertNotIn('Payroll ghost list', html)

    @override_settings(WORKFORCE_GHOSTS_IN_EMAIL=False,
                       WORKFORCE_HR_OVERDUE_IN_EMAIL=False)
    def test_both_off_names_nobody(self):
        html = er.build_html(self.DATA)
        self.assertNotIn('HR has not answered', html)
        self.assertNotIn('Payroll ghost list', html)

    @override_settings(WORKFORCE_GHOSTS_IN_EMAIL=True,
                       WORKFORCE_HR_OVERDUE_IN_EMAIL=False)
    def test_the_two_switches_are_independent_both_ways(self):
        # The first version used elif, so the HR switch only applied when the
        # ghost list was OFF — turning the ghost list back on would have named
        # HR regardless of its own setting. Caught by the review panel.
        html = er.build_html(self.DATA)
        self.assertIn('Payroll ghost list', html)
        self.assertNotIn('HR has not answered', html)

    @override_settings(WORKFORCE_GHOSTS_IN_EMAIL=True,
                       WORKFORCE_HR_OVERDUE_IN_EMAIL=True)
    def test_both_on_shows_both(self):
        html = er.build_html(self.DATA)
        self.assertIn('Payroll ghost list', html)
        self.assertIn('HR has not answered', html)

    @override_settings(WORKFORCE_GHOSTS_IN_EMAIL=False,
                       WORKFORCE_HR_OVERDUE_IN_EMAIL=True)
    def test_nothing_overdue_means_no_section_at_all(self):
        data = dict(self.DATA, hr_overdue=[])
        self.assertNotIn('HR has not answered', er.build_html(data))
