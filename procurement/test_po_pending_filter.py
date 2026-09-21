"""
procurement/test_po_pending_filter.py — the FM queue and the Claims queue
must filter apart.

Kago Tshutlhedi (Finance Manager) 2026-07-25: with 5 000+ POs (Claims raises
hundreds a day), the "Pending FM" filter was unusable — the operational leg of
a finance PO and the operational leg of a claims PO both sit in the single
PENDING_FM_APPROVAL status, so one filter returned both approvers' queues.

Two ?status= aliases split them; the raw status still means "both".
"""
from datetime import date

from django.contrib.auth.models import User
from rest_framework.test import APIClient, APITestCase

from billing.models import Contact
from core.models import Company, Currency, UserProfile
from procurement.models import PurchaseOrder


class PendingApprovalFilterSplitTest(APITestCase):

    @classmethod
    def setUpTestData(cls):
        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        cls.company = Company.objects.create(code='TESTFLT', name='PO Filter Test Co.')
        cls.supplier = Contact.objects.create(
            name='Panel Beaters', contact_type='vendor', company=cls.company)
        cls.user = User.objects.create_user('fltroot', password='x', is_superuser=True)
        UserProfile.objects.create(user=cls.user, title=UserProfile.Title.CFO, is_active=True)

        def mk(department, status, number):
            po = PurchaseOrder.objects.create(
                department=department, supplier=cls.supplier, company=cls.company,
                issue_date=date(2026, 7, 25), created_by=cls.user,
                po_number=number)
            # status is set post-create so the model's own default/save path
            # doesn't reset it.
            PurchaseOrder.objects.filter(pk=po.pk).update(status=status)
            return po

        S = PurchaseOrder.Status
        D = PurchaseOrder.Department
        cls.claims_pending  = mk(D.CLAIMS, S.PENDING_FM_APPROVAL, 'PO-CLM-2026-900001')
        cls.claims_pending2 = mk(D.CLAIMS, S.PENDING_FM_APPROVAL, 'PO-CLM-2026-900002')
        cls.admin_pending   = mk(D.ADMIN,  S.PENDING_FM_APPROVAL, 'PO-ADM-2026-900001')
        cls.hr_pending      = mk(D.HR,     S.PENDING_FM_APPROVAL, 'PO-HR-2026-900001')
        cls.claims_approved = mk(D.CLAIMS, S.APPROVED,            'PO-CLM-2026-900003')
        cls.admin_cfo_leg   = mk(D.ADMIN,  S.PENDING_CFO_APPROVAL, 'PO-ADM-2026-900002')

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _numbers(self, status_value):
        res = self.client.get('/api/v1/purchase-orders/',
                              {'status': status_value, 'page_size': 100})
        self.assertEqual(res.status_code, 200, res.content[:300])
        return {row['po_number'] for row in res.json()['results']}

    def test_fm_alias_excludes_claims(self):
        """The FM's own queue: operational departments only."""
        self.assertEqual(
            self._numbers('pending_fm_finance'),
            {'PO-ADM-2026-900001', 'PO-HR-2026-900001'},
        )

    def test_claims_alias_returns_only_claims(self):
        """The Claims Manager's queue: claims department only."""
        self.assertEqual(
            self._numbers('pending_claims_approval'),
            {'PO-CLM-2026-900001', 'PO-CLM-2026-900002'},
        )

    def test_raw_status_still_returns_both_legs(self):
        """Existing callers passing the DB value keep the old behaviour."""
        self.assertEqual(
            self._numbers('pending_fm_approval'),
            {'PO-CLM-2026-900001', 'PO-CLM-2026-900002',
             'PO-ADM-2026-900001', 'PO-HR-2026-900001'},
        )

    def test_aliases_do_not_leak_other_statuses(self):
        """An approved claims PO and a CFO-leg PO stay out of both aliases."""
        both = self._numbers('pending_fm_finance') | self._numbers('pending_claims_approval')
        self.assertNotIn('PO-CLM-2026-900003', both)
        self.assertNotIn('PO-ADM-2026-900002', both)

    def test_unknown_status_still_filters_literally(self):
        """A bogus status must return nothing, not silently fall back to all."""
        self.assertEqual(self._numbers('not_a_status'), set())
