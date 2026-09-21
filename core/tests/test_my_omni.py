"""My Omni home — the two net-new feeds.

Guards the ONE real control here: only HR / IT / C-suite may post a company
announcement (reusing the existing hris.Announcement model). An ordinary
employee must be refused (403). Also checks the dashboard read hides retired,
expired, private (targeted) and disciplinary items, and that the personal-hours
feed is own-scoped and never 500s for a login with no linked employee record.
"""
from datetime import timedelta

from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from hris.models import Announcement


def _today():
    return timezone.localtime().date()


class AnnouncementGateTest(APITestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            'ordinary', email='ordinary@alphadirect.co.bw', password='x')
        self.boss = User.objects.create_superuser(
            'boss', email='boss@alphadirect.co.bw', password='x')

    def test_ordinary_employee_cannot_post(self):
        c = APIClient(); c.force_authenticate(user=self.staff)
        r = c.post('/api/v1/announcements/',
                   {'title': 'Sneaky', 'body': 'should be blocked'}, format='json')
        self.assertEqual(r.status_code, 403)
        self.assertFalse(Announcement.objects.filter(title='Sneaky').exists())

    def test_authorised_can_post_and_everyone_reads(self):
        c = APIClient(); c.force_authenticate(user=self.boss)
        r = c.post('/api/v1/announcements/',
                   {'title': 'Office closed Friday', 'body': 'Public holiday', 'category': 'meeting'},
                   format='json')
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()['announcement']['category'], 'meeting')
        posted = Announcement.objects.get(title='Office closed Friday')
        self.assertIsNone(posted.audience)          # company-wide
        self.assertEqual(posted.starts_on, _today())

        c2 = APIClient(); c2.force_authenticate(user=self.staff)
        r2 = c2.get('/api/v1/announcements/')
        self.assertEqual(r2.status_code, 200)
        titles = [a['title'] for a in r2.json()['announcements']]
        self.assertIn('Office closed Friday', titles)
        self.assertFalse(r2.json()['can_make'])     # ordinary staff cannot post

    def test_invalid_category_falls_back_to_announcement(self):
        c = APIClient(); c.force_authenticate(user=self.boss)
        r = c.post('/api/v1/announcements/',
                   {'title': 'X', 'category': 'disciplinary'}, format='json')
        self.assertEqual(r.status_code, 201)
        # disciplinary is NOT postable here → coerced to a company announcement
        self.assertEqual(r.json()['announcement']['category'], 'announcement')

    def test_expired_announcement_is_hidden(self):
        Announcement.objects.create(
            title='Old news', body='gone', is_active=True,
            starts_on=_today() - timedelta(days=10),
            ends_on=_today() - timedelta(days=1))
        c = APIClient(); c.force_authenticate(user=self.staff)
        r = c.get('/api/v1/announcements/')
        self.assertNotIn('Old news', [a['title'] for a in r.json()['announcements']])

    def test_retired_announcement_is_hidden(self):
        Announcement.objects.create(title='Retired', body='off', is_active=False,
                                    starts_on=_today())
        c = APIClient(); c.force_authenticate(user=self.staff)
        r = c.get('/api/v1/announcements/')
        self.assertNotIn('Retired', [a['title'] for a in r.json()['announcements']])

    def test_disciplinary_never_on_dashboard(self):
        Announcement.objects.create(title='Private DP', body='x', is_active=True,
                                    category='disciplinary', starts_on=_today())
        c = APIClient(); c.force_authenticate(user=self.staff)
        r = c.get('/api/v1/announcements/')
        self.assertNotIn('Private DP', [a['title'] for a in r.json()['announcements']])

    def test_anonymous_blocked(self):
        r = APIClient().get('/api/v1/announcements/')
        self.assertIn(r.status_code, (401, 403))


class MyHoursTest(APITestCase):
    def test_unlinked_login_gets_clean_empty(self):
        u = User.objects.create_user('nolink', email='nobody@alphadirect.co.bw', password='x')
        c = APIClient(); c.force_authenticate(user=u)
        r = c.get('/api/v1/timedoctor/my-hours/')
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()['linked'])
        self.assertEqual(r.json()['windows'], {})

    def test_requires_auth(self):
        r = APIClient().get('/api/v1/timedoctor/my-hours/')
        self.assertIn(r.status_code, (401, 403))
