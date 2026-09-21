"""hris/tests/test_hours_never_go_down.py

THE GUARDRAIL (CFO directive 2026-09-09, after the SAME employee reported the
same lost hours twice — bug 5dffc022 on 3-Sep, closed as resolved, then bug
c82def7f on 9-Sep).

Fixing `send_daily_brief` alone was a NO-OP on prod. `reconcile_workday_records`
runs 30 minutes later (07:35 UTC, and again 14:10 UTC), re-read the STALE 06:30
snapshot through `hours_by_employee`, and rewrote `tracked_hours` in EITHER
direction. Its guard refused to worsen a person's STATUS ("never convicts") but
never refused to lower their HOURS — so the corrected figure was pulled back
down every day, each time with a tidy audit row reading "Reconciled from final
Time Doctor snapshot". That is why the first report was closed and the same
thing happened again six days later.

The rule these tests pin: Time Doctor only ever ADDS time to a past day (an
offline machine uploading its buffer). A figure BELOW what is already recorded
therefore means an incomplete read, never that the person worked less. A genuine
reduction is a human decision, not a cron's.

Needs Postgres (the omni suite dies on sqlite at ledger migration 0022).
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from unittest import mock

from django.test import TestCase, override_settings

from core.models import User
from hris.models import HRISProfile, WorkdayJustification
from integrations.models import TimeDoctorDailySnapshot
from payroll.models import Employee

DAY = datetime.date(2026, 9, 8)
SNAPSHOT_HOURS = 1.74      # what the 06:30 pull captured (machine still offline)
LIVE_HOURS = 3.24          # what Time Doctor reports once the buffer uploaded


def _member(uid, name, email, hours):
    return {'user_id': uid, 'name': name, 'email': email,
            'hours_tracked': hours, 'productive_hours': hours,
            'productive_pct': 100.0, 'manual_hours': None}


class HoursNeverGoDownTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.emp = Employee.objects.create(
            employee_number='PC-002', full_name='Floor Rule Worker',
            email='floor.rule@alphadirect.co.bw', status='active',
            department='Compliance')
        cls.profile = HRISProfile.objects.create(employee=cls.emp)

    def setUp(self):
        self.snap = TimeDoctorDailySnapshot.objects.create(
            company_id='c-test', as_of=DAY,
            payload=[_member('u-fr', 'Floor Rule Worker',
                             'floor.rule@alphadirect.co.bw', SNAPSHOT_HOURS)],
            settle_samples={'0300': {'u-fr': 6264}, '0400': {'u-fr': 6264},
                            '0830': {'u-fr': 6264}})
        # The record as the daily brief now correctly leaves it: the LIVE figure.
        self.row = WorkdayJustification.objects.create(
            profile=self.profile, work_date=DAY,
            required_hours=Decimal('6.50'),
            tracked_hours=Decimal(str(LIVE_HOURS)),
            status='explained')

    def _reconcile(self, live):
        from hris.management.commands import reconcile_workday_records as cmd
        with mock.patch('integrations.td_live.live_members', return_value=live), \
             mock.patch('hris.eligibility.tracking_profiles',
                        return_value=[self.profile]):
            return cmd.reconcile_day(DAY, apply=True)

    def test_reconcile_does_not_pull_the_corrected_figure_back_down(self):
        """THE REPEAT BUG: live is unreachable, so reconcile sees only the stale
        1.74 h snapshot. It must NOT overwrite the recorded 3.24 h."""
        out = self._reconcile(None)
        self.row.refresh_from_db()
        self.assertEqual(
            float(self.row.tracked_hours), LIVE_HOURS,
            'reconcile lowered the corrected hours back to the stale snapshot — '
            'the exact reason this employee reported the same problem twice')
        self.assertEqual(len(out['refused_downward']), 1)
        self.assertEqual(out['corrected'], [])

    def test_reconcile_still_corrects_upward(self):
        """The guardrail must not break what this command is FOR: a false zero
        that turns out to be real hours still gets fixed in the person's favour."""
        self.row.tracked_hours = Decimal('0.00')
        self.row.save(update_fields=['tracked_hours'])
        out = self._reconcile([_member('u-fr', 'Floor Rule Worker',
                                       'floor.rule@alphadirect.co.bw', 5.73)])
        self.row.refresh_from_db()
        self.assertEqual(float(self.row.tracked_hours), 5.73)
        self.assertEqual(len(out['corrected']), 1)
        self.assertEqual(out['refused_downward'], [])

    def test_a_degenerate_live_read_of_all_zeros_is_ignored(self):
        """aggregate() emits a row per roster user, so a 200 response with an
        empty worklog looks like a full list of ZEROS. Writing that over a
        settled snapshot would record the whole company at 0 h and dock leave."""
        from integrations.td_live import rows_for_day
        with mock.patch('integrations.td_live.live_members',
                        return_value=[_member('u-fr', 'Floor Rule Worker',
                                              'floor.rule@alphadirect.co.bw', 0.0)]):
            rows, source = rows_for_day(DAY, self.snap.payload)
        self.assertEqual(source, 'snapshot (live read looked incomplete)')
        self.assertEqual(float(rows[0]['hours_tracked']), SNAPSHOT_HOURS)

    def test_a_higher_live_read_is_used(self):
        """The normal back-fill case: live has more, live wins."""
        from integrations.td_live import rows_for_day
        with mock.patch('integrations.td_live.live_members',
                        return_value=[_member('u-fr', 'Floor Rule Worker',
                                              'floor.rule@alphadirect.co.bw',
                                              LIVE_HOURS)]):
            rows, source = rows_for_day(DAY, self.snap.payload)
        self.assertEqual(source, 'live')
        self.assertEqual(float(rows[0]['hours_tracked']), LIVE_HOURS)


