"""The CFO dashboard must say which entity its figures cover.

Manus, 2026-08-09: payables read 94,474,792.15 and 43,606,991.52 and the two were
reported as a contradiction. They are the same field and the same calculation —
one call passed no ?company and got all 13 entities, the other asked for ADIC.
Nothing was wrong with the arithmetic; the answer simply never said what it was
about.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import Company

User = get_user_model()


class DashboardScopeLabelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser('gdashadm', 'da@example.invalid', 'x')
        cls.co = Company.objects.create(code='TSTDS', name='Test Entity')

    def _kpis(self, qs=''):
        c = APIClient()
        c.force_authenticate(self.admin)
        res = c.get(f'/api/v1/dashboard/cfo/{qs}')
        self.assertEqual(res.status_code, 200, res.content[:200])
        return res.json()['kpis']

    def test_an_unscoped_call_says_it_is_every_entity(self):
        scope = self._kpis()['scope']
        self.assertIn('ALL ENTITIES', scope)
        self.assertIn('?company=', scope, 'it must show how to narrow it')

    def test_a_scoped_call_names_the_entity(self):
        scope = self._kpis(f'?company={self.co.code}')['scope']
        self.assertIn('TSTDS', scope)
        self.assertIn('Test Entity', scope)

    def test_the_payables_figure_is_still_reported(self):
        self.assertIn('ap_outstanding_bwp', self._kpis())
