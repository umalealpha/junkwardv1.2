"""commissions/test_upload_formats.py — commission upload/export bug 2026-08-21
(bug 02853931).

Two fixes, both proven here:
  1. can_export honours a COMMISSIONS_EXPORT_EMAILS allowlist — a no-deploy way
     to grant the payout/statement DOWNLOAD — WITHOUT granting review or
     final-approval authority (download access is not payout sign-off).
  2. the commission upload accepts .xls and .ods (via python-calamine), not only
     .xlsx/.xlsb — the reporter's sheet was an old-format workbook that used to
     500, then was rejected outright.

Fixtures in testdata/ are a real .xls (BIFF) and .ods (OpenDocument) — formats
openpyxl cannot read — so the .xls/.ods tests fail on the old reader and pass on
the new one.

Run: DB_ENGINE=sqlite SECRET_KEY=x python manage.py test commissions.test_upload_formats
"""
import os
from decimal import Decimal
from pathlib import Path
from unittest import mock

# stage-2 is email-pinned (no name fallback) — give the tests a known address.
os.environ.setdefault('COMMISSIONS_STAGE2_EMAILS', 'pako@x.co')

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from core.deadline_test_utils import submission_window_open

from . import access, importer
from .api_views import CommissionSubmissionViewSet
from .models import CommissionGroup, CommissionSubmission

User = get_user_model()
DATA = Path(__file__).resolve().parent / 'testdata'


class CanExportAllowlistTests(TestCase):
    """Item 1 — the export allowlist grants DOWNLOAD only, to exactly who is named."""

    def setUp(self):
        # a plain accountant — NOT on any review roster, not a superuser.
        self.user = User.objects.create_user('acct', 'acct@x.co', 'pw',
                                              first_name='Plain', last_name='Accountant')

    def test_not_on_allowlist_cannot_export(self):
        self.assertFalse(access.can_export(self.user))

    @mock.patch.dict(os.environ, {'COMMISSIONS_EXPORT_EMAILS': 'acct@x.co, someone@else.co'})
    def test_allowlist_grants_export_only(self):
        self.assertTrue(access.can_export(self.user))                       # download granted
        self.assertFalse(access.is_reviewer(self.user))                     # still NOT a reviewer
        self.assertFalse(access.can_review_stage(self.user, access.FINAL))  # still NOT an approver

    @mock.patch.dict(os.environ, {'COMMISSIONS_EXPORT_EMAILS': 'other@x.co'})
    def test_someone_else_on_list_does_not_grant_me(self):
        self.assertFalse(access.can_export(self.user))


class UploadFormatParseTests(TestCase):
    """Item 2 — the parser reads every flavour the house reader supports."""

    def test_xls_parses(self):
        lines = importer.parse_workbook(str(DATA / 'comm_test.xls'))
        self.assertEqual(len(lines), 2)                                     # totals row skipped
        self.assertEqual(sum(l['commission_amount'] for l in lines), Decimal('250'))
        self.assertEqual(lines[0]['collection_date'].isoformat(), '2026-08-15')

    def test_ods_parses(self):
        lines = importer.parse_workbook(str(DATA / 'comm_test.ods'))
        self.assertEqual(len(lines), 2)

    def test_xlsx_still_parses(self):   # regression — the working path is unchanged
        lines = importer.parse_workbook(str(DATA / 'comm_test.xlsx'))
        self.assertEqual(len(lines), 2)

    def test_inhouse_summary_reads_across_formats(self):
        # parse_inhouse_summary now reuses _iter_sheets — cover both a native
        # .xlsx and an .xls so the shared-reader refactor can't silently regress.
        for fixture in ('comm_inhouse.xlsx', 'comm_inhouse.xls'):
            pairs = importer.parse_inhouse_summary(str(DATA / fixture), '2026-08')
            names = {n for n, _ in pairs}
            self.assertEqual(names, {'Agent One', 'Agent Two'}, fixture)   # totals row skipped
            self.assertEqual(sum(g for _, g in pairs), Decimal('800'), fixture)


@submission_window_open   # 16th submission deadline (2026-08-28)
class UploadEndpointFormatTests(TestCase):
    """Item 2 end-to-end — a reviewer's .xls/.ods upload is accepted (not 400)."""

    def setUp(self):
        CommissionGroup.objects.get_or_create(
            key='independent', defaults={'name': 'Independent agents'})

    def _upload(self, filename):
        # A reviewer may upload for any agent. Grant reviewer status by the email
        # roster (COMMISSIONS_STAGE1_EMAILS) so the test needs no real staff name.
        raw = (DATA / filename).read_bytes()
        f = SimpleUploadedFile(filename, raw)
        req = APIRequestFactory().post(
            '/x', {'file': f, 'period': '2026-08', 'agent': 'Fixture Agent',
                   'group': 'independent'}, format='multipart')
        reviewer = User.objects.create_user('rv', 'rv@x.co', 'pw')
        force_authenticate(req, user=reviewer)
        return CommissionSubmissionViewSet.as_view({'post': 'upload'})(req)

    @mock.patch.dict(os.environ, {'COMMISSIONS_STAGE1_EMAILS': 'rv@x.co'})
    def test_xls_upload_accepted(self):
        resp = self._upload('comm_test.xls')
        self.assertEqual(resp.status_code, 200, getattr(resp, 'data', None))
        self.assertTrue(CommissionSubmission.objects.filter(period_label='2026-08').exists())

    @mock.patch.dict(os.environ, {'COMMISSIONS_STAGE1_EMAILS': 'rv@x.co'})
    def test_ods_upload_accepted(self):
        resp = self._upload('comm_test.ods')
        self.assertEqual(resp.status_code, 200, getattr(resp, 'data', None))
        self.assertTrue(CommissionSubmission.objects.filter(period_label='2026-08').exists())
