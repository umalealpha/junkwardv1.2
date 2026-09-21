"""
The task assignee picker is "payroll only, no system accounts".

An automated-QA Employee (the "Manus Reviewer" account) is a real Employee row
with a live login, so it passed every check the picker had and showed up as a
person a task could be handed to (Manus QC, 2026-09-01). The `is_test_record`
flag exists precisely to mark these; the picker must honour it, and honour it
for any future flagged bot, not just this one name.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from core.api_views import omni_task_assignees
from payroll.models import Employee

User = get_user_model()


class TaskAssigneesExcludeTestRecordsTests(TestCase):

    def setUp(self):
        self.rf = APIRequestFactory(SERVER_NAME='omni.alphadirect.co.bw')

    def _employee(self, username, full_name, *, is_test):
        u = User.objects.create_user(username=username, password='x' * 20, is_active=True)
        Employee.objects.create(
            employee_number=f'E-{username}', full_name=full_name, status='active', user=u,
            job_title='Automated Reviewer' if is_test else 'Analyst',
            department='IT' if is_test else 'Finance',
            is_test_record=is_test)
        return u

    def _assignee_usernames(self, viewer):
        req = self.rf.get('/api/v1/tasks/assignees/', secure=True)
        force_authenticate(req, user=viewer)
        return {r['username'] for r in omni_task_assignees(req).data['assignees']}

    def test_a_flagged_qa_account_is_not_offered_as_an_assignee(self):
        bot = self._employee('manus', 'Manus Reviewer (automated QA)', is_test=True)
        real = self._employee('t.modise', 'Thabo Modise', is_test=False)
        usernames = self._assignee_usernames(viewer=real)

        self.assertIn('t.modise', usernames)      # a real person is still offered
        self.assertNotIn('manus', usernames)      # the QA account is not
        # and the bot viewing the picker still sees the real person, not itself
        self.assertNotIn('manus', self._assignee_usernames(viewer=bot))
