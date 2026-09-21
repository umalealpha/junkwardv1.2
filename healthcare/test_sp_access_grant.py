"""The ADH Health team members the CFO approved for the service-provider
registry (bug 6cb44d9e, 2026-09-08).

Gated on the email local part, so the test asserts the real addresses rather
than names — two people in the directory share the display name 'Amantle'.
"""
from types import SimpleNamespace

from django.test import SimpleTestCase

from healthcare.permissions import IsServiceProviderManager

GRANTED = [
    ('mtlagae@alphadirect.co.bw', 'Meduduetso Tlagae'),
    ('rtonkope@alphadirect.co.bw', 'Ritah Tonkope'),
    ('lkeotlhoboge@alphadirect.co.bw', 'Loapi Keotlhoboge'),
    ('kjane@alphadirect.co.bw', 'Keneilwe Jane'),
    ('athake@alphadirect.co.bw', 'Amantle Thake'),
    ('osebetlela@alphadirect.co.bw', 'Onkgolotse Sebetlela'),
]


def _u(email):
    return SimpleNamespace(is_authenticated=True, is_superuser=False,
                           email=email, username=email.split('@')[0])


class ServiceProviderAccessTests(SimpleTestCase):
    def test_every_approved_person_can_reach_the_registry(self):
        perm = IsServiceProviderManager()
        for email, name in GRANTED:
            with self.subTest(person=name):
                self.assertTrue(perm.has_permission(SimpleNamespace(user=_u(email)), None),
                                f'{name} ({email}) cannot reach the registry')

    def test_an_unrelated_member_of_staff_still_cannot(self):
        perm = IsServiceProviderManager()
        self.assertFalse(perm.has_permission(
            SimpleNamespace(user=_u('nobody@alphadirect.co.bw')), None))

    def test_the_same_local_part_on_another_domain_is_refused(self):
        """Gating on the local part alone would let an outside address in."""
        perm = IsServiceProviderManager()
        self.assertFalse(perm.has_permission(
            SimpleNamespace(user=_u('kjane@gmail.com')), None))
