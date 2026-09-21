"""Omni sends UniCoin's sign-in codes — the narrow relay.

The point of these tests is what the endpoint REFUSES. A relay that will send any
text to any address is a phishing tool the moment its key leaks, so the refusals
are the feature and the successful send is almost incidental.
"""
from __future__ import annotations

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from core.models import ApiKey

# 64 hex chars, and the row stores the first 12 as its prefix — the same shape
# core/tests/test_hr_extract_key.py uses. Copied from there rather than invented:
# my own version passed `name=`/`created_by=`/a sha256 hash and every test that
# minted a key errored on TypeError, because those are not the model's fields.
PLAINTEXT = 'ucmail000001' + '0' * 52
OTHER_KEY = 'ucother00001' + '1' * 52


def _make_key(service_user, scopes, raw):
    return ApiKey.objects.create(
        label=f'UniCoin mail test {",".join(scopes)}',
        key_prefix=raw[:12],
        key_hash=make_password(raw),
        service_user=service_user,
        allowed_scopes=scopes,
        is_active=True,
    )


class UniCoinMailTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.svc = User.objects.create_user('uc-svc', email='svc-unicoin@alphadirect.co.bw',
                                           password='x')
        cls.staff = User.objects.create_user('realperson',
                                             email='bbalasubramanian@alphadirect.co.bw',
                                             password='x')

    def setUp(self):
        cache.clear()
        mail.outbox = []

    def _post(self, body, key=PLAINTEXT):
        return self.client.post(reverse('unicoin-login-code'), data=body,
                                content_type='application/json',
                                HTTP_AUTHORIZATION=f'ApiKey {key}')

    # ---- what it refuses -------------------------------------------------
    def test_no_key_no_send(self):
        r = self.client.post(reverse('unicoin-login-code'),
                             data={'email': 'bbalasubramanian@alphadirect.co.bw',
                                   'code': '123456'},
                             content_type='application/json')
        self.assertIn(r.status_code, (401, 403))
        self.assertEqual(len(mail.outbox), 0)

    def test_a_key_without_the_scope_cannot_send(self):
        """A read-only notebook key must not become a mail cannon."""
        _make_key(self.svc, ['notebook'], OTHER_KEY)
        r = self._post({'email': 'bbalasubramanian@alphadirect.co.bw', 'code': '123456'},
                       key=OTHER_KEY)
        self.assertIn(r.status_code, (401, 403))
        self.assertEqual(len(mail.outbox), 0)

    def test_an_outside_address_is_refused(self):
        """The one that matters. A stolen key must not be able to mail the public."""
        _make_key(self.svc, ['unicoin-mail'], PLAINTEXT)
        r = self._post({'email': 'someone@gmail.com', 'code': '123456'})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(len(mail.outbox), 0)

    def test_a_shared_mailbox_is_refused(self):
        _make_key(self.svc, ['unicoin-mail'], PLAINTEXT)
        for addr in ('admin@alphadirect.co.bw', 'info@alphadirect.co.bw',
                     'accounts@alphadirect.co.bw'):
            r = self._post({'email': addr, 'code': '123456'})
            self.assertEqual(r.status_code, 400, addr)
        self.assertEqual(len(mail.outbox), 0)

    def test_the_caller_cannot_choose_the_words(self):
        """No subject or body field exists, so a leaked key cannot compose a
        convincing 'click here to reset your bank details' email."""
        _make_key(self.svc, ['unicoin-mail'], PLAINTEXT)
        r = self._post({'email': 'bbalasubramanian@alphadirect.co.bw', 'code': '123456',
                        'subject': 'URGENT: confirm your bank account',
                        'body': 'Click http://evil.example to keep your salary.'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.subject, 'Your UniCoin sign-in code')
        self.assertNotIn('evil.example', sent.body)
        self.assertNotIn('bank account', sent.body.lower())

    def test_a_non_numeric_code_is_refused_and_never_echoed(self):
        _make_key(self.svc, ['unicoin-mail'], PLAINTEXT)
        r = self._post({'email': 'bbalasubramanian@alphadirect.co.bw',
                        'code': "'; DROP TABLE users; --"})
        self.assertEqual(r.status_code, 400)
        self.assertNotIn('DROP TABLE', r.json()['detail'])
        self.assertEqual(len(mail.outbox), 0)

    def test_a_silly_expiry_is_refused(self):
        """Including ZERO. `0 or '10'` is '10' in Python, so minutes=0 was silently
        treated as "not supplied" and became the ten-minute default — a caller who
        asked for a zero-minute expiry got a ten-minute code and was never told.
        This test found that.

        Each case uses its own address, because a refused request must not be
        confused with one the 30-second throttle turned away."""
        _make_key(self.svc, ['unicoin-mail'], PLAINTEXT)
        for i, m in enumerate((0, -5, 61, 99999, 'ten', '')):
            r = self._post({'email': 'bbalasubramanian@alphadirect.co.bw',
                            'code': '123456', 'minutes': m})
            self.assertEqual(r.status_code, 400, f'minutes={m!r}')
        self.assertEqual(len(mail.outbox), 0, 'no code may go out on a bad expiry')

    def test_a_second_code_within_thirty_seconds_is_throttled(self):
        """Otherwise this is a way to bomb a colleague's inbox."""
        _make_key(self.svc, ['unicoin-mail'], PLAINTEXT)
        first = self._post({'email': 'bbalasubramanian@alphadirect.co.bw', 'code': '111111'})
        second = self._post({'email': 'bbalasubramanian@alphadirect.co.bw', 'code': '222222'})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(len(mail.outbox), 1)

    # ---- what it does ----------------------------------------------------
    def test_it_sends_the_code_to_the_one_address_and_nobody_else(self):
        """A second factor must reach exactly one mailbox. The house rule copies
        excoboard@ on outbound mail; copying a sign-in code there would put a
        working second factor in a shared inbox."""
        _make_key(self.svc, ['unicoin-mail'], PLAINTEXT)
        r = self._post({'email': 'bbalasubramanian@alphadirect.co.bw', 'code': '654321'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, ['bbalasubramanian@alphadirect.co.bw'])
        self.assertEqual(list(sent.cc or []), [])
        self.assertEqual(list(sent.bcc or []), [])
        self.assertIn('654321', sent.body)
        self.assertIn('10 minutes', sent.body)

    def test_the_expiry_it_states_is_the_one_it_was_given(self):
        """A message that says 10 minutes when the code lives 5 teaches people to
        distrust it."""
        _make_key(self.svc, ['unicoin-mail'], PLAINTEXT)
        self._post({'email': 'bbalasubramanian@alphadirect.co.bw', 'code': '333333',
                    'minutes': 5})
        self.assertIn('5 minutes', mail.outbox[0].body)

    def test_the_scope_is_registered_in_the_path_map(self):
        """Regression guard, 19-Aug-2026.

        Every key goes through _path_allowed_by_scopes at the AUTHENTICATION layer,
        so a scope missing from SCOPE_PATHS is refused with 401 — which reads like a
        bad key rather than a missing registration, and cost a full CI round to
        find. The view can be perfectly correct and still never run.

        Same class as H24 (a new env var absent from the compose allow-list arrives
        empty and fails silently): a new capability that is not in its allow-list
        does not work at all.
        """
        from core.api_key_auth import SCOPE_PATHS, _path_allowed_by_scopes
        self.assertIn('unicoin-mail', SCOPE_PATHS)
        self.assertTrue(
            _path_allowed_by_scopes(reverse('unicoin-login-code'), ['unicoin-mail'], 'POST'),
            'the endpoint path and the SCOPE_PATHS entry have drifted apart',
        )

    def test_the_scope_grants_nothing_else(self):
        """It must not become a general-purpose key. If this scope ever opens a
        second path, that is a decision someone has to make on purpose."""
        from core.api_key_auth import _path_allowed_by_scopes
        for path in ('/api/v1/journal-entries/', '/api/v1/payroll/', '/api/v1/notebook/raw/',
                     '/api/v1/mail/', '/admin/'):
            self.assertFalse(_path_allowed_by_scopes(path, ['unicoin-mail'], 'POST'), path)
            self.assertFalse(_path_allowed_by_scopes(path, ['unicoin-mail'], 'GET'), path)
