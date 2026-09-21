"""hris/tests/test_hours_floor_readers.py

The five screens that still read the FROZEN Time Doctor snapshot after the
9-Sep-2026 hours work (CFO: "fix those five screens too").

Background. `TimeDoctorDailySnapshot` freezes at the 06:30 UTC pull, and Time
Doctor BACK-FILLS time a machine buffered while it was offline. The permanent
record (`WorkdayJustification.tracked_hours`) is corrected upward afterwards and,
since checklist L28, can never be revised down. So the record is the better of
the two numbers and these screens were each showing somebody less time than Omni
already held for them:

  1. `send_weekly_hours_review`  — manager-facing weekly review (false shortfall
                                   in writing) -> floored by the record, and
                                   FAILS CLOSED if the record cannot be read.
  2. `send_saturday_explain`     — asks a person to justify a Saturday shortfall
                                   -> floored by the record.
  3. `send_hours_reminder`       — 10-day "typical hours" median behind the
                                   suspect-reminder guard -> floored per day.
  4. `leave_excuse_service`      — the Excuses feed. Reads PRODUCTIVE hours, so
                                   it CANNOT use the record (which holds TRACKED)
                                   -> reads live via `td_live.rows_for_day`.
  5. `reporting/cost_per_hour`   — a whole month inside an uncached web request;
                                   31 live pulls would time the page out, so it
                                   keeps reading the snapshot and the new 15:50
                                   SAST catch-up pull refreshes what it reads.

The metric trap these tests exist to pin: productive hours are always <= tracked
hours, so flooring a productive figure with the tracked record would silently
INFLATE it. Fix 4 must never use `hours_for_day.floored`.

Needs Postgres (the omni suite dies on sqlite at ledger migration 0022).
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.cache import cache
from django.test import TestCase, override_settings

from hris.hours_for_day import RecordUnavailable, floored
from hris.models import HRISProfile, WorkdayJustification
from integrations.models import TimeDoctorDailySnapshot, TimeDoctorUserMap
from payroll.models import Employee

# A Tuesday. Weekday target applies, not a Botswana public holiday.
DAY = datetime.date(2026, 9, 8)

SNAPSHOT_HOURS = 1.74      # what the 06:30 pull captured (machine still offline)
RECORD_HOURS = 3.24        # what the record holds after the correction


def _member(uid, name, email, tracked, productive=None):
    return {'user_id': uid, 'name': name, 'email': email,
            'hours_tracked': tracked,
            'productive_hours': tracked if productive is None else productive,
            'productive_pct': 100.0, 'manual_hours': None}


class FloorByRecordTests(TestCase):
    """The helper itself."""

    @classmethod
    def setUpTestData(cls):
        cls.emp = Employee.objects.create(
            employee_number='HF-001', full_name='Floored Worker',
            email='floored@alphadirect.co.bw', status='active', department='Compliance')
        cls.profile = HRISProfile.objects.create(employee=cls.emp)
        cls.other = Employee.objects.create(
            employee_number='HF-002', full_name='No Tracker Person',
            email='notracker@alphadirect.co.bw', status='active', department='Compliance')
        HRISProfile.objects.create(employee=cls.other)

    def test_it_raises_a_figure_that_is_below_the_record(self):
        WorkdayJustification.objects.create(
            profile=self.profile, work_date=DAY, required_hours=Decimal('6.50'),
            tracked_hours=Decimal(str(RECORD_HOURS)), status='explained')
        out = floored(DAY, {self.emp.id: SNAPSHOT_HOURS})
        self.assertEqual(
            out[self.emp.id], RECORD_HOURS,
            'the screen would show 1.74 h while the record holds 3.24 h')

    def test_it_leaves_a_figure_above_the_record_alone(self):
        """The snapshot can legitimately be AHEAD of the record (a day not yet
        reconciled). The floor raises, it never caps."""
        WorkdayJustification.objects.create(
            profile=self.profile, work_date=DAY, required_hours=Decimal('6.50'),
            tracked_hours=Decimal('2.00'), status='unjustified')
        out = floored(DAY, {self.emp.id: 7.5})
        self.assertEqual(out[self.emp.id], 7.5)

    def test_it_never_invents_an_entry_for_someone_with_no_tracker(self):
        """Absent means "no matched Time Doctor account", which callers tell
        apart from a matched zero. Inventing a key here would make a person with
        no tracker look tracked."""
        WorkdayJustification.objects.create(
            profile=HRISProfile.objects.get(employee=self.other), work_date=DAY,
            required_hours=Decimal('6.50'), tracked_hours=Decimal('5.00'), status='met')
        out = floored(DAY, {self.emp.id: 1.0})
        self.assertNotIn(self.other.id, out)

    def test_no_record_for_the_day_changes_nothing(self):
        out = floored(DAY, {self.emp.id: SNAPSHOT_HOURS})
        self.assertEqual(out[self.emp.id], SNAPSHOT_HOURS)

    def test_an_empty_input_is_returned_untouched(self):
        self.assertEqual(floored(DAY, {}), {})

    def test_it_fails_closed_when_the_record_cannot_be_read(self):
        """The default must REFUSE, not fall back to the un-floored figure.

        Falling back would hand the caller exactly the understated number this
        exists to prevent — and both callers that use the default put a shortfall
        in writing to a person. An unproven shortfall never becomes an accusation.
        """
        with mock.patch('hris.models.WorkdayJustification.objects.filter',
                        side_effect=RuntimeError('database gone')):
            with self.assertRaises(RecordUnavailable):
                floored(DAY, {self.emp.id: SNAPSHOT_HOURS})

    def test_it_can_degrade_where_the_figure_is_only_decorative(self):
        """The opt-in for the "typical hours" median, where killing the whole
        reminder run would be worse than a stale number."""
        with mock.patch('hris.models.WorkdayJustification.objects.filter',
                        side_effect=RuntimeError('database gone')):
            out = floored(DAY, {self.emp.id: SNAPSHOT_HOURS}, on_error='degrade')
        self.assertEqual(out[self.emp.id], SNAPSHOT_HOURS)


class SaturdayExplainTests(TestCase):
    """Screen 2 — never ask someone to explain a shortfall Omni already fixed."""

    SAT = datetime.date(2026, 9, 5)          # a Saturday

    @classmethod
    def setUpTestData(cls):
        cls.emp = Employee.objects.create(
            employee_number='SE-001', full_name='Saturday Worker',
            email='saturday.worker@alphadirect.co.bw', status='active',
            department='Compliance')
        cls.profile = HRISProfile.objects.create(employee=cls.emp)
        TimeDoctorUserMap.objects.create(
            td_user_id='u-sat', td_name='Saturday Worker',
            td_email='saturday.worker@alphadirect.co.bw',
            employee=cls.emp, confirmed=True)

    def setUp(self):
        # The command's default band is "zero" — it chases people the snapshot
        # shows at 0 h, which is exactly the shape a machine that was offline all
        # morning produces.
        TimeDoctorDailySnapshot.objects.create(
            company_id='c-test', as_of=self.SAT,
            payload=[_member('u-sat', 'Saturday Worker',
                             'saturday.worker@alphadirect.co.bw', 0.0)])

    def _run(self):
        """Run the command with a stand-in Time Doctor.

        The people-data guard inside this command does a LIVE roster read and
        FAILS CLOSED — an unproven zero must never become an accusation — so
        without a client it aborts the entire send and every assertion below
        would pass for the wrong reason.
        """
        class _FakeTD:
            configured = True
            company_id = 'c-test'

            def users(self):
                return [{'id': 'u-sat', 'name': 'Saturday Worker',
                         'email': 'saturday.worker@alphadirect.co.bw'}]

        out = StringIO()
        with mock.patch('hris.eligibility.tracking_profiles',
                        return_value=[self.profile]), \
             mock.patch('integrations.timedoctor.TimeDoctorClient.from_settings',
                        return_value=_FakeTD()), \
             mock.patch('hris.people_data_guard.held_uids', return_value={}):
            call_command('send_saturday_explain', '--date', self.SAT.isoformat(),
                         stdout=out, stderr=StringIO())
        return out.getvalue()

    def test_a_corrected_saturday_is_not_chased(self):
        """THE BUG: the snapshot froze at 0 h; the record was corrected to
        4.10 h, comfortably over the 3 h Saturday rule. Asking this person to
        explain themselves accuses them off a figure Omni knows is stale."""
        WorkdayJustification.objects.create(
            profile=self.profile, work_date=self.SAT, required_hours=Decimal('3.00'),
            tracked_hours=Decimal('4.10'), status='met')
        out = self._run()
        self.assertNotIn('Saturday Worker', out)

    def test_a_genuinely_short_saturday_is_still_chased(self):
        """The counterweight: the floor must not excuse a real shortfall."""
        WorkdayJustification.objects.create(
            profile=self.profile, work_date=self.SAT, required_hours=Decimal('3.00'),
            tracked_hours=Decimal('0.00'), status='unjustified')
        out = self._run()
        self.assertIn('Saturday Worker', out)


class ExcusesFeedMetricTests(TestCase):
    """Screen 4 — the metric trap.

    The Excuses feed reads PRODUCTIVE hours. The permanent record holds TRACKED
    hours, and productive is always <= tracked. So flooring this feed with the
    record would silently inflate a person's productive time and hide a real
    shortfall. It must read live instead.
    """

    @classmethod
    def setUpTestData(cls):
        from hris.models import TrackingDirective
        cls.emp = Employee.objects.create(
            employee_number='EX-001', full_name='Excuse Worker',
            email='excuse.worker@alphadirect.co.bw', status='active',
            department='Compliance')
        cls.profile = HRISProfile.objects.create(employee=cls.emp)
        TrackingDirective.objects.create(employee=cls.emp, expected_to_track=True)
        TimeDoctorUserMap.objects.create(
            td_user_id='u-ex', td_name='Excuse Worker',
            td_email='excuse.worker@alphadirect.co.bw',
            employee=cls.emp, confirmed=True)

    def setUp(self):
        # The live Time Doctor pull is cached per day inside
        # leave_excuse_service (added 2026-09-09 so a slow Time Doctor cannot
        # stall the page). Both tests below mock a DIFFERENT live answer for the
        # same day, so without this the second one silently reads the first
        # one's cached rows and proves nothing.
        cache.clear()
        # Snapshot froze low; the record (TRACKED) was corrected high.
        TimeDoctorDailySnapshot.objects.create(
            company_id='c-test', as_of=DAY,
            payload=[_member('u-ex', 'Excuse Worker',
                             'excuse.worker@alphadirect.co.bw', 1.0, productive=0.8)])
        WorkdayJustification.objects.create(
            profile=self.profile, work_date=DAY, required_hours=Decimal('6.50'),
            tracked_hours=Decimal('6.60'), status='met')

    def test_a_late_synced_day_drops_off_the_feed(self):
        """THE BUG: the feed lists whoever is at or under the low-hours bar, and
        it was reading the frozen 0.8 h. Live productive is 6.2 h — well clear of
        the 4 h bar — so this person should not be on a manager's chase list."""
        from hris.leave_excuse_service import build_day
        with mock.patch('integrations.td_live.live_members',
                        return_value=[_member('u-ex', 'Excuse Worker',
                                              'excuse.worker@alphadirect.co.bw',
                                              6.6, productive=6.2)]):
            day = build_day(date=DAY)
        names = [r.get('employee') for r in day['rows']]
        self.assertNotIn(
            'Excuse Worker', names,
            'still on the excuses feed against the stale 0.8 h figure')

    def test_productive_hours_are_never_floored_by_the_tracked_record(self):
        """Guards the trap directly: 6.60 h TRACKED on the record must never be
        presented as 6.60 h PRODUCTIVE. Live productive here is 2.10 h."""
        from hris.leave_excuse_service import build_day
        with mock.patch('integrations.td_live.live_members',
                        return_value=[_member('u-ex', 'Excuse Worker',
                                              'excuse.worker@alphadirect.co.bw',
                                              6.6, productive=2.1)]):
            day = build_day(date=DAY)
        row = next((r for r in day['rows']
                    if r.get('employee') == 'Excuse Worker'), None)
        self.assertIsNotNone(row, day)
        self.assertAlmostEqual(
            float(row['productive_hours']), 2.1, places=2,
            msg='a TRACKED figure leaked into the PRODUCTIVE column, which would '
                'inflate productive time and hide a real shortfall')

