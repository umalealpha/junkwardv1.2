"""The read-only HR data-extract API key (CFO directive 2026-08-03).

Unami Butale (Chief Human Capital Officer) gets a key so she can pull her own
department's data from a Claude Code session. Three things must hold, and the
first one is the reason this file exists at all:

  1. READ-ONLY MUST BE ENFORCED IN AUTHENTICATION, NOT IN A PERMISSION CLASS.
     ApiKeyScopePermission is installed via DEFAULT_PERMISSION_CLASSES, and
     DRF's @permission_classes([...]) REPLACES the defaults instead of adding
     to them. ~1,070 views in this codebase declare their own permission_classes
     without re-listing ApiKeyScopePermission — so on every one of those, the
     scope check silently did not run and a "read-only" key could write.
     test_write_is_refused_even_where_the_scope_permission_is_bypassed is the
     regression guard: it targets a view that overrides permission_classes.

  2. The key may not reach the accounting department's data (the 2026-07-15
     access audit took HR_MANAGER out of FINANCIALS_VIEW_TITLES for exactly
     this reason).

  3. The service account must stay weak — is_superuser or a live `read-all`
     role assignment both short-circuit ApiKeyScopePermission and would defeat
     the path narrowing.
"""
from __future__ import annotations

from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.test import TestCase

from core.api_key_auth import READ_ONLY_SCOPES, SCOPE_PATHS
from core.models import ApiKey, UserProfile

PLAINTEXT = 'a1b2c3d4e5f6' + '0' * 52          # 64 hex chars, prefix = first 12


def _make_key(service_user, scopes):
    return ApiKey.objects.create(
        label='HR data extract (test)',
        key_prefix=PLAINTEXT[:12],
        key_hash=make_password(PLAINTEXT),
        service_user=service_user,
        allowed_scopes=scopes,
        is_active=True,
    )


class HrExtractKeyTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.svc = User.objects.create_user('hr-data-extract',
                                           email='hr-data-extract@alphadirect.co.bw')
        cls.svc.set_unusable_password()
        cls.svc.save()
        UserProfile.objects.create(
            user=cls.svc,
            role=UserProfile.Role.SYSTEM_API,
            title=UserProfile.Title.HR_MANAGER,
            job_title='HR Data Extract (read-only)',
            is_administrator=False,
            is_active=True,
        )

    def _auth(self, scopes=('hr-extract',)):
        _make_key(self.svc, list(scopes))
        return {'HTTP_AUTHORIZATION': f'ApiKey {PLAINTEXT}'}

    # ---- layer 1: read-only at the authentication layer ----------------
    def test_hr_extract_is_registered_as_a_read_only_scope(self):
        self.assertIn('hr-extract', READ_ONLY_SCOPES)

    def test_write_is_refused_even_where_the_scope_permission_is_bypassed(self):
        """The regression guard for the 1,070-view hole.

        /api/v1/admin/api-keys/ declares @permission_classes([IsAuthenticated]),
        so ApiKeyScopePermission does NOT run on it. Without the
        authentication-layer guard a read-only key could POST here — i.e. mint
        itself a wider key. This is the escalation path, so it is tested by the
        thing that matters: no ApiKey row is created.
        """
        r = self.client.post('/api/v1/admin/api-keys/',
                             {'label': 'escalation', 'allowed_scopes': ['admin'],
                              'service_user': 'hr-data-extract'},
                             content_type='application/json', **self._auth())
        # 401 since ebc242ad: the read-only guard raises AuthenticationFailed in
        # the auth layer, and the Nexus bridge now sets a WWW-Authenticate header,
        # so DRF answers 401 (was silently downgraded to 403). Access is still
        # refused — assert the reason + that nothing was created.
        self.assertEqual(r.status_code, 401)
        self.assertIn('read-only', r.content.decode().lower())
        self.assertEqual(ApiKey.objects.count(), 1)     # nothing was created

    def test_every_write_method_is_refused(self):
        """401 since ebc242ad (the Nexus bridge now sets a WWW-Authenticate
        header, so DRF no longer downgrades AuthenticationFailed to 403). The
        status alone could also be an ordinary auth failure, so assert the reason
        too — the read-only guard is what refuses the write."""
        headers = self._auth()
        for method in ('post', 'put', 'patch', 'delete'):
            with self.subTest(method=method):
                r = getattr(self.client, method)('/api/v1/employees/', **headers)
                self.assertEqual(r.status_code, 401)
                self.assertIn('read-only', r.content.decode().lower())

    def test_a_write_scope_on_the_same_key_is_not_downgraded(self):
        """Existing upload keys must keep working — this guard is opt-in by scope."""
        headers = self._auth(scopes=('hr-extract', 'smart-upload'))
        r = self.client.post('/api/v1/employees/', **headers)
        self.assertNotIn('read-only', r.content.decode().lower())

    # ---- layer 2: the paths the scope grants ---------------------------
    def test_scope_grants_hr_and_pay_but_not_accounting(self):
        granted = SCOPE_PATHS['hr-extract']
        for path in ('/hris/', '/api/v1/employees/', '/api/v1/payslips/'):
            self.assertIn(path, granted)
        for path in ('/api/v1/reports/', '/api/v1/journal-entries/',
                     '/api/v1/accounts/', '/api/v1/bank-accounts/',
                     '/api/v1/payments/', '/api/v1/invoices/',
                     '/api/v1/dashboard/cfo/'):
            self.assertNotIn(path, granted)

    def test_reading_an_accounting_endpoint_is_refused(self):
        """Verified broken before the front-door path guard: this returned 200,
        because JournalEntryViewSet declares its own permission_classes and so
        never ran ApiKeyScopePermission."""
        for path in ('/api/v1/journal-entries/', '/api/v1/accounts/',
                     '/api/v1/bank-accounts/', '/api/v1/payments/',
                     '/api/v1/invoices/'):
            with self.subTest(path=path):
                r = self.client.get(path, **self._auth())
                # 401 since ebc242ad (see the write-refusal tests above); the
                # scope guard still refuses — assert the reason.
                self.assertEqual(r.status_code, 401)
                self.assertIn('not allowed to read', r.content.decode().lower())

    def test_reading_an_hr_endpoint_is_allowed_by_scope(self):
        for path in ('/api/v1/employees/', '/api/v1/payslips/'):
            with self.subTest(path=path):
                r = self.client.get(path, **self._auth())
                self.assertEqual(r.status_code, 200)

    def test_the_notebook_key_still_reads_the_notebook(self):
        """Claude reads the notebook with a `notebook`-scoped key at the start of
        every session. That scope is now enforced at the front door, so prove the
        one path it needs still answers — and that it cannot wander further."""
        headers = self._auth(scopes=('notebook',))
        self.assertEqual(self.client.get('/api/v1/notebook/raw/', **headers).status_code, 200)
        blocked = self.client.get('/api/v1/employees/', **headers)
        self.assertEqual(blocked.status_code, 401)   # 401 since ebc242ad; still refused

    def test_a_read_only_scope_key_still_reads_everything_it_used_to(self):
        """The broad `read-only` scope must not be narrowed by this change —
        the notebook helper and existing read keys depend on it."""
        r = self.client.get('/api/v1/journal-entries/',
                            **self._auth(scopes=('read-only',)))
        self.assertEqual(r.status_code, 200)

    # ---- layer 3: the identity stays weak ------------------------------
    def test_hr_title_cannot_see_financials(self):
        """The 2026-07-15 audit removed HR_MANAGER from FINANCIALS_VIEW_TITLES.

        If someone puts it back, this HR key silently gains sight of the
        accounting department's reports.
        """
        self.assertNotIn(UserProfile.Title.HR_MANAGER,
                         UserProfile.FINANCIALS_VIEW_TITLES)

    def test_hr_title_grants_no_journal_authority(self):
        for group in (UserProfile.APPROVAL_TITLES, UserProfile.CREATION_TITLES):
            self.assertNotIn(UserProfile.Title.HR_MANAGER, group)

    def test_service_account_is_not_a_superuser(self):
        """is_superuser short-circuits ApiKeyScopePermission — it would defeat
        the path narrowing entirely."""
        self.svc.refresh_from_db()
        self.assertFalse(self.svc.is_superuser)
        self.assertFalse(self.svc.is_staff)
        self.assertFalse(self.svc.has_usable_password())

    def test_seed_command_produces_a_weak_identity(self):
        from django.core.management import call_command
        from io import StringIO
        out = StringIO()
        self.svc.is_superuser = True         # simulate drift
        self.svc.save()
        call_command('seed_hr_extract_account', '--commit', stdout=out)
        self.svc.refresh_from_db()
        self.assertFalse(self.svc.is_superuser)
        prof = UserProfile.objects.get(user=self.svc)
        self.assertEqual(prof.title, UserProfile.Title.HR_MANAGER)
        self.assertFalse(prof.is_administrator)
