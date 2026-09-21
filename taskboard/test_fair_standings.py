"""The Alpha League — fair Delivery Score (CFO 2026-08-26).

Each test fails on the old pct_done ranking / champion-loser code:
 - a high-volume finisher with pending work outranks a 1-of-1 (the CFO's case);
 - the late-tax is capped and the score never goes negative;
 - done tasks older than 14 days are hidden from the list but still don't change
   anyone's score, and ?all_done=1 shows them;
 - the leaderboard no longer has a 'loser' — it has standings + needs_support.
"""
from __future__ import annotations

from datetime import timedelta

from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from core.models import OmniTask, TaskFeedback
from taskboard.cfo_views import (_standings, _monthly_rewards, REWARD_MIN_TASKS,
                                 REWARD_PER_TASK, REWARD_PER_POINT, REWARD_POINT_MIN)
from django.core.management import call_command
from django.core import mail


def _mk(assigner, assignee, status, *, priority='normal', done_days_ago=1, overdue=False):
    due = (timezone.localdate() - timedelta(days=5)) if overdue else None
    t = OmniTask.objects.create(assigner=assigner, assignee=assignee, title='t',
                                status=status, priority=priority, due_at=due)
    if status == OmniTask.Status.DONE:
        t.completed_at = timezone.now() - timedelta(days=done_days_ago)
        t.save(update_fields=['completed_at'])
    return t


class FairStandingsTests(APITestCase):
    def setUp(self):
        self.mgr = User.objects.create_superuser('mgr', 'mgr@x.co', 'x')
        self.hi = User.objects.create_user('hi', password='x', first_name='Hi', last_name='V')
        self.lo = User.objects.create_user('lo', password='x', first_name='Lo', last_name='V')
        self.client.force_authenticate(self.mgr)

    def test_high_volume_with_pending_outranks_one_of_one(self):
        for _ in range(20):
            _mk(self.mgr, self.hi, OmniTask.Status.DONE)
        for _ in range(5):                      # pending must NOT hurt the score
            _mk(self.mgr, self.hi, OmniTask.Status.PENDING)
        _mk(self.mgr, self.lo, OmniTask.Status.DONE)   # the 1-of-1 "100%" person
        rows = _standings(self.mgr)
        by = {r['assignee_id']: r for r in rows}
        self.assertEqual(rows[0]['assignee_id'], self.hi.id)     # high-volume is #1
        self.assertGreater(by[self.hi.id]['score'], by[self.lo.id]['score'])

    def test_late_tax_is_capped_and_score_never_negative(self):
        for _ in range(10):
            _mk(self.mgr, self.hi, OmniTask.Status.DONE)         # delivered = 20 (normal=2)
        for _ in range(50):
            _mk(self.mgr, self.hi, OmniTask.Status.PENDING, overdue=True)
        row = _standings(self.mgr)[0]
        base = row['delivered'] + row['bonus']
        self.assertLessEqual(row['late_tax'], round(0.20 * base) + 1)   # cap ~20%
        self.assertGreaterEqual(row['score'], 0)                        # floor

    def test_zero_delivered_all_overdue_floors_at_zero(self):
        for _ in range(3):
            _mk(self.mgr, self.lo, OmniTask.Status.PENDING, overdue=True)
        row = {r['assignee_id']: r for r in _standings(self.mgr)}[self.lo.id]
        self.assertEqual(row['score'], 0)

    def test_old_done_hidden_from_list_but_score_unchanged(self):
        _mk(self.mgr, self.hi, OmniTask.Status.DONE, done_days_ago=2)    # recent
        _mk(self.mgr, self.hi, OmniTask.Status.DONE, done_days_ago=40)   # old
        url = reverse('v1-taskboard-overview')
        default = self.client.get(url).json()
        all_done = self.client.get(url + '?all_done=1').json()
        self.assertEqual(len(default['tasks']), 1)          # old one hidden
        self.assertEqual(len(all_done['tasks']), 2)         # shown with the flag
        # score counts only the in-window task, and is identical either way
        s1 = {r['assignee_id']: r for r in default['standings']}[self.hi.id]
        s2 = {r['assignee_id']: r for r in all_done['standings']}[self.hi.id]
        self.assertEqual(s1['done_14d'], 1)
        self.assertEqual(s1['score'], s2['score'])

    def test_leaderboard_has_no_loser_and_has_standings(self):
        for _ in range(3):
            _mk(self.mgr, self.hi, OmniTask.Status.DONE)
        _mk(self.mgr, self.lo, OmniTask.Status.BLOCKED)      # a support case
        body = self.client.get(reverse('v1-taskboard-hall-of-fame')).json()
        self.assertNotIn('loser', body)
        self.assertIn('standings', body)
        self.assertIn('needs_support', body)
        self.assertEqual(body['champion']['assignee_id'], self.hi.id)


