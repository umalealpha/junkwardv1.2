"""Consolidated 'morning to-dos' email — CONSOLIDATED_EMAILS_ENABLED (CFO 2026-08-05).

Behaviour contract this pins:
  * Flag OFF → the per-person task digest and the Manager Accountability note go
    out as TWO separate emails, exactly as before. email_open_reminders never even
    computes accountability, and send_manager_accountability does NOT self-skip.
  * Flag ON → the 06:30 task sweep is the single sender:
      (b) a manager with due tasks AND a gap gets ONE email carrying both
          sections, and no standalone accountability email;
      (c) a manager with a gap but NO due tasks gets ONE email — just the note;
      (d) a staffer with tasks who is not a manager gets ONE email — tasks only;
      (e) a person with neither gets NO email;
      and send_manager_accountability self-skips its own send.

The Time Doctor compute (managers_on_the_hook internals) is exercised by
hris/tests/test_manager_accountability_*.py; here it is MOCKED so these tests are
about the fold/route logic only. Fixture + mail.outbox style follows
taskboard/tests.py.

Needs a DB (Postgres in CI; sqlite dies on ledger migration 0022):
    python manage.py test taskboard.test_consolidated_todos
"""
from io import StringIO
from unittest import mock

from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import OmniTask

from . import services

# A unique sentinel so we can assert the accountability section was folded in,
# without coupling the test to build_manager_email's exact wording.
MGR_SECTION = '<div>[[ACCOUNTABILITY-NOTE-FOR-TESTS]]</div>'
# The HR escalation addresses the note (folded or standalone) must be CC'd to.
HR_CC = {'ubutale@alphadirect.co.bw', 'dikgopoleng@alphadirect.co.bw'}
CFO_CC = 'excoboard@alphadirect.co.bw'
# Where managers_on_the_hook is imported from inside email_open_reminders.
MOTH = 'hris.manager_accountability.managers_on_the_hook'


class _Base(TestCase):
    def setUp(self):
        self.boss = User.objects.create_user('boss', email='boss@alphadirect.co.bw')

    def _due_task(self, user, title='Reconcile FNB'):
        return OmniTask.objects.create(
            assigner=self.boss, assignee=user, title=title,
            due_at=timezone.localdate(), status=OmniTask.Status.PENDING)

    def _html(self, msg):
        return next(c for c, m in msg.alternatives if m == 'text/html')


@override_settings(CONSOLIDATED_EMAILS_ENABLED=False)
class FlagOffTests(_Base):
    """(a) Flag OFF ⇒ two separate emails, exactly as today."""

    def test_task_digest_is_alone_and_accountability_never_computed(self):
        mgr = User.objects.create_user('mgr', email='mgr@alphadirect.co.bw')
        self._due_task(mgr)
        with mock.patch(MOTH) as moth:
            services.email_open_reminders()
        # Flag OFF: accountability is never even looked at.
        moth.assert_not_called()
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.to, ['mgr@alphadirect.co.bw'])
        html = self._html(msg)
        self.assertIn('Reconcile FNB', html)
        self.assertNotIn(MGR_SECTION, html)
        # A plain task email carries no HR CC and no CFO CC (cc_cfo=False).
        for addr in HR_CC:
            self.assertNotIn(addr, msg.cc or [])
        self.assertNotIn(CFO_CC, msg.cc or [])

    def test_standalone_command_does_not_selfskip_when_off(self):
        # No TD token ⇒ the command skips for THAT reason, proving it ran past the
        # consolidated guard (which would print a different message and send none).
        out = StringIO()
        with mock.patch('integrations.timedoctor.TimeDoctorClient.from_settings') as fs:
            fs.return_value = mock.Mock(configured=False)
            call_command('send_manager_accountability', stdout=out)
        text = out.getvalue()
        self.assertIn('TIMEDOCTOR_TOKEN not set', text)
        self.assertNotIn('CONSOLIDATED_EMAILS_ENABLED', text)


