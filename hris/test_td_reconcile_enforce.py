"""TD-RECON-01 + TD-ENFORCE-01 — the two open CRITICAL findings of the Time Doctor
control audit (3-Sep-2026), built on the real 2-Sep shapes.

RECONCILE: the permanent day-record was a copy taken once at 09:05 and never
corrected. Natasha's 2-Sep row read 0.0 h "explained" while the final snapshot
held 5.73 h. The reconcile must rewrite the hours, clear the false shortfall,
leave real shortfalls and leave-facts alone, and never convict.

ENFORCE: the pay-docking step ran from a hand-typed server script with no guard.
The command must consider only the PUBLISHED did-not-track set, and skip anyone
who fails any one of seven guards — quiet, never docking on doubt.
"""
import datetime
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings

from core.models import AuditLog
from hris.models import HRISProfile, LeaveRequest, LeaveType, WorkdayJustification as WJ
from integrations.models import TimeDoctorDailySnapshot as S, TimeDoctorUserMap as M
from payroll.models import Employee

DAY = datetime.date(2026, 9, 2)
UID = 'adkFmpg_AyGD8aDl'


def _payload(uid, hours):
    return [{'user_id': uid, 'name': 'Natasha Nthite', 'hours_tracked': hours,
             'tracked_seconds': int(hours * 3600), 'productive_hours': hours}]


class _Base(TestCase):
    def setUp(self):
        self.mgr_user = User.objects.create_user('mgr', email='mgr@alphadirect.co.bw', password='x')
        self.emp_user = User.objects.create_user('natasha', email='nnthite@alphadirect.co.bw', password='x')
        self.emp = Employee.objects.create(full_name='Natasha Nthite', employee_number='RC-1',
                                           email='nnthite@alphadirect.co.bw', status='active',
                                           user=self.emp_user)
        self.mgr_emp = Employee.objects.create(full_name='Line Manager', employee_number='RC-M',
                                               email='mgr@alphadirect.co.bw', status='active',
                                               user=self.mgr_user)
        self.prof = HRISProfile.objects.create(employee=self.emp, manager=self.mgr_emp)
        M.objects.create(td_user_id=UID, td_name='Natasha Nthite', td_email='nnthite@alphadirect.co.bw',
                         employee=self.emp, confirmed=True)
        self.td_type, _ = LeaveType.objects.get_or_create(
            code='td_deduct', defaults={'name': 'Time Doctor Deduction'})

    def _snap(self, hours, slots=4, reported=None):
        return S.objects.create(
            company_id='X', as_of=DAY, payload=_payload(UID, hours),
            settle_samples={s: {UID: int(hours * 3600)} for s in ['0300', '0400', '0430', '0830'][:slots]},
            reported_no_track=reported or [])

    def _wj(self, tracked, status, required=Decimal('6.50')):
        return WJ.objects.create(profile=self.prof, work_date=DAY, required_hours=required,
                                 tracked_hours=Decimal(str(tracked)), status=status)

    def _run(self, cmd, *a):
        out = StringIO()
        with mock.patch('hris.eligibility.tracking_profiles', return_value=[self.prof]):
            call_command(cmd, *a, stdout=out)
        return out.getvalue()


