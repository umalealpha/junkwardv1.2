"""taskboard/tests.py — the enforcement rules that must never regress.

Focus: the server-side completion gate (30-char note; dwell gate removed
2026-07-18) and the idempotent due/overdue sweep.
Run in CI (needs a DB): manage.py test taskboard
"""
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from core.models import OmniTask

from django.core import mail

from . import services
from .models import CompletionNote, Notification, TaskReminderEmailLog

VALID_NOTE = "Reconciled the FNB feed and cleared the three exceptions with Dorothy."  # well over the 30-char min


class CompletionGateTests(TestCase):
    def setUp(self):
        self.boss = User.objects.create_user("boss")
        self.staff = User.objects.create_user("staff")
        self.task = OmniTask.objects.create(
            assigner=self.boss, assignee=self.staff, title="Test", due_at=timezone.localdate()
        )

    def test_fast_completion_allowed(self):
        # Dwell gate removed (CFO 2026-07-18): an instant completion with a
        # valid note must succeed; seconds are still recorded for audit.
        note = services.complete_task(self.task, self.staff, VALID_NOTE, 0)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, OmniTask.Status.DONE)
        self.assertEqual(note.interaction_seconds, 0)

    def test_note_is_optional(self):
        # CFO 2026-07-24: the completion note is optional — Done is one tap.
        # A short or empty note must complete the task, not raise.
        note = services.complete_task(self.task, self.staff, "", 120)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, OmniTask.Status.DONE)
        self.assertEqual(note.body, "")

    def test_accepts_valid_and_stamps(self):
        note = services.complete_task(self.task, self.staff, VALID_NOTE, 45)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, OmniTask.Status.DONE)
        self.assertIsNotNone(self.task.completed_at)
        self.assertEqual(note.interaction_seconds, 45)

    def test_completion_acknowledges_open_reminders(self):
        Notification.objects.create(
            recipient=self.staff, task=self.task, type=Notification.Type.OVERDUE
        )
        services.complete_task(self.task, self.staff, VALID_NOTE, 40)
        self.assertFalse(
            Notification.objects.filter(task=self.task, acknowledged=False).exists()
        )

    def test_double_complete_blocked(self):
        services.complete_task(self.task, self.staff, VALID_NOTE, 40)
        with self.assertRaises(ValidationError):
            services.complete_task(self.task, self.staff, VALID_NOTE, 40)


class SweepTests(TestCase):
    def setUp(self):
        self.boss = User.objects.create_user("boss2")
        self.staff = User.objects.create_user("staff2")
        self.today = timezone.localdate()

    def test_due_and_overdue_created_once(self):
        OmniTask.objects.create(assigner=self.boss, assignee=self.staff, title="due", due_at=self.today)
        OmniTask.objects.create(
            assigner=self.boss, assignee=self.staff, title="od",
            due_at=self.today - timedelta(days=2),
        )
        first = services.sweep_due_and_overdue(self.today)
        self.assertEqual(first["due_day"], 1)
        self.assertEqual(first["overdue"], 1)
        # idempotent — a standing unacknowledged reminder is not duplicated
        second = services.sweep_due_and_overdue(self.today)
        self.assertEqual(second["due_day"], 0)
        self.assertEqual(second["overdue"], 0)

    def test_done_task_not_swept(self):
        OmniTask.objects.create(
            assigner=self.boss, assignee=self.staff, title="done",
            due_at=self.today, status=OmniTask.Status.DONE,
        )
        result = services.sweep_due_and_overdue(self.today)
        self.assertEqual(result["due_day"], 0)

    def test_assign_notice_idempotent(self):
        # Creating the task already fires the assign toast via the post_save
        # signal (taskboard/signals.py), so further notify_on_assign calls are
        # no-ops — the invariant is that exactly one ASSIGN_DAY notice ever exists.
        task = OmniTask.objects.create(
            assigner=self.boss, assignee=self.staff, title="a", due_at=self.today
        )
        self.assertIsNone(services.notify_on_assign(task))
        self.assertIsNone(services.notify_on_assign(task))
        self.assertEqual(
            Notification.objects.filter(task=task, type=Notification.Type.ASSIGN_DAY).count(), 1
        )


