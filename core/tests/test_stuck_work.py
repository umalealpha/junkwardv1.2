"""core/tests/test_stuck_work.py — the stuck-work chaser.

CFO 2026-08-20, on an August commission sitting two days at the first review with
42 unapproved June + July submissions behind it: *"you have not put tasks for them
thats why they were sitting on the commissions, can we create auto reminders and
tasks if someone is sitting on something"*.

What these tests pin:
  * a pending item raises ONE rolling OmniTask for each person who can clear it,
    with a REAL deadline (landed + sla_days) so it goes genuinely overdue and the
    existing 06:30 digest + the 2-day overdue gate both bite;
  * a second sweep refreshes that task instead of creating a duplicate;
  * the task closes itself the moment the queue is empty;
  * priority climbs with age, and only items past escalate_days escalate — once;
  * a person with no email is skipped, not crashed on.

Needs a DB (Postgres in CI; sqlite dies on the ledger migrations):
    python manage.py test core.tests.test_stuck_work
"""
import os
from datetime import date, timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase

from core import stuck_work
from core.models import OmniTask
from core.stuck_work import PendingItem, Watcher

TODAY = date(2026, 8, 20)


class _Fixture(TestCase):
    def setUp(self):
        self.cfo = User.objects.create_user(
            'cfo', email='pganesharajah@alphadirect.co.bw',
            first_name='Chief', last_name='Fo', is_superuser=True)
        self.rev1 = User.objects.create_user(
            'rev1', email='rev1@example.test',
            first_name='Naledi', last_name='Sepako')
        self.rev2 = User.objects.create_user(
            'rev2', email='rev2@example.test',
            first_name='Kefilwe', last_name='Modise')

    def _watcher(self, items, *, sla_days=2, escalate_days=5):
        return Watcher(
            key='test_queue', label='Commission approvals',
            task_title='Approve commissions waiting with you',
            url='/commissions', sla_days=sla_days, escalate_days=escalate_days,
            pending=lambda: items,
        )

    def _item(self, days_old, owed_by=None, ref='Agent Nine — 2026-08'):
        return PendingItem(
            ref=ref, detail='P33,281.47 · first review',
            since=TODAY - timedelta(days=days_old),
            owed_by=tuple(owed_by or [self.rev1, self.rev2]),
        )

    def _open(self, user):
        return OmniTask.objects.filter(
            assignee=user, status__in=[OmniTask.Status.PENDING,
                                       OmniTask.Status.IN_PROGRESS])


class RaisesTasks(_Fixture):
    def test_one_task_per_person_who_can_clear_it(self):
        res = stuck_work.sweep(watchers=[self._watcher([self._item(2)])], today=TODAY)

        self.assertEqual(res['tasks_created'], 2)
        for u in (self.rev1, self.rev2):
            t = self._open(u).get()
            self.assertEqual(t.title, 'Approve commissions waiting with you')
            self.assertIn('Agent Nine', t.body)
            self.assertIn('waiting 2 days', t.body)
            self.assertIn('/commissions', t.body)
            self.assertEqual(t.source, 'stuck:test_queue')

    def test_deadline_is_when_the_item_became_due_not_today(self):
        """A task due 'today' every day never goes overdue — so the overdue gate
        (leave / loan / incentive countersign) and the perf rating cap never bite,
        which is exactly how 45 submissions sat unchased."""
        stuck_work.sweep(watchers=[self._watcher([self._item(6)])], today=TODAY)

        t = self._open(self.rev1).get()
        self.assertEqual(t.due_at, TODAY - timedelta(days=4))   # landed 6 days ago + 2-day SLA
        self.assertLess(t.due_at, TODAY, 'task must be genuinely overdue')

    def test_deadline_tracks_the_oldest_item(self):
        items = [self._item(1, ref='Bonno Ben — 2026-08'), self._item(9, ref='June batch')]
        stuck_work.sweep(watchers=[self._watcher(items)], today=TODAY)

        self.assertEqual(self._open(self.rev1).get().due_at, TODAY - timedelta(days=7))

    def test_second_sweep_refreshes_instead_of_duplicating(self):
        w1 = self._watcher([self._item(2)])
        stuck_work.sweep(watchers=[w1], today=TODAY)
        res = stuck_work.sweep(
            watchers=[self._watcher([self._item(3), self._item(1, ref='Bonno Ben — 2026-08')])],
            today=TODAY)

        self.assertEqual(res['tasks_created'], 0)
        self.assertEqual(res['tasks_refreshed'], 2)
        t = self._open(self.rev1).get()          # .get() proves there is exactly one
        self.assertIn('Bonno Ben', t.body)

    def test_priority_climbs_with_age(self):
        stuck_work.sweep(watchers=[self._watcher([self._item(0)])], today=TODAY)
        self.assertEqual(self._open(self.rev1).get().priority, OmniTask.Priority.NORMAL)

        stuck_work.sweep(watchers=[self._watcher([self._item(3)])], today=TODAY)
        self.assertEqual(self._open(self.rev1).get().priority, OmniTask.Priority.HIGH)

        stuck_work.sweep(watchers=[self._watcher([self._item(9)])], today=TODAY)
        self.assertEqual(self._open(self.rev1).get().priority, OmniTask.Priority.URGENT)

    def test_person_without_an_email_is_skipped_not_crashed_on(self):
        ghost = User.objects.create_user('ghost', email='')
        res = stuck_work.sweep(
            watchers=[self._watcher([self._item(2, owed_by=[ghost, self.rev1])])], today=TODAY)

        self.assertEqual(res['tasks_created'], 1)
        self.assertFalse(self._open(ghost).exists())


