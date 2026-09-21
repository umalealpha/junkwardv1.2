"""core/tests/test_cfo_task_guard.py — staff cannot dump Urgent / short-notice
tasks on the CFO (CFO directive 2026-07-27).

Rule: a task assigned TO the CFO, raised by a non-executive, may not be Urgent
and must give >= 48h (2 clear days) notice. Executives bypass. Tasks NOT
assigned to the CFO are always allowed — the CFO must still be able to hand out
his own urgent, due-today work.

Run: python manage.py test core.tests.test_cfo_task_guard
"""
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import OmniTask, UserProfile
from core.task_guard import cfo_task_notice_error

User = get_user_model()
TODAY = date(2026, 7, 27)


def _user(username, title=None, is_superuser=False, is_admin=False):
    u = User.objects.create_user(username=username, password="x", is_superuser=is_superuser)
    prof, _ = UserProfile.objects.get_or_create(user=u)
    if title is not None:
        prof.title = title
    if is_admin:
        prof.is_administrator = True
    prof.save()
    return u


class CfoTaskNoticeRuleTests(TestCase):
    """The pure rule in core.task_guard."""

    @classmethod
    def setUpTestData(cls):
        cls.cfo = _user("cfo", UserProfile.Title.CFO)
        cls.staff = _user("staff", UserProfile.Title.ACCOUNTANT)
        cls.exec_ = _user("execu", UserProfile.Title.EXECUTIVE)
        cls.fm = _user("fm", UserProfile.Title.FINANCE_MANAGER)

    def test_staff_cannot_mark_cfo_task_urgent(self):
        err = cfo_task_notice_error(self.staff, self.cfo, OmniTask.Priority.URGENT,
                                    TODAY + timedelta(days=5), TODAY)
        self.assertIsNotNone(err)
        self.assertIn("Urgent", err)

    def test_staff_cannot_give_cfo_short_notice(self):
        # Due today = 0 days notice -> blocked.
        err = cfo_task_notice_error(self.staff, self.cfo, OmniTask.Priority.NORMAL,
                                    TODAY, TODAY)
        self.assertIsNotNone(err)
        self.assertIn("48 hours", err)
        # Due tomorrow = 1 day -> still short of 2 clear days.
        err = cfo_task_notice_error(self.staff, self.cfo, OmniTask.Priority.NORMAL,
                                    TODAY + timedelta(days=1), TODAY)
        self.assertIsNotNone(err)

    def test_staff_may_give_cfo_two_clear_days(self):
        err = cfo_task_notice_error(self.staff, self.cfo, OmniTask.Priority.NORMAL,
                                    TODAY + timedelta(days=2), TODAY)
        self.assertIsNone(err)

    def test_staff_task_with_no_due_date_is_rejected(self):
        # A missing date would sidestep the 48h rule -> require one.
        err = cfo_task_notice_error(self.staff, self.cfo, OmniTask.Priority.NORMAL,
                                    None, TODAY)
        self.assertIsNotNone(err)
        self.assertIn("due date", err)

    def test_cfo_may_do_anything_to_himself(self):
        err = cfo_task_notice_error(self.cfo, self.cfo, OmniTask.Priority.URGENT,
                                    TODAY, TODAY)
        self.assertIsNone(err)

    def test_executive_bypasses_rule(self):
        err = cfo_task_notice_error(self.exec_, self.cfo, OmniTask.Priority.URGENT,
                                    TODAY, TODAY)
        self.assertIsNone(err)

    def test_rule_does_not_touch_non_cfo_assignee(self):
        # This is the Legakwa case: CFO assigns urgent due-today work to a FM.
        err = cfo_task_notice_error(self.cfo, self.fm, OmniTask.Priority.URGENT,
                                    TODAY, TODAY)
        self.assertIsNone(err)
        # And staff -> staff urgent due-today is fine too.
        err = cfo_task_notice_error(self.staff, self.fm, OmniTask.Priority.URGENT,
                                    TODAY, TODAY)
        self.assertIsNone(err)


class CfoTaskEndpointTests(TestCase):
    """The guard as enforced on POST /api/v1/tasks/."""

    @classmethod
    def setUpTestData(cls):
        cls.cfo = _user("cfo2", UserProfile.Title.CFO)
        cls.staff = _user("staff2", UserProfile.Title.ACCOUNTANT)

    def _post(self, actor, **payload):
        c = APIClient()
        c.force_authenticate(actor)
        return c.post("/api/v1/tasks/", payload, format="json")

    def test_staff_urgent_to_cfo_rejected(self):
        r = self._post(self.staff, assignee_username="cfo2", title="do this now",
                       priority="urgent")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(OmniTask.objects.filter(assignee=self.cfo).count(), 0)

    def test_staff_due_today_to_cfo_rejected(self):
        r = self._post(self.staff, assignee_username="cfo2", title="today please",
                       priority="normal", due_at=date.today().isoformat())
        self.assertEqual(r.status_code, 403)

    def test_staff_proper_notice_to_cfo_allowed(self):
        far = (date.today() + timedelta(days=3)).isoformat()
        r = self._post(self.staff, assignee_username="cfo2", title="planned work",
                       priority="normal", due_at=far)
        self.assertEqual(r.status_code, 201)
        self.assertEqual(OmniTask.objects.filter(assignee=self.cfo).count(), 1)

    def test_cfo_urgent_to_staff_allowed(self):
        r = self._post(self.cfo, assignee_username="staff2", title="pay now",
                       priority="urgent", due_at=date.today().isoformat())
        self.assertEqual(r.status_code, 201)

    def test_staff_cannot_patch_compliant_cfo_task_to_urgent(self):
        # Raise a compliant task, then try to escalate it via edit.
        far = (date.today() + timedelta(days=3)).isoformat()
        r = self._post(self.staff, assignee_username="cfo2", title="planned",
                       priority="normal", due_at=far)
        self.assertEqual(r.status_code, 201)
        tid = r.json()["id"]
        c = APIClient(); c.force_authenticate(self.staff)
        p = c.patch(f"/api/v1/tasks/{tid}/", {"priority": "urgent"}, format="json")
        self.assertEqual(p.status_code, 403)
        # And pulling the due date forward to today is also blocked.
        p2 = c.patch(f"/api/v1/tasks/{tid}/", {"due_at": date.today().isoformat()},
                     format="json")
        self.assertEqual(p2.status_code, 403)
        self.assertEqual(OmniTask.objects.get(pk=tid).priority, "normal")

    def test_staff_cannot_reassign_a_self_urgent_task_to_cfo(self):
        # The third escalation path (Fable): raise an urgent due-today task to
        # yourself (allowed staff->staff), then hand it to the CFO.
        r = self._post(self.staff, assignee_username="staff2", title="mine",
                       priority="urgent", due_at=date.today().isoformat())
        self.assertEqual(r.status_code, 201)
        tid = r.json()["id"]
        c = APIClient(); c.force_authenticate(self.staff)
        p = c.patch(f"/api/v1/tasks/{tid}/", {"reassign_to": "cfo2"}, format="json")
        self.assertEqual(p.status_code, 403)
        self.assertEqual(OmniTask.objects.get(pk=tid).assignee_id, self.staff.id)
