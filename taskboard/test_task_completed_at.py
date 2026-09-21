"""
completed_at must be the REAL first-completion time — a later manager
confirmation must not rewrite it (reconciliation fix, CFO 2026-09-01).

The Alpha League 14-day window keys on completed_at, so if TaskFeedbackView
re-stamps it to now() every time a manager clicks 'Done', a task slides in and
out of the score window based on WHEN THE MANAGER CLICKED rather than when the
work was finished — one of the "screens don't talk to each other" mismatches.
"""
from datetime import datetime, timezone as dt_tz
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from core.models import OmniTask

T1 = datetime(2026, 8, 1, 9, 0, tzinfo=dt_tz.utc)
T2 = datetime(2026, 8, 20, 15, 0, tzinfo=dt_tz.utc)   # a manager confirming 19 days later


class CompletedAtIdempotenceTests(TestCase):
    def setUp(self):
        U = get_user_model()
        self.mgr = U.objects.create_superuser('mgr-fb', 'mgr-fb@example.invalid', 'x' * 16)
        self.staff = U.objects.create_user('staff-fb', email='staff-fb@example.invalid')
        self.task = OmniTask.objects.create(
            assigner=self.mgr, assignee=self.staff, title='Do the thing',
            status=OmniTask.Status.PENDING)
        self.c = APIClient()
        self.c.force_authenticate(self.mgr)
        self.url = reverse('v1-taskboard-feedback', args=[self.task.id])

    def _fb(self, body, status):
        return self.c.post(self.url, {'body': body, 'status': status}, format='json')

    def test_first_done_stamps_completed_at(self):
        with mock.patch('taskboard.cfo_views.timezone.now', return_value=T1):
            r = self._fb('Good, finished.', 'done')
        self.assertIn(r.status_code, (200, 201), r.content)
        self.task.refresh_from_db()
        self.assertEqual(self.task.completed_at, T1)

    def test_confirming_done_again_does_not_move_the_finish_time(self):
        with mock.patch('taskboard.cfo_views.timezone.now', return_value=T1):
            self._fb('done', 'done')
        # 19 days later a manager re-confirms 'Done'. The finish time must NOT jump.
        with mock.patch('taskboard.cfo_views.timezone.now', return_value=T2):
            self._fb('confirming again', 'done')
        self.task.refresh_from_db()
        self.assertEqual(self.task.completed_at, T1)

    def test_reopening_off_done_clears_the_finish_time(self):
        with mock.patch('taskboard.cfo_views.timezone.now', return_value=T1):
            self._fb('done', 'done')
        self.task.refresh_from_db()
        self.assertIsNotNone(self.task.completed_at)
        # 'Not done' needs a >=15-word note (view rule).
        self._fb('This was not actually finished because the vendor never sent the '
                 'invoice that we still need before we can complete it properly', 'not_done')
        self.task.refresh_from_db()
        self.assertIsNone(self.task.completed_at)
