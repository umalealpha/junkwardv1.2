"""Tests for the bank-details bulk importer (CFO 2026-07-15).

Covers the rules that must never regress: branch-code bank derivation, the
Excel-mangled-account guard, and the maker-checker approval gate (uploader
cannot approve their own; only HR Manager / CFO may approve; commit writes the
employee bank fields).
"""
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import UserProfile
from payroll.bank_codes import resolve_bank
from payroll.models import BankDetailImportBatch, Employee

UPLOAD = '/api/v1/payroll/bank-imports/upload/'


def _approve_url(pk):
    return f'/api/v1/payroll/bank-imports/{pk}/approve/'


class BankCodeDerivationTests(TestCase):
    def test_fnb_branch_and_account_agree(self):
        r = resolve_bank('0062285562106', '283767')
        self.assertEqual(r['bank_name'], 'First National Bank Botswana')
        self.assertEqual(r['source'], 'branch')
        self.assertEqual(r['warning'], '')

    def test_stanbic_branch_with_mangled_account_warns(self):
        r = resolve_bank('9.06E+12', '60167')   # dropped-zero 060167 -> Stanbic
        self.assertEqual(r['bank_name'], 'Stanbic Bank Botswana')
        self.assertIn('corrupted', r['warning'].lower())

    def test_absa_and_bank_gaborone(self):
        self.assertEqual(resolve_bank('0000001113650', '290467')['bank_name'], 'Absa Bank Botswana')
        self.assertEqual(resolve_bank('0008001479464', '202167')['bank_name'], 'Bank Gaborone')

    def test_account_digit_fallback_when_branch_unknown(self):
        r = resolve_bank('0062285562106', '999999')
        self.assertEqual(r['bank_name'], 'First National Bank Botswana')
        self.assertEqual(r['source'], 'account')

    def test_unknown_returns_blank_never_guesses(self):
        self.assertEqual(resolve_bank('0001234567', '777777')['bank_name'], '')


class BankImportFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Uploader: HRIS access via is_administrator, but NOT an approver.
        cls.uploader = User.objects.create_user('uploader', email='uploader@alphadirect.co.bw')
        UserProfile.objects.create(user=cls.uploader, role=UserProfile.Role.FINANCE_ADMIN,
                                   title=UserProfile.Title.ACCOUNTANT, is_administrator=True)
        # Dorothy: HR Manager + whitelisted local-part -> may approve.
        cls.dorothy = User.objects.create_user('dikgopoleng', email='dikgopoleng@alphadirect.co.bw')
        UserProfile.objects.create(user=cls.dorothy, role=UserProfile.Role.OPERATIONS_STAFF,
                                   title=UserProfile.Title.HR_MANAGER, is_administrator=True)
        # A plain user with no HRIS access / no approve rights.
        cls.outsider = User.objects.create_user('outsider', email='outsider@alphadirect.co.bw')

        cls.jane = Employee.objects.create(employee_number='E-J1', full_name='Jane Tester')
        cls.stan = Employee.objects.create(employee_number='E-S1', full_name='Stan Mangled')

    def _upload(self, user):
        csv = (
            'Recipient Name,Recipient Account Number,Branch Code,Amount\n'
            'Jane Tester,0062285562106,283767,5000\n'         # FNB, clean -> ready
            'Stan Mangled,9.06E+12,60167,4000\n'              # Stanbic acct mangled -> account_check
            'Ghost Person,0062000000000,283767,3000\n'        # no employee -> no_match
        ).encode()
        f = SimpleUploadedFile('salary.csv', csv, content_type='text/csv')
        c = APIClient(); c.force_authenticate(user)
        return c.post(UPLOAD, {'file': f}, format='multipart')

    def test_upload_previews_without_committing(self):
        resp = self._upload(self.uploader)
        self.assertEqual(resp.status_code, 201, resp.content)
        data = resp.json()
        self.assertEqual(data['rows_total'], 3)
        self.assertEqual(data['rows_matched'], 2)          # Jane + Stan
        self.assertEqual(data['rows_committable'], 1)      # only Jane (Stan mangled)
        statuses = {r['name']: r['status'] for r in data['rows']}
        self.assertEqual(statuses['Jane Tester'], 'ready')
        self.assertEqual(statuses['Stan Mangled'], 'account_check')
        self.assertEqual(statuses['Ghost Person'], 'no_match')
        # NOTHING committed yet.
        self.jane.refresh_from_db()
        self.assertEqual(self.jane.bank_account_no, '')

    def test_outsider_cannot_upload(self):
        self.assertEqual(self._upload(self.outsider).status_code, 403)

    def test_uploader_cannot_approve_own_batch(self):
        bid = self._upload(self.uploader).json()['id']
        c = APIClient(); c.force_authenticate(self.uploader)
        self.assertEqual(c.post(_approve_url(bid)).status_code, 403)

    def test_outsider_cannot_approve(self):
        bid = self._upload(self.uploader).json()['id']
        c = APIClient(); c.force_authenticate(self.outsider)
        self.assertEqual(c.post(_approve_url(bid)).status_code, 403)

    def test_dorothy_approves_and_commits_only_ready_rows(self):
        bid = self._upload(self.uploader).json()['id']
        c = APIClient(); c.force_authenticate(self.dorothy)
        resp = c.post(_approve_url(bid))
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()['rows_committed'], 1)

        self.jane.refresh_from_db(); self.stan.refresh_from_db()
        # Jane committed with derived bank name.
        self.assertEqual(self.jane.bank_account_no, '0062285562106')
        self.assertEqual(self.jane.bank_branch_code, '283767')
        self.assertEqual(self.jane.bank_name, 'First National Bank Botswana')
        # Stan's mangled account was NOT committed.
        self.assertEqual(self.stan.bank_account_no, '')

        BankDetailImportBatch.objects.get(pk=bid)  # exists
        self.assertEqual(BankDetailImportBatch.objects.get(pk=bid).status, 'approved')

    def test_double_approve_blocked(self):
        bid = self._upload(self.uploader).json()['id']
        c = APIClient(); c.force_authenticate(self.dorothy)
        self.assertEqual(c.post(_approve_url(bid)).status_code, 200)
        self.assertEqual(c.post(_approve_url(bid)).status_code, 400)   # already approved
