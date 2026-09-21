"""DPIA sign-off access (CFO 2026-08-18).

The DPIA register viewsets used to gate writes on _can_run_audit (superuser +
cfo/iso_auditor groups only), so the DPO (Oratile) and the Compliance Officer
(Kakale) could not sign their OWN slot or tick a condition — every write
returned "You do not have permission" ("she can't click anything"). These tests
pin the fix: the DPO/CO can act, ordinary staff still cannot, and no one signs
another role's slot.
"""
from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework.test import APIClient, APITestCase

from iso_compliance.models import DPIA


class DpiaSignoffPermissionTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.dpo = User.objects.create_user(
            'oratile', email='otlhomelang@alphadirect.co.bw', password='x')
        cls.co = User.objects.create_user(
            'kakale', email='kbotana@alphadirect.co.bw', password='x')
        cls.rando = User.objects.create_user(
            'rando', email='someoneelse@alphadirect.co.bw', password='x')
        cls.dpia = DPIA.objects.create(project='Alpha Nexus')

    def _c(self, u):
        c = APIClient()
        c.force_authenticate(u)
        return c

    def _url(self):
        return reverse('dpo-dpia-detail', args=[str(self.dpia.id)])

    def test_dpo_can_sign_the_dpo_slot(self):
        r = self._c(self.dpo).patch(self._url(), {'dpo_signed': True}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.dpia.refresh_from_db()
        self.assertTrue(self.dpia.dpo_signed)
        self.assertIsNotNone(self.dpia.dpo_signed_at)

    def test_compliance_officer_can_sign_the_compliance_slot(self):
        r = self._c(self.co).patch(self._url(), {'compliance_signed': True}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.dpia.refresh_from_db()
        self.assertTrue(self.dpia.compliance_signed)

    def test_ordinary_staff_cannot_write(self):
        r = self._c(self.rando).patch(self._url(), {'dpo_signed': True}, format='json')
        self.assertEqual(r.status_code, 403, r.content)
        self.dpia.refresh_from_db()
        self.assertFalse(self.dpia.dpo_signed)

    def test_dpo_cannot_sign_another_slot(self):
        # Segregation: holding write access does not let the DPO sign the CFO slot.
        r = self._c(self.dpo).patch(self._url(), {'cfo_signed': True}, format='json')
        self.assertEqual(r.status_code, 403, r.content)
        self.dpia.refresh_from_db()
        self.assertFalse(self.dpia.cfo_signed)
