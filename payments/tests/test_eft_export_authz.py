"""
Authorization gate on the EFT batch export (FNB BOL file).

Security regression test (2026-08-21): EFTBatchExportView had NO
permission_classes and fell back to the DRF default (IsAuthenticated), so ANY
authenticated staffer could download the bank file — which carries UNMASKED
vendor bank account numbers. It is now gated by CanExportEftBatch (outbound-
payment makers + finance management), matching the other outbound-payment / FNB
views.

Gate contract:
  * blocked (403)  — operational / untitled staff; the checker-only Finance
                     Manager (approves at the bank, must not originate — SoD).
  * allowed (past permission → 400 "payment_ids is required") — outbound-payment
                     makers (Financial Controller / Senior Accountant /
                     Accountant), the CFO, administrators, superusers.

A blocked caller is stopped by the permission layer (403) before post() runs; an
allowed caller reaches post() and hits the input-validation error (400). That
403-vs-400 split is what proves the gate, with no payment fixtures needed.
"""
from __future__ import annotations

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from core.models import UserProfile
from payments.api_views import EFTBatchExportView

EFT_EXPORT_URL = '/api/v1/payments/eft-export/'


def _user(username, title=None, *, is_superuser=False, is_administrator=False):
    u = User.objects.create_user(username=username, password='x',
                                 is_superuser=is_superuser, is_staff=is_superuser)
    if title is not None or is_administrator:
        UserProfile.objects.update_or_create(
            user=u,
            defaults={
                'role': UserProfile.Role.ACCOUNTANT,
                'title': title or UserProfile.Title.OPERATIONS,
                'is_active': True,
                'is_administrator': is_administrator,
            },
        )
    return u


class EftExportAuthzTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.view = EFTBatchExportView.as_view()

    def _post(self, user, body=None):
        request = self.factory.post(EFT_EXPORT_URL, body or {}, format='json')
        if user is not None:
            force_authenticate(request, user=user)
        return self.view(request)

    # --- blocked ---------------------------------------------------------

    def test_unauthenticated_is_denied(self):
        resp = self._post(None, {'payment_ids': ['x'], 'source_account_number': '62'})
        self.assertIn(resp.status_code, (401, 403))

    def test_operations_staff_cannot_export(self):
        """Plain authenticated non-finance user → 403 (the reported gap)."""
        u = _user('ops1', UserProfile.Title.OPERATIONS)
        resp = self._post(u, {'payment_ids': ['x'], 'source_account_number': '62'})
        self.assertEqual(resp.status_code, 403)

    def test_finance_manager_checker_cannot_export(self):
        """Checker-only Finance Manager must not originate the file (SoD)."""
        u = _user('fm1', UserProfile.Title.FINANCE_MANAGER)
        resp = self._post(u, {'payment_ids': ['x'], 'source_account_number': '62'})
        self.assertEqual(resp.status_code, 403)

    # --- allowed (pass permission → 400 on missing input) ----------------

    def test_maker_can_export(self):
        """Financial Controller is an outbound-payment maker → passes the gate."""
        u = _user('fc1', UserProfile.Title.FINANCIAL_CONTROLLER)
        resp = self._post(u, {})  # no payment_ids → 400 proves gate passed
        self.assertEqual(resp.status_code, 400)

    def test_accountant_maker_can_export(self):
        u = _user('acc1', UserProfile.Title.ACCOUNTANT)
        resp = self._post(u, {})
        self.assertEqual(resp.status_code, 400)

    def test_cfo_can_export(self):
        """CFO is the control owner (title=cfo, not a maker title, not a
        superuser) — must still be allowed through."""
        u = _user('cfo1', UserProfile.Title.CFO)
        resp = self._post(u, {})
        self.assertEqual(resp.status_code, 400)

    def test_administrator_can_export(self):
        u = _user('admin1', UserProfile.Title.OPERATIONS, is_administrator=True)
        resp = self._post(u, {})
        self.assertEqual(resp.status_code, 400)

    def test_superuser_can_export(self):
        u = _user('root1', is_superuser=True)
        resp = self._post(u, {})
        self.assertEqual(resp.status_code, 400)
