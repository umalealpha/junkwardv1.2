"""CFO grant, 11 Aug 2026: one named person may see restricted records.

The Records officer physically holds the personnel files that were hidden from
her on screen. Her title is `accountant`. The grant must reach HER and must NOT
reach every other accountant in the group — that is the whole point of doing it
as a per-user permission rather than by widening RESTRICTED_TITLES.

No real employee name appears here; the fixture references are invented.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import Company, UserProfile
from records.api_views import _may_see_restricted
from records.models import RecordCategory, RecordItem

User = get_user_model()


class RestrictedGrantTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(code='GRT', name='Grant Co.')
        cls.cat = RecordCategory.objects.create(name='Files', retention_months=72)
        for i in range(3):
            RecordItem.objects.create(
                reference=f'G-{i}', title='Personnel file', category=cls.cat,
                company=cls.company, current_location='Store',
                confidentiality=RecordItem.Confidentiality.RESTRICTED)
        RecordItem.objects.create(
            reference='G-INT', title='Policy copy', category=cls.cat,
            company=cls.company, current_location='Store',
            confidentiality=RecordItem.Confidentiality.INTERNAL)

    def _accountant(self, username):
        u = User.objects.create_user(username, f'{username}@alphadirect.co.bw', 'x')
        UserProfile.objects.update_or_create(
            user=u, defaults={'title': UserProfile.Title.ACCOUNTANT})
        return u

    def _grant(self, user):
        user.user_permissions.add(
            Permission.objects.get(codename='view_restricted_records'))
        return User.objects.get(pk=user.pk)      # drop the perm cache

    def test_the_permission_exists(self):
        self.assertTrue(
            Permission.objects.filter(codename='view_restricted_records').exists())

    def test_an_accountant_without_the_grant_still_cannot_see_restricted(self):
        u = self._accountant('plain')
        self.assertFalse(_may_see_restricted(u))
        c = APIClient(); c.force_authenticate(user=u)
        r = c.get('/api/v1/records/')
        self.assertEqual(len(r.data['results']), 1)          # the internal one only
        self.assertEqual(r.data['restricted_hidden'], 3)

    def test_the_granted_accountant_sees_every_record(self):
        u = self._grant(self._accountant('granted'))
        self.assertTrue(_may_see_restricted(u))
        c = APIClient(); c.force_authenticate(user=u)
        r = c.get('/api/v1/records/')
        self.assertEqual(len(r.data['results']), 4)
        # ...and is no longer told anything is being withheld
        self.assertNotIn('restricted_hidden', r.data)

    def test_the_grant_does_not_leak_to_another_accountant(self):
        """The reason this is a per-user permission and not a title change."""
        self._grant(self._accountant('granted2'))
        other = self._accountant('colleague')
        self.assertFalse(_may_see_restricted(other))
        c = APIClient(); c.force_authenticate(user=other)
        r = c.get('/api/v1/records/')
        self.assertEqual(len(r.data['results']), 1)

    def test_a_group_grant_also_works_and_is_deliberate(self):
        """has_perm honours groups too — asserted so it can never surprise anyone."""
        from django.contrib.auth.models import Group
        g = Group.objects.create(name='Records officers')
        g.permissions.add(Permission.objects.get(codename='view_restricted_records'))
        u = self._accountant('viagroup')
        u.groups.add(g)
        u = User.objects.get(pk=u.pk)             # drop the perm cache
        self.assertTrue(_may_see_restricted(u))
