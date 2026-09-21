"""Salvage inventory edit + void — Kgosi Seboko, 18-Sep-2026.

"Please add a delete & edit feature to the Salvage/Inventory section
under operations. I want to be able to delete and/or edit inventory that
has been added in case of mistakes."

Straight delete would leave the intake JE dangling. So:
  * PATCH is honoured for the whitelisted 'typo' fields only;
  * DELETE is refused — the void action does it instead, keeping the row
    for audit and posting a reversing JE so the GL stays balanced;
  * a sold / disposed / scrapped row is frozen — voiding those would
    strand the sale JE and the buyer record.

Every test proves ONE promise. Revert the fix and the corresponding test
must go red — otherwise the test is theatre.
"""
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import User
from rest_framework.test import APITestCase

from core.models import Company
from salvage.models import SalvageItem


class SalvageEditTests(APITestCase):

    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(code='VCM', name='Veritas Capital')
        cls.user = User.objects.create_superuser(
            'yardmanager', 'yard@alphadirect.co.bw', 'x')

    def setUp(self):
        self.client.force_authenticate(user=self.user)
        self.item = SalvageItem.objects.create(
            part_name='Toyoto Corolla battery',  # deliberate typo — this
                                                 # is what Kgosi's flow fixes
            condition='fair',
            status='available',
            company=self.company,
        )

    def test_patch_fixes_a_typo(self):
        r = self.client.patch(
            f'/api/v1/salvage-items/{self.item.id}/',
            {'part_name': 'Toyota Corolla battery'},
            format='json',
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.item.refresh_from_db()
        self.assertEqual(self.item.part_name, 'Toyota Corolla battery')

    def test_patch_ignores_frozen_fields(self):
        """item_code is not on the whitelist — a caller trying to rewrite it
        gets the original code back."""
        original_code = self.item.item_code
        r = self.client.patch(
            f'/api/v1/salvage-items/{self.item.id}/',
            {'item_code': 'HACKED-9999', 'part_name': 'ok fixed'},
            format='json',
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.item.refresh_from_db()
        self.assertEqual(self.item.item_code, original_code)
        self.assertEqual(self.item.part_name, 'ok fixed')

    def test_patch_refuses_when_row_is_sold(self):
        self.item.status = 'sold'
        self.item.save(update_fields=['status'])
        r = self.client.patch(
            f'/api/v1/salvage-items/{self.item.id}/',
            {'part_name': 'trying to rewrite history'},
            format='json',
        )
        self.assertEqual(r.status_code, 400, r.content)


class SalvageVoidTests(APITestCase):

    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(code='VCM', name='Veritas Capital')
        cls.user = User.objects.create_superuser(
            'yardmanager', 'yard@alphadirect.co.bw', 'x')

    def setUp(self):
        self.client.force_authenticate(user=self.user)
        self.item = SalvageItem.objects.create(
            part_name='Wrong claim — booked in error',
            condition='fair',
            status='available',
            company=self.company,
        )

    def test_void_requires_a_reason(self):
        r = self.client.post(
            f'/api/v1/salvage-items/{self.item.id}/void/', {}, format='json',
        )
        self.assertEqual(r.status_code, 400, r.content)

    def test_void_flips_status_and_stamps_the_reason(self):
        r = self.client.post(
            f'/api/v1/salvage-items/{self.item.id}/void/',
            {'reason': 'Duplicated by mistake, real row is ML-0007'},
            format='json',
        )
        self.assertEqual(r.status_code, 200, r.content)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, 'voided')
        self.assertIn('Duplicated by mistake', self.item.notes)

    def test_cannot_void_a_sold_row(self):
        self.item.status = 'sold'
        self.item.save(update_fields=['status'])
        r = self.client.post(
            f'/api/v1/salvage-items/{self.item.id}/void/',
            {'reason': 'oops'},
            format='json',
        )
        self.assertEqual(r.status_code, 409, r.content)

    def test_cannot_double_void(self):
        self.client.post(
            f'/api/v1/salvage-items/{self.item.id}/void/',
            {'reason': 'first void'},
            format='json',
        )
        r = self.client.post(
            f'/api/v1/salvage-items/{self.item.id}/void/',
            {'reason': 'second void'},
            format='json',
        )
        self.assertEqual(r.status_code, 409, r.content)

    def test_delete_is_still_refused(self):
        """DELETE is not in the viewset — Kgosi's row is never hard-deleted."""
        r = self.client.delete(f'/api/v1/salvage-items/{self.item.id}/')
        self.assertEqual(r.status_code, 405, r.content)
