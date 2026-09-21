"""Closing a leaver's Omni login — CFO directive 2026-09-18.

Written after a live prod check found SEVEN people with a past termination date
still holding an active Omni account, the oldest 84 days after their last day.
Recording the exit did nothing to the login, and Human Capital believed that
suspending the mailbox closed it. It does not: Omni has its own password and
emails its own sign-in code, so a mailbox suspension blocks a NEW sign-in while
an open browser session (15h) or phone session (30 days) carries on.

Every test here fails without payroll/offboard_access.py.
"""
from datetime import date, timedelta
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.authtoken.models import Token

from core.models import AuditLog
from payroll.archive_service import terminate_employee
from payroll.models import Employee
from payroll.offboard_access import close_omni_access, should_close

TODAY = timezone.localdate()
YESTERDAY = TODAY - timedelta(days=1)


def _staff(username, **kwargs):
    """An employee WITH an Omni login — the only kind this feature acts on."""
    user = User.objects.create_user(username, username + '@alphadirect.co.bw', 'pw')
    defaults = dict(full_name='Person ' + username, employee_number='OFF-' + username,
                    status=Employee.Status.ACTIVE, hire_date=date(2024, 1, 1),
                    email=username + '@alphadirect.co.bw', user=user)
    defaults.update(kwargs)
    return Employee.objects.create(**defaults), user


def _device_session(user, expires_in_days=30):
    """A live phone session, or None when the model is not installed."""
    try:
        from core.device_session_models import StaffDeviceSession
    except Exception:                                    # noqa: BLE001
        return None
    raw = 'a' * 64
    return StaffDeviceSession.objects.create(
        user=user,
        token_hash=StaffDeviceSession.hash_token(raw),
        expires_at=timezone.now() + timedelta(days=expires_in_days),
    )


class ClosesTheLoginTest(TestCase):
    def setUp(self):
        self.hr = User.objects.create_superuser('hr_off', 'hr_off@test.com', 'pw')

    def test_terminating_a_past_leaver_closes_the_omni_login(self):
        emp, user = _staff('leaver1')
        terminate_employee(emp, actor=self.hr, termination_date=YESTERDAY,
                           reason='resignation')
        user.refresh_from_db()
        self.assertFalse(user.is_active,
                         'the leaver can still sign into Omni after their exit')

    def test_it_ends_the_browser_session_too(self):
        emp, user = _staff('leaver2')
        Token.objects.create(user=user)
        terminate_employee(emp, actor=self.hr, termination_date=YESTERDAY,
                           reason='resignation')
        self.assertFalse(Token.objects.filter(user=user).exists(),
                         'the 15-hour browser session outlived the exit')

    def test_it_ends_the_phone_session_too(self):
        emp, user = _staff('leaver3')
        sess = _device_session(user)
        if sess is None:
            self.skipTest('device sessions not installed in this build')
        terminate_employee(emp, actor=self.hr, termination_date=YESTERDAY,
                           reason='resignation')
        sess.refresh_from_db()
        self.assertIsNotNone(sess.revoked_at,
                             'the 30-day phone session outlived the exit')

    def test_it_switches_the_profile_off_so_the_screen_agrees(self):
        # Closing only the login left five of the first six real leavers still
        # showing "Active" on the Users screen — the same screen-vs-login
        # mismatch Unami Butale reported, created from the other side.
        from core.models import UserProfile
        emp, user = _staff('screenagrees')
        prof = UserProfile.objects.create(user=user, role=UserProfile.Role.OPERATIONS_STAFF)
        terminate_employee(emp, actor=self.hr, termination_date=YESTERDAY,
                           reason='resignation')
        prof.refresh_from_db()
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertFalse(prof.is_active,
                         'the Users screen still shows this leaver as Active')

    def test_it_writes_one_audit_row_naming_what_was_ended(self):
        emp, user = _staff('leaver4')
        terminate_employee(emp, actor=self.hr, termination_date=YESTERDAY,
                           reason='resignation')
        rows = AuditLog.objects.filter(table_name='auth.User', record_id=str(user.pk))
        self.assertEqual(rows.count(), 1)
        self.assertIn('Closed Omni access', rows.first().description)


