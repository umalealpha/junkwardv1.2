"""TASK-ATT-01 — remove a task attachment (bug 83594e5d, D. Ikgopoleng 2026-09-03).

Reported: "Users currently have no way to remove an attachment once uploaded to
a task. When a wrong or sensitive document is attached in error, there is no
self-service correction option, requiring manual IT intervention."

So DELETE on the same URL the reader already uses
(/tasks/<task_id>/comments/<comment_id>/file/) removes the file:
  * the uploader (comment author), the task assigner, or a superuser may remove;
  * the assignee who did NOT upload it may not (they must not be able to destroy
    evidence that was handed TO them);
  * the bytes leave storage — not just the DB pointer (the whole point is a
    confidential document stops being readable);
  * the activity feed keeps an append-only line saying who removed what and when,
    plus an AuditLog row.
"""
import os

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from core.models import AuditLog, OmniTask, OmniTaskComment


class TaskAttachmentRemoveTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.assigner = User.objects.create_user('att-assigner', password='x')
        cls.assignee = User.objects.create_user('att-assignee', password='x')
        cls.stranger = User.objects.create_user('att-stranger', password='x')
        cls.admin = User.objects.create_superuser('att-admin', 'a@b.co', 'x')

    def setUp(self):
        self.task = OmniTask.objects.create(
            assigner=self.assigner, assignee=self.assignee,
            title='Please review the attached')
        self.comment = OmniTaskComment.objects.create(
            task=self.task, author=self.assigner, body='See attached',
            evidence=SimpleUploadedFile('wrong-omang.pdf', b'confidential-bytes',
                                        content_type='application/pdf'))

    def _url(self, comment=None):
        c = comment or self.comment
        return f'/api/v1/tasks/{self.task.id}/comments/{c.id}/file/'

    # ── who may remove ──────────────────────────────────────────────────────
    def test_uploader_can_remove(self):
        self.client.force_login(self.assigner)
        r = self.client.delete(self._url())
        self.assertEqual(r.status_code, 200, r.content)
        self.comment.refresh_from_db()
        self.assertFalse(self.comment.evidence)

    def test_superuser_can_remove(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.delete(self._url()).status_code, 200)

    def test_task_assignee_who_did_not_upload_cannot_remove(self):
        # Evidence handed TO the assignee must not be destroyable by them.
        self.client.force_login(self.assignee)
        r = self.client.delete(self._url())
        self.assertEqual(r.status_code, 403)
        self.comment.refresh_from_db()
        self.assertTrue(self.comment.evidence)

    def test_assignee_can_remove_their_own_upload(self):
        own = OmniTaskComment.objects.create(
            task=self.task, author=self.assignee, body='my reply',
            evidence=SimpleUploadedFile('mine.pdf', b'x', content_type='application/pdf'))
        self.client.force_login(self.assignee)
        self.assertEqual(self.client.delete(self._url(own)).status_code, 200)

    def test_stranger_cannot_remove(self):
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.delete(self._url()).status_code, 403)

    def test_anonymous_cannot_remove(self):
        self.assertIn(self.client.delete(self._url()).status_code, (401, 403))

    # ── what removal actually does ──────────────────────────────────────────
    def test_bytes_leave_storage(self):
        path = self.comment.evidence.path
        self.assertTrue(os.path.exists(path))
        self.client.force_login(self.assigner)
        self.client.delete(self._url())
        self.assertFalse(os.path.exists(path),
                         'the file must be deleted from storage, not just unlinked in the DB')

    def test_download_is_404_after_removal(self):
        self.client.force_login(self.assigner)
        self.client.delete(self._url())
        self.assertEqual(self.client.get(self._url()).status_code, 404)

    def test_audit_line_added_to_activity(self):
        # Storage may suffix the name to avoid a collision (wrong-omang_A1b2.pdf),
        # so match on the stem, not the exact upload name.
        self.client.force_login(self.assigner)
        self.client.delete(self._url())
        bodies = list(OmniTaskComment.objects.filter(task=self.task)
                      .values_list('body', flat=True))
        self.assertTrue(any('wrong-omang' in b and 'removed' in b.lower()
                            for b in bodies), bodies)

    def test_audit_log_row_written(self):
        self.client.force_login(self.assigner)
        self.client.delete(self._url())
        self.assertTrue(
            AuditLog.objects.filter(table_name='core.OmniTaskComment',
                                    record_id=str(self.comment.id),
                                    action=AuditLog.Action.DELETE).exists())

    def test_removing_twice_is_404(self):
        self.client.force_login(self.assigner)
        self.assertEqual(self.client.delete(self._url()).status_code, 200)
        self.assertEqual(self.client.delete(self._url()).status_code, 404)

    # ── the UI needs to know whether to show the button ─────────────────────
    def test_detail_flags_can_remove_evidence(self):
        self.client.force_login(self.assigner)
        c = self.client.get(f'/api/v1/tasks/{self.task.id}/').json()['comments'][0]
        self.assertTrue(c['can_remove_evidence'])

        self.client.force_login(self.assignee)
        c = self.client.get(f'/api/v1/tasks/{self.task.id}/').json()['comments'][0]
        self.assertFalse(c['can_remove_evidence'])
