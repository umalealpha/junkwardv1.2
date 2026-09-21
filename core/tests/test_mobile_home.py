"""Tests for the single mobile Home payload (Omni Mobile B)."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from core.mobile_capabilities import MOBILE_CAPABILITY_KEYS
from core.models import UserProfile

User = get_user_model()


class MobileHomeTests(TestCase):
    def setUp(self):
        self.u = User.objects.create_user('mh', email='mh@x', password='x', first_name='Mo')
        UserProfile.objects.create(
            user=self.u, role=UserProfile.Role.OPERATIONS_STAFF,
            title=UserProfile.Title.OPERATIONS, is_active=True,
        )
        self.c = APIClient()
        self.c.force_authenticate(user=self.u)

    def test_home_returns_one_combined_payload(self):
        r = self.c.get('/api/v1/mobile/home/')
        self.assertEqual(r.status_code, 200)
        for key in ('as_of', 'first_name', 'capabilities', 'approvals', 'my_tasks_open'):
            self.assertIn(key, r.data)
        self.assertEqual(r.data['first_name'], 'Mo')
        self.assertIn('streams', r.data['approvals'])
        self.assertIn('total', r.data['approvals'])
        self.assertEqual(set(r.data['capabilities']), set(MOBILE_CAPABILITY_KEYS))
        self.assertIsInstance(r.data['my_tasks_open'], int)

    def test_home_requires_auth(self):
        r = APIClient().get('/api/v1/mobile/home/')
        self.assertIn(r.status_code, (401, 403))
