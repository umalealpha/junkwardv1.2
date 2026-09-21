"""pull_realpay_live — book parsing + guard rails (no network).

These exercise only the pure config parsing and the fail-fast guards that run
BEFORE any API pull, so they never touch the live RealPay endpoint.
"""
from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from realpay.management.commands.pull_realpay_live import _books


class RealPayLiveBooksParseTests(TestCase):
    @override_settings(
        REALPAY_LIVE_BOOKS='16244:Alpha Direct Insurance,24936:ADII, ,19111:Third')
    def test_books_parses_pairs_and_skips_blanks(self):
        self.assertEqual(
            _books(),
            [('16244', 'Alpha Direct Insurance'), ('24936', 'ADII'), ('19111', 'Third')],
        )

    @override_settings(REALPAY_LIVE_BOOKS='16244')
    def test_book_without_label(self):
        self.assertEqual(_books(), [('16244', '')])


class RealPayLiveCommandGuardTests(TestCase):
    @override_settings(REALPAY_LIVE_BOOKS='')
    def test_empty_config_raises(self):
        with self.assertRaises(CommandError):
            call_command('pull_realpay_live')

    @override_settings(REALPAY_LIVE_BOOKS='16244:ADI')
    def test_unknown_book_raises_before_any_pull(self):
        with self.assertRaises(CommandError):
            call_command('pull_realpay_live', '--book', '99999')

    @override_settings(REALPAY_LIVE_BOOKS='16244:ADI')
    def test_bad_month_raises(self):
        with self.assertRaises(CommandError):
            call_command('pull_realpay_live', '--month', 'not-a-month')
