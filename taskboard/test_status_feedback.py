"""Tests for the dashboard feedback modal's status decision + percent-complete
+ 15-word rule (CFO 2026-07-13; halved from 30). Endpoint: TaskFeedbackView."""
from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework.test import APITestCase

from core.models import OmniTask, TaskFeedback

WORDS_15 = " ".join(["reason"] * 15)          # exactly the minimum
WORDS_14 = " ".join(["reason"] * 14)          # one short


class FeedbackStatusTests(APITestCase):
    def setUp(self):
        self.manager = User.objects.create_user("manager", password="x")
        self.staff = User.objects.create_user("staff", password="x")
        self.other = User.objects.create_user("other", password="x")
        self.task = OmniTask.objects.create(
            assigner=self.manager, assignee=self.staff, title="Do the thing")
        self.url = reverse("v1-taskboard-feedback", args=[self.task.id])

    def _post(self, user, payload):
        self.client.force_authenticate(user=user)
        return self.client.post(self.url, payload, format="json")

    def test_not_done_under_15_words_rejected(self):
        r = self._post(self.manager, {"body": WORDS_14, "status": "not_done"})
        self.assertEqual(r.status_code, 400)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, OmniTask.Status.PENDING)
        self.assertIsNone(self.task.completion_pct)

    def test_not_done_with_15_words_ok(self):
        r = self._post(self.manager, {"body": WORDS_15, "status": "not_done"})
        self.assertEqual(r.status_code, 201)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, OmniTask.Status.PENDING)
        self.assertEqual(self.task.completion_pct, 0)

    def test_partial_sets_percentage(self):
        r = self._post(self.manager, {"body": "halfway there",
                                       "status": "partial", "completion_pct": 50})
        self.assertEqual(r.status_code, 201)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, OmniTask.Status.PARTIAL)
        self.assertEqual(self.task.completion_pct, 50)

    def test_partial_invalid_percentage_rejected(self):
        r = self._post(self.manager, {"body": "x", "status": "partial",
                                       "completion_pct": 33})
        self.assertEqual(r.status_code, 400)

    def test_partial_missing_percentage_rejected(self):
        r = self._post(self.manager, {"body": "x", "status": "partial"})
        self.assertEqual(r.status_code, 400)

    def test_done_sets_100_and_completed_at(self):
        r = self._post(self.manager, {"body": "great job", "status": "done"})
        self.assertEqual(r.status_code, 201)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, OmniTask.Status.DONE)
        self.assertEqual(self.task.completion_pct, 100)
        self.assertIsNotNone(self.task.completed_at)

    def test_plain_feedback_no_status_leaves_task_unchanged(self):
        r = self._post(self.manager, {"body": "nice work"})
        self.assertEqual(r.status_code, 201)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, OmniTask.Status.PENDING)
        self.assertIsNone(self.task.completion_pct)
        self.assertEqual(TaskFeedback.objects.filter(task=self.task).count(), 1)

    def test_non_assigner_forbidden(self):
        r = self._post(self.other, {"body": "hi", "status": "done"})
        self.assertEqual(r.status_code, 403)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, OmniTask.Status.PENDING)
