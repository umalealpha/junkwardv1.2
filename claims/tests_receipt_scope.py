"""Entity-isolation checks for subrogation receipts (Fable review, fixes 1 & 2).

Proves a company-scoped user cannot see or bank a receipt against another
company's case. Without the perform_create IDOR guard the write test 201s;
without the queryset scope the read test leaks the other entity's receipt.
"""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from claims.models import Subrogation, SubrogationReceipt
from core.models import Company, UserCompanyAccess


class ReceiptScopeTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.coA = Company.objects.create(code='ADIC', name='Alpha Direct')
        cls.coB = Company.objects.create(code='RSA', name='ADRisk')
        cls.creator = User.objects.create(username='sys', is_superuser=True)
        # A user granted ONLY company B.
        cls.userB = User.objects.create(username='scoped_b')
        UserCompanyAccess.objects.create(user=cls.userB, company=cls.coB, can_view=True, can_write=True)
        cls.subA = Subrogation.objects.create(
            claim_reference='A-1', company=cls.coA, third_party_name='TP A',
            claim_paid_amount=Decimal('0'), expected_recovery=Decimal('100'),
            created_by=cls.creator)
        cls.subB = Subrogation.objects.create(
            claim_reference='B-1', company=cls.coB, third_party_name='TP B',
            claim_paid_amount=Decimal('0'), expected_recovery=Decimal('100'),
            created_by=cls.creator)
        # An existing receipt on each case.
        SubrogationReceipt.objects.create(subrogation=cls.subA, amount=Decimal('10'),
            received_date=date(2026, 1, 1), created_by=cls.creator)
        SubrogationReceipt.objects.create(subrogation=cls.subB, amount=Decimal('20'),
            received_date=date(2026, 1, 1), created_by=cls.creator)

    def test_scoped_user_cannot_bank_against_another_companys_case(self):
        self.client.force_authenticate(self.userB)
        r = self.client.post(reverse('subrogation-receipt-list'), {
            'subrogation': str(self.subA.id), 'amount': '50', 'received_date': '2026-02-01',
        }, format='json')
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)
        # nothing was written
        self.assertEqual(self.subA.receipts.count(), 1)

    def test_scoped_user_can_bank_against_their_own_company(self):
        self.client.force_authenticate(self.userB)
        r = self.client.post(reverse('subrogation-receipt-list'), {
            'subrogation': str(self.subB.id), 'amount': '50', 'received_date': '2026-02-01',
        }, format='json')
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        self.assertEqual(self.subB.receipts.count(), 2)

    def test_receipt_list_is_scoped_to_the_users_company(self):
        self.client.force_authenticate(self.userB)
        r = self.client.get(reverse('subrogation-receipt-list'))
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        refs = {row['claim_reference'] for row in r.json()['results']}
        self.assertEqual(refs, {'B-1'})   # company A's receipt is NOT visible

    def test_superuser_sees_all_and_can_bank_anywhere(self):
        self.client.force_authenticate(self.creator)
        r = self.client.get(reverse('subrogation-receipt-list'))
        refs = {row['claim_reference'] for row in r.json()['results']}
        self.assertEqual(refs, {'A-1', 'B-1'})

    def test_summary_kpis_and_scope(self):
        # Both cases are open (100 recoverable, 10/20 recovered) — superuser
        # sees the aggregate across both companies.
        self.client.force_authenticate(self.creator)
        r = self.client.get(reverse('subrogation-summary'))
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        d = r.json()
        self.assertEqual(d['open_count'], 2)
        self.assertEqual(d['recovered_total'], '30.00')          # 10 + 20
        self.assertEqual(d['open_recoverable'], '170.00')        # (100-10)+(100-20)
        # A scoped user's summary only covers their own company.
        self.client.force_authenticate(self.userB)
        d2 = self.client.get(reverse('subrogation-summary')).json()
        self.assertEqual(d2['open_count'], 1)
        self.assertEqual(d2['recovered_total'], '20.00')
