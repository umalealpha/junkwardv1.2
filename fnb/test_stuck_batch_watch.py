"""fnb/test_stuck_batch_watch.py

A batch that FNB never answers about must become VISIBLE.

The 2026-08-20 sweep (poll_fnb_batches) closed the "reject never read" gap, but
one hop further along the same silence remained: FNB answers `425 Too Early.
Retry-After 120 seconds` to retrieveReport for a while after a submit, and on
batch ALPHA-EFT-20260820-02f48be99baf4a66 it did so on every attempt for over
ten minutes. The sweep logged and continued exactly as designed — `1 checked,
0 changed, 1 errored` — and the batch stayed `submitted`. If FNB never starts
answering, that batch sits in `submitted` forever, its payments still carry
bank_submitted_at (so they cannot be re-sent), and nobody is told.

These tests are the red half of that fix.
"""
from __future__ import annotations

from datetime import timedelta
from unittest import mock

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone


@override_settings(FNB_HEALTH_ALERT_RECIPIENTS=['cfo@example.com'])
class StuckBatchAlertTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        # Build the chain outright — Currency -> ledger.Account -> BankAccount.
        # A skipTest fallback on a missing fixture reads as green and can never
        # go red, which is the whole failure mode under test here.
        # Pattern: banking/test_statement_date_timezone.py and
        # fnb/tests.py::PollFNBBatchesCommandTests.
        from banking.models import BankAccount
        from core.models import Currency
        from ledger.models import Account

        Currency.objects.get_or_create(
            code='BWP', defaults={'name': 'Botswana Pula', 'symbol': 'P'})
        gl = Account.objects.create(
            code='1119', name='Test FNB stuck-batch clearing',
            account_type='asset', sub_type='test', is_active=True,
            is_bank_account=True)
        cls.account = BankAccount.objects.create(
            gl_account=gl, bank_name='FNB', account_name='Test current',
            account_number='000000003', currency_code_id='BWP')

    # -- helpers ----------------------------------------------------------

    def _batch(self, *, status, age_hours, key='ALPHA-EFT-20260820-02f48be99baf4a66',
               count=3, total='1234.56', set_submitted_at=True):
        from fnb.models import FNBBatchSubmission as B
        when = timezone.now() - timedelta(hours=age_hours)
        batch = B.objects.create(
            idempotency_key=key, source_account=self.account,
            payment_count=count, total_amount_bwp=total, currency_code='BWP',
            status=status, fnb_reference='ref-' + key[-6:],
            submitted_at=when if set_submitted_at else None,
        )
        # created_at is auto_now_add — force it so the created_at fallback for
        # batches that never got a submitted_at is exercised honestly.
        B.objects.filter(pk=batch.pk).update(created_at=when)
        batch.refresh_from_db()
        return batch

    def _poll_error(self, batch, http_status=425,
                    text='{"code":"Too Early. Retry-After 120 seconds"}'):
        from fnb.models import FNBSyncLog
        return FNBSyncLog.objects.create(
            direction=FNBSyncLog.Direction.OUTBOUND,
            service=FNBSyncLog.Service.PAYMENT_BATCH,
            status=FNBSyncLog.Status.FAILED,
            endpoint='payments/status',
            request_summary=f'Status poll for batch {batch.idempotency_key}',
            http_status=http_status, error_message=text,
        )

    def _run(self, **kw):
        from fnb.health_watch import check_stuck_batches
        return check_stuck_batches(**kw)

    # -- the gap ----------------------------------------------------------

    def test_a_batch_stuck_past_the_threshold_raises_an_alert(self):
        from fnb.models import FNBBatchSubmission as B
        batch = self._batch(status=B.Status.SUBMITTED, age_hours=3)
        self._poll_error(batch)

        res = self._run()

        self.assertTrue(res['emailed'], msg=f'no alert sent: {res}')
        self.assertEqual(res['stuck_count'], 1)
        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        # Everything needed to act, without opening the database.
        self.assertIn(batch.idempotency_key, body)
        # Pin the LABELLED line: a bare assertIn('3') is satisfied by
        # "3.0 hours" elsewhere in the body and would certify nothing.
        self.assertIn('Payments       : 3', body)
        self.assertIn('BWP 1,234.56', body)            # total, thousands-separated
        self.assertIn('HTTP 425', body)                # last poll error
        self.assertIn('Too Early', body)

    def test_a_terminal_batch_does_not_alert(self):
        from fnb.models import FNBBatchSubmission as B
        for terminal in (B.Status.SETTLED, B.Status.FAILED, B.Status.CANCELLED):
            with self.subTest(status=terminal):
                mail.outbox = []
                from fnb.models import FNBHealthAlertState
                FNBHealthAlertState.objects.all().delete()
                B.objects.all().delete()
                batch = self._batch(status=terminal, age_hours=48,
                                    key=f'ALPHA-EFT-TERMINAL-{terminal}')
                self._poll_error(batch)

                res = self._run()

                self.assertEqual(res['stuck_count'], 0, msg=f'{terminal} treated as stuck')
                self.assertFalse(res['emailed'])
                self.assertEqual(mail.outbox, [])

    def test_a_recent_batch_is_left_alone(self):
        """425 for the first few minutes is FNB's documented behaviour, not a
        fault — alerting on it would train everyone to ignore the alert."""
        from fnb.models import FNBBatchSubmission as B
        batch = self._batch(status=B.Status.SUBMITTED, age_hours=0.5)
        self._poll_error(batch)

        res = self._run()

        self.assertEqual(res['stuck_count'], 0)
        self.assertFalse(res['emailed'])
        self.assertEqual(mail.outbox, [])

    def test_an_unknown_batch_with_no_submitted_at_is_still_caught(self):
        """submit_eft_batch writes submitted_at ONLY on the success path, so an
        UNKNOWN batch (POST left, no clean answer, money MAY have moved) has
        submitted_at = NULL forever. Ageing on submitted_at alone would skip
        the single most dangerous status there is."""
        from fnb.models import FNBBatchSubmission as B
        batch = self._batch(status=B.Status.UNKNOWN, age_hours=6,
                            key='ALPHA-EFT-UNKNOWN-nosubmitted',
                            set_submitted_at=False)
        self.assertIsNone(batch.submitted_at)
        self._poll_error(batch, http_status=599, text='Network error: read timeout')

        res = self._run()

        self.assertEqual(res['stuck_count'], 1)
        self.assertTrue(res['emailed'])
        self.assertIn(batch.idempotency_key, mail.outbox[0].body)

    def test_the_same_stuck_batch_is_not_emailed_every_run(self):
        from fnb.models import FNBBatchSubmission as B
        batch = self._batch(status=B.Status.SUBMITTED, age_hours=3)
        self._poll_error(batch)

        first = self._run()
        self.assertTrue(first['emailed'])

        mail.outbox = []
        second = self._run()

        self.assertFalse(second['emailed'], msg='re-emailed the same stuck batch')
        self.assertEqual(second['stuck_count'], 1)     # still stuck, just quiet
        self.assertEqual(mail.outbox, [])

    def test_a_newly_stuck_batch_alerts_even_inside_the_quiet_window(self):
        """Dedupe must key on WHICH batches are stuck, not merely on 'we sent
        something recently' — otherwise the second stuck batch is swallowed."""
        from fnb.models import FNBBatchSubmission as B
        first_batch = self._batch(status=B.Status.SUBMITTED, age_hours=3)
        self._poll_error(first_batch)
        self.assertTrue(self._run()['emailed'])

        mail.outbox = []
        second_batch = self._batch(status=B.Status.SUBMITTED, age_hours=3,
                                   key='ALPHA-EFT-20260820-secondstuck')
        self._poll_error(second_batch)

        res = self._run()

        self.assertTrue(res['emailed'], msg='a NEW stuck batch was swallowed')
        self.assertEqual(res['stuck_count'], 2)
        self.assertIn(second_batch.idempotency_key, mail.outbox[0].body)

    def test_the_threshold_is_tunable(self):
        from fnb.models import FNBBatchSubmission as B
        batch = self._batch(status=B.Status.SUBMITTED, age_hours=3)
        self._poll_error(batch)

        self.assertEqual(self._run(threshold_hours=8)['stuck_count'], 0)
        self.assertEqual(self._run(threshold_hours=1)['stuck_count'], 1)

    def test_dry_run_never_emails_and_never_records_state(self):
        from fnb.models import FNBBatchSubmission as B, FNBHealthAlertState
        batch = self._batch(status=B.Status.SUBMITTED, age_hours=3)
        self._poll_error(batch)

        res = self._run(dry_run=True)

        self.assertEqual(res['stuck_count'], 1)
        self.assertFalse(res['emailed'])
        self.assertEqual(mail.outbox, [])
        self.assertIn(batch.idempotency_key, res['body'])
        self.assertEqual(FNBHealthAlertState.load().stuck_keys, '')

    def test_a_batch_with_no_poll_error_yet_says_so_plainly(self):
        """Never print 'None' at the CFO — the absence of an error is itself
        the finding (nothing has even tried to poll this batch)."""
        from fnb.models import FNBBatchSubmission as B
        self._batch(status=B.Status.SUBMITTED, age_hours=3)

        res = self._run()

        self.assertTrue(res['emailed'])
        body = mail.outbox[0].body
        self.assertNotIn('None', body)
        self.assertIn('no poll error recorded', body.lower())

    def test_the_watch_command_runs_the_stuck_check(self):
        """The cron entry is `fnb_health_watch`. If the check is not wired into
        that command it is scheduled nowhere and this whole file is theatre."""
        from io import StringIO
        from django.core.management import call_command
        from fnb.models import FNBBatchSubmission as B

        batch = self._batch(status=B.Status.SUBMITTED, age_hours=3)
        self._poll_error(batch)

        out = StringIO()
        with mock.patch('fnb.health_watch.compute_verdict',
                        return_value=('up', {'successes': 1, 'hard_failures': 0,
                                             'total': 1})):
            call_command('fnb_health_watch', stdout=out, stderr=StringIO())

        self.assertIn('stuck', out.getvalue().lower())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(batch.idempotency_key, mail.outbox[0].body)

    def test_a_failed_send_is_retried_next_run_not_muted_for_six_hours(self):
        """A watcher that stamps "already told them" on a send that never left
        silences the very alert it exists to deliver."""
        from fnb.models import FNBBatchSubmission as B, FNBHealthAlertState
        batch = self._batch(status=B.Status.SUBMITTED, age_hours=3)
        self._poll_error(batch)

        with mock.patch('fnb.health_watch.send_with_cfo_cc',
                        side_effect=RuntimeError('SMTP down')):
            first = self._run()
        self.assertFalse(first['emailed'])
        self.assertEqual(FNBHealthAlertState.load().stuck_keys, '')

        second = self._run()

        self.assertTrue(second['emailed'], msg='muted after a failed send')
        self.assertIn(batch.idempotency_key, mail.outbox[0].body)

    def test_a_send_returning_zero_is_not_treated_as_delivered(self):
        """send() returns 0 on a silent failure — accepted is not delivered."""
        from fnb.models import FNBBatchSubmission as B, FNBHealthAlertState
        batch = self._batch(status=B.Status.SUBMITTED, age_hours=3)
        self._poll_error(batch)

        with mock.patch('fnb.health_watch.send_with_cfo_cc', return_value=0):
            res = self._run()

        self.assertFalse(res['emailed'])
        self.assertEqual(FNBHealthAlertState.load().stuck_keys, '')

    def test_the_six_hour_reminder_fires_while_a_batch_stays_stuck(self):
        from fnb.models import FNBBatchSubmission as B, FNBHealthAlertState
        batch = self._batch(status=B.Status.SUBMITTED, age_hours=3)
        self._poll_error(batch)
        self.assertTrue(self._run()['emailed'])

        # Nothing new, nothing resolved — but the quiet window has passed.
        state = FNBHealthAlertState.load()
        state.stuck_notified_at = timezone.now() - timedelta(hours=7)
        state.save(update_fields=['stuck_notified_at'])
        mail.outbox = []

        res = self._run()

        self.assertEqual(res['action'], 'reminder')
        self.assertTrue(res['emailed'])
        self.assertIn(batch.idempotency_key, mail.outbox[0].body)
