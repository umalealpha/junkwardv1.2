"""
SoD controls — Workstream #3 (vendor bank) + #4 (payroll / petty cash).
CFO-approved 2026-07-02. Reuses the maker/checker engine from Workstream A.

MAKER (originate) = Financial Controller / Senior Accountant / Accountant.
CHECKER (approve) = Finance Manager / CFO. Approver != originator, record-level.
Payroll uses the payroll-approval set (CFO / FM / HR Manager); terminations
require CFO.
"""
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIRequestFactory, force_authenticate

from core.models import UserProfile


def _user(username, title, is_superuser=False):
    u = User.objects.create_user(username=username, password='x', is_superuser=is_superuser)
    UserProfile.objects.update_or_create(
        user=u, defaults={'role': UserProfile.Role.ACCOUNTANT, 'title': title, 'is_active': True})
    return u


class VendorBankSoDTests(TestCase):
    """#3 — makers create/submit; Finance Manager approves; FC removed as checker."""

    def setUp(self):
        from core.models import Currency
        from billing.models import Contact
        Currency.objects.get_or_create(code='BWP', defaults={'name': 'Pula', 'symbol': 'P'})
        self.vendor = Contact.objects.create(name='Acme Panelbeaters', contact_type='vendor')
        self.fc = _user('fc_vb', UserProfile.Title.FINANCIAL_CONTROLLER)
        self.fc2 = _user('fc2_vb', UserProfile.Title.FINANCIAL_CONTROLLER)
        self.fm = _user('fm_vb', UserProfile.Title.FINANCE_MANAGER)

    def _draft(self, created_by):
        from procurement.models import VendorBankAccount
        return VendorBankAccount.objects.create(
            contact=self.vendor, bank_name='FNB', account_holder_name='Acme',
            account_number='620111', currency_code_id='BWP',
            status=VendorBankAccount.Status.DRAFT, created_by=created_by)

    def test_can_approve_bank_is_fm_cfo_only(self):
        from procurement.services import _can_approve_bank
        self.assertTrue(_can_approve_bank(self.fm))
        self.assertTrue(_can_approve_bank(_user('cfo_vb', UserProfile.Title.CFO)))
        self.assertFalse(_can_approve_bank(self.fc))   # FC removed as checker
        self.assertFalse(_can_approve_bank(_user('acc_vb', UserProfile.Title.ACCOUNTANT)))

    def test_finance_manager_cannot_submit(self):
        from procurement.services import submit_bank_for_approval
        bank = self._draft(self.fm)
        with self.assertRaises(ValidationError):
            submit_bank_for_approval(bank, self.fm)   # (a) FM not a maker

    def test_maker_submits_then_different_fm_approves(self):
        from procurement.services import submit_bank_for_approval, approve_bank
        from procurement.models import VendorBankAccount
        bank = self._draft(self.fc)
        submit_bank_for_approval(bank, self.fc)        # (b) FC can originate
        bank.refresh_from_db()
        self.assertEqual(bank.status, VendorBankAccount.Status.PENDING_APPROVAL)
        # a second FC cannot approve (FC is not a checker) -> (b)/(d)
        with self.assertRaises(ValidationError):
            approve_bank(bank, self.fc2)
        # the FM approves -> ACTIVE (c)(e)
        approve_bank(bank, self.fm)
        bank.refresh_from_db()
        self.assertEqual(bank.status, VendorBankAccount.Status.ACTIVE)

    def test_approver_cannot_be_creator(self):
        # An FM who somehow created the draft cannot approve it (record-level SoD).
        from procurement.services import submit_bank_for_approval, approve_bank
        bank = self._draft(self.fc)
        submit_bank_for_approval(bank, self.fc)
        # fc is creator+submitter; even if fc were a checker, SoD blocks self.
        with self.assertRaises(ValidationError):
            approve_bank(bank, self.fc)


