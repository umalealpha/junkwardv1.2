"""core/tests/test_bulk_approve.py — "select all + sign once" (CFO 2026-07-22).

The bulk-approve endpoint is a fan-out: it dispatches each selected item to the
SAME service call its own page uses. These tests pin the fan-out contract — the
parts unique to this endpoint — rather than re-testing each module's approval
rule (those have their own suites):

  - one bad item (SoD / gone / not-bulk-able) never rolls back the good ones
  - a service ValidationError is REPORTED per-item, not raised to a 500
  - an unknown stream / empty selection / over-cap is rejected cleanly
  - the itemiser returns nothing for a user with no authority (no leakage)

Adapters are patched so the dispatch contract is tested without heavy Payment /
JournalEntry fixtures; one real end-to-end approval is proven separately by each
module's own tests.

Run: python manage.py test core.tests.test_bulk_approve
"""
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from rest_framework.test import APIClient, APITestCase

from core import approvals_views


class BulkApproveDispatch(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            'bulk_signer', email='signer@alphadirect.co.bw', password='x')
        self.c = APIClient()
        self.c.force_authenticate(user=self.user)

    def _post(self, items):
        return self.c.post('/api/v1/my-approvals/bulk-approve/',
                           {'items': items}, format='json')

    def test_empty_selection_rejected(self):
        r = self._post([])
        self.assertEqual(r.status_code, 400)

    def test_unknown_stream_reported_not_crash(self):
        r = self._post([{'stream': 'made_up', 'id': '1'}])
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data['approved'], 0)
        self.assertEqual(len(r.data['failed']), 1)
        self.assertIn('bulk-approvable', r.data['failed'][0]['error'])

    def test_over_cap_rejected(self):
        items = [{'stream': 'journal_entries', 'id': str(i)}
                 for i in range(approvals_views._BULK_CAP + 1)]
        r = self._post(items)
        self.assertEqual(r.status_code, 400)

    def test_good_items_succeed_even_when_one_fails(self):
        calls = []

        def ok(user, pk):
            calls.append(pk)

        def boom(user, pk):
            raise ValidationError("Segregation of duties: you cannot approve your own.")

        with patch.dict(approvals_views._BULK_ADAPTERS,
                        {'journal_entries': ok, 'payments': boom}, clear=False):
            r = self._post([
                {'stream': 'journal_entries', 'id': 'A'},
                {'stream': 'payments', 'id': 'B'},
                {'stream': 'journal_entries', 'id': 'C'},
            ])
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data['approved'], 2)              # A + C signed
        self.assertEqual(calls, ['A', 'C'])
        self.assertEqual(len(r.data['failed']), 1)           # B surfaced, not raised
        self.assertEqual(r.data['failed'][0]['id'], 'B')
        self.assertIn('Segregation', r.data['failed'][0]['error'])

    def test_missing_id_reported(self):
        r = self._post([{'stream': 'journal_entries', 'id': ''}])
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data['approved'], 0)
        self.assertEqual(len(r.data['failed']), 1)

    def test_generic_exception_is_swallowed_into_failed(self):
        def gone(user, pk):
            raise KeyError("row vanished")

        with patch.dict(approvals_views._BULK_ADAPTERS, {'payments': gone}, clear=False):
            r = self._post([{'stream': 'payments', 'id': 'X'}])
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data['approved'], 0)
        self.assertEqual(len(r.data['failed']), 1)
        # No internal detail leaks — friendly message only.
        self.assertIn('already been actioned', r.data['failed'][0]['error'])


class PoLegDispatch(APITestCase):
    """The PO adapter must call fm_approve for the operational/claims leg and
    cfo_approve for the final CFO leg, chosen by the PO's current status."""

    def _run_with_status(self, status_value):
        from procurement.models import PurchaseOrder
        calls = {"fm": 0, "cfo": 0}

        class _PO:
            status = status_value

        with patch("procurement.models.PurchaseOrder.objects") as mgr, \
                patch("procurement.services.fm_approve",
                      side_effect=lambda po, u: calls.__setitem__("fm", calls["fm"] + 1)), \
                patch("procurement.services.cfo_approve",
                      side_effect=lambda po, u: calls.__setitem__("cfo", calls["cfo"] + 1)):
            mgr.get.return_value = _PO()
            approvals_views._approve_po(object(), "some-id")
        return calls, PurchaseOrder

    def test_cfo_leg_calls_cfo_approve(self):
        from procurement.models import PurchaseOrder
        calls, _ = self._run_with_status(PurchaseOrder.Status.PENDING_CFO_APPROVAL)
        self.assertEqual(calls, {"fm": 0, "cfo": 1})

    def test_fm_leg_calls_fm_approve(self):
        from procurement.models import PurchaseOrder
        calls, _ = self._run_with_status(PurchaseOrder.Status.PENDING_FM_APPROVAL)
        self.assertEqual(calls, {"fm": 1, "cfo": 0})


