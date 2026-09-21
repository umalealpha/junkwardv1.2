"""Tests for the user-administration audit gap-closer + read-only audit-ask
endpoint (CFO directive 2026-06-21).

Part A: every user-admin mutation (title, company access, admin toggle, user
        create/deactivate, RBAC role grant) must now write an actor-stamped
        AuditLog row.
Part B: POST /api/v1/admin/audit-ask/ is admin-gated, strictly read-only, and
        returns {answer, advice, sources}. DeepSeek is mocked; a missing key
        degrades gracefully.
"""
from unittest import mock

from django.contrib.auth.models import User
from rest_framework.test import APIClient, APITestCase

from core.models import (
    AuditLog, Company, Currency, UserCompanyAccess, UserProfile,
)


def _cfo(username='cfo'):
    """Create a CFO-titled admin user (passes the /admin/ gate)."""
    u = User.objects.create_user(username, email=f'{username}@alphadirect.co.bw',
                                 password='x', is_superuser=True, is_staff=True)
    UserProfile.objects.update_or_create(
        user=u, defaults={'title': UserProfile.Title.CFO,
                          'role': UserProfile.Role.FINANCE_ADMIN,
                          'is_administrator': True, 'is_active': True})
    return u


def _company(code='ADIC'):
    cur, _ = Currency.objects.get_or_create(code='BWP', defaults={'name': 'Pula'})
    return Company.objects.create(code=code, name=f'{code} Ltd', base_currency=cur)