class AmbiguousManagerTests(TestCase):
    """A terminated duplicate sharing an address must never resolve as the
    manager (Fable review 2026-09-09): the leaver copy has no reports, so
    "nothing owed" would be true of the wrong person and a live manager's task
    would be closed while their team feedback was still outstanding."""

    def test_a_leaver_duplicate_does_not_resolve_the_manager(self):
        from hris.feedback_task_close import _manager_for
        user = User.objects.create_user(
            username='dup.mgr', email='dup.mgr@alphadirect.co.bw', password='x')
        # The leaver sorts FIRST by name, so the old .first() would have taken it.
        Employee.objects.create(
            employee_number='DUP-OLD', full_name='Aaa Leaver Copy',
            email='dup.mgr@alphadirect.co.bw',
            status=Employee.Status.TERMINATED, department='Compliance')
        live = Employee.objects.create(
            employee_number='DUP-NEW', full_name='Zzz Live Manager',
            email='dup.mgr@alphadirect.co.bw', status='active',
            department='Compliance')
        self.assertEqual(_manager_for(user), live)

    def test_two_active_rows_on_one_address_refuse_to_resolve(self):
        from hris.feedback_task_close import _manager_for
        user = User.objects.create_user(
            username='dup2.mgr', email='dup2@alphadirect.co.bw', password='x')
        for n in ('A', 'B'):
            Employee.objects.create(
                employee_number=f'DUP2-{n}', full_name=f'Person {n}',
                email='dup2@alphadirect.co.bw', status='active',
                department='Compliance')
        self.assertIsNone(_manager_for(user))


class DailyBriefRerunTests(TestCase):
    """The floor must be taken BEFORE the brief is built, not after.

    Fable round-3 finding 2c: applying it afterwards kept the higher hours but
    left `status`, `shortfall` and the emailed badge derived from the LOWER
    figure — a record reading 7.00 h stamped UNJUSTIFIED with a red
    below-target badge. And it would never have self-healed: reconcile only
    re-classifies when the gap exceeds its tolerance, so on the next run it
    either refuses (live lower) or skips (live equal). That false verdict feeds
    the Monthly Manager Return and the performance panel.
    """

    @classmethod
    def setUpTestData(cls):
        cls.emp = Employee.objects.create(
            employee_number='RR-001', full_name='Rerun Worker',
            email='rerun.worker@alphadirect.co.bw', status='active',
            department='Compliance')
        cls.profile = HRISProfile.objects.create(employee=cls.emp)

    def setUp(self):
        TimeDoctorDailySnapshot.objects.create(
            company_id='c-test', as_of=DAY,
            payload=[_member('u-rr', 'Rerun Worker',
                             'rerun.worker@alphadirect.co.bw', 2.0)],
            settle_samples={'0300': {'u-rr': 7200}, '0400': {'u-rr': 7200},
                            '0830': {'u-rr': 7200}})
        # A full day already on the record, met.
        self.row = WorkdayJustification.objects.create(
            profile=self.profile, work_date=DAY,
            required_hours=Decimal('6.50'), tracked_hours=Decimal('7.00'),
            status='met')

    @override_settings(WORKFORCE_DATA_GUARD_ENABLED=False)
    def test_a_rerun_keeps_the_status_that_matches_the_kept_hours(self):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        # A degenerate per-person live read: 2.00 h against a recorded 7.00 h.
        with mock.patch('integrations.td_live.live_members',
                        return_value=[_member('u-rr', 'Rerun Worker',
                                              'rerun.worker@alphadirect.co.bw', 2.0)]),              mock.patch('hris.eligibility._paid_employee_ids',
                        return_value={self.emp.id}):
            call_command('send_daily_brief', '--date', DAY.isoformat(),
                         '--force', '--no-email', stdout=out, stderr=StringIO())
        self.row.refresh_from_db()
        self.assertEqual(float(self.row.tracked_hours), 7.00)
        self.assertEqual(
            self.row.status, 'met',
            'the record kept 7.00 h but was stamped from the 2.00 h read — a '
            'contradiction that feeds the Monthly Manager Return and never self-heals')
