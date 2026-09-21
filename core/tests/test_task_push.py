"""send_task_push (CFO 2026-09-03): one morning nudge per person with tasks due
today or overdue. No push when disabled; body carries the count; nobody with
zero due tasks is pushed.
"""
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from core.models import OmniTask, PushSubscription

CMD = 'core.management.commands.send_task_push'


def _subscribe(user, tag):
    return PushSubscription.objects.create(
        user=user, endpoint=f'https://push.example.test/{tag}',
        p256dh='p', auth='a')


class SendTaskPushTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.boss = User.objects.create_user('push_assigner', password='x')
        cls.busy = User.objects.create_user('push_busy', password='x')
        cls.idle = User.objects.create_user('push_idle', password='x')
        _subscribe(cls.busy, 'busy')
        _subscribe(cls.idle, 'idle')
        today = timezone.localdate()
        # Two live tasks for `busy`: one due today, one overdue.
        OmniTask.objects.create(assigner=cls.boss, assignee=cls.busy,
                                title='Due today', due_at=today)
        OmniTask.objects.create(assigner=cls.boss, assignee=cls.busy,
                                title='Overdue', due_at=today - timedelta(days=3))
        # Noise that must NOT count: done, due tomorrow, and no due date.
        OmniTask.objects.create(assigner=cls.boss, assignee=cls.busy,
                                title='Finished', due_at=today,
                                status=OmniTask.Status.DONE)
        OmniTask.objects.create(assigner=cls.boss, assignee=cls.busy,
                                title='Tomorrow', due_at=today + timedelta(days=1))
        OmniTask.objects.create(assigner=cls.boss, assignee=cls.idle,
                                title='Someday', due_at=None)

    def _run(self):
        out = StringIO()
        call_command('send_task_push', stdout=out)
        return out.getvalue()

    def test_no_send_when_push_disabled(self):
        with patch('core.webpush.push_enabled', return_value=False), \
             patch('core.webpush.send_push_to_user') as send:
            out = self._run()
        send.assert_not_called()
        self.assertIn('disabled', out)

    def test_user_with_two_due_tasks_gets_exactly_one_push_with_count(self):
        with patch('core.webpush.push_enabled', return_value=True), \
             patch('core.webpush.send_push_to_user', return_value=1) as send:
            out = self._run()
        pushed_to = [c.args[0] for c in send.call_args_list]
        self.assertEqual(pushed_to, [self.busy])
        _, title, body = send.call_args.args
        self.assertIn('2', body)
        self.assertIn('waiting on you', body)
        self.assertEqual(send.call_args.kwargs.get('url'), '/app/tasks')
        self.assertNotIn('CFO', title + body)
        self.assertIn('Nudged 1 person(s); 1 device(s).', out)

    def test_user_with_zero_due_tasks_gets_nothing(self):
        with patch('core.webpush.push_enabled', return_value=True), \
             patch('core.webpush.send_push_to_user', return_value=1) as send:
            self._run()
        self.assertNotIn(self.idle, [c.args[0] for c in send.call_args_list])