@override_settings(CONSOLIDATED_EMAILS_ENABLED=True)
class FlagOnTests(_Base):
    def test_manager_with_tasks_and_gaps_gets_one_merged_email(self):
        """(b) tasks AND gaps ⇒ ONE email, both sections, HR CC'd, no standalone."""
        mgr = User.objects.create_user('mgr', email='mgr@alphadirect.co.bw')
        self._due_task(mgr)
        with mock.patch(MOTH, return_value={'mgr@alphadirect.co.bw': MGR_SECTION}):
            services.email_open_reminders()
        self.assertEqual(len(mail.outbox), 1)          # ONE email, not two
        msg = mail.outbox[0]
        self.assertEqual(msg.to, ['mgr@alphadirect.co.bw'])
        html = self._html(msg)
        self.assertIn('Reconcile FNB', html)           # task section present
        self.assertIn(MGR_SECTION, html)               # accountability folded in
        # Merged email CCs Human Resources, never the CFO.
        self.assertTrue(HR_CC.issubset(set(msg.cc or [])))
        self.assertNotIn(CFO_CC, msg.cc or [])

    def test_manager_with_gaps_but_no_tasks_gets_note_only(self):
        """(c) gap but no tasks ⇒ ONE email, just the note, HR CC'd."""
        User.objects.create_user('mgr', email='mgr@alphadirect.co.bw')
        with mock.patch(MOTH, return_value={'mgr@alphadirect.co.bw': MGR_SECTION}):
            services.email_open_reminders()
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.to, ['mgr@alphadirect.co.bw'])
        html = self._html(msg)
        self.assertIn(MGR_SECTION, html)
        self.assertNotIn('needing attention in Omni', html)   # no task table
        self.assertTrue(HR_CC.issubset(set(msg.cc or [])))
        self.assertNotIn(CFO_CC, msg.cc or [])

    def test_staffer_with_tasks_but_not_a_manager_gets_tasks_only(self):
        """(d) tasks, no gap ⇒ ONE email, tasks only, no HR CC."""
        staff = User.objects.create_user('staff', email='staff@alphadirect.co.bw')
        self._due_task(staff)
        with mock.patch(MOTH, return_value={}):
            services.email_open_reminders()
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        html = self._html(msg)
        self.assertIn('Reconcile FNB', html)
        self.assertNotIn(MGR_SECTION, html)
        for addr in HR_CC:
            self.assertNotIn(addr, msg.cc or [])

    def test_person_with_neither_gets_nothing(self):
        """(e) no tasks, no gap ⇒ NO email."""
        User.objects.create_user('idle', email='idle@alphadirect.co.bw')
        with mock.patch(MOTH, return_value={}):
            services.email_open_reminders()
        self.assertEqual(len(mail.outbox), 0)

    def test_gap_only_manager_deduped_on_same_day_rerun(self):
        """No double-send: a second sweep the same day sends the note-only email
        just once (TaskReminderEmailLog one-per-person-per-day guard)."""
        User.objects.create_user('mgr', email='mgr@alphadirect.co.bw')
        with mock.patch(MOTH, return_value={'mgr@alphadirect.co.bw': MGR_SECTION}):
            services.email_open_reminders()
            services.email_open_reminders()
        self.assertEqual(len(mail.outbox), 1)

    def test_td_outage_never_blocks_the_task_reminders(self):
        """Accountability is additive: if the TD compute raises, task emails still
        go out (just without any folded note)."""
        staff = User.objects.create_user('staff', email='staff@alphadirect.co.bw')
        self._due_task(staff)
        with mock.patch(MOTH, side_effect=RuntimeError('TD down')):
            services.email_open_reminders()
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Reconcile FNB', self._html(mail.outbox[0]))

    def test_standalone_command_selfskips_when_on(self):
        """send_manager_accountability sends nothing when the flag is on — the
        sweep delivers accountability instead. It must not even build a TD client."""
        out = StringIO()
        with mock.patch('integrations.timedoctor.TimeDoctorClient.from_settings') as fs:
            fs.side_effect = AssertionError('must not build a TD client when self-skipping')
            call_command('send_manager_accountability', stdout=out)
        self.assertIn('CONSOLIDATED_EMAILS_ENABLED on', out.getvalue())
        self.assertEqual(len(mail.outbox), 0)

    def test_dry_run_still_runs_with_flag_on(self):
        """--dry-run must stay testable by hand even with the flag on: it does NOT
        self-skip (it reaches the TD client, which here is unconfigured)."""
        out = StringIO()
        with mock.patch('integrations.timedoctor.TimeDoctorClient.from_settings') as fs:
            fs.return_value = mock.Mock(configured=False)
            call_command('send_manager_accountability', '--dry-run', stdout=out)
        text = out.getvalue()
        self.assertIn('TIMEDOCTOR_TOKEN not set', text)
        self.assertNotIn('CONSOLIDATED_EMAILS_ENABLED on', text)