class DecideEndpoint(APITestCase):
    """The five-button single-item decision: approve dispatches to the approve
    adapter, every send-back button dispatches to the reject adapter with its
    reason, and a reject without a reason / unknown action is refused."""

    def setUp(self):
        self.user = User.objects.create_user(
            'decider', email='decider@alphadirect.co.bw', password='x')
        self.c = APIClient(); self.c.force_authenticate(user=self.user)

    def _post(self, body):
        return self.c.post('/api/v1/my-approvals/decide/', body, format='json')

    def test_approve_dispatches_to_approve_adapter(self):
        seen = {}
        with patch.dict(approvals_views._BULK_ADAPTERS,
                        {'payments': lambda u, pk: seen.setdefault('a', pk)}, clear=False):
            r = self._post({'stream': 'payments', 'id': 'P1', 'action': 'approve'})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(seen.get('a'), 'P1')

    def test_reject_passes_reason_to_reject_adapter(self):
        seen = {}
        with patch.dict(approvals_views._REJECT_ADAPTERS,
                        {'payments': lambda u, pk, reason: seen.update(pk=pk, reason=reason)}, clear=False):
            r = self._post({'stream': 'payments', 'id': 'P2', 'action': 'reject', 'note': 'Send me the invoice.'})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(seen.get('pk'), 'P2')
        self.assertEqual(seen.get('reason'), 'Send me the invoice.')

    def test_reject_without_reason_refused(self):
        r = self._post({'stream': 'payments', 'id': 'P3', 'action': 'reject', 'note': ''})
        self.assertEqual(r.status_code, 400)

    def test_unknown_action_refused(self):
        r = self._post({'stream': 'payments', 'id': 'P4', 'action': 'maybe'})
        self.assertEqual(r.status_code, 400)

    def test_service_error_surfaced_as_400(self):
        def boom(u, pk):
            raise ValidationError('Segregation of duties: you cannot approve your own.')
        with patch.dict(approvals_views._BULK_ADAPTERS, {'payments': boom}, clear=False):
            r = self._post({'stream': 'payments', 'id': 'P5', 'action': 'approve'})
        self.assertEqual(r.status_code, 400)
        self.assertIn('Segregation', str(r.content))


class ItemiserAuthority(APITestCase):
    def test_no_authority_no_items(self):
        """A plain user with no approver title sees an empty itemised inbox —
        the streams must never leak items to someone who cannot sign them."""
        nobody = User.objects.create_user(
            'nobody', email='nobody@alphadirect.co.bw', password='x')
        streams = approvals_views.pending_approval_items_for(nobody)
        self.assertEqual(streams, [])

    def test_items_endpoint_shape(self):
        u = User.objects.create_user('viewer', email='viewer@alphadirect.co.bw',
                                     password='x')
        c = APIClient(); c.force_authenticate(user=u)
        r = c.get('/api/v1/my-approvals/items/')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIn('streams', r.data)
        self.assertIn('total', r.data)

    def test_brief_unknown_stream_is_empty(self):
        u = User.objects.create_user('briefer', email='briefer@alphadirect.co.bw', password='x')
        c = APIClient(); c.force_authenticate(user=u)
        r = c.get('/api/v1/my-approvals/brief/?stream=nope&id=1')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data['brief'], '')

    def test_vapid_key_endpoint(self):
        u = User.objects.create_user('vapid', email='vapid@alphadirect.co.bw', password='x')
        c = APIClient(); c.force_authenticate(user=u)
        r = c.get('/api/v1/my-approvals/push/vapid-key/')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIn('key', r.data)

    def test_push_subscribe_incomplete_rejected(self):
        u = User.objects.create_user('pusher', email='pusher@alphadirect.co.bw', password='x')
        c = APIClient(); c.force_authenticate(user=u)
        r = c.post('/api/v1/my-approvals/push/subscribe/', {'subscription': {'endpoint': 'x'}}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_push_subscribe_saves(self):
        from core.models import PushSubscription
        u = User.objects.create_user('pusher2', email='pusher2@alphadirect.co.bw', password='x')
        c = APIClient(); c.force_authenticate(user=u)
        r = c.post('/api/v1/my-approvals/push/subscribe/',
                   {'subscription': {'endpoint': 'https://push/abc', 'keys': {'p256dh': 'k', 'auth': 'a'}}},
                   format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(PushSubscription.objects.filter(user=u, endpoint='https://push/abc').exists())

    def test_history_endpoint(self):
        """The "What I signed" ledger returns the user's own APPROVE audit rows."""
        from core.models import AuditLog
        u = User.objects.create_user('signer2', email='signer2@alphadirect.co.bw',
                                     password='x')
        AuditLog.objects.create(table_name='Payment', record_id='1',
                                action=AuditLog.Action.APPROVE, user=u,
                                description='Approved PMT-0001')
        c = APIClient(); c.force_authenticate(user=u)
        r = c.get('/api/v1/my-approvals/history/')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data['total'], 1)
        self.assertEqual(r.data['history'][0]['kind'], 'Payment')
