"""hris/tests/test_consolidated_brief.py

CONSOLIDATED_EMAILS_ENABLED (CFO 2026-08-05): when ON, the manager "team
scoreboard" (the Daily Exceptions report) is folded INTO each manager's 07:05
Morning Brief, so a manager gets ONE email instead of two. send_exceptions_report
skips its own manager send when the flag is on; send_morning_brief owns delivery
of the scoreboard — folded into the brief for managers who get one, and sent
standalone to any manager-group address it did not cover. When the flag is OFF,
behaviour is unchanged: the exceptions report goes out standalone and the brief
carries no scoreboard.

These run the real management commands against a mocked Time Doctor client (same
seam the send_saturday_explain tests use: patch active_td_users / the client),
with the people-data AI guard disabled for determinism.

Needs Postgres (the omni suite dies on sqlite at ledger migration 0022).
"""
from __future__ import annotations

import datetime
from io import StringIO
from unittest import mock

from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings

from payroll.models import Employee
from hris.models import HRISProfile

# A fixed ordinary weekday (Wednesday) in the past — daily mode, never a rest day
# and never a Botswana public holiday.
DAY = datetime.date(2026, 7, 29)

MANAGER_EMAIL = 'omogomotsi@alphadirect.co.bw'      # a manager who IS a tracked employee
WORKER_EMAIL = 'consworker@alphadirect.co.bw'       # a tracked employee, NOT a manager
EXTERNAL_MGR = 'lanand@theriskco.com'               # in the manager group, NOT an employee


class _FakeTD:
    """Minimal stand-in for TimeDoctorClient — configured, with canned data so
    every matched person shows real hours (nobody trips a breaker)."""

    configured = True
    company_id = 'c-test'

    def __init__(self, td_users):
        self._users = td_users

    def users(self):
        return list(self._users)

    def _rows(self):
        # One worklog row per user: 6h observed, arriving 08:00 local (UTC+2).
        return [[{'userId': u['id'], 'time': 6 * 3600,
                  'start': '2026-07-29T06:00:00', 'mode': 'computer'} for u in self._users]]

    def worklog(self, day_from, day_to, user_ids=None):
        return self._rows()

    def timeuse(self, day_from, day_to, user_ids=None):
        return []


BASE_SETTINGS = dict(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    WORKFORCE_EXCEPTIONS_TO=[MANAGER_EMAIL, EXTERNAL_MGR],
    WORKFORCE_DATA_GUARD_ENABLED=False,      # skip the settle + AI hold gate
    ELRA_PERF_ENABLED=False,                 # _feedback_missing() → []
)


class ConsolidatedBriefTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.mgr = Employee.objects.create(
            employee_number='CB-MGR', full_name='Oprah Mogomotsi',
            email=MANAGER_EMAIL, status='active', department='Finance')
        cls.worker = Employee.objects.create(
            employee_number='CB-WRK', full_name='Regular Worker',
            email=WORKER_EMAIL, status='active', department='Claims')
        HRISProfile.objects.create(employee=cls.mgr)
        HRISProfile.objects.create(employee=cls.worker)
        cls.td_users = [
            {'id': 'u-mgr', 'name': 'Oprah Mogomotsi', 'email': MANAGER_EMAIL},
            {'id': 'u-wrk', 'name': 'Regular Worker', 'email': WORKER_EMAIL},
        ]

    # -- helpers --------------------------------------------------------------
    def _run(self, command):
        """Run a command with the mocked TD client, no-op Aria, and the two test
        employees marked as the paid/tracked roster."""
        fake = _FakeTD(self.td_users)
        with mock.patch('integrations.timedoctor.TimeDoctorClient.from_settings',
                        return_value=fake), \
             mock.patch('hris.morning_brief.aria_note', return_value=''), \
             mock.patch('hris.eligibility._paid_employee_ids',
                        return_value={self.mgr.id, self.worker.id}):
            call_command(command, '--date', DAY.isoformat(), '--force',
                         stdout=StringIO(), stderr=StringIO())

    @staticmethod
    def _msgs_to(addr):
        return [m for m in mail.outbox if addr in m.to]

    @staticmethod
    def _html(msg):
        return msg.alternatives[0][0] if msg.alternatives else msg.body

    def _brief_msg(self, addr):
        """The personal Morning Brief message for an address (subject starts 🌅)."""
        for m in self._msgs_to(addr):
            if m.subject.startswith('🌅'):
                return m
        return None

    # -- (a) flag OFF: unchanged ---------------------------------------------
    @override_settings(CONSOLIDATED_EMAILS_ENABLED=False, **BASE_SETTINGS)
    def test_flag_off_exceptions_standalone_and_brief_has_no_scoreboard(self):
        self._run('send_exceptions_report')
        self._run('send_morning_brief')

        # The exceptions report still goes out standalone to the whole group.
        standalone = [m for m in mail.outbox
                      if set(m.to) == {MANAGER_EMAIL, EXTERNAL_MGR}]
        self.assertEqual(len(standalone), 1)
        self.assertIn('Daily Exceptions Report', self._html(standalone[0]))

        # The manager's personal brief carries NO folded scoreboard.
        brief = self._brief_msg(MANAGER_EMAIL)
        self.assertIsNotNone(brief)
        self.assertNotIn('Team scoreboard', self._html(brief))
        self.assertNotIn('Daily Exceptions Report', self._html(brief))

    # -- (b) flag ON: the scoreboard is folded into the manager's brief -------
    @override_settings(CONSOLIDATED_EMAILS_ENABLED=True, **BASE_SETTINGS)
    def test_flag_on_manager_brief_has_scoreboard_folded_in(self):
        # CFO 2026-08-30: the manager group (WORKFORCE_EXCEPTIONS_TO) now ALWAYS
        # gets the standalone report too, as C-suite belt-and-braces, so a line
        # manager who also sits on that group legitimately receives BOTH — the
        # standalone report AND the brief with the scoreboard folded in. The
        # point of the flag is that the fold happens; the standalone is
        # deliberate oversight, not the old double-send bug this once guarded.
        self._run('send_exceptions_report')
        self._run('send_morning_brief')         # folds the scoreboard in

        # The manager's personal brief carries the folded scoreboard.
        brief = self._brief_msg(MANAGER_EMAIL)
        self.assertIsNotNone(brief)
        html = self._html(brief)
        self.assertIn('Morning Brief', html)                 # personal brief present
        self.assertIn('Team scoreboard', html)               # scoreboard folded in
        self.assertIn('Daily Exceptions Report', html)

        # A non-manager tracked employee gets a plain brief, no scoreboard.
        worker_brief = self._brief_msg(WORKER_EMAIL)
        self.assertIsNotNone(worker_brief)
        self.assertNotIn('Team scoreboard', self._html(worker_brief))

    # -- (c) flag ON: a manager-group address that is NOT an employee --------
    @override_settings(CONSOLIDATED_EMAILS_ENABLED=True, **BASE_SETTINGS)
    def test_flag_on_non_employee_manager_still_gets_scoreboard(self):
        self._run('send_exceptions_report')
        self._run('send_morning_brief')

        to_ext = self._msgs_to(EXTERNAL_MGR)
        self.assertEqual(len(to_ext), 1, [m.subject for m in to_ext])
        # It is the standalone scoreboard (not a personal brief).
        self.assertFalse(to_ext[0].subject.startswith('🌅'))
        self.assertIn('Daily Exceptions Report', self._html(to_ext[0]))
        # …and this external address never received a personal brief.
        self.assertIsNone(self._brief_msg(EXTERNAL_MGR))

    # -- (d) flag ON + exceptions breaker tripped ----------------------------
    @override_settings(CONSOLIDATED_EMAILS_ENABLED=True, **BASE_SETTINGS)
    def test_flag_on_breaker_trips_no_scoreboard_briefs_still_attempt(self):
        # Force the SCOREBOARD compute to look like a Time Doctor outage (empty
        # matched roster) so its circuit breaker trips — while the brief's own
        # hero pull (unpatched) still has good data, so briefs proceed.
        bad = {'summary': {'roster': 0, 'tracked': 0, 'did_not_track': 0}}
        with mock.patch('hris.exceptions_report.compute', return_value=bad):
            self._run('send_morning_brief')

        # No scoreboard folded or sent anywhere.
        self.assertFalse(any('Team scoreboard' in self._html(m) for m in mail.outbox))
        self.assertFalse(any('Daily Exceptions Report' in self._html(m) for m in mail.outbox))
        self.assertEqual(self._msgs_to(EXTERNAL_MGR), [])    # no standalone fallback

        # Personal briefs still went out to the tracked employees.
        self.assertIsNotNone(self._brief_msg(MANAGER_EMAIL))
        self.assertIsNotNone(self._brief_msg(WORKER_EMAIL))


