"""fnb/test_notification_dispatch.py — the FNB & Payments plan fix, 2026-09-14.

`fnb/notifications.py:_persist_items` used to create an FNBWebhookEvent row for
every polled bank notification and stop — a comment claimed a post_save signal
dispatched it onward, but no such signal existed anywhere in this codebase.
Every notification (840 on production, oldest from 27-May-2026) sat at
status=received forever. These tests fail without the fix: revert
process_notification_event()'s classify+dispatch step and every one of them
goes red.
"""
from __future__ import annotations
from unittest import mock

from django.test import TestCase

from fnb.models import FNBWebhookEvent
from fnb.notifications import _persist_items, process_notification_event
from fnb.three_way_check import notification_health


def _camt054_item(ext_id, cdt_dbt='CRDT', fmly_cd='RCDT'):
    return {
        'Id': ext_id,
        'Ntry': [{
            'CdtDbtInd': cdt_dbt,
            'BkTxCd': {'Domn': {'Fmly': {'Cd': fmly_cd}}},
        }],
    }


class NotificationAdvancesPastReceived(TestCase):
    def test_a_classifiable_notification_never_stays_at_received(self):
        n = _persist_items([_camt054_item('COR-TEST-001')])
        self.assertEqual(n, 1)
        e = FNBWebhookEvent.objects.get(external_id='COR-TEST-001')
        self.assertNotEqual(e.status, FNBWebhookEvent.Status.RECEIVED)
        self.assertEqual(e.status, FNBWebhookEvent.Status.PROCESSED)
        self.assertIsNotNone(e.processed_at)

    def test_an_unclassifiable_notification_is_flagged_not_silent(self):
        """No CdtDbtInd/BkTxCd at all — cannot be verified, so it must become
        VISIBLE to a person (status=failed + a reason), never sit quietly."""
        _persist_items([{'Id': 'COR-TEST-BAD'}])
        e = FNBWebhookEvent.objects.get(external_id='COR-TEST-BAD')
        self.assertEqual(e.status, FNBWebhookEvent.Status.FAILED)
        self.assertTrue(e.error_message)

    def test_reprocessing_an_already_processed_event_never_redispatches(self):
        """Panel review 2026-09-14 (K3/K4): FNB's 'new' feed can redeliver an
        id we already advanced. A second call must not run dispatch_webhook
        again — not just "not blow up"."""
        _persist_items([_camt054_item('COR-TEST-002')])
        e = FNBWebhookEvent.objects.get(external_id='COR-TEST-002')
        self.assertEqual(e.status, FNBWebhookEvent.Status.PROCESSED)
        with mock.patch('fnb.notifications.dispatch_webhook') as disp:
            process_notification_event(e)
            disp.assert_not_called()
        e.refresh_from_db()
        self.assertEqual(e.status, FNBWebhookEvent.Status.PROCESSED)

    def test_a_redelivered_id_does_not_reset_status_to_received(self):
        """_persist_items used to use update_or_create with status in
        `defaults`, so a redelivered id got its status stamped back to
        RECEIVED — and re-dispatched — every time FNB resent it."""
        _persist_items([_camt054_item('COR-TEST-003')])
        e = FNBWebhookEvent.objects.get(external_id='COR-TEST-003')
        self.assertEqual(e.status, FNBWebhookEvent.Status.PROCESSED)
        with mock.patch('fnb.notifications.dispatch_webhook') as disp:
            n = _persist_items([_camt054_item('COR-TEST-003')])
            disp.assert_not_called()
        self.assertEqual(n, 0)   # not counted as a new insert either
        e.refresh_from_db()
        self.assertEqual(e.status, FNBWebhookEvent.Status.PROCESSED)

    def test_backlog_events_created_directly_also_advance(self):
        """Simulates the pre-fix backlog: a row already sitting at RECEIVED
        (as if created before this fix shipped), the way the backfill command
        processes the real 840 stuck production rows."""
        e = FNBWebhookEvent.objects.create(
            event_type='camt054_CRDT_RCDT', external_id='COR-BACKLOG-1',
            raw_payload=_camt054_item('COR-BACKLOG-1'),
            status=FNBWebhookEvent.Status.RECEIVED)
        process_notification_event(e)
        e.refresh_from_db()
        self.assertEqual(e.status, FNBWebhookEvent.Status.PROCESSED)


class NotificationHealthVisibility(TestCase):
    def test_a_stuck_backlog_is_counted(self):
        FNBWebhookEvent.objects.create(
            event_type='camt054_CRDT_RCDT', external_id='COR-STUCK-1',
            raw_payload={}, status=FNBWebhookEvent.Status.RECEIVED)
        h = notification_health(stuck_hours=0)
        self.assertGreaterEqual(h['stuck'], 1)

    def test_a_flagged_failure_is_counted(self):
        FNBWebhookEvent.objects.create(
            event_type='unknown', external_id='COR-FAILED-1',
            raw_payload={}, status=FNBWebhookEvent.Status.FAILED,
            error_message='could not classify')
        h = notification_health()
        self.assertGreaterEqual(h['failed'], 1)

    def test_nothing_stuck_or_failed_is_clean(self):
        # Asserted key by key, not as an exact dict. notification_health() gained
        # `backlog` / `backlog_before` / `backlog_note` on 17-Sep-2026 so the
        # live alert could read zero again while the 863 historic events are
        # counted apart; an exact-dict assertion breaks on any honest addition
        # and says nothing about the three values that actually matter.
        h = notification_health()
        self.assertEqual(h['stuck'], 0)
        self.assertEqual(h['failed'], 0)
        self.assertEqual(h['stuck_hours'], 2)


class RunKeepsCleanAndNotificationsCleanSeparate(TestCase):
    """Fable 5.1 review, 2026-09-14: the payments/exceptions screen and the
    daily email both print 'clean' as "0 payments where Omni and the bank
    do not agree" — a headline about payment/batch contradictions only.
    Folding a flagged bank notification into that SAME flag would make an
    untouched renderer print a false "0 payment(s) disagree" on a day a
    notification legitimately failed. The two must stay independent keys."""

    def test_a_failed_notification_does_not_flip_the_payments_clean_flag(self):
        from fnb.three_way_check import run
        FNBWebhookEvent.objects.create(
            event_type='unknown', external_id='COR-RUN-FAILED-1',
            raw_payload={}, status=FNBWebhookEvent.Status.FAILED,
            error_message='could not classify')
        res = run()
        self.assertTrue(res['clean'])              # no payment/batch contradiction
        self.assertFalse(res['notifications_clean'])  # but this must say so
