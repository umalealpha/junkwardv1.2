"""One signature must clear EVERY signer's task (CFO 2026-08-21).

The CEO's daily brief carried three items under "Waiting on You":

    Sign off: <employee A> — leave request           10 days
    Sign off: <employee B> — incentive request        4 days
    Sign off: <employee B> — incentive request        4 days

All three had ALREADY been signed by the CFO — his own copies read `done`.
`exec_signoff_service.decide()` claims the ExecSignoff row with a conditional
update but never touches the OmniTasks raised for the OTHER signers, so each
signature leaves one stale HIGH-priority task per remaining signer, for ever.
On prod that was 7 open tasks representing decisions taken 4 to 13 days
earlier — the CEO being chased for work that was already done.
"""
import datetime as dt
import uuid

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from core.models import OmniTask
from hris import exec_signoff_service as svc
from hris.exec_signoff_models import ExecSignoff


class SignoffClosesSiblingTasksTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        from core.models import UserProfile
        cls.ceo = User.objects.create_user('ceo-sib', 'ceo-sib@example.invalid', 'x')
        cls.cfo = User.objects.create_user('cfo-sib', 'cfo-sib@example.invalid', 'x')
        cls.applicant = User.objects.create_user('emp-sib', 'emp-sib@example.invalid', 'x')
        for u, title in ((cls.ceo, 'ceo'), (cls.cfo, 'cfo')):
            p, _ = UserProfile.objects.get_or_create(user=u)
            p.title = title
            p.save(update_fields=['title'])

    def _signoff(self, module=ExecSignoff.Module.INCENTIVE, name='Tumelo Fixture'):
        return ExecSignoff.objects.create(
            module=module, object_id=uuid.uuid4(), applicant=self.applicant,
            applicant_name=name, reason='carrying overdue work',
            status=ExecSignoff.Status.PENDING)

    def _task_for(self, so, user, status=OmniTask.Status.PENDING):
        return OmniTask.objects.create(
            assigner=self.applicant, assignee=user,
            title=f'Sign off: {so.applicant_name} — {so.get_module_display().lower()}',
            source=f'exec_signoff:{str(so.pk)[:8]}',
            priority=OmniTask.Priority.HIGH, status=status)

    def _open(self, user):
        return OmniTask.objects.filter(
            assignee=user, status__in=[OmniTask.Status.PENDING,
                                       OmniTask.Status.IN_PROGRESS]).count()

    # -- the defect -------------------------------------------------------

    def test_the_cfo_signing_clears_the_ceos_copy(self):
        so = self._signoff()
        self._task_for(so, self.ceo)
        self._task_for(so, self.cfo)
        self.assertEqual(self._open(self.ceo), 1)

        svc.decide(so, self.cfo, True)

        self.assertEqual(self._open(self.ceo), 0,
                         "the CEO is still being chased for a decision already made")
        self.assertEqual(self._open(self.cfo), 0)

    def test_a_decline_also_clears_every_copy(self):
        so = self._signoff(module=ExecSignoff.Module.LEAVE, name='Naledi Fixture')
        self._task_for(so, self.ceo)
        self._task_for(so, self.cfo)

        svc.decide(so, self.cfo, False)

        self.assertEqual(self._open(self.ceo), 0)
        self.assertEqual(self._open(self.cfo), 0)

    def test_an_unrelated_task_is_left_alone(self):
        so = self._signoff()
        self._task_for(so, self.ceo)
        other = OmniTask.objects.create(
            assigner=self.applicant, assignee=self.ceo,
            title='Review & sign the Development Dialogue',
            source='dev_dialogue', status=OmniTask.Status.PENDING)

        svc.decide(so, self.cfo, True)

        other.refresh_from_db()
        self.assertEqual(other.status, OmniTask.Status.PENDING,
                         'a real CEO task was closed as collateral damage')

    def test_another_signoffs_tasks_are_left_alone(self):
        """Two people gated in the same week must not clear each other."""
        mine, theirs = self._signoff(), self._signoff(name='Someone Else')
        self._task_for(mine, self.ceo)
        theirs_task = self._task_for(theirs, self.ceo)

        svc.decide(mine, self.cfo, True)

        theirs_task.refresh_from_db()
        self.assertEqual(theirs_task.status, OmniTask.Status.PENDING)

    def test_a_legacy_task_with_no_source_is_still_cleared(self):
        """The 7 stale rows on prod predate the source tag — matching must not
        depend on a field they do not carry."""
        so = self._signoff()
        legacy = OmniTask.objects.create(
            assigner=self.applicant, assignee=self.ceo,
            title=f'Sign off: {so.applicant_name} — {so.get_module_display().lower()}',
            source='', priority=OmniTask.Priority.HIGH,
            status=OmniTask.Status.PENDING)

        svc.decide(so, self.cfo, True)

        legacy.refresh_from_db()
        self.assertIn(legacy.status,
                      [OmniTask.Status.DONE, OmniTask.Status.CANCELLED])

    def test_the_signoff_itself_still_records_the_decision(self):
        """Closing siblings must not disturb the conditional claim."""
        so = self._signoff()
        svc.decide(so, self.cfo, True)
        so.refresh_from_db()
        self.assertEqual(so.status, ExecSignoff.Status.APPROVED)
        self.assertEqual(so.decided_by, self.cfo)
        self.assertIsNotNone(so.decided_at)