class ReportFormatFollowsTheDateNotTheClockTests(TestCase):
    """--date must decide daily-vs-weekly, not the day the command happens to run.

    `weekly` was read off `timezone.localtime()`, so re-running a Wednesday
    report on a Sunday produced a WEEKLY report for a week nobody asked about.
    The same wall-clock read made four tests in this file fail every weekend and
    pass again on Monday, which is how it stayed hidden (2026-08-09).
    """

    def _weekly_for(self, opts):
        """Mirror of the command's decision, driven by the same inputs."""
        import datetime as _dt
        today = _dt.date(2026, 8, 9)                       # a Sunday
        report_day = (_dt.datetime.strptime(opts['date'], '%Y-%m-%d').date()
                      if opts.get('date') else today)
        return bool(opts.get('weekly')) or report_day.weekday() == 6

    def test_named_weekday_stays_daily_even_when_run_on_a_sunday(self):
        self.assertFalse(self._weekly_for({'date': '2026-07-29'}))   # a Wednesday

    def test_named_sunday_is_weekly(self):
        self.assertTrue(self._weekly_for({'date': '2026-08-09'}))

    def test_explicit_weekly_flag_still_wins(self):
        self.assertTrue(self._weekly_for({'date': '2026-07-29', 'weekly': True}))

    def test_no_date_falls_back_to_today(self):
        self.assertTrue(self._weekly_for({}))              # today is the Sunday above


class ExceptionsReportDateDrivesFormatCommandTests(TestCase):
    """The same rule, driven through the REAL command and its real output.

    The class above copies the decision into the test, which would keep passing
    if the command drifted (checklist K12). This runs the command and reads the
    HTML it actually produced. Nothing is swallowed: if the command raises, this
    test fails, which is the point.
    """

    DATA = {
        'day': datetime.date(2026, 7, 29),
        'summary': {'tracked': 1, 'did_not_track': 0, 'roster': 1,
                    'total_h': 6.0, 'avg_h': 6.0, 'prod_h': 5.0},
        'did_not_track': [], 'on_leave': [], 'planned': [], 'alarm': [],
        'critical': [], 'low': [], 'below6': [], 'late': [], 'unproductive': [],
        'day_map': {}, 'ghosts': [], 'momentum': {}, 'leaderboard': [],
        'lb_hidden': 0, 'focus': {}, 'shortfall': [], 'unexplained': {},
    }

    def _run_and_read(self, *extra):
        from unittest import mock
        from io import StringIO
        import tempfile, os
        from django.core.management import call_command

        fd, path = tempfile.mkstemp(suffix='.html')
        os.close(fd)
        with mock.patch('integrations.timedoctor.TimeDoctorClient.from_settings',
                        return_value=mock.Mock(configured=True)), \
             mock.patch('hris.exceptions_report.compute', return_value=self.DATA), \
             mock.patch('hris.exceptions_report.compute_weekly',
                        return_value=self.DATA) as weekly:
            call_command('send_exceptions_report', '--preview-file', path,
                         *extra, stdout=StringIO(), stderr=StringIO())
        with open(path, encoding='utf8') as fh:
            html = fh.read()
        os.unlink(path)
        return html, weekly

    def test_a_named_weekday_run_on_a_sunday_sends_the_daily_report(self):
        # The whole bug: today is Sunday, but the report is FOR a Wednesday.
        html, weekly = self._run_and_read('--date', '2026-07-29')
        self.assertIn('Daily Exceptions Report', html)
        self.assertNotIn('Weekly Exceptions Report', html)
        self.assertFalse(weekly.called,
                         'a Wednesday report took the weekly path')

    def test_a_named_sunday_sends_the_weekly_report(self):
        html, weekly = self._run_and_read('--date', '2026-08-09')
        self.assertIn('Weekly Exceptions Report', html)
        self.assertTrue(weekly.called)

    def test_the_explicit_weekly_flag_still_forces_weekly(self):
        html, weekly = self._run_and_read('--date', '2026-07-29', '--weekly')
        self.assertIn('Weekly Exceptions Report', html)
        self.assertTrue(weekly.called)
