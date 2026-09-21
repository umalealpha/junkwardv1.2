"""The Transformation Board must be fair before it is useful.

This board puts a number next to a department and a name. Every test below
exists because getting one of these wrong would put an unfair mark against a
colleague, or a wrong figure in front of the CEO.
"""
from __future__ import annotations

import datetime as _dt
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from transformation import pulse
from transformation.models import DepartmentPlan, Initiative, PulseSnapshot
from transformation.permissions import may_view


class ScoreBlendTests(TestCase):
    def test_a_signal_with_no_evidence_does_not_drag_the_score_down(self):
        """An unmeasured signal must be skipped, not counted as zero.

        Without this, a department we have shipped nothing for would score 0 on
        adoption and be branded a laggard for our own inaction.
        """
        both = pulse._score_from(80, 80, None, None, None)
        self.assertEqual(both, 80)

    def test_all_signals_missing_scores_zero_not_a_crash(self):
        self.assertEqual(pulse._score_from(None, None, None, None, None), 0)

    def test_a_weak_signal_pulls_the_score_down(self):
        strong = pulse._score_from(100, 100, 100, 100, 100)
        weak = pulse._score_from(100, 0, 100, 100, 100)
        self.assertEqual(strong, 100)
        self.assertLess(weak, strong)


class LeaderboardFairnessTests(TestCase):
    def setUp(self):
        self.base = dict(
            title='x', track=Initiative.Track.SWITCH_ON, month=1,
            department='Claims', manager_name='Test Owner',
            manager_email='owner@alphadirect.co.bw',
        )

    def test_someone_with_one_item_is_not_scored(self):
        """One step and no tasks is not enough to call somebody a dinosaur."""
        Initiative.objects.create(code='A-1', percent=0, **self.base)
        board = pulse.manager_leaderboard()
        unscored = {r['email'] for r in board['unscored']}
        self.assertIn('owner@alphadirect.co.bw', unscored)
        self.assertNotIn('owner@alphadirect.co.bw',
                         {r['email'] for r in board['lagging']})

    def test_enough_work_and_no_progress_lands_in_lagging(self):
        for i in range(3):
            Initiative.objects.create(code=f'B-{i}', percent=0, **self.base)
        board = pulse.manager_leaderboard()
        self.assertIn('owner@alphadirect.co.bw',
                      {r['email'] for r in board['lagging']})

    def test_finished_work_lands_in_pushing(self):
        for i in range(3):
            Initiative.objects.create(code=f'C-{i}', percent=100,
                                      status=Initiative.Status.DONE, **self.base)
        board = pulse.manager_leaderboard()
        self.assertIn('owner@alphadirect.co.bw',
                      {r['email'] for r in board['pushing']})

    def test_every_listed_person_carries_their_evidence(self):
        for i in range(3):
            Initiative.objects.create(code=f'D-{i}', percent=0, **self.base)
        board = pulse.manager_leaderboard()
        for group in ('pushing', 'lagging'):
            for row in board[group]:
                self.assertTrue(row['evidence'],
                                'a name on this board must carry its evidence')


class BoardTests(TestCase):
    def test_board_builds_on_an_empty_database(self):
        """The screen must render on day one, before anything has happened."""
        board = pulse.build_board()
        self.assertEqual(board['headline'],
                         'First AI Insurance Company in Botswana')
        self.assertIn('workforce', board)
        self.assertIn('departments', board)
        self.assertEqual(board['overall_percent'], 0)

    def test_progress_is_weighted_by_the_people_it_frees(self):
        common = dict(track=Initiative.Track.SWITCH_ON, month=1, department='Claims')
        Initiative.objects.create(code='BIG', title='big', percent=100,
                                  fte_released=Decimal('9'), **common)
        Initiative.objects.create(code='SMALL', title='small', percent=0,
                                  fte_released=Decimal('0'), **common)
        board = pulse.build_board()
        # 10 of 11 weight finished — a big win must not be averaged away by a
        # small untouched item.
        self.assertGreater(board['overall_percent'], 80)

    def test_a_step_behind_the_clock_is_listed(self):
        """Pin the clock — guarding the assertion on today's date meant this
        test asserted nothing at all until October."""
        from unittest.mock import patch

        Initiative.objects.create(
            code='LATE', title='late', track=Initiative.Track.SWITCH_ON,
            month=1, department='Claims', percent=0,
            target_date=_dt.date(2026, 10, 20))

        with patch('transformation.pulse._today',
                   return_value=_dt.date(2026, 11, 20)):
            board = pulse.build_board()
        self.assertIn('LATE', {b['code'] for b in board['behind']})

    def test_only_finished_automation_can_be_charged_to_a_department(self):
        """A department cannot be billed for not using something undelivered."""
        DepartmentPlan.objects.create(department='Claims', headcount_target=5)
        Initiative.objects.create(
            code='UNDONE', title='not yet', track=Initiative.Track.SWITCH_ON,
            month=1, department='Claims', percent=10,
            fte_released=Decimal('3'), status=Initiative.Status.IN_PROGRESS)
        rows = {r['department']: r for r in pulse.department_scores()}
        self.assertEqual(rows['Claims']['monthly_cost_of_not_using'], 0.0)

    def test_the_clock_counts_down_to_the_cfos_january_deadline(self):
        clock = pulse.programme_clock()
        self.assertEqual(clock['end'], '2027-01-20')
        self.assertEqual(clock['days_total'], 122)