class LeavesTheRightPeopleAloneTest(TestCase):
    """The three cases where closing the account would be WRONG."""

    def setUp(self):
        self.hr = User.objects.create_superuser('hr_off2', 'hr_off2@test.com', 'pw')

    def test_someone_leaving_today_keeps_access_until_end_of_day(self):
        # CFO 2026-09-18: HR records the exit on the morning of the last day,
        # while the person is still handing over and returning their laptop.
        emp, user = _staff('lastday')
        terminate_employee(emp, actor=self.hr, termination_date=TODAY,
                           reason='resignation')
        user.refresh_from_db()
        self.assertTrue(user.is_active,
                        'locked someone out during their own last working day')

    def test_a_contractor_marked_keep_access_is_never_closed(self):
        # The live case: Chipo Bamusi, employment ended 21-Aug-2026, still
        # working with us as an external contractor (CFO confirmed 18-Sep-2026).
        emp, user = _staff('contractor', keep_access_after_exit=True)
        terminate_employee(emp, actor=self.hr, termination_date=YESTERDAY,
                           reason='end_of_contract')
        user.refresh_from_db()
        self.assertTrue(user.is_active,
                        'cut off a contractor who is still working with us')
        self.assertFalse(should_close(emp))

    def test_a_system_administrator_is_reported_not_closed(self):
        # A wrong termination date on the wrong record must never lock the
        # business out of its own system.
        emp, user = _staff('adminleaver')
        user.is_superuser = True
        user.save(update_fields=['is_superuser'])
        emp.termination_date = YESTERDAY
        emp.save(update_fields=['termination_date'])
        result = close_omni_access(emp, actor=self.hr)
        user.refresh_from_db()
        self.assertTrue(result['skipped_superuser'])
        self.assertFalse(result['closed'])
        self.assertTrue(user.is_active)


class RaceAndAuditHonestyTest(TestCase):
    """Two defects the off-subscription review found on 2026-09-18."""

    def setUp(self):
        self.hr = User.objects.create_superuser('hr_off3', 'hr_off3@test.com', 'pw')

    def test_ticking_keep_access_mid_sweep_still_saves_the_contractor(self):
        # The sweep decides from a row it read at the top of its loop. If HR
        # ticks "keep access" while it is running, the stale copy would still
        # close the account — and the flag is the only thing standing between a
        # working contractor and being locked out.
        emp, user = _staff('raced')
        Employee.objects.filter(pk=emp.pk).update(
            status=Employee.Status.TERMINATED, termination_date=YESTERDAY)
        stale = Employee.objects.select_related('user').get(pk=emp.pk)
        # HR ticks the box after the sweep has already read the row.
        Employee.objects.filter(pk=emp.pk).update(keep_access_after_exit=True)
        result = close_omni_access(stale, actor=self.hr)
        user.refresh_from_db()
        self.assertTrue(user.is_active,
                        'a contractor exempted mid-sweep was locked out anyway')
        self.assertFalse(result['closed'])
        self.assertTrue(result['changed_under_us'])

    def test_it_does_not_claim_to_have_ended_an_already_dead_phone_session(self):
        # An expired session needs no revoking; counting it makes the audit row
        # claim we ended something that had already ended.
        emp, user = _staff('expiredphone')
        sess = _device_session(user, expires_in_days=-1)
        if sess is None:
            self.skipTest('device sessions not installed in this build')
        emp.termination_date = YESTERDAY
        emp.save(update_fields=['termination_date'])
        result = close_omni_access(emp, actor=self.hr)
        self.assertEqual(result['device_sessions_revoked'], 0)
        self.assertFalse(user.__class__.objects.get(pk=user.pk).is_active)


class SweepCommandTest(TestCase):
    """The sweep is what catches the people the Terminate button never saw."""

    def _run(self, *args):
        out = StringIO()
        call_command('close_leaver_access', *args, stdout=out)
        return out.getvalue()

    def test_it_closes_a_leaver_whose_status_was_changed_without_terminate(self):
        # How most of the fourteen existing leavers got there.
        emp, user = _staff('bypassed')
        Employee.objects.filter(pk=emp.pk).update(
            status=Employee.Status.TERMINATED, termination_date=YESTERDAY)
        self._run()
        user.refresh_from_db()
        self.assertFalse(user.is_active)

    def test_dry_run_changes_nothing(self):
        emp, user = _staff('dryrun')
        Employee.objects.filter(pk=emp.pk).update(
            status=Employee.Status.TERMINATED, termination_date=YESTERDAY)
        output = self._run('--dry-run')
        user.refresh_from_db()
        self.assertTrue(user.is_active, 'a dry run closed a real account')
        self.assertIn('WOULD CLOSE', output)

    def test_it_skips_the_contractor_and_says_so(self):
        emp, user = _staff('sweepcontractor', keep_access_after_exit=True)
        Employee.objects.filter(pk=emp.pk).update(
            status=Employee.Status.TERMINATED, termination_date=YESTERDAY)
        output = self._run()
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertIn('KEEP', output)

    def test_running_it_twice_does_nothing_the_second_time(self):
        emp, user = _staff('twice')
        Employee.objects.filter(pk=emp.pk).update(
            status=Employee.Status.TERMINATED, termination_date=YESTERDAY)
        self._run()
        second = self._run()
        self.assertIn('Closed 0 leaver account(s)', second)

    def test_it_leaves_current_staff_alone(self):
        emp, user = _staff('current')
        self._run()
        user.refresh_from_db()
        self.assertTrue(user.is_active, 'the sweep closed a working employee')
