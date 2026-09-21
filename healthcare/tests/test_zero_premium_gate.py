"""Regression: a group-health quote worth nothing must not be issuable.

Bug (Tlamelo Chimidza 2026-07-28, report 03a2b875): a member schedule whose
plan/tier column wasn't recognised left every member excluded, so the quote
priced to zero. Zero then passed submit, approve AND invoice, because every gate
only counted members and never looked at the money — a BWP 0.00 health invoice
could be raised and given an invoice number.

Synthetic members only — never real member data.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from rest_framework.test import APIClient, APITestCase

from healthcare.models import HealthQuote, HealthQuoteMember


def _member(quote, name, premium):
    excl = Decimal(premium)
    vat = (excl * Decimal('0.14')).quantize(Decimal('0.01'))
    return HealthQuoteMember.objects.create(
        quote=quote, full_name=name, member_type='main', gender='M',
        date_of_birth=datetime.date(1990, 1, 1), age=36, age_band='35-39',
        tier='AD_ESSENTIAL', premium_excl=excl, vat=vat, premium_incl=excl + vat)


class ZeroPremiumQuoteGateTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('zqgate', 'zqgate@alphadirect.co.bw', 'x')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _quote(self, ref, premiums, status='draft'):
        q = HealthQuote.objects.create(client_name='Test Employer', ref=ref,
                                       created_by=self.user, status=status)
        for i, p in enumerate(premiums):
            _member(q, f'Test Member {i}', p)
        total = sum(Decimal(p) for p in premiums)
        q.gross_excl = q.subtotal_excl = total
        q.vat = (total * Decimal('0.14')).quantize(Decimal('0.01'))
        q.total_incl = q.subtotal_excl + q.vat
        q.discount_excl = Decimal('0')
        q.save()
        return q

    # --- the bug -------------------------------------------------------------
    def test_zero_premium_quote_cannot_be_submitted(self):
        q = self._quote('ZQ-0001', ['0', '0'])
        r = self.client.post(f'/api/v1/health/quotes/{q.id}/submit/')
        self.assertEqual(r.status_code, 400)
        self.assertIn('totals 0.00', r.json()['detail'])
        self.assertIn('Tier', r.json()['detail'])
        q.refresh_from_db()
        self.assertEqual(q.status, 'draft')

    def test_zero_premium_quote_cannot_be_approved(self):
        q = self._quote('ZQ-0002', ['0'], status='submitted')
        r = self.client.post(f'/api/v1/health/quotes/{q.id}/approve/')
        self.assertEqual(r.status_code, 400)
        self.assertIn('totals 0.00', r.json()['detail'])
        q.refresh_from_db()
        self.assertEqual(q.status, 'submitted')

    def test_zero_premium_quote_cannot_be_invoiced(self):
        q = self._quote('ZQ-0003', ['0'], status='approved')
        r = self.client.post(f'/api/v1/health/quotes/{q.id}/invoice/')
        self.assertEqual(r.status_code, 400)
        q.refresh_from_db()
        self.assertFalse(q.invoice_no, 'a zero-premium quote must not get an invoice number')

    def test_message_names_the_members_that_priced_to_nothing(self):
        q = self._quote('ZQ-0004', ['0', '0', '0'])
        r = self.client.post(f'/api/v1/health/quotes/{q.id}/submit/')
        detail = r.json()['detail']
        self.assertIn('3 of 3 member(s)', detail)
        self.assertIn('Test Member 0', detail)

    # --- must not break the normal path -------------------------------------
    def test_a_priced_quote_still_submits(self):
        q = self._quote('ZQ-0005', ['281', '350'])
        r = self.client.post(f'/api/v1/health/quotes/{q.id}/submit/')
        self.assertEqual(r.status_code, 200, r.content[:300])
        q.refresh_from_db()
        self.assertEqual(q.status, 'submitted')

    def test_a_partly_zero_quote_still_submits(self):
        """One unpriced life is a warning, not a block — the total is still real."""
        q = self._quote('ZQ-0006', ['281', '0'])
        r = self.client.post(f'/api/v1/health/quotes/{q.id}/submit/')
        self.assertEqual(r.status_code, 200, r.content[:300])

    def test_empty_quote_keeps_its_own_message(self):
        q = HealthQuote.objects.create(client_name='Empty Co', ref='ZQ-0007',
                                       created_by=self.user)
        r = self.client.post(f'/api/v1/health/quotes/{q.id}/submit/')
        self.assertEqual(r.status_code, 400)
        self.assertIn('Add members', r.json()['detail'])
