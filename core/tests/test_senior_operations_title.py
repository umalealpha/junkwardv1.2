"""Senior Operational Staff title — a team-leader tier with NO finance access.

CFO directive 2026-08-24: create a level a step above ordinary operations staff
(so a senior operational person can see/approve their own team) WITHOUT any of
the finance/salary access a Financial Controller title carries. Bharath
Balasubramanian moves onto this title; his old (erroneous) 'financial_controller'
title had let him see the whole leave-encashment salary queue + provision
register.

These tests pin both halves:
  * the title resolves to the 'mgr' HRIS tier (team visibility + approve team
    leave) even without a direct-report link, and
  * it is held OUT of every salary / payroll / finance surface — so the tier
    can never be widened by accident into the thing it was created to avoid.
"""
from __future__ import annotations

from django.contrib.auth.models import User
from django.test import TestCase

from core.hris_access import (
    ROLE_CAPABILITIES, can_view_compensation, hris_role, user_can_access_hris,
)
from core.models import UserProfile
from hris.leave_encash_service import can_view_all, is_finance_approver


def _titled(username, title, **profile_kwargs):
    u = User.objects.create_user(username, email=f'{username}@alphadirect.co.bw')
    UserProfile.objects.create(
        user=u, role=UserProfile.Role.OPERATIONS_STAFF, title=title,
        is_active=True, **profile_kwargs,
    )
    return u


class SeniorOperationsTierTests(TestCase):

    def test_resolves_to_the_manager_tier_without_direct_reports(self):
        u = _titled('senior-ops', UserProfile.Title.SENIOR_OPERATIONS,
                    is_administrator=False)
        self.assertEqual(hris_role(u), 'mgr')

    def test_can_see_and_approve_their_own_team(self):
        u = _titled('senior-ops-team', UserProfile.Title.SENIOR_OPERATIONS)
        caps = ROLE_CAPABILITIES[hris_role(u)]
        self.assertIn('view_team', caps)
        self.assertIn('approve_team_leave', caps)

    def test_has_no_salary_payroll_or_org_wide_capabilities(self):
        u = _titled('senior-ops-nofin', UserProfile.Title.SENIOR_OPERATIONS)
        caps = ROLE_CAPABILITIES[hris_role(u)]
        for forbidden in ('view_all', 'view_compensation', 'view_bonus_pool',
                          'manage_payroll', 'manage_employees', 'manage_leave_admin'):
            self.assertNotIn(forbidden, caps)

    def test_cannot_view_compensation(self):
        u = _titled('senior-ops-comp', UserProfile.Title.SENIOR_OPERATIONS)
        self.assertFalse(can_view_compensation(u))

    def test_is_not_a_finance_approver_and_cannot_view_the_encashment_queue(self):
        """The exact access Bharath is losing: the leave-encashment salary queue
        + provision register (can_view_all) and payment approval
        (is_finance_approver)."""
        u = _titled('senior-ops-enc', UserProfile.Title.SENIOR_OPERATIONS)
        self.assertFalse(is_finance_approver(u))
        self.assertFalse(can_view_all(u))

    def test_does_not_get_the_hris_payroll_module(self):
        u = _titled('senior-ops-hris', UserProfile.Title.SENIOR_OPERATIONS)
        self.assertFalse(user_can_access_hris(u))

    def test_is_in_no_finance_title_set(self):
        t = UserProfile.Title.SENIOR_OPERATIONS
        self.assertNotIn(t, UserProfile.FINANCIALS_VIEW_TITLES)
        self.assertNotIn(t, UserProfile.APPROVAL_TITLES)
        self.assertNotIn(t, UserProfile.CREATION_TITLES)
        self.assertNotIn(t, UserProfile.PAYROLL_APPROVAL_TITLES)
        self.assertNotIn(t, UserProfile.SOD_MAKER_TITLES)
        self.assertNotIn(t, UserProfile.SOD_CHECKER_TITLES)
