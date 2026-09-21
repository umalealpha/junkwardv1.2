"""[C8] The broker commission register answers to ONE named role.

Finance's words on the board item: "All other users default to NO ACCESS unless
Finance explicitly grants read-only." Before this, the register answered to any
commission-stage reviewer — a wider group than the four people Finance named,
on a register that decides what brokers get paid.

The interesting test is the third one: a reviewer WITHOUT the role must be
refused. That is the whole change, and it is the one that goes green again if
somebody restores the old `or is_reviewer(u)` for convenience.
"""
from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import Permission, Role, UserRoleAssignment
from commissions.broker_views import (BROKER_COMMISSION_PERMISSION,
                                      BROKER_COMMISSION_ROLE)

REGISTER_URL = '/api/v1/commissions/brokers/'


def _role():
    role = Role.objects.create(code=BROKER_COMMISSION_ROLE,
                               name='Broker Commission - Full Access', level=3)
    permission, _ = Permission.objects.get_or_create(
        code=BROKER_COMMISSION_PERMISSION,
        defaults={'category': 'commissions', 'is_active': True})
    role.permissions.add(permission)
    return role


class BrokerRegisterAccessTests(TestCase):

    def setUp(self):
        self.client = APIClient()

    def test_a_signed_out_visitor_is_refused(self):
        self.assertIn(self.client.get(REGISTER_URL).status_code, (401, 403))

    def test_a_role_holder_is_let_in(self):
        user = User.objects.create_user('rose', 'rmokgware@alphadirect.co.bw', 'x')
        UserRoleAssignment.objects.create(user=user, role=_role(),
                                          justification='[C8]')
        self.client.force_authenticate(user)
        self.assertEqual(self.client.get(REGISTER_URL).status_code, 200)

    def test_a_commission_reviewer_without_the_role_is_refused(self):
        """Being on a commission stage roster is no longer enough.

        Tlamelo Chimidza is a real first-review roster name (commissions/access
        `_ROSTER_NAMES[STAGE1]`) and is NOT one of the four people Finance named
        on [C8]. So this user genuinely passes `is_reviewer` — restore the old
        `or is_reviewer(u)` and this test goes red, which is the point of it.
        """
        user = User.objects.create_user('tchimidza', 'tchimidza@alphadirect.co.bw',
                                        'x', first_name='Tlamelo',
                                        last_name='Chimidza')
        _role()
        from commissions.access import is_reviewer
        self.assertTrue(is_reviewer(user),
                        'fixture is not actually a reviewer, so this test would '
                        'pass even with the old permission')
        self.client.force_authenticate(user)
        self.assertEqual(self.client.get(REGISTER_URL).status_code, 403)

    def test_an_expired_assignment_no_longer_opens_the_door(self):
        """A time-boxed grant — an auditor, a contractor — must actually expire.

        Checking UserRoleAssignment directly missed `expires_at` entirely, which
        is why this goes through `user_has_permission`, the check core/models
        says in as many words is the canonical one.
        """
        from django.utils import timezone
        from datetime import timedelta
        user = User.objects.create_user('auditor', 'auditor@alphadirect.co.bw', 'x')
        UserRoleAssignment.objects.create(
            user=user, role=_role(), justification='[C8]',
            expires_at=timezone.now() - timedelta(days=1))
        self.client.force_authenticate(user)
        self.assertEqual(self.client.get(REGISTER_URL).status_code, 403)

    def test_a_revoked_assignment_no_longer_opens_the_door(self):
        from django.utils import timezone
        user = User.objects.create_user('leaver', 'leaver@alphadirect.co.bw', 'x')
        UserRoleAssignment.objects.create(user=user, role=_role(),
                                          justification='[C8]',
                                          revoked_at=timezone.now())
        self.client.force_authenticate(user)
        self.assertEqual(self.client.get(REGISTER_URL).status_code, 403)

    def test_an_inactive_role_no_longer_opens_the_door(self):
        user = User.objects.create_user('paused', 'paused@alphadirect.co.bw', 'x')
        role = _role()
        role.is_active = False
        role.save(update_fields=['is_active'])
        UserRoleAssignment.objects.create(user=user, role=role,
                                          justification='[C8]')
        self.client.force_authenticate(user)
        self.assertEqual(self.client.get(REGISTER_URL).status_code, 403)
