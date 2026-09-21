"""Tests for the Omni Mobile capability manifest (Workstream A).

Each test is red-first capable: e.g. remove the read-only guard in
build_mobile_capabilities and test_readonly_qc_identity_* fails; drop the
serializer field and test_me_includes_manifest fails.
"""
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase
from rest_framework.test import APIClient

from core.mobile_capabilities import build_mobile_capabilities, MOBILE_CAPABILITY_KEYS
from core.models import UserProfile

User = get_user_model()


def _mk(username, title=None, is_admin=False, is_super=False, active=True, email=''):
    """Create a user; attach a UserProfile only when a title is given."""
    u = User.objects.create_user(
        username=username, email=email, password='x',
        is_superuser=is_super, is_staff=is_super,
    )
    if title is not None:
        UserProfile.objects.create(
            user=u, role=UserProfile.Role.OPERATIONS_STAFF, title=title,
            is_administrator=is_admin, is_active=active,
        )
    return u


class BuildManifestTests(TestCase):
    def test_shape_is_the_canonical_key_set(self):
        caps = build_mobile_capabilities(_mk('shape', title=UserProfile.Title.OPERATIONS))
        self.assertEqual(set(caps), set(MOBILE_CAPABILITY_KEYS))

    def test_anonymous_all_false(self):
        caps = build_mobile_capabilities(AnonymousUser())
        self.assertTrue(all(v is False for v in caps.values()))

    def test_none_all_false(self):
        caps = build_mobile_capabilities(None)
        self.assertTrue(all(v is False for v in caps.values()))

    def test_no_profile_only_personal_home(self):
        caps = build_mobile_capabilities(_mk('noprofile'))
        self.assertTrue(caps['view_personal_home'])
        for k in MOBILE_CAPABILITY_KEYS:
            if k != 'view_personal_home':
                self.assertFalse(caps[k], f'{k} should be False for a no-profile user')

    def test_operations_staff_has_no_finance_claims_or_fnb(self):
        caps = build_mobile_capabilities(_mk('ops', title=UserProfile.Title.OPERATIONS))
        self.assertFalse(caps['view_finance_workspace'])
        self.assertFalse(caps['view_claims_workspace'])
        self.assertFalse(caps['manage_fnb'])
        self.assertFalse(caps['view_executive_dashboard'])
        self.assertTrue(caps['view_personal_home'])

    def test_cfo_gets_finance_fnb_exec_payroll(self):
        u = _mk('cfo', title=UserProfile.Title.CFO,
                email='pganesharajah@alphadirect.co.bw')
        caps = build_mobile_capabilities(u)
        self.assertTrue(caps['view_finance_workspace'])
        self.assertTrue(caps['create_finance_transaction'])
        self.assertTrue(caps['approve_finance_transaction'])
        self.assertTrue(caps['manage_fnb'])
        self.assertTrue(caps['view_executive_dashboard'])
        self.assertTrue(caps['manage_payroll'])

    def test_claims_manager_sees_and_captures(self):
        caps = build_mobile_capabilities(_mk('cm', title=UserProfile.Title.CLAIMS_MANAGER))
        self.assertTrue(caps['view_claims_workspace'])
        self.assertTrue(caps['capture_claims_action'])

    def test_claims_intern_view_only(self):
        caps = build_mobile_capabilities(_mk('ci', title=UserProfile.Title.CLAIMS_INTERN))
        self.assertTrue(caps['view_claims_workspace'])
        self.assertFalse(caps['capture_claims_action'])

    def test_readonly_qc_identity_keeps_view_drops_every_action(self):
        # The QC/screenshot identity is a superuser with a CFO title — so every
        # gate would answer True unless the read-only guard fires.
        u = _mk('qcbot', title=UserProfile.Title.CFO, is_super=True)
        with mock.patch('core.screenshot_bot.READ_ONLY_USERNAMES', {'qcbot'}):
            caps = build_mobile_capabilities(u)
        # View stays — seeing screens is the whole point of QC.
        self.assertTrue(caps['view_finance_workspace'])
        self.assertTrue(caps['view_claims_workspace'])
        # Every action is denied despite superuser + CFO title.
        self.assertFalse(caps['manage_fnb'])
        self.assertFalse(caps['create_finance_transaction'])
        self.assertFalse(caps['approve_finance_transaction'])
        self.assertFalse(caps['capture_claims_action'])
        self.assertFalse(caps['manage_payroll'])
        self.assertFalse(caps['manage_recruitment'])
        self.assertFalse(caps['give_monthly_feedback'])


class MeEndpointTests(TestCase):
    def test_me_includes_manifest(self):
        u = _mk('someone', title=UserProfile.Title.OPERATIONS)
        client = APIClient()
        client.force_authenticate(user=u)
        resp = client.get('/api/v1/user-profiles/me/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('mobile_capabilities', resp.data)
        self.assertEqual(set(resp.data['mobile_capabilities']), set(MOBILE_CAPABILITY_KEYS))

    def test_me_manifest_present_even_without_profile(self):
        u = _mk('ghost')  # authenticated but no UserProfile row
        client = APIClient()
        client.force_authenticate(user=u)
        resp = client.get('/api/v1/user-profiles/me/')
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data['has_profile'])
        self.assertIn('mobile_capabilities', resp.data)
        self.assertEqual(set(resp.data['mobile_capabilities']), set(MOBILE_CAPABILITY_KEYS))