class TaskIncentiveTests(APITestCase):
    """BWP 50 per MANAGER-CONFIRMED task above 30/month (CFO 2026-08-26)."""
    def setUp(self):
        self.mgr = User.objects.create_superuser('mgr', 'mgr@x.co', 'x')
        self.hi = User.objects.create_user('hi', password='x', first_name='Hi', last_name='V')
        self.lo = User.objects.create_user('lo', password='x', first_name='Lo', last_name='V')

    def _confirmed_done(self, assignee, n):
        """n done-this-month tasks WITH manager feedback (manager-confirmed)."""
        for _ in range(n):
            t = _mk(self.mgr, assignee, OmniTask.Status.DONE)
            TaskFeedback.objects.create(task=t, from_user=self.mgr, to_user=assignee, body='confirmed done')

    def test_reward_is_50_per_task_above_30(self):
        self._confirmed_done(self.hi, 35)          # 5 above 30 → P250
        r = _monthly_rewards(self.mgr)[self.hi.id]
        self.assertEqual(r['confirmed_month'], 35)
        self.assertEqual(r['reward_bwp'], 5 * REWARD_PER_TASK)

    def test_below_minimum_earns_nothing(self):
        self._confirmed_done(self.lo, 30)          # exactly 30 → P0
        self.assertEqual(_monthly_rewards(self.mgr).get(self.lo.id, {}).get('reward_bwp', 0), 0)

    def test_self_marked_done_without_manager_feedback_does_not_count(self):
        for _ in range(40):                        # 40 done but NO manager feedback
            _mk(self.mgr, self.hi, OmniTask.Status.DONE)
        self.assertEqual(_monthly_rewards(self.mgr).get(self.hi.id, {}).get('reward_bwp', 0), 0)

    def test_generate_command_dry_run_creates_nothing(self):
        self._confirmed_done(self.hi, 33)
        from hris.incentive_models import IncentiveRequest
        call_command('generate_task_incentive')    # dry-run
        self.assertEqual(IncentiveRequest.objects.count(), 0)

    def test_reward_capped_at_2000(self):
        self._confirmed_done(self.hi, 100)         # 70 above 30 × 50 = 3500 → capped 2000
        self.assertEqual(_monthly_rewards(self.mgr)[self.hi.id]['reward_bwp'], 2000)

    def test_managers_and_exco_do_not_earn(self):
        from core.models import UserProfile
        boss = User.objects.create_user('boss', password='x', first_name='Big', last_name='Boss')
        UserProfile.objects.update_or_create(user=boss, defaults={'title': 'finance_manager'})
        self._confirmed_done(boss, 40)             # 40 confirmed but a manager → earns nothing
        self.assertEqual(_monthly_rewards(self.mgr).get(boss.id, {}).get('reward_bwp', 0), 0)

    def test_payment_request_and_approvals_are_not_tasks(self):
        # A payment-request / approval item must NOT count toward the incentive
        # and must NOT appear on the task board.
        from taskboard.cfo_views import _visible_tasks
        pr = OmniTask.objects.create(assigner=self.mgr, assignee=self.hi,
                                     title='Approve payment PAY-OUT-2026-000099',
                                     status=OmniTask.Status.DONE, source='payment_request')
        pr.completed_at = timezone.now(); pr.save(update_fields=['completed_at'])
        TaskFeedback.objects.create(task=pr, from_user=self.mgr, to_user=self.hi, body='ok')
        self._confirmed_done(self.hi, 31)          # 31 real confirmed → 1 above 30 → P50
        r = _monthly_rewards(self.mgr)[self.hi.id]
        self.assertEqual(r['confirmed_month'], 31)              # the approval did NOT add to the count
        self.assertEqual(r['reward_bwp'], REWARD_PER_TASK)
        # and it's off the task board
        self.assertFalse(_visible_tasks(self.mgr).filter(pk=pr.pk).exists())

    def test_generate_command_commit_creates_one_request(self):
        self._confirmed_done(self.hi, 33)          # 3 above 30 → P150
        from hris.incentive_models import IncentiveRequest
        ym = timezone.localdate().strftime('%Y-%m')
        call_command('generate_task_incentive', '--commit')
        reqs = IncentiveRequest.objects.filter(period=ym)
        self.assertEqual(reqs.count(), 1)
        req = reqs.first()
        self.assertEqual(req.lines.count(), 1)
        self.assertEqual(int(req.lines.first().amount), 3 * REWARD_PER_TASK)
        self.assertEqual(req.status, IncentiveRequest.Status.PENDING)   # not paid — awaits CFO+HR
        # idempotent
        call_command('generate_task_incentive', '--commit')
        self.assertEqual(IncentiveRequest.objects.filter(period=ym).count(), 1)

    def test_priority_weighted_hard_tasks_earn_more(self):
        # v2 (CFO 2026-08-26): reward follows task WEIGHT, not a flat count. 20
        # URGENT confirmed tasks (weight 5 = 100 points) earn, while 20 NORMAL
        # confirmed tasks (weight 2 = 40 points, below the 60 threshold) earn nothing
        # — same count, different reward. A flat-count model would give both the same.
        for _ in range(20):
            t = _mk(self.mgr, self.hi, OmniTask.Status.DONE, priority='urgent')
            TaskFeedback.objects.create(task=t, from_user=self.mgr, to_user=self.hi, body='ok')
        for _ in range(20):
            t = _mk(self.mgr, self.lo, OmniTask.Status.DONE, priority='normal')
            TaskFeedback.objects.create(task=t, from_user=self.mgr, to_user=self.lo, body='ok')
        rw = _monthly_rewards(self.mgr)
        self.assertEqual(rw[self.hi.id]['points_month'], 100)          # 20 × 5
        self.assertEqual(rw[self.hi.id]['reward_bwp'], (100 - REWARD_POINT_MIN) * REWARD_PER_POINT)  # P1000
        self.assertEqual(rw.get(self.lo.id, {}).get('reward_bwp', 0), 0)  # 40 pts < 60


