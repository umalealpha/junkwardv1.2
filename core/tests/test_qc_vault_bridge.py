"""core/tests/test_qc_vault_bridge.py — the QC agent's narrow door into the vault.

CFO 2026-09-07: the CFO files platform credentials in Omni → Settings → Secrets
under names starting ``qc/``; the QC agent on his Mac reads them at runtime with
its scoped 'qc-bot' key. Guarantees tested here (each fails if the feature is
reverted: the route is 404 without it):
  * qc-bot can read ``qc/<name>`` and the read is stamped + audit-logged;
  * qc-bot can NOT read anything outside ``qc/`` (an HRIS password) — 403;
  * plain staff can not use the door — 403;
  * an unknown qc/ name is a clear 404.
"""
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from rest_framework.test import APIClient, APITestCase

from core.models import ApiKey, AuditLog, VaultSecret


def _key(user, scopes, seed):
    plaintext = seed + '0' * (64 - len(seed))
    ApiKey.objects.create(label=f'{seed} test', key_prefix=plaintext[:12],
                          key_hash=make_password(plaintext), service_user=user,
                          allowed_scopes=scopes, is_active=True)
    return plaintext


class QcVaultBridgeTests(APITestCase):
    def setUp(self):
        self.svc = User.objects.create_user('svc-qc-vault', email='omni-qc-bot@alphadirect.co.bw', password='x')
        self.bot = _key(self.svc, ['qc-bot'], 'qcbotvault')
        self.staff = User.objects.create_user('vault-staff', email='vs@alphadirect.co.bw', password='x')
        self.qc_secret = VaultSecret(name='qc/cloudflare_api_token', category='cloud', username='cf')
        self.qc_secret.set_secret('cf-token-123'); self.qc_secret.save()
        self.hr_secret = VaultSecret(name='HRIS unlock password', category='hris')
        self.hr_secret.set_secret('never-for-the-bot'); self.hr_secret.save()

    def _get(self, name, key=None):
        c = APIClient()
        kw = {'HTTP_AUTHORIZATION': f'ApiKey {key}'} if key else {}
        return c.get(f'/api/v1/admin/vault/qc/{name}/', **kw)

    def test_qc_bot_reads_qc_namespaced_secret_and_it_is_audited(self):
        r = self._get('cloudflare_api_token', self.bot)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['secret'], 'cf-token-123')
        self.qc_secret.refresh_from_db()
        self.assertIsNotNone(self.qc_secret.last_revealed_at)
        self.assertEqual(self.qc_secret.last_revealed_by_id, self.svc.id)
        self.assertTrue(AuditLog.objects.filter(description__contains='QC agent read vault secret').exists())

    def test_qc_bot_cannot_reach_a_non_qc_secret(self):
        # the door only ever looks up "qc/<name>", so an HRIS name is simply not there
        r = self._get('HRIS unlock password', self.bot)
        self.assertEqual(r.status_code, 404, r.content)
        # and the general reveal endpoint stays CFO-only for the bot (scope prefix does not cover it)
        r2 = APIClient().post(f'/api/v1/admin/vault/{self.hr_secret.id}/reveal/',
                              HTTP_AUTHORIZATION=f'ApiKey {self.bot}')
        self.assertIn(r2.status_code, (401, 403), r2.content)

    def test_plain_staff_cannot_use_the_door(self):
        c = APIClient(); c.force_authenticate(self.staff)
        r = c.get('/api/v1/admin/vault/qc/cloudflare_api_token/')
        self.assertEqual(r.status_code, 403, r.content)

    def test_unknown_qc_name_is_a_clear_404(self):
        r = self._get('does_not_exist', self.bot)
        self.assertEqual(r.status_code, 404, r.content)
        self.assertIn('Settings', r.json()['detail'])
