"""A write-capable key's scope must be enforced too — at the authentication layer.

`_enforce_read_only_key` closed this in Aug 2026 for keys whose scopes are ALL
read-only. It never fired for a key carrying a WRITE scope, and the fallback for
those was `ApiKeyScopePermission` — a DRF permission class.

Counted 2026-08-09: **419 views declare `permission_classes`; 5 include it.**
DRF's `@permission_classes([...])` REPLACES the defaults, so on the other ~414 a
write-capable key's scope was decorative. The QC key carries smart-upload +
bulk-upload, which is exactly that position.

These tests pin the new auth-layer check AND, just as importantly, that the
existing callers it must not break still work.
"""
from __future__ import annotations

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase
from rest_framework import exceptions

from core.api_key_auth import _enforce_key_scope
from core.models import ApiKey

PLAINTEXT = 'f1e2d3c4b5a6' + '0' * 52


def _key(user, scopes):
    return ApiKey.objects.create(
        label='scope test', key_prefix=PLAINTEXT[:12],
        key_hash=make_password(PLAINTEXT), service_user=user,
        allowed_scopes=scopes, is_active=True)


class KeyScopeIsEnforcedAtTheAuthLayerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.svc = User.objects.create_user('svc', 'svc@example.invalid', 'x')
        cls.rf = RequestFactory()

    def _req(self, path, method='GET'):
        return getattr(self.rf, method.lower())(path)

    # ── the hole ──────────────────────────────────────────────────────────
    def test_a_write_key_is_refused_a_path_its_scope_does_not_grant(self):
        k = _key(self.svc, ['smart-upload'])
        with self.assertRaises(exceptions.AuthenticationFailed):
            _enforce_key_scope(self._req('/api/v1/journal-entries/'), k)

    def test_a_key_with_no_scopes_at_all_is_refused(self):
        k = _key(self.svc, [])
        with self.assertRaises(exceptions.AuthenticationFailed):
            _enforce_key_scope(self._req('/api/v1/journal-entries/'), k)

    # ── what must NOT break ───────────────────────────────────────────────
    def test_a_key_reaching_a_path_its_scope_DOES_grant_still_works(self):
        from core.api_key_auth import SCOPE_PATHS
        allowed = SCOPE_PATHS['smart-upload'][0]
        k = _key(self.svc, ['smart-upload'])
        _enforce_key_scope(self._req(allowed, 'POST'), k)      # must not raise

    def test_a_superuser_service_account_is_untouched(self):
        su = User.objects.create_superuser('svcsu', 'su@example.invalid', 'x')
        k = _key(su, ['smart-upload'])
        _enforce_key_scope(self._req('/api/v1/journal-entries/'), k)   # no raise

    def test_the_role_based_bypass_still_wins(self):
        """A BULK_UPLOADER-style role must still fan out — this is how the live
        Graphite ingest and the bulk uploads reach paths not in their scope map."""
        from core.models import Role, UserRoleAssignment, Permission
        perm, _ = Permission.objects.get_or_create(
            code='read-all')
        role, _ = Role.objects.get_or_create(
            code='TEST_BULK', defaults={'name': 'Test bulk', 'is_active': True, 'level': 1})
        role.permissions.add(perm)
        UserRoleAssignment.objects.create(user=self.svc, role=role)
        k = _key(self.svc, ['smart-upload'])
        _enforce_key_scope(self._req('/api/v1/journal-entries/'), k)   # no raise


class ReadOnlyDoesNotMeanReadEverythingTests(TestCase):
    """'read-only' limits what a key may CHANGE, not what it may see — and it was
    granting every GET under /api/v1/, including the entire audit trail.

    Manus pulled 525,283 audit rows with the QC key on 2026-08-09, while I had
    reported that same path as proof enforcement worked. I had tested the guard
    function in a shell instead of through a request.
    """

    @classmethod
    def setUpTestData(cls):
        cls.svc = User.objects.create_user('rosvc', 'ro@example.invalid', 'x')
        cls.rf = RequestFactory()

    def _k(self):
        return _key(self.svc, ['read-only'])

    def test_the_whole_audit_trail_is_refused(self):
        with self.assertRaises(exceptions.AuthenticationFailed):
            _enforce_key_scope(self.rf.get('/api/v1/audit-log/'), self._k())

    def test_payslips_and_the_staff_file_are_refused(self):
        k = self._k()
        for p in ('/api/v1/payslips/', '/api/v1/employees/', '/hris/api/employees/'):
            with self.assertRaises(exceptions.AuthenticationFailed, msg=p):
                _enforce_key_scope(self.rf.get(p), k)

    def test_what_a_qc_reader_actually_needs_still_works(self):
        k = self._k()
        for p in ('/api/v1/payment-requests/', '/api/v1/reports/', '/api/v1/companies/'):
            _enforce_key_scope(self.rf.get(p), k)      # must not raise

    def test_writes_are_still_refused_outright(self):
        from core.api_key_auth import _enforce_read_only_key
        with self.assertRaises(exceptions.AuthenticationFailed):
            _enforce_read_only_key(self.rf.post('/api/v1/payment-requests/'), self._k())
