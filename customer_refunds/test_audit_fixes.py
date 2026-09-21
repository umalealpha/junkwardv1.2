"""customer_refunds/test_audit_fixes.py — Fable 5.1 audit fix M6 (2026-09-02).

LOADED to FNB is not PAID: nothing leaves the bank until the CFO authorises it
there. Recording a loaded refund as paid (which credits the policy back in
Graphite) therefore needs the same money authority as a manual payment.

Run: manage.py test customer_refunds.test_audit_fixes
"""
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import Group, User
from django.test import TestCase
from rest_framework.test import APIClient

from customer_refunds.models import CustomerRefund


class MarkPaidFromLoadedTests(TestCase):
    def _refund(self, **over):
        d = dict(segment=CustomerRefund.Segment.MIS, graphite_ref='RFND-000123',
                 policy_number='MIS2024079662', customer_name='Tiny Maswabi',
                 refund_amount=Decimal('474.00'), currency='BWP',
                 bank_name='FNB Botswana', account_last4='6200',
                 status=CustomerRefund.Status.FNB_LOADED)
        d.update(over)
        return CustomerRefund.objects.create(**d)

    def _area_user(self, name='reviewer'):
        u = User.objects.create_user(name, email=f'{name}@example.com')
        g, _ = Group.objects.get_or_create(name='refund_area_mis')
        u.groups.add(g)
        return u

    def test_an_area_reviewer_without_money_authority_cannot_mark_a_loaded_refund_paid(self):
        r = self._refund()
        c = APIClient()
        c.force_authenticate(self._area_user())
        resp = c.post(f'/api/v1/customer-refunds/{r.pk}/mark-paid/', {}, format='json')
        self.assertEqual(resp.status_code, 403, resp.content)
        r.refresh_from_db()
        self.assertEqual(r.status, CustomerRefund.Status.FNB_LOADED)

    def test_money_authority_may_still_record_it_paid(self):
        """Pins that the gate admits what it should."""
        approver = self._area_user('approver')
        r = self._refund(finance_approved_by=approver)
        payer = self._area_user('payer')
        g, _ = Group.objects.get_or_create(name='refund_money')
        payer.groups.add(g)
        c = APIClient()
        c.force_authenticate(payer)
        with mock.patch('customer_refunds.api_views.post_refund_back_to_graphite',
                        return_value={'ok': True}):
            resp = c.post(f'/api/v1/customer-refunds/{r.pk}/mark-paid/', {}, format='json')
        self.assertEqual(resp.status_code, 200, resp.content)
        r.refresh_from_db()
        self.assertEqual(r.status, CustomerRefund.Status.PAID)

    def test_the_same_person_who_approved_it_cannot_also_record_it_paid(self):
        approver = self._area_user('approver2')
        g, _ = Group.objects.get_or_create(name='refund_money')
        approver.groups.add(g)
        r = self._refund(finance_approved_by=approver)
        c = APIClient()
        c.force_authenticate(approver)
        resp = c.post(f'/api/v1/customer-refunds/{r.pk}/mark-paid/', {}, format='json')
        self.assertEqual(resp.status_code, 403, resp.content)
