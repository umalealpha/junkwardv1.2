"""Consolidated ops digest (CFO 2026-08-05, CONSOLIDATED_EMAILS_ENABLED).

Locks down the behaviour of the single 06:30 "ops & system health" email
(send_ops_digest) and the self-skip guards added to the two standalone senders:

  (a) flag OFF  → both standalone commands still send separately;
                  send_ops_digest stays dormant (no double-send).
  (b) flag ON + Time Doctor PASS → one ops email, NO Time Doctor section
                  (all-green-silent).
  (c) flag ON + Time Doctor FAIL → one ops email WITH the Time Doctor section.
  (d) the ops email goes to the CFO (OUTBOUND_DIGEST_TO) and CCs excoboard@.

External calls are mocked: the whole Time Doctor pipeline via
timedoctor_healthcheck.run_checks (so no network / DB-heavy matching is hit),
and the outbound digest reads a couple of real OutboundEmailLog rows created in
setUp (the accounting record it summarises).
"""
from __future__ import annotations

from unittest.mock import patch

from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings

from core.models import OutboundEmailLog
from integrations.management.commands import timedoctor_healthcheck as tdhc

CFO = 'pganesharajah@alphadirect.co.bw'
EXCO = 'excoboard@alphadirect.co.bw'


def _checks(status: str):
    """Fake run_checks() return tuple for a given overall status."""
    if status == 'PASS':
        checks = [tdhc.Check('Time Doctor API', 'PASS', 'Reachable — 100 accounts returned.')]
    else:
        checks = [tdhc.Check('Stored data (pull)', status,
                             'The daily pull has stopped — newest data is 5 days old.')]
    return (status, '2026-08-05 06:30 SAST', checks, [], 100)


@override_settings(OUTBOUND_DIGEST_TO=[CFO])
class OpsDigestTests(TestCase):

    def setUp(self):
        mail.outbox = []
        # Two neutral-subject rows so the outbound-digest section has content and
        # the string "Time Doctor" can only come from the health section.
        OutboundEmailLog.objects.create(subject='Daily Brief',
                                        status=OutboundEmailLog.Status.SENT,
                                        to_addrs='a@alphadirect.co.bw')
        OutboundEmailLog.objects.create(subject='Payslip',
                                        status=OutboundEmailLog.Status.SENT,
                                        to_addrs='b@alphadirect.co.bw')

    # (a) -----------------------------------------------------------------
    @override_settings(CONSOLIDATED_EMAILS_ENABLED=False)
    def test_flag_off_both_standalone_commands_still_send(self):
        with patch.object(tdhc, 'run_checks', return_value=_checks('PASS')):
            call_command('email_outbound_digest', '--to', 'cfo@x')
            self.assertEqual(len(mail.outbox), 1, 'outbound digest did not send')

            mail.outbox = []
            call_command('timedoctor_healthcheck', '--to', 'exco@x')
            self.assertEqual(len(mail.outbox), 1,
                             'daily PASS heartbeat must still send when flag OFF')

            # And the consolidated sender must stay dormant (no double-send).
            mail.outbox = []
            call_command('send_ops_digest')
            self.assertEqual(len(mail.outbox), 0,
                             'send_ops_digest must not send when flag OFF')

    # (b) -----------------------------------------------------------------
    @override_settings(CONSOLIDATED_EMAILS_ENABLED=True)
    def test_flag_on_td_pass_one_email_no_td_section(self):
        with patch.object(tdhc, 'run_checks', return_value=_checks('PASS')):
            call_command('send_ops_digest')
        self.assertEqual(len(mail.outbox), 1)
        html = mail.outbox[0].alternatives[0][0]
        # Outbound section always present.
        self.assertIn('Outbound email', html)
        # Time Doctor section omitted entirely on PASS (all-green-silent).
        self.assertNotIn('Time Doctor health', html)
        self.assertNotIn('The daily pull has stopped', html)

    # (c) -----------------------------------------------------------------
    @override_settings(CONSOLIDATED_EMAILS_ENABLED=True)
    def test_flag_on_td_fail_one_email_with_td_section(self):
        with patch.object(tdhc, 'run_checks', return_value=_checks('FAIL')):
            call_command('send_ops_digest')
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        html = msg.alternatives[0][0]
        self.assertIn('Outbound email', html)
        self.assertIn('Time Doctor health — FAIL', html)
        self.assertIn('The daily pull has stopped', html)
        self.assertIn('Time Doctor FAIL', msg.subject)

    # WARN also surfaces the section (not all-green) --------------------
    @override_settings(CONSOLIDATED_EMAILS_ENABLED=True)
    def test_flag_on_td_warn_shows_section(self):
        with patch.object(tdhc, 'run_checks', return_value=_checks('WARN')):
            call_command('send_ops_digest')
        html = mail.outbox[0].alternatives[0][0]
        self.assertIn('Time Doctor health — WARN', html)

    # (d) -----------------------------------------------------------------
    @override_settings(CONSOLIDATED_EMAILS_ENABLED=True)
    def test_ops_email_goes_to_cfo_and_ccs_exco(self):
        with patch.object(tdhc, 'run_checks', return_value=_checks('PASS')):
            call_command('send_ops_digest')
        msg = mail.outbox[0]
        self.assertEqual(msg.to, [CFO])
        self.assertIn(EXCO, msg.cc)

    # dry-run computes but never sends, whatever the flag ---------------
    @override_settings(CONSOLIDATED_EMAILS_ENABLED=True)
    def test_dry_run_does_not_send(self):
        with patch.object(tdhc, 'run_checks', return_value=_checks('FAIL')):
            call_command('send_ops_digest', '--dry-run')
        self.assertEqual(len(mail.outbox), 0)

    # health check never raising is preserved: even if run_checks blows up,
    # the ops email still goes out (with a visible WARN section).
    @override_settings(CONSOLIDATED_EMAILS_ENABLED=True)
    def test_td_check_error_still_sends_with_warn_section(self):
        with patch.object(tdhc, 'run_checks', side_effect=RuntimeError('boom')):
            call_command('send_ops_digest')
        self.assertEqual(len(mail.outbox), 1)
        html = mail.outbox[0].alternatives[0][0]
        self.assertIn('Time Doctor health — WARN', html)
        self.assertIn('Health check errored', html)
