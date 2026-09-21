"""Objective check for parse_claim_number.

The register carries two numbering eras. 812 rows use the legacy 8-digit form
(20150018) which pre-dates Graphite; 169 use the Graphite form (G2026004951).
Only 169 of 988 register rows match a claim in Graphite — so the parser must say
plainly which era a number belongs to rather than guessing a link that isn't there.

xlsb also hands numeric cells back as floats, so '20241409' arrives as 20241409.0.
"""
import pytest

from claims.recoveries.claim_number import NumberFormat, parse_claim_number


def test_legacy_eight_digit():
    r = parse_claim_number('20150018')
    assert r.normalised == '20150018'
    assert r.fmt is NumberFormat.LEGACY
    assert r.year == 2015
    assert r.is_graphite_era is False


def test_graphite_format():
    r = parse_claim_number('G2026004951')
    assert r.normalised == 'G2026004951'
    assert r.fmt is NumberFormat.GRAPHITE
    assert r.year == 2026
    assert r.is_graphite_era is True


def test_float_from_xlsb_does_not_become_20241409_point_zero():
    """The single most likely silent corruption in the load."""
    r = parse_claim_number(20241409.0)
    assert r.normalised == '20241409'
    assert r.year == 2024
    assert r.fmt is NumberFormat.LEGACY


def test_int_input():
    assert parse_claim_number(20150018).normalised == '20150018'


def test_whitespace_and_case_are_cleaned():
    r = parse_claim_number('  g2026004951  ')
    assert r.normalised == 'G2026004951'
    assert r.fmt is NumberFormat.GRAPHITE


@pytest.mark.parametrize('raw', ['TBA', '', None, 'Column1', '   '])
def test_placeholders_are_unknown_not_an_exception(raw):
    r = parse_claim_number(raw)
    assert r.fmt is NumberFormat.UNKNOWN
    assert r.year is None
    assert r.is_graphite_era is False


def test_malformed_graphite_number_with_an_extra_digit_is_still_readable():
    """Real register value G20260005212 — 11 digits after the G, one too many.
    Must not be silently dropped; flag it instead."""
    r = parse_claim_number('G20260005212')
    assert r.year == 2026
    assert r.is_suspect is True


def test_ten_digit_legacy_number_is_read_not_rejected():
    """Real register value 2025003435."""
    r = parse_claim_number('2025003435')
    assert r.year == 2025
    assert r.fmt is NumberFormat.LEGACY


def test_trailing_suffix_is_preserved_but_flagged():
    """Real register value '20240016 -SA'."""
    r = parse_claim_number('20240016 -SA')
    assert r.year == 2024
    assert r.is_suspect is True


def test_year_is_never_absurd():
    """A four-digit lead that cannot be a claim year must not be reported as one."""
    r = parse_claim_number('99999999')
    assert r.year is None or 2000 <= r.year <= 2100
