"""
Serializer SoD gate for the PO "Approve" button — dead-end-button fix (2026-09-06).

PurchaseOrderDetailSerializer.get_can_approve must mirror the segregation-of-duties
guard that procurement.services.fm_approve / cfo_approve enforce, so a user is only
shown an "Approve" button when the action will actually succeed.

The bug: a Finance Manager who RAISED a >= P10,000 operational PO was shown an
"Approve" button (desktop and mobile) that only ever returned
400 "Segregation of duties: you cannot FM-approve a PO you created."

These tests pin the fix and prove the two "unchanged" paths (single-step < 10k,
and claims) still offer the button to their raiser.
"""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase

from core.models import Company, Currency, UserProfile
from ledger.models import FiscalPeriod
from procurement.models import PurchaseOrder, PurchaseOrderLine
from procurement.serializers import PurchaseOrderDetailSerializer
from procurement.services import (
    CFO_APPROVAL_THRESHOLD_BWP, submit_for_approval, fm_approve, cfo_approve,
)
from core.approvals_views import pending_approval_items_for, pending_approvals_for


class POApproveSoDSerializerTest(TestCase):
    """get_can_approve mirrors the fm_approve / cfo_approve SoD guard."""

    @classmethod
    def setUpTestData(cls):
        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'},
        )
        cls.company = Company.objects.create(code='TEST', name='Test Co.')
        # An open fiscal period covering today — submit_for_approval locks the PO
        # into the period for its issue_date (which we set to today).
        _t = date.today()
        FiscalPeriod.objects.create(
            period_name=_t.strftime('%Y-%m'),
            start_date=date(_t.year, 1, 1), end_date=date(_t.year, 12, 31),
            status=FiscalPeriod.Status.OPEN,
        )
        # Two Finance Managers, both eligible at the FM leg. Neither is a Django
        # superuser — approval authority comes from the TITLE, exactly like prod,
        # so the only thing that can turn the button off is the SoD mirror.
        cls.fm_raiser = User.objects.create_user('fm_raiser', password='x')
        UserProfile.objects.create(
            user=cls.fm_raiser, title=UserProfile.Title.FINANCE_MANAGER, is_active=True,
        )
        cls.fm_other = User.objects.create_user('fm_other', password='x')
        UserProfile.objects.create(
            user=cls.fm_other, title=UserProfile.Title.FINANCE_MANAGER, is_active=True,
        )
        # A claims senior — approves claims POs (which are single-step, no SoD).
        cls.claims_mgr = User.objects.create_user('claims_mgr', password='x')
        UserProfile.objects.create(
            user=cls.claims_mgr, title=UserProfile.Title.CLAIMS_MANAGER, is_active=True,
        )
        # Two CFOs for the CFO-leg SoD checks (a CFO title also satisfies the FM
        # leg, so a CFO can be the FM-approver and then be barred at the CFO leg).
        cls.cfo_a = User.objects.create_user('cfo_a', password='x')
        UserProfile.objects.create(
            user=cls.cfo_a, title=UserProfile.Title.CFO, is_active=True,
        )
        cls.cfo_b = User.objects.create_user('cfo_b', password='x')
        UserProfile.objects.create(
            user=cls.cfo_b, title=UserProfile.Title.CFO, is_active=True,
        )

    def _pending_fm_po(self, total, raiser, department='admin'):
        """Create a PO raised by `raiser`, submit it, return it at
        PENDING_FM_APPROVAL (so created_by == submitted_by == raiser)."""
        from billing.models import Contact
        supplier = Contact.objects.create(
            name='Acme Supplies', contact_type='vendor',
            is_related_party=False, company=self.company,
        )
        po = PurchaseOrder.objects.create(
            po_number=f'PO-{PurchaseOrder.objects.count() + 1:04d}',
            issue_date=date.today(),
            department=department, supplier=supplier, currency_code_id='BWP',
            exchange_rate=Decimal('1.0'), company=self.company,
            created_by=raiser, status=PurchaseOrder.Status.DRAFT,
            justification='ops', related_claim_reference='CLM-1',
        )
        PurchaseOrderLine.objects.create(
            purchase_order=po, description='Line', quantity=Decimal('1'),
            unit_price=total, line_total=total,
        )
        po.recalculate_totals()
        po.save()
        po = submit_for_approval(po, raiser)   # submitted_by = raiser
        po.refresh_from_db()
        return po

    def _pending_cfo_po(self, total, raiser, fm_approver):
        """Create + submit a >=10k operational PO, FM-approve it with a different
        eligible approver, and return it at PENDING_CFO_APPROVAL."""
        po = self._pending_fm_po(total, raiser=raiser)
        po = fm_approve(po, fm_approver)         # -> PENDING_CFO_APPROVAL
        po.refresh_from_db()
        return po

    def _flags(self, po, user):
        ser = PurchaseOrderDetailSerializer(
            po, context={'request': SimpleNamespace(user=user)},
        )
        return ser.data['can_approve'], ser.data['can_reject']

    def _can_approve(self, po, user):
        return self._flags(po, user)[0]

    def _can_reject(self, po, user):
        return self._flags(po, user)[1]

    def _po_item_ids(self, user):
        """PO pks the approvals ITEMS list would show `user`."""
        for stream in pending_approval_items_for(user):
            if stream.get('key') == 'po':
                return {it['id'] for it in stream['items']}
        return set()

    def _po_count(self, user):
        """Count the approvals BADGE would show `user` for the 'po' stream."""
        for stream in pending_approvals_for(user):
            if stream.get('key') == 'po':
                return stream['count']
        return 0

    # -- the fix -------------------------------------------------------------

    def test_raiser_of_large_operational_po_cannot_approve(self):
        """The FM who raised a >= 10k operational PO must NOT be offered Approve
        (the backend would only 400). This is the dead-end-button fix."""
        po = self._pending_fm_po(Decimal('15000.00'), raiser=self.fm_raiser)
        self.assertEqual(po.status, PurchaseOrder.Status.PENDING_FM_APPROVAL)
        self.assertGreaterEqual(Decimal(str(po.total_bwp)), CFO_APPROVAL_THRESHOLD_BWP)
        self.assertFalse(self._can_approve(po, self.fm_raiser))

    # -- don't over-block ----------------------------------------------------

    def test_independent_fm_can_approve_large_operational_po(self):
        """A different eligible FM (not creator/submitter) still sees Approve."""
        po = self._pending_fm_po(Decimal('15000.00'), raiser=self.fm_raiser)
        self.assertTrue(self._can_approve(po, self.fm_other))

    # -- single-step (< 10k) unchanged --------------------------------------

    def test_raiser_of_small_operational_po_can_approve(self):
        """Single-step (< 10k) operational POs carry no SoD gate: the raiser may
        still self-approve, exactly as before."""
        po = self._pending_fm_po(Decimal('5000.00'), raiser=self.fm_raiser)
        self.assertLess(Decimal(str(po.total_bwp)), CFO_APPROVAL_THRESHOLD_BWP)
        self.assertTrue(self._can_approve(po, self.fm_raiser))

    # -- claims unchanged ----------------------------------------------------

    def test_claims_senior_can_approve_large_claims_po_they_raised(self):
        """Claims POs are single-step (no CFO, no SoD). A claims senior who
        raised a large claims PO still sees Approve — unchanged."""
        po = self._pending_fm_po(
            Decimal('15000.00'), raiser=self.claims_mgr,
            department=PurchaseOrder.Department.CLAIMS,
        )
        self.assertTrue(self._can_approve(po, self.claims_mgr))

    # -- CFO leg (the code path this change added: _cfo_leg_sod_ok) -----------

    def test_creator_cfo_cannot_cfo_approve(self):
        """A CFO who RAISED a >=10k operational PO must not see Approve at the
        CFO leg; an independent CFO does."""
        po = self._pending_cfo_po(Decimal('15000.00'), raiser=self.cfo_a,
                                  fm_approver=self.fm_other)
        self.assertEqual(po.status, PurchaseOrder.Status.PENDING_CFO_APPROVAL)
        self.assertFalse(self._can_approve(po, self.cfo_a))   # creator barred
        self.assertTrue(self._can_approve(po, self.cfo_b))    # independent CFO

    def test_fm_approver_cannot_also_cfo_approve(self):
        """The person who FM-approved a PO must not also CFO-approve it (a CFO
        title can do both legs, so the SoD bar is what stops the double-sign)."""
        po = self._pending_cfo_po(Decimal('15000.00'), raiser=self.fm_raiser,
                                  fm_approver=self.cfo_b)
        self.assertEqual(po.fm_approved_by_id, self.cfo_b.pk)
        self.assertFalse(self._can_approve(po, self.cfo_b))   # was the FM-approver
        self.assertTrue(self._can_approve(po, self.cfo_a))    # independent CFO

    # -- reject rides on authority, NOT on the SoD-tightened approve flag ----

    def test_raiser_can_still_reject_large_operational_po(self):
        """The raiser loses Approve (SoD) but KEEPS Reject — reject has no SoD
        bar. can_reject must not collapse when can_approve is tightened."""
        po = self._pending_fm_po(Decimal('15000.00'), raiser=self.fm_raiser)
        self.assertFalse(self._can_approve(po, self.fm_raiser))
        self.assertTrue(self._can_reject(po, self.fm_raiser))

    def test_non_approver_cannot_reject(self):
        """A user with no approval authority over the leg sees neither button."""
        po = self._pending_fm_po(Decimal('15000.00'), raiser=self.fm_raiser)
        # claims_mgr has no FM authority over an OPERATIONAL PO.
        self.assertFalse(self._can_approve(po, self.claims_mgr))
        self.assertFalse(self._can_reject(po, self.claims_mgr))

    # -- the approvals INBOX (mobile Approvals screen + desktop /my-approvals) -

    def test_raiser_large_op_po_absent_from_own_approvals_inbox(self):
        """The reported bug: the FM raiser's own >=10k op PO must NOT appear in
        their approvals list or badge count (both fed core/approvals_views), but
        an independent FM sees it."""
        po = self._pending_fm_po(Decimal('15000.00'), raiser=self.fm_raiser)
        self.assertNotIn(str(po.pk), self._po_item_ids(self.fm_raiser))
        self.assertEqual(self._po_count(self.fm_raiser), 0)
        self.assertIn(str(po.pk), self._po_item_ids(self.fm_other))
        self.assertEqual(self._po_count(self.fm_other), 1)

    # -- the serializer flag matches what the SERVICE actually does (L2/L6) ---

    def test_serializer_flag_matches_fm_service_outcome(self):
        """Pin the 'mirror': can_approve=False for the raiser because fm_approve
        really 400s; can_approve=True for an independent FM because it succeeds."""
        po = self._pending_fm_po(Decimal('15000.00'), raiser=self.fm_raiser)
        self.assertFalse(self._can_approve(po, self.fm_raiser))
        with self.assertRaises(ValidationError):
            fm_approve(po, self.fm_raiser)          # the dead-end the button hid
        po.refresh_from_db()
        self.assertTrue(self._can_approve(po, self.fm_other))
        po = fm_approve(po, self.fm_other)          # independent FM: succeeds
        self.assertEqual(po.status, PurchaseOrder.Status.PENDING_CFO_APPROVAL)

    def test_serializer_flag_matches_cfo_service_outcome(self):
        """Same pin at the CFO leg: the creator's can_approve=False tracks a real
        cfo_approve 400."""
        po = self._pending_cfo_po(Decimal('15000.00'), raiser=self.cfo_a,
                                  fm_approver=self.fm_other)
        self.assertFalse(self._can_approve(po, self.cfo_a))
        with self.assertRaises(ValidationError):
            cfo_approve(po, self.cfo_a)             # creator barred at CFO leg
