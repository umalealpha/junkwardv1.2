"""Approving leave clears the attendance days it covers (CFO 2026-08-03).

The live failure this locks down: Oratile Ria Tlhomelang took 27–31 Jul 2026,
captured it in Omni on 3 Aug, and her manager approved it four minutes later —
but all five days stayed `unjustified` on the daily attendance record, because
send_daily_brief only asks about approved leave on the morning it runs. That
record is what manager_accountability walks, so she was still on course to be
chased (and escalated to HR) for leave that had been granted.

Covers: the back-fill itself; that it never invents days Omni holds no record
for; that a worked day is not overwritten; that a half-day of leave does NOT
clear a whole day of missing hours; and that both approval routes trigger it.
"""
import datetime as _dt
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import Company, Currency
from hris.leave_backfill import backfill_workdays_for_leave
from hris.models import HRISProfile, LeaveRequest, LeaveType, WorkdayJustification
from payroll.models import Employee

D = _dt.date
WJ = WorkdayJustification


class LeaveBackfillTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        cls.company = Company.objects.create(code='ADIC', name='Alpha Direct')
        cls.lt = LeaveType.objects.create(code='annual', name='Annual Leave')

        cls.staff_user = User.objects.create_user('oratile', email='o@ad.co.bw', password='x')
        cls.staff_emp = Employee.objects.create(
            employee_number='E1', full_name='Oratile T', company=cls.company, user=cls.staff_user)
        cls.mgr_user = User.objects.create_user('kakale', email='k@ad.co.bw', password='x')
        cls.mgr_emp = Employee.objects.create(
            employee_number='E2', full_name='Kakale B', company=cls.company, user=cls.mgr_user)
        cls.profile = HRISProfile.objects.create(employee=cls.staff_emp, manager=cls.mgr_emp)

        # The in-app queue (decide_leave) is a PRIVILEGED surface — it needs HRIS
        # access on top of approve_team_leave, which is precisely why ordinary
        # line managers use the one-click email button instead. Superuser →
        # 'superadmin', same scaffolding as test_leave_admin's HR head.
        cls.hr = User.objects.create_superuser('unami', email='ubutale@ad.co.bw', password='x')

    # ── helpers ─────────────────────────────────────────────────────────────
    def _leave(self, status=LeaveRequest.Status.APPROVED, sdt='full', edt='full',
               start=D(2026, 7, 27), end=D(2026, 7, 31)):
        return LeaveRequest.objects.create(
            profile=self.profile, leave_type=self.lt, start_date=start, end_date=end,
            days=Decimal('5'), start_day_type=sdt, end_day_type=edt, status=status)

    def _day(self, d, *, required='6.50', tracked='0.00', status=WJ.Status.UNJUSTIFIED):
        return WJ.objects.create(
            profile=self.profile, work_date=d,
            required_hours=Decimal(required), tracked_hours=Decimal(tracked), status=status)

    def _dark_week(self):
        """Oratile's real rows: Mon–Fri 27–31 Jul, 6.5h required, 0h tracked."""
        return [self._day(D(2026, 7, day)) for day in range(27, 32)]

    # ── the core fix ────────────────────────────────────────────────────────
    def test_approved_leave_clears_every_unjustified_day(self):
        self._dark_week()
        lr = self._leave()
        self.assertEqual(backfill_workdays_for_leave(lr), 5)
        for row in WJ.objects.filter(profile=self.profile):
            self.assertEqual(row.status, WJ.Status.JUSTIFIED)
            self.assertEqual(row.reason, WJ.Reason.ON_LEAVE)
            self.assertEqual(row.linked_leave_id, lr.id)
            self.assertEqual(row.justified_hours, Decimal('6.50'))

    def test_pending_leave_changes_nothing(self):
        self._dark_week()
        lr = self._leave(status=LeaveRequest.Status.PENDING)
        self.assertEqual(backfill_workdays_for_leave(lr), 0)
        self.assertEqual(
            WJ.objects.filter(profile=self.profile, status=WJ.Status.UNJUSTIFIED).count(), 5)

    def test_refused_leave_changes_nothing(self):
        self._dark_week()
        self.assertEqual(backfill_workdays_for_leave(
            self._leave(status=LeaveRequest.Status.REFUSED)), 0)

    def test_never_creates_a_day_omni_has_no_record_for(self):
        # Only Mon has a row; the other four days must NOT be invented — a day
        # with no record is not an accusation and must not become one.
        self._day(D(2026, 7, 27))
        self.assertEqual(backfill_workdays_for_leave(self._leave()), 1)
        self.assertEqual(WJ.objects.filter(profile=self.profile).count(), 1)

    def test_worked_day_is_not_overwritten(self):
        self._day(D(2026, 7, 27), tracked='6.92', status=WJ.Status.MET)
        self._day(D(2026, 7, 28))
        self.assertEqual(backfill_workdays_for_leave(self._leave()), 1)
        met = WJ.objects.get(work_date=D(2026, 7, 27))
        self.assertEqual(met.status, WJ.Status.MET)
        self.assertEqual(met.reason, WJ.Reason.NONE)
        self.assertIsNone(met.linked_leave_id)

    def test_off_day_is_left_alone(self):
        self._day(D(2026, 7, 26), required='0.00', status=WJ.Status.NOT_REQUIRED)
        self.assertEqual(backfill_workdays_for_leave(
            self._leave(start=D(2026, 7, 26), end=D(2026, 7, 26))), 0)

    def test_already_justified_day_is_not_rewritten(self):
        self._day(D(2026, 7, 27), status=WJ.Status.JUSTIFIED)
        self.assertEqual(backfill_workdays_for_leave(self._leave()), 0)

    def test_partly_worked_day_is_cleared_by_the_remaining_gap(self):
        self._day(D(2026, 7, 27), tracked='4.00')
        backfill_workdays_for_leave(self._leave())
        row = WJ.objects.get(work_date=D(2026, 7, 27))
        self.assertEqual(row.status, WJ.Status.JUSTIFIED)
        self.assertEqual(row.justified_hours, Decimal('2.50'))   # 6.5 - 4.0

    def test_half_day_does_not_clear_a_whole_missing_day(self):
        # Half a day of leave against 6.5h missing justifies 3.25h — not enough,
        # so the day stays UNJUSTIFIED for the manager to look at.
        self._day(D(2026, 7, 27))
        lr = self._leave(sdt='pm', start=D(2026, 7, 27), end=D(2026, 7, 27))
        self.assertEqual(backfill_workdays_for_leave(lr), 1)
        row = WJ.objects.get(work_date=D(2026, 7, 27))
        self.assertEqual(row.status, WJ.Status.UNJUSTIFIED)
        self.assertEqual(row.justified_hours, Decimal('3.25'))
        self.assertEqual(row.linked_leave_id, lr.id)            # notice IS on record

    def test_half_day_clears_a_day_that_is_only_half_short(self):
        self._day(D(2026, 7, 27), tracked='3.25')
        lr = self._leave(edt='am', start=D(2026, 7, 27), end=D(2026, 7, 27))
        backfill_workdays_for_leave(lr)
        self.assertEqual(WJ.objects.get(work_date=D(2026, 7, 27)).status, WJ.Status.JUSTIFIED)

    def test_is_idempotent(self):
        self._dark_week()
        lr = self._leave()
        self.assertEqual(backfill_workdays_for_leave(lr), 5)
        self.assertEqual(backfill_workdays_for_leave(lr), 0)     # second pass = no churn

    # ── both approval routes must trigger it ────────────────────────────────
    def test_in_app_approval_triggers_the_backfill(self):
        self._dark_week()
        lr = self._leave(status=LeaveRequest.Status.PENDING)
        c = APIClient()
        c.force_authenticate(user=self.hr)
        r = c.post(f'/hris/api/leave-requests/{lr.id}/decide/',
                   {'decision': 'approve'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(
            WJ.objects.filter(profile=self.profile, status=WJ.Status.JUSTIFIED).count(), 5)

    def test_one_click_email_approval_triggers_the_backfill(self):
        from hris.leave_actions import make_leave_action_token

        self._dark_week()
        lr = self._leave(status=LeaveRequest.Status.PENDING)
        lr.requested_approver = self.mgr_user
        lr.save(update_fields=['requested_approver'])
        token = make_leave_action_token(lr, self.mgr_user)
        r = self.client.post(f'/hris/api/leave-action/{token}/submit/',
                             {'decision': 'approve'})
        self.assertEqual(r.status_code, 200, r.content[:400])
        lr.refresh_from_db()
        self.assertEqual(lr.status, LeaveRequest.Status.APPROVED)
        self.assertEqual(
            WJ.objects.filter(profile=self.profile, status=WJ.Status.JUSTIFIED).count(), 5)


class DarkStreakJustifiedTest(TestCase):
    """An approved-leave day must END a dark streak (CFO 2026-08-03).

    'justified' is the strongest explanation Omni holds — approved leave, an
    approved client visit, or an explanation a manager signed off — yet it was
    missing from dark_working_streak's breaker list while the WEAKER 'explained'
    (self-reported, still awaiting sign-off) was present. So approved leave days
    still counted as dark days on a manager accountability note.
    """

    class _Day:
        def __init__(self, required, tracked, status):
            self.required_hours, self.tracked_hours, self.status = required, tracked, status

    def _week(self, friday_status):
        """Mon-Fri, nothing tracked; Friday carries the status under test."""
        from datetime import date, timedelta
        fri = date(2026, 7, 31)
        days = {fri - timedelta(days=k): self._Day(6.5, 0, 'unjustified') for k in range(1, 5)}
        days[fri] = self._Day(6.5, 0, friday_status)
        return days, fri

    def _streak(self, friday_status):
        from hris.manager_accountability import dark_working_streak
        days, fri = self._week(friday_status)
        return dark_working_streak(days, {}, fri)[0]

    def test_justified_day_ends_the_streak(self):
        self.assertEqual(self._streak('justified'), 0)

    def test_met_day_ends_the_streak(self):
        self.assertEqual(self._streak('met'), 0)

    def test_explained_day_ends_the_streak(self):
        self.assertEqual(self._streak('explained'), 0)

    def test_unjustified_day_still_counts_as_dark(self):
        # The control: a genuinely unexplained day must still accuse.
        self.assertEqual(self._streak('unjustified'), 5)


class FactsGateTest(TestCase):
    """Gate 0: a day Omni already explains is never an accusation (CFO 2026-08-03).

    The settle gate and the AI gate both ask "is this Time Doctor figure FINAL?".
    Oratile's zero WAS final and WAS real — what nobody checked was the leave
    register. This gate asks that, deterministically, before either of them.
    """

    def _gate(self, **facts):
        from hris.people_data_guard import facts_gate
        return facts_gate(facts, D(2026, 7, 28))

    def test_nothing_on_record_reports(self):
        ok, _ = self._gate(required_hours=6.5, day_status='unjustified')
        self.assertTrue(ok)

    def test_no_facts_at_all_reports(self):
        from hris.people_data_guard import facts_gate
        # Absence of a record is not evidence either way — it must not hold a name
        # on its own, or the whole report would silently empty.
        self.assertTrue(facts_gate({}, D(2026, 7, 28))[0])
        self.assertTrue(facts_gate(None, D(2026, 7, 28))[0])

    def test_leave_on_record_holds(self):
        ok, why = self._gate(required_hours=6.5, leave='approved, 2026-07-27 to 2026-07-31')
        self.assertFalse(ok)
        self.assertIn('leave on record', why)

    def test_pending_leave_also_holds(self):
        # A pending application is still the person having told us.
        ok, why = self._gate(required_hours=6.5, leave='pending approval, 2026-07-27 to 2026-07-31')
        self.assertFalse(ok)
        self.assertIn('leave', why)

    def test_client_visit_holds(self):
        self.assertFalse(self._gate(required_hours=6.5, client_visit='logged for 2026-07-28')[0])

    def test_public_holiday_holds(self):
        ok, why = self._gate(required_hours=6.5, public_holiday='President\u2019s Day')
        self.assertFalse(ok)
        self.assertIn('public holiday', why)

    def test_no_hours_owed_holds(self):
        self.assertFalse(self._gate(required_hours=0)[0])

    def test_settled_day_statuses_hold(self):
        for st in ('met', 'justified', 'explained', 'not_required'):
            self.assertFalse(self._gate(required_hours=6.5, day_status=st)[0], st)

    def test_unjustified_and_pending_statuses_still_report(self):
        # 'pending' = awaiting the employee's answer, which is NOT an explanation.
        for st in ('unjustified', 'pending'):
            self.assertTrue(self._gate(required_hours=6.5, day_status=st)[0], st)

    def test_junk_required_hours_does_not_crash_or_clear(self):
        self.assertTrue(self._gate(required_hours='not a number')[0])

    def test_gate_runs_before_settle_and_ai(self):
        from hris.people_data_guard import guard_candidates
        cands = [{'uid': 'u1', 'name': 'Oratile T', 'is_zero': True}]
        # use_ai=False so this proves ORDER, not the AI: with no samples the settle
        # gate would hold anyway, but the reason must be the leave record.
        out = guard_candidates(cands, {}, {}, D(2026, 7, 28), use_ai=False,
                               facts_by_uid={'u1': {'required_hours': 6.5,
                                                    'leave': 'approved, 27th to 31st'}})
        self.assertEqual(out['u1']['action'], 'hold')
        self.assertIn('leave on record', out['u1']['reason'])

    def test_no_facts_supplied_behaves_exactly_as_before(self):
        from hris.people_data_guard import guard_candidates
        cands = [{'uid': 'u1', 'name': 'Someone', 'is_zero': False}]
        out = guard_candidates(cands, {'0300': {'u1': 100}, '0400': {'u1': 100}},
                               {}, D(2026, 7, 28), use_ai=False)
        self.assertEqual(out['u1']['action'], 'report')
