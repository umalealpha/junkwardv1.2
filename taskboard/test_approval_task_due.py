"""A payment-approval task must never be 'overdue' before it was created.

Approval tasks used to inherit the payment's own due date, which is often
historical (a supplier bill dated last week, a petty-cash reimbursement for a
closed period) — so the task landed in the CFO's queue already overdue and the
daily 'N task(s) due or overdue' digest nagged him about work he'd just
received (CFO 2026-09-10 — reconcile the alert email with reality).

Pure logic, no database — safe to run anywhere.
"""
import unittest
from datetime import timedelta

from django.utils import timezone

from taskboard.payment_views import _approval_task_due
from taskboard.services import is_overdue
from core.models import OmniTask


class ApprovalTaskDueFloorTests(unittest.TestCase):
    def test_past_payment_due_is_floored_to_today(self):
        past = timezone.localdate() - timedelta(days=7)
        self.assertEqual(_approval_task_due(past), timezone.localdate())

    def test_future_due_passes_through_unchanged(self):
        future = timezone.localdate() + timedelta(days=3)
        self.assertEqual(_approval_task_due(future), future)

    def test_today_passes_through_unchanged(self):
        today = timezone.localdate()
        self.assertEqual(_approval_task_due(today), today)

    def test_none_stays_none(self):
        self.assertIsNone(_approval_task_due(None))

    def test_task_with_floored_due_is_not_overdue_at_birth(self):
        class _T:  # stand-in carrying only what is_overdue() reads
            status = OmniTask.Status.PENDING
            due_at = _approval_task_due(timezone.localdate() - timedelta(days=7))
            due_time = None
        self.assertFalse(is_overdue(_T()))


if __name__ == "__main__":
    unittest.main()
