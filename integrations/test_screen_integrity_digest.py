"""send_screen_integrity_digest — the weekly frozen-screen hand-off to HR (CFO 20-Sep-2026).

The 20-Sep-2026 control check found two people flagged repeatedly over 30 days
(one of them 3× suspicious plus 12× watch) and nobody told: nothing consumed
ScreenIntegrityFlag outside the page. Staff are never named in source (C5). Every Monday HR
(Unami) and the person's line manager get ONE email: who was flagged in the last 7
days, how many days, how many hours credited on a frozen screen. Names and counts
only — never a window title, screenshot or hash (AD-POL-AI-GOV-001). Flags only:
nothing is docked, nobody is accused. Dry-run unless --send.

Run: python manage.py test integrations.test_screen_integrity_digest
"""
import datetime
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings

from hris.models import HRISProfile
from integrations.models import ScreenIntegrityFlag, ScreenIntegrityScan, TimeDoctorUserMap
from payroll.models import Employee

TODAY = datetime.date(2026, 9, 21)        # a Monday
HR = 'ubutale@alphadirect.co.bw'


@override_settings(TD_CHASE_HR_CC=[HR])
class DigestTests(TestCase):
    def setUp(self):
        self.mgr_user = User.objects.create_user('mgr', email='mgr@alphadirect.co.bw', password='x')
        self.mgr = Employee.objects.create(full_name='Line Manager', employee_number='SI-M', status='active',
                                           email='mgr@alphadirect.co.bw', user=self.mgr_user)
        self.emp = Employee.objects.create(full_name='Flagged Person', employee_number='SI-1', status='active',
                                           email='flagged@alphadirect.co.bw')
        HRISProfile.objects.create(employee=self.mgr)
        HRISProfile.objects.create(employee=self.emp, manager=self.mgr)
        TimeDoctorUserMap.objects.create(td_user_id='uid-flagged', td_name='Flagged Person', employee=self.emp, confirmed=True)

    def _flag(self, day, suspicion='suspicious', hours='2.50', uid='uid-flagged', name='Flagged Person'):
        scan, _ = ScreenIntegrityScan.objects.get_or_create(day=day, defaults={'people_checked': 80, 'status': 'ok'})
        return ScreenIntegrityFlag.objects.create(scan=scan, day=day, td_user_id=uid, name=name, suspicion=suspicion,
                                                  shots=90, frozen_typing_pct=Decimal('60.0'),
                                                  frozen_typing_hours=Decimal(hours), mouse_dead_pct=Decimal('95.0'),
                                                  identical_pct=Decimal('70.0'),
                                                  reasons=['60% of screenshots show heavy typing on a screen that never changes — window title: SECRET.xlsx'])

    def _run(self, *args):
        out = StringIO()
        with mock.patch('core.notifications.send_html_with_cfo_cc', return_value=1) as send:
            call_command('send_screen_integrity_digest', '--today', TODAY.isoformat(), *args, stdout=out)
        return out.getvalue(), send

    def test_flags_in_the_last_seven_days_go_to_hr_with_the_manager_copied(self):
        self._flag(datetime.date(2026, 9, 16)); self._flag(datetime.date(2026, 9, 18), suspicion='watch', hours='1.00')
        out, send = self._run('--send')
        self.assertEqual(send.call_count, 1)
        args, kw = send.call_args
        self.assertEqual(args[2], [HR])
        self.assertIn('mgr@alphadirect.co.bw', kw.get('cc') or [])
        self.assertFalse(kw.get('cc_cfo', True), 'people matters go to HR, never the CFO')
        html = args[1]
        self.assertIn('Flagged Person', html)
        self.assertIn('2 day', html)                      # 2 flagged days
        self.assertIn('3.5', html)                        # 2.50 + 1.00 h credited on a frozen screen
        self.assertIn('1 person', out)

    def test_idle_only_flag_reports_its_own_hours_not_zero(self):
        """The idle-frozen rule (2026-09-21) raises rows with frozen_typing_hours = 0
        by definition. Summing that column alone printed the worst cases to HR and
        the line manager as "0.0 h", which reads as a glitch and gets ignored."""
        scan, _ = ScreenIntegrityScan.objects.get_or_create(
            day=TODAY - datetime.timedelta(days=1),
            defaults={'people_checked': 100, 'status': 'ok'})
        ScreenIntegrityFlag.objects.create(
            scan=scan, day=TODAY - datetime.timedelta(days=1),
            td_user_id='uid-flagged', name='Flagged Person', suspicion='suspicious',
            shots=187,
            frozen_typing_pct=Decimal('0.5'), frozen_typing_hours=Decimal('0.04'),
            mouse_dead_pct=Decimal('100.0'), identical_pct=Decimal('74.3'),
            idle_frozen_pct=Decimal('70.1'), idle_frozen_hours=Decimal('5.79'),
            reasons=['70% of screenshots show NO typing, NO mouse and NO clicks '
                     'on a screen that never changes'])
        out, send = self._run('--send')
        html = send.call_args.args[1]           # same positional arg the sibling tests read
        self.assertIn('5.8', html)              # its OWN credited hours
        self.assertNotIn('0.0 h', html)         # never the typing column's zero

    def test_never_leaks_a_window_title_or_screenshot_detail(self):
        self._flag(datetime.date(2026, 9, 17))
        out, send = self._run('--send')
        html = send.call_args.args[1]
        self.assertNotIn('SECRET.xlsx', html)
        self.assertNotIn('window title', html)
        self.assertNotIn('SECRET', out)

    def test_older_flags_are_outside_the_window(self):
        self._flag(datetime.date(2026, 9, 13))            # 8 days before Monday 21 → not in the week
        out, send = self._run('--send')
        self.assertEqual(send.call_count, 0)
        self.assertIn('nothing to report', out.lower())

    def test_window_is_last_monday_to_sunday_inclusive(self):
        # The detector scans YESTERDAY, so a Monday run covers Mon 14 .. Sun 20 and not
        # today. Fable review 20-Sep-2026: day__gt/day__lte would drop every Monday.
        self._flag(datetime.date(2026, 9, 14))            # last Monday = today-7 → IN
        self._flag(datetime.date(2026, 9, 21), uid='uid-nobody', name='Today Person')  # today → OUT
        out, send = self._run('--send')
        self.assertEqual(send.call_count, 1)
        html = send.call_args.args[1]
        self.assertIn('Flagged Person', html)
        self.assertNotIn('Today Person', html)

    def test_unconfirmed_map_never_picks_a_manager(self):
        TimeDoctorUserMap.objects.filter(td_user_id='uid-flagged').update(confirmed=False)
        self._flag(datetime.date(2026, 9, 17))
        out, send = self._run('--send')
        self.assertEqual(send.call_args.kwargs.get('cc') or [], [])

    def test_no_flags_sends_nothing(self):
        out, send = self._run('--send')
        self.assertEqual(send.call_count, 0)

    def test_dry_run_sends_nothing(self):
        self._flag(datetime.date(2026, 9, 17))
        out, send = self._run()
        self.assertEqual(send.call_count, 0)
        self.assertIn('DRY-RUN', out)

    def test_unlinked_account_still_reported_to_hr_without_a_manager(self):
        self._flag(datetime.date(2026, 9, 17), uid='uid-nobody', name='Unknown Person')
        out, send = self._run('--send')
        self.assertEqual(send.call_count, 1)
        self.assertIn('Unknown Person', send.call_args.args[1])
        self.assertEqual(send.call_args.kwargs.get('cc') or [], [])

    def test_nothing_is_written(self):
        self._flag(datetime.date(2026, 9, 17))
        before = list(ScreenIntegrityFlag.objects.values_list('id', 'suspicion'))
        self._run('--send')
        self.assertEqual(before, list(ScreenIntegrityFlag.objects.values_list('id', 'suspicion')))
