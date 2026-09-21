"""payroll/test_addition.py — "Add Employee to Payroll" with Finance sign-off.

Feature: Pako Kago (Financial Controller) 2026-08-12. Maker-checker with
segregation of duties; NOTHING hits headcount / period totals / payslips until
a Finance signer approves; a re-added existing name LINKS to that employee
instead of minting a duplicate blank-shell row (the July-2026 bug).

Run: manage.py test payroll.test_addition
"""
import os
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from rest_framework.test import APIClient

from core.approvals_views import pending_approvals_for
from core.models import Company
from payroll import addition_service as svc
from payroll.addition_models import PayrollAdditionRequest
from payroll.models import (Employee, PayrollPeriod, Payslip, PayslipComponent,
                            TaxBracket)
from payroll.signoff_models import PayrollSignOff


def _seed_components():
    for code, name in [('BASIC', 'Basic Salary'), ('COMMISSION', 'Commission'),
                       ('INCENTIVE', 'Incentive'), ('HOUSING_ALLOWANCE', 'Housing Allowance')]:
        PayslipComponent.objects.get_or_create(
            code=code, defaults=dict(name=name, kind=PayslipComponent.Kind.EARNING))
    PayslipComponent.objects.get_or_create(
        code='PAYE', defaults=dict(name='PAYE', kind=PayslipComponent.Kind.TAX))


def _seed_brackets():
    # Botswana resident bands: 0/5/12.5/18.75/25% at 48k/84k/120k/156k.
    rows = [(0, 48000, 0, '0'), (48000, 84000, 0, '5'), (84000, 120000, 1800, '12.5'),
            (120000, 156000, 6300, '18.75'), (156000, None, 13050, '25')]
    for lo, hi, base, rate in rows:
        TaxBracket.objects.create(
            name='Resident 2026', effective_from='2026-07-01',
            lower_bound=lo, upper_bound=hi, base_amount=base,
            rate_pct=Decimal(rate), is_active=True)


