"""send_morning_push (CFO 2026-09-03): ONE push per person each morning —
payments (count + the itemiser's amounts), other approvals, tasks due. Nothing
when push is disabled; nothing for a person with nothing waiting; the URL is the
deepest single destination.

The approval helpers are patched (same as test_bulk_approve) so the composition
is tested without heavy Payment fixtures; tasks are real OmniTask rows.

Run: python manage.py test core.tests.test_morning_brief
"""
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from core.models import OmniTask, PushSubscription


def _subscribe(user, tag):
    return PushSubscription.objects.create(
        user=user, endpoint=f'https://push.example.test/{tag}',
        p256dh='p', auth='a')


def _stream(key, count, label=None):
    return {"key": key, "label": label or key, "count": count,
            "href": "/x", "oldest_days": 1}


def _item(pk, amount, ccy="BWP"):
    return {"id": str(pk), "title": f"PAY-{pk}", "sub": "", "amount": amount,
            "ccy": ccy, "age_days": 1, "flags": []}


class SendMorningBriefTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.boss = User.objects.create_user('brief_assigner', password='x')
        cls.signer = User.objects.create_user('brief_signer', password='x')   # 2 payments + 1 task
        cls.payer = User.objects.create_user('brief_payer', password='x')     # payments only
        cls.hr = User.objects.create_user('brief_hr', password='x')           # other approvals only
        cls.idle = User.objects.create_user('brief_idle', password='x')       # nothing
        for u, tag in ((cls.signer, 's'), (cls.payer, 'p'), (cls.hr, 'h'), (cls.idle, 'i')):
            _subscribe(u, tag)
        today = timezone.localdate()
        OmniTask.objects.create(assigner=cls.boss, assignee=cls.signer,
                                title='Overdue', due_at=today - timedelta(days=2))
        # Noise that must NOT count for the signer: done, and due tomorrow.
        OmniTask.objects.create(assigner=cls.boss, assignee=cls.signer,
                                title='Finished', due_at=today,
                                status=OmniTask.Status.DONE)
        OmniTask.objects.create(assigner=cls.boss, assignee=cls.signer,
                                title='Tomorrow', due_at=today + timedelta(days=1))

    # --- fakes standing in for the 24-stream inbox + the itemiser -----------
    def _streams(self, u):
        if u in (self.signer, self.payer):
            return [_stream("payments", 2, "Payments to sign")]
        if u == self.hr:
            return [_stream("leave", 2, "Leave requests to approve"),
                    _stream("petty_cash", 1, "Petty cash to approve")]
        return []

    def _items(self, u):
        if u in (self.signer, self.payer):
            return [{"key": "payments", "label": "Payments to sign", "href": "/x",
                     "bulk_ok": True, "items": [_item(1, 100.0), _item(2, 250.5)]}]
        return []

    def _run(self, enabled=True):
        out = StringIO()
        with patch('core.webpush.push_enabled', return_value=enabled), \
             patch('core.webpush.send_push_to_user', return_value=1) as send, \
             patch('core.approvals_views.pending_approvals_for', side_effect=self._streams), \
             patch('core.approvals_views.pending_approval_items_for', side_effect=self._items):
            call_command('send_morning_push', stdout=out)
        return send, out.getvalue()

    def _call_for(self, send, user):
        calls = [c for c in send.call_args_list if c.args[0] == user]
        return calls

    def test_no_send_when_push_disabled(self):
        send, out = self._run(enabled=False)
        send.assert_not_called()
        self.assertIn('disabled', out)

    def test_user_with_nothing_waiting_gets_nothing(self):
        send, _ = self._run()
        self.assertEqual(self._call_for(send, self.idle), [])

    def test_two_payments_and_one_task_is_one_push_with_total_and_count(self):
        send, out = self._run()
        calls = self._call_for(send, self.signer)
        self.assertEqual(len(calls), 1, "exactly one push per person")
        _, title, body = calls[0].args
        self.assertIn('3', title)
        self.assertIn('Good morning', title)
        self.assertIn('350.50', body)
        self.assertIn('BWP', body)
        self.assertIn('2 payments', body)
        self.assertIn('1 task', body)
        self.assertEqual(calls[0].kwargs.get('url'), '/app')
        self.assertNotIn('CFO', title + body)
        self.assertIn('Briefed 3 person(s)', out)

    def test_payments_only_goes_straight_to_the_payments_screen(self):
        send, _ = self._run()
        calls = self._call_for(send, self.payer)
        self.assertEqual(len(calls), 1)
        _, title, body = calls[0].args
        self.assertIn('2 things', title)
        self.assertIn('350.50', body)
        self.assertNotIn('task', body)
        self.assertEqual(calls[0].kwargs.get('url'), '/app/payments')

    def test_other_approvals_name_the_top_streams(self):
        send, _ = self._run()
        calls = self._call_for(send, self.hr)
        self.assertEqual(len(calls), 1)
        _, title, body = calls[0].args
        self.assertIn('3 things', title)
        self.assertIn('2 leave', body)
        self.assertIn('1 petty cash', body)
        self.assertEqual(calls[0].kwargs.get('url'), '/app')

    def test_one_persons_error_does_not_stop_the_loop(self):
        def boom(u):
            if u == self.hr:
                raise RuntimeError('stream blew up')
            return self._streams(u)
        out = StringIO()
        with patch('core.webpush.push_enabled', return_value=True), \
             patch('core.webpush.send_push_to_user', return_value=1) as send, \
             patch('core.approvals_views.pending_approvals_for', side_effect=boom), \
             patch('core.approvals_views.pending_approval_items_for', side_effect=self._items), \
             self.assertLogs('core.management.commands.send_morning_push', level='ERROR'):
            call_command('send_morning_push', stdout=out)
        self.assertEqual(len(self._call_for(send, self.signer)), 1)
        self.assertEqual(len(self._call_for(send, self.payer)), 1)
        self.assertIn('1 failed', out.getvalue())
