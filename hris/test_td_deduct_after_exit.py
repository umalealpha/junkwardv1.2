"""hris/test_td_deduct_after_exit.py — guard 8: was the person even employed?

Kakale Botana, Omni bug f8ded9e1 (17-Sep-2026):

    "Oratile Tlhomelang resigned on 14/09/2026. I have received the attached
     request to approve an Omni raised leave request due to tracked hours
     shortfall... I received the same email yesterday which I authorised in
     oversight and realised now that it was Tuesday when she had already
     resigned."

Every one of the seven guards passed, because they all ask about the DAY — is
the snapshot settled, is the record unjustified, is the figure still dark —
and none of them asks about the PERSON. Somebody who has left cannot track
hours, so every day after their exit is dark for ever and the chase runs until
a human notices. One already reached an approver and was approved.

Guard 8 asks the missing question. It fails the module's way: QUIET. A
terminated record with no date on file docks nothing rather than guessing.
"""
import datetime
from decimal import Decimal
from unittest import mock

from django.test import override_settings

from hris.management.commands.enforce_td_deductions import employed_on
from hris.models import LeaveRequest, WorkdayJustification as WJ
from hris.test_td_reconcile_enforce import DAY, UID, _Base


class EmployedOnTests(_Base):
    """The question itself, in isolation."""

    def test_a_plain_active_employee_is_employed(self):
        ok, why = employed_on(self.emp, DAY)
        self.assertTrue(ok)
        self.assertEqual(why, '')

    def test_a_day_after_the_termination_date_is_not(self):
        self.emp.termination_date = DAY - datetime.timedelta(days=3)
        ok, why = employed_on(self.emp, DAY)
        self.assertFalse(ok)
        self.assertIn('left the company', why)

    def test_the_termination_day_itself_still_counts_as_worked(self):
        # The last working day is a working day — docking it would be the same
        # error in the other direction.
        self.emp.termination_date = DAY
        ok, _ = employed_on(self.emp, DAY)
        self.assertTrue(ok)

    def test_terminated_with_no_date_docks_nothing(self):
        self.emp.status = 'terminated'
        self.emp.termination_date = None
        ok, why = employed_on(self.emp, DAY)
        self.assertFalse(ok)
        self.assertIn('cannot tell which days', why)

    def test_suspended_is_not_expected_to_track(self):
        self.emp.status = 'suspended'
        ok, why = employed_on(self.emp, DAY)
        self.assertFalse(ok)
        self.assertIn('suspended', why)

    def test_a_day_before_the_hire_date_is_not_employed(self):
        self.emp.hire_date = DAY + datetime.timedelta(days=1)
        ok, why = employed_on(self.emp, DAY)
        self.assertFalse(ok)
        self.assertIn('started on', why)


@override_settings(WORKFORCE_DATA_GUARD_AI=False)
class EnforceAfterExitTests(_Base):
    """The same happy path the other seven guards are tested against, with the
    one fact changed that Kakale reported."""

    def _happy(self):
        self._snap(0.0, slots=4, reported=[UID])
        self._wj(0, WJ.Status.UNJUSTIFIED)

    def test_still_employed_still_creates_the_request(self):
        # The control itself is untouched — this is the companion that proves
        # guard 8 did not quietly switch the enforcement off.
        self._happy()
        with mock.patch('core.notifications.notify_leave_pending_approval'):
            self._run('enforce_td_deductions', '--date', DAY.isoformat(),
                      '--apply', '--actor', 'mgr')
        self.assertEqual(LeaveRequest.objects.count(), 1)

    def test_a_day_after_the_employee_resigned_creates_nothing(self):
        self._happy()
        self.emp.termination_date = DAY - datetime.timedelta(days=3)
        self.emp.save()
        out = self._run('enforce_td_deductions', '--date', DAY.isoformat(),
                        '--apply', '--actor', 'mgr')
        self.assertEqual(LeaveRequest.objects.count(), 0)
        self.assertIn('left the company', out)

    def test_the_skip_reason_is_printed_for_the_audit_trail(self):
        self._happy()
        term = DAY - datetime.timedelta(days=3)
        self.emp.termination_date = term
        self.emp.save()
        out = self._run('enforce_td_deductions', '--date', DAY.isoformat(),
                        '--apply', '--actor', 'mgr')
        self.assertIn(str(term), out)
        self.assertIn(self.emp.full_name, out)

    def test_terminated_without_a_date_creates_nothing(self):
        self._happy()
        self.emp.status = 'terminated'
        self.emp.save()
        self._run('enforce_td_deductions', '--date', DAY.isoformat(),
                  '--apply', '--actor', 'mgr')
        self.assertEqual(LeaveRequest.objects.count(), 0)

    def test_a_run_of_days_spanning_the_exit_docks_only_the_employed_ones(self):
        # The command walks back --days from the given date; only the days the
        # person was still on the payroll may survive.
        self.emp.termination_date = DAY - datetime.timedelta(days=1)
        self.emp.save()
        self._happy()
        self._run('enforce_td_deductions', '--date', DAY.isoformat(),
                  '--days', '3', '--apply', '--actor', 'mgr')
        for lr in LeaveRequest.objects.all():
            self.assertLessEqual(lr.end_date, self.emp.termination_date)
        self.assertFalse(
            LeaveRequest.objects.filter(end_date__gt=self.emp.termination_date).exists())
        # and nothing is docked for DAY itself, which is after the exit
        self.assertEqual(
            LeaveRequest.objects.filter(start_date__lte=DAY, end_date__gte=DAY).count(), 0)

    def test_days_value_is_a_decimal_day_count_not_touched_by_the_guard(self):
        self._happy()
        with mock.patch('core.notifications.notify_leave_pending_approval'):
            self._run('enforce_td_deductions', '--date', DAY.isoformat(),
                      '--apply', '--actor', 'mgr')
        lr = LeaveRequest.objects.get()
        self.assertEqual(lr.days, Decimal('1'))
