"""The forgiveness watch opens wider than the CFO's other screens (CFO 2026-09-09).

CFO + CEO + HR can see it (is_hr_doc_admin). The build log and the job switches
stay the CFO's alone — this test guards that the widening did not leak to them.
"""
from django.contrib.auth.models import User
from django.test import TestCase

from core.models import UserProfile, get_user_profile


def _user(username, email, title):
    u = User.objects.create_user(username=username, email=email, password='x')
    UserProfile.objects.create(user=u, role=UserProfile.Role.choices[0][0],
                               title=title, is_active=True)
    u.refresh_from_db()
    return u


class ForgivenessGateTests(TestCase):
    def test_the_hr_manager_can_see_forgiveness(self):
        u = _user('unami', 'ubutale@alphadirect.co.bw', UserProfile.Title.HR_MANAGER)
        self.client.force_login(u)
        self.assertEqual(self.client.get('/api/v1/cfo/forgiveness/?ai=0').status_code, 200)

    def test_the_ceo_can_see_forgiveness(self):
        u = _user('arun.iyer', 'aiyer@alphadirect.co.bw', UserProfile.Title.CEO)
        self.client.force_login(u)
        self.assertEqual(self.client.get('/api/v1/cfo/forgiveness/?ai=0').status_code, 200)

    def test_the_cfo_can_still_see_forgiveness(self):
        u = _user('pganesharajah', 'pganesharajah@alphadirect.co.bw', UserProfile.Title.CFO)
        self.client.force_login(u)
        self.assertEqual(self.client.get('/api/v1/cfo/forgiveness/?ai=0').status_code, 200)

    def test_an_ordinary_staff_member_cannot(self):
        u = _user('nobody', 'nobody@alphadirect.co.bw', UserProfile.Title.FINANCE_MANAGER)
        self.client.force_login(u)
        self.assertIn(self.client.get('/api/v1/cfo/forgiveness/').status_code, (401, 403))

    def test_the_ceo_still_cannot_see_the_build_log_or_the_switches(self):
        """The widening is forgiveness-only. These two stay the CFO's alone."""
        u = _user('arun.iyer', 'aiyer@alphadirect.co.bw', UserProfile.Title.CEO)
        self.client.force_login(u)
        self.assertIn(self.client.get('/api/v1/cfo/build-log/').status_code, (401, 403))
        self.assertIn(self.client.get('/api/v1/cfo/jobs/').status_code, (401, 403))

    def test_the_hr_manager_cannot_see_the_switches(self):
        u = _user('unami', 'ubutale@alphadirect.co.bw', UserProfile.Title.HR_MANAGER)
        self.client.force_login(u)
        self.assertIn(self.client.get('/api/v1/cfo/jobs/').status_code, (401, 403))
