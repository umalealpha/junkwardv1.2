"""Regression: AFT medical-claims payment runs must not parse to zero claims.

Bug (Tlamelo Chimidza, 2026-07-02): uploading ADI_AFT_PmtRun_*.xlsx via the
Claims Smart-Upload showed "zero claims for that week" for some weeks. Cause:
the endpoint used the generic read_only `_parse_xlsx`, whose largest-sheet
heuristic misfires on AFT files (they omit the sheet <dimension> tag, so every
sheet sizes as 1x1) and never lands on the "Claim Lines" sheet. Fix: AFT files
route to the dedicated non-read_only `import_aft_run`.

Synthetic fixtures only — fake member numbers / amounts, never real PII.
"""

import io

from django.contrib.auth.models import User
from django.test import SimpleTestCase
from rest_framework.test import APITestCase, APIClient

import openpyxl

from healthcare.aft_import import parse_aft_run
from healthcare.upload_views import _is_aft_run


def _make_aft_blob(n_lines=3, sheet_name='Claim Lines', with_title_row=True):
    """Build an AFT-shaped xlsx in memory. Fake data only."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_name
    if with_title_row:
        ws.append(['Claim Lines'])  # single-cell title row (as AFA exports)
    headers = ['Remit Date', 'Member Number', 'Product', 'Practice Name',
               'Claim Number', 'Charged Amount', 'Paid Amount']
    ws.append(headers)
    for i in range(n_lines):
        ws.append(['2026-06-01', f'M{1000+i}', 'Hospital Plan', 'Test Practice',
                   f'CLM{i}', 100 + i, 90 + i])
    # summary tabs the parser also reads
    for extra in ('Product Totals', 'Practice Totals'):
        w2 = wb.create_sheet(extra)
        w2.append(['Product', 'Total Amount'])
        w2.append(['Hospital Plan', 500])
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


class AftDetectionTests(SimpleTestCase):
    def test_detects_by_filename(self):
        blob = _make_aft_blob()
        self.assertTrue(_is_aft_run(blob, 'ADI_AFT_PmtRun_20260627.xlsx'))
        self.assertTrue(_is_aft_run(blob, 'aft pmtrun weird name.xlsx'))

    def test_detects_by_sheet_when_name_unhelpful(self):
        blob = _make_aft_blob()
        self.assertTrue(_is_aft_run(blob, 'random_upload.xlsx'))

    def test_non_aft_not_detected(self):
        wb = openpyxl.Workbook()
        wb.active.append(['Bordereaux Month', 'GWP'])
        bio = io.BytesIO(); wb.save(bio)
        self.assertFalse(_is_aft_run(bio.getvalue(), 'HEALTH GWP March 2026.xlsx'))


class AftParseTests(SimpleTestCase):
    def test_counts_claim_lines(self):
        parsed = parse_aft_run(_make_aft_blob(n_lines=5))
        self.assertEqual(parsed['line_count'], 5)
        self.assertGreater(float(parsed['total_paid']), 0)
        self.assertGreater(float(parsed['total_charged']), 0)

    def test_single_line_is_not_zero(self):
        parsed = parse_aft_run(_make_aft_blob(n_lines=1))
        self.assertEqual(parsed['line_count'], 1)
        self.assertGreater(float(parsed['total_paid']), 0)


class AftUploadRoutingTests(APITestCase):
    """The Claims Smart-Upload endpoint must route AFT files through the correct
    parser and return non-zero claim counts (the reported bug)."""

    URL = '/api/v1/health/upload/'

    def setUp(self):
        self.user = User.objects.create_superuser('aft_tester', 'aft@x.co', 'pw12345!')
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _upload(self, blob, name):
        from django.core.files.uploadedfile import SimpleUploadedFile
        f = SimpleUploadedFile(
            name, blob,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        return self.client.post(self.URL, {'kind': 'claims', 'file': f}, format='multipart')

    def test_aft_upload_reports_nonzero_claims(self):
        resp = self._upload(_make_aft_blob(n_lines=7), 'ADI_AFT_PmtRun_20260627.xlsx')
        self.assertIn(resp.status_code, (200, 201), resp.data)
        self.assertEqual(resp.data['total_rows'], 7)  # was 0 before the fix
        self.assertGreater(float(resp.data['paid_amount']), 0)

    def test_aft_resend_supersedes_not_duplicates(self):
        first = self._upload(_make_aft_blob(n_lines=3), 'ADI_AFT_PmtRun_20260627.xlsx')
        self.assertEqual(first.data['total_rows'], 3)
        # identical bytes → idempotent (duplicate skip)
        again = self._upload(_make_aft_blob(n_lines=3), 'ADI_AFT_PmtRun_20260627.xlsx')
        self.assertIn(again.data['status'], ('duplicate', 'imported'))
