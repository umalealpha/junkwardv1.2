"""
Security Posture dashboard — access control and register integrity.

The dashboard names our own weaknesses and the exact files they live in. That
makes it one of the most sensitive pages in Omni: a leak of this page is a map
for an attacker. So the access tests here matter more than the rendering ones.

Also pins two things that have gone wrong before in this codebase: a page
shipped with no menu link, and a register whose percentage silently disagreed
with its own rows.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from core.security_dashboard import (_FINDINGS, _findings, _summary,
                                     _sprints, can_view_security_dashboard,
                                     security_access, security_dashboard)

User = get_user_model()


class SecurityDashboardAccessTests(TestCase):
    """Who may read our own list of weaknesses."""

    def setUp(self):
        self.rf = APIRequestFactory(SERVER_NAME='omni.alphadirect.co.bw')

    def _get(self, user, view=security_dashboard):
        req = self.rf.get('/x/')
        if user is not None:
            force_authenticate(req, user=user)
        return view(req)

    def _user(self, username, email, **kw):
        return User.objects.create_user(username, email=email, password='x', **kw)

    def test_an_anonymous_caller_gets_nothing(self):
        resp = self._get(None)
        self.assertIn(resp.status_code, (401, 403))

    def test_an_ordinary_staff_member_is_refused(self):
        # The whole point: a junior clerk must not be handed a map of our gaps.
        clerk = self._user('sec-test-clerk', 'sec-test-clerk@alphadirect.co.bw')
        resp = self._get(clerk)
        self.assertEqual(resp.status_code, 403)
        self.assertNotIn('findings', resp.data)

    def test_a_plain_administrator_flag_is_not_enough(self):
        # is_staff is not the C-suite. Only CEO/COO/CFO or a superuser.
        someone = self._user('sec-test-staff', 'sec-test-staff@alphadirect.co.bw',
                             is_staff=True)
        self.assertEqual(self._get(someone).status_code, 403)

    def test_the_cfo_can_read_it(self):
        cfo = self._user('sec-test-cfo', 'pganesharajah@alphadirect.co.bw')
        resp = self._get(cfo)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data['findings'])

    def test_the_ceo_can_read_it(self):
        ceo = self._user('sec-test-ceo', 'aiyer@alphadirect.co.bw')
        self.assertEqual(self._get(ceo).status_code, 200)

    def test_the_coo_can_read_it(self):
        coo = self._user('sec-test-coo', 'arjuniyer@alphadirect.co.bw')
        self.assertEqual(self._get(coo).status_code, 200)

    def test_a_superuser_can_read_it(self):
        su = self._user('sec-test-su', 'sec-test-su@alphadirect.co.bw', is_superuser=True)
        self.assertEqual(self._get(su).status_code, 200)

    def test_the_access_probe_agrees_with_the_real_endpoint(self):
        # The sidebar shows/hides the link off this probe. If it ever said yes
        # while the page said no, an executive would get a broken menu item —
        # and worse, if it said yes to a clerk we would advertise the page.
        for username, email, expected in (
            ('sec-test-p1', 'sec-test-p1@alphadirect.co.bw', False),
            ('sec-test-p2', 'arjuniyer@alphadirect.co.bw', True),
        ):
            user = self._user(username, email)
            probe = self._get(user, view=security_access)
            self.assertEqual(probe.data['allowed'], expected, email)
            self.assertEqual(self._get(user).status_code, 200 if expected else 403)

    def test_the_helper_refuses_none(self):
        self.assertFalse(can_view_security_dashboard(None))


class RegisterIntegrityTests(TestCase):
    """The register must not be able to lie about its own progress."""

    def test_every_finding_carries_a_fix_and_an_owner(self):
        # A finding with no fix is a complaint. The CFO asked for a solution
        # provider, and this test is what keeps that true as rows are added.
        for f in _findings():
            self.assertTrue(f['fix'].strip(), f'{f["id"]} has no fix')
            self.assertTrue(f['owner'].strip(), f'{f["id"]} has no owner')
            self.assertTrue(f['evidence'].strip(), f'{f["id"]} cites no evidence')
            self.assertTrue(f['plain_english'].strip(), f'{f["id"]} has no plain-English line')

    def test_ids_are_unique(self):
        ids = [f['id'] for f in _findings()]
        self.assertEqual(len(ids), len(set(ids)))

    def test_severities_and_statuses_are_known_values(self):
        for f in _findings():
            self.assertIn(f['severity'], {'critical', 'high', 'medium', 'low'})
            self.assertIn(f['status'], {'open', 'in_progress', 'fixed'})
            self.assertIn(f['effort'], {'quick', 'sprint', 'architectural'})
            self.assertIn(f['sprint'], {1, 2, 3})

    def test_the_percentage_matches_the_rows(self):
        fs = _findings()
        s = _summary(fs)
        self.assertEqual(s['fixed'] + s['in_progress'] + s['open'], s['total_findings'])
        expected = round(100 * (s['fixed'] + 0.5 * s['in_progress']) / len(fs))
        self.assertEqual(s['pct_complete'], expected)

    def test_nothing_fixed_yet_reads_as_zero_not_as_done(self):
        # Guards the direction of the maths: an all-open register must not
        # render as 100% complete.
        s = _summary(_findings())
        if not any(f['status'] == 'fixed' for f in _findings()):
            self.assertLess(s['pct_complete'], 100)

    def test_open_counts_by_severity_exclude_fixed_items(self):
        fs = _findings()
        s = _summary(fs)
        self.assertEqual(
            sum(s['open_by_severity'].values()),
            len([f for f in fs if f['status'] != 'fixed']))

    def test_every_finding_appears_in_exactly_one_sprint(self):
        fs = _findings()
        placed = sum(sp['total'] for sp in _sprints(fs))
        self.assertEqual(placed, len(fs), 'a finding fell outside the plan')

    def test_the_register_is_not_empty(self):
        self.assertGreaterEqual(len(_FINDINGS), 1)