@override_settings(WORKFORCE_DATA_GUARD_AI=False)
class ReconcileTests(_Base):
    def test_natasha_false_zero_gets_true_hours_but_stays_explained(self):
        # Her real case: 5.73 h against 6.5 required is still 0.77 h short, so
        # the honest record is "5.73 h, explained" — not "met". Hours corrected;
        # her own explanation stands; nobody is emailed because nothing cleared.
        self._snap(5.73)
        w = self._wj(0, WJ.Status.EXPLAINED)
        with mock.patch('core.notifications.send_html_with_cfo_cc') as send:
            out = self._run('reconcile_workday_records', '--date', DAY.isoformat(), '--apply')
        w.refresh_from_db()
        self.assertEqual(w.tracked_hours, Decimal('5.73'))
        self.assertEqual(w.status, WJ.Status.EXPLAINED)
        self.assertNotIn('CLEARED', out)
        self.assertEqual(send.call_count, 0)

    def test_false_zero_that_meets_the_requirement_is_cleared_to_met(self):
        # Brian Ngele / Kutlo Ntshole shape on 1-Sep: 0 on record, a full day in
        # the final snapshot. The false shortfall vanishes.
        self._snap(6.8)
        w = self._wj(0, WJ.Status.EXPLAINED)
        out = self._run('reconcile_workday_records', '--date', DAY.isoformat(), '--apply', '--no-email')
        w.refresh_from_db()
        self.assertEqual(w.tracked_hours, Decimal('6.80'))
        self.assertEqual(w.status, WJ.Status.MET)
        self.assertIn('CLEARED', out)

    def test_audit_row_written_for_the_correction(self):
        self._snap(5.73); w = self._wj(0, WJ.Status.EXPLAINED)
        self._run('reconcile_workday_records', '--date', DAY.isoformat(), '--apply', '--no-email')
        self.assertTrue(AuditLog.objects.filter(record_id=str(w.pk), description__icontains='Reconciled').exists())

    def test_dry_run_writes_nothing(self):
        self._snap(5.73); w = self._wj(0, WJ.Status.EXPLAINED)
        out = self._run('reconcile_workday_records', '--date', DAY.isoformat())
        w.refresh_from_db()
        self.assertEqual(w.tracked_hours, Decimal('0'))
        self.assertEqual(w.status, WJ.Status.EXPLAINED)
        self.assertIn('DRY-RUN', out)

    def test_a_real_shortfall_keeps_its_status_but_gets_true_hours(self):
        # 3.0 h against 6.5 required: still short. Hours update; verdict is NOT
        # touched — convicting belongs to the daily brief and its gates.
        self._snap(3.0); w = self._wj(0, WJ.Status.UNJUSTIFIED)
        self._run('reconcile_workday_records', '--date', DAY.isoformat(), '--apply', '--no-email')
        w.refresh_from_db()
        self.assertEqual(w.tracked_hours, Decimal('3.00'))
        self.assertEqual(w.status, WJ.Status.UNJUSTIFIED)

    def test_never_moves_a_day_to_a_worse_status(self):
        # Record says MET at 7h; the read now says 2h (a machine dropped out of
        # the merge, say). CONTRACT CHANGED 2026-09-09 (CFO guardrail): hours no
        # longer "follow the data" downward. Time Doctor only ever ADDS time to a
        # past day, so a lower figure means an incomplete read, not less work —
        # and this command re-imposing a stale figure is what made the same
        # employee report the same lost hours twice (bugs 5dffc022, c82def7f).
        # The refusal is recorded so it reaches a human; the verdict is still
        # never worsened from here.
        self._snap(2.0); w = self._wj(7.0, WJ.Status.MET)
        out = self._run('reconcile_workday_records', '--date', DAY.isoformat(),
                        '--apply', '--no-email')
        w.refresh_from_db()
        self.assertEqual(w.tracked_hours, Decimal('7.00'))   # kept, not lowered
        self.assertEqual(w.status, WJ.Status.MET)
        self.assertIn('REFUSED', out)
        self.assertIn('downward read(s) refused', out)

    def test_approved_leave_day_keeps_justified(self):
        self._snap(6.8); w = self._wj(0, WJ.Status.JUSTIFIED)
        self._run('reconcile_workday_records', '--date', DAY.isoformat(), '--apply', '--no-email')
        w.refresh_from_db()
        self.assertEqual(w.status, WJ.Status.JUSTIFIED)
        self.assertEqual(w.tracked_hours, Decimal('6.80'))

    def test_within_tolerance_is_left_alone(self):
        self._snap(5.75); w = self._wj(5.70, WJ.Status.MET)
        self._run('reconcile_workday_records', '--date', DAY.isoformat(), '--apply', '--no-email')
        w.refresh_from_db()
        self.assertEqual(w.tracked_hours, Decimal('5.70'))

    def test_no_snapshot_touches_nothing(self):
        w = self._wj(0, WJ.Status.EXPLAINED)
        out = self._run('reconcile_workday_records', '--date', DAY.isoformat(), '--apply', '--no-email')
        w.refresh_from_db()
        self.assertEqual(w.status, WJ.Status.EXPLAINED)
        self.assertIn('no snapshot', out)

    def test_docked_day_that_now_shows_hours_is_flagged_not_reversed(self):
        self._snap(5.73); self._wj(0, WJ.Status.UNJUSTIFIED)
        lr = LeaveRequest.objects.create(profile=self.prof, leave_type=self.td_type, start_date=DAY,
                                         end_date=DAY, days=1, status=LeaveRequest.Status.APPROVED)
        out = self._run('reconcile_workday_records', '--date', DAY.isoformat(), '--apply', '--no-email')
        lr.refresh_from_db()
        self.assertEqual(lr.status, LeaveRequest.Status.APPROVED)      # money untouched
        self.assertIn('CONFLICT', out)

    def test_person_is_emailed_once_when_a_false_shortfall_clears(self):
        self._snap(6.8); self._wj(0, WJ.Status.EXPLAINED)
        with mock.patch('core.notifications.send_html_with_cfo_cc', return_value=1) as send:
            self._run('reconcile_workday_records', '--date', DAY.isoformat(), '--apply')
        self.assertEqual(send.call_count, 1)
        subj, html, to = send.call_args[0][:3]
        self.assertIn('corrected', subj.lower())
        self.assertEqual(to, ['nnthite@alphadirect.co.bw'])
        self.assertIn('6.80', html)


