"""core/tests/test_never_cc_admin.py — admin@ never receives approval mail.

CFO directive 2026-07-23: admin@alphadirect.co.bw is a shared mailbox worked
by junior staff, so no approval notification may be routed to it. The central
send helpers (send_with_cfo_cc / send_html_with_cfo_cc) strip it from BOTH the
TO and CC lists. These tests pin that guard.

Run: python manage.py test core.tests.test_never_cc_admin
"""
from django.core import mail
from django.test import TestCase, override_settings

from core.notifications import send_with_cfo_cc, send_html_with_cfo_cc

ADMIN = 'admin@alphadirect.co.bw'


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    NOTIFICATIONS_ENABLED=True,
)
class NeverCcAdminTests(TestCase):
    def setUp(self):
        mail.outbox = []

    def test_admin_stripped_from_to(self):
        send_with_cfo_cc('Approve me', 'body', to=[ADMIN, 'ubutale@alphadirect.co.bw'])
        self.assertEqual(len(mail.outbox), 1)
        m = mail.outbox[0]
        self.assertNotIn(ADMIN, m.to)
        self.assertNotIn(ADMIN, m.cc)
        self.assertIn('ubutale@alphadirect.co.bw', m.to)

    def test_admin_stripped_from_cc(self):
        send_html_with_cfo_cc('Approve me', '<p>hi</p>', to=['ubutale@alphadirect.co.bw'],
                              cc=[ADMIN])
        self.assertEqual(len(mail.outbox), 1)
        m = mail.outbox[0]
        self.assertNotIn(ADMIN, m.to)
        self.assertNotIn(ADMIN, m.cc)

    def test_admin_case_insensitive(self):
        send_with_cfo_cc('x', 'y', to=['Admin@AlphaDirect.co.bw'])
        self.assertEqual(len(mail.outbox), 1)
        self.assertNotIn('Admin@AlphaDirect.co.bw', mail.outbox[0].to)
