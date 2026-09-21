"""Objective check for the SHARED register date parser.

Root cause of two confirmed defects: ageing.py and prescription.py each grew
their own private date parser for the SAME spreadsheet column (date appointed),
and the two disagreed. An Excel serial produced a real age bucket in one module
and 'no date' in the other; a datetime crashed one and not the other.

One parser, used by both. Every input below appears in the real register
(`Subrogations sheet updated.xlsb`), which is an .xlsb — so numeric cells arrive
as FLOATS, not ints.
"""
from datetime import date, datetime

import pytest

from claims.recoveries.dates import parse_register_date


def test_date_passes_through():
    assert parse_register_date(date(2015, 7, 15)) == date(2015, 7, 15)


def test_datetime_is_reduced_to_a_date():
    """Must return `date`, not `datetime` — mixing the two raises TypeError on
    subtraction, which is exactly how the prescription clock crashed."""
    got = parse_register_date(datetime(2015, 7, 15, 0, 0))
    assert got == date(2015, 7, 15)
    assert type(got) is date


def test_iso_string():
    assert parse_register_date('2015-07-15') == date(2015, 7, 15)


def test_iso_datetime_string():
    """The register's Date Appointed column is full of these."""
    assert parse_register_date('2015-07-15 00:00:00') == date(2015, 7, 15)


def test_excel_serial_as_int():
    assert parse_register_date(45900) == date(2025, 8, 31)


def test_excel_serial_as_float():
    """THE BUG. .xlsb hands numeric cells back as floats, so every serial-dated
    case arrived as 45900.0 and was silently treated as 'no date appointed' —
    dropping it out of its true age bucket into the P21.8m unaged pile."""
    assert parse_register_date(45900.0) == date(2025, 8, 31)


def test_the_real_future_dated_serial_still_parses():
    """Claim 20241409 carries serial 46299 = 2026-10-04. Parsing is not the
    place to reject a future date — the caller decides that."""
    assert parse_register_date(46299) == date(2026, 10, 4)
    assert parse_register_date(46299.0) == date(2026, 10, 4)


@pytest.mark.parametrize('raw', [
    None, '', '   ', '\t',
    'No Appointment Date Set',      # real register value
    'TBA',
    'n/a',
    [], {},
])
def test_junk_is_none_never_an_exception(raw):
    assert parse_register_date(raw) is None


@pytest.mark.parametrize('raw', [0, 0.0, -5, 1e12, float('nan'), float('inf')])
def test_absurd_serials_are_none_not_a_crash(raw):
    """Serial 0 is 1899-12-30 — not a real appointment date. NaN and infinity
    must not raise or produce a nonsense date."""
    got = parse_register_date(raw)
    assert got is None or date(1990, 1, 1) <= got <= date(2100, 1, 1)


def test_a_serial_with_a_time_fraction_keeps_the_date():
    """Excel stores 2025-08-31 09:00 as 45900.375."""
    assert parse_register_date(45900.375) == date(2025, 8, 31)


def test_bool_is_not_a_serial():
    """`True` is an int subclass in Python; it must not become 1899-12-31."""
    assert parse_register_date(True) is None
    assert parse_register_date(False) is None
