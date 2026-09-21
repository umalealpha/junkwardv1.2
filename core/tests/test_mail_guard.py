"""GuardedEmailBackend + outbound digest (CFO instruction 2026-07-25, option A).

The incident these tests lock down: ~18 senders build EmailMessage /
EmailMultiAlternatives / send_mail directly and never went through
core.notifications, so the shared-mailbox blocklist did not apply to them —
including the two that email the Omni sign-in code and the password-reset code.
The shared `admin` account carried admin@alphadirect.co.bw, so its login codes
were delivered into a mailbox worked by junior clerks.
"""
from __future__ import annotations

from django.core import mail
from django.core.mail import EmailMessage, EmailMultiAlternatives, send_mail
from django.test import TestCase, override_settings

from core.models import OutboundEmailLog

GUARD = 'core.email_backends.GuardedEmailBackend'
INNER = 'django.core.mail.backends.locmem.EmailBackend'
BARRED = 'admin@alphadirect.co.bw'
CFO = 'pganesharajah@alphadirect.co.bw'


@override_settings(EMAIL_BACKEND=GUARD, EMAIL_INNER_BACKEND=INNER,
                   NEVER_DELIVER_EMAILS=[BARRED])
class MailGuardTests(TestCase):

    def setUp(self):
        mail.outbox = []

    # ---- the actual incident -------------------------------------------
    def test_raw_send_mail_cannot_reach_barred_mailbox(self):
        """send_mail is the OTP path — it must not reach the shared mailbox."""
        send_mail('Your Omni sign-in code', 'code is 123456',
                  'omni@alphadirect.co.bw', [BARRED])
        self.assertEqual(len(mail.outbox), 0, 'sign-in code was delivered!')
        row = OutboundEmailLog.objects.get()
        self.assertEqual(row.status, OutboundEmailLog.Status.BLOCKED)
        self.assertIn(BARRED, row.blocked_addrs)

    def test_directly_built_message_is_scrubbed_not_dropped(self):
        """A barred CC is removed; the real recipient still gets the mail."""
        EmailMessage('Payslip', 'body', 'omni@alphadirect.co.bw',
                     ['staff@alphadirect.co.bw'], cc=[BARRED]).send()
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, ['staff@alphadirect.co.bw'])
        self.assertEqual(sent.cc, [])
        row = OutboundEmailLog.objects.get()
        self.assertEqual(row.status, OutboundEmailLog.Status.SENT)
        self.assertIn(BARRED, row.blocked_addrs)

    def test_barred_address_stripped_from_bcc_too(self):
        EmailMessage('X', 'b', 'omni@alphadirect.co.bw',
                     ['ok@alphadirect.co.bw'], bcc=[BARRED]).send()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].bcc, [])

    def test_display_name_form_is_still_caught(self):
        """'System Admin <admin@...>' must not slip past the check."""
        send_mail('X', 'b', 'omni@alphadirect.co.bw',
                  [f'System Admin <{BARRED}>'])
        self.assertEqual(len(mail.outbox), 0)

    def test_case_and_whitespace_insensitive(self):
        send_mail('X', 'b', 'omni@alphadirect.co.bw', ['  Admin@AlphaDirect.CO.BW '])
        self.assertEqual(len(mail.outbox), 0)

    # ---- must not over-block -------------------------------------------
    def test_normal_mail_is_untouched(self):
        send_mail('Hello', 'body', 'omni@alphadirect.co.bw',
                  ['a@alphadirect.co.bw', 'b@alphadirect.co.bw'])
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to,
                         ['a@alphadirect.co.bw', 'b@alphadirect.co.bw'])
        self.assertEqual(OutboundEmailLog.objects.get().blocked_addrs, '')

    @override_settings(NEVER_CC_EMAILS=['arjuniyer@alphadirect.co.bw'])
    def test_never_cc_list_is_NOT_enforced_here(self):
        """Arjun must still receive mail addressed to him directly.

        NEVER_CC_EMAILS means "do not AUTO-CC as a decision-maker", not "never
        deliver". integrations.check_timedoctor_token mails him directly on
        purpose; enforcing that list at backend level would silently kill the
        token-renewal reminder and let the Time Doctor token lapse.
        """
        send_mail('Token expiring', 'renew it', 'omni@alphadirect.co.bw',
                  ['arjuniyer@alphadirect.co.bw'])
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['arjuniyer@alphadirect.co.bw'])

    # ---- oversight log --------------------------------------------------
    def test_body_is_never_stored(self):
        """Bodies carry salary figures and live codes — must not be persisted."""
        send_mail('Your Omni sign-in code', 'your code is 998877',
                  'omni@alphadirect.co.bw', ['staff@alphadirect.co.bw'])
        row = OutboundEmailLog.objects.get()
        blob = ' '.join(str(v) for v in row.__dict__.values())
        self.assertNotIn('998877', blob)
        self.assertNotIn('your code is', blob)

    def test_attachment_count_recorded(self):
        msg = EmailMultiAlternatives('Payslip', 'see attached',
                                     'omni@alphadirect.co.bw',
                                     ['staff@alphadirect.co.bw'])
        msg.attach('payslip.pdf', b'%PDF-1.4 fake', 'application/pdf')
        msg.send()
        self.assertEqual(OutboundEmailLog.objects.get().attachment_count, 1)

    def test_logging_failure_never_blocks_the_mail(self):
        """Oversight must not be why a payslip fails to send."""
        from unittest.mock import patch
        with patch('core.models.OutboundEmailLog.objects.create',
                   side_effect=RuntimeError('db down')):
            send_mail('X', 'b', 'omni@alphadirect.co.bw', ['a@alphadirect.co.bw'])
        self.assertEqual(len(mail.outbox), 1)

    # ---- digest ---------------------------------------------------------
    # These pin CONSOLIDATED_EMAILS_ENABLED explicitly. They used to inherit it
    # from whatever environment the suite happened to run under, and when the CFO
    # turned consolidation on (2026-08-05) the standalone command began correctly
    # self-skipping — so both tests failed while the product was working fine. A
    # test that depends on ambient configuration reports the environment, not the
    # code. What actually matters is that the CFO gets told what Omni emailed, so
    # that guarantee is now asserted under BOTH settings.
    @override_settings(CONSOLIDATED_EMAILS_ENABLED=False)
    def test_digest_reports_sent_blocked_and_stripped(self):
        from django.core.management import call_command
        send_mail('Daily Brief', 'b', 'omni@alphadirect.co.bw',
                  ['a@alphadirect.co.bw'])
        send_mail('Daily Brief', 'b', 'omni@alphadirect.co.bw',
                  ['b@alphadirect.co.bw'])
        send_mail('Your Omni sign-in code', 'c', 'omni@alphadirect.co.bw', [BARRED])
        EmailMessage('PO', 'b', 'omni@alphadirect.co.bw',
                     ['supplier@x.com'], cc=[BARRED]).send()
        mail.outbox = []

        call_command('email_outbound_digest', '--to', 'cfo@alphadirect.co.bw')
        self.assertEqual(len(mail.outbox), 1)
        html = mail.outbox[0].alternatives[0][0]
        self.assertIn('Daily Brief', html)
        self.assertIn('NOT SENT', html)          # the suppressed sign-in code
        self.assertIn('stripped', html)          # the PO that carried a barred CC
        self.assertIn(BARRED, html)

    @override_settings(CONSOLIDATED_EMAILS_ENABLED=False)
    def test_digest_says_so_when_clean(self):
        from django.core.management import call_command
        send_mail('Fine', 'b', 'omni@alphadirect.co.bw', ['a@alphadirect.co.bw'])
        mail.outbox = []
        call_command('email_outbound_digest', '--to', 'cfo@alphadirect.co.bw')
        html = mail.outbox[0].alternatives[0][0]
        self.assertIn('Nothing blocked and nothing failed', html)

    # ---- the consolidated path (CFO 2026-08-05) --------------------------
    @override_settings(CONSOLIDATED_EMAILS_ENABLED=True)
    def test_the_standalone_digest_stands_down_when_consolidation_is_on(self):
        """No duplicate: send_ops_digest owns the delivery when the flag is on."""
        from django.core.management import call_command
        send_mail('Fine', 'b', 'omni@alphadirect.co.bw', ['a@alphadirect.co.bw'])
        mail.outbox = []
        call_command('email_outbound_digest', '--to', 'cfo@alphadirect.co.bw')
        self.assertEqual(len(mail.outbox), 0, 'the CFO would get two copies')

    @override_settings(CONSOLIDATED_EMAILS_ENABLED=True,
                       OUTBOUND_DIGEST_TO=[CFO])
    def test_but_the_digest_still_reaches_the_cfo_inside_the_ops_email(self):
        """The guarantee that matters. If this ever fails, the CFO has silently
        stopped being told what Omni emailed — which is exactly how the blocked
        sign-in codes went unnoticed in the first place.

        The recipient is pinned, not inherited: without that this test passes
        happily while OUTBOUND_DIGEST_TO points at the wrong person, which is the
        very failure it claims to catch. Time Doctor is stubbed because
        send_ops_digest also renders a live health section — a unit test has no
        business calling a third-party API with production credentials."""
        from unittest.mock import patch as _patch
        from django.core.management import call_command
        send_mail('Your Omni sign-in code', 'c', 'omni@alphadirect.co.bw', [BARRED])
        mail.outbox = []
        with _patch('integrations.management.commands.timedoctor_healthcheck.run_checks',
                    return_value=('PASS', 'stubbed', [], [], 1)):
            call_command('send_ops_digest')
        self.assertEqual(len(mail.outbox), 1, 'nobody was told anything')
        self.assertIn(CFO, mail.outbox[0].to, 'the digest went to somebody else')
        html = mail.outbox[0].alternatives[0][0]
        self.assertIn('NOT SENT', html)      # the suppressed sign-in code
        self.assertIn(BARRED, html)

    @override_settings(CONSOLIDATED_EMAILS_ENABLED=True)
    def test_a_dry_run_still_works_with_consolidation_on(self):
        # Kept so the digest stays checkable by hand without sending anything.
        from django.core.management import call_command
        call_command('email_outbound_digest', '--dry-run')
        self.assertEqual(len(mail.outbox), 0)
