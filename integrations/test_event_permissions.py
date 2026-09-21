"""
integrations/test_event_permissions.py

Manus nine-area retest P0 (2026-08-25). IntegrationEventViewSet declared no
permission_classes, so it fell back to the project default (IsAuthenticated) and
ANY signed-in staffer could POST an event — which runs EventProcessor
synchronously and raises a real customer invoice, vendor bill or credit note
under the first superuser's name.

Each test below fails on the pre-fix code: the 403 assertions get 201/200, and
the idempotency test raises a SECOND invoice.
"""
from django.contrib.auth.models import User
from django.contrib.auth.hashers import make_password
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import ApiKey, UserProfile
from integrations.models import IntegrationEvent

EVENTS = '/api/v1/events/'


def _mint(label, scopes, service_user):
    """An ApiKey row plus its plaintext, mirroring how the console mints one."""
    plaintext = 'a' * 64
    ApiKey.objects.create(
        label=label, key_prefix=plaintext[:12],
        key_hash=make_password(plaintext),
        service_user=service_user, allowed_scopes=scopes, is_active=True,
    )
    return plaintext


class IntegrationEventAccessTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user('clerk', email='clerk@x.co', password='x')
        UserProfile.objects.create(user=cls.staff, title=UserProfile.Title.ACCOUNTANT)
        cls.admin = User.objects.create_user('boss', email='boss@x.co', password='x')
        UserProfile.objects.create(user=cls.admin, is_administrator=True)
        cls.svc = User.objects.create_user('svc-graphite', email='svc@x.co', password='x')

    def setUp(self):
        self.client = APIClient()

    # ---- create: service keys only -------------------------------------
    def test_ordinary_staff_cannot_post_an_event(self):
        self.client.force_authenticate(user=self.staff)
        r = self.client.post(EVENTS, {
            'source_system': 'graphite', 'event_type': 'policy_issued',
            'event_data': {'policy_number': 'P-1'},
        }, format='json')
        self.assertEqual(r.status_code, 403, r.content)
        self.assertEqual(IntegrationEvent.objects.count(), 0)

    def test_even_an_administrator_cannot_hand_inject_an_event(self):
        """An inbound integration event is machine-to-machine by definition. A
        person who needs to raise an invoice uses the invoice screens."""
        self.client.force_authenticate(user=self.admin)
        r = self.client.post(EVENTS, {
            'source_system': 'graphite', 'event_type': 'policy_issued',
            'event_data': {'policy_number': 'P-1'},
        }, format='json')
        self.assertEqual(r.status_code, 403, r.content)

    def test_a_key_without_the_events_scope_cannot_post(self):
        key = _mint('wrong-scope', ['read-only'], self.svc)
        r = self.client.post(EVENTS, {
            'source_system': 'graphite', 'event_type': 'policy_issued',
            'event_data': {'policy_number': 'P-1'},
        }, format='json', HTTP_AUTHORIZATION=f'ApiKey {key}')
        self.assertIn(r.status_code, (401, 403), r.content)
        self.assertEqual(IntegrationEvent.objects.count(), 0)

    # ---- read: finance administrators ----------------------------------
    def test_ordinary_staff_cannot_read_the_raw_feed(self):
        self.client.force_authenticate(user=self.staff)
        r = self.client.get(EVENTS)
        self.assertEqual(r.status_code, 403, r.content)

    def test_administrator_can_read_the_feed(self):
        self.client.force_authenticate(user=self.admin)
        r = self.client.get(EVENTS)
        self.assertEqual(r.status_code, 200, r.content)

    def test_ordinary_staff_cannot_retry_an_event(self):
        ev = IntegrationEvent.objects.create(
            source_system='graphite', event_type='policy_issued',
            event_data={'policy_number': 'P-1'},
            status=IntegrationEvent.Status.FAILED,
        )
        self.client.force_authenticate(user=self.staff)
        r = self.client.post(f'{EVENTS}{ev.pk}/retry/', {}, format='json')
        self.assertEqual(r.status_code, 403, r.content)


class IntegrationEventIdempotencyTest(TestCase):
    """A redelivered push must not book the same revenue twice."""

    @classmethod
    def setUpTestData(cls):
        cls.svc = User.objects.create_user('svc2', email='svc2@x.co', password='x')

    def test_same_idempotency_key_returns_the_original_event(self):
        key = _mint('graphite-feed', ['graphite-events'], self.svc)
        client = APIClient()
        body = {
            'source_system': 'graphite', 'event_type': 'policy_issued',
            'event_data': {'policy_number': 'P-1'},
            'idempotency_key': 'graphite-evt-0001',
        }
        first = client.post(EVENTS, body, format='json',
                            HTTP_AUTHORIZATION=f'ApiKey {key}')
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(IntegrationEvent.objects.count(), 1)

        second = client.post(EVENTS, body, format='json',
                             HTTP_AUTHORIZATION=f'ApiKey {key}')
        # 200, not 201 — the sender can tell a replay from a first delivery.
        self.assertEqual(second.status_code, 200, second.content)
        self.assertEqual(second.data['id'], first.data['id'])
        self.assertEqual(IntegrationEvent.objects.count(), 1)

    def test_the_submitting_key_is_recorded(self):
        key = _mint('graphite-feed', ['graphite-events'], self.svc)
        APIClient().post(EVENTS, {
            'source_system': 'graphite', 'event_type': 'policy_issued',
            'event_data': {'policy_number': 'P-2'},
        }, format='json', HTTP_AUTHORIZATION=f'ApiKey {key}')
        ev = IntegrationEvent.objects.get()
        self.assertEqual(ev.received_via, 'graphite-feed')

    def test_a_sender_cannot_claim_to_be_another_service(self):
        key = _mint('graphite-feed', ['graphite-events'], self.svc)
        APIClient().post(EVENTS, {
            'source_system': 'graphite', 'event_type': 'policy_issued',
            'event_data': {'policy_number': 'P-3'},
            'received_via': 'somebody-else',
        }, format='json', HTTP_AUTHORIZATION=f'ApiKey {key}')
        ev = IntegrationEvent.objects.get()
        self.assertEqual(ev.received_via, 'graphite-feed')
