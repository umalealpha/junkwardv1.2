"""Junk amounts in an uploaded payment list must be ONE bad row, never a crash.

Manus QC-UNICOIN-BULK-2026-09-12 (HIGH): "does not reject non-finite Decimal
values such as NaN/Infinity before comparison; malformed amounts can become a
server error rather than a row-level validation message."

It was right, and the reason is easy to miss: Decimal('NaN') and
Decimal('Infinity') are perfectly valid decimals, so the InvalidOperation guard
never fired. NaN then reached `amount <= 0`, where comparing a NaN raises
InvalidOperation and took the whole upload down with a 500 — one bad cell in a
two-hundred-line file and nobody is told which cell. Infinity was quieter and
worse: it compares greater than zero, so it passed as a real amount.

Run: manage.py test taskboard.test_bulk_upload_amounts
"""
from decimal import Decimal

from django.test import SimpleTestCase

from taskboard.bulk_payment_upload import _money


class JunkAmountsAreRefusedNotCrashed(SimpleTestCase):

    def test_nan_is_not_an_amount(self):
        for raw in ('NaN', 'nan', 'sNaN', '-NaN'):
            self.assertIsNone(_money(raw), f'{raw!r} was accepted as an amount')

    def test_infinity_is_not_an_amount(self):
        for raw in ('Infinity', '-Infinity', 'inf', '-inf', 'INF'):
            self.assertIsNone(_money(raw), f'{raw!r} was accepted as an amount')

    def test_a_refused_amount_never_reaches_the_comparison_that_crashed(self):
        """The crash was `amount <= 0` on a NaN. Proving None comes back is the
        same as proving that line is never reached with one."""
        val = _money('NaN')
        self.assertIsNone(val)
        # and for completeness: this is what used to happen.
        with self.assertRaises(Exception):
            _ = Decimal('NaN') <= 0

    def test_real_amounts_still_read(self):
        self.assertEqual(_money('1500.00'), Decimal('1500.00'))
        self.assertEqual(_money('P1,304.55'), Decimal('1304.55'))
        self.assertEqual(_money('BWP 275.50'), Decimal('275.50'))
        self.assertEqual(_money('(90.00)'), Decimal('-90.00'))
        self.assertIsNone(_money(''))