class ReminderSelfHealTests(TestCase):
    """A task completed OFF the complete_task() path (e.g. the dashboard status
    control) must not keep nagging. CFO 2026-07-15.

    "Nagging" means the force-action modal, which the frontend builds from
    type != assign_day (TaskReminderGuard.tsx). The assign-day toast is a
    separate, user-dismissible thing that the post_save signal fires on task
    creation — so these tests assert on the reminder types, not on every
    unacknowledged row, or the assign toast makes them fail for the wrong reason.
    """

    NAG_TYPES = [Notification.Type.DUE_DAY, Notification.Type.OVERDUE]

    def setUp(self):
        self.boss = User.objects.create_user("boss2")
        self.staff = User.objects.create_user("staff2")
        self.today = timezone.localdate()

    def _standing_nags(self, task):
        return Notification.objects.filter(
            task=task, acknowledged=False, type__in=self.NAG_TYPES)

    def test_sweep_clears_orphaned_reminder_on_done_task(self):
        task = OmniTask.objects.create(
            assigner=self.boss, assignee=self.staff, title="Load budgets",
            due_at=self.today,
        )
        # standing overdue reminder, then task marked DONE off-path (no clear)
        Notification.objects.create(
            recipient=self.staff, task=task, type=Notification.Type.OVERDUE)
        task.status = OmniTask.Status.DONE
        task.save(update_fields=["status", "updated_at"])
        # before the sweep the nag is still standing
        self.assertTrue(self._standing_nags(task).exists())
        result = services.sweep_due_and_overdue(self.today)
        self.assertEqual(result["cleared"], 1)
        self.assertFalse(self._standing_nags(task).exists())

    def test_clear_task_reminders_helper(self):
        task = OmniTask.objects.create(
            assigner=self.boss, assignee=self.staff, title="x", due_at=self.today,
            status=OmniTask.Status.DONE)
        Notification.objects.create(
            recipient=self.staff, task=task, type=Notification.Type.DUE_DAY)
        self.assertEqual(services.clear_task_reminders(task), 1)
        self.assertFalse(self._standing_nags(task).exists())
        # the dismissible assign-day toast is deliberately left alone
        self.assertTrue(Notification.objects.filter(
            task=task, type=Notification.Type.ASSIGN_DAY, acknowledged=False).exists())

    def test_sweep_leaves_open_task_reminder_standing(self):
        task = OmniTask.objects.create(
            assigner=self.boss, assignee=self.staff, title="still open",
            due_at=self.today, status=OmniTask.Status.PENDING)
        Notification.objects.create(
            recipient=self.staff, task=task, type=Notification.Type.OVERDUE)
        result = services.sweep_due_and_overdue(self.today)
        self.assertEqual(result["cleared"], 0)
        self.assertTrue(self._standing_nags(task).exists())


