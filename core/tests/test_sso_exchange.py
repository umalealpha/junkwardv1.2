"""SSO -> Omni-token exchange (/api/v1/auth/sso/exchange/).

The endpoint turns a verified Microsoft access token into Omni's own DRF token,
so the browser stops depending on a live Microsoft token for every API call
(the Graphite-style fix, CFO 2026-08-24). We mock the Microsoft-token VERIFIER
(a real signed token needs Azure's live keys) and let the real user-resolution
+ token minting run.
"""
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import override_settings
from rest_framework import exceptions
from rest_framework.test import APITestCase


@override_settings(AZURE_SSO_ENABLED=True)
class SsoExchangeTests(APITestCase):
    URL = '/api/v1/auth/sso/exchange/'

    def test_missing_authorization_is_rejected(self):
        self.assertEqual(self.client.post(self.URL).status_code, 401)

    def test_a_non_bearer_authorization_is_rejected(self):
        r = self.client.post(self.URL, HTTP_AUTHORIZATION='Token abc123')
        self.assertEqual(r.status_code, 401)

    @patch('core.sso_exchange_views._verify_token')
    def test_a_valid_microsoft_token_yields_an_omni_token(self, mock_verify):
        mock_verify.return_value = {
            'oid': 'oid-abc-123',
            'preferred_username': 'jdoe@alphadirect.co.bw',
            'given_name': 'Jane', 'family_name': 'Doe',
        }
        r = self.client.post(self.URL, HTTP_AUTHORIZATION='Bearer real-ms-token')
        self.assertEqual(r.status_code, 200, r.content)
        key = r.json().get('token')
        self.assertTrue(key, 'must return an Omni token')
        # It maps to a real user...
        user = User.objects.get(email__iexact='jdoe@alphadirect.co.bw')
        # ...and the token actually authenticates that user.
        from rest_framework.authtoken.models import Token
        self.assertEqual(Token.objects.get(key=key).user_id, user.id)

    @patch('core.sso_exchange_views._verify_token')
    def test_the_same_user_keeps_one_stable_token(self, mock_verify):
        mock_verify.return_value = {
            'oid': 'oid-abc-123', 'preferred_username': 'jdoe@alphadirect.co.bw',
        }
        first = self.client.post(self.URL, HTTP_AUTHORIZATION='Bearer t1').json()['token']
        second = self.client.post(self.URL, HTTP_AUTHORIZATION='Bearer t2').json()['token']
        self.assertEqual(first, second)

    @patch('core.sso_exchange_views._verify_token')
    def test_a_rejected_microsoft_token_gives_401_not_a_token(self, mock_verify):
        mock_verify.side_effect = exceptions.AuthenticationFailed('Token expired.')
        r = self.client.post(self.URL, HTTP_AUTHORIZATION='Bearer expired')
        self.assertEqual(r.status_code, 401)
        self.assertNotIn('token', r.json())

    @patch('core.sso_exchange_views._verify_token')
    def test_an_inactive_account_is_refused(self, mock_verify):
        User.objects.create_user('frozen', email='frozen@alphadirect.co.bw',
                                 is_active=False)
        mock_verify.return_value = {
            'oid': 'oid-frozen', 'preferred_username': 'frozen@alphadirect.co.bw',
        }
        r = self.client.post(self.URL, HTTP_AUTHORIZATION='Bearer x')
        self.assertEqual(r.status_code, 403)

    @override_settings(AZURE_SSO_ENABLED=False)
    @patch('core.sso_exchange_views._verify_token')
    def test_the_kill_switch_blocks_even_a_valid_token(self, mock_verify):
        """With SSO disabled, no Microsoft token may mint an Omni token here —
        the endpoint must gate like the DRF auth class. Fails without the flag
        check in the view."""
        mock_verify.return_value = {
            'oid': 'oid-abc-123', 'preferred_username': 'jdoe@alphadirect.co.bw',
        }
        r = self.client.post(self.URL, HTTP_AUTHORIZATION='Bearer valid-but-disabled')
        self.assertEqual(r.status_code, 401)
        self.assertNotIn('token', r.json())
