"""Every purchase order must say why, at any value.

CFO 2026-08-11, after PO-ADM-2026-000045 (an iPad, P4,114.26) reached him with the
justification blank: the rule only bit above P5,000, so anything smaller arrived with
no recorded reason and the only explanation was "kindly approve the attached" in a
covering email. An approved PO is immutable — the reason cannot be added afterwards,
so it has to be required before submission or it is lost for good.

Built on POCommitmentJETest's fixture (company, accounts, fiscal period, distinct
submitter/fm/cfo users, `_make_po`) rather than a hand-rolled one — the PO fixture is
involved and duplicating it is how two tests drift apart.
"""
from decimal import Decimal

from django.core.exceptions import ValidationError

from procurement import services
from procurement.models import PurchaseOrder
from procurement.tests import POCommitmentJETest


class JustificationRequiredTests(POCommitmentJETest):
    """Reuses the parent's fixture. Its own draft builder, because the parent's
    `_make_po` submits AND fm-approves — and this test needs a PO still in draft."""

    def _draft(self, total=Decimal('4114.26'), justification=''):
        import datetime as _dt
        from billing.models import Contact
        from procurement.models import PurchaseOrderLine
        supplier = Contact.objects.create(
            name='New Technology Group', contact_type='vendor',
            is_related_party=False, company=self.company)
        po = PurchaseOrder.objects.create(
            po_number=f'PO-JUST-{PurchaseOrder.objects.count() + 1:04d}',
            issue_date=_dt.date.today(), department='admin', supplier=supplier,
            currency_code_id='BWP', exchange_rate=Decimal('1.0'),
            company=self.company, created_by=self.submitter,
            status=PurchaseOrder.Status.DRAFT, justification=justification)
        PurchaseOrderLine.objects.create(
            purchase_order=po, account=self.acct_office,
            description='Apple iPad 9th generation', quantity=Decimal('1'),
            unit_price=total, line_total=total)
        po.recalculate_totals()
        po.save()
        return po

    def test_a_small_po_with_no_reason_is_refused(self):
        """The iPad case: P4,114.26 slipped under the old P5,000 threshold."""
        po = self._draft()
        with self.assertRaises(ValidationError) as caught:
            services.submit_for_approval(po, self.submitter)
        self.assertIn('why', ' '.join(caught.exception.messages).lower())
        po.refresh_from_db()
        self.assertEqual(po.status, PurchaseOrder.Status.DRAFT,
                         'it must not reach an approver without a reason')

    def test_whitespace_is_not_a_reason(self):
        po = self._draft(justification='   ')
        with self.assertRaises(ValidationError):
            services.submit_for_approval(po, self.submitter)

    def test_a_small_po_with_a_reason_goes_through(self):
        po = self._draft(justification='For the EXCO board.')
        services.submit_for_approval(po, self.submitter)
        po.refresh_from_db()
        self.assertEqual(po.status, PurchaseOrder.Status.PENDING_FM_APPROVAL)

    def test_a_large_po_still_needs_one(self):
        po = self._draft(total=Decimal('80000.00'))
        with self.assertRaises(ValidationError):
            services.submit_for_approval(po, self.submitter)