class NotificationReadFilterTests(TestCase):
    """A cleared task must stop popping up on the very NEXT poll, whatever
    cleared it (CFO 2026-07-26: "even after we clear the task they do not
    appear again, especially in nexus and pop ups").

    ReminderSelfHealTests above covers the sweep, which only runs on its cron
    tick — so a task finished by any path that doesn't call complete_task() (the
    dashboard status control, the CFO decision modal, admin, a workflow's bulk
    status update) kept re-raising the force-modal for up to a day. The READ
    endpoint now filters on task status too, which also hides the assign-day
    toast on a finished task without touching the stored row.
    """

    def setUp(self):
        self.boss = User.objects.create_user("boss3")
        self.staff = User.objects.create_user("staff3")
        self.client.force_login(self.staff)

    def _notify(self, status):
        task = OmniTask.objects.create(
            assigner=self.boss, assignee=self.staff, title="Sign the payroll",
            due_at=timezone.localdate(), status=status)
        Notification.objects.create(
            recipient=self.staff, task=task, type=Notification.Type.OVERDUE)
        return task

    def test_open_task_reminder_is_returned(self):
        self._notify(OmniTask.Status.PENDING)
        r = self.client.get("/api/v1/taskboard/notifications/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(Notification.Type.OVERDUE, {n["type"] for n in r.json()})

    def test_done_task_reminder_is_not_returned_before_any_sweep(self):
        task = self._notify(OmniTask.Status.DONE)
        # The rows are still unacknowledged — no sweep has run.
        self.assertTrue(
            Notification.objects.filter(task=task, acknowledged=False).exists())
        self.assertEqual(self.client.get("/api/v1/taskboard/notifications/").json(), [])

    def test_cancelled_task_reminder_is_not_returned(self):
        self._notify(OmniTask.Status.CANCELLED)
        self.assertEqual(self.client.get("/api/v1/taskboard/notifications/").json(), [])


class ReminderEmailIdempotencyTests(TestCase):
    """The daily digest must go out at most ONCE per person per day, no matter
    how many times the sweep runs (Pramod, Sr IT, 2026-07-24: it was firing
    5-6x/day). The (recipient, sent_on) send-ledger enforces this."""

    def setUp(self):
        self.boss = User.objects.create_user("boss2")
        self.staff = User.objects.create_user("staff2", email="staff2@alphadirect.co.bw")
        self.today = timezone.localdate()
        OmniTask.objects.create(
            assigner=self.boss, assignee=self.staff, title="Overdue thing",
            due_at=self.today - timedelta(days=1), status=OmniTask.Status.PENDING)

    def test_second_run_same_day_sends_nothing(self):
        first = services.email_open_reminders(self.today)
        self.assertEqual(first["emailed"], 1)
        self.assertEqual(len(mail.outbox), 1)
        # Re-run (simulates a duplicate cron / manual re-trigger the same day)
        second = services.email_open_reminders(self.today)
        self.assertEqual(second["emailed"], 0)
        self.assertEqual(len(mail.outbox), 1)              # still just the one
        self.assertEqual(
            TaskReminderEmailLog.objects.filter(
                recipient=self.staff, sent_on=self.today).count(), 1)

    def test_next_day_sends_again(self):
        services.email_open_reminders(self.today)
        tomorrow = self.today + timedelta(days=1)
        result = services.email_open_reminders(tomorrow)
        self.assertEqual(result["emailed"], 1)             # a new day => a new send
        self.assertEqual(len(mail.outbox), 2)


class TaskDetailsEmailTests(TestCase):
    """CFO 2026-07-28: emails must carry the task DETAILS, not just a summary
    line — both the assign-time email and the daily due/overdue digest."""

    def setUp(self):
        self.boss = User.objects.create_user(
            "boss3", email="boss3@alphadirect.co.bw", first_name="Prathap",
            last_name="Ganesharajah")
        self.staff = User.objects.create_user(
            "staff3", email="staff3@alphadirect.co.bw", first_name="Kago",
            last_name="Tshutlhedi")
        self.today = timezone.localdate()

    def _html(self, msg):
        return next(c for c, m in msg.alternatives if m == "text/html")

    def test_assign_email_carries_full_details(self):
        t = OmniTask.objects.create(
            assigner=self.boss, assignee=self.staff,
            title="Reconcile FNB June", body="Pull the statement.\nClear all exceptions.",
            priority=OmniTask.Priority.HIGH, due_at=self.today,
            status=OmniTask.Status.PENDING)
        sent = services.email_task_assigned(t)
        self.assertEqual(sent, 1)
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.to, ["staff3@alphadirect.co.bw"])
        html = self._html(msg)
        # Full body present (both lines), the assigner named, and NOT cc'd to CFO.
        self.assertIn("Pull the statement.", html)
        self.assertIn("Clear all exceptions.", html)
        self.assertIn("Prathap Ganesharajah", html)
        self.assertNotIn("excoboard@alphadirect.co.bw", msg.cc or [])

    def test_assign_email_escapes_body(self):
        t = OmniTask.objects.create(
            assigner=self.boss, assignee=self.staff, title="X",
            body="<script>alert(1)</script>", status=OmniTask.Status.PENDING)
        services.email_task_assigned(t)
        html = self._html(mail.outbox[0])
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_no_email_when_self_assigned(self):
        t = OmniTask.objects.create(
            assigner=self.staff, assignee=self.staff, title="mine",
            body="do it", status=OmniTask.Status.PENDING)
        self.assertEqual(services.email_task_assigned(t), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_digest_includes_task_body(self):
        OmniTask.objects.create(
            assigner=self.boss, assignee=self.staff, title="Overdue thing",
            body="The specific thing to do this week.",
            due_at=self.today - timedelta(days=1), status=OmniTask.Status.PENDING)
        services.email_open_reminders(self.today)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("The specific thing to do this week.",
                      self._html(mail.outbox[0]))


class CompleteEndpointTests(TestCase):
    """API-level completion gate — through the DRF view + CompleteTaskSerializer,
    NOT services.complete_task directly. Regression for the Fable 2026-08-03
    finding: the serializer had allow_blank=False, so one-tap Done (empty note)
    400'd at the shape check even though the service gate is MIN_NOTE_CHARS=0.
    The existing CompletionGateTests call the service directly and never crossed
    the serializer, so they could not catch this."""

    def setUp(self):
        from rest_framework.test import APIClient
        self.boss = User.objects.create_user("boss2", password="x")
        self.staff = User.objects.create_user("staff2", password="x")
        self.task = OmniTask.objects.create(
            assigner=self.boss, assignee=self.staff, title="One-tap done")
        self.client = APIClient()

    def _url(self):
        from django.urls import reverse
        return reverse("v1-taskboard-complete", args=[self.task.id])

    def test_one_tap_done_empty_note_ok(self):
        # The CFO's one-tap Done: no note. Must complete (200), not 400.
        self.client.force_authenticate(user=self.staff)
        r = self.client.post(self._url(), {"body": "", "interaction_seconds": 0}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, OmniTask.Status.DONE)
