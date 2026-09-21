"""payroll/test_batch_apply_handoff.py — applying a parsed amendment batch.

CFO 2026-07-23: the staging-step SoD (uploader cannot apply) was moved
DOWNSTREAM (FC Pako Kago + FM agreed; CFO signed off) — applying only stages
DRAFT payslips, and the real control is at payslip/period approval + GL-posting
authority + the CFO's FNB payment authorisation. So:
  - ANY finance approver (FC/FM) may apply, INCLUDING the uploader;
  - apply stays finance-only (a non-approver still cannot apply);
  - both uploader AND applier are recorded (applied_by) for the audit trail.
The list endpoint (GET /api/v1/payroll/amendment-batches/) surfaces parsed
batches so any FC/FM can pick one up.

Run in CI (needs a DB): manage.py test payroll.test_batch_apply_handoff
"""
from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework.test import APITestCase

from core.models import UserProfile
from payroll.models import PayrollPeriod, PayrollAmendmentBatch


class BatchApplyHandoffTests(APITestCase):
    def setUp(self):
        self.fc = User.objects.create_user('pkago', email='pkago@alphadirect.co.bw')
        UserProfile.objects.create(user=self.fc, role=UserProfile.Role.ACCOUNTANT,
                                   title=UserProfile.Title.FINANCIAL_CONTROLLER, is_active=True)
        self.fm = User.objects.create_user('lntabeni', email='lntabeni@alphadirect.co.bw')
        UserProfile.objects.create(user=self.fm, role=UserProfile.Role.ACCOUNTANT,
                                   title=UserProfile.Title.FINANCE_MANAGER, is_active=True)
        self.hr = User.objects.create_user('ubutale', email='ubutale@alphadirect.co.bw')
        UserProfile.objects.create(user=self.hr, role=UserProfile.Role.OPERATIONS_STAFF,
                                   title='hr_manager', is_active=True)
        self.target = PayrollPeriod.objects.create(period_name='2026-06',
                                                   start_date='2026-06-01', end_date='2026-06-30')
        self.base = PayrollPeriod.objects.create(period_name='2026-05',
                                                 start_date='2026-05-01', end_date='2026-05-31')
        self.batch = PayrollAmendmentBatch.objects.create(
            target_period=self.target, baseline_period=self.base,
            uploaded_by=self.fc, row_count=3, status=PayrollAmendmentBatch.Status.PARSED)
        self.url = reverse('v1-payroll-amendment-batches')

    def test_uploader_can_now_apply(self):
        # Staging SoD moved downstream — the uploader may apply their own batch.
        self.client.force_authenticate(self.fc)
        rows = self.client.get(self.url).json()['batches']
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['uploaded_by_me'])
        self.assertTrue(rows[0]['can_apply'])

    def test_other_finance_approver_can_apply(self):
        self.client.force_authenticate(self.fm)
        data = self.client.get(self.url).json()
        self.assertTrue(data['is_finance_approver'])
        self.assertEqual(len(data['batches']), 1)
        self.assertTrue(data['batches'][0]['can_apply'])

    def test_viewer_without_finance_title_cannot_apply(self):
        self.client.force_authenticate(self.hr)            # can view payroll, not a finance approver
        data = self.client.get(self.url).json()
        self.assertFalse(data['is_finance_approver'])
        self.assertFalse(data['batches'][0]['can_apply'])

    def test_applied_batches_are_not_listed(self):
        self.batch.status = PayrollAmendmentBatch.Status.APPLIED
        self.batch.save(update_fields=['status'])
        self.client.force_authenticate(self.fm)
        self.assertEqual(self.client.get(self.url).json()['batches'], [])

    # ── the apply endpoint itself ───────────────────────────────────────────
    def _apply_url(self):
        return reverse('v1-payroll-amendments-apply', args=[self.batch.id])

    def test_uploader_can_apply_own_batch_and_is_recorded(self):
        # The SoD block is gone: the uploader (FC) applies their own batch.
        # (baseline has no payslips → a clean 0-row apply, still 200.)
        self.client.force_authenticate(self.fc)
        r = self.client.post(self._apply_url())
        self.assertEqual(r.status_code, 200, r.content)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.status, PayrollAmendmentBatch.Status.APPLIED)
        self.assertEqual(self.batch.applied_by_id, self.fc.id)   # audit: who applied
        self.assertEqual(self.batch.uploaded_by_id, self.fc.id)  # audit: who uploaded

    def test_non_approver_still_cannot_apply(self):
        # An HR manager (not FC/FM, not named) still cannot apply.
        self.client.force_authenticate(self.hr)
        r = self.client.post(self._apply_url())
        self.assertEqual(r.status_code, 403)

    def test_named_applier_can_apply(self):
        # A CFO-named applier (Tshephang, 'operations' — no finance title) may
        # apply (CFO 2026-07-23). Matched on email local-part 'tshephang'.
        tsh = User.objects.create_user('tshephang.motswagae',
                                       email='tshephang@motorliquidators.co.bw')
        UserProfile.objects.create(user=tsh, role=UserProfile.Role.OPERATIONS_STAFF,
                                   title='operations', is_active=True)
        self.client.force_authenticate(tsh)
        data = self.client.get(self.url).json()
        self.assertTrue(data['is_finance_approver'])
        self.assertTrue(data['batches'][0]['can_apply'])
        r = self.client.post(self._apply_url())
        self.assertEqual(r.status_code, 200, r.content)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.applied_by_id, tsh.id)

    def test_apply_refuses_batch_with_unresolved_rows(self):
        """Manus QC round 1 (2026-08-29): the pre-flight 'not ready' must be a
        HARD server gate, not a warning. A batch with an unresolved employee row
        (resolution_error set) must be REFUSED (409), stay PARSED, and touch
        ZERO payslips — NOT skip the row, stage the rest as DRAFT and report
        APPLIED/200 (the proven bypass)."""
        from payroll.models import PayrollAmendment, Payslip
        PayrollAmendment.objects.create(
            batch=self.batch, kind=PayrollAmendment.Kind.BONUS,
            employee=None, employee_ref='Ghost Person', amount='1000.00',
            resolution_error='employee "Ghost Person" not found',
        )
        self.client.force_authenticate(self.fm)
        r = self.client.post(self._apply_url())
        self.assertEqual(r.status_code, 409, r.content)
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.status, PayrollAmendmentBatch.Status.PARSED)
        self.assertIsNone(self.batch.applied_at)
        self.assertEqual(Payslip.objects.filter(period=self.target).count(), 0)