@override_settings(WORKFORCE_DATA_GUARD_AI=False, WORKFORCE_DATA_GUARD_ENABLED=False)
class EnforceTests(_Base):
    """Guards 1-7. The happy path must create exactly one PENDING request; every
    other test removes ONE guard's precondition and must create nothing."""

    def _happy(self):
        self._snap(0.0, slots=4, reported=[UID])
        self._wj(0, WJ.Status.UNJUSTIFIED)

    def test_all_guards_pass_creates_one_pending_request_to_the_manager(self):
        self._happy()
        with mock.patch('core.notifications.notify_leave_pending_approval') as ntf:
            out = self._run('enforce_td_deductions', '--date', DAY.isoformat(), '--apply', '--actor', 'mgr')
        lr = LeaveRequest.objects.get(profile=self.prof, leave_type=self.td_type)
        self.assertEqual(lr.status, LeaveRequest.Status.PENDING)
        self.assertEqual(lr.requested_approver, self.mgr_user)
        self.assertEqual((lr.start_date, lr.end_date, lr.days), (DAY, DAY, Decimal('1')))
        self.assertIn('Guards passed', lr.reason)
        self.assertEqual(ntf.call_count, 1)
        self.assertIn('CREATED', out)
        # the human who ran it is on the audit trail
        self.assertTrue(AuditLog.objects.filter(record_id=str(lr.pk), user=self.mgr_user).exists())

    def test_dry_run_creates_nothing(self):
        self._happy()
        out = self._run('enforce_td_deductions', '--date', DAY.isoformat())
        self.assertEqual(LeaveRequest.objects.count(), 0)
        self.assertIn('WOULD create', out)

    def test_not_in_published_list_is_never_touched(self):
        # The morning report did not name them → they were never chased → no
        # 4pm cut-off applies to them.
        self._snap(0.0, slots=4, reported=[]); self._wj(0, WJ.Status.UNJUSTIFIED)
        self._run('enforce_td_deductions', '--date', DAY.isoformat(), '--apply')
        self.assertEqual(LeaveRequest.objects.count(), 0)

    def test_a_late_upload_the_snapshot_never_saw_stops_the_docking(self):
        """The leave-costing gap (Fable review 2026-09-09, finding B).

        The snapshot is frozen at the 06:30 pull and NEVER receives a late
        upload, so reading it could not keep this guard's promise that "hours
        that arrived since end it". Sequence that docked a real person a full
        unpaid day: machine offline at 06:30 (snapshot 0.00, settled, published
        dark) -> daily brief writes 0.00 UNJUSTIFIED -> buffer uploads 3.20 h at
        10:00 -> 14:10 reconcile raises the RECORD to 3.20 (status stays
        UNJUSTIFIED, because it never clears a real shortfall) -> 14:25 enforce
        reads the still-0.00 snapshot, calls it dark, and docks the day.

        Either source alone now stops it: the LIVE read, and the record itself.
        """
        self._snap(0.0, slots=4, reported=[UID])      # snapshot still says dark
        self._wj(3.20, WJ.Status.UNJUSTIFIED)         # record already corrected
        out = self._run('enforce_td_deductions', '--date', DAY.isoformat(), '--apply')
        self.assertEqual(
            LeaveRequest.objects.count(), 0,
            'a full unpaid day was docked off a frozen 0.00 h snapshot while the '
            'permanent record already showed 3.20 h')
        self.assertIn('not dark', out)

    def test_hours_that_arrived_since_end_it(self):
        # Natasha's exact case: published dark in the morning, 5.73 h by the
        # afternoon (second machine folded in). Must NOT dock.
        self._snap(5.73, slots=4, reported=[UID]); self._wj(0, WJ.Status.UNJUSTIFIED)
        out = self._run('enforce_td_deductions', '--date', DAY.isoformat(), '--apply')
        self.assertEqual(LeaveRequest.objects.count(), 0)
        self.assertIn('not dark', out)

    def test_an_explanation_on_record_blocks_it(self):
        self._snap(0.0, slots=4, reported=[UID]); self._wj(0, WJ.Status.EXPLAINED)
        out = self._run('enforce_td_deductions', '--date', DAY.isoformat(), '--apply')
        self.assertEqual(LeaveRequest.objects.count(), 0)
        self.assertIn('not unjustified', out)

    def test_no_record_at_all_blocks_it(self):
        self._snap(0.0, slots=4, reported=[UID])
        out = self._run('enforce_td_deductions', '--date', DAY.isoformat(), '--apply')
        self.assertEqual(LeaveRequest.objects.count(), 0)
        self.assertIn('no permanent record', out)

    def test_unsettled_snapshot_blocks_it(self):
        self._snap(0.0, slots=2, reported=[UID]); self._wj(0, WJ.Status.UNJUSTIFIED)
        out = self._run('enforce_td_deductions', '--date', DAY.isoformat(), '--apply')
        self.assertEqual(LeaveRequest.objects.count(), 0)
        self.assertIn('not settled', out)

    def test_a_guard_hold_blocks_it(self):
        self._happy()
        with mock.patch('hris.people_data_guard.held_uids', return_value={UID}):
            out = self._run('enforce_td_deductions', '--date', DAY.isoformat(), '--apply')
        self.assertEqual(LeaveRequest.objects.count(), 0)
        self.assertIn('HELD', out)

    def test_a_broken_guard_counts_as_held(self):
        self._happy()
        with mock.patch('hris.people_data_guard.held_uids', side_effect=RuntimeError('ai down')):
            self._run('enforce_td_deductions', '--date', DAY.isoformat(), '--apply')
        self.assertEqual(LeaveRequest.objects.count(), 0)

    def test_other_leave_on_the_day_blocks_it(self):
        self._happy()
        annual, _ = LeaveType.objects.get_or_create(code='annual', defaults={'name': 'Annual'})
        LeaveRequest.objects.create(profile=self.prof, leave_type=annual, start_date=DAY, end_date=DAY,
                                    days=1, status=LeaveRequest.Status.PENDING)
        out = self._run('enforce_td_deductions', '--date', DAY.isoformat(), '--apply')
        self.assertEqual(LeaveRequest.objects.filter(leave_type=self.td_type).count(), 0)
        self.assertIn('another leave', out)

    def test_idempotent_second_run_creates_nothing(self):
        self._happy()
        with mock.patch('core.notifications.notify_leave_pending_approval'):
            self._run('enforce_td_deductions', '--date', DAY.isoformat(), '--apply')
            self._run('enforce_td_deductions', '--date', DAY.isoformat(), '--apply')
        self.assertEqual(LeaveRequest.objects.filter(leave_type=self.td_type).count(), 1)

    def test_no_approver_means_no_request(self):
        self._happy()
        self.prof.manager = None; self.prof.save()
        out = self._run('enforce_td_deductions', '--date', DAY.isoformat(), '--apply')
        self.assertEqual(LeaveRequest.objects.count(), 0)
        self.assertIn('no active leave approver', out)

    def test_refuses_to_run_on_today(self):
        from django.core.management.base import CommandError
        from django.utils import timezone
        with self.assertRaises(CommandError):
            call_command('enforce_td_deductions', '--date', timezone.localtime().date().isoformat())

    def test_before_enforcement_start_is_skipped(self):
        early = datetime.date(2026, 8, 20)
        S.objects.create(company_id='X', as_of=early, payload=_payload(UID, 0.0),
                         settle_samples={s: {UID: 0} for s in ['0300', '0400', '0430', '0830']},
                         reported_no_track=[UID])
        WJ.objects.create(profile=self.prof, work_date=early, required_hours=Decimal('6.5'),
                          tracked_hours=0, status=WJ.Status.UNJUSTIFIED)
        out = self._run('enforce_td_deductions', '--date', early.isoformat(), '--apply')
        self.assertEqual(LeaveRequest.objects.count(), 0)
        self.assertIn('not active', out)