class AdditionServiceTests(TestCase):
    def setUp(self):
        self.co = Company.objects.create(code='ADIC', name='Alpha Direct')
        self.period = PayrollPeriod.objects.create(
            period_name='2026-07', start_date='2026-07-01', end_date='2026-07-31')
        _seed_components()
        _seed_brackets()
        # Finance approvers via a synthetic env allowlist — no real staff emails
        # in tests, so nothing personal leaves to the review panel.
        os.environ['OMNI_PAYROLL_FIN_SIGNERS'] = 'fin1,fin2'
        self.addCleanup(lambda: os.environ.pop('OMNI_PAYROLL_FIN_SIGNERS', None))
        self.fin_a = User.objects.create_user('fin1', email='fin1@example.com', password='x')
        self.fin_b = User.objects.create_user('fin2', email='fin2@example.com', password='x')
        # A non-finance requester (payroll clerk).
        self.clerk = User.objects.create_user('clerk', email='clerk@example.com', password='x')

    def _req(self, user=None, **kw):
        d = dict(full_name='New Person', basic=Decimal('10000.00'))
        d.update(kw)
        return svc.create_addition(period=self.period, company=self.co,
                                   user=user or self.clerk, **d)

    # ── staging touches nothing ─────────────────────────────────────────────
    def test_create_is_pending_and_touches_nothing(self):
        req, _ = self._req()
        self.assertEqual(req.status, PayrollAdditionRequest.Status.PENDING)
        self.assertEqual(Employee.objects.count(), 0)
        self.assertEqual(Payslip.objects.filter(period=self.period).count(), 0)

    def test_basic_required(self):
        with self.assertRaises(ValidationError):
            self._req(basic=Decimal('0'))

    # ── approval posts a DRAFT payslip with reused PAYE engine ───────────────
    def test_approve_creates_draft_payslip_and_paye(self):
        req, _ = self._req(basic=Decimal('10000.00'))
        svc.approve_addition(req, self.fin_a)
        req.refresh_from_db()
        self.assertEqual(req.status, PayrollAdditionRequest.Status.APPROVED)
        ps = req.created_payslip
        self.assertIsNotNone(ps)
        self.assertEqual(ps.status, Payslip.Status.DRAFT)
        self.assertEqual(ps.gross_amount, Decimal('10000.00'))
        # 10,000/mo → 120,000/yr → 1800 + 12.5%×(120000−84000)=6300/yr → 525.00/mo
        self.assertEqual(ps.paye_amount, Decimal('525.00'))
        self.assertEqual(ps.net_amount, Decimal('9475.00'))

    def test_allowances_fold_into_gross(self):
        req, _ = self._req(basic=Decimal('8000.00'),
                           allowances=[{'code': 'HOUSING_ALLOWANCE', 'amount': '500.00'},
                                       {'code': 'COMMISSION', 'amount': '250.00'}])
        svc.approve_addition(req, self.fin_a)
        req.refresh_from_db()
        self.assertEqual(req.created_payslip.gross_amount, Decimal('8750.00'))

    # ── maker-checker + segregation of duties ────────────────────────────────
    def test_sod_requester_cannot_approve_own(self):
        req, _ = self._req(user=self.fin_a)          # a finance user submits
        with self.assertRaises(ValidationError):     # …cannot approve their own
            svc.approve_addition(req, self.fin_a)

    def test_non_finance_cannot_approve(self):
        req, _ = self._req()
        with self.assertRaises(PermissionDenied):
            svc.approve_addition(req, self.clerk)

    def test_reject_requires_comment_and_posts_nothing(self):
        req, _ = self._req()
        with self.assertRaises(ValidationError):
            svc.reject_addition(req, self.fin_a, '   ')
        svc.reject_addition(req, self.fin_a, 'Details incomplete, please resubmit.')
        req.refresh_from_db()
        self.assertEqual(req.status, PayrollAdditionRequest.Status.REJECTED)
        self.assertEqual(req.rejection_comment, 'Details incomplete, please resubmit.')
        self.assertEqual(Payslip.objects.count(), 0)
        self.assertEqual(Employee.objects.count(), 0)

    # ── the July-2026 duplicate-employee guard ───────────────────────────────
    def test_links_existing_never_duplicates(self):
        existing = Employee.objects.create(employee_number='E1', full_name='Bonang Lentswe',
                                           company=self.co, status='active')
        req, warns = self._req(full_name='Bonang  Lentswe')   # double space variant
        self.assertTrue(warns)                                 # duplicate warning surfaced
        svc.approve_addition(req, self.fin_a)
        req.refresh_from_db()
        self.assertTrue(req.linked_existing)
        self.assertEqual(req.created_payslip.employee_id, existing.id)
        self.assertEqual(Employee.objects.count(), 1)          # NO duplicate minted

    def test_already_in_period_blocks_approve(self):
        emp = Employee.objects.create(employee_number='E9', full_name='Dup Person',
                                      company=self.co, status='active')
        Payslip.objects.create(employee=emp, period=self.period, company=self.co,
                               status=Payslip.Status.DRAFT)
        req, _ = self._req(full_name='Dup Person')
        with self.assertRaises(ValidationError):
            svc.approve_addition(req, self.fin_a)

    # ── period controls ──────────────────────────────────────────────────────
    def test_signed_off_period_blocks_create(self):
        PayrollSignOff.objects.create(
            period=self.period, company=self.co, status=PayrollSignOff.Status.APPROVED,
            hr_signed_by=self.fin_b, fin_signed_by=self.fin_a)
        with self.assertRaises(ValidationError):
            self._req()

    # ── inbox stream + SoD exclusion ─────────────────────────────────────────
    def test_stream_and_sod_exclusion(self):
        self._req(user=self.clerk)                                   # pending #1 (clerk)
        self._req(user=self.fin_b)                                   # pending #2 (fin_b's own)
        self.assertEqual(svc.pending_for_approver(self.fin_a).count(), 2)   # sees both
        self.assertEqual(svc.pending_for_approver(self.fin_b).count(), 1)   # not own
        self.assertEqual(svc.pending_for_approver(self.clerk).count(), 0)   # non-finance
        keys = {s['key'] for s in pending_approvals_for(self.fin_a)}
        self.assertIn('payroll_additions', keys)

    def test_can_action_predicate_matches_the_inbox(self):
        # H50: the row's action flag and the My-Approvals count share one rule —
        # a Finance signer who did NOT submit it may act; the submitter may not.
        from payroll.addition_views import _serialize
        req, _ = self._req(user=self.fin_a)                        # fin_a submits
        self.assertFalse(_serialize(req, self.fin_a)['can_action'])  # own → no button
        self.assertTrue(_serialize(req, self.fin_b)['can_action'])   # other finance → yes
        self.assertFalse(_serialize(req, self.clerk)['can_action'])  # non-finance → no

    def test_signed_off_period_blocks_the_addition_with_a_reason(self):
        # If the period was signed off AFTER the addition was raised, the approve
        # would just error — so no dead Approve button; instead an explicit reason
        # (reopen the period first). Fixed 2026-08-17 after the CFO hit the
        # contradictory "awaiting sign-off" + "already signed off" screen.
        from unittest.mock import patch
        from payroll.addition_views import _serialize
        req, _ = self._req(user=self.fin_a)
        with patch('payroll.signoff_service.is_signed_off', return_value=True):
            row = _serialize(req, self.fin_b)            # another finance signer
        self.assertFalse(row['can_action'])              # no Approve button offered
        self.assertTrue(row['blocked_reason'])           # a reason is given instead
        self.assertIn('signed off', row['blocked_reason'].lower())
        self.assertIn('reopen', row['blocked_reason'].lower())


