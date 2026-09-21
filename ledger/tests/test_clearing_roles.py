"""
Voucher-clearing access parity — the four clearing surfaces must agree on who
is a maker / checker (residue of the 2026-08-21 voucher-clearing access
incident).

Workstream A (2026-07-02) widened the SUBMIT (``perform_create``) and DECIDE
(``_decide``) gates from the legacy ``voucher_clearing_maker`` /
``voucher_clearing_approver`` groups to the title-aware SoD capabilities
(``can_originate_controlled_txn`` / ``can_check_controlled_txn``) but left
``roles()`` and ``snapshot()`` on the old group-only check. Result: a
title-only maker/checker (no group membership) could submit and approve a
clearing, yet ``roles()`` told the UI they had no role and ``snapshot()``
returned 403 on the very request they had decided.

These tests pin all four surfaces to one predicate:

    maker   = ``voucher_clearing_maker`` group    OR  can_originate_controlled_txn
    checker = ``voucher_clearing_approver`` group  OR  can_check_controlled_txn

and prove an outsider (neither) is still denied so the widening does not open
the surfaces to everyone.
"""
from __future__ import annotations

from django.contrib.auth.models import Group, User
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from core.models import UserProfile
from ledger.api_views import JEClearingRequestViewSet
from ledger.models import JEClearingRequest


def _user(username, title):
    """Title-only finance user: no clearing group, not staff, not superuser."""
    u = User.objects.create_user(username=username, password='x')
    UserProfile.objects.update_or_create(
        user=u,
        defaults={'role': UserProfile.Role.ACCOUNTANT, 'title': title,
                  'is_active': True},
    )
    return u


def _grouped(username, group_name):
    """Legacy-group member with a NON-finance title (Operations) — grants only
    via the group leg of the predicate, never the title leg. Proves the OR's
    legacy branch survives the refactor (a transposed group constant inside
    the helper would make these go red)."""
    u = _user(username, UserProfile.Title.OPERATIONS)
    grp, _ = Group.objects.get_or_create(name=group_name)
    u.groups.add(grp)
    return u


class ClearingRolesEndpointTests(TestCase):
    """roles() must reflect the SoD capabilities, not just group membership."""

    def setUp(self):
        self.factory = APIRequestFactory()

    def _roles(self, user):
        req = self.factory.get('/api/v1/je-clearings/roles/')
        force_authenticate(req, user=user)
        return JEClearingRequestViewSet.as_view({'get': 'roles'})(req)

    def test_title_only_maker_is_reported_as_maker(self):
        ac = _user('cr_ac', UserProfile.Title.ACCOUNTANT)
        resp = self._roles(ac)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data['is_maker'])        # title maker, no group
        self.assertFalse(resp.data['is_approver'])

    def test_title_only_checker_is_reported_as_approver(self):
        fm = _user('cr_fm', UserProfile.Title.FINANCE_MANAGER)
        resp = self._roles(fm)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data['is_approver'])     # title checker, no group
        self.assertFalse(resp.data['is_maker'])

    def test_outsider_has_no_clearing_role(self):
        op = _user('cr_op', UserProfile.Title.OPERATIONS)
        resp = self._roles(op)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data['is_maker'])
        self.assertFalse(resp.data['is_approver'])

    def test_group_only_maker_is_reported_as_maker(self):
        u = _grouped('cr_gm', 'voucher_clearing_maker')
        resp = self._roles(u)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data['is_maker'])        # legacy group leg survives
        self.assertFalse(resp.data['is_approver'])

    def test_group_only_approver_is_reported_as_approver(self):
        u = _grouped('cr_ga', 'voucher_clearing_approver')
        resp = self._roles(u)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data['is_approver'])     # legacy group leg survives
        self.assertFalse(resp.data['is_maker'])


class ClearingSnapshotAccessTests(TestCase):
    """snapshot() must be readable by the same maker/checker population that
    can submit and decide — a title-only maker/checker included."""

    def setUp(self):
        self.factory = APIRequestFactory()
        submitter = _user('cr_sub', UserProfile.Title.SENIOR_ACCOUNTANT)
        self.req = JEClearingRequest.objects.create(
            reason='duplicate posting',
            status=JEClearingRequest.Status.APPROVED,
            submitted_by=submitter,
            bulk_scope={'company': 'ADI', 'from_date': '2026-01-01',
                        'to_date': '2026-01-31'},
            deleted_je_snapshot={'entries': []},
            deleted_count=1,
        )

    def _snapshot(self, user):
        req = self.factory.get(f'/api/v1/je-clearings/{self.req.pk}/snapshot/')
        force_authenticate(req, user=user)
        return JEClearingRequestViewSet.as_view({'get': 'snapshot'})(
            req, pk=str(self.req.pk))

    def test_title_only_maker_can_read_snapshot(self):
        ac = _user('cr_ac2', UserProfile.Title.ACCOUNTANT)
        resp = self._snapshot(ac)
        self.assertEqual(resp.status_code, 200)       # was 403 (group-only gate)
        self.assertEqual(resp.data['deleted_count'], 1)

    def test_title_only_checker_can_read_snapshot(self):
        fm = _user('cr_fm2', UserProfile.Title.FINANCE_MANAGER)
        resp = self._snapshot(fm)
        self.assertEqual(resp.status_code, 200)       # was 403 (group-only gate)

    def test_outsider_cannot_read_snapshot(self):
        op = _user('cr_op2', UserProfile.Title.OPERATIONS)
        resp = self._snapshot(op)
        self.assertEqual(resp.status_code, 403)       # widening must not open it up

    def test_group_only_member_can_read_snapshot(self):
        u = _grouped('cr_gs', 'voucher_clearing_maker')
        resp = self._snapshot(u)
        self.assertEqual(resp.status_code, 200)       # legacy group leg survives on snapshot