class AccessTests(TestCase):
    def test_the_four_named_viewers_are_allowed(self):
        for email in ('aiyer@alphadirect.co.bw', 'pganesharajah@alphadirect.co.bw',
                      'arjuniyer@alphadirect.co.bw', 'ubutale@alphadirect.co.bw'):
            user = User.objects.create(username=email.split('@')[0], email=email)
            self.assertTrue(may_view(user), f'{email} should be allowed')

    def test_everybody_else_is_refused(self):
        user = User.objects.create(username='someone',
                                   email='someone@alphadirect.co.bw')
        self.assertFalse(may_view(user))

    def test_an_unknown_address_is_refused_not_defaulted(self):
        """Positive match only — no domain or prefix fallback."""
        for email in ('', 'aiyer@evil.com', 'aiyer', 'xaiyer@alphadirect.co.bw'):
            user = User(username='u', email=email)
            self.assertFalse(may_view(user), f'{email!r} must be refused')

    def test_an_anonymous_caller_is_refused(self):
        self.assertFalse(may_view(None))

    def test_the_api_refuses_a_non_viewer(self):
        user = User.objects.create_user('nobody', 'nobody@alphadirect.co.bw', 'pw')
        self.client.force_login(user)
        resp = self.client.get('/api/v1/transformation/board/')
        self.assertEqual(resp.status_code, 403)

    def test_the_api_serves_a_viewer(self):
        user = User.objects.create_user('ceo', 'aiyer@alphadirect.co.bw', 'pw')
        self.client.force_login(user)
        resp = self.client.get('/api/v1/transformation/board/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['headline'],
                         'First AI Insurance Company in Botswana')


class ProgressTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('cfo', 'pganesharajah@alphadirect.co.bw', 'pw')
        self.client.force_login(self.user)
        self.item = Initiative.objects.create(
            code='SW-01', title='switch on', track=Initiative.Track.SWITCH_ON,
            month=1, department='Finance & Planning')

    def test_setting_a_step_to_100_marks_it_done(self):
        resp = self.client.post('/api/v1/transformation/initiatives/SW-01/progress/',
                                {'percent': 100}, content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, Initiative.Status.DONE)

    def test_a_percent_outside_0_to_100_is_refused(self):
        for bad in (-1, 101, 'lots'):
            resp = self.client.post(
                '/api/v1/transformation/initiatives/SW-01/progress/',
                {'percent': bad}, content_type='application/json')
            self.assertEqual(resp.status_code, 400, f'{bad!r} should be refused')

    def test_blocking_starts_the_clock_and_unblocking_stops_it(self):
        self.client.post('/api/v1/transformation/initiatives/SW-01/progress/',
                         {'status': 'blocked', 'blocked_on': 'Vendor'},
                         content_type='application/json')
        self.item.refresh_from_db()
        self.assertIsNotNone(self.item.blocked_since)

        self.client.post('/api/v1/transformation/initiatives/SW-01/progress/',
                         {'status': 'in_progress'}, content_type='application/json')
        self.item.refresh_from_db()
        self.assertIsNone(self.item.blocked_since)

    def test_every_move_is_recorded(self):
        self.client.post('/api/v1/transformation/initiatives/SW-01/progress/',
                         {'percent': 40, 'note': 'started'},
                         content_type='application/json')
        self.assertEqual(self.item.updates.count(), 1)
        self.assertEqual(self.item.updates.first().actor_name, 'cfo')

    def test_an_unknown_code_is_a_404(self):
        resp = self.client.post('/api/v1/transformation/initiatives/NOPE/progress/',
                                {'percent': 10}, content_type='application/json')
        self.assertEqual(resp.status_code, 404)


class SeedTests(TestCase):
    def test_seeding_twice_does_not_duplicate_and_does_not_lose_progress(self):
        from transformation.seed import seed

        seed()
        count = Initiative.objects.count()
        self.assertGreater(count, 0)

        item = Initiative.objects.get(code='SW-01')
        item.percent = 70
        item.status = Initiative.Status.IN_PROGRESS
        item.save()

        seed()
        self.assertEqual(Initiative.objects.count(), count)
        item.refresh_from_db()
        self.assertEqual(item.percent, 70, 'a re-seed must never reset progress')
        self.assertEqual(item.status, Initiative.Status.IN_PROGRESS)

    def test_vendor_blocked_steps_start_blocked_with_the_clock_running(self):
        from transformation.seed import seed

        seed()
        item = Initiative.objects.get(code='SW-02')
        self.assertEqual(item.status, Initiative.Status.BLOCKED)
        self.assertIsNotNone(item.blocked_since)
        self.assertTrue(item.is_vendor)

    def test_no_step_is_seeded_with_a_guessed_owner(self):
        """Where the owner is unknown the row must say so, not name someone."""
        from transformation.seed import seed

        seed()
        for item in Initiative.objects.exclude(manager_name=''):
            if item.manager_name != 'TBC — CFO to name':
                self.assertTrue(item.manager_email,
                                f'{item.code} names {item.manager_name} with no email')


class SnapshotTests(TestCase):
    def test_the_pulse_writes_one_row_a_day(self):
        from django.core.management import call_command

        call_command('transformation_pulse', '--commit', '--no-ai')
        call_command('transformation_pulse', '--commit', '--no-ai')
        self.assertEqual(PulseSnapshot.objects.count(), 1)

    def test_a_dry_run_writes_nothing(self):
        from django.core.management import call_command

        call_command('transformation_pulse', '--no-ai')
        self.assertEqual(PulseSnapshot.objects.count(), 0)


class AiSafetyTests(TestCase):
    def test_no_person_or_customer_detail_is_sent_to_the_model(self):
        """The facts pack must carry aggregates only.

        Naming a colleague to an outside model is exactly what AD-POL-AI-GOV-001
        forbids, and the laggard list is the most tempting thing to send.
        """
        from transformation import ai_panel

        # A name has to be reachable from BOTH places the board carries one —
        # the step's owner AND the department plan's manager. An earlier version
        # of this test only created the step, so a leak through the department
        # row would have passed unnoticed.
        Initiative.objects.create(
            code='X-1', title='x', track=Initiative.Track.SWITCH_ON, month=1,
            department='Claims', manager_name='Wangu Moses',
            manager_email='wmoses@alphadirect.co.bw')
        DepartmentPlan.objects.create(
            department='Claims', manager_name='Wangu Moses',
            manager_email='wmoses@alphadirect.co.bw', headcount_target=5)

        board = pulse.build_board()
        # The board itself MUST carry the name (the screen shows it) —
        # otherwise this test would pass for the wrong reason.
        self.assertIn('Wangu Moses',
                      [d['manager_name'] for d in board['departments']])

        facts = ai_panel._safe_facts(board)
        self.assertNotIn('Wangu', facts)
        self.assertNotIn('wmoses', facts)
        self.assertNotIn('@', facts)

    def test_the_board_is_unchanged_when_every_model_is_down(self):
        from unittest.mock import patch

        from transformation import ai_panel

        board = pulse.build_board()
        with patch('transformation.ai_panel._draft', return_value=('', '')):
            read = ai_panel.daily_read(board)
        self.assertEqual(read['narrative'], '')
        self.assertIn('unaffected', read['note'])


