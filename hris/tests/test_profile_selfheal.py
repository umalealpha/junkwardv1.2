"""A linked staff record must never be told it is unlinked.

The live symptom (2026-08-08): two developers at a group company could not apply for
leave. Omni told them "Your staff record has not been linked to your login yet — ask HR to link
your employee record." Their records WERE linked: `Employee.user` pointed straight
at the login. What was missing was the HRIS extension row, which nothing on the
payroll-import path creates, so the message sent them to HR to fix something that
was not broken and HR had nothing to do.

`_profile_for()` now creates that row when the staff record is already paired, and
still returns None — correctly — when there is genuinely no staff record behind the
login. Both halves are tested here, because a self-heal that fires for everyone
would hand a leave balance to logins that are not staff at all.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase

from hris.feature_views import _profile_for
from hris.models import HRISProfile
from payroll.models import Employee

User = get_user_model()


class ProfileSelfHealTests(TestCase):

    _next_number = 7700

    def _number(self):
        ProfileSelfHealTests._next_number += 1
        return str(ProfileSelfHealTests._next_number)

    def _linked_staff(self, name, email):
        user = User.objects.create_user(email.split('@')[0], email=email, password='x')
        emp = Employee.objects.create(full_name=name, email=email,
                                      employee_number=self._number(), user=user)
        return emp, user

    def test_linked_staff_without_an_hris_row_gets_one(self):
        emp, user = self._linked_staff('Test Developer One', 'td-one@example.com')
        self.assertFalse(HRISProfile.objects.filter(employee=emp).exists())

        profile = _profile_for(user)

        self.assertIsNotNone(profile, 'a linked staff record must resolve to a profile')
        self.assertEqual(profile.employee_id, emp.id)
        self.assertTrue(HRISProfile.objects.filter(employee=emp).exists())

    def test_it_is_idempotent_and_never_duplicates(self):
        emp, user = self._linked_staff('Test Developer Two', 'td-two@example.com')
        first = _profile_for(user)
        second = _profile_for(user)

        self.assertEqual(first.id, second.id)
        self.assertEqual(HRISProfile.objects.filter(employee=emp).count(), 1)

    def test_an_existing_profile_is_returned_untouched(self):
        emp, user = self._linked_staff('Test Developer Three', 'td-three@example.com')
        existing = HRISProfile.objects.create(employee=emp, location='Head Office')

        profile = _profile_for(user)

        self.assertEqual(profile.id, existing.id)
        self.assertEqual(profile.location, 'Head Office',
                         'self-heal must not overwrite a real profile')

    def test_a_login_with_no_staff_record_still_gets_nothing(self):
        # The guard the self-heal must not swallow: a login that is genuinely not
        # staff (a service account, a contractor with no employee record) has no
        # profile to create, and the "ask HR" message is the right answer for them.
        outsider = User.objects.create_user('svc.reporting',
                                            email='svc@example.com', password='x')

        self.assertIsNone(_profile_for(outsider))
        self.assertEqual(HRISProfile.objects.count(), 0)


class JoinerPathTests(TestCase):
    """The root cause: the paths that create staff never made the HRIS row.

    The self-heal above rescues someone already stuck. This is the half that
    stops anyone new getting stuck at all — proven against the payroll importer,
    which is how both affected developers were created.
    """

    def test_the_payroll_importer_gives_a_new_joiner_a_profile(self):
        from payroll.importer import _ensure_hris_profile

        emp = Employee.objects.create(full_name='Test Joiner One',
                                      email='tj-one@example.com',
                                      employee_number='7790')
        HRISProfile.objects.filter(employee=emp).delete()   # as the importer leaves it

        _ensure_hris_profile(emp)

        self.assertTrue(HRISProfile.objects.filter(employee=emp).exists(),
                        'a payroll-created joiner must be usable in self-service')

    def test_it_is_safe_to_call_twice(self):
        from payroll.importer import _ensure_hris_profile

        emp = Employee.objects.create(full_name='Test Joiner Two',
                                      email='tj-two@example.com',
                                      employee_number='7791')
        _ensure_hris_profile(emp)
        _ensure_hris_profile(emp)

        self.assertEqual(HRISProfile.objects.filter(employee=emp).count(), 1)
