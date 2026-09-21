"""chase_manager_reviews — the Time Doctor follow-up chase (CFO 2026-09-20).

Found by the 20-Sep control check: 137 staff explanations sat in "explained"
with no manager ruling, and 9 unpaid deductions sat "pending" — six of them
with an approver whose login was last used on 11-Aug. Nothing chased anyone.

The command reminds the MANAGER after 3 working days (HR copied), and copies
the manager's own manager after 5. Working days = Mon-Fri minus Botswana public
holidays. It decides nothing itself. Dry-run unless --send.

Run: python manage.py test hris.test_chase_manager_reviews
"""
import datetime
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from hris.models import HRISProfile, LeaveRequest, LeaveType, PublicHoliday, WorkdayJustification as WJ
from payroll.models import Employee

TODAY = datetime.date(2026, 9, 24)          # a Thursday
HR = 'ubutale@alphadirect.co.bw'


def _aware(d: datetime.date):
    return timezone.make_aware(datetime.datetime.combine(d, datetime.time(9, 0)))


@override_settings(TD_CHASE_HR_CC=[HR])
class ChaseTests(TestCase):
    def setUp(self):
        self.big_user = User.objects.create_user('bigboss', email='bigboss@alphadirect.co.bw', password='x')
        self.mgr_user = User.objects.create_user('mgr', email='mgr@alphadirect.co.bw', password='x')
        self.big = Employee.objects.create(full_name='Big Boss', employee_number='CH-B', status='active',
                                           email='bigboss@alphadirect.co.bw', user=self.big_user)
        self.mgr = Employee.objects.create(full_name='Line Manager', employee_number='CH-M', status='active',
                                           email='mgr@alphadirect.co.bw', user=self.mgr_user)
        self.emp = Employee.objects.create(full_name='Staff Person', employee_number='CH-1', status='active',
                                           email='staff@alphadirect.co.bw')
        HRISProfile.objects.create(employee=self.big)
        HRISProfile.objects.create(employee=self.mgr, manager=self.big)
        self.prof = HRISProfile.objects.create(employee=self.emp, manager=self.mgr)
        self.td_type, _ = LeaveType.objects.get_or_create(code='td_deduct', defaults={'name': 'Time Doctor Deduction'})

    def _explained(self, responded: datetime.date, work_date=None):
        return WJ.objects.create(profile=self.prof, work_date=work_date or responded,
                                 required_hours=Decimal('6.50'), tracked_hours=Decimal('1.00'),
                                 status=WJ.Status.EXPLAINED, justification='Client visit',
                                 responded_at=_aware(responded))

    def _deduction(self, created: datetime.date):
        lr = LeaveRequest.objects.create(profile=self.prof, leave_type=self.td_type,
                                         start_date=created - datetime.timedelta(days=1),
                                         end_date=created - datetime.timedelta(days=1), days=Decimal('1'),
                                         status=LeaveRequest.Status.PENDING, requested_approver=self.mgr_user)
        LeaveRequest.objects.filter(pk=lr.pk).update(created_at=_aware(created))
        return lr

    def _run(self, *args):
        out = StringIO()
        with mock.patch('core.notifications.send_html_with_cfo_cc', return_value=1) as send:
            call_command('chase_manager_reviews', '--today', TODAY.isoformat(), *args, stdout=out)
        return out.getvalue(), send

    # -- the 3-working-day chase ---------------------------------------------
    def test_explanation_waiting_four_working_days_chases_the_manager_with_hr_copied(self):
        self._explained(datetime.date(2026, 9, 18))          # Fri → Mon,Tue,Wed,Thu = 4 working days
        out, send = self._run('--send')
        self.assertEqual(send.call_count, 1)
        kw = send.call_args.kwargs
        self.assertEqual(send.call_args.args[2], ['mgr@alphadirect.co.bw'])
        self.assertIn(HR, kw.get('cc') or [])
        self.assertNotIn('bigboss@alphadirect.co.bw', kw.get('cc') or [])
        self.assertFalse(kw.get('cc_cfo', True), 'people matters go to HR, never the CFO (notebook)')
        self.assertIn('Staff Person', send.call_args.args[1])
        self.assertIn('1 manager', out)

    def test_two_working_days_is_too_soon(self):
        self._explained(datetime.date(2026, 9, 22))          # Tue → Wed,Thu = 2
        out, send = self._run('--send')
        self.assertEqual(send.call_count, 0)

    def test_weekend_and_public_holiday_do_not_count_as_working_days(self):
        # Fri 18-Sep → Mon 21 (holiday), Tue 22, Wed 23 = 2 working days by Wed 23.
        PublicHoliday.objects.create(country_code='BW', holiday_date=datetime.date(2026, 9, 21), name='Test Day')
        self._explained(datetime.date(2026, 9, 18))
        out = StringIO()
        with mock.patch('core.notifications.send_html_with_cfo_cc', return_value=1) as send:
            call_command('chase_manager_reviews', '--today', '2026-09-23', '--send', stdout=out)
        self.assertEqual(send.call_count, 0)
        # Without the holiday the same span is 3 working days and IS chased.
        PublicHoliday.objects.all().delete()
        with mock.patch('core.notifications.send_html_with_cfo_cc', return_value=1) as send:
            call_command('chase_manager_reviews', '--today', '2026-09-23', '--send', stdout=out)
        self.assertEqual(send.call_count, 1)

    # -- the 5-working-day escalation ----------------------------------------
    def test_after_five_working_days_the_managers_manager_is_copied(self):
        self._explained(datetime.date(2026, 9, 15))          # Tue → 7 working days by Thu 24
        out, send = self._run('--send')
        self.assertEqual(send.call_count, 1)
        cc = send.call_args.kwargs.get('cc') or []
        self.assertIn(HR, cc)
        self.assertIn('bigboss@alphadirect.co.bw', cc)

    # -- undecided deductions -------------------------------------------------
    def test_pending_deduction_is_chased_to_its_approver(self):
        lr = self._deduction(datetime.date(2026, 9, 18))
        out, send = self._run('--send')
        self.assertEqual(send.call_count, 1)
        self.assertEqual(send.call_args.args[2], ['mgr@alphadirect.co.bw'])
        self.assertIn('deduction', send.call_args.args[1].lower())
        lr.refresh_from_db()
        self.assertEqual(lr.status, LeaveRequest.Status.PENDING, 'the chase must never decide')

    def test_one_email_per_manager_lists_everything_waiting(self):
        self._explained(datetime.date(2026, 9, 18), work_date=datetime.date(2026, 9, 17))
        self._explained(datetime.date(2026, 9, 16), work_date=datetime.date(2026, 9, 15))
        self._deduction(datetime.date(2026, 9, 17))
        out, send = self._run('--send')
        self.assertEqual(send.call_count, 1)
        html = send.call_args.args[1]
        self.assertIn('17 Sep', html); self.assertIn('15 Sep', html)

    # -- safety -----------------------------------------------------------------
    def test_dry_run_sends_nothing_and_changes_nothing(self):
        w = self._explained(datetime.date(2026, 9, 15))
        out, send = self._run()
        self.assertEqual(send.call_count, 0)
        self.assertIn('DRY-RUN', out)
        w.refresh_from_db()
        self.assertEqual(w.status, WJ.Status.EXPLAINED)

    def test_reviewed_or_answered_items_are_not_chased(self):
        w = self._explained(datetime.date(2026, 9, 15))
        w.status = WJ.Status.JUSTIFIED; w.reviewed_at = _aware(datetime.date(2026, 9, 16)); w.save()
        out, send = self._run('--send')
        self.assertEqual(send.call_count, 0)

    def test_items_from_before_enforcement_started_are_not_chased(self):
        # First prod dry-run (20-Sep-2026) reached back to July: 320 items, one
        # manager with 60. The floor is the enforcement start, 1-Sep-2026.
        self._explained(datetime.date(2026, 8, 20), work_date=datetime.date(2026, 8, 19))
        out, send = self._run('--send')
        self.assertEqual(send.call_count, 0)