class SignoffTaskTaggingTests(TestCase):
    """Exercise the REAL creation path (core.notifications.notify_exec_signoff_
    required), not hand-made rows — that is the line this change touched."""

    @classmethod
    def setUpTestData(cls):
        from core.models import UserProfile
        cls.ceo = User.objects.create_user('ceo-tag', 'ceo-tag@example.invalid', 'x')
        cls.cfo = User.objects.create_user('cfo-tag', 'cfo-tag@example.invalid', 'x')
        cls.applicant = User.objects.create_user('emp-tag', 'emp-tag@example.invalid', 'x')
        for u, title in ((cls.ceo, 'ceo'), (cls.cfo, 'cfo')):
            p, _ = UserProfile.objects.get_or_create(user=u)
            p.title = title
            p.save(update_fields=['title'])

    def _signoff(self):
        return ExecSignoff.objects.create(
            module=ExecSignoff.Module.INCENTIVE, object_id=uuid.uuid4(),
            applicant=self.applicant, applicant_name='Tumelo Fixture',
            reason='carrying overdue work', status=ExecSignoff.Status.PENDING)

    def test_the_real_creation_path_tags_every_task_with_the_signoff(self):
        from core import notifications
        so = self._signoff()

        notifications.notify_exec_signoff_required(so)

        tasks = OmniTask.objects.filter(title__startswith='Sign off: ')
        self.assertGreaterEqual(tasks.count(), 2, 'no signer tasks were raised')
        want = svc.signoff_task_source(so)
        self.assertTrue(all(t.source == want for t in tasks),
                        f'untagged task(s): {[t.source for t in tasks]}')

    def test_created_then_decided_leaves_no_open_task_for_anyone(self):
        """End to end on the real paths: raise -> sign -> nobody is chased."""
        from core import notifications
        so = self._signoff()
        notifications.notify_exec_signoff_required(so)
        self.assertTrue(OmniTask.objects.filter(
            status__in=[OmniTask.Status.PENDING, OmniTask.Status.IN_PROGRESS]).exists())

        svc.decide(so, self.cfo, True)

        still_open = OmniTask.objects.filter(
            title__startswith='Sign off: ',
            status__in=[OmniTask.Status.PENDING, OmniTask.Status.IN_PROGRESS])
        self.assertEqual(still_open.count(), 0,
                         f'still chasing: {[t.assignee.username for t in still_open]}')

    def test_a_broken_close_is_reported_loudly_not_swallowed(self):
        """A failed tidy-up leaves the exact stale tasks this fix removes, so it
        must reach the log with a stack trace and a distinguishable -1."""
        from unittest import mock
        so = self._signoff()
        with mock.patch('hris.exec_signoff_service.signoff_task_source',
                        side_effect=RuntimeError('boom')):
            with self.assertLogs('hris.exec_signoff_service', level='ERROR') as cap:
                closed = svc._close_signer_tasks(so)
        self.assertEqual(closed, -1)
        self.assertIn('could NOT be closed', '\n'.join(cap.output))

    def test_a_failed_close_does_not_poison_a_callers_transaction(self):
        """staff_loans.cfo_decide is @transaction.atomic and reaches this code
        via auto_resolve, then queries again on the very next line. Catching a
        DatabaseError WITHOUT a savepoint marks the outer transaction
        needs-rollback, so that next query raises TransactionManagementError
        and the CFO's loan approval 500s and rolls back — the tidy-up
        destroying the decision it swore not to touch.

        The failure must come from the REAL connection: a mock that merely
        raises DatabaseError in Python never marks the transaction broken, so
        that version of this test passed with and without the savepoint and
        proved nothing. This one runs genuinely invalid SQL.
        """
        from unittest import mock
        from django.db import connection
        so = self._signoff()

        def _break_the_connection(*a, **kw):
            with connection.cursor() as c:
                c.execute('SELECT * FROM a_table_that_does_not_exist_xyz')

        with mock.patch('core.models.OmniTask.objects.filter',
                        side_effect=_break_the_connection):
            with self.assertLogs('hris.exec_signoff_service', level='ERROR'):
                closed = svc._close_signer_tasks(so)
        self.assertEqual(closed, -1)

        # TestCase wraps each test in atomic, so this IS a caller's block.
        # Without the savepoint this raises TransactionManagementError.
        self.assertGreaterEqual(OmniTask.objects.count(), 0)


class RoutineSignoffFilterTests(TestCase):
    """The CEO brief must not carry routine staff sign-offs (CFO 2026-08-21)."""

    def _task(self, **kw):
        from core.models import OmniTask
        return OmniTask(**kw)

    def test_a_tagged_signoff_task_is_routine(self):
        self.assertTrue(svc.is_routine_signoff_task(
            self._task(source='exec_signoff:1a2b3c4d', title='Sign off: X — leave request')))

    def test_a_legacy_untagged_signoff_task_is_still_routine(self):
        """The prod rows predate the tag; matching only on source would miss
        exactly the items the CFO pointed at."""
        self.assertTrue(svc.is_routine_signoff_task(
            self._task(source='', title='Sign off: X — incentive request')))
        self.assertTrue(svc.is_routine_signoff_task(
            self._task(source='', title='Sign off: X — staff loan')))

    def test_real_ceo_work_is_not_routine(self):
        """These two stayed in the brief and must keep staying."""
        for src, title in (
            ('dev_dialogue', "Review & sign someone's Development Dialogue"),
            ('monthly_feedback:2026-07', 'Monthly performance feedback — 7 team member(s)'),
            ('payment_request', 'Payment authorisation — ADIC · Claim payments · BWP 202,005.92'),
            ('stuck:commission_approvals', 'Approve the commissions waiting with you'),
            ('', 'Board pack for Thursday'),
        ):
            with self.subTest(src=src):
                self.assertFalse(svc.is_routine_signoff_task(
                    self._task(source=src, title=title)))

    def test_missing_attributes_do_not_explode(self):
        """The brief engine runs unattended at 04:30 — a None must not kill it."""
        self.assertFalse(svc.is_routine_signoff_task(self._task(source=None, title=None)))
        self.assertFalse(svc.is_routine_signoff_task(object()))
