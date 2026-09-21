"""BONU is open to the BONU team, and to nobody else it was not already open to.

CFO 2026-08-11: Patience Phesodi leads the BONU team but her title is `operations`,
so `can_view_financials` — a property derived from the title — refused her. Making
her finance would have granted the general ledger, the reports, the dashboards and
the audit log along with it. So the gate now also accepts a named BONU-team member,
and that is the ONLY thing it widens.
"""
from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from bonu.access import (bonu_team_emails, can_capture_claim, can_view_bonu,
                         is_bonu_team)
from core.models import Company, UserProfile, get_user_profile

PATIENCE = 'pphesodi@alphadirect.co.bw'


class BonuTeamAccessTests(TestCase):
    def setUp(self):
        self.company = (Company.objects.filter(code='ADIC').first()
                        or Company.objects.create(code='ADIC', name='ADIC'))

    def _user(self, email, title=None):
        u = User.objects.create_user(email.split('@')[0], email=email, password='x')
        if title:
            UserProfile.objects.update_or_create(
                user=u, defaults={'title': title, 'is_active': True})
        return User.objects.get(pk=u.pk)

    def test_the_bonu_team_lead_is_in_the_named_list(self):
        self.assertIn(PATIENCE, bonu_team_emails())

    def test_she_gets_in_despite_an_operations_title(self):
        u = self._user(PATIENCE, UserProfile.Title.OPERATIONS)
        self.assertFalse(u.profile.can_view_financials,
                         'the financials gate must still refuse her — that is the point')
        self.assertTrue(can_view_bonu(u))

    def test_an_unrelated_operations_user_is_still_refused(self):
        """The widening is a named list, not a title. Nobody else comes with her."""
        u = self._user('someone.else@alphadirect.co.bw', UserProfile.Title.OPERATIONS)
        self.assertFalse(is_bonu_team(u))
        self.assertFalse(can_view_bonu(u))

    def test_finance_still_gets_in(self):
        u = self._user('an.accountant@alphadirect.co.bw', UserProfile.Title.ACCOUNTANT)
        self.assertTrue(can_view_bonu(u))

    def test_a_superuser_gets_in(self):
        u = User.objects.create_superuser('bonu-su', 'su@example.invalid', 'x')
        self.assertTrue(can_view_bonu(u))

    def test_an_anonymous_user_is_refused(self):
        from django.contrib.auth.models import AnonymousUser
        self.assertFalse(can_view_bonu(AnonymousUser()))
        self.assertFalse(can_view_bonu(None))

    def test_a_member_can_be_added_with_no_deploy(self):
        """The env override is what lets the CFO add a BONU team member himself."""
        import os
        os.environ['BONU_TEAM_EMAILS'] = 'newmember@alphadirect.co.bw'
        try:
            u = self._user('newmember@alphadirect.co.bw', UserProfile.Title.OPERATIONS)
            self.assertTrue(can_view_bonu(u))
        finally:
            os.environ.pop('BONU_TEAM_EMAILS', None)

    def test_the_endpoint_itself_lets_her_through(self):
        from rest_framework.test import APIRequestFactory, force_authenticate

        from bonu.views import dashboard
        u = self._user(PATIENCE, UserProfile.Title.OPERATIONS)
        req = APIRequestFactory().get('/api/v1/bonu/dashboard/')
        force_authenticate(req, user=u)
        resp = dashboard(req)
        self.assertNotEqual(resp.status_code, 403,
                            'she must not be refused at the endpoint either')


class NamedTeamMembersTests(TestCase):
    """The two people who are on the BONU team by NAME, not by title.

    Both have title `operations`, so `can_view_financials` is False and the
    plain financials gate refuses them. They are named in `BONU_TEAM_EMAILS`
    instead of being made finance, which would hand them the general ledger,
    the financial reports and the audit log. This test pins WHO is on that list
    and, just as importantly, that being on it grants BONU and nothing wider.
    """

    def setUp(self):
        self.company = (Company.objects.filter(code='ADIC').first()
                        or Company.objects.create(code='ADIC', name='ADIC'))

    def _ops_user(self, email):
        u = User.objects.create_user(email.split('@')[0], email=email, password='x')
        UserProfile.objects.update_or_create(
            user=u, defaults={'title': 'operations', 'is_active': True})
        return User.objects.get(pk=u.pk)

    def _titled_user(self, email, title):
        u = User.objects.create_user(email.split('@')[0], email=email, password='x')
        UserProfile.objects.update_or_create(
            user=u, defaults={'title': title, 'is_active': True})
        return User.objects.get(pk=u.pk)

    def test_kelvin_kimani_can_open_bonu(self):
        # CFO directive 9 Sep 2026. He wrote the Legal claim-intake and
        # bill-capture spec and could not open either screen.
        u = self._ops_user('kkimani@alphadirect.co.bw')
        self.assertTrue(can_view_bonu(u))
        self.assertTrue(can_capture_claim(u))

    def test_karabo_borupile_can_open_bonu(self):
        # CFO directive 15 Sep 2026: "kindly give him full access to BONU".
        # His title is `claims_intern`, NOT operations — so this also proves the
        # gate is by name and does not depend on any particular title.
        u = self._titled_user('kborupile@alphadirect.co.bw', 'claims_intern')
        self.assertFalse(get_user_profile(u).can_view_financials,
                         'the financials gate must still refuse him — that is the point')
        self.assertTrue(can_view_bonu(u))
        self.assertTrue(can_capture_claim(u))

    def test_karabo_gets_bonu_and_nothing_wider(self):
        u = self._titled_user('kborupile@alphadirect.co.bw', 'claims_intern')
        prof = get_user_profile(u)
        self.assertFalse(prof.can_view_financials)
        self.assertFalse(u.is_superuser)

    def test_mbako_salani_can_open_bonu(self):
        # CFO directive 15 Sep 2026 — Kelvin Kimani requested BONU Legal access
        # for Mbako, who takes the legal work over from Kutlo Keitumele. His
        # title is `operations`, so the financials gate refuses him.
        u = self._ops_user('msalani@alphadirect.co.bw')
        self.assertFalse(get_user_profile(u).can_view_financials,
                         'the financials gate must still refuse him — that is the point')
        self.assertTrue(can_view_bonu(u))
        self.assertTrue(can_capture_claim(u))

    def test_mbako_gets_bonu_and_nothing_wider(self):
        u = self._ops_user('msalani@alphadirect.co.bw')
        prof = get_user_profile(u)
        self.assertFalse(prof.can_view_financials)
        self.assertFalse(u.is_superuser)
        self.assertFalse(prof.is_administrator)

    def test_another_claims_intern_is_still_refused(self):
        # Proves the grant is by NAME and has not widened to every intern.
        u = self._titled_user('other.intern@alphadirect.co.bw', 'claims_intern')
        self.assertFalse(can_view_bonu(u))

    def test_patience_phesodi_still_can(self):
        u = self._ops_user('pphesodi@alphadirect.co.bw')
        self.assertTrue(can_view_bonu(u))

    def test_the_list_grants_bonu_and_nothing_wider(self):
        # The whole reason for naming people here rather than making them
        # finance: BONU access must not become ledger access.
        u = self._ops_user('kkimani@alphadirect.co.bw')
        prof = get_user_profile(u)
        self.assertFalse(prof.can_view_financials)
        self.assertFalse(u.is_superuser)

    def test_another_operations_person_is_still_refused(self):
        # Proves the grant is by NAME and has not accidentally widened to
        # everyone carrying the `operations` title.
        u = self._ops_user('someone.else@alphadirect.co.bw')
        self.assertFalse(can_view_bonu(u))
