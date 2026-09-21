"""Tests for the limited access administrator (CFO directive 2026-09-15).

Unopa Male manages access from here on. The rule the CFO gave was a deny-list —
no manager level, no super user, no payroll — and he accepted the counter-proposal
of an allow-list instead, because a deny-list silently grants every title nobody
thought to name. These tests prove the allow-list holds in BOTH directions:
the delegate CAN do the ordinary grants, and CANNOT reach anything money-shaped.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase

from core.access_delegate import (
    delegable_titles, delegate_refusal, is_access_delegate,
)
from core.models import UserProfile

User = get_user_model()
T = UserProfile.Title


class AllowListShapeTests(TestCase):
    def test_the_three_things_he_named_are_all_refused(self):
        allowed = delegable_titles()
        # 1. manager level
        for t in (T.FINANCE_MANAGER, T.CLAIMS_MANAGER, T.OPERATIONS_MANAGER,
                  T.HR_MANAGER, T.FINANCIAL_CONTROLLER):
            self.assertNotIn(t, allowed, f'{t} is manager-level')
        # 2. payroll
        for t in UserProfile.PAYROLL_APPROVAL_TITLES:
            self.assertNotIn(t, allowed, f'{t} approves payroll')
        # 3. the top of the house
        for t in (T.CEO, T.COO, T.CFO):
            self.assertNotIn(t, allowed)

    def test_the_holes_a_deny_list_would_have_left(self):
        """The reason the CFO was asked to switch to an allow-list."""
        allowed = delegable_titles()
        # Journal entries — never named in his deny-list, but they move money.
        for t in UserProfile.APPROVAL_TITLES | UserProfile.CREATION_TITLES:
            self.assertNotIn(t, allowed, f'{t} touches journal entries')
        # Claims seniors approve payment orders (see the Title enum comments).
        self.assertNotIn(T.CLAIMS_TEAM_LEADER, allowed)
        self.assertNotIn(T.SENIOR_CLAIMS_ASSOCIATE, allowed)
        # Anyone who can read the general ledger.
        for t in UserProfile.FINANCIALS_VIEW_TITLES:
            self.assertNotIn(t, allowed, f'{t} can read financials')
        self.assertNotIn(T.SYSTEM_API, allowed)

    def test_ordinary_staff_titles_are_delegable(self):
        allowed = delegable_titles()
        self.assertIn(T.OPERATIONS, allowed)
        self.assertIn(T.SENIOR_OPERATIONS, allowed)
        self.assertIn(T.CLAIMS_INTERN, allowed)
        self.assertIn(T.JUNIOR_CLAIMS_ASSOCIATE, allowed)

    def test_a_new_title_defaults_to_locked(self):
        """The property the CFO chose the allow-list for: new modules and new
        titles are NOT grantable until someone decides they should be."""
        allowed = delegable_titles()
        self.assertNotIn('some_future_title_nobody_has_reviewed', allowed)


class DelegateGuardTests(TestCase):
    def _profile(self, email, title=T.OPERATIONS, **flags):
        u = User.objects.create_user(email.split('@')[0], email=email, password='x')
        p, _ = UserProfile.objects.update_or_create(
            user=u, defaults={'title': title, 'is_active': True, **flags})
        return UserProfile.objects.get(pk=p.pk)

    def setUp(self):
        self.unopa = self._profile('umale@alphadirect.co.bw', is_access_delegate=True)
        self.staff = self._profile('ordinary@alphadirect.co.bw')
        self.admin = self._profile('boss@alphadirect.co.bw', is_administrator=True)

    def test_flag_is_recognised(self):
        self.assertTrue(is_access_delegate(self.unopa))
        self.assertFalse(is_access_delegate(self.staff))

    def test_an_inactive_delegate_is_not_a_delegate(self):
        self.unopa.is_active = False
        self.assertFalse(is_access_delegate(self.unopa))

    def test_delegate_may_grant_an_allowed_title(self):
        self.assertIsNone(
            delegate_refusal(self.unopa, self.staff, {'title': T.SENIOR_OPERATIONS}))

    def test_delegate_may_revoke_by_deactivating(self):
        # Give AND take — the CFO's choice, 15-Sep-2026.
        self.assertIsNone(
            delegate_refusal(self.unopa, self.staff, {'is_active': False}))

    def test_delegate_may_not_grant_payroll(self):
        msg = delegate_refusal(self.unopa, self.staff, {'title': T.HR_MANAGER})
        self.assertIsNotNone(msg)
        self.assertIn('reserved for the CFO', msg)

    def test_delegate_may_not_grant_a_manager_title(self):
        self.assertIsNotNone(
            delegate_refusal(self.unopa, self.staff, {'title': T.FINANCE_MANAGER}))

    def test_delegate_may_not_make_anyone_an_administrator(self):
        msg = delegate_refusal(self.unopa, self.staff, {'is_administrator': True})
        self.assertIsNotNone(msg)
        self.assertIn('Only the CFO', msg)

    def test_delegate_may_not_appoint_another_delegate(self):
        self.assertIsNotNone(
            delegate_refusal(self.unopa, self.staff, {'is_access_delegate': True}))

    def test_delegate_may_not_promote_themselves(self):
        self.assertIsNotNone(
            delegate_refusal(self.unopa, self.unopa, {'title': T.OPERATIONS}))

    def test_delegate_may_not_touch_an_administrator(self):
        msg = delegate_refusal(self.unopa, self.admin, {'title': T.OPERATIONS})
        self.assertIsNotNone(msg)
        self.assertIn('administrator', msg)

    def test_delegate_may_not_reset_a_password(self):
        """The side door Fable found on 15-Sep-2026. The write serializer takes
        a password, and none of the title rules would have seen it: set one on
        the Finance Manager, sign in as her, approve payroll."""
        fm = self._profile('fm@alphadirect.co.bw', title=T.FINANCE_MANAGER)
        msg = delegate_refusal(self.unopa, fm, {'password': 'x'})
        self.assertIsNotNone(msg)
        self.assertIn('signs in', msg)

    def test_delegate_may_still_create_a_new_starter(self):
        """Fable round 2: the first version of the login rule fired on CREATE
        too, where a username and password are compulsory — which blocked the
        most ordinary access request there is, and broke the "otherwise he can
        give access to other areas" half of the CFO's instruction."""
        self.assertIsNone(delegate_refusal(
            self.unopa, None,
            {'username': 'new.hire', 'password': 'x', 'title': T.OPERATIONS,
             'email': 'new.hire@alphadirect.co.bw'}))

    def test_delegate_may_save_an_edit_that_echoes_the_unchanged_email(self):
        """Fable round 3, and the one that would have made the whole feature
        useless: the edit form posts the WHOLE profile on every save, so the
        unchanged email comes back with it. Refusing that refused every save
        Unopa could ever make — a screen he can open and cannot use."""
        self.assertIsNone(delegate_refusal(
            self.unopa, self.staff,
            {'email': self.staff.user.email,
             'username': self.staff.user.username,
             'title': T.SENIOR_OPERATIONS}))

    def test_a_create_with_no_title_is_refused(self):
        """UserProfile.title defaults to `accountant`, which can raise journal
        entries and read the general ledger. An absent title on create is not
        "no change" — it is that default."""
        self.assertIsNotNone(
            delegate_refusal(self.unopa, None, {'username': 'a', 'password': 'b'}))

    def test_a_create_reusing_an_existing_email_is_refused(self):
        msg = delegate_refusal(
            self.unopa, None,
            {'username': 'imposter', 'password': 'x', 'title': T.OPERATIONS,
             'email': self.admin.user.email})
        self.assertIsNotNone(msg)
        self.assertIn('already belongs', msg)

    def test_delegate_may_not_repoint_an_email(self):
        """The remaining login side door. SSO resolves a person by email, so
        moving the CFO's address onto an ordinary profile is a login change
        wearing a different hat."""
        msg = delegate_refusal(self.unopa, self.staff,
                               {'email': 'pganesharajah@alphadirect.co.bw'})
        self.assertIsNotNone(msg)
        self.assertIn('signs in', msg)

    def test_delegate_may_not_rename_a_login(self):
        msg = delegate_refusal(self.unopa, self.staff, {'username': 'someone-else'})
        self.assertIsNotNone(msg)

    def test_delegate_may_not_touch_a_reserved_title_holder(self):
        """Granting a manager title was already blocked; this stops the delegate
        moving or deactivating someone who ALREADY holds one."""
        fm = self._profile('fm2@alphadirect.co.bw', title=T.FINANCE_MANAGER)
        self.assertIsNotNone(delegate_refusal(self.unopa, fm, {'title': T.OPERATIONS}))

    def test_delegate_may_not_deactivate_the_finance_manager(self):
        fm = self._profile('fm3@alphadirect.co.bw', title=T.FINANCE_MANAGER)
        self.assertIsNotNone(delegate_refusal(self.unopa, fm, {'is_active': False}))

    def test_delegate_may_still_deactivate_ordinary_staff(self):
        # The give-and-take the CFO asked for must survive the rule above.
        self.assertIsNone(delegate_refusal(self.unopa, self.staff, {'is_active': False}))

    def test_a_non_delegate_is_refused_outright(self):
        self.assertIsNotNone(
            delegate_refusal(self.staff, self.admin, {'title': T.OPERATIONS}))
