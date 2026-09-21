"""Tests for the incentive-list UPLOAD (CFO / Bharath 2026-08-24).

The upload is a typing shortcut only. These tests pin two things:
  1. It reads a spreadsheet / CSV of people + amounts into the form rows.
  2. It does NOT open a second, weaker way in — an uploaded row still has to
     pass the identical earned-incentive gate at Submit (a row with the
     qualification columns unfilled is rejected; a fully-filled one is accepted).
"""
import io
import tempfile

from django.contrib.auth.models import User
from rest_framework.test import APIClient, APITestCase

from core.deadline_test_utils import submission_window_open
from core.models import Company
from hris.incentive_import import parse_incentive_list
from hris.models import HRISProfile, IncentiveRequest
from payroll.models import Employee

URL = '/hris/api/incentives/'
UPLOAD_URL = '/hris/api/incentives/parse-upload/'

# A ≥50-word justification, so an uploaded row can be fully qualified from the
# sheet and go straight through Submit.
FIFTY = (
    "This work went far beyond the employee's normal day-to-day role and required "
    "substantial extra effort over several evenings and one weekend to design, test "
    "and deliver a new process that was not part of their job description. It was "
    "completed on time, was entirely error-free, and needed no manager rework at all.")


def _xlsx_bytes(rows):
    """A one-sheet .xlsx workbook from a list of row-lists (row 0 = header)."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _write_tmp(suffix, data: bytes):
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp.write(data)
    tmp.close()
    return tmp.name


class IncentiveImportParserTest(APITestCase):
    def test_reads_names_amounts_and_flags_from_xlsx(self):
        data = _xlsx_bytes([
            ['Name', 'Amount', 'Basis', 'Beyond normal duties', 'On time',
             'Error free', 'Needed manager fix', 'Justification'],
            ['Bonang Lentswe', '1200', 'Motor claims', 'Yes', 'Yes', 'Yes', 'No', FIFTY],
            ['Segolame Masilo', '1,500.00', 'Motor claims', 'yes', 'y', 'YES', 'no', FIFTY],
            ['Grand Total', '2700', '', '', '', '', '', ''],   # totals row → skipped
            ['', '', '', '', '', '', '', ''],                  # blank row → skipped
        ])
        rows = parse_incentive_list(_write_tmp('.xlsx', data))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['name'], 'Bonang Lentswe')
        self.assertEqual(rows[0]['amount'], '1200.00')
        self.assertEqual(rows[1]['amount'], '1500.00')       # comma-tolerant
        self.assertTrue(rows[0]['beyond_normal_duties'])
        self.assertTrue(rows[1]['on_time'])                   # 'y' is truthy
        self.assertFalse(rows[0]['needed_manager_fix'])
        self.assertEqual(rows[0]['basis'], 'Motor claims')
        self.assertIn('beyond', rows[0]['justification'])

    def test_missing_flag_columns_default_to_unticked(self):
        # A bare name+amount sheet loads, but leaves the gate answers UNSET so the
        # manager must consciously tick them (the gate is never auto-satisfied).
        data = _xlsx_bytes([['Name', 'Amount'],
                            ['Kagiso M', '800']])
        rows = parse_incentive_list(_write_tmp('.xlsx', data))
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]['beyond_normal_duties'])
        self.assertFalse(rows[0]['on_time'])
        self.assertEqual(rows[0]['justification'], '')

    def test_zero_or_bad_amount_becomes_blank(self):
        data = _xlsx_bytes([['Name', 'Amount'], ['No Amount Person', '0']])
        rows = parse_incentive_list(_write_tmp('.xlsx', data))
        self.assertEqual(rows[0]['amount'], '')

    def test_reads_csv(self):
        csv = ("Name,Amount,Justification\r\n"
               f"Tebogo K,900,\"{FIFTY}\"\r\n").encode('utf-8')
        rows = parse_incentive_list(_write_tmp('.csv', csv))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['name'], 'Tebogo K')
        self.assertEqual(rows[0]['amount'], '900.00')

    def test_no_name_amount_header_raises(self):
        data = _xlsx_bytes([['Random', 'Columns'], ['foo', 'bar']])
        with self.assertRaises(ValueError):
            parse_incentive_list(_write_tmp('.xlsx', data))


@submission_window_open   # the 16th deadline (2026-08-28) blocks post-16th uploads
class IncentiveUploadEndpointTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(code='TEST', name='Test Co.')
        cls.maker = User.objects.create_user(
            'bharath', 'bbalasubramanian@alphadirect.co.bw', 'x')
        maker_emp = Employee.objects.create(
            employee_number='E200', full_name='Bharath B.',
            job_title='Senior Manager', department='Sales',
            email='bbalasubramanian@alphadirect.co.bw',
            company=cls.company, user=cls.maker)
        # hris_role → 'mgr' requires at least one direct report (HRISProfile.manager);
        # that is what makes can_submit True. Mirror test_incentives.py's setup.
        report = Employee.objects.create(
            employee_number='E201', full_name='Report One',
            job_title='Agent', department='Sales', company=cls.company)
        HRISProfile.objects.create(employee=report, manager=maker_emp)
        cls.ess = User.objects.create_user('worker', 'w@example.com', 'x')

    def _client(self, user):
        c = APIClient()
        c.force_authenticate(user)
        return c

    def _upload(self, user, name, data):
        from django.core.files.uploadedfile import SimpleUploadedFile
        return self._client(user).post(
            UPLOAD_URL, {'file': SimpleUploadedFile(name, data)}, format='multipart')

    def test_manager_uploads_and_gets_rows(self):
        data = _xlsx_bytes([['Name', 'Amount'], ['Bonang Lentswe', '1200']])
        r = self._upload(self.maker, 'list.xlsx', data)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['count'], 1)
        self.assertEqual(r.json()['lines'][0]['name'], 'Bonang Lentswe')

    def test_non_manager_cannot_upload(self):
        data = _xlsx_bytes([['Name', 'Amount'], ['X', '1']])
        self.assertEqual(self._upload(self.ess, 'l.xlsx', data).status_code, 403)

    def test_no_file_and_bad_type_rejected(self):
        self.assertEqual(
            self._client(self.maker).post(UPLOAD_URL, {}, format='multipart').status_code, 400)
        self.assertEqual(self._upload(self.maker, 'l.txt', b'nope').status_code, 400)

    def test_unreadable_file_gives_friendly_400(self):
        # A .xlsx that is not really a workbook → friendly message, not a 500.
        r = self._upload(self.maker, 'broken.xlsx', b'this is not a zip')
        self.assertEqual(r.status_code, 400)
        self.assertIn('Could not read', r.json()['detail'])

    # ── the gate still holds on uploaded rows ────────────────────────────────
    def test_uploaded_unqualified_row_is_still_blocked_at_submit(self):
        """Upload a bare name+amount, then submit those exact rows → rejected by
        the earned-incentive gate (proves the upload is not a bypass)."""
        data = _xlsx_bytes([['Name', 'Amount'], ['Bonang Lentswe', '1200']])
        rows = self._upload(self.maker, 'list.xlsx', data).json()['lines']
        r = self._client(self.maker).post(URL, {
            'title': 'Motor claims', 'period': '2026-06',
            'manager_attested': True, 'lines': rows}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(IncentiveRequest.objects.count(), 0)

    def test_uploaded_fully_qualified_row_submits(self):
        """A sheet that carries the qualification columns + a 50-word reason loads
        AND submits — the shortcut works end-to-end without weakening the gate."""
        data = _xlsx_bytes([
            ['Name', 'Amount', 'Beyond normal duties', 'On time', 'Error free',
             'Needed manager fix', 'Justification'],
            ['Bonang Lentswe', '1200', 'Yes', 'Yes', 'Yes', 'No', FIFTY]])
        rows = self._upload(self.maker, 'list.xlsx', data).json()['lines']
        r = self._client(self.maker).post(URL, {
            'title': 'Motor claims', 'period': '2026-06',
            'manager_attested': True, 'lines': rows}, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(IncentiveRequest.objects.count(), 1)
        self.assertEqual(r.json()['lines'][0]['amount'], '1200.00')
