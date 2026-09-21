"""Offboarding prompts to IT and Payroll.

Dorothy Ikgopoleng, 2026-09-11, answering the Terminate Employee follow-up
("should the system automatically let IT and Payroll know?"):

    "Perhaps they can receive a prompt so that they are able to ensure all
     offboarding checks are done on their end."

Covers: a termination raises one task per side carrying that side's own
checklist, emails both sides, does not raise the same task twice, and — the
load-bearing one — a notification failure can never undo a recorded exit.
"""
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.core import mail
from django.db import connection
from django.test import TestCase
from django.utils import timezone
from unittest.mock import patch

from core.it_queue import IT_OWNERS
from core.models import OmniTask
from payroll.archive_service import terminate_employee
from payroll.models import Employee

YESTERDAY = timezone.localdate() - timedelta(days=1)


class OffboardingNoticeTest(TestCase):
    def setUp(self):
        self.hr = User.objects.create_superuser('hr_off', 'hr_off@test.com', 'pw')
        # IT side: the Help Desk's own IT owners — NOT a role, and not a
        # manager (CFO 2026-09-11: "Sechele is the IT guy he is not a manager").
        self.it = User.objects.create_user('it_off', IT_OWNERS[1], 'pw')
        self.it2 = User.objects.create_user('it_off2', IT_OWNERS[0], 'pw')
        # Payroll side: a Finance payroll signer (matched on the email local part).
        self.fin = User.objects.create_user('fin_off', 'pkago@alphadirect.co.bw', 'pw')

    def _emp(self, number='OFF-001'):
        return Employee.objects.create(
            full_name='Leaving Person', employee_number=number,
            status=Employee.Status.ACTIVE, hire_date=date(2024, 1, 1))

    def test_raises_one_task_per_side_with_its_own_checklist(self):
        terminate_employee(self._emp(), actor=self.hr,
                           termination_date=YESTERDAY, reason='resignation')

        it_task = OmniTask.objects.get(assignee=self.it)
        # BOTH IT owners are prompted, not just one.
        self.assertTrue(OmniTask.objects.filter(assignee=self.it2).exists())
        fin_task = OmniTask.objects.get(assignee=self.fin)
        self.assertIn('Offboarding (IT)', it_task.title)
        self.assertIn('Offboarding (Payroll)', fin_task.title)
        # Each side gets its OWN steps, not a shared generic list.
        self.assertIn('Disable the omni', it_task.body)
        self.assertNotIn('Disable the omni', fin_task.body)
        self.assertIn('Stop the salary', fin_task.body)
        self.assertNotIn('Stop the salary', it_task.body)
        # The leaver's facts travel with the prompt.
        for task in (it_task, fin_task):
            self.assertIn('Leaving Person', task.body)
            self.assertIn(str(YESTERDAY), task.body)
            self.assertIn('resignation', task.body)

    def test_the_prompt_is_chased(self):
        """CFO 2026-09-11: 'remind them until it is done'. The reminder sweep and
        the daily nudge email BOTH skip tasks with no due date, so a missing
        due_at would silently mean no chase at all."""
        from taskboard.services import sweep_due_and_overdue
        terminate_employee(self._emp(), actor=self.hr,
                           termination_date=YESTERDAY, reason='resignation')
        due = timezone.localdate() + timedelta(days=7)
        for task in OmniTask.objects.all():
            self.assertEqual(task.due_at, due, task.title)
            self.assertIsNotNone(task.due_time)
        # A backlog leaver — the exit recorded weeks late — still gets a WEEK to
        # do the checks, not a task born overdue that nags on the first login.
        old = Employee.objects.create(
            full_name='Long Gone', employee_number='OFF-OLD',
            status=Employee.Status.ACTIVE, hire_date=date(2020, 1, 1))
        terminate_employee(old, actor=self.hr,
                           termination_date=timezone.localdate() - timedelta(days=60),
                           reason='resignation')
        for task in OmniTask.objects.filter(title__contains='OFF-OLD'):
            self.assertEqual(task.due_at, due, task.title)

        # Once that date passes, the sweep actually raises the overdue nag.
        OmniTask.objects.update(due_at=YESTERDAY)
        raised = sweep_due_and_overdue()
        self.assertEqual(raised['overdue'], 6, raised)
        # ...and marking it done stops the nag, so this cannot nag for ever.
        OmniTask.objects.update(status=OmniTask.Status.DONE)
        self.assertEqual(sweep_due_and_overdue()['cleared'], 6)

    def test_hr_is_not_prompted(self):
        """HR did the terminating — prompting them back is noise."""
        terminate_employee(self._emp(), actor=self.hr,
                           termination_date=YESTERDAY, reason='resignation')
        self.assertFalse(OmniTask.objects.filter(assignee=self.hr).exists())

    def test_both_sides_are_emailed(self):
        mail.outbox = []
        terminate_employee(self._emp(), actor=self.hr,
                           termination_date=YESTERDAY, reason='resignation')
        sent = {m.subject: m for m in mail.outbox}
        it_subj = [s for s in sent if 'IT offboarding' in s]
        pay_subj = [s for s in sent if 'Payroll offboarding' in s]
        self.assertTrue(it_subj, f'no IT email in {list(sent)}')
        self.assertTrue(pay_subj, f'no Payroll email in {list(sent)}')
        self.assertEqual(sorted(sent[it_subj[0]].to), sorted(IT_OWNERS))
        self.assertIn('pkago@alphadirect.co.bw', sent[pay_subj[0]].to)

    def test_does_not_raise_a_second_task_while_one_is_still_open(self):
        from core.notifications import notify_offboarding_started
        emp = self._emp()
        terminate_employee(emp, actor=self.hr,
                           termination_date=YESTERDAY, reason='resignation')
        first = OmniTask.objects.count()
        # Same leaver, notice re-fired (retry, replay) — no duplicate prompts.
        mail.outbox = []
        notify_offboarding_started(emp, termination_date=YESTERDAY,
                                   reason='resignation', actor=self.hr)
        self.assertEqual(OmniTask.objects.count(), first)
        # And nobody is re-mailed a checklist they are already holding.
        self.assertEqual(mail.outbox, [])

    def test_a_real_database_error_never_undoes_the_termination(self):
        """A swallowed DatabaseError outside a savepoint would abort the caller's
        transaction and roll the exit back — the notice must own its savepoint."""
        emp = self._emp()
        def boom(*a, **kw):
            # A REAL database error, raised by the database itself — that is what
            # poisons a transaction. A hand-raised Python DatabaseError does not.
            with connection.cursor() as cur:
                cur.execute('SELECT 1/0')

        with patch.object(OmniTask.objects, 'create', side_effect=boom):
            terminate_employee(emp, actor=self.hr,
                               termination_date=YESTERDAY, reason='redundancy')
        emp.refresh_from_db()
        self.assertEqual(emp.status, Employee.Status.TERMINATED)
        self.assertEqual(emp.termination_date, YESTERDAY)
        # The connection is still usable — the savepoint absorbed the error.
        self.assertTrue(Employee.objects.filter(pk=emp.pk).exists())

    def test_a_broken_notifier_never_undoes_the_termination(self):
        emp = self._emp()
        with patch('core.notifications._it_offboarding_users',
                   side_effect=RuntimeError('boom')):
            terminate_employee(emp, actor=self.hr,
                               termination_date=YESTERDAY, reason='dismissal')
        emp.refresh_from_db()
        self.assertEqual(emp.status, Employee.Status.TERMINATED)
        self.assertEqual(emp.termination_date, YESTERDAY)
        # ...and the IT failure did not cost Payroll its prompt.
        self.assertTrue(OmniTask.objects.filter(assignee=self.fin).exists())

    def test_two_leavers_with_the_same_name_each_get_their_own_prompt(self):
        first = self._emp(number='OFF-A')
        second = Employee.objects.create(
            full_name='Leaving Person', employee_number='OFF-B',
            status=Employee.Status.ACTIVE, hire_date=date(2024, 1, 1))
        terminate_employee(first, actor=self.hr,
                           termination_date=YESTERDAY, reason='resignation')
        terminate_employee(second, actor=self.hr,
                           termination_date=YESTERDAY, reason='resignation')
        titles = set(OmniTask.objects.filter(assignee=self.it)
                     .values_list('title', flat=True))
        self.assertEqual(len(titles), 2, titles)
        self.assertTrue(any('OFF-A' in t for t in titles))
        self.assertTrue(any('OFF-B' in t for t in titles))
