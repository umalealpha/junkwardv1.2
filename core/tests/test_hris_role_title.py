"""The HR_MANAGER *title* must resolve to the 'hr' HRIS tier (2026-08-03).

Found while raising Unami's read-only HR data-extract key: the key authenticates
as a title-only HR identity, and every HR read came back
"HRIS is locked. Enter the HRIS password to continue." — a wall a service
identity can never answer, because it has no session and no password.

The cause was an inconsistency, not a policy: user_can_access_hris() accepts the
HR_MANAGER title, and so do hris.leave_encash_service.is_hr and
hris.document_access.is_hr_doc_admin — but hris_role() demanded a role
ASSIGNMENT. So the identity passed the door, resolved to 'ess', and was refused
every HR capability.

These tests pin both halves: the title now resolves to 'hr', and it must NOT be
mistaken for an administrator or superuser tier.
"""
from __future__ import annotations

from django.contrib.auth.models import User
from django.test import TestCase

from core.hris_access import hris_role, user_can_access_hris
from core.hris_unlock import is_hris_unlocked
from core.models import UserProfile


def _titled(username, title, **profile_kwargs):
    u = User.objects.create_user(username, email=f'{username}@alphadirect.co.bw')
    UserProfile.objects.create(
        user=u, role=UserProfile.Role.SYSTEM_API, title=title,
        is_active=True, **profile_kwargs,
    )
    return u


class HrManagerTitleTierTests(TestCase):

    def test_title_alone_resolves_to_the_hr_tier(self):
        u = _titled('title-only-hr', UserProfile.Title.HR_MANAGER,
                    is_administrator=False)
        self.assertEqual(hris_role(u), 'hr')

    def test_title_alone_passes_the_hris_door(self):
        u = _titled('door-hr', UserProfile.Title.HR_MANAGER, is_administrator=False)
        self.assertTrue(user_can_access_hris(u))

    def test_title_alone_clears_the_password_wall(self):
        """The privileged-role bypass in is_hris_unlocked covers the 'hr' tier.
        Without this, a service identity is stuck forever: it cannot type a
        shared password."""
        u = _titled('unlock-hr', UserProfile.Title.HR_MANAGER, is_administrator=False)
        self.assertTrue(is_hris_unlocked(u))

    def test_the_title_does_not_confer_admin_or_superadmin(self):
        u = _titled('not-admin-hr', UserProfile.Title.HR_MANAGER,
                    is_administrator=False)
        self.assertNotIn(hris_role(u), ('admin', 'superadmin', 'ceo'))
        self.assertFalse(u.is_superuser)

    def test_an_operations_title_is_still_only_self_service(self):
        """Guard against the check being written too broadly — the 2026-07-09
        directive keeps non-HR department staff out of the HRIS module."""
        u = _titled('ops-person', UserProfile.Title.OPERATIONS,
                    is_administrator=False)
        self.assertEqual(hris_role(u), 'ess')
        self.assertFalse(is_hris_unlocked(u))

    def test_an_untitled_user_is_unaffected(self):
        u = User.objects.create_user('plain', email='plain@alphadirect.co.bw')
        UserProfile.objects.create(user=u, role=UserProfile.Role.OPERATIONS_STAFF,
                                   title='', is_active=True)
        self.assertNotEqual(hris_role(u), 'hr')
