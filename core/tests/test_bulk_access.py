"""Bulk role-assignment + Aria proposal endpoints (CFO 2026-06-30).

Exercises the grant path on the TEST database only — never prod. Confirms:
  - bulk-assign grants many in one call, and re-running skips duplicates
  - resolve-targets is a read-only preview (no writes)
  - a non-admin cannot bulk-assign (403)
  - Aria parse-request proposes a role + people WITHOUT granting (read-only;
    runs the deterministic fallback since no DEEPSEEK_API_KEY in test env)
"""
from django.contrib.auth.models import User
from rest_framework.test import APIClient, APITestCase

from core.models import Role, UserRoleAssignment


class BulkAccessTest(APITestCase):
    def setUp(self):
        self.cfo = User.objects.create_user(
            'cfo_bulk', email='cfo_bulk@alphadirect.co.bw', password='x',
            is_superuser=True, is_staff=True)
        self.role = Role.objects.create(code='TESTER_T', name='System Tester', level=5)
        self.u1 = User.objects.create_user('bulk1', email='bulk1@alphadirect.co.bw',
                                           password='x', first_name='Leone', last_name='Nkola')
        self.u2 = User.objects.create_user('bulk2', email='bulk2@alphadirect.co.bw',
                                           password='x', first_name='Bonno', last_name='Ben')
        self.c = APIClient()
        self.c.force_authenticate(user=self.cfo)

    def test_bulk_assign_grants_then_skips_duplicates(self):
        r = self.c.post('/api/v1/rbac/assignments/bulk-assign/',
                        {'role': str(self.role.id),
                         'emails': ['bulk1@alphadirect.co.bw', 'bulk2@alphadirect.co.bw']},
                        format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data['totals']['granted'], 2)
        self.assertEqual(
            UserRoleAssignment.objects.filter(role=self.role, revoked_at__isnull=True).count(), 2)
        r2 = self.c.post('/api/v1/rbac/assignments/bulk-assign/',
                         {'role': str(self.role.id),
                          'emails': ['bulk1@alphadirect.co.bw', 'bulk2@alphadirect.co.bw']},
                         format='json')
        self.assertEqual(r2.data['totals']['granted'], 0)
        self.assertEqual(r2.data['totals']['skipped'], 2)

    def test_resolve_targets_is_readonly_preview(self):
        before = UserRoleAssignment.objects.count()
        r = self.c.post('/api/v1/rbac/assignments/resolve-targets/',
                        {'role': str(self.role.id),
                         'emails': ['bulk1@alphadirect.co.bw', 'nobody@nowhere.com']},
                        format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data['count'], 1)
        self.assertIn('nobody@nowhere.com', r.data['unmatched_emails'])
        self.assertEqual(UserRoleAssignment.objects.count(), before)

    def test_non_admin_cannot_bulk_assign(self):
        peon = User.objects.create_user('peon_bulk', email='peon_bulk@alphadirect.co.bw', password='x')
        c = APIClient()
        c.force_authenticate(user=peon)
        r = c.post('/api/v1/rbac/assignments/bulk-assign/',
                   {'role': str(self.role.id), 'emails': ['bulk1@alphadirect.co.bw']}, format='json')
        self.assertEqual(r.status_code, 403, r.content)

    def test_aria_parse_request_proposes_without_granting(self):
        before = UserRoleAssignment.objects.count()
        r = self.c.post('/api/v1/rbac/assignments/parse-request/',
                        {'text': 'give Leone the system tester role'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIsNotNone(r.data['role'])
        self.assertEqual(r.data['role']['code'], 'TESTER_T')
        emails = [u['email'] for u in r.data['resolved']]
        self.assertIn('bulk1@alphadirect.co.bw', emails)
        self.assertEqual(UserRoleAssignment.objects.count(), before)
