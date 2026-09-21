"""SEC-02 — "no entity restriction recorded" must mean YOUR entity, not ALL of them.

We run 13 companies. `scoped_company_ids` used to treat a user with no
UserCompanyAccess row exactly like a superuser: no filter, see everything. The
sibling helper `CompanyScopedViewSetMixin` treated the same user as denied, so
the two disagreed about one person. A brand-new account — nobody had restricted
it yet — could read every entity.
"""
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from core.mixins import scoped_company_ids
from core.models import Company, UserCompanyAccess, UserProfile
from payroll.models import Employee


class EntityScopeDefaultTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.a = Company.objects.create(code='ESA', name='Entity A')
        cls.b = Company.objects.create(code='ESB', name='Entity B')
        cls.rf = APIRequestFactory()

    def _req(self, user, company=None):
        # DRF Request — the helper reads .query_params, which a plain
        # WSGIRequest does not have.
        r = Request(self.rf.get('/x' + (f'?company={company}' if company else '')))
        r.user = user
        return r

    def _staff(self, username, company):
        u = User.objects.create_user(username, f'{username}@alphadirect.co.bw', 'x')
        Employee.objects.create(company=company, employee_number=username.upper(),
                                full_name=username, user=u,
                                email=f'{username}@alphadirect.co.bw')
        return u

    # ── the hole ────────────────────────────────────────────────────────────
    def test_a_user_with_no_grant_sees_only_their_own_entity(self):
        u = self._staff('esplain', self.a)
        self.assertEqual(scoped_company_ids(self._req(u)), [str(self.a.id)])

    def test_they_cannot_reach_another_entity_by_asking_for_it(self):
        u = self._staff('esask', self.a)
        self.assertEqual(scoped_company_ids(self._req(u, company=self.b.id)), [],
                         'asking for another entity must return nothing')

    def test_they_can_still_ask_for_their_own(self):
        u = self._staff('esown', self.a)
        self.assertEqual(scoped_company_ids(self._req(u, company=self.a.id)),
                         [str(self.a.id)])

    def test_no_grant_and_no_employee_record_shows_nothing_not_everything(self):
        """Fail CLOSED. We cannot tell which entity they belong to, so we must
        not fall back to showing all 13."""
        u = User.objects.create_user('esghost', 'esghost@alphadirect.co.bw', 'x')
        self.assertEqual(scoped_company_ids(self._req(u)), [])

    # ── what must NOT change ────────────────────────────────────────────────
    def test_a_superuser_is_still_unrestricted(self):
        u = User.objects.create_user('essuper', 'essuper@alphadirect.co.bw', 'x')
        u.is_superuser = True
        u.save(update_fields=['is_superuser'])
        self.assertIsNone(scoped_company_ids(self._req(u)))

    def test_the_cfo_is_still_unrestricted(self):
        u = User.objects.create_user('escfo', 'escfo@alphadirect.co.bw', 'x')
        UserProfile.objects.update_or_create(
            user=u, defaults={'title': UserProfile.Title.CFO, 'is_active': True})
        self.assertIsNone(scoped_company_ids(self._req(u)))

    def test_hr_keeps_the_consolidated_view(self):
        """This leniency existed FOR the consolidated HRIS view. HR holds
        'view_all', so they must still see every entity at once."""
        u = User.objects.create_user('eshr', 'eshr@alphadirect.co.bw', 'x')
        UserProfile.objects.update_or_create(
            user=u, defaults={'title': UserProfile.Title.HR_MANAGER, 'is_active': True})
        from core.hris_access import ROLE_CAPABILITIES, hris_role
        self.assertIn('view_all', ROLE_CAPABILITIES.get(hris_role(u), set()),
                      'fixture must actually hold view_all or this proves nothing')
        self.assertIsNone(scoped_company_ids(self._req(u)))

    def test_an_explicitly_granted_user_is_unchanged(self):
        u = self._staff('esgrant', self.a)
        UserCompanyAccess.objects.create(user=u, company=self.b, can_view=True)
        self.assertEqual(scoped_company_ids(self._req(u)), [str(self.b.id)])

    # ── the escape hatch ────────────────────────────────────────────────────
    @override_settings(OMNI_ENTITY_SCOPE_STRICT=False)
    def test_the_switch_restores_the_old_behaviour(self):
        """If something legitimate turns out to be hidden, this is the one env
        var that puts it back — no deploy."""
        u = self._staff('esoff', self.a)
        self.assertIsNone(scoped_company_ids(self._req(u)))
