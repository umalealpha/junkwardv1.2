"""/api/v1/my-approvals/pack/ — entitlement, not obscurity.

The pack carries payee bank details, salaries, client names and policy numbers.
Knowing an id is not a permission: the endpoint only answers for an item that is
on THIS user's own pending-approval list, which is the same list that decides
what they may sign.

Run: python manage.py test core.tests.test_approval_pack_endpoint --keepdb
"""
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

User = get_user_model()


class ApprovalPackEndpointTests(TestCase):
    def setUp(self):
        self.url = reverse('v1-my-approvals-pack')
        self.user = User.objects.create_user(
            username='packs-nobody', email='packs-nobody@alphadirect.co.bw',
            password='x')

    def test_sign_in_is_required(self):
        self.assertIn(self.client.get(self.url).status_code, (401, 403))

    def test_an_item_not_on_your_list_is_refused(self):
        """The failure that matters: a staff member who is not an approver
        asking for a payment pack by id."""
        self.client.force_login(self.user)
        r = self.client.get(self.url, {'stream': 'payments', 'id': str(uuid.uuid4())})
        self.assertEqual(r.status_code, 403)
        self.assertIsNone(r.json()['pack'])

    def test_a_missing_stream_or_id_is_answered_empty_not_500(self):
        self.client.force_login(self.user)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json()['pack'])

    def test_an_entitled_item_is_answered(self):
        """With the item on the user's list the pack comes back — so the 403
        above is proving the gate, not just an empty database."""
        from unittest.mock import patch
        self.client.force_login(self.user)
        item_id = str(uuid.uuid4())
        streams = [{'key': 'payments', 'label': 'Payments', 'items': [{'id': item_id}]}]
        with patch('core.approvals_views.pending_approval_items_for', return_value=streams), \
                patch('core.approval_pack.build_pack',
                      return_value={'stream': 'payments', 'title': 'A payee'}):
            r = self.client.get(self.url, {'stream': 'payments', 'id': item_id})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['pack']['title'], 'A payee')