class ClosesItself(_Fixture):
    def test_task_closes_when_the_queue_empties(self):
        stuck_work.sweep(watchers=[self._watcher([self._item(2)])], today=TODAY)
        res = stuck_work.sweep(watchers=[self._watcher([])], today=TODAY)

        self.assertEqual(res['tasks_closed'], 2)
        self.assertFalse(self._open(self.rev1).exists())
        t = OmniTask.objects.filter(assignee=self.rev1).get()
        self.assertEqual(t.status, OmniTask.Status.DONE)
        self.assertIsNotNone(t.completed_at)

    def test_closes_only_this_watchers_tasks(self):
        mine = OmniTask.objects.create(
            assigner=self.cfo, assignee=self.rev1, title='Something else entirely',
            status=OmniTask.Status.PENDING)
        stuck_work.sweep(watchers=[self._watcher([self._item(2)])], today=TODAY)
        stuck_work.sweep(watchers=[self._watcher([])], today=TODAY)

        mine.refresh_from_db()
        self.assertEqual(mine.status, OmniTask.Status.PENDING)


class Escalation(_Fixture):
    def test_only_items_past_escalate_days_escalate(self):
        mail.outbox = []
        stuck_work.sweep(watchers=[self._watcher([self._item(4)])], today=TODAY)
        self.assertEqual(len(mail.outbox), 0, 'inside the escalation window — no alarm')

        stuck_work.sweep(watchers=[self._watcher([self._item(6)])], today=TODAY)
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body + ''.join(c for c, _ in mail.outbox[0].alternatives)
        self.assertIn('Agent Nine', body)
        self.assertIn('Naledi Sepako', body, 'must name who is sitting on it')

    def test_escalation_is_once_a_day_not_once_a_sweep(self):
        stuck_work.sweep(watchers=[self._watcher([self._item(6)])], today=TODAY)
        mail.outbox = []
        stuck_work.sweep(watchers=[self._watcher([self._item(6)])], today=TODAY)
        self.assertEqual(len(mail.outbox), 0)

        stuck_work.sweep(watchers=[self._watcher([self._item(7)])],
                         today=TODAY + timedelta(days=1))
        self.assertEqual(len(mail.outbox), 1, 'a new day escalates again')

    def test_dry_run_writes_nothing(self):
        mail.outbox = []
        res = stuck_work.sweep(watchers=[self._watcher([self._item(9)])],
                               today=TODAY, dry_run=True)

        self.assertEqual(res['tasks_created'], 2)
        self.assertFalse(OmniTask.objects.exists())
        self.assertEqual(len(mail.outbox), 0)


class CommissionsWatcher(TestCase):
    """The real watcher: every submission awaiting review is owed by that stage's
    roster, and nothing else is."""

    def test_pending_covers_every_awaiting_status_and_nothing_else(self):
        from commissions.stuck import COMMISSIONS_WATCHER
        from commissions.models import (CommissionAgent, CommissionGroup,
                                        CommissionSubmission)

        User.objects.create_user('r', email='rev1@example.test',
                                 first_name='Naledi', last_name='Sepako')
        # The 3 groups are seeded by migration 0002 — reuse, don't recreate
        # (key is unique).
        g = CommissionGroup.objects.get(key=CommissionGroup.Key.IN_HOUSE)
        S = CommissionSubmission.Status
        # One agent per status — (agent, period_label) is unique.
        for n, status in enumerate((S.DRAFT, S.SUBMITTED, S.SECOND_REVIEW,
                                    S.FINAL_REVIEW, S.APPROVED, S.PAID, S.REJECTED)):
            a = CommissionAgent.objects.create(
                group=g, name=f'Test Agent {n}', email=f'ta{n}@alphadirect.co.bw')
            CommissionSubmission.objects.create(
                group=g, agent=a, period_label='2026-08', status=status,
                gross_commission=100, net_payable=100)

        env = {'COMMISSIONS_STAGE1_EMAILS': 'rev1@example.test',
               'COMMISSIONS_STAGE2_EMAILS': 'rev1@example.test',
               'COMMISSIONS_FINAL_EMAILS': 'rev1@example.test'}
        with mock.patch.dict(os.environ, env):
            items = COMMISSIONS_WATCHER.pending()
        refs = [i.ref for i in items]
        self.assertEqual(len(refs), 3, 'submitted + second_review + final_review only')

    def test_is_registered_in_the_default_sweep(self):
        self.assertIn('commission_approvals', {w.key for w in stuck_work.WATCHERS})
