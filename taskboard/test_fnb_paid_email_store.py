"""The /payment-requests screen must not read the mailbox on a page load, and
the mailbox read must ask Graph for FNB's own emails (19-Sep-2026: 8.5-11s
loads; the 500-message cap counted ALL mail and saw 40 of 531 confirmations)."""
import tempfile
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from taskboard import fnb_email_view_helpers as h

PAID = [{'ref': 'R1', 'amount': '10.00', 'status': 'Fully Processed', 'paid': True,
         'date': '2026-09-18'}]


class PaidEmailStore(SimpleTestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.ov = override_settings(FNB_PAID_EMAIL_STORE_DIR=self.dir)
        self.ov.enable()
        cache.clear()

    def tearDown(self):
        self.ov.disable()
        cache.clear()

    def test_a_page_load_reads_the_saved_list_not_the_mailbox(self):
        with mock.patch.object(h, 'fetch_paid_fnb_emails', return_value=PAID):
            h.refresh_paid_fnb_emails_store(720)
        cache.clear()   # a different gunicorn worker: nothing in its own memory
        with mock.patch.object(h, 'fetch_paid_fnb_emails',
                               side_effect=AssertionError('read the mailbox')):
            self.assertEqual(h.fetch_paid_fnb_emails_cached(720), PAID)

    def test_a_stale_saved_list_is_not_trusted(self):
        with mock.patch.object(h, 'fetch_paid_fnb_emails', return_value=PAID):
            h.refresh_paid_fnb_emails_store(720)
        cache.clear()
        with mock.patch.object(h, '_STORE_MAX_AGE_S', -1), \
             mock.patch.object(h, 'fetch_paid_fnb_emails', return_value=[]) as f:
            self.assertEqual(h.fetch_paid_fnb_emails_cached(720), [])
            f.assert_called_once()


class GraphQueryAsksForFnbOnly(SimpleTestCase):
    @override_settings(FNB_EMAIL_READ_MAILBOX='cfo@example.com')
    def test_the_filter_names_the_fnb_sender(self):
        seen = []

        class R:
            status_code = 200
            def json(self): return {'value': []}

        def get(url, **kw):
            seen.append(url); return R()

        with mock.patch('core.management.commands.auto_reply_omni_mail._reader_token',
                        return_value='t'), mock.patch.object(h.requests, 'get', get):
            h.fetch_paid_fnb_emails(720)
        self.assertIn("from/emailAddress/address eq 'noreply@fnb.co.za'", seen[0])