@override_settings(HOURS_REMINDERS_ENABLED=False)
class WeeklyHoursReviewTests(TestCase):
    """Screen 1 — the manager-facing weekly review.

    This one puts a shortfall in WRITING to a person and their manager, so a
    stale figure here is an accusation. Deterministic: the command takes --date
    (any day in the target week), so no clock mocking is needed.

    Week of Mon 7-Sep-2026, run on Wed 9-Sep. Completed weekdays are Mon and Tue,
    so the to-date target is 2 x 6.5 = 13.0 h.
    """

    RUN_ON = datetime.date(2026, 9, 9)       # Wednesday
    MON = datetime.date(2026, 9, 7)
    TUE = datetime.date(2026, 9, 8)

    @classmethod
    def setUpTestData(cls):
        cls.emp = Employee.objects.create(
            employee_number='WR-001', full_name='Weekly Worker',
            email='weekly.worker@alphadirect.co.bw', status='active',
            department='Compliance', job_title='Claims Officer')
        cls.profile = HRISProfile.objects.create(employee=cls.emp)
        TimeDoctorUserMap.objects.create(
            td_user_id='u-wk', td_name='Weekly Worker',
            td_email='weekly.worker@alphadirect.co.bw',
            employee=cls.emp, confirmed=True)

    def _snap(self, day, hours):
        TimeDoctorDailySnapshot.objects.create(
            company_id='c-test', as_of=day,
            payload=[_member('u-wk', 'Weekly Worker',
                             'weekly.worker@alphadirect.co.bw', hours)])

    def _run(self):
        class _FakeTD:
            configured = True
            company_id = 'c-test'

            def users(self):
                return [{'id': 'u-wk', 'name': 'Weekly Worker',
                         'email': 'weekly.worker@alphadirect.co.bw'}]

        out = StringIO()
        with mock.patch('hris.eligibility.tracking_profiles',
                        return_value=[self.profile]), \
             mock.patch('integrations.timedoctor.TimeDoctorClient.from_settings',
                        return_value=_FakeTD()):
            call_command('send_weekly_hours_review', '--date', self.RUN_ON.isoformat(),
                         '--force', stdout=out, stderr=StringIO())
        return out.getvalue()

    def test_a_late_synced_day_does_not_become_a_written_shortfall(self):
        """THE BUG: Tuesday's snapshot froze at 1.74 h while the permanent record
        was corrected to 6.50 h. Read from the snapshot the person is told they
        are 4.8 h short for the week; read from the record they are square."""
        self._snap(self.MON, 6.5)
        self._snap(self.TUE, 1.74)
        WorkdayJustification.objects.create(
            profile=self.profile, work_date=self.TUE,
            required_hours=Decimal('6.50'), tracked_hours=Decimal('6.50'), status='met')
        out = self._run()
        self.assertIn('Weekly Worker', out)
        self.assertIn(
            '0.0h short', out,
            'the weekly review told this person they were short against a Tuesday '
            'figure Omni had already corrected: ' + out)

    def test_a_real_shortfall_is_still_reported(self):
        """The counterweight: the floor must not erase a genuine gap."""
        self._snap(self.MON, 6.5)
        self._snap(self.TUE, 1.74)
        WorkdayJustification.objects.create(
            profile=self.profile, work_date=self.TUE,
            required_hours=Decimal('6.50'), tracked_hours=Decimal('1.74'),
            status='unjustified')
        out = self._run()
        self.assertIn('4.8h short', out, out)
