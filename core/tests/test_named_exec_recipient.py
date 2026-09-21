"""A recipient the CFO names explicitly must actually receive the email.

The 2026-05-25 directive on _NEVER_CC reads: Arun and Arjun "must NOT be CC'd on
any automated outbound mail UNLESS the CFO names them explicitly in the call".
Only the first half was implemented. send_html_with_cfo_cc stripped them from BOTH
`to` and `cc` unconditionally, and did not record the removal in blocked_addrs — so
the send reported success and the person was quietly not on it.

Found on 2026-08-10: the CFO asked for aiyer@ on an IT ticket to Sechele, the send
returned 1, the log showed only kmolefe@ and excoboard@, and the email body itself
told the recipient that Arun was copied.

The guarded backend already gets this right — see test_mail_guard
`test_never_cc_list_is_NOT_enforced_here` — so this closes the gap in the helper.
"""
from django.core import mail
from django.test import TestCase, override_settings

from core.notifications import send_html_with_cfo_cc

ARUN = 'aiyer@alphadirect.co.bw'
HTML = '<html><body><p>body text so the guard does not see this as empty</p></body></html>'


@override_settings(NOTIFICATIONS_ENABLED=True,
                   EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   NEVER_CC_EMAILS=[ARUN])
class NamedExecRecipientTests(TestCase):

    def setUp(self):
        mail.outbox = []

    def test_by_default_an_automated_send_still_cannot_reach_him(self):
        """The protection the directive actually asked for stays the default."""
        send_html_with_cfo_cc(subject='auto', html=HTML,
                              to=['someone@alphadirect.co.bw'], cc=[ARUN])
        self.assertEqual(len(mail.outbox), 1)
        self.assertNotIn(ARUN, mail.outbox[0].cc)

    def test_when_named_explicitly_he_is_on_the_cc(self):
        send_html_with_cfo_cc(subject='named', html=HTML,
                              to=['someone@alphadirect.co.bw'], cc=[ARUN],
                              allow_named_exec=True)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(ARUN, mail.outbox[0].cc,
                      'the CFO named him — the directive allows exactly this')

    def test_when_named_explicitly_he_can_be_the_recipient(self):
        send_html_with_cfo_cc(subject='to him', html=HTML, to=[ARUN],
                              allow_named_exec=True)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(ARUN, mail.outbox[0].to)

    def test_addressing_him_directly_without_the_flag_sends_to_nobody(self):
        """The failure that started this: `to` was emptied and it still 'worked'.

        Pinned rather than changed — the flag is the way in, and a silent empty
        send is exactly what made the original fault invisible.
        """
        send_html_with_cfo_cc(subject='to him', html=HTML, to=[ARUN])
        if mail.outbox:
            self.assertNotIn(ARUN, mail.outbox[0].to)

    def test_the_mandatory_cfo_cc_survives_the_flag(self):
        send_html_with_cfo_cc(subject='named', html=HTML,
                              to=['someone@alphadirect.co.bw'], cc=[ARUN],
                              allow_named_exec=True)
        self.assertIn('excoboard@alphadirect.co.bw', mail.outbox[0].cc,
                      'the house CFO cc must not be lost')
