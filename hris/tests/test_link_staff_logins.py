"""
Linking a staff record to its login — the thing that stopped 15 people booking leave.

The live symptom: an employee record existed, a login existed, the emails matched exactly, and
the HRIS profile that joins them did not — so `_profile_for()` returned None and leave could not
be applied for. The person could see their balances and could do nothing with them.

What matters most here is what the command REFUSES. Payroll has duplicate employee rows (two
records, one email), and linking the wrong one books somebody's leave against a record nobody
looks at. So: match on the full email only, never a name, and refuse anything ambiguous.
"""
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from hris.feature_views import _profile_for
from hris.models import HRISProfile
from payroll.models import Employee

User = get_user_model()


class LinkStaffLoginsTests(TestCase):

    _next_number = 9000

    def _number(self):
        """employee_number is unique, and a blank one collides with the next blank one."""
        LinkStaffLoginsTests._next_number += 1
        return str(LinkStaffLoginsTests._next_number)

    def _staff(self, name, email, with_login=True, active=True):
        emp = Employee.objects.create(full_name=name, email=email,
                                      employee_number=self._number())
        user = None
        if with_login:
            user = User.objects.create_user(email.split('@')[0], email=email, password='x')
            user.is_active = active
            user.save(update_fields=['is_active'])
        return emp, user

    def _run(self, commit=True, **kw):
        call_command('link_staff_logins', commit=commit, **kw)

    # ── the live case ────────────────────────────────────────────────────────
    def test_a_missing_profile_is_created_so_leave_can_be_applied_for(self):
        emp, user = self._staff('Test Consultant One', 'tc-one@example.co.bw')
        emp.user = user
        emp.save(update_fields=['user'])
        # The reported symptom was no HRIS row in the database. It used to be
        # asserted through _profile_for() returning None, but that helper now
        # self-heals a linked record on read (see test_profile_selfheal), so the
        # absence is checked directly — the command's job is unchanged.
        self.assertFalse(HRISProfile.objects.filter(employee=emp).exists())

        self._run()

        self.assertIsNotNone(_profile_for(user))       # and now they can apply
        self.assertEqual(HRISProfile.objects.filter(employee=emp).count(), 1)

    def test_a_missing_login_link_is_set_from_the_matching_email(self):
        emp, user = self._staff('Test Consultant Two', 'tc-two@example.co.bw')
        self.assertIsNone(getattr(emp, 'user_id', None))
        self._run()
        emp.refresh_from_db()
        self.assertEqual(emp.user_id, user.id)
        self.assertIsNotNone(_profile_for(user))

    def test_a_dry_run_changes_nothing(self):
        emp, user = self._staff('Test Consultant Three', 'tc-three@example.co.bw')
        self._run(commit=False)
        emp.refresh_from_db()
        self.assertIsNone(getattr(emp, 'user_id', None))
        self.assertEqual(HRISProfile.objects.count(), 0)

    def test_running_it_twice_changes_nothing_the_second_time(self):
        emp, user = self._staff('Test Consultant Four', 'tc-four@example.co.bw')
        self._run()
        self._run()
        self.assertEqual(HRISProfile.objects.filter(employee=emp).count(), 1)

    # ── what it must REFUSE ──────────────────────────────────────────────────
    def test_two_staff_records_on_one_email_are_both_refused(self):
        # The duplicate-employee shape that exists in payroll. Guessing which record owns the
        # login books leave against the wrong one.
        first, user = self._staff('Test Duplicate Full Name', 'dupe@example.co.bw')
        second = Employee.objects.create(full_name='Test Duplicate', email='dupe@example.co.bw',
                                         employee_number=self._number())
        self._run()
        for emp in (first, second):
            emp.refresh_from_db()
            self.assertIsNone(getattr(emp, 'user_id', None),
                              'an ambiguous email must not be linked')
        self.assertEqual(HRISProfile.objects.count(), 0)

    def test_two_logins_on_one_email_are_refused(self):
        emp, user = self._staff('Test Two Logins', 'twologins@example.co.bw')
        User.objects.create_user('twologins-second', email='twologins@example.co.bw', password='x')
        self._run()
        emp.refresh_from_db()
        self.assertIsNone(getattr(emp, 'user_id', None))

    def test_an_inactive_login_is_not_used(self):
        # Linking to a disabled account leaves the person no better off and hides the real gap.
        emp, user = self._staff('Test Disabled', 'disabled@example.co.bw', active=False)
        self._run()
        emp.refresh_from_db()
        self.assertIsNone(getattr(emp, 'user_id', None))

    def test_a_staff_record_with_no_login_is_left_alone(self):
        emp, _ = self._staff('Test No Login', 'nologin@example.co.bw', with_login=False)
        self._run()
        emp.refresh_from_db()
        self.assertIsNone(getattr(emp, 'user_id', None))
        self.assertEqual(HRISProfile.objects.count(), 0)

    def test_it_never_matches_on_a_name(self):
        # Same person, different email: a name match would pair them, and that is exactly how
        # payroll grew a second row for one person.
        emp = Employee.objects.create(full_name='Test Same Name', email='same-a@example.co.bw',
                                      employee_number=self._number())
        User.objects.create_user('same-b', email='same-b@example.co.bw', password='x')
        self._run()
        emp.refresh_from_db()
        self.assertIsNone(getattr(emp, 'user_id', None))

    def test_one_person_can_be_fixed_on_their_own(self):
        wanted, wuser = self._staff('Test Wanted', 'wanted@example.co.bw')
        other, _ = self._staff('Test Other', 'other@example.co.bw')
        self._run(employee='wanted@example.co.bw')
        self.assertIsNotNone(_profile_for(wuser))
        self.assertEqual(HRISProfile.objects.filter(employee=other).count(), 0)
