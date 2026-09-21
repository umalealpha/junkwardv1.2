"""
procurement/test_claims_po_approver.py — the CFO is OUT of claims POs.

CFO directive 2026-07-19: claims purchase orders are approvable ONLY by the
claims seniors (Claims Manager / Team Leader / Senior Claims Associate) — not
the CFO, and not via the generic superuser backstop, at ANY amount. The CFO
authorises OPERATIONAL POs only. This locks `_can_approve_po` against the
recurring drift back to including the CFO in the claims approver set.
"""
from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase

from billing.models import Contact
from core.models import Company, Currency, UserProfile
from procurement.models import PurchaseOrder
from procurement.services import _can_approve_po


class ClaimsPOApproverRuleTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        cls.company = Company.objects.create(code='TESTPO', name='Claims PO Test Co.')
        cls.supplier = Contact.objects.create(
            name='Panel Beaters', contact_type='vendor', company=cls.company)

        def mk(username, title, is_super=False):
            u = User.objects.create_user(username, password='x', is_superuser=is_super)
            UserProfile.objects.create(user=u, title=title, is_active=True)
            return u

        cls.claims_mgr = mk('cmgr', UserProfile.Title.CLAIMS_MANAGER)
        cls.claims_snr = mk('csnr', UserProfile.Title.SENIOR_CLAIMS_ASSOCIATE)
        cls.cfo = mk('cfotest', UserProfile.Title.CFO)
        cls.fm = mk('fmtest', UserProfile.Title.FINANCE_MANAGER)
        # a superuser whose title is irrelevant — the point is superuser must
        # NOT be a claims-PO backstop.
        cls.superu = mk('roottest', UserProfile.Title.ACCOUNTANT, is_super=True)

    def _po(self, department):
        return PurchaseOrder.objects.create(
            department=department, supplier=self.supplier, company=self.company,
            issue_date=date(2026, 7, 19), created_by=self.claims_mgr)

    def test_claims_po_approved_only_by_claims_seniors(self):
        po = self._po(PurchaseOrder.Department.CLAIMS)
        self.assertTrue(_can_approve_po(self.claims_mgr, po))
        self.assertTrue(_can_approve_po(self.claims_snr, po))
        # CFO is OUT of claims POs, any amount (CFO 2026-07-19).
        self.assertFalse(_can_approve_po(self.cfo, po))
        # No generic superuser backstop on claims.
        self.assertFalse(_can_approve_po(self.superu, po))
        # An FM has no claims role.
        self.assertFalse(_can_approve_po(self.fm, po))

    def test_operational_po_still_allows_cfo_and_superuser(self):
        po = self._po(PurchaseOrder.Department.ADMIN)
        self.assertTrue(_can_approve_po(self.cfo, po))      # FM leg includes CFO
        self.assertTrue(_can_approve_po(self.fm, po))
        self.assertTrue(_can_approve_po(self.superu, po))   # superuser backstop
        # A claims senior has no authority on an operational PO.
        self.assertFalse(_can_approve_po(self.claims_snr, po))
