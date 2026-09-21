"""
procurement/test_claims_analytics.py — tests for the Claims-PO analytics
endpoint (GET /api/v1/reports/claims-po-analytics/).

Covers:
  * empty state — zero claims POs never divides by zero / never 500s
  * metric math — spend, top suppliers, excess (negative lines),
    repairer-vs-parts split, turnaround from ClaimsAssessment.po_results
  * company scoping via ?company=<code>
  * the last_emailed_at guard — the sent-status block must not crash whether
    or not that (parallel-change) field/column exists
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from billing.models import Contact
from core.models import Company, Currency

from .claims_models import ClaimsAssessment
from .models import PurchaseOrder, PurchaseOrderLine

URL = '/api/v1/reports/claims-po-analytics/'


class ClaimsPOAnalyticsTests(TestCase):

    def setUp(self):
        Currency.objects.get_or_create(code='BWP', defaults={'name': 'Pula', 'symbol': 'P'})
        self.company = Company.objects.create(code='ADIC', name='Alpha Direct')
        self.user = User.objects.create_user('analytics_user', password='x')
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.repairer = Contact.objects.create(name='Acme Panelbeaters', contact_type='vendor')
        self.parts = Contact.objects.create(name='Parts World', contact_type='vendor')

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _po(self, supplier, amount, *, excess=None, company=None,
            issue_date=None, status=PurchaseOrder.Status.DRAFT):
        po = PurchaseOrder.objects.create(
            department=PurchaseOrder.Department.CLAIMS,
            supplier=supplier,
            company=company or self.company,
            issue_date=issue_date or date.today(),
            created_by=self.user,
            status=status,
        )
        PurchaseOrderLine.objects.create(
            purchase_order=po, description='Repairs',
            quantity=Decimal('1'), unit_price=Decimal(str(amount)),
            line_total=Decimal(str(amount)),
        )
        if excess is not None:
            PurchaseOrderLine.objects.create(
                purchase_order=po, description='Less: excess',
                quantity=Decimal('1'), unit_price=-Decimal(str(excess)),
                line_total=-Decimal(str(excess)),
            )
        po.recalculate_totals()
        # A fixture may create a terminal-status PO (e.g. cancelled, to prove
        # the endpoint excludes it); the immutability guard blocks re-saving
        # those, so use the same sanctioned bypass the service layer uses.
        po._allow_status_transition = True
        po.save()
        return po

    # ------------------------------------------------------------------
    # tests
    # ------------------------------------------------------------------

    def test_requires_auth(self):
        resp = APIClient().get(URL)
        self.assertIn(resp.status_code, (401, 403))

    def test_empty_state_never_500s(self):
        resp = self.client.get(URL)
        self.assertEqual(resp.status_code, 200)
        t = resp.json()['totals']
        self.assertEqual(t['po_count'], 0)
        self.assertEqual(t['total_spend'], 0.0)
        self.assertEqual(t['excess_avg'], 0.0)
        self.assertIsNone(t['avg_turnaround_days'])
        self.assertEqual(len(resp.json()['monthly']), 12)

    def test_metrics(self):
        # Repairer PO: 10,000 work less 2,000 excess => total 8,000
        po1 = self._po(self.repairer, 10000, excess=2000)
        # Parts PO: 5,000, no excess
        self._po(self.parts, 5000)
        # Cancelled PO must not count as spend
        self._po(self.parts, 99999, status=PurchaseOrder.Status.CANCELLED)

        # Assessment that generated po1 (turnaround >= 0 days)
        ClaimsAssessment.objects.create(
            company=self.company,
            status=ClaimsAssessment.Status.POS_CREATED,
            po_results=[{'id': str(po1.pk), 'po_number': po1.po_number}],
        )

        resp = self.client.get(URL)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        t = body['totals']

        self.assertEqual(t['po_count'], 2)
        self.assertAlmostEqual(t['total_spend'], 13000.0)
        self.assertAlmostEqual(t['spend_this_month'], 13000.0)

        # Excess — reported as positive magnitudes
        self.assertAlmostEqual(t['excess_total'], 2000.0)
        self.assertAlmostEqual(t['excess_avg'], 2000.0)
        self.assertEqual(t['excess_po_count'], 1)

        # Turnaround — same-day creation => 0.0 days over 1 sample
        self.assertEqual(t['turnaround_sample'], 1)
        self.assertAlmostEqual(t['avg_turnaround_days'], 0.0)

        # Split — po1 has a negative line => repairer; the other => parts
        self.assertEqual(body['split']['repairer']['count'], 1)
        self.assertAlmostEqual(body['split']['repairer']['value'], 8000.0)
        self.assertEqual(body['split']['parts']['count'], 1)
        self.assertAlmostEqual(body['split']['parts']['value'], 5000.0)

        # Top suppliers — repairer first (8k > 5k), cancelled PO excluded
        suppliers = body['top_suppliers']
        self.assertEqual(suppliers[0]['supplier'], 'Acme Panelbeaters')
        self.assertAlmostEqual(suppliers[0]['total'], 8000.0)
        self.assertEqual(len(suppliers), 2)

        # Monthly trend — current month carries the spend
        self.assertAlmostEqual(body['monthly'][-1]['spend'], 13000.0)
        self.assertEqual(body['monthly'][-1]['count'], 2)

        # Sent-status guard — must be present and never crash, whether or not
        # the parallel-change last_emailed_at field exists yet.
        self.assertIn('sent_known', t)
        if not t['sent_known']:
            self.assertEqual(t['sent'], 0)
            self.assertEqual(t['unsent'], 0)

    def test_company_scoping(self):
        other = Company.objects.create(code='OTHR', name='Other Co')
        self._po(self.repairer, 1000)                    # ADIC
        self._po(self.parts, 7777, company=other)        # OTHR

        resp = self.client.get(URL, {'company': 'ADIC'})
        self.assertEqual(resp.status_code, 200)
        t = resp.json()['totals']
        self.assertEqual(t['po_count'], 1)
        self.assertAlmostEqual(t['total_spend'], 1000.0)