@override_settings(WORKFORCE_DATA_GUARD_AI=False, WORKFORCE_DATA_GUARD_ENABLED=False)
class EnforceGuardNineTests(_Base):
    """Guard 9 (Time Doctor control check, 20-Sep-2026): an account that is NOT in
    Time Doctor's own user list for the day cannot have tracked, so its zero is
    IT's problem, never the person's. Bakang Taote and Conrad Seretse dropped out
    of the roster on 15-Sep-2026 (archived on the Time Doctor side), were still
    PUBLISHED as did-not-track, and were docked."""

    def test_account_missing_from_the_days_roster_is_never_docked(self):
        # Published as did-not-track, record unjustified, snapshot settled —
        # but the snapshot's roster (payload) does not carry the account at all.
        S.objects.create(
            company_id='X', as_of=DAY, payload=_payload('someone_else_uid', 5.0),
            settle_samples={s: {} for s in ['0300', '0400', '0430', '0830']},
            reported_no_track=[UID])
        self._wj(0, WJ.Status.UNJUSTIFIED)
        with mock.patch('core.notifications.notify_leave_pending_approval'):
            out = self._run('enforce_td_deductions', '--date', DAY.isoformat(), '--apply')
        self.assertEqual(LeaveRequest.objects.count(), 0,
                         'docked a person whose Time Doctor account did not exist that day')
        self.assertIn('not in Time Doctor', out)

    def test_account_present_at_zero_hours_still_goes_through(self):
        # Control: same day, account IS on the roster at 0 h → guard 9 passes and
        # the happy path still creates the request.
        self._snap(0.0, slots=4, reported=[UID])
        self._wj(0, WJ.Status.UNJUSTIFIED)
        with mock.patch('core.notifications.notify_leave_pending_approval'):
            self._run('enforce_td_deductions', '--date', DAY.isoformat(), '--apply')
        self.assertEqual(LeaveRequest.objects.count(), 1)