class MyLeagueTests(APITestCase):
    """Staff-facing 'My League' (CFO 2026-08-26 v2) — each staff member sees THEIR
    own score, incentive progress, and what is waiting on a manager to confirm."""
    def setUp(self):
        self.mgr = User.objects.create_superuser('mgr', 'mgr@x.co', 'x')
        self.me = User.objects.create_user('me', password='x', first_name='Me', last_name='Self')
        self.other = User.objects.create_user('other', password='x', first_name='Oth', last_name='Er')

    def test_my_league_shows_own_score_reward_and_waiting(self):
        # two confirmed-done + one done-but-unconfirmed for me
        for _ in range(2):
            t = _mk(self.mgr, self.me, OmniTask.Status.DONE)
            TaskFeedback.objects.create(task=t, from_user=self.mgr, to_user=self.me, body='ok')
        _mk(self.mgr, self.me, OmniTask.Status.DONE)                 # finished, NOT confirmed
        _mk(self.mgr, self.other, OmniTask.Status.DONE)             # someone else's task
        self.client.force_authenticate(self.me)
        body = self.client.get(reverse('v1-taskboard-my-league')).json()
        self.assertEqual(body['me']['assignee_id'], self.me.id)
        self.assertGreater(body['me']['score'], 0)                  # 3 delivered
        self.assertEqual(body['reward']['confirmed_month'], 2)      # only the 2 confirmed count
        self.assertEqual(len(body['waiting_on_confirm']), 1)        # the unconfirmed one
        self.assertEqual(body['waiting_on_confirm'][0]['waiting_on'], self.mgr.get_full_name() or 'mgr')

    def test_my_league_never_leaks_another_persons_row(self):
        _mk(self.mgr, self.other, OmniTask.Status.DONE)
        self.client.force_authenticate(self.me)
        body = self.client.get(reverse('v1-taskboard-my-league')).json()
        self.assertEqual(body['me']['assignee_id'], self.me.id)     # my row, not other's
        self.assertNotIn('standings', body)                         # no full table for staff