class UserAdminAuditTrailTest(APITestCase):
    """Part A — each instrumented endpoint writes an actor-stamped row."""

    def setUp(self):
        self.cfo = _cfo()
        self.client = APIClient()
        self.client.force_authenticate(user=self.cfo)

    def test_user_create_writes_audit(self):
        r = self.client.post('/api/v1/user-profiles/', {
            'username': 'newbie', 'email': 'newbie@alphadirect.co.bw',
            'title': UserProfile.Title.ACCOUNTANT,
            'role': UserProfile.Role.ACCOUNTANT,
        }, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        row = AuditLog.objects.filter(table_name='core.UserProfile',
                                      action=AuditLog.Action.CREATE,
                                      new_values__username='newbie').first()
        self.assertIsNotNone(row, 'no CREATE audit row for new user')
        self.assertEqual(row.user_id, self.cfo.id)

    def test_admin_toggle_and_title_update_writes_audit(self):
        target = User.objects.create_user('staff1', email='s1@alphadirect.co.bw', password='x')
        prof = UserProfile.objects.create(user=target, title=UserProfile.Title.ACCOUNTANT,
                                           role=UserProfile.Role.ACCOUNTANT)
        r = self.client.patch(f'/api/v1/user-profiles/{prof.id}/',
                              {'is_administrator': True}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        row = AuditLog.objects.filter(table_name='core.UserProfile',
                                      action=AuditLog.Action.UPDATE,
                                      new_values__username='staff1').first()
        self.assertIsNotNone(row, 'no UPDATE audit row for admin toggle')
        self.assertEqual(row.user_id, self.cfo.id)
        self.assertEqual(row.old_values['is_administrator'], False)
        self.assertEqual(row.new_values['is_administrator'], True)

    def test_user_deactivate_writes_audit(self):
        target = User.objects.create_user('staff2', email='s2@alphadirect.co.bw', password='x')
        prof = UserProfile.objects.create(user=target, title=UserProfile.Title.ACCOUNTANT,
                                           role=UserProfile.Role.ACCOUNTANT)
        r = self.client.delete(f'/api/v1/user-profiles/{prof.id}/')
        self.assertIn(r.status_code, (204, 200), r.content)
        row = AuditLog.objects.filter(table_name='core.UserProfile',
                                      action=AuditLog.Action.DELETE,
                                      new_values__username='staff2').first()
        self.assertIsNotNone(row, 'no DELETE audit row for deactivation')
        self.assertEqual(row.user_id, self.cfo.id)

    def test_title_change_writes_audit(self):
        target = User.objects.create_user('staff3', email='s3@alphadirect.co.bw', password='x')
        r = self.client.post('/api/v1/admin/user-titles/',
                             {'username': 'staff3', 'title': 'finance_manager'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        row = AuditLog.objects.filter(action=AuditLog.Action.UPDATE,
                                      new_values__username='staff3',
                                      new_values__title='finance_manager').first()
        self.assertIsNotNone(row, 'no audit row for title change')
        self.assertEqual(row.user_id, self.cfo.id)

    def test_company_access_grant_and_revoke_writes_audit(self):
        target = User.objects.create_user('staff4', email='s4@alphadirect.co.bw', password='x')
        comp = _company('ADIC')
        # Grant. (Resolve company by UUID — the endpoint's `filter(id=...)`-first
        # resolver raises on SQLite when given a non-UUID code; on prod Postgres
        # the code fallback works. UUID keeps this test DB-agnostic.)
        r = self.client.post('/api/v1/admin/user-company-access/', {
            'user': 'staff4', 'company': str(comp.id), 'can_view': True, 'can_write': True,
        }, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        grant = AuditLog.objects.filter(table_name='core.UserCompanyAccess',
                                        action=AuditLog.Action.CREATE,
                                        new_values__username='staff4').first()
        self.assertIsNotNone(grant, 'no audit row for company-access grant')
        self.assertEqual(grant.user_id, self.cfo.id)
        # Revoke
        r = self.client.delete(f'/api/v1/admin/user-company-access/?user=staff4&company={comp.id}')
        self.assertEqual(r.status_code, 200, r.content)
        revoke = AuditLog.objects.filter(table_name='core.UserCompanyAccess',
                                         action=AuditLog.Action.DELETE,
                                         new_values__username='staff4').first()
        self.assertIsNotNone(revoke, 'no audit row for company-access revoke')
        self.assertEqual(revoke.user_id, self.cfo.id)

    def test_company_access_bulk_writes_audit(self):
        User.objects.create_user('staff5', email='s5@alphadirect.co.bw', password='x')
        comp = _company('QIH')
        r = self.client.post('/api/v1/admin/user-company-access/bulk/', {
            'users': ['staff5'], 'companies': [str(comp.id)],
            'can_view': True, 'can_write': False, 'action': 'grant',
        }, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        row = AuditLog.objects.filter(table_name='core.UserCompanyAccess',
                                      new_values__username='staff5',
                                      new_values__company_code='QIH').first()
        self.assertIsNotNone(row, 'no audit row for bulk grant')
        self.assertEqual(row.user_id, self.cfo.id)

    def test_rbac_role_assign_writes_audit_with_ip(self):
        from core.rbac_service import assign_role
        from core.models import Role
        target = User.objects.create_user('staff6', email='s6@alphadirect.co.bw', password='x')
        role = Role.objects.create(code='BOOKKEEPER_T', name='Bookkeeper', level=5)
        a = assign_role(target_user=target, role=role, granted_by=self.cfo,
                        bypass_hierarchy=True, request_ip='10.0.0.9')
        row = AuditLog.objects.filter(table_name='core.UserRoleAssignment',
                                      record_id=str(a.pk)).first()
        self.assertIsNotNone(row, 'no audit row for role assignment')
        self.assertEqual(row.user_id, self.cfo.id)
        self.assertEqual(row.ip_address, '10.0.0.9')


class AuditAskEndpointTest(APITestCase):
    """Part B — admin-gated, read-only, structured output, graceful no-key."""

    URL = '/api/v1/admin/audit-ask/'

    def setUp(self):
        self.cfo = _cfo('cfo2')
        # A seed audit row about a target user so the endpoint has context.
        self.target = User.objects.create_user('kago', email='kago@alphadirect.co.bw', password='x')
        self.seed = AuditLog.objects.create(
            table_name='core.UserCompanyAccess', record_id='r1',
            action=AuditLog.Action.CREATE,
            new_values={'user_id': str(self.target.pk), 'username': 'kago',
                        'company_code': 'ADIC', 'can_write': True},
            user=self.cfo, description='Granted company access ADIC for kago',
        )

    def _client(self, user):
        c = APIClient(); c.force_authenticate(user=user); return c

    def test_non_admin_is_denied(self):
        peon = User.objects.create_user('peon', email='peon@alphadirect.co.bw', password='x')
        UserProfile.objects.create(user=peon, title=UserProfile.Title.ACCOUNTANT,
                                   role=UserProfile.Role.OPERATIONS_STAFF, is_administrator=False)
        r = self._client(peon).post(self.URL, {'question': 'who changed kago?'}, format='json')
        self.assertEqual(r.status_code, 403, r.content)

    @mock.patch('core.api_views.deepseek_complete')
    def test_admin_gets_structured_answer(self, mock_ds):
        mock_ds.return_value = (
            '{"answer":"kago was granted write access to ADIC by cfo2.",'
            '"advice":"Confirm this grant was approved.",'
            f'"sources":["{self.seed.id}"]}}'
        )
        r = self._client(self.cfo).post(self.URL,
                                        {'question': "why did kago's access change?"}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertIn('kago', body['answer'])
        self.assertEqual(body['advice'], 'Confirm this grant was approved.')
        self.assertIn(str(self.seed.id), body['sources'])
        # Sanity: DeepSeek was actually consulted.
        self.assertTrue(mock_ds.called)

    @mock.patch('core.api_views.deepseek_complete')
    def test_endpoint_is_read_only(self, mock_ds):
        mock_ds.return_value = '{"answer":"x","advice":"","sources":[]}'
        before = AuditLog.objects.count()
        r = self._client(self.cfo).post(self.URL, {'question': 'anything'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(AuditLog.objects.count(), before,
                         'audit-ask must not write any AuditLog rows')

    @mock.patch('core.api_views.deepseek_complete')
    def test_no_key_degrades_gracefully(self, mock_ds):
        from core.ai_assist import DeepSeekUnavailable
        mock_ds.side_effect = DeepSeekUnavailable('DEEPSEEK_API_KEY is not configured.')
        r = self._client(self.cfo).post(self.URL, {'question': 'who changed kago?'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(body['answer'], 'AI assistant not configured')
        self.assertEqual(body['advice'], '')
        self.assertEqual(body['sources'], [])

    def test_blank_question_is_rejected(self):
        r = self._client(self.cfo).post(self.URL, {'question': '   '}, format='json')
        self.assertEqual(r.status_code, 400, r.content)
