"""
core/tests/test_date_format.py

`%-d` / `%-I` are a glibc extension — they render on the Linux containers and
raise `ValueError: Invalid format string` on the Windows dev machine. These
assert the exact strings Linux produces, so `format_dt` is pinned to that
output and the suite means the same thing on both platforms.
"""
from datetime import date, datetime

from django.test import SimpleTestCase

from core.date_format import format_dt


class FormatDtTests(SimpleTestCase):

    def test_day_loses_its_leading_zero(self):
        self.assertEqual(format_dt(date(2026, 7, 5), '%-d %b'), '5 Jul')

    def test_two_digit_day_is_left_alone(self):
        self.assertEqual(format_dt(date(2026, 7, 24), '%-d %b'), '24 Jul')

    def test_nexus_trip_label(self):
        # The WebFleet / phone-trip label — %-d and %-I in one format string.
        stamp = datetime(2026, 1, 3, 13, 7)
        self.assertEqual(format_dt(stamp, '%a %-d %b · %-I:%M %p'),
                         'Sat 3 Jan · 1:07 PM')

    def test_midnight_is_twelve_am(self):
        self.assertEqual(format_dt(datetime(2026, 6, 1, 0, 5), '%-I:%M %p'),
                         '12:05 AM')

    def test_zero_value_renders_as_one_digit(self):
        # glibc prints %-M on the hour as '0', not as an empty string.
        self.assertEqual(format_dt(datetime(2026, 6, 1, 9, 0), '%-M'), '0')

    def test_padded_directives_still_pad(self):
        self.assertEqual(format_dt(date(2026, 7, 5), '%d/%m'), '05/07')

    def test_escaped_percent_is_not_read_as_a_directive(self):
        self.assertEqual(format_dt(date(2026, 7, 5), '100%%-d'), '100%-d')
