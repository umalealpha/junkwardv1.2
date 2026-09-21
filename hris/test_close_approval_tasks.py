"""
hris/test_close_approval_tasks.py — CFO batch 2026-07-22:
"done but still nagging" fix. When an approval item is decided (approved /
rejected) its "Approve …" reminder OmniTask must close so the approver stops
being nagged.

Proves the close-on-outcome helpers for every approval type that opens an
OmniTask:
  - payment  (close_payment_approval_tasks)   approved → DONE, rejected → CANCELLED
  - JE       (close_je_approval_tasks)         approved → DONE, rejected → CANCELLED
  - petty cash (close_petty_cash_tasks)        approved → DONE, rejected → CANCELLED
  - payment batch summary (close_payment_batch_tasks) → DONE when the approval
    queue is empty, stays open while a payment is still pending.

The close helpers each read a single attribute off the decided object, so the
tests pass lightweight duck-typed stubs (same approach as
hris/test_batch2_fixes.py::CloseJeApprovalTasks) rather than building heavy
Payment / JournalEntry / PettyCashVoucher fixtures.

Run: python manage.py test hris.test_close_approval_tasks
"""
from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase


class _CloseTaskBase(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        self.user = get_user_model().objects.create_user(
            'closer', 'closer@alphadirect.co.bw', 'pw')

    def _make_task(self, title):
        from core.models import OmniTask
        # assigner + assignee + priority are NOT NULL — supply real values.
        return OmniTask.objects.create(
            title=title, body='', status=OmniTask.Status.PENDING,
            priority=OmniTask.Priority.HIGH,
            assignee=self.user, assigner=self.user)


class ClosePaymentApprovalTasks(_CloseTaskBase):
    def test_marks_done_on_approve(self):
        from core.models import OmniTask
        from core.notifications import close_payment_approval_tasks

        class _P:
            payment_number = 'PMT-TEST-0001'
        t = self._make_task('Approve payment PMT-TEST-0001')
        close_payment_approval_tasks(_P(), 'approved')
        t.refresh_from_db()
        self.assertEqual(t.status, OmniTask.Status.DONE)
        self.assertIsNotNone(t.completed_at)

    def test_cancels_on_reject(self):
        from core.models import OmniTask
        from core.notifications import close_payment_approval_tasks

        class _P:
            payment_number = 'PMT-TEST-0002'
        t = self._make_task('Approve payment PMT-TEST-0002')
        close_payment_approval_tasks(_P(), 'rejected')
        t.refresh_from_db()
        self.assertEqual(t.status, OmniTask.Status.CANCELLED)


class CloseJeApprovalTasks(_CloseTaskBase):
    def test_marks_done_on_approve_both_title_variants(self):
        from core.models import OmniTask
        from core.notifications import close_je_approval_tasks

        class _JE:
            entry_number = 'JE-CLOSE-0001'
        t = self._make_task('Approve JE JE-CLOSE-0001')
        rp = self._make_task('Approve JE JE-CLOSE-0001 [RELATED PARTY]')
        close_je_approval_tasks(_JE(), 'approved')
        t.refresh_from_db(); rp.refresh_from_db()
        self.assertEqual(t.status, OmniTask.Status.DONE)
        self.assertEqual(rp.status, OmniTask.Status.DONE)

    def test_cancels_on_reject(self):
        from core.models import OmniTask
        from core.notifications import close_je_approval_tasks

        class _JE:
            entry_number = 'JE-CLOSE-0002'
        t = self._make_task('Approve JE JE-CLOSE-0002')
        close_je_approval_tasks(_JE(), 'rejected')
        t.refresh_from_db()
        self.assertEqual(t.status, OmniTask.Status.CANCELLED)


class ClosePettyCashTasks(_CloseTaskBase):
    def test_marks_done_on_approve(self):
        from core.models import OmniTask
        from core.notifications import close_petty_cash_tasks

        class _V:
            voucher_number = 'PC-TEST-0001'
        t = self._make_task('Petty cash PC-TEST-0001 — review')
        close_petty_cash_tasks(_V(), 'approved')
        t.refresh_from_db()
        self.assertEqual(t.status, OmniTask.Status.DONE)

    def test_cancels_on_reject(self):
        from core.models import OmniTask
        from core.notifications import close_petty_cash_tasks

        class _V:
            voucher_number = 'PC-TEST-0002'
        t = self._make_task('Petty cash PC-TEST-0002 — review')
        close_petty_cash_tasks(_V(), 'rejected')
        t.refresh_from_db()
        self.assertEqual(t.status, OmniTask.Status.CANCELLED)


class ClosePaymentBatchTasks(_CloseTaskBase):
    def test_batch_task_done_when_queue_clear(self):
        # A fresh test DB has zero Payment rows → the approval queue is empty →
        # the "clear the queue" summary nudge is done.
        from core.models import OmniTask
        from core.notifications import close_payment_batch_tasks
        t = self._make_task('Approve 3 uploaded payment(s)')
        close_payment_batch_tasks()
        t.refresh_from_db()
        self.assertEqual(t.status, OmniTask.Status.DONE)
        self.assertIsNotNone(t.completed_at)

    def test_batch_task_stays_open_while_payment_pending(self):
        # No premature close: while any payment is still PENDING approval the
        # summary nudge remains valid and must stay open.
        from core.models import OmniTask
        from core.notifications import close_payment_batch_tasks
        t = self._make_task('Approve 3 uploaded payment(s)')
        with patch('payments.models.Payment.objects') as mgr:
            mgr.filter.return_value.exists.return_value = True
            close_payment_batch_tasks()
        t.refresh_from_db()
        self.assertEqual(t.status, OmniTask.Status.PENDING)

    def test_per_payment_close_also_sweeps_batch_task(self):
        # close_payment_approval_tasks (fired on every payment decision) also
        # sweeps the batch summary task once the queue is empty.
        from core.models import OmniTask
        from core.notifications import close_payment_approval_tasks

        class _P:
            payment_number = 'PMT-TEST-0003'
        per_item = self._make_task('Approve payment PMT-TEST-0003')
        batch = self._make_task('Approve 5 uploaded payment(s)')
        close_payment_approval_tasks(_P(), 'approved')
        per_item.refresh_from_db(); batch.refresh_from_db()
        self.assertEqual(per_item.status, OmniTask.Status.DONE)
        self.assertEqual(batch.status, OmniTask.Status.DONE)
