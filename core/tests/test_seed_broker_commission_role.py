"""The [C8] role seeder — because a command no test ever imports ships broken.

This codebase has the receipt: a management command shipped with a SyntaxError
and all six CI checks green, because nothing ever imported it.

Two of these matter more than the rest:

  * the AMBIGUOUS branch, which had never executed anywhere — there are no
    duplicate accounts on the four addresses locally or on prod — and guards
    against handing a role that decides broker pay to whichever of two accounts
    sharing an address happened to sort first;
  * the REVOKED branch, because Finance manages membership on the roles screen
    and a re-run that silently restored someone they had removed would defeat
    the point of [C8].
"""
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from commissions.broker_views import (BROKER_COMMISSION_PERMISSION,
                                      BROKER_COMMISSION_ROLE)
from core.models import Role, UserRoleAssignment, user_has_permission

ROSE = 'rmokgware@alphadirect.co.bw'


def _run(commit=False):
    out = StringIO()
    args = ['seed_broker_commission_role'] + (['--commit'] if commit else [])
    call_command(*args, stdout=out)
    return out.getvalue()


class SeedBrokerCommissionRoleTests(TestCase):

    def setUp(self):
        self.rose = User.objects.create_user('rose.mokgware', ROSE, 'x' * 24)

    def test_a_dry_run_changes_nothing(self):
        output = _run()
        self.assertIn('DRY RUN', output)
        self.assertFalse(Role.objects.filter(code=BROKER_COMMISSION_ROLE).exists())
        self.assertFalse(UserRoleAssignment.objects.exists())

    def test_commit_creates_the_role_and_grants_the_named_member(self):
        output = _run(commit=True)
        self.assertIn('Applied.', output)
        role = Role.objects.get(code=BROKER_COMMISSION_ROLE)
        self.assertIn(BROKER_COMMISSION_PERMISSION,
                      {p.code for p in role.permissions.all()})
        self.assertTrue(UserRoleAssignment.objects.filter(
            user=self.rose, role=role, revoked_at__isnull=True).exists())
        self.assertTrue(user_has_permission(
            User.objects.get(pk=self.rose.pk), BROKER_COMMISSION_PERMISSION))

    def test_running_it_twice_grants_once(self):
        _run(commit=True)
        output = _run(commit=True)
        self.assertIn('ALREADY HELD', output)
        self.assertEqual(UserRoleAssignment.objects.filter(user=self.rose).count(), 1)

    def test_a_revoked_member_is_not_quietly_re_granted(self):
        """Finance took that decision on the roles screen. Honour it."""
        _run(commit=True)
        UserRoleAssignment.objects.filter(user=self.rose).update(
            revoked_at=timezone.now())
        output = _run(commit=True)
        self.assertIn('WAS REVOKED', output)
        self.assertFalse(UserRoleAssignment.objects.filter(
            user=self.rose, revoked_at__isnull=True).exists())

    def test_two_accounts_on_one_address_are_granted_to_neither(self):
        """`email` is not unique, so picking the first match is a coin toss."""
        User.objects.create_user('r.mokgware.old', ROSE, 'x' * 24)
        output = _run(commit=True)
        self.assertIn('MEMBER AMBIGUOUS', output)
        role = Role.objects.get(code=BROKER_COMMISSION_ROLE)
        self.assertEqual(UserRoleAssignment.objects.filter(role=role).count(), 0)

    def test_a_missing_account_is_reported_not_skipped_in_silence(self):
        output = _run(commit=True)
        self.assertIn('MEMBER MISSING', output)

    def test_it_names_who_loses_access(self):
        """Tightening the door must never lock someone out quietly."""
        User.objects.create_user('tchimidza', 'tchimidza@alphadirect.co.bw',
                                 'x' * 24, first_name='Tlamelo',
                                 last_name='Chimidza')
        output = _run(commit=True)
        self.assertIn('LOSES ACCESS', output)
        self.assertIn('tchimidza', output)

    def test_the_grant_lands_in_the_audit_trail(self):
        from core.models import AuditLog
        _run(commit=True)
        self.assertTrue(AuditLog.objects.filter(
            table_name='core.UserRoleAssignment').exists())
