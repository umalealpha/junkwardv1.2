"""A dead DRF token must answer 401, not 403.

Why this exists: DRF takes the WWW-Authenticate header from the FIRST
authenticator in DEFAULT_AUTHENTICATION_CLASSES, and when that returns None it
downgrades every authentication failure to 403. The Nexus staff bridge sits
first and returned None, so a rotated/expired/unknown token came back as
403 {"detail":"Invalid token."} — and the web app's stale-token recovery keys on
the 401, so it never fired. The dead key stayed in localStorage and the user was
locked out of every screen with no route back to sign-in (4-Aug-2026: a non-finance
user could not submit a spend request; ~600 calls in two hours failed this way).
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

URL = '/api/v1/notifications/pending/'


class DeadTokenStatusTests(APITestCase):
    def test_unknown_token_is_401_invalid_token(self):
        r = self.client.get(URL, HTTP_AUTHORIZATION='Token ' + '0' * 40)
        self.assertEqual(r.status_code, 401, r.content[:200])
        self.assertEqual(r.json().get('detail'), 'Invalid token.')

    def test_expired_token_is_401_and_says_sign_in_again(self):
        user = get_user_model().objects.create_user(
            username='expired.person', email='expired@alphadirect.co.bw', password='x')
        token = Token.objects.create(user=user)
        # Older than OMNI_TOKEN_TTL_HOURS (15h default).
        Token.objects.filter(pk=token.pk).update(
            created=timezone.now() - timedelta(hours=48))

        r = self.client.get(URL, HTTP_AUTHORIZATION=f'Token {token.key}')
        self.assertEqual(r.status_code, 401, r.content[:200])
        self.assertIn('sign in again', r.json().get('detail', ''))
        # The expiring auth class drops the row so the next sign-in mints a fresh one.
        self.assertFalse(Token.objects.filter(pk=token.pk).exists())

    def test_no_credentials_is_still_not_authenticated(self):
        r = self.client.get(URL)
        self.assertIn(r.status_code, (401, 403), r.content[:200])
        self.assertIn('credentials', r.json().get('detail', '').lower())
