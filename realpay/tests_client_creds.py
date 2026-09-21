"""RealPay credential resolution — settings first, then the encrypted vault.

Guards the go-live wiring (2026-08-27): the LIVE secret is typed into the
CFO-only Secrets Vault, never into .env. These tests fail against the old
client that read settings only.
"""
from __future__ import annotations

from django.test import TestCase, override_settings

from core.models import VaultSecret
from realpay import client as rp_client


@override_settings(REALPAY_CLIENT_ID='', REALPAY_CLIENT_SECRET='',
                   REALPAY_VAULT_NAME='RealPay API')
class RealPayVaultCredsTests(TestCase):
    def test_creds_from_vault_when_settings_empty(self):
        vs = VaultSecret(name='RealPay API', category=VaultSecret.Category.API,
                         username='cid-live')
        vs.set_secret('super-secret-value')
        vs.save()
        cid, secret = rp_client._creds()
        self.assertEqual(cid, 'cid-live')
        self.assertEqual(secret, 'super-secret-value')

    def test_missing_everything_raises_clear_error(self):
        # No vault row, no settings → the token fetch must fail with a message
        # that points the operator at the vault, not a silent empty auth.
        with self.assertRaises(rp_client.RealPayError) as ctx:
            rp_client._fetch_token()
        self.assertIn('RealPay API', str(ctx.exception))

    def test_present_but_undecryptable_secret_names_the_real_fault(self):
        # Ciphertext present but not decryptable (wrong VAULT_FERNET_KEY / tamper):
        # reveal() returns '' silently, so _creds must detect the stored-but-
        # unreadable case and raise an error that names DECRYPTION — never the
        # misleading "not configured" that would send the operator to re-paste.
        vs = VaultSecret(name='RealPay API', category=VaultSecret.Category.API,
                         username='cid-live',
                         secret_ciphertext='not-a-valid-fernet-token')
        vs.save()
        with self.assertRaises(rp_client.RealPayError) as ctx:
            rp_client._creds()
        msg = str(ctx.exception).lower()
        self.assertIn('decrypt', msg)
        self.assertNotIn('not configured', msg)


@override_settings(REALPAY_CLIENT_ID='envid', REALPAY_CLIENT_SECRET='envsec',
                   REALPAY_VAULT_NAME='RealPay API')
class RealPaySettingsWinTests(TestCase):
    def test_settings_win_over_vault(self):
        vs = VaultSecret(name='RealPay API', category=VaultSecret.Category.API,
                         username='vault-id')
        vs.set_secret('vault-value')
        vs.save()
        cid, secret = rp_client._creds()
        self.assertEqual((cid, secret), ('envid', 'envsec'))