class AssignmentTests(TestCase):
    """Assigning must create work a person actually receives."""

    def setUp(self):
        self.cfo = User.objects.create_user(
            'cfo', 'pganesharajah@alphadirect.co.bw', 'pw')
        self.client.force_login(self.cfo)
        self.worker = User.objects.create_user(
            'wmoses', 'wmoses@alphadirect.co.bw', 'pw',
            first_name='Wangu', last_name='Moses')
        self.item = Initiative.objects.create(
            code='SW-02', title='land the bridge',
            track=Initiative.Track.SWITCH_ON, month=1, department='Claims',
            target_date=_dt.date(2026, 10, 20))

    def _assign(self, **body):
        return self.client.post(
            '/api/v1/transformation/initiatives/SW-02/assign/',
            {'email': 'wmoses@alphadirect.co.bw', **body},
            content_type='application/json')

    def test_assigning_creates_a_real_task_with_a_deadline(self):
        from core.models import OmniTask

        resp = self._assign(due_date='2026-10-10', role='land the merge')
        self.assertEqual(resp.status_code, 200)

        task = OmniTask.objects.get(assignee=self.worker)
        self.assertEqual(task.due_at, _dt.date(2026, 10, 10))
        self.assertIn('SW-02', task.title)
        self.assertEqual(task.assigner, self.cfo)

    def test_a_missing_deadline_falls_back_to_the_step_date(self):
        from core.models import OmniTask

        self._assign()
        self.assertEqual(OmniTask.objects.get(assignee=self.worker).due_at,
                         _dt.date(2026, 10, 20))

    def test_assigning_somebody_who_has_no_login_is_refused(self):
        from core.models import OmniTask

        resp = self.client.post(
            '/api/v1/transformation/initiatives/SW-02/assign/',
            {'email': 'ghost@alphadirect.co.bw'}, content_type='application/json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('not an active Omni login', resp.json()['detail'])
        self.assertEqual(OmniTask.objects.count(), 0)

    def test_assigning_the_same_person_twice_updates_one_task(self):
        from core.models import OmniTask

        self._assign(due_date='2026-10-10')
        self._assign(due_date='2026-11-01')
        self.assertEqual(OmniTask.objects.filter(assignee=self.worker).count(), 1)
        self.assertEqual(OmniTask.objects.get(assignee=self.worker).due_at,
                         _dt.date(2026, 11, 1))

    def test_the_owner_flag_sets_the_name_shown_on_the_board(self):
        self._assign(is_owner=True)
        self.item.refresh_from_db()
        self.assertEqual(self.item.manager_name, 'Wangu Moses')
        self.assertEqual(self.item.manager_email, 'wmoses@alphadirect.co.bw')

    def test_removing_someone_cancels_the_task_but_keeps_the_record(self):
        from core.models import OmniTask

        self._assign()
        resp = self.client.delete(
            '/api/v1/transformation/initiatives/SW-02/assign/',
            {'email': 'wmoses@alphadirect.co.bw'}, content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        task = OmniTask.objects.get(assignee=self.worker)
        self.assertEqual(task.status, 'cancelled')

    def test_an_overdue_assignment_shows_on_the_board(self):
        self._assign(due_date='2020-01-01')
        board = pulse.build_board()
        step = next(i for i in board['initiatives'] if i['code'] == 'SW-02')
        self.assertTrue(step['assignments'][0]['overdue'])

    def test_a_non_viewer_cannot_assign_anyone(self):
        self.client.force_login(
            User.objects.create_user('rando', 'rando@alphadirect.co.bw', 'pw'))
        self.assertEqual(self._assign().status_code, 403)

    def test_the_people_list_only_offers_real_logins(self):
        resp = self.client.get('/api/v1/transformation/people/')
        self.assertEqual(resp.status_code, 200)
        emails = {p['email'] for p in resp.json()['people']}
        self.assertIn('wmoses@alphadirect.co.bw', emails)
        self.assertNotIn('', emails)


class LeaveGuardTests(TestCase):
    """The leave guard must work, and must fail safe when it cannot.

    These exist because the first version of this code swallowed the error and
    returned an empty set — which reads as "nobody is on leave". The query was
    ALSO wrong (LeaveRequest has no `employee` field; it goes through
    `profile`), so the guard never fired at all and a person on approved leave
    could have been published as a laggard. The silence hid the bug.
    """

    def setUp(self):
        self.base = dict(
            title='x', track=Initiative.Track.SWITCH_ON, month=1,
            department='Claims', manager_name='On Leave Person',
            manager_email='onleave@alphadirect.co.bw',
        )
        for i in range(3):
            Initiative.objects.create(code=f'L-{i}', percent=0, **self.base)

    def test_the_leave_query_actually_runs(self):
        """A wrong field name here is invisible without this test."""
        emails, ok = pulse._on_leave_today()
        self.assertTrue(ok, 'the leave register could not be read — check the relation')
        self.assertIsInstance(emails, set)

    def test_a_person_on_approved_leave_is_never_a_laggard(self):
        from payroll.models import Employee
        from hris.models import HRISProfile, LeaveRequest, LeaveType

        employee = Employee.objects.create(
            full_name='On Leave', employee_number='TEST-LEAVE-1',
            email='onleave@alphadirect.co.bw', department='Claims',
            status='active')
        profile = HRISProfile.objects.create(employee=employee)
        today = timezone.localdate()
        LeaveRequest.objects.create(
            profile=profile,
            leave_type=LeaveType.objects.create(name='Annual', code='ANN'),
            start_date=today - _dt.timedelta(days=1),
            end_date=today + _dt.timedelta(days=1),
            status='approved', days=3)

        board = pulse.manager_leaderboard()
        self.assertTrue(board['leave_data_available'])
        self.assertNotIn('onleave@alphadirect.co.bw',
                         {r['email'] for r in board['lagging']})
        reasons = {r['email']: r.get('why_unscored') for r in board['unscored']}
        self.assertIn('approved leave', reasons.get('onleave@alphadirect.co.bw', ''))

    def test_nobody_is_named_when_the_leave_register_cannot_be_read(self):
        """Fail safe: an unreadable register must not produce an accusation."""
        from unittest.mock import patch

        with patch('transformation.pulse._on_leave_today', return_value=(set(), False)):
            board = pulse.manager_leaderboard()
        self.assertFalse(board['leave_data_available'])
        self.assertEqual(board['lagging'], [],
                         'nobody may be called a laggard while leave is unknown')
        reasons = ' '.join(str(r.get('why_unscored')) for r in board['unscored'])
        self.assertIn('Leave register could not be read', reasons)

    def test_unknown_leave_shows_as_not_available_never_zero_days(self):
        """A false zero makes a department look like it never takes leave."""
        from unittest.mock import patch

        from transformation import workforce as wf

        with patch('transformation.workforce.leave_by_department',
                   return_value=({}, False)):
            block = wf.workforce_block({'Claims': 100000}, {'Claims': 10})
        self.assertFalse(block['leave_data_available'])
        for row in block['departments']:
            self.assertIsNone(row['leave_days'],
                              'unknown leave must be None (shown as "—"), never 0')


class AssignmentAtomicityTests(TestCase):
    """The assignment row and its task live or die together."""

    def setUp(self):
        self.cfo = User.objects.create_user(
            'cfo2', 'pganesharajah@alphadirect.co.bw', 'pw')
        self.worker = User.objects.create_user(
            'w2', 'w2@alphadirect.co.bw', 'pw', first_name='Work', last_name='Er')
        self.item = Initiative.objects.create(
            code='AT-1', title='atomic', track=Initiative.Track.SWITCH_ON,
            month=1, department='Claims', target_date=_dt.date(2026, 10, 20))

    def test_no_orphan_task_when_the_row_cannot_be_saved(self):
        """A half-written assignment must leave nothing behind."""
        from unittest.mock import patch

        from core.models import OmniTask
        from transformation import assign as svc

        # get_or_create calls save() itself, so failing the FIRST save proves
        # nothing — the task was never attempted. Let the row insert succeed,
        # create the task, then fail the second save.
        real_save = svc.InitiativeAssignment.save
        calls = {'n': 0}

        def flaky(self_, *a, **kw):
            calls['n'] += 1
            if calls['n'] >= 2:
                raise RuntimeError('boom')
            return real_save(self_, *a, **kw)

        with patch.object(svc.InitiativeAssignment, 'save', flaky):
            with self.assertRaises(RuntimeError):
                svc.assign(self.item, 'w2@alphadirect.co.bw', assigner=self.cfo)

        self.assertGreaterEqual(calls['n'], 2,
                                'the task must have been attempted before the failure')

        self.assertEqual(OmniTask.objects.count(), 0,
                         'the task must roll back with the row')
        self.assertEqual(svc.InitiativeAssignment.objects.count(), 0)

    def test_assigning_twice_never_creates_two_tasks(self):
        from core.models import OmniTask
        from transformation import assign as svc

        svc.assign(self.item, 'w2@alphadirect.co.bw', assigner=self.cfo)
        svc.assign(self.item, 'w2@alphadirect.co.bw', assigner=self.cfo)
        self.assertEqual(OmniTask.objects.filter(assignee=self.worker).count(), 1)
        self.assertEqual(svc.InitiativeAssignment.objects.count(), 1)


class AssignmentRaceTests(TestCase):
    """The database, not Python, is what stops a double assignment."""

    def test_the_database_refuses_two_assignments_for_one_person_on_one_step(self):
        from django.db import IntegrityError, transaction

        from transformation.models import InitiativeAssignment

        item = Initiative.objects.create(
            code='RC-1', title='race', track=Initiative.Track.SWITCH_ON,
            month=1, department='Claims')
        user = User.objects.create_user('rc', 'rc@alphadirect.co.bw', 'pw')

        InitiativeAssignment.objects.create(initiative=item, assignee=user)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                InitiativeAssignment.objects.create(initiative=item, assignee=user)


class DepartmentFairnessTests(TestCase):
    """A department nobody gave work to is unmeasured, not excellent."""

    def test_a_department_with_no_steps_is_not_scored_100(self):
        DepartmentPlan.objects.create(department='Empty Dept', headcount_target=3)
        rows = {r['department']: r for r in pulse.department_scores()}
        row = rows['Empty Dept']
        self.assertIsNone(row['automation_score'],
                          'no evidence must mean no score, never a perfect one')
        self.assertFalse(row['scored'])
        self.assertIn('attendance alone is not an automation score',
                      row['why_unscored'])

    def test_a_department_doing_the_work_outranks_an_unmeasured_one(self):
        DepartmentPlan.objects.create(department='Empty Dept', headcount_target=3)
        DepartmentPlan.objects.create(department='Busy Dept', headcount_target=3)
        Initiative.objects.create(
            code='BD-1', title='real work', track=Initiative.Track.SWITCH_ON,
            month=1, department='Busy Dept', percent=50)

        rows = pulse.department_scores()
        order = [r['department'] for r in rows]
        self.assertLess(order.index('Busy Dept'), order.index('Empty Dept'),
                        'an unmeasured department must not sit in the ranking')


class DayOneFairnessTests(TestCase):
    """Nobody is a laggard on the first morning of a four-month programme."""

    def setUp(self):
        self.base = dict(
            track=Initiative.Track.SWITCH_ON, department='Claims',
            manager_name='Owner Person', manager_email='owner@alphadirect.co.bw',
        )

    def test_owners_of_future_work_are_not_published_as_lagging(self):
        """A month-4 step at 0% on day one is not a failure to deliver."""
        from unittest.mock import patch

        for i in range(4):
            Initiative.objects.create(
                code=f'F-{i}', title='future', month=4, percent=0,
                target_date=_dt.date(2027, 1, 20), **self.base)

        with patch('transformation.pulse._today',
                   return_value=_dt.date(2026, 9, 20)):   # day one
            board = pulse.manager_leaderboard()

        self.assertEqual(board['lagging'], [],
                         'nobody may be named on the first day')
        reasons = {r['email']: r.get('why_unscored') for r in board['unscored']}
        self.assertIn('Nothing due yet', reasons['owner@alphadirect.co.bw'])

    def test_someone_who_is_genuinely_behind_is_still_named(self):
        """The guard must not become a blanket excuse."""
        from unittest.mock import patch

        for i in range(4):
            Initiative.objects.create(
                code=f'B-{i}', title='overdue', month=1, percent=0,
                target_date=_dt.date(2026, 10, 20), **self.base)

        with patch('transformation.pulse._today',
                   return_value=_dt.date(2026, 12, 20)):  # two months past due
            board = pulse.manager_leaderboard()

        self.assertIn('owner@alphadirect.co.bw',
                      {r['email'] for r in board['lagging']})


class MoneyFairnessTests(TestCase):
    """A department is never billed on the basis of something unknown."""

    def test_unknown_adoption_is_never_billed_as_zero_adoption(self):
        from payroll.models import Employee, PayrollPeriod, Payslip

        # The department must have REAL cost, or the multiplication is zero
        # either way and this test would pass without proving anything.
        period = PayrollPeriod.objects.create(
            period_name='2026-08', start_date=_dt.date(2026, 8, 1),
            end_date=_dt.date(2026, 8, 31), pay_date=_dt.date(2026, 8, 28))
        for i in range(5):
            employee = Employee.objects.create(
                full_name=f'Person {i}', employee_number=f'NE-{i}',
                email=f'ne{i}@alphadirect.co.bw', department='NoEvidence',
                status='active')
            Payslip.objects.create(period=period, employee=employee,
                                   gross_amount=Decimal('20000'),
                                   net_amount=Decimal('16000'),
                                   ctc_amount=Decimal('24000'))

        DepartmentPlan.objects.create(department='NoEvidence', headcount_target=5)
        Initiative.objects.create(
            code='M-1', title='done thing', track=Initiative.Track.SWITCH_ON,
            month=1, department='NoEvidence', percent=100,
            status=Initiative.Status.DONE, fte_released=Decimal('3'))

        row = {r['department']: r for r in pulse.department_scores()}['NoEvidence']
        self.assertGreater(row['cost_now'], 0,
                           'the department must carry real cost for this to mean anything')
        self.assertIsNone(row['adoption_percent'], 'no Build Log evidence expected')
        self.assertEqual(row['monthly_cost_of_not_using'], 0.0,
                         'unknown adoption must cost nothing, not everything')
        self.assertFalse(row['cost_measurable'])


class LeaveNeverCountsAgainstYouTests(TestCase):
    """The promise printed on the screen must be true in the arithmetic."""

    def _day(self, profile, *, required, tracked, justified, status):
        from hris.models import WorkdayJustification

        return WorkdayJustification.objects.create(
            profile=profile, work_date=timezone.localdate() - _dt.timedelta(days=2),
            required_hours=required, tracked_hours=tracked,
            justified_hours=justified, status=status)

    def test_an_approved_leave_day_is_not_a_short_day_or_an_absence(self):
        from hris.models import HRISProfile
        from payroll.models import Employee
        from transformation import workforce as wf

        employee = Employee.objects.create(
            full_name='Leave Taker', employee_number='LV-1',
            email='lv@alphadirect.co.bw', department='Claims', status='active')
        profile = HRISProfile.objects.create(employee=employee)
        # A leave day arrives as: hours owed, nothing tracked, day justified.
        self._day(profile, required=8, tracked=0, justified=8, status='justified')

        rows = wf.workday_record()
        claims = rows['Claims']
        self.assertEqual(claims['short_days'], 0)
        self.assertEqual(claims['absence_days'], 0)
        self.assertEqual(claims['attendance_percent'], 100,
                         'a day on approved leave is fully accounted for')


class AssignmentIdentityTests(TestCase):
    """The right person gets the task, or nobody does."""

    def test_two_active_accounts_on_one_address_are_refused(self):
        from transformation import assign as svc

        User.objects.create_user('dup1', 'dup@alphadirect.co.bw', 'pw')
        User.objects.create_user('dup2', 'dup@alphadirect.co.bw', 'pw')
        self.assertIsNone(svc._find_user('dup@alphadirect.co.bw'),
                          'an ambiguous address must be refused, not guessed')

    def test_removing_someone_works_after_their_email_changes(self):
        from transformation import assign as svc

        user = User.objects.create_user('mover', 'old@alphadirect.co.bw', 'pw')
        item = Initiative.objects.create(
            code='ID-1', title='x', track=Initiative.Track.SWITCH_ON,
            month=1, department='Claims')
        svc.assign(item, 'old@alphadirect.co.bw', assigner=user)

        user.email = 'new@alphadirect.co.bw'
        user.save()

        self.assertTrue(svc.unassign(item, 'new@alphadirect.co.bw'),
                        'unassign must key on the person, not a stored string')


class EvolutionTests(TestCase):
    """The creature is earned, and the strip never breaks."""

    def test_the_company_starts_as_the_paper_pusher(self):
        from transformation.evolution import evolution

        strip = evolution(0)
        self.assertEqual(strip['current']['key'], 'papyrus')
        self.assertEqual(strip['current_index'], 0)
        self.assertFalse(strip['is_final'])
        self.assertEqual(strip['points_to_next'], 20)

    def test_each_band_lands_on_its_own_creature(self):
        from transformation.evolution import STAGES, stage_for

        for percent, key in ((0, 'papyrus'), (19, 'papyrus'), (20, 'tabulator'),
                             (39, 'tabulator'), (40, 'clickus'), (59, 'clickus'),
                             (60, 'cyborgus'), (79, 'cyborgus'),
                             (80, 'automaticus'), (100, 'automaticus')):
            self.assertEqual(stage_for(percent)['key'], key, f'{percent}% -> {key}')
        self.assertEqual(len(STAGES), 5)

    def test_the_last_stage_has_no_next_and_does_not_divide_by_zero(self):
        from transformation.evolution import evolution

        strip = evolution(100)
        self.assertTrue(strip['is_final'])
        self.assertIsNone(strip['next'])
        self.assertEqual(strip['points_to_next'], 0)
        self.assertEqual(strip['percent_into_stage'], 100)

    def test_a_missing_or_silly_percent_never_breaks_the_picture(self):
        from transformation.evolution import evolution

        for value in (None, -40, 999):
            strip = evolution(value)
            self.assertIn(strip['current']['key'],
                          {'papyrus', 'tabulator', 'clickus', 'cyborgus', 'automaticus'})
            self.assertTrue(0 <= strip['percent_into_stage'] <= 100)

    def test_the_figure_moves_between_thresholds_not_only_at_them(self):
        """A living board should move a little every day."""
        from transformation.evolution import evolution

        self.assertEqual(evolution(20)['percent_into_stage'], 0)
        self.assertEqual(evolution(30)['percent_into_stage'], 50)
        self.assertEqual(evolution(39)['percent_into_stage'], 95)

    def test_no_stage_name_points_at_a_person_or_a_department(self):
        """The joke is about the company. It sits beside named colleagues."""
        from hris.departments import DEPARTMENTS
        from transformation.evolution import STAGES

        words = ' '.join(f"{s['name']} {s['nickname']} {s['caption']} {s['tagline']}"
                         for s in STAGES).lower()
        for dept in DEPARTMENTS:
            self.assertNotIn(dept.lower(), words,
                             f'stage text must not name the {dept} department')
        for role in ('clerk', 'accountant', 'underwriter', 'manager', 'agent'):
            self.assertNotIn(role, words, f'stage text must not name the {role} role')

    def test_the_strip_rides_on_the_board(self):
        board = pulse.build_board()
        self.assertIn('evolution', board)
        self.assertEqual(board['evolution']['percent'], board['overall_percent'])
        self.assertEqual(len(board['evolution']['stages']), 5)


class GateFixTests(TestCase):
    """Seven defects Fable found at the release gate. Each is pinned here."""

    # ---- 1. adoption cannot be inferred from the dev team's deploy queue ---
    def test_adoption_is_not_inferred_from_the_dev_queue(self):
        """`waiting` means "built, waiting to go live" — it is the dev team's
        backlog, not a feature a department refused to use."""
        from devlog.models import DevItem

        for status in ('live', 'waiting', 'waiting'):
            DevItem.objects.create(
                asked_text='x', asked_at=timezone.now(), status=status,
                area='claims')

        bucket = pulse.adoption_by_area()['claims']
        self.assertIsNone(bucket['adoption_percent'],
                          'no confirmation source exists — this must stay unmeasured')

    def test_a_persons_score_is_never_moved_by_the_dev_queue(self):
        """Checking the bucket alone was not enough: the leaderboard rebuilt
        live/(live+waiting) from the raw counts, so a department's undeployed
        backlog moved a named colleague's score and could push them into
        "Lagging"."""
        from unittest.mock import patch

        from devlog.models import DevItem

        DevItem.objects.create(asked_text='x', asked_at=timezone.now(),
                               status='live', area='claims')
        for _ in range(3):
            DevItem.objects.create(asked_text='x', asked_at=timezone.now(),
                                   status='waiting', area='claims')

        for i in range(3):
            Initiative.objects.create(
                code=f'AQ-{i}', title='month one', track=Initiative.Track.SWITCH_ON,
                month=1, department='Claims', percent=0,
                target_date=_dt.date(2026, 10, 20),
                manager_name='Owner', manager_email='owner@alphadirect.co.bw')

        with patch('transformation.pulse._today',
                   return_value=_dt.date(2026, 10, 1)):
            board = pulse.manager_leaderboard()

        rows = {r['email']: r for r in
                board['pushing'] + board['lagging'] + board['unscored']}
        person = rows['owner@alphadirect.co.bw']
        self.assertIsNone(person['adoption_percent'],
                          'the dev queue must never reach a person\'s score')

    def test_a_department_is_never_billed_from_the_dev_queue(self):
        from devlog.models import DevItem

        DevItem.objects.create(asked_text='x', asked_at=timezone.now(),
                               status='waiting', area='claims')
        DepartmentPlan.objects.create(department='Claims', headcount_target=5)
        Initiative.objects.create(
            code='G-1', title='done', track=Initiative.Track.SWITCH_ON,
            month=1, department='Claims', percent=100,
            status=Initiative.Status.DONE, fte_released=Decimal('3'))

        row = {r['department']: r for r in pulse.department_scores()}['Claims']
        self.assertEqual(row['monthly_cost_of_not_using'], 0.0)

    # ---- 2. the cliff: day two must not brand anyone -----------------------
    def test_nobody_is_lagging_on_day_two(self):
        from unittest.mock import patch

        for i in range(4):
            Initiative.objects.create(
                code=f'D2-{i}', title='month one', track=Initiative.Track.SWITCH_ON,
                month=1, department='Claims', percent=0,
                target_date=_dt.date(2026, 10, 20),
                manager_name='Owner', manager_email='owner@alphadirect.co.bw')

        with patch('transformation.pulse._today',
                   return_value=_dt.date(2026, 9, 21)):   # day two
            board = pulse.manager_leaderboard()
        self.assertEqual(board['lagging'], [],
                         'a ratio-to-expected cliff brands people on day two')

    def test_credit_degrades_by_points_behind_not_as_a_ratio(self):
        self.assertEqual(pulse._credit(0, 3), 1.0)     # day two, nothing due yet
        self.assertEqual(pulse._credit(50, 50), 1.0)   # exactly on track
        self.assertLess(pulse._credit(0, 60), 1.0)     # genuinely behind
        self.assertGreater(pulse._credit(0, 60), 0.0)  # but never a zero cliff

    # ---- 3. one definition of "behind" ------------------------------------
    def test_behind_uses_the_steps_own_window(self):
        from unittest.mock import patch

        Initiative.objects.create(
            code='BH-1', title='month four', track=Initiative.Track.SWITCH_ON,
            month=4, department='Claims', percent=0,
            target_date=_dt.date(2027, 1, 20))
        with patch('transformation.pulse._today',
                   return_value=_dt.date(2026, 10, 1)):
            board = pulse.build_board()
        self.assertNotIn('BH-1', {b['code'] for b in board['behind']},
                         'month-4 work is not behind in October')

    # ---- 4. departments get the same fairness as people --------------------
    def test_a_department_is_not_marked_down_on_day_one(self):
        from unittest.mock import patch

        DepartmentPlan.objects.create(department='Claims', headcount_target=5)
        Initiative.objects.create(
            code='DP-1', title='month three', track=Initiative.Track.SWITCH_ON,
            month=3, department='Claims', percent=0,
            target_date=_dt.date(2026, 12, 20))
        with patch('transformation.pulse._today',
                   return_value=_dt.date(2026, 9, 21)):
            row = {r['department']: r for r in pulse.department_scores()}['Claims']
        self.assertIsNone(row['delivery_percent'],
                          'nothing due yet means NOT SCORED, never scored 100')

    # ---- 5. a vendor block is not the owner's fault ------------------------
    def test_a_vendor_block_never_scores_against_the_internal_owner(self):
        common = dict(track=Initiative.Track.SWITCH_ON, month=1,
                      department='Claims', percent=0,
                      target_date=_dt.date(2026, 10, 20),
                      status=Initiative.Status.BLOCKED,
                      blocked_since=timezone.localdate() - _dt.timedelta(days=30),
                      manager_name='Owner', manager_email='owner@alphadirect.co.bw')
        Initiative.objects.create(code='V-1', title='vendor', is_vendor=True, **common)
        Initiative.objects.create(code='V-2', title='vendor2', is_vendor=True, **common)

        board = pulse.manager_leaderboard()
        rows = {r['email']: r for r in
                board['pushing'] + board['lagging'] + board['unscored']}
        self.assertEqual(rows['owner@alphadirect.co.bw']['blocked_days'], 0,
                         'waiting on a vendor is not the owner holding it up')

    # ---- 6. a reseed must not undo the CFO's own changes -------------------
    def test_a_reseed_does_not_put_back_an_owner_the_cfo_changed(self):
        """The entrypoint reseeds on EVERY deploy."""
        from transformation.seed import seed

        seed()
        item = Initiative.objects.get(code='SW-02')
        item.manager_name = 'Someone Else'
        item.manager_email = 'else@alphadirect.co.bw'
        item.blocked_on = ''
        item.save()

        seed()
        item.refresh_from_db()
        self.assertEqual(item.manager_name, 'Someone Else',
                         'a reseed must never re-stamp the owner')
        self.assertEqual(item.blocked_on, '')

    def test_a_reseed_still_refreshes_the_wording_and_the_money(self):
        from transformation.seed import seed

        seed()
        item = Initiative.objects.get(code='SW-02')
        item.title = 'stale title'
        item.annual_saving_bwp = Decimal('1')
        item.save()

        seed()
        item.refresh_from_db()
        self.assertNotEqual(item.title, 'stale title')
        self.assertGreater(item.annual_saving_bwp, Decimal('1'))

    # ---- 7. "awaiting manager" is the manager's number --------------------
    def test_awaiting_manager_counts_days_the_manager_owes_not_the_employee(self):
        from hris.models import HRISProfile, WorkdayJustification
        from payroll.models import Employee
        from transformation import workforce as wf

        employee = Employee.objects.create(
            full_name='Explainer', employee_number='EX-1',
            email='ex@alphadirect.co.bw', department='Claims', status='active')
        profile = HRISProfile.objects.create(employee=employee)

        # The employee HAS explained; the manager has not ruled.
        WorkdayJustification.objects.create(
            profile=profile, work_date=timezone.localdate() - _dt.timedelta(days=2),
            required_hours=8, tracked_hours=3, justified_hours=0,
            status='explained', responded_at=timezone.now())
        # Nobody has answered this one at all — that is the EMPLOYEE's to do.
        WorkdayJustification.objects.create(
            profile=profile, work_date=timezone.localdate() - _dt.timedelta(days=3),
            required_hours=8, tracked_hours=3, justified_hours=0,
            status='pending')

        claims = wf.workday_record()['Claims']
        self.assertEqual(claims['awaiting_manager'], 1,
                         'only the explained day is the manager\'s to answer')


class ScopedToThisProgrammeTests(TestCase):
    """The board judges work on THIS programme, not all of Omni."""

    def test_unrelated_overdue_tasks_never_brand_somebody(self):
        """Caught on live data the day this shipped.

        The first person published as "not pushing automation" had no step due
        yet — only two overdue tasks from other work. The board must not import
        the company's general task debt and call it an automation judgement.
        """
        from core.models import OmniTask

        person = User.objects.create_user(
            'busy', 'busy@alphadirect.co.bw', 'pw',
            first_name='Busy', last_name='Person')
        Initiative.objects.create(
            code='SC-1', title='month four', track=Initiative.Track.SWITCH_ON,
            month=4, department='Claims', percent=0,
            target_date=_dt.date(2027, 1, 20),
            manager_name='Busy Person', manager_email='busy@alphadirect.co.bw')

        # Two overdue tasks that have nothing to do with this programme.
        for i in range(2):
            OmniTask.objects.create(
                assigner=person, assignee=person, title=f'unrelated {i}',
                due_at=_dt.date(2026, 1, 1), status='pending')

        board = pulse.manager_leaderboard()
        self.assertEqual(board['lagging'], [],
                         'unrelated task debt must not brand anyone here')
        reasons = {r['email']: r.get('why_unscored') for r in board['unscored']}
        self.assertIn('busy@alphadirect.co.bw', reasons)

    def test_a_task_this_board_raised_does_count(self):
        from transformation import assign as svc

        person = User.objects.create_user(
            'owner2', 'owner2@alphadirect.co.bw', 'pw',
            first_name='Own', last_name='Er')
        item = Initiative.objects.create(
            code='SC-2', title='month one', track=Initiative.Track.SWITCH_ON,
            month=1, department='Claims', percent=0,
            target_date=_dt.date(2026, 10, 20),
            manager_name='Own Er', manager_email='owner2@alphadirect.co.bw')
        svc.assign(item, 'owner2@alphadirect.co.bw',
                   due_date=_dt.date(2026, 1, 1), assigner=person)

        counts = pulse._overdue_tasks()
        self.assertEqual(counts['owner2@alphadirect.co.bw']['overdue'], 1,
                         'a task THIS board raised must still count')


class AutomationScoreMeansAutomationTests(TestCase):
    """A column headed "automation score" must not be a timesheet.

    Found by looking at the live board: Veritas showed 60 against 60%
    attendance, UniCoin 74 against 74%, Executive 84 against 84%. For a
    department with no step open and nothing shipped, attendance was the only
    measurable signal, so it BECAME the automation score — telling the CEO
    something the number does not mean.
    """

    def _department_with_attendance(self, name):
        from hris.models import HRISProfile, WorkdayJustification
        from payroll.models import Employee

        employee = Employee.objects.create(
            full_name=f'{name} Person', employee_number=f'AS-{name[:4]}',
            email=f'{name.lower().replace(" ", "")}@alphadirect.co.bw',
            department=name, status='active')
        profile = HRISProfile.objects.create(employee=employee)
        WorkdayJustification.objects.create(
            profile=profile, work_date=timezone.localdate() - _dt.timedelta(days=2),
            required_hours=8, tracked_hours=5, justified_hours=0, status='pending')
        DepartmentPlan.objects.create(department=name, headcount_target=3)

    def test_attendance_alone_never_becomes_the_automation_score(self):
        self._department_with_attendance('Veritas')
        # Through build_board, so the workforce figures reach the row exactly
        # as they do on the real screen.
        board = pulse.build_board()
        row = {r['department']: r for r in board['departments']}['Veritas']

        self.assertIsNotNone(row['attendance_percent'],
                             'attendance is still measured and still shown')
        self.assertIsNone(row['automation_score'],
                          'but it must not be published as an automation score')
        self.assertIn('attendance alone', row['why_unscored'])

    def test_a_step_that_has_not_opened_does_not_score_the_department(self):
        """A month-4 step at 0% on day one is not a green 100."""
        from unittest.mock import patch

        DepartmentPlan.objects.create(department='Sales & Marketing', headcount_target=4)
        Initiative.objects.create(
            code='SM-1', title='month four', track=Initiative.Track.ACQUISITION,
            month=4, department='Sales & Marketing', percent=0,
            target_date=_dt.date(2027, 1, 20))

        # 1-Oct, not day zero: the month-4 step is ~9% due, still inside the
        # grace band. Gating on > 0 passed this only because day zero makes
        # EVERY step look closed.
        with patch('transformation.pulse._today',
                   return_value=_dt.date(2026, 10, 1)):
            row = {r['department']: r
                   for r in pulse.department_scores()}['Sales & Marketing']
        self.assertIsNone(row['delivery_percent'])
        self.assertIsNone(row['automation_score'])

    def test_a_department_with_work_open_IS_scored(self):
        """The guard must not silence every department."""
        from unittest.mock import patch

        DepartmentPlan.objects.create(department='Claims', headcount_target=8)
        Initiative.objects.create(
            code='CL-1', title='month one', track=Initiative.Track.SWITCH_ON,
            month=1, department='Claims', percent=60,
            target_date=_dt.date(2026, 10, 20))

        with patch('transformation.pulse._today',
                   return_value=_dt.date(2026, 10, 1)):
            row = {r['department']: r for r in pulse.department_scores()}['Claims']
        self.assertIsNotNone(row['delivery_percent'])
        self.assertIsNotNone(row['automation_score'])


class PeopleGateHasATestTests(TestCase):
    """The people-side grace gate, with something that can fail.

    The gate flagged this: reverting `if due <= GRACE_POINTS` back to
    `if due <= 0` left all 77 tests green. A fix nothing can fail is not a
    fix — the next person who simplifies it back gets a clean build and a
    named colleague in "Pushing automation" for having started nothing.
    """

    def test_an_owner_of_only_future_work_is_never_published_as_pushing(self):
        from unittest.mock import patch

        # Two steps, so MIN_WORKLOAD_TO_SCORE cannot mask the result.
        for i in range(2):
            Initiative.objects.create(
                code=f'PG-{i}', title='month four',
                track=Initiative.Track.ACQUISITION, month=4, percent=0,
                department='Sales & Marketing',
                target_date=_dt.date(2027, 1, 20),
                manager_name='Future Owner',
                manager_email='future@alphadirect.co.bw')

        # 1 October: the step is ~9% due, still inside the grace band.
        with patch('transformation.pulse._today',
                   return_value=_dt.date(2026, 10, 1)):
            board = pulse.manager_leaderboard()

        self.assertEqual(board['pushing'], [],
                         'nobody earns credit for work that is not due yet')
        reasons = {r['email']: r.get('why_unscored') for r in board['unscored']}
        self.assertEqual(reasons.get('future@alphadirect.co.bw'),
                         'Nothing due yet — their steps open later')
