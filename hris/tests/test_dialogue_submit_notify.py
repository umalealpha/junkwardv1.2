"""Development Dialogue submit → reviewer task (CFO 2026-07-27).

When an employee signs off their own dialogue it must reach their reviewer as a
core.OmniTask — which by itself surfaces in the reviewer's omni Tasks, the Nexus
Staff Portal "My tasks", and their Morning Brief (all read OmniTask by assignee).
The task auto-closes when the reviewer signs. These guards:
  * a HIGH task is raised to the owner's line-manager on employee sign,
  * it is idempotent (re-sign / double-click never duplicates),
  * a manager sign-off closes it,
  * the free-text supervisor name resolves a reviewer when no manager link exists,
  * an inactive reviewer raises no task (a disabled login can't action it).
"""
from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework.test import APITestCase

from core.models import Company, OmniTask
from hris.models import DevelopmentDialogue, HRISProfile
from payroll.models import Employee


class DialogueSubmitNotifyTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.co = Company.objects.create(code='DD', name='Dialogue Co.')

        cls.emp_user = User.objects.create_user('pg', email='pg@alphadirect.co.bw')
        cls.rev_user = User.objects.create_user(
            'arun', email='aiyer@alphadirect.co.bw', first_name='Arun', last_name='Iyer')

        cls.emp = Employee.objects.create(
            employee_number='E1', full_name='Prathap G', company=cls.co,
            email='pg@alphadirect.co.bw', user=cls.emp_user)
        cls.rev = Employee.objects.create(
            employee_number='R1', full_name='Arun Iyer', company=cls.co,
            email='aiyer@alphadirect.co.bw', user=cls.rev_user)

        HRISProfile.objects.create(employee=cls.emp, manager=cls.rev)

    def _make_dialogue(self, supervisor='Arun P Iyer'):
        return DevelopmentDialogue.objects.create(
            ref='pg::2025', email='pg@alphadirect.co.bw', name='Prathap G',
            supervisor=supervisor, period='1 July 2025 to 30 June 2026',
            is_current=True, payload={'dd': {}})

    def _sign(self, user, ref, role):
        self.client.force_authenticate(user=user)
        return self.client.post(reverse('hris:api-talent-sign'),
                                {'ref': ref, 'role': role}, format='json')

    def test_employee_sign_raises_high_task_to_line_manager(self):
        d = self._make_dialogue()
        r = self._sign(self.emp_user, d.ref, 'employee')
        self.assertEqual(r.status_code, 200)
        t = OmniTask.objects.get(assignee=self.rev_user, source='dev_dialogue')
        self.assertEqual(t.priority, OmniTask.Priority.HIGH)
        self.assertEqual(t.status, OmniTask.Status.PENDING)
        self.assertIn('Prathap G', t.title)
        self.assertIsNotNone(t.due_at)

    def test_task_is_idempotent_on_resign(self):
        d = self._make_dialogue()
        self._sign(self.emp_user, d.ref, 'employee')
        self._sign(self.emp_user, d.ref, 'employee')
        self.assertEqual(
            OmniTask.objects.filter(assignee=self.rev_user, source='dev_dialogue').count(), 1)

    def test_two_periods_get_two_distinct_tasks(self):
        # DeepSeek CRITICAL 2026-07-27: a title keyed on name alone would suppress
        # the second dialogue's task. Period in the title keeps them distinct.
        for ref, period in (('pg::2024', '1 July 2024 to 30 June 2025'),
                            ('pg::2025', '1 July 2025 to 30 June 2026')):
            DevelopmentDialogue.objects.create(
                ref=ref, email='pg@alphadirect.co.bw', name='Prathap G',
                supervisor='Arun P Iyer', period=period,
                is_current=True, payload={'dd': {}})
            self._sign(self.emp_user, ref, 'employee')
        self.assertEqual(
            OmniTask.objects.filter(assignee=self.rev_user, source='dev_dialogue').count(), 2)

    def test_manager_sign_closes_the_task(self):
        d = self._make_dialogue()
        self._sign(self.emp_user, d.ref, 'employee')
        # Reviewer is on the exec/HR all-scope? No — give them scope via manager
        # chain: they line-manage the owner, so _scope includes the ref.
        r = self._sign(self.rev_user, d.ref, 'manager')
        self.assertEqual(r.status_code, 200)
        t = OmniTask.objects.get(source='dev_dialogue')
        self.assertEqual(t.status, OmniTask.Status.DONE)
        self.assertIsNotNone(t.completed_at)

    def test_close_is_scoped_to_the_signer(self):
        # DeepSeek CRITICAL 2026-07-27: closing must be the signer's OWN task, so
        # another manager/moderator signing never clears the assigned reviewer's.
        from core.notifications import close_dialogue_review_tasks
        d = self._make_dialogue()
        self._sign(self.emp_user, d.ref, 'employee')
        t = OmniTask.objects.get(source='dev_dialogue')
        # A non-assignee signing must NOT close the reviewer's task.
        close_dialogue_review_tasks(d, signer=self.emp_user)
        t.refresh_from_db()
        self.assertEqual(t.status, OmniTask.Status.PENDING)
        # The actual reviewer (assignee) signing closes it.
        close_dialogue_review_tasks(d, signer=self.rev_user)
        t.refresh_from_db()
        self.assertEqual(t.status, OmniTask.Status.DONE)

    def test_supervisor_name_fallback_when_no_manager_link(self):
        # No manager link at all — reviewer must resolve from the supervisor text
        # ("Arun P Iyer" vs the account "Arun Iyer": 2 shared name tokens).
        HRISProfile.objects.filter(employee=self.emp).delete()
        d = self._make_dialogue(supervisor='Arun P Iyer')
        self._sign(self.emp_user, d.ref, 'employee')
        self.assertTrue(
            OmniTask.objects.filter(assignee=self.rev_user, source='dev_dialogue').exists())

    def test_name_fallback_is_company_scoped(self):
        # DeepSeek omni-review 2026-07-27: a fuzzy supervisor-name match must never
        # route one entity's confidential dialogue to a namesake in another company.
        from core.notifications import _dialogue_reviewer_user
        coB = Company.objects.create(code='OB', name='Other Co')
        userB = User.objects.create_user(
            'arunB', email='arunb@other.co', first_name='Arun', last_name='Iyer')
        Employee.objects.create(
            employee_number='B1', full_name='Arun Iyer', company=coB,
            email='arunb@other.co', user=userB)
        HRISProfile.objects.filter(employee=self.emp).delete()  # force the fallback path
        d = self._make_dialogue(supervisor='Arun P Iyer')
        who = _dialogue_reviewer_user(d)
        self.assertEqual(who, self.rev_user)     # same-company Arun (co) ...
        self.assertNotEqual(who, userB)          # ... never the other-company namesake

    def test_employee_cannot_sign_another_persons_dialogue(self):
        # IDOR guard (DeepSeek omni-review): a different user signing someone
        # else's dialogue as 'employee' is refused and raises no task.
        d = self._make_dialogue()  # owned by emp (pg@)
        r = self._sign(self.rev_user, d.ref, 'employee')
        self.assertEqual(r.status_code, 403)
        self.assertFalse(OmniTask.objects.filter(source='dev_dialogue').exists())

    def test_inactive_reviewer_raises_no_task(self):
        self.rev_user.is_active = False
        self.rev_user.save()
        d = self._make_dialogue()
        r = self._sign(self.emp_user, d.ref, 'employee')
        self.assertEqual(r.status_code, 200)      # sign still succeeds
        self.assertFalse(OmniTask.objects.filter(source='dev_dialogue').exists())