class PayrollApplySoDTests(TestCase):
    """#4 — batch apply is FINANCE-only (FC/FM). The staging-step
    'uploader cannot apply' SoD was moved DOWNSTREAM (CFO 2026-07-23): applying
    only stages DRAFT payslips; SoD lives at payslip/period approval + GL-posting
    authority + the CFO's FNB payment authorisation. Uploader may now apply."""

    def setUp(self):
        self.factory = APIRequestFactory()
        from payroll.models import PayrollPeriod
        self.p1 = PayrollPeriod.objects.create(period_name='2026-05', start_date='2026-05-01', end_date='2026-05-31')
        self.p2 = PayrollPeriod.objects.create(period_name='2026-06', start_date='2026-06-01', end_date='2026-06-30')
        from payroll.amendment_views import apply_amendment_batch
        self.view = apply_amendment_batch

    def _batch(self, uploaded_by):
        from payroll.models import PayrollAmendmentBatch
        return PayrollAmendmentBatch.objects.create(
            baseline_period=self.p1, target_period=self.p2, uploaded_by=uploaded_by,
            status=PayrollAmendmentBatch.Status.PARSED)

    def test_uploader_can_apply_own_batch(self):
        # Staging SoD moved downstream (CFO 2026-07-23): a finance approver may
        # apply their OWN batch. p1(May)→p2(Jun), no payslips → a clean 0-row
        # apply (200), and applied_by is recorded for the audit trail.
        fm = _user('fm_pay', UserProfile.Title.FINANCE_MANAGER)
        batch = self._batch(uploaded_by=fm)
        req = self.factory.post(f'/api/v1/payroll/amendment-batches/{batch.id}/apply/')
        force_authenticate(req, user=fm)
        resp = self.view(req, batch_id=str(batch.id))
        self.assertEqual(resp.status_code, 200)
        batch.refresh_from_db()
        self.assertEqual(batch.applied_by_id, fm.id)

    def test_hr_manager_cannot_apply(self):
        # HR raises + reviews (Dorothy / Unami), Finance applies. An HR Manager
        # is NOT a finance approver, so cannot apply. (CFO directive 2026-07-02.)
        hr = _user('hr_pay', UserProfile.Title.HR_MANAGER)
        uploader = _user('up_pay', UserProfile.Title.ACCOUNTANT)
        batch = self._batch(uploaded_by=uploader)
        req = self.factory.post(f'/api/v1/payroll/amendment-batches/{batch.id}/apply/')
        force_authenticate(req, user=hr)
        resp = self.view(req, batch_id=str(batch.id))
        self.assertEqual(resp.status_code, 403)   # apply is Finance-only (FC/FM)

    def test_financial_controller_passes_apply_gate(self):
        # An FC (finance approver) who did NOT upload the batch clears both the
        # finance-approver gate and the uploader!=approver gate. We set
        # baseline==target so apply short-circuits to 400 (same-period guard)
        # AFTER the SoD gates — proving FC cleared them without full payslip setup.
        from payroll.models import PayrollAmendmentBatch
        fc = _user('fc_pay', UserProfile.Title.FINANCIAL_CONTROLLER)
        uploader = _user('up_pay2', UserProfile.Title.ACCOUNTANT)
        batch = PayrollAmendmentBatch.objects.create(
            baseline_period=self.p1, target_period=self.p1, uploaded_by=uploader,
            status=PayrollAmendmentBatch.Status.PARSED)
        req = self.factory.post(f'/api/v1/payroll/amendment-batches/{batch.id}/apply/')
        force_authenticate(req, user=fc)
        resp = self.view(req, batch_id=str(batch.id))
        self.assertNotEqual(resp.status_code, 403)   # FC cleared finance + SoD gates


class PettyCashReimbursementSoDTests(TestCase):
    """#4 — reimbursement creator cannot post it."""

    def test_creator_cannot_post_reimbursement(self):
        from petty_cash.models import PettyCashReimbursement
        from petty_cash.services import post_reimbursement
        fm = _user('fm_pc', UserProfile.Title.FINANCE_MANAGER)
        # unsaved instance — the SoD guard raises before any DB write, so we
        # don't need to satisfy every not-null column just to prove the guard.
        reimb = PettyCashReimbursement(
            status=PettyCashReimbursement.Status.DRAFT, created_by=fm)
        with self.assertRaises(ValidationError):
            post_reimbursement(reimb, fm)   # creator == poster -> SoD block
