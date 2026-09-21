"""The committee email must land members ON the exceptions board.

5-Sep-2026: a committee member followed the wording "the Exceptions board" to
Administration -> Exceptions (the anomaly list) and found nothing. The link in
the committee email now carries ?exceptions=1, which opens the board itself.
No database needed — the helper is a pure string.
"""
from django.test import SimpleTestCase, override_settings

from taskboard.payment_views import _committee_link


class CommitteeLinkTests(SimpleTestCase):
    databases = []

    @override_settings(PUBLIC_BASE_URL='https://omni.example.test/')
    def test_link_opens_the_committee_board_directly(self):
        self.assertEqual(_committee_link(),
                         'https://omni.example.test/payment-requests?exceptions=1')

    def test_default_base_is_the_live_site(self):
        self.assertTrue(_committee_link().startswith('https://omni.alphadirect.co.bw/payment-requests'))
        self.assertIn('?exceptions=1', _committee_link())