class OneTapConfirmTests(APITestCase):
    """core.magic_action `task_confirm` — one-tap, no-login manager confirmation."""
    def setUp(self):
        self.mgr = User.objects.create_superuser('mgr', 'mgr@x.co', 'x')
        self.staff = User.objects.create_user('staff', password='x', first_name='St', last_name='Aff')
        self.intruder = User.objects.create_user('intruder', password='x')

    def _done_task(self):
        return _mk(self.mgr, self.staff, OmniTask.Status.DONE)

    def test_one_tap_creates_non_assignee_feedback_and_counts(self):
        from core.magic_action import _tc_act
        t = self._done_task()
        ok, msg = _tc_act(self.mgr, {'task_id': str(t.id)})
        self.assertTrue(ok)
        self.assertTrue(t.feedback.exclude(from_user_id=self.staff.id).exists())   # it counts
        # idempotent — a second tap does not add a second feedback
        _tc_act(self.mgr, {'task_id': str(t.id)})
        self.assertEqual(t.feedback.count(), 1)

    def test_only_the_assigner_or_admin_can_confirm(self):
        from core.magic_action import _tc_act
        t = self._done_task()
        ok, _ = _tc_act(self.intruder, {'task_id': str(t.id)})
        self.assertFalse(ok)
        self.assertFalse(t.feedback.exists())

    def test_cannot_confirm_a_task_not_yet_done(self):
        from core.magic_action import _tc_act
        t = _mk(self.mgr, self.staff, OmniTask.Status.PENDING)
        ok, _ = _tc_act(self.mgr, {'task_id': str(t.id)})
        self.assertFalse(ok)


class ConfirmDigestTests(APITestCase):
    """task_confirm_digest — twice-weekly manager nudge with one-tap links."""
    def setUp(self):
        self.mgr = User.objects.create_user('mgr', email='mgr@x.co', password='x',
                                             first_name='Man', last_name='Ager', is_superuser=True)
        self.staff = User.objects.create_user('staff', password='x', first_name='St', last_name='Aff')

    def test_dry_run_sends_nothing_commit_emails_the_manager(self):
        _mk(self.mgr, self.staff, OmniTask.Status.DONE)             # finished, unconfirmed
        call_command('task_confirm_digest')                        # dry-run
        self.assertEqual(len(mail.outbox), 0)
        call_command('task_confirm_digest', '--commit')
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('mgr@x.co', mail.outbox[0].to)
        self.assertIn('confirm', (mail.outbox[0].subject + mail.outbox[0].body).lower())

    def test_already_confirmed_task_is_not_nudged(self):
        t = _mk(self.mgr, self.staff, OmniTask.Status.DONE)
        TaskFeedback.objects.create(task=t, from_user=self.mgr, to_user=self.staff, body='ok')
        call_command('task_confirm_digest', '--commit')
        self.assertEqual(len(mail.outbox), 0)                      # nothing outstanding
