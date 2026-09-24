"""core/tests/test_vault_share_once.py — the vault's one-time hand-over link.

CFO 2026-09-21, refund go-live night: "why don't you send him a secure link".
Pramod (Graphite) is not a vault user, so the shared blind-index key had no
path out of the vault except a human copy-paste. This is that path. Each test
fails without the feature (the routes 404, or the link would open twice):
  * CFO mints a link; the response carries the URL exactly once and the DB
    holds only a hash of the token;
  * plain staff cannot mint one (403);
  * GET on the link shows a button and reveals NOTHING (mail scanners
    pre-fetch with GET — a GET that revealed would burn the link unseen);
  * POST reveals the value once, stamps the secret, writes an AuditLog;
  * the second POST is refused (410) — the link is dead;
  * an expired link is refused before it is ever opened;
  * a wrong token is a 404 and reveals nothing.
"""
from datetime import timedelta

from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from core.models import AuditLog, VaultSecret, VaultShareLink

VALUE = 'a1b2c3d4' * 8   # 64 hex chars, like the real key


class VaultShareOnceTests(APITestCase):
    def setUp(self):
        self.cfo = User.objects.create_superuser('vault-cfo', 'cfo@alphadirect.co.bw', 'x')
        self.staff = User.objects.create_user('vault-staff2', email='vs2@alphadirect.co.bw', password='x')
        self.secret = VaultSecret(name='Refund blind-index key (test)', category='api')
        self.secret.set_secret(VALUE)
        self.secret.save()

    def _mint(self, user=None, **body):
        c = APIClient()
        c.force_authenticate(user or self.cfo)
        return c.post(f'/api/v1/admin/vault/{self.secret.id}/share/', body or {'recipient': 'pbisen@theriskco.com'}, format='json')

    @staticmethod
    def _path(url):
        return '/api/v1' + url.split('/api/v1', 1)[1] if '/api/v1' in url else url

    def test_cfo_mints_a_link_and_only_its_hash_is_stored(self):
        r = self._mint()
        self.assertEqual(r.status_code, 201, r.content)
        url = r.json()['url']
        self.assertIn('/api/v1/vault-share/', url)
        token = url.rstrip('/').rsplit('/', 1)[1]
        link = VaultShareLink.objects.get(secret=self.secret)
        self.assertEqual(link.token_hash, VaultShareLink.hash_token(token))
        self.assertNotIn(token, link.token_hash)
        self.assertEqual(link.recipient, 'pbisen@theriskco.com')
        self.assertTrue(AuditLog.objects.filter(description__contains='Minted one-time share link').exists())

    def test_plain_staff_cannot_mint(self):
        r = self._mint(user=self.staff)
        self.assertEqual(r.status_code, 403, r.content)
        self.assertEqual(VaultShareLink.objects.count(), 0)

    def test_get_shows_a_button_and_reveals_nothing(self):
        url = self._path(self._mint().json()['url'])
        r = APIClient().get(url)
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn('Show it once', html)
        self.assertNotIn(VALUE, html)
        link = VaultShareLink.objects.get(secret=self.secret)
        self.assertIsNone(link.opened_at, 'a GET must never burn the link')
        self.assertEqual(r['Cache-Control'], 'no-store, max-age=0')

    def test_post_reveals_once_then_the_link_is_dead(self):
        url = self._path(self._mint().json()['url'])
        first = APIClient().post(url)
        self.assertEqual(first.status_code, 200, first.content)
        self.assertIn(VALUE, first.content.decode())
        link = VaultShareLink.objects.get(secret=self.secret)
        self.assertIsNotNone(link.opened_at)
        self.secret.refresh_from_db()
        self.assertIsNotNone(self.secret.last_revealed_at)
        self.assertTrue(AuditLog.objects.filter(description__contains='One-time share link OPENED').exists())

        second = APIClient().post(url)
        self.assertEqual(second.status_code, 410, second.content)
        self.assertNotIn(VALUE, second.content.decode())
        # and a GET after use says so too, still without the value
        again = APIClient().get(url)
        self.assertEqual(again.status_code, 410)
        self.assertNotIn(VALUE, again.content.decode())

    def test_expired_link_is_refused_unopened(self):
        url = self._path(self._mint().json()['url'])
        VaultShareLink.objects.filter(secret=self.secret).update(expires_at=timezone.now() - timedelta(minutes=1))
        r = APIClient().post(url)
        self.assertEqual(r.status_code, 410)
        self.assertNotIn(VALUE, r.content.decode())
        self.assertIsNone(VaultShareLink.objects.get(secret=self.secret).opened_at)

    def test_wrong_token_is_a_404_with_nothing_in_it(self):
        self._mint()
        r = APIClient().post('/api/v1/vault-share/not-the-token-at-all/')
        self.assertEqual(r.status_code, 404)
        self.assertNotIn(VALUE, r.content.decode())

    def test_hours_are_clamped_to_the_72h_ceiling(self):
        r = self._mint(recipient='x', hours=500)
        self.assertEqual(r.status_code, 201)
        link = VaultShareLink.objects.get(secret=self.secret)
        self.assertLessEqual(link.expires_at, timezone.now() + timedelta(hours=72, minutes=1))

    def test_recipient_logged_into_omni_in_the_same_browser_still_gets_it(self):
        """DRF SessionAuthentication enforces CSRF for anyone holding a Django
        session cookie, and @csrf_exempt does not reach it (HRIS unlock, May
        2026). A recipient who happens to be signed into Omni must not be
        met with 'CSRF Failed' instead of the value."""
        url = self._path(self._mint().json()['url'])
        c = APIClient(enforce_csrf_checks=True)
        c.force_login(self.staff)
        r = c.post(url)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIn(VALUE, r.content.decode())

    def test_an_undecryptable_secret_does_not_consume_the_link(self):
        """The one chance must not be spent on an empty box. If the value
        cannot be produced, the link stays live and the page says so."""
        url = self._path(self._mint().json()['url'])
        VaultSecret.objects.filter(pk=self.secret.pk).update(secret_ciphertext='gAAAAABnot-real-ciphertext')
        r = APIClient().post(url)
        self.assertNotEqual(r.status_code, 200)
        self.assertNotIn('0 characters', r.content.decode())
        self.assertIsNone(VaultShareLink.objects.get(secret=self.secret).opened_at,
                          'a failed reveal must leave the link unopened')