class AdditionApiTests(TestCase):
    def setUp(self):
        self.co = Company.objects.create(code='ADIC', name='Alpha Direct')
        self.period = PayrollPeriod.objects.create(
            period_name='2026-07', start_date='2026-07-01', end_date='2026-07-31')
        _seed_components()
        _seed_brackets()
        self.su1 = User.objects.create_superuser('boss1', 'boss1@example.com', 'x')
        self.su2 = User.objects.create_superuser('boss2', 'boss2@example.com', 'x')

    def test_meta_endpoint(self):
        c = APIClient(); c.force_authenticate(self.su1)
        r = c.get('/api/v1/payroll/additions/meta/')
        self.assertEqual(r.status_code, 200)
        self.assertIn('allowance_types', r.data)
        self.assertTrue(any(p['period_name'] == '2026-07' for p in r.data['periods']))

    def test_create_then_approve_via_api(self):
        c1 = APIClient(); c1.force_authenticate(self.su1)
        r = c1.post('/api/v1/payroll/additions/', {
            'period_id': str(self.period.id), 'company_id': str(self.co.id),
            'full_name': 'Api Person', 'basic': '8000.00',
            'allowances': [{'code': 'HOUSING_ALLOWANCE', 'amount': '500.00'}],
        }, format='json')
        self.assertEqual(r.status_code, 201)
        rid = r.data['request']['id']
        # totals untouched until approval
        self.assertEqual(Payslip.objects.filter(period=self.period).count(), 0)
        c2 = APIClient(); c2.force_authenticate(self.su2)   # different approver (SoD)
        r2 = c2.post(f'/api/v1/payroll/additions/{rid}/approve/', {}, format='json')
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(Payslip.objects.filter(period=self.period).exists())

    def test_requester_cannot_self_approve_via_api(self):
        c1 = APIClient(); c1.force_authenticate(self.su1)
        r = c1.post('/api/v1/payroll/additions/', {
            'period_id': str(self.period.id), 'company_id': str(self.co.id),
            'full_name': 'Self Approve', 'basic': '5000.00',
        }, format='json')
        rid = r.data['request']['id']
        r2 = c1.post(f'/api/v1/payroll/additions/{rid}/approve/', {}, format='json')
        self.assertEqual(r2.status_code, 400)   # SoD blocks own approval


class AdditionEntityScopeTests(TestCase):
    """H33 — a payroll user granted entity A must not STAGE onto, READ the
    staged salaries of, or ACTION entity B by passing B's company_id. This is
    the live exploit Fable ran; it fails without the _can_touch clamp."""

    def setUp(self):
        from core.models import UserProfile, UserCompanyAccess
        self.a = Company.objects.create(code='ADIC', name='Alpha Direct')
        self.b = Company.objects.create(code='ADSA', name='Alpha SA')
        self.period = PayrollPeriod.objects.create(
            period_name='2026-07', start_date='2026-07-01', end_date='2026-07-31')
        _seed_components(); _seed_brackets()
        # Payroll user: HR-manager title passes CanViewPayroll, but NOT
        # administrator/CFO/superuser → entity-restricted to their grants.
        self.u = User.objects.create_user('esclerk', email='esclerk@example.com', password='x')
        UserProfile.objects.update_or_create(
            user=self.u, defaults=dict(title='hr_manager', department='HR', is_administrator=False))
        UserCompanyAccess.objects.create(user=self.u, company=self.a, can_view=True)
        # A pending addition on entity B, which esclerk has NO grant to.
        self.b_req = PayrollAdditionRequest.objects.create(
            period=self.period, company=self.b, full_name='Secret B Salary',
            basic=Decimal('99999.00'), status=PayrollAdditionRequest.Status.PENDING)

    def _client(self):
        c = APIClient(); c.force_authenticate(self.u); return c

    def test_cannot_stage_onto_an_ungranted_entity(self):
        r = self._client().post('/api/v1/payroll/additions/', {
            'period_id': str(self.period.id), 'company_id': str(self.b.id),
            'full_name': 'Sneaky', 'basic': '5000.00'}, format='json')
        self.assertEqual(r.status_code, 403)
        self.assertFalse(PayrollAdditionRequest.objects.filter(full_name='Sneaky').exists())

    def test_can_still_stage_onto_the_granted_entity(self):
        r = self._client().post('/api/v1/payroll/additions/', {
            'period_id': str(self.period.id), 'company_id': str(self.a.id),
            'full_name': 'Allowed', 'basic': '5000.00'}, format='json')
        self.assertEqual(r.status_code, 201)

    def test_list_does_not_leak_another_entitys_pending(self):
        r = self._client().get('/api/v1/payroll/additions/')
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('Secret B Salary', [x['full_name'] for x in r.data['results']])

    def test_meta_offers_only_granted_entities(self):
        r = self._client().get('/api/v1/payroll/additions/meta/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual({c['code'] for c in r.data['companies']}, {'ADIC'})

    def test_cannot_approve_or_reject_across_entity(self):
        c = self._client()
        self.assertEqual(
            c.post(f'/api/v1/payroll/additions/{self.b_req.id}/approve/', {}, format='json').status_code, 403)
        self.assertEqual(
            c.post(f'/api/v1/payroll/additions/{self.b_req.id}/reject/', {'comment': 'no'}, format='json').status_code, 403)
