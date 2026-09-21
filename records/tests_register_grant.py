"""
records/tests_register_grant.py

A NAMED person can be admitted to the records register without changing their job
title (2026-08-25, Tlotlo Maswabi's request that Goitsemang Ngwako be able to
cover for her).

The gap these tests close: `view_restricted_records` could already be granted to
one person, but OPENING the register came only from a job title. So the only ways
to admit a second Records Officer were to change her title — which in Omni also
changes her approval rights on payments and purchase orders — or to admit her
whole title. Every test below fails on the pre-fix code.
"""
from django.contrib.auth.models import Group, Permission, User
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import UserProfile

REGISTER = '/api/v1/records/'


class RegisterNamedGrantTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Goitsemang's real position: a Records Officer whose title is `operations`.
        cls.officer = User.objects.create_user(
            'records.officer', email='ro@alphadirect.co.bw', password='x')
        UserProfile.objects.create(user=cls.officer,
                                   title=UserProfile.Title.OPERATIONS)
        # A second operational staffer who was NOT granted anything.
        cls.other_ops = User.objects.create_user(
            'other.ops', email='ops@alphadirect.co.bw', password='x')
        UserProfile.objects.create(user=cls.other_ops,
                                   title=UserProfile.Title.OPERATIONS)

    @staticmethod
    def _perm():
        return Permission.objects.get(codename='view_records_register')

    def setUp(self):
        self.client = APIClient()

    def test_the_permission_exists(self):
        """A grant nobody can make is not a grant."""
        self.assertTrue(
            Permission.objects.filter(codename='view_records_register').exists())

    def test_an_operational_title_alone_is_still_refused(self):
        self.client.force_authenticate(user=self.officer)
        r = self.client.get(REGISTER)
        self.assertEqual(r.status_code, 403, r.content)

    def test_the_named_grant_opens_the_register(self):
        self.officer.user_permissions.add(self._perm())
        self.officer = User.objects.get(pk=self.officer.pk)   # drop the perm cache
        self.client.force_authenticate(user=self.officer)
        r = self.client.get(REGISTER)
        self.assertEqual(r.status_code, 200, r.content)

    def test_the_grant_does_not_leak_to_her_colleagues(self):
        """This is the whole reason it is a per-person grant and not a widened
        title: admitting one Records Officer must not admit every operational
        staff member."""
        self.officer.user_permissions.add(self._perm())
        self.client.force_authenticate(user=self.other_ops)
        r = self.client.get(REGISTER)
        self.assertEqual(r.status_code, 403, r.content)

    def test_a_group_grant_works_too(self):
        """A "Records officers" group is a legitimate way to hold this — the
        existing restricted-records docstring says so explicitly."""
        g = Group.objects.create(name='Records officers')
        g.permissions.add(self._perm())
        self.officer.groups.add(g)
        self.officer = User.objects.get(pk=self.officer.pk)
        self.client.force_authenticate(user=self.officer)
        self.assertEqual(self.client.get(REGISTER).status_code, 200)

    def test_revoking_it_closes_the_door_again(self):
        """Revocable in the admin, never by a code change."""
        self.officer.user_permissions.add(self._perm())
        self.officer = User.objects.get(pk=self.officer.pk)
        self.client.force_authenticate(user=self.officer)
        self.assertEqual(self.client.get(REGISTER).status_code, 200)

        self.officer.user_permissions.remove(self._perm())
        self.officer = User.objects.get(pk=self.officer.pk)
        self.client.force_authenticate(user=self.officer)
        self.assertEqual(self.client.get(REGISTER).status_code, 403)

    def test_opening_the_register_is_not_the_same_as_seeing_restricted_records(self):
        """Two separate grants on purpose. Being let into the register must not, on
        its own, expose staff personal files."""
        self.officer.user_permissions.add(self._perm())
        self.officer = User.objects.get(pk=self.officer.pk)
        self.assertTrue(self.officer.has_perm('records.view_records_register'))
        self.assertFalse(self.officer.has_perm('records.view_restricted_records'))

    def test_a_title_that_always_had_it_is_unaffected(self):
        """The pre-existing route in must keep working — Tlotlo is an accountant."""
        acct = User.objects.create_user('acct', email='a@alphadirect.co.bw', password='x')
        UserProfile.objects.create(user=acct, title=UserProfile.Title.ACCOUNTANT)
        self.client.force_authenticate(user=acct)
        self.assertEqual(self.client.get(REGISTER).status_code, 200)
