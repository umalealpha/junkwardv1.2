"""Tests for the WS1 outbound state bus (integrations.outbound).

Includes the change-detecting test the JD requires: the refund post-back now
records a durable OutboundEvent, which the old bespoke urllib call never did —
so `test_refund_postback_creates_durable_event` fails against the old code.
"""
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from integrations import outbound
from integrations.models import OutboundEvent

_EP = 'https://graphite.local/api/v1/webhooks/omni/refund-paid'


def _enqueue(**over):
    kw = dict(target=OutboundEvent.Target.GRAPHITE, event_type='refund.paid',
              payload={'graphite_ref': 'GRF-1', 'status': 'paid'}, endpoint=_EP,
              auth_kind=OutboundEvent.AuthKind.BEARER, token_setting='TESTBUS_TOKEN',
              idempotency_key='GRF-1')
    kw.update(over)
    return outbound.enqueue(**kw)


@override_settings(TESTBUS_TOKEN='secret', OUTBOUND_BUS_ENABLED=True)
class OutboundBusTests(TestCase):

    def test_enqueue_creates_pending_event(self):
        ev = _enqueue()
        self.assertEqual(ev.status, OutboundEvent.Status.PENDING)
        self.assertEqual(ev.attempts, 0)
        self.assertEqual(ev.payload['graphite_ref'], 'GRF-1')
        self.assertEqual(ev.token_setting, 'TESTBUS_TOKEN')

    def test_enqueue_is_idempotent_on_key(self):
        a = _enqueue()
        b = _enqueue(payload={'graphite_ref': 'GRF-1', 'status': 'changed'})
        self.assertEqual(a.pk, b.pk)                     # same row, not a second
        self.assertEqual(OutboundEvent.objects.count(), 1)

    def test_deliver_marks_sent_on_2xx(self):
        ev = _enqueue()
        with mock.patch.object(outbound, '_http_post', return_value=200) as post:
            r = outbound.deliver(ev)
        self.assertTrue(r['sent'])
        post.assert_called_once()
        ev.refresh_from_db()
        self.assertEqual(ev.status, OutboundEvent.Status.SENT)
        self.assertEqual(ev.attempts, 1)
        self.assertIsNotNone(ev.sent_at)
        # The Bearer secret was resolved from settings, not stored on the row.
        _url, _body, headers = post.call_args.args
        self.assertEqual(headers['Authorization'], 'Bearer secret')

    def test_deliver_retries_then_dies(self):
        ev = _enqueue(max_attempts=3)
        with mock.patch.object(outbound, '_http_post', side_effect=OSError('down')):
            for _ in range(2):
                outbound.deliver(ev.__class__.objects.get(pk=ev.pk))
            ev.refresh_from_db()
            self.assertEqual(ev.status, OutboundEvent.Status.FAILED)
            self.assertEqual(ev.attempts, 2)
            self.assertIsNotNone(ev.next_attempt_at)      # scheduled for retry
            outbound.deliver(ev.__class__.objects.get(pk=ev.pk))   # 3rd = max
        ev.refresh_from_db()
        self.assertEqual(ev.status, OutboundEvent.Status.DEAD)
        self.assertIsNone(ev.next_attempt_at)

    def test_non_2xx_is_a_failure(self):
        ev = _enqueue()
        with mock.patch.object(outbound, '_http_post', return_value=500):
            outbound.deliver(ev)
        ev.refresh_from_db()
        self.assertEqual(ev.status, OutboundEvent.Status.FAILED)
        self.assertEqual(ev.response_status, 500)

    @override_settings(OUTBOUND_BUS_ENABLED=False)
    def test_kill_switch_does_not_send(self):
        ev = _enqueue()
        with mock.patch.object(outbound, '_http_post') as post:
            r = outbound.deliver(ev)
        self.assertFalse(r['sent'])
        post.assert_not_called()
        ev.refresh_from_db()
        self.assertEqual(ev.status, OutboundEvent.Status.PENDING)   # nothing lost

    def test_unconfigured_token_stays_pending(self):
        ev = _enqueue(token_setting='NO_SUCH_SETTING')
        with mock.patch.object(outbound, '_http_post') as post:
            r = outbound.deliver(ev)
        self.assertEqual(r['reason'], 'not_configured')
        post.assert_not_called()
        ev.refresh_from_db()
        self.assertEqual(ev.status, OutboundEvent.Status.PENDING)

    def test_drain_sends_due_events(self):
        _enqueue(idempotency_key='A', payload={'x': 1})
        _enqueue(idempotency_key='B', payload={'x': 2})
        with mock.patch.object(outbound, '_http_post', return_value=200):
            result = outbound.drain()
        self.assertEqual(result['sent'], 2)
        self.assertEqual(OutboundEvent.objects.filter(status=OutboundEvent.Status.SENT).count(), 2)

    def test_drain_command_delivers(self):
        from django.core.management import call_command
        _enqueue(idempotency_key='CMD', payload={'x': 1})
        with mock.patch.object(outbound, '_http_post', return_value=200):
            call_command('drain_outbound_events')
        self.assertEqual(
            OutboundEvent.objects.get(idempotency_key='CMD').status,
            OutboundEvent.Status.SENT)

    def test_not_yet_due_is_left_alone(self):
        ev = _enqueue(idempotency_key='LATER')
        ev.status = OutboundEvent.Status.FAILED
        ev.next_attempt_at = timezone.now() + timezone.timedelta(minutes=10)
        ev.save()
        with mock.patch.object(outbound, '_http_post') as post:
            outbound.drain()
        post.assert_not_called()   # backoff not elapsed → skipped


@override_settings(GRAPHITE_REFUND_CALLBACK_URL=_EP,
                   GRAPHITE_REFUND_CALLBACK_TOKEN='rtok',
                   OUTBOUND_BUS_ENABLED=True)
class RefundPostBackOnBusTests(TestCase):
    """The refund post-back is re-pointed onto the bus — parity + durability."""

    def _fake_refund(self):
        class _Status:
            POSTED_BACK = 'posted_back'
        return SimpleNamespace(
            graphite_ref='GRF-REF-1', policy_number='POL-9',
            refund_amount=Decimal('250.00'),
            fnb_batch=SimpleNamespace(fnb_reference='FNB-77'),
            graphite_posted=False, graphite_posted_at=None, status='queued',
            Status=_Status, save=lambda **kw: None)

    def test_refund_postback_creates_durable_event(self):
        # FAILS on the old code: it did a raw urllib POST and created NO record.
        from customer_refunds.services import post_refund_back_to_graphite
        refund = self._fake_refund()
        with mock.patch.object(outbound, '_http_post', return_value=200):
            res = post_refund_back_to_graphite(refund)
        self.assertTrue(res['sent'])
        ev = OutboundEvent.objects.get(idempotency_key='GRF-REF-1')
        self.assertEqual(ev.event_type, 'refund.paid')
        self.assertEqual(ev.status, OutboundEvent.Status.SENT)
        self.assertEqual(ev.payload['policy_number'], 'POL-9')
        self.assertEqual(refund.status, 'posted_back')          # behaviour kept
        self.assertTrue(refund.graphite_posted)

    def test_refund_postback_dedupes(self):
        from customer_refunds.services import post_refund_back_to_graphite
        with mock.patch.object(outbound, '_http_post', return_value=200):
            post_refund_back_to_graphite(self._fake_refund())
            post_refund_back_to_graphite(self._fake_refund())      # same graphite_ref
        self.assertEqual(OutboundEvent.objects.filter(idempotency_key='GRF-REF-1').count(), 1)
