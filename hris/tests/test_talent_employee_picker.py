"""Board item [DD] — the Talent Cockpit employee picker.

Why it exists: the cockpit's "+ Add person" minted a synthetic record called
"New team member" with NO email. The save path links a dialogue to a payroll
record by email (`_match_employee`), so a person created that way came out
ORPHANED — they could not open their own dialogue (my-dialogue looks up by
email) and the 9-box grid could not place them. There was no way at all to
create a real employee's dialogue from that screen, which is the one thing the
screen exists to do. The CFO's QC pass failed it on exactly that.

These pin the roster the picker chooses from, and — the part that matters — that
it is scoped the SAME way the cockpit itself is. Handing a manager the whole
staff list would leak the org chart to someone the cockpit otherwise shows
almost nothing to.
"""
from django.contrib.auth.models import User
from rest_framework.test import APIClient, APITestCase

from hris.models import HRISProfile
from payroll.models import Employee

URL = '/hris/api/talent/employees/'


class TalentEmployeePicker(APITestCase):

    @classmethod
    def setUpTestData(cls):
        # _sees_all() matches the LOCAL PART only, so example.com works and no
        # company address needs to sit in this file. 'pganesharajah' is on the
        # exec/HR allowlist -> sees everyone.
        cls.exec_user = User.objects.create_user(
            'pganesharajah', 'pganesharajah@example.com', 'x')
        cls.outsider = User.objects.create_user(
            'nobody', 'nobody@example.com', 'x')

        cls.alice = Employee.objects.create(
            employee_number='E001', full_name='Alice Motswana',
            department='Finance', job_title='Accountant',
            email='alice@example.com', status='active')
        cls.bob = Employee.objects.create(
            employee_number='E002', full_name='Bob Kgosi',
            department='Claims', job_title='Assessor',
            email='bob@example.com', status='active')
        # Must never be offered: left the company.
        cls.gone = Employee.objects.create(
            employee_number='E003', full_name='Former Person',
            department='Claims', job_title='Assessor',
            email='gone@example.com', status='terminated')
        # Must never be offered: no email, so a dialogue for them would orphan
        # exactly the way the old blank-person path did.
        cls.noemail = Employee.objects.create(
            employee_number='E004', full_name='No Email Person',
            department='Ops', job_title='Clerk', email='', status='active')

    def _client(self, user):
        c = APIClient()
        c.force_authenticate(user)
        return c

    def _names(self, body):
        return {e['name'] for e in body['employees']}

    def test_an_exec_sees_the_active_roster(self):
        r = self._client(self.exec_user).get(URL)
        self.assertEqual(r.status_code, 200, r.content)
        names = self._names(r.json())
        self.assertIn('Alice Motswana', names)
        self.assertIn('Bob Kgosi', names)

    def test_someone_who_has_left_is_never_offered(self):
        names = self._names(self._client(self.exec_user).get(URL).json())
        self.assertNotIn('Former Person', names,
                         'A terminated employee must not be offered a new dialogue.')

    def test_an_employee_with_no_email_is_never_offered(self):
        # The dialogue is linked by email. Offering someone without one just
        # recreates the orphan record this whole item exists to stop.
        names = self._names(self._client(self.exec_user).get(URL).json())
        self.assertNotIn('No Email Person', names)

    def test_it_carries_what_the_picker_needs_to_search_on(self):
        row = next(e for e in self._client(self.exec_user).get(URL).json()['employees']
                   if e['name'] == 'Alice Motswana')
        self.assertEqual(row['email'], 'alice@example.com')
        self.assertEqual(row['department'], 'Finance')
        self.assertEqual(row['position'], 'Accountant')
        self.assertEqual(row['employee_no'], 'E001')
        self.assertIn('has_dialogue', row)

    def test_somebody_with_no_talent_access_is_refused(self):
        # Not on the allowlist and manages nobody — the roster is the org chart,
        # so it must not fall open to any signed-in user.
        r = self._client(self.outsider).get(URL)
        self.assertEqual(r.status_code, 403, r.content)

    def test_it_requires_a_login(self):
        self.assertIn(APIClient().get(URL).status_code, (401, 403))
