"""
hris/test_batch2_fixes.py — CFO batch 2026-07-22:
leave-encashment >=50-word motivation + JE approval-task auto-close.

Run: python manage.py test hris.test_batch2_fixes
"""
from __future__ import annotations

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase


class EncashmentReasonWordGate(SimpleTestCase):
    def test_short_reason_rejected(self):
        from hris.leave_encash_service import _validate_reason
        with self.assertRaises(ValidationError):
            _validate_reason('School fees please.')

    def test_fifty_words_accepted(self):
        from hris.leave_encash_service import _validate_reason
        reason = ' '.join(['word'] * 50)
        self.assertEqual(_validate_reason(reason), reason)

    def test_returns_stripped(self):
        from hris.leave_encash_service import _validate_reason
        reason = '  ' + ' '.join(['motivate'] * 55) + '  '
        self.assertEqual(_validate_reason(reason), reason.strip())


class CloseJeApprovalTasks(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        self.user = get_user_model().objects.create_user('jeapprover', 'jeapprover@alphadirect.co.bw', 'pw')

    def _make_task(self, title):
        from core.models import OmniTask
        return OmniTask.objects.create(title=title, body='', status=OmniTask.Status.PENDING,
                                       priority=OmniTask.Priority.HIGH,
                                       assignee=self.user, assigner=self.user)

    def test_close_marks_done_on_approve(self):
        from core.models import OmniTask
        from core.notifications import close_je_approval_tasks

        class _JE:
            entry_number = 'JE-TEST-0001'
        t = self._make_task('Approve JE JE-TEST-0001')
        rp = self._make_task('Approve JE JE-TEST-0001 [RELATED PARTY]')
        close_je_approval_tasks(_JE(), 'approved')
        t.refresh_from_db(); rp.refresh_from_db()
        self.assertEqual(t.status, OmniTask.Status.DONE)
        self.assertEqual(rp.status, OmniTask.Status.DONE)

    def test_close_cancels_on_reject(self):
        from core.models import OmniTask
        from core.notifications import close_je_approval_tasks

        class _JE:
            entry_number = 'JE-TEST-0002'
        t = self._make_task('Approve JE JE-TEST-0002')
        close_je_approval_tasks(_JE(), 'rejected')
        t.refresh_from_db()
        self.assertEqual(t.status, OmniTask.Status.CANCELLED)
