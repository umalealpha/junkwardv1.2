"""HR gets a task to map the dialogues the importer could not place.

CFO 2026-08-01: "the development dialogs we couldn't match put it as a task for
Unami and Dorothy so they can themselves fix the email id and names of the files".

The rule these pin: a dialogue with no work email and no payroll link is NEVER
guessed into a person — it is handed to HR with the evidence. Guessing would
eventually attach an appraisal to the wrong colleague.
"""
from __future__ import annotations

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from core.models import OmniTask
from hris.management.commands.raise_dd_mapping_tasks import SOURCE, unmatched_rows
from hris.talent_cockpit_models import DevelopmentDialogue
from payroll.models import Employee


class UnmatchedTests(TestCase):
    def test_only_rows_with_no_email_and_no_payroll_link_count(self):
        DevelopmentDialogue.objects.create(
            ref='a::FY2025', name='No Owner', period='FY2025',
            payload={'source_document': 'hr_documents/FY24_DD_-_PHENYO_M.xlsx'})
        DevelopmentDialogue.objects.create(
            ref='b::FY2025', name='Has Email', period='FY2025',
            email='has@alphadirect.co.bw')
        emp = Employee.objects.create(full_name='Linked', status='active',
                                      email='linked@alphadirect.co.bw',
                                      employee_number='MAP-1')
        DevelopmentDialogue.objects.create(ref='c::FY2025', name='Linked',
                                           period='FY2025', employee=emp)
        rows = unmatched_rows('FY2025')
        self.assertEqual([n for n, _ in rows], ['No Owner'])
        self.assertEqual(rows[0][1], 'FY24_DD_-_PHENYO_M.xlsx')   # file named for HR


class TaskTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cfo = User.objects.create_user('cfo2', email='pganesharajah@alphadirect.co.bw')
        cls.unami = User.objects.create_user('unami2', email='ubutale@alphadirect.co.bw')
        cls.dorothy = User.objects.create_user('dot', email='dikgopoleng@alphadirect.co.bw')
        DevelopmentDialogue.objects.create(ref='x::FY2025', name='PHENYO M', period='FY2025',
                                           payload={'source_document': 'FY24_DD_-_PHENYO_M.xlsx'})

    def test_raises_one_task_for_each_hr_owner(self):
        call_command('raise_dd_mapping_tasks', '--commit')
        tasks = OmniTask.objects.filter(source=SOURCE)
        self.assertEqual(tasks.count(), 2)
        self.assertEqual({t.assignee for t in tasks}, {self.unami, self.dorothy})
        t = tasks.first()
        self.assertEqual(t.assigner, self.cfo)
        self.assertEqual(t.priority, OmniTask.Priority.HIGH)
        self.assertIsNotNone(t.due_at)

    def test_the_task_names_the_person_and_the_file_so_hr_can_act(self):
        call_command('raise_dd_mapping_tasks', '--commit')
        body = OmniTask.objects.filter(source=SOURCE).first().body
        self.assertIn('PHENYO M', body)
        self.assertIn('FY24_DD_-_PHENYO_M.xlsx', body)
        self.assertIn('work email', body)
        self.assertIn('do NOT guess', body)          # says why we are asking, not guessing
        self.assertIn('locked', body)                # reassures nothing is lost

    def test_re_running_does_not_duplicate_an_open_task(self):
        call_command('raise_dd_mapping_tasks', '--commit')
        call_command('raise_dd_mapping_tasks', '--commit')
        self.assertEqual(OmniTask.objects.filter(source=SOURCE).count(), 2)

    def test_a_closed_task_lets_a_fresh_one_be_raised(self):
        call_command('raise_dd_mapping_tasks', '--commit')
        OmniTask.objects.filter(source=SOURCE).update(status=OmniTask.Status.DONE)
        call_command('raise_dd_mapping_tasks', '--commit')
        self.assertEqual(OmniTask.objects.filter(source=SOURCE).count(), 4)

    def test_dry_run_writes_nothing(self):
        call_command('raise_dd_mapping_tasks')
        self.assertEqual(OmniTask.objects.filter(source=SOURCE).count(), 0)

    def test_no_orphans_means_no_task(self):
        DevelopmentDialogue.objects.filter(ref='x::FY2025').update(
            email='phenyo@alphadirect.co.bw')
        call_command('raise_dd_mapping_tasks', '--commit')
        self.assertEqual(OmniTask.objects.filter(source=SOURCE).count(), 0)
