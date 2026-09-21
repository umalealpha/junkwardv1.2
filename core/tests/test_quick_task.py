"""Quick Task (Omni Mobile Workstream G) — source allow-list + idempotent create.

Red-first: remove the allow-list and test_privileged_source_cannot_be_spoofed
fails; remove the client_key dedupe and test_same_client_key_creates_one_task fails.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import OmniTask

User = get_user_model()


class QuickTaskTests(TestCase):
    def setUp(self):
        self.boss = User.objects.create_user('boss', password='x', is_active=True)
        self.mate = User.objects.create_user('mate', password='x', is_active=True)
        self.c = APIClient()
        self.c.force_authenticate(user=self.boss)

    def _post(self, **extra):
        payload = {'assignee_username': 'mate', 'title': 'Send the recons'}
        payload.update(extra)
        return self.c.post('/api/v1/tasks/', payload, format='json')

    def test_quick_task_source_and_key_are_stored(self):
        r = self._post(source='quick_task', client_key='k1')
        self.assertEqual(r.status_code, 201)
        t = OmniTask.objects.get(id=r.data['id'])
        self.assertEqual(t.source, 'quick_task')
        self.assertEqual(t.client_key, 'k1')

    def test_privileged_source_cannot_be_spoofed(self):
        r = self._post(source='payment_request', client_key='k2')
        self.assertEqual(r.status_code, 201)
        t = OmniTask.objects.get(id=r.data['id'])
        self.assertEqual(t.source, '')  # dropped — not in the allow-list

    def test_same_client_key_creates_one_task(self):
        r1 = self._post(source='quick_task', client_key='dup')
        r2 = self._post(source='quick_task', client_key='dup', title='changed')
        self.assertEqual(r1.status_code, 201)
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(r2.data.get('deduped'))
        self.assertEqual(r1.data['id'], r2.data['id'])
        self.assertEqual(OmniTask.objects.filter(assigner=self.boss).count(), 1)

    def test_no_client_key_allows_distinct_tasks(self):
        self._post()
        self._post()
        self.assertEqual(OmniTask.objects.filter(assigner=self.boss).count(), 2)

    def test_different_assigners_same_key_do_not_collide(self):
        self._post(source='quick_task', client_key='shared')
        other = User.objects.create_user('other', password='x', is_active=True)
        c2 = APIClient()
        c2.force_authenticate(user=other)
        r = c2.post('/api/v1/tasks/',
                    {'assignee_username': 'mate', 'title': 'x',
                     'source': 'quick_task', 'client_key': 'shared'},
                    format='json')
        self.assertEqual(r.status_code, 201)  # different assigner → not a duplicate
        self.assertEqual(OmniTask.objects.filter(client_key='shared').count(), 2)
