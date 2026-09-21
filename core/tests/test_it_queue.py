"""core/tests/test_it_queue.py — raising an IT request from the backend.

The third action the CFO asked for on 2026-08-12 ("I cannot close tasks, submit
IT requests, or approve purchase orders" from the backend). The real Help Desk
is an external SharePoint list omni can only READ, so the request goes on omni's
own queue as an OmniTask assigned to IT — the same place CFO directive
2026-06-29 already put Help Desk work.

The load-bearing test here is `test_refuses_when_no_it_owner_resolves`: routing
is by named email precisely because prod has no usable IT department/title data,
so the failure mode to guard is a request that reaches NOBODY and says nothing.

Run: python manage.py test core.tests.test_it_queue
"""
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from core.it_queue import IT_OWNERS, IT_REQUEST_SOURCE, it_owner_users, raise_it_request
from core.models import AuditLog, OmniTask, UserProfile

CFO_USERNAME = "pganesharajah"


def _cfo():
    u = User.objects.create_user(username=CFO_USERNAME, password="x")
    prof, _ = UserProfile.objects.get_or_create(user=u)
    prof.title = UserProfile.Title.CFO
    prof.save()
    return u


def _owner(email, username, active=True):
    return User.objects.create_user(
        username=username, email=email, password="x", is_active=active)


class ItQueueServiceTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.requester = User.objects.create_user("requester", password="x")

    def test_raises_a_task_for_the_first_it_owner(self):
        first = _owner(IT_OWNERS[0], "it_first")
        _owner(IT_OWNERS[1], "it_second")
        task = raise_it_request(self.requester, subject="Laptop will not boot",
                                body="Dead at the login screen.", priority="high")
        self.assertEqual(task.assignee, first)
        self.assertEqual(task.assigner, self.requester)
        self.assertEqual(task.source, IT_REQUEST_SOURCE)
        self.assertEqual(task.priority, "high")
        self.assertEqual(task.title, "Laptop will not boot")
        self.assertIn("Dead at the login screen.", task.body)

    def test_names_the_other_owner_so_the_request_is_not_invisible_to_them(self):
        _owner(IT_OWNERS[0], "it_first")
        _owner(IT_OWNERS[1], "it_second")
        task = raise_it_request(self.requester, subject="Printer offline")
        self.assertIn("Also on the IT queue", task.body)

    def test_refuses_when_no_it_owner_resolves(self):
        """The failure this guards: an IT request routed to nobody, silently.
        Remove the owners check in core/it_queue.py and this goes red."""
        with self.assertRaises(ValidationError) as cm:
            raise_it_request(self.requester, subject="Nobody home")
        self.assertIn("No IT Help Desk owner", str(cm.exception))
        self.assertFalse(OmniTask.objects.filter(source=IT_REQUEST_SOURCE).exists())

    def test_an_inactive_owner_is_not_routed_to(self):
        _owner(IT_OWNERS[0], "it_first", active=False)
        second = _owner(IT_OWNERS[1], "it_second")
        task = raise_it_request(self.requester, subject="Still needs to land")
        self.assertEqual(task.assignee, second)

    def test_owner_order_follows_the_list_not_the_database(self):
        """Created in reverse so a natural pk ordering would pick the wrong one."""
        _owner(IT_OWNERS[1], "it_second")
        first = _owner(IT_OWNERS[0], "it_first")
        self.assertEqual([u.email for u in it_owner_users()], IT_OWNERS)
        self.assertEqual(raise_it_request(self.requester, subject="x").assignee, first)

    def test_subject_is_required(self):
        _owner(IT_OWNERS[0], "it_first")
        with self.assertRaises(ValidationError):
            raise_it_request(self.requester, subject="   ")

    def test_unknown_priority_refused(self):
        _owner(IT_OWNERS[0], "it_first")
        with self.assertRaises(ValidationError) as cm:
            raise_it_request(self.requester, subject="x", priority="catastrophic")
        self.assertIn("Unknown priority", str(cm.exception))


class ItRequestViaOmniDoTests(TestCase):
    """The backend door — `manage.py omni_do --action it_request`."""

    @classmethod
    def setUpTestData(cls):
        cls.cfo = _cfo()

    def test_raises_the_request_and_writes_an_audit_row(self):
        owner = _owner(IT_OWNERS[0], "it_first")
        call_command("omni_do", "--actor", "cfo", "--action", "it_request",
                     "--subject", "VPN drops every 10 minutes",
                     "--body", "Happens on the Gaborone office wifi.",
                     "--priority", "high")
        task = OmniTask.objects.get(source=IT_REQUEST_SOURCE)
        self.assertEqual(task.assignee, owner)
        self.assertEqual(task.assigner, self.cfo)
        self.assertEqual(task.title, "VPN drops every 10 minutes")
        log = AuditLog.objects.get(table_name="OmniBackendAction")
        self.assertEqual(log.user, self.cfo)
        self.assertEqual(log.action, AuditLog.Action.CREATE)
        self.assertIn("it_request", log.description)

    def test_missing_subject_refused_and_nothing_written(self):
        _owner(IT_OWNERS[0], "it_first")
        with self.assertRaises(CommandError) as cm:
            call_command("omni_do", "--actor", "cfo", "--action", "it_request")
        self.assertIn("--subject is required", str(cm.exception))
        self.assertFalse(OmniTask.objects.filter(source=IT_REQUEST_SOURCE).exists())
        self.assertFalse(AuditLog.objects.filter(table_name="OmniBackendAction").exists())

    def test_no_owner_refused_and_nothing_written(self):
        with self.assertRaises(CommandError) as cm:
            call_command("omni_do", "--actor", "cfo", "--action", "it_request",
                         "--subject", "Nobody home")
        self.assertIn("No IT Help Desk owner", str(cm.exception))
        self.assertFalse(AuditLog.objects.filter(table_name="OmniBackendAction").exists())

    def test_task_close_still_requires_its_id_after_the_arg_change(self):
        """--id stopped being parser-required so it_request could exist; the
        other actions must still refuse without it. Remove the _require call in
        core/backend_actions.py and this goes red."""
        with self.assertRaises(CommandError) as cm:
            call_command("omni_do", "--actor", "cfo", "--action", "task_close",
                         "--note", "no id given")
        self.assertIn("--id is required", str(cm.exception))

    def test_po_approve_still_requires_its_id_after_the_arg_change(self):
        with self.assertRaises(CommandError) as cm:
            call_command("omni_do", "--actor", "cfo", "--action", "po_approve")
        self.assertIn("--id is required", str(cm.exception))


class HelpdeskReminderSharesOneOwnerListTests(TestCase):
    """The reminder command and the queue must never disagree about who IT is."""

    def test_reminder_owners_are_the_same_object_as_the_queue_list(self):
        from core.management.commands.helpdesk_pending_reminder import OWNERS
        self.assertIs(OWNERS, IT_OWNERS)
