"""Salvage is recorded under Veritas Capital (VCM) only.

Kgosi bug 87a249f3 (2026-09-18): ensure that future salvage entries cannot be
created or recorded under AIDC or any company other than Veritas Capital.
"""
from __future__ import annotations

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APITestCase

from core.models import Company
from salvage.models import SalvageItem
from salvage.services import get_salvage_company, SALVAGE_COMPANY_CODE


class SalvageCompanyValidationTests(TestCase):
    """Test that Veritas is the only allowed salvage company."""

    @classmethod
    def setUpTestData(cls):
        cls.veritas = Company.objects.create(
            code=SALVAGE_COMPANY_CODE, name='Veritas Capital'
        )
        cls.adic = Company.objects.create(code='ADIC', name='Alpha Direct Insurance')

    def test_get_salvage_company_returns_veritas(self):
        """get_salvage_company() returns the VCM company."""
        company = get_salvage_company()
        self.assertEqual(company.code, 'VCM')
        self.assertEqual(company, self.veritas)

    def test_salvage_company_code_constant(self):
        """SALVAGE_COMPANY_CODE is 'VCM'."""
        self.assertEqual(SALVAGE_COMPANY_CODE, 'VCM')

    def test_create_with_explicit_veritas_company_succeeds(self):
        """Creating a salvage item with company=VCM succeeds."""
        item = SalvageItem.objects.create(
            part_name='Test engine',
            company=self.veritas,
        )
        self.assertEqual(item.company, self.veritas)
        self.assertEqual(item.company.code, 'VCM')

    def test_create_with_adic_succeeds_in_direct_db_call(self):
        """Direct DB create with ADIC works (REST API enforces at view layer)."""
        # This test documents that the model itself does NOT prevent non-VCM.
        # The enforcement is at the REST API layer via perform_create.
        item = SalvageItem.objects.create(
            part_name='Test engine',
            company=self.adic,
        )
        self.assertEqual(item.company, self.adic)
        self.assertEqual(item.company.code, 'ADIC')

    def test_get_salvage_company_raises_if_vcm_not_found(self):
        """get_salvage_company() raises if VCM doesn't exist."""
        self.veritas.delete()
        with self.assertRaises(Company.DoesNotExist):
            get_salvage_company()


class SalvageCreateApiTests(APITestCase):
    """Test REST API enforcement of Veritas-only salvage."""

    @classmethod
    def setUpTestData(cls):
        cls.veritas = Company.objects.create(
            code=SALVAGE_COMPANY_CODE, name='Veritas Capital'
        )
        cls.adic = Company.objects.create(code='ADIC', name='Alpha Direct Insurance')
        cls.user = User.objects.create_superuser(
            'yardmanager', 'yard@alphadirect.co.bw', 'x'
        )

    def setUp(self):
        self.client.force_authenticate(user=self.user)

    def test_create_with_no_company_sets_veritas(self):
        """POST without company parameter defaults to Veritas."""
        r = self.client.post(
            '/api/v1/salvage-items/',
            {'part_name': 'Engine block', 'condition': 'fair', 'status': 'available'},
            format='json',
        )
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(str(r.data['company']), str(self.veritas.id))

    def test_create_with_explicit_veritas_succeeds(self):
        """POST with company=Veritas ID succeeds."""
        r = self.client.post(
            '/api/v1/salvage-items/',
            {
                'part_name': 'Engine block',
                'condition': 'fair',
                'status': 'available',
                'company': str(self.veritas.id),
            },
            format='json',
        )
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(str(r.data['company']), str(self.veritas.id))

    def test_create_with_adic_returns_400(self):
        """POST with company=ADIC returns 400."""
        r = self.client.post(
            '/api/v1/salvage-items/',
            {
                'part_name': 'Engine block',
                'condition': 'fair',
                'status': 'available',
                'company': str(self.adic.id),
            },
            format='json',
        )
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn('Veritas', str(r.data))
        # Verify no item was created.
        self.assertEqual(SalvageItem.objects.count(), 0)

    def test_create_with_nonexistent_company_returns_400(self):
        """POST with a non-existent company ID returns 400."""
        r = self.client.post(
            '/api/v1/salvage-items/',
            {
                'part_name': 'Engine block',
                'condition': 'fair',
                'status': 'available',
                'company': '00000000-0000-0000-0000-000000000000',
            },
            format='json',
        )
        # This will be a 400 from DRF's ForeignKey validation before our check
        # or from our check if the FK resolves to None.
        self.assertIn(r.status_code, [400, 422])

    def test_two_creates_both_land_in_veritas(self):
        """Two creates without company both land in Veritas."""
        body = {'part_name': 'Differential', 'condition': 'good', 'status': 'available'}
        r1 = self.client.post('/api/v1/salvage-items/', body, format='json')
        r2 = self.client.post('/api/v1/salvage-items/', body, format='json')
        self.assertEqual(r1.status_code, 201)
        self.assertEqual(r2.status_code, 201)
        self.assertEqual(r1.data['company'], r2.data['company'])
        self.assertEqual(str(r1.data['company']), str(self.veritas.id))


class SalvageEntityAccessTests(APITestCase):
    """Forcing Veritas must not widen reach: a salvage user without a Veritas
    grant is refused, not silently given a Veritas row."""

    @classmethod
    def setUpTestData(cls):
        from core.models import UserCompanyAccess
        cls.veritas = Company.objects.create(code=SALVAGE_COMPANY_CODE, name='Veritas Capital')
        cls.adic = Company.objects.create(code='ADIC', name='Alpha Direct Insurance')
        cls.user = User.objects.create_user('walled', 'walled@alphadirect.co.bw', 'x')
        UserCompanyAccess.objects.create(user=cls.user, company=cls.adic,
                                         can_view=True, can_write=True)

    def test_a_user_without_veritas_access_is_refused(self):
        from unittest.mock import patch
        self.client.force_authenticate(user=self.user)
        with patch('salvage.permissions.user_can_access_salvage', return_value=True):
            r = self.client.post(
                '/api/v1/salvage-items/',
                {'part_name': 'Door', 'condition': 'fair', 'status': 'available'},
                format='json')
        self.assertEqual(r.status_code, 403, r.content)
        self.assertEqual(SalvageItem.objects.count(), 0)
