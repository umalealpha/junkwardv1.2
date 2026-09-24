"""B2 — the six event names Graphite's locked specification fires.

The CFO's item says the door at POST /api/v1/events/ accepts only the five OLD
names, so every one of the six new ones would be rejected today. That is true of
this door. A SECOND door already exists (claims_automation, scope
`claims-events`) which handles the claim lifecycle, so the fix here is to accept
the six on the door Graphite was pointed at and hand them to the machine that is
already built — not to write a second claims engine.

DONE MEANS, in the CFO's words: all six event names accepted, stored,
de-duplicated on the event id, and a replayed event does not fire a rule twice.
"""
from unittest import mock

from django.test import TestCase, override_settings

from core.models import ApiKey
from integrations.models import IntegrationEvent
from integrations.services import EventProcessor

SIX = [
    'claim_registered',
    'form_submitted',
    'premium_checked',
    'assessment_received',
    'write_off_flagged',
    'decision_recorded',
]


class TheSixNamesAreAccepted(TestCase):
    def test_every_one_of_the_six_is_a_known_event_type(self):
        known = set(IntegrationEvent.EventType.values)
        for name in SIX:
            self.assertIn(name, known, f'{name} would be rejected at the door')

    def test_the_five_old_names_still_work(self):
        known = set(IntegrationEvent.EventType.values)
        for name in ('policy_issued', 'policy_cancelled', 'claim_approved',
                     'commission_calculated', 'bank_transaction'):
            self.assertIn(name, known)

    def test_a_claim_event_is_stored_with_its_payload(self):
        for name in SIX:
            ev = IntegrationEvent.objects.create(
                source_system=IntegrationEvent.SourceSystem.GRAPHITE,
                event_type=name,
                event_data={'claim_ref': 'CLM-1', 'idempotency_key': f'k-{name}'},
                idempotency_key=f'k-{name}',
            )
            self.assertEqual(
                IntegrationEvent.objects.get(pk=ev.pk).event_data['claim_ref'], 'CLM-1'
            )


class NothingIsSwitchedOn(TestCase):
    """B14 — the door ACCEPTS and STORES every event; acting on one is a switch.

    Without this the deploy itself armed the claims machine: the moment Graphite
    fired at /api/v1/events/, real handlers got tasks, real people got emails and
    letters were drafted on live claims, with no setting anyone could turn off.
    """

    def _event(self, key='sw'):
        return IntegrationEvent.objects.create(
            source_system=IntegrationEvent.SourceSystem.GRAPHITE,
            event_type='claim_registered',
            event_data={'claim_ref': 'CLM-SW', 'facts': {}},
            idempotency_key=key,
        )

    @override_settings(CLAIMS_LIFECYCLE_FORWARD_EVENTS=False)
    def test_with_the_switch_off_the_claims_machine_is_never_called(self):
        ev = self._event('sw-off')
        with mock.patch('claims_automation.processor.receive') as receive:
            EventProcessor().run(ev)
        receive.assert_not_called()

    @override_settings(CLAIMS_LIFECYCLE_FORWARD_EVENTS=False)
    def test_with_the_switch_off_the_event_is_still_stored_and_not_lost(self):
        ev = self._event('sw-stored')
        with mock.patch('claims_automation.processor.receive'):
            EventProcessor().run(ev)
        ev.refresh_from_db()
        self.assertTrue(IntegrationEvent.objects.filter(pk=ev.pk).exists())
        self.assertEqual(ev.event_data['claim_ref'], 'CLM-SW')
        self.assertIn('stored, not acted on', ev.error_message or '')
        self.assertEqual(ev.status, IntegrationEvent.Status.SKIPPED,
                         'a stored-but-not-acted-on event must not read as processed')

    @override_settings(CLAIMS_LIFECYCLE_FORWARD_EVENTS=True)
    def test_with_the_switch_on_the_claims_machine_is_called(self):
        ev = self._event('sw-on')
        with mock.patch('claims_automation.processor.receive') as receive:
            EventProcessor().run(ev)
        self.assertEqual(receive.call_count, 1)

    @override_settings(CLAIMS_LIFECYCLE_FORWARD_EVENTS=True)
    def test_the_door_owns_the_dedupe_key_not_the_caller(self):
        ev = IntegrationEvent.objects.create(
            source_system=IntegrationEvent.SourceSystem.GRAPHITE,
            event_type='claim_registered',
            event_data={'claim_ref': 'CLM-SW', 'idempotency_key': 'the-callers-own'},
            idempotency_key='the-doors-own',
        )
        with mock.patch('claims_automation.processor.receive') as receive:
            EventProcessor().run(ev)
        sent = receive.call_args[0][0]
        self.assertEqual(sent['idempotency_key'], 'the-doors-own')


@override_settings(CLAIMS_LIFECYCLE_FORWARD_EVENTS=True)
class ReplayDoesNotFireTwice(TestCase):
    """De-duplicated on the event id, and a replay must not run the rule again."""

    def test_the_same_event_id_cannot_be_stored_twice(self):
        from django.db import IntegrityError, transaction
        IntegrationEvent.objects.create(
            source_system=IntegrationEvent.SourceSystem.GRAPHITE,
            event_type='claim_registered',
            event_data={'claim_ref': 'CLM-2'},
            idempotency_key='same-key',
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                IntegrationEvent.objects.create(
                    source_system=IntegrationEvent.SourceSystem.GRAPHITE,
                    event_type='claim_registered',
                    event_data={'claim_ref': 'CLM-2'},
                    idempotency_key='same-key',
                )

    def test_a_claim_event_is_handed_to_the_claims_machine_once(self):
        ev = IntegrationEvent.objects.create(
            source_system=IntegrationEvent.SourceSystem.GRAPHITE,
            event_type='claim_registered',
            event_data={'claim_ref': 'CLM-3', 'claim': {'claim_ref': 'CLM-3'}},
            idempotency_key='hand-once',
        )
        with mock.patch('claims_automation.processor.receive') as receive:
            receive.return_value = {'ok': True}
            EventProcessor().run(ev)
            EventProcessor().run(ev)   # the replay
        self.assertEqual(
            receive.call_count, 1,
            'a replayed event fired the rule twice',
        )

    def test_a_processed_event_is_marked_processed_not_skipped(self):
        ev = IntegrationEvent.objects.create(
            source_system=IntegrationEvent.SourceSystem.GRAPHITE,
            event_type='premium_checked',
            event_data={'claim_ref': 'CLM-4'},
            idempotency_key='marked',
        )
        with mock.patch('claims_automation.processor.receive') as receive:
            receive.return_value = {'ok': True}
            EventProcessor().run(ev)
        ev.refresh_from_db()
        self.assertEqual(ev.status, IntegrationEvent.Status.PROCESSED)
        self.assertNotEqual(ev.status, IntegrationEvent.Status.SKIPPED)
