"""/api/v1/user-profiles/me/ for a user who has no profile row.

On prod 2026-07-29 that was 58 of 188 active users, and they got a 404 that the
app showed as a red "Request failed" toast. Asking who I am is not a failure.

The two things that must both hold: it answers, and it grants nothing.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from core.models import UserProfile

U = get_user_model()

# Every permission the serializer exposes. All must be false without a profile.
CAPABILITIES = [
    'can_approve_journal_entries', 'can_create_journal_entries',
    'can_approve_payroll', 'can_administer_users', 'can_post_directly',
    'can_manage_periods', 'is_payroll_processor',
    'can_view_internal_audit', 'can_edit_internal_audit',
    'is_administrator',
    # CFO 2026-09-15 — the limited access administrator. A user with no
    # profile row must not be one; caught by CI on PR #1049 when the key was
    # added to the serializer and not to this shape.
    'is_access_delegate',
]


@override_settings(ALLOWED_HOSTS=['*'])
class MeWithoutProfileTests(TestCase):
    URL = '/api/v1/user-profiles/me/'

    def setUp(self):
        self.client = APIClient()

    def test_answers_200_instead_of_404(self):
        user = U.objects.create_user('noprofile@alphadirect.co.bw',
                                     email='noprofile@alphadirect.co.bw',
                                     first_name='No', last_name='Profile')
        self.assertIsNone(getattr(user, 'profile', None))
        self.client.force_authenticate(user=user)

        r = self.client.get(self.URL)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIs(r.json()['has_profile'], False)

    def test_grants_nothing(self):
        """The dangerous failure would be answering 200 with real permissions."""
        user = U.objects.create_user('nobody@alphadirect.co.bw')
        self.client.force_authenticate(user=user)
        body = self.client.get(self.URL).json()
        for cap in CAPABILITIES:
            self.assertIs(body[cap], False, f'{cap} must be false without a profile')
        self.assertIsNone(body['role'])
        self.assertIsNone(body['title'])

    def test_does_not_create_a_profile_behind_the_scenes(self):
        """title defaults to ACCOUNTANT, which may create journal entries and read
        financials — loading a page must never confer that."""
        user = U.objects.create_user('stilnobody@alphadirect.co.bw')
        self.client.force_authenticate(user=user)
        self.client.get(self.URL)
        self.assertFalse(UserProfile.objects.filter(user=user).exists())

    def test_identity_still_comes_through(self):
        user = U.objects.create_user('who@alphadirect.co.bw',
                                     email='who@alphadirect.co.bw',
                                     first_name='Who', last_name='Ami')
        self.client.force_authenticate(user=user)
        body = self.client.get(self.URL).json()
        self.assertEqual(body['first_name'], 'Who')
        self.assertEqual(body['last_name'], 'Ami')
        self.assertEqual(body['email'], 'who@alphadirect.co.bw')

    def test_a_real_profile_is_unchanged_and_flagged(self):
        user = U.objects.create_user('real@alphadirect.co.bw')
        UserProfile.objects.create(user=user, role=UserProfile.Role.ACCOUNTANT,
                                   title=UserProfile.Title.CFO)
        self.client.force_authenticate(user=user)
        body = self.client.get(self.URL).json()
        self.assertIs(body['has_profile'], True)
        self.assertEqual(body['title'], UserProfile.Title.CFO)
        self.assertIs(body['can_approve_journal_entries'], True)

    def test_both_shapes_carry_the_same_keys(self):
        """So the frontend needs no special case beyond has_profile."""
        with_user = U.objects.create_user('shape1@alphadirect.co.bw')
        UserProfile.objects.create(user=with_user, role=UserProfile.Role.ACCOUNTANT,
                                   title=UserProfile.Title.ACCOUNTANT)
        without = U.objects.create_user('shape2@alphadirect.co.bw')

        self.client.force_authenticate(user=with_user)
        a = set(self.client.get(self.URL).json())
        self.client.force_authenticate(user=without)
        b = set(self.client.get(self.URL).json())
        self.assertEqual(a - b, set(), f'missing from the no-profile answer: {a - b}')
