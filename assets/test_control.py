"""
assets/test_control.py

Acceptance-criteria tests for the Asset Control & Handover module (spec §12).
Each test maps to one acceptance criterion / hard rule and exercises the REAL
service path, so a regression that removes a control turns the test red.

Run:
    DB_ENGINE=sqlite DJANGO_SETTINGS_MODULE=alpha_finance.settings \
    SECRET_KEY=x DB_PASSWORD=x <venv>/python manage.py test assets.test_control
"""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase

from core.models import Company, UserProfile
from ledger.models import Account, Currency

from assets import control_services as svc
from assets.control_models import AssetHandover, AssetRequisition
from assets.models import Asset, AssetCategory
from payroll.archive_service import archive_employee
from payroll.models import Employee


def _mk_user(username, title, is_super=False):
    u = User.objects.create_user(username, password='x', is_superuser=is_super)
    UserProfile.objects.create(user=u, title=title, is_active=True)
    return u


class AssetControlTestBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        Currency.objects.get_or_create(code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        cls.bwp = Currency.objects.get(code='BWP')
        cls.company = Company.objects.create(code='ACMTEST', name='Asset Control Test Co.')

        def acct(code, name, sub):
            return Account.objects.create(
                code=code, name=name, account_type=Account.AccountType.ASSET,
                sub_type=sub, currency_code=cls.bwp,
            )
        cost = acct('1420T', 'IT equipment cost', Account.SubType.FIXED_ASSET)
        accum = acct('1452T', 'IT accum depr', Account.SubType.FIXED_ASSET)
        expense = Account.objects.create(
            code='6600T', name='Depreciation expense',
            account_type=Account.AccountType.EXPENSE, currency_code=cls.bwp,
        )
        cls.category = AssetCategory.objects.create(
            code='ITT', name='IT equipment', cost_account=cost,
            accum_depr_account=accum, depreciation_expense_account=expense,
        )

        # Users on the gate.
        cls.cfo = _mk_user('cfo_acm', UserProfile.Title.CFO)
        cls.fm = _mk_user('fm_acm', UserProfile.Title.FINANCE_MANAGER)
        cls.it = _mk_user('it_acm', UserProfile.Title.ACCOUNTANT)      # IT Asset Officer
        cls.it.email = 'itofficer@alphadirect.co.bw'
        cls.it.save(update_fields=['email'])
        cls.recipient_user = _mk_user('recip_acm', UserProfile.Title.OPERATIONS)

        # Register the test IT user as a named IT Asset Officer (CFO allow-list).
        from assets.control_models import AssetControlPolicy
        policy = AssetControlPolicy.current()
        policy.it_officer_emails = ['itofficer@alphadirect.co.bw']
        policy.save(update_fields=['it_officer_emails'])

        cls.recipient = Employee.objects.create(
            full_name='Kabo Recipient', email='kabo@alphadirect.co.bw',
            employee_number='E-ACM-1', status=Employee.Status.ACTIVE,
            company=cls.company, user=cls.recipient_user,
        )

    def _asset(self, tag, custody=Asset.CustodyStatus.IN_STOCK):
        a = Asset.objects.create(
            tag_number=tag, name=f'Laptop {tag}', company=self.company, category=self.category,
            cost=Decimal('9000.00'), salvage_value=Decimal('0.00'),
            method=Asset.Method.STRAIGHT_LINE, useful_life_months=36,
            purchase_date=date(2026, 1, 1), in_service_date=date(2026, 1, 1),
            created_by=self.it, custody_status=custody,
        )
        return a

    def _new_material_req(self):
        return svc.create_requisition(
            company=self.company, req_type=AssetRequisition.Type.NEW_PURCHASE,
            category=self.category, description='Dell Latitude', estimated_value=Decimal('9000'),
            reason='Replacement laptop', recipient=self.recipient, user=self.it,
        )

    def _approve_full(self, req):
        svc.fm_approve(req, self.fm)
        req.refresh_from_db()
        svc.cfo_approve(req, self.cfo)
        req.refresh_from_db()
        return req


class RecipientMustBeDirectoryPick(AssetControlTestBase):
    """AC1 + Rule 2 — no free-typed recipient."""

    def test_recipient_is_a_foreign_key_not_text(self):
        req = self._new_material_req()
        self.assertEqual(req.recipient_id, self.recipient.id)
        self.assertEqual(req.recipient_name, 'Kabo Recipient')
        # The model field is an FK to a real Employee — a name string cannot be
        # stored as the recipient at all.
        field = AssetRequisition._meta.get_field('recipient')
        self.assertEqual(field.related_model, Employee)

    def test_inactive_recipient_rejected(self):
        self.recipient.status = Employee.Status.TERMINATED
        self.recipient.save(update_fields=['status'])
        with self.assertRaises(ValidationError):
            self._new_material_req()


class OnlyITOfficerCanRaiseAndRelease(AssetControlTestBase):
    """CFO 2026-09-02 — only the named IT Asset Officers raise a requisition and
    sign the IT-release."""

    def test_non_it_cannot_raise(self):
        # An ordinary finance user is not an IT Asset Officer.
        with self.assertRaises(ValidationError):
            svc.create_requisition(
                company=self.company, req_type=AssetRequisition.Type.NEW_PURCHASE,
                category=self.category, description='Laptop', estimated_value=Decimal('9000'),
                reason='x', recipient=self.recipient, user=self.fm,
            )

    def test_non_it_cannot_sign_it_release(self):
        req = self._approve_full(self._new_material_req())
        asset = self._asset('AD-LT-ITR')
        ho = svc.create_handover(req, asset=asset, user=self.it)
        # The FM is not an IT Asset Officer — cannot sign the IT-release.
        with self.assertRaises(ValidationError):
            svc.handover_it_release(ho, self.fm)


class NoSelfApproval(AssetControlTestBase):
    """AC2 — a user cannot approve their own requisition."""

    def _make_it_officer(self, user):
        """Register a user as an IT Asset Officer so they can raise a requisition
        — used to isolate the self-approval rule from the IT-officer rule."""
        from assets.control_models import AssetControlPolicy
        user.email = f'{user.username}@alphadirect.co.bw'
        user.save(update_fields=['email'])
        policy = AssetControlPolicy.current()
        policy.it_officer_emails = list(policy.it_officer_emails) + [user.email]
        policy.save(update_fields=['it_officer_emails'])

    def test_requester_cannot_fm_approve(self):
        # An FM who is ALSO an IT officer raises the requisition themselves. They
        # ARE an eligible approver, so any block here comes purely from the
        # self-approval rule (AC2), not from a lack of authority.
        fm2 = _mk_user('fm2_acm', UserProfile.Title.FINANCE_MANAGER)
        self._make_it_officer(fm2)
        req = svc.create_requisition(
            company=self.company, req_type=AssetRequisition.Type.NEW_PURCHASE,
            category=self.category, description='Laptop', estimated_value=Decimal('9000'),
            reason='own request', recipient=self.recipient, user=fm2,
        )
        self.assertTrue(svc.can_approve_as_fm(fm2))   # eligibility is NOT the reason
        with self.assertRaises(ValidationError):
            svc.fm_approve(req, fm2)

    def test_requester_cannot_cfo_approve(self):
        # The CFO raises the requisition. The CFO is an eligible CFO-leg approver,
        # so the block is the self-approval rule alone.
        cfo2 = _mk_user('cfo2_acm', UserProfile.Title.CFO)
        self._make_it_officer(cfo2)
        req = svc.create_requisition(
            company=self.company, req_type=AssetRequisition.Type.NEW_PURCHASE,
            category=self.category, description='Laptop', estimated_value=Decimal('9000'),
            reason='own request', recipient=self.recipient, user=cfo2,
        )
        svc.fm_approve(req, self.fm)
        req.refresh_from_db()
        self.assertTrue(svc.can_approve_as_cfo(cfo2))  # eligibility is NOT the reason
        with self.assertRaises(ValidationError):
            svc.cfo_approve(req, cfo2)

    def test_cfo_approver_must_differ_from_fm(self):
        req = self._new_material_req()
        svc.fm_approve(req, self.cfo)   # CFO acts on FM leg (allowed: FM set includes CFO)
        req.refresh_from_db()
        # Same person cannot then take the CFO leg.
        with self.assertRaises(ValidationError):
            svc.cfo_approve(req, self.cfo)


class DualGateBeforeHandover(AssetControlTestBase):
    """AC3 — both CFO and FM must approve before a handover is possible."""

    def test_handover_blocked_until_fully_approved(self):
        req = self._new_material_req()
        asset = self._asset('AD-LT-A3')
        # Not approved at all.
        with self.assertRaises(ValidationError):
            svc.create_handover(req, asset=asset, user=self.it)
        # Only FM approved (material -> still pending CFO).
        svc.fm_approve(req, self.fm)
        req.refresh_from_db()
        self.assertEqual(req.status, AssetRequisition.Status.PENDING_CFO_APPROVAL)
        with self.assertRaises(ValidationError):
            svc.create_handover(req, asset=asset, user=self.it)
        # Both approved -> handover allowed.
        svc.cfo_approve(req, self.cfo)
        req.refresh_from_db()
        ho = svc.create_handover(req, asset=asset, user=self.it)
        self.assertEqual(ho.status, AssetHandover.Status.PENDING)

    def test_below_threshold_needs_single_signoff(self):
        req = svc.create_requisition(
            company=self.company, req_type=AssetRequisition.Type.NEW_PURCHASE,
            category=self.category, description='USB mouse', estimated_value=Decimal('120'),
            reason='Mouse', recipient=self.recipient, user=self.it,
        )
        self.assertFalse(req.requires_full_gate)
        svc.fm_approve(req, self.fm)
        req.refresh_from_db()
        # One sign-off is enough below the threshold.
        self.assertEqual(req.status, AssetRequisition.Status.APPROVED)

    def test_threshold_is_5000(self):
        # CFO 2026-09-02: the full CFO+FM gate targets laptop/vehicle movements
        # — assets above P5,000. P4,999 is light; P5,000 is material.
        def mk(value):
            return svc.create_requisition(
                company=self.company, req_type=AssetRequisition.Type.NEW_PURCHASE,
                category=self.category, description='x', estimated_value=Decimal(value),
                reason='x', recipient=self.recipient, user=self.it,
            )
        self.assertFalse(mk('4999').requires_full_gate)
        self.assertTrue(mk('5000').requires_full_gate)


class SpareNeedsFreshRequisition(AssetControlTestBase):
    """AC4 + Rule 1 — a spare cannot be issued without a fresh approved requisition."""

    def test_reissue_requires_returned_spare(self):
        in_stock = self._asset('AD-LT-A4a', custody=Asset.CustodyStatus.IN_STOCK)
        with self.assertRaises(ValidationError):
            svc.create_requisition(
                company=self.company, req_type=AssetRequisition.Type.REISSUE,
                category=self.category, description='reissue', estimated_value=Decimal('9000'),
                reason='reissue', recipient=self.recipient, user=self.it, spare_asset=in_stock,
            )

    def test_direct_transfer_of_spare_blocked(self):
        from assets.services import transfer_asset_custodian
        spare = self._asset('AD-LT-A4b', custody=Asset.CustodyStatus.RETURNED_SPARE)
        with self.assertRaises(ValidationError):
            transfer_asset_custodian(
                spare, to_employee=self.recipient, to_location='', reason='hand it over', user=self.it,
            )

    def test_spare_reissue_full_happy_path(self):
        spare = self._asset('AD-LT-A4c', custody=Asset.CustodyStatus.RETURNED_SPARE)
        req = svc.create_requisition(
            company=self.company, req_type=AssetRequisition.Type.REISSUE,
            category=self.category, description='reissue spare', estimated_value=Decimal('9000'),
            reason='reissue', recipient=self.recipient, user=self.it, spare_asset=spare,
        )
        self._approve_full(req)
        ho = svc.create_handover(req, asset=spare, user=self.it)
        self.assertEqual(ho.asset_id, spare.id)


class CloseSideDoors(AssetControlTestBase):
    """CFO 2026-09-02 — every holder change and every exit must pass the gate."""

    def test_quick_transfer_to_new_holder_blocked(self):
        from assets.services import transfer_asset_custodian
        a = self._asset('AD-LT-D1a')  # in stock, no custodian
        # Handing it to a person via the quick button is blocked.
        with self.assertRaises(ValidationError):
            transfer_asset_custodian(a, to_employee=self.recipient, to_location='',
                                     reason='x', user=self.it)

    def test_location_only_move_still_allowed(self):
        from assets.services import transfer_asset_custodian
        a = self._asset('AD-LT-D1b')
        # No new holder — just a location change — still works.
        transfer_asset_custodian(a, to_employee=None, to_location='Store B',
                                 reason='moved', user=self.it)
        a.refresh_from_db()
        self.assertEqual(a.location, 'Store B')

    def test_superuser_can_still_correct_holder(self):
        from assets.services import transfer_asset_custodian
        su = User.objects.create_user('door1_su', password='x', is_superuser=True)
        a = self._asset('AD-LT-D1c')
        transfer_asset_custodian(a, to_employee=self.recipient, to_location='',
                                 reason='data fix', user=su)   # no raise
        a.refresh_from_db()
        self.assertEqual(a.custodian_employee_id, self.recipient.id)

    def test_direct_terminate_blocked_while_holding_asset(self):
        from rest_framework.test import APIRequestFactory, force_authenticate
        from payroll.api_views import EmployeeViewSet
        # Give the recipient a held asset through the full controlled flow.
        req = self._approve_full(self._new_material_req())
        asset = self._asset('AD-LT-D2')
        ho = svc.create_handover(req, asset=asset, user=self.it)
        svc.handover_it_release(ho, self.it)
        svc.handover_finance_record(ho, self.fm)
        svc.handover_employee_accept(ho, self.recipient_user)

        su = User.objects.create_user('door2_su', password='x', is_superuser=True)
        view = EmployeeViewSet.as_view({'patch': 'partial_update'})
        factory = APIRequestFactory()
        r = factory.patch(f'/api/v1/employees/{self.recipient.id}/',
                          {'status': 'terminated'}, format='json')
        force_authenticate(r, user=su)
        resp = view(r, pk=str(self.recipient.id))
        self.assertEqual(resp.status_code, 400)   # blocked — still holds AD-LT-D2

        # After returning the asset, the terminate goes through the guard.
        asset.refresh_from_db()
        svc.return_asset(asset, self.it)
        r2 = factory.patch(f'/api/v1/employees/{self.recipient.id}/',
                           {'status': 'terminated'}, format='json')
        force_authenticate(r2, user=su)
        resp2 = view(r2, pk=str(self.recipient.id))
        self.assertNotEqual(resp2.status_code, 400)


class InUseNeedsThreeSignatures(AssetControlTestBase):
    """AC5 — no asset shows 'In use' without a signed 3-signature handover."""

    def test_full_signature_chain_sets_in_use(self):
        req = self._approve_full(self._new_material_req())
        asset = self._asset('AD-LT-A5')
        ho = svc.create_handover(req, asset=asset, user=self.it)
        asset.refresh_from_db()
        self.assertEqual(asset.custody_status, Asset.CustodyStatus.ISSUED)  # not yet in use

        svc.handover_it_release(ho, self.it)
        svc.handover_finance_record(ho, self.fm)
        asset.refresh_from_db()
        self.assertNotEqual(asset.custody_status, Asset.CustodyStatus.IN_USE)  # still not in use

        svc.handover_employee_accept(ho, self.recipient_user)
        asset.refresh_from_db()
        ho.refresh_from_db()
        self.assertEqual(asset.custody_status, Asset.CustodyStatus.IN_USE)
        self.assertEqual(asset.custodian_employee_id, self.recipient.id)
        self.assertTrue(ho.is_complete)
        self.assertEqual(ho.requisition.status, AssetRequisition.Status.FULFILLED)
        # An append-only custody row was written.
        self.assertTrue(asset.assignments.filter(to_employee=self.recipient).exists())

    def test_only_recipient_can_accept(self):
        req = self._approve_full(self._new_material_req())
        asset = self._asset('AD-LT-A5b')
        ho = svc.create_handover(req, asset=asset, user=self.it)
        svc.handover_it_release(ho, self.it)
        svc.handover_finance_record(ho, self.fm)
        # The FM is not the recipient — cannot accept on their behalf.
        with self.assertRaises(ValidationError):
            svc.handover_employee_accept(ho, self.fm)

    def test_finance_signer_must_differ_from_it(self):
        req = self._approve_full(self._new_material_req())
        asset = self._asset('AD-LT-A5c')
        ho = svc.create_handover(req, asset=asset, user=self.it)
        svc.handover_it_release(ho, self.it)
        # Same person cannot provide the finance signature.
        with self.assertRaises(ValidationError):
            svc.handover_finance_record(ho, self.it)

    def test_recipient_cannot_sign_it_release(self):
        # All three signers must be distinct: the recipient cannot also release.
        req = self._approve_full(self._new_material_req())
        asset = self._asset('AD-LT-A5d')
        ho = svc.create_handover(req, asset=asset, user=self.it)
        with self.assertRaises(ValidationError):
            svc.handover_it_release(ho, self.recipient_user)


class CancelHandoverReleasesAsset(AssetControlTestBase):
    """An abandoned handover can be cancelled and the asset freed (not stuck ISSUED)."""

    def test_cancel_frees_asset_and_reopens_requisition(self):
        req = self._approve_full(self._new_material_req())
        asset = self._asset('AD-LT-CAN')
        ho = svc.create_handover(req, asset=asset, user=self.it)
        svc.handover_it_release(ho, self.it)
        asset.refresh_from_db()
        self.assertEqual(asset.custody_status, Asset.CustodyStatus.ISSUED)

        # Only Finance / an FM may cancel — the FM does here.
        svc.cancel_handover(ho, self.fm, reason='Recipient declined')
        asset.refresh_from_db()
        ho.refresh_from_db()
        req.refresh_from_db()
        self.assertEqual(asset.custody_status, Asset.CustodyStatus.IN_STOCK)
        self.assertEqual(ho.status, AssetHandover.Status.CANCELLED)
        # OneToOne blocks re-opening a note on this requisition → it is cancelled too.
        self.assertEqual(req.status, AssetRequisition.Status.CANCELLED)
        self.assertIsNone(req.resulting_asset_id)

    def test_recipient_cannot_cancel(self):
        req = self._approve_full(self._new_material_req())
        asset = self._asset('AD-LT-CAN2')
        ho = svc.create_handover(req, asset=asset, user=self.it)
        with self.assertRaises(ValidationError):
            svc.cancel_handover(ho, self.recipient_user, reason='nope')


class OffboardingBlockedByHeldAsset(AssetControlTestBase):
    """AC6 — offboarding cannot complete while an asset is still with the leaver."""

    def test_archive_blocked_then_allowed_after_return(self):
        req = self._approve_full(self._new_material_req())
        asset = self._asset('AD-LT-A6')
        ho = svc.create_handover(req, asset=asset, user=self.it)
        svc.handover_it_release(ho, self.it)
        svc.handover_finance_record(ho, self.fm)
        svc.handover_employee_accept(ho, self.recipient_user)

        self.recipient.termination_date = date(2026, 9, 1)
        self.recipient.save(update_fields=['termination_date'])

        with self.assertRaises(ValidationError):
            archive_employee(self.recipient, actor=self.it, reason='Resigned')

        # Return the asset — now the exit can be finalised.
        asset.refresh_from_db()
        svc.return_asset(asset, self.it, reason='Exit return')
        archive_employee(self.recipient, actor=self.it, reason='Resigned')
        self.recipient.refresh_from_db()
        self.assertTrue(self.recipient.is_archived)


class RegisterTiesOutToCount(AssetControlTestBase):
    """AC7 — the register export ties out to the physical count."""

    def test_reconciliation_view_ties_out(self):
        from assets.control_api import AssetCountReconciliationView
        from assets.models import AssetSignOff
        from django.utils import timezone
        from rest_framework.test import APIRequestFactory, force_authenticate
        self._asset('AD-LT-A7a')
        self._asset('AD-LT-A7b')
        # Physical count of 2 assets, both found -> ties out.
        AssetSignOff.objects.create(
            kind=AssetSignOff.Kind.SEMI_ANNUAL_COUNT, period_label='H2-2026',
            due_date=date(2026, 12, 31), company=self.company,
            status=AssetSignOff.Status.COMPLETED, counted_assets=2,
            second_signed_at=timezone.now(),
        )
        su = User.objects.create_user('recon_su', password='x', is_superuser=True)
        UserProfile.objects.create(user=su, title=UserProfile.Title.CFO, is_active=True)
        req = APIRequestFactory().get(f'/api/v1/asset-control/reconciliation/?company={self.company.id}')
        force_authenticate(req, user=su)
        resp = AssetCountReconciliationView.as_view()(req)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['register_count'], 2)
        self.assertEqual(resp.data['counted_assets'], 2)
        self.assertEqual(resp.data['variance'], 0)
        self.assertIs(resp.data['ties_out'], True)
