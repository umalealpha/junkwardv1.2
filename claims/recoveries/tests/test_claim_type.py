"""Objective check for normalise_claim_type.

Every raw string below was taken verbatim from the live subrogation register
(`Subrogations sheet updated.xlsb`, 988 rows, 17-Aug-2026). The register spells
one claim type fifteen different ways; the module must collapse them onto
Keetile Mokhendo's requested closed picklist and nothing else.
"""
import pytest

from claims.recoveries.claim_type import ClaimType, normalise_claim_type


ALLOWED = {
    ClaimType.MOTOR_ACCIDENT,
    ClaimType.BUILDINGS_COMBINED,
    ClaimType.FIRE_SPECIAL_PERILS,
    ClaimType.MONEY_FIDELITY,
    ClaimType.OTHER,
}


@pytest.mark.parametrize('raw', [
    'Motor Accident Claim',
    'MOTOR ACCIDENT',
    'Motor Accident Claim ',          # trailing space — 263 rows
    'Motor Accident Claim    ',       # four trailing spaces
    'MOTOR ACCIDENT ',
    'Motor Accident',
    'Accident',                       # 14 rows
    'MOTPR ACCIDENT CLAIM',           # keying typo, 1 row
    '  motor   accident  claim  ',    # collapsed whitespace
])
def test_every_motor_spelling_collapses_to_one_value(raw):
    assert normalise_claim_type(raw) == ClaimType.MOTOR_ACCIDENT


@pytest.mark.parametrize('raw', ['Building Combined', 'BUILDINGS COMBINED', 'buildings combined'])
def test_buildings_combined(raw):
    assert normalise_claim_type(raw) == ClaimType.BUILDINGS_COMBINED


@pytest.mark.parametrize('raw', ['Fire and special Perils Claim', 'FIRE AND SPECIAL PERILS'])
def test_fire_special_perils(raw):
    assert normalise_claim_type(raw) == ClaimType.FIRE_SPECIAL_PERILS


@pytest.mark.parametrize('raw', [
    'Money and Fedility Claim (money, airtime,cheques ets)',   # typo "Fedility", "ets"
    'Money and Fedility Claim (money, airtime,cheques etc)',
    'Money and Fidelity',
])
def test_money_and_fidelity(raw):
    assert normalise_claim_type(raw) == ClaimType.MONEY_FIDELITY


@pytest.mark.parametrize('raw', ['Non Motor', 'Glass', 'Something Nobody Has Seen', 'NON MOTOR'])
def test_unrecognised_falls_to_other_not_motor(raw):
    """'Non Motor' must NOT be swept into MOTOR just because it contains 'Motor'."""
    assert normalise_claim_type(raw) == ClaimType.OTHER


@pytest.mark.parametrize('raw', [None, '', '   ', '\t'])
def test_blank_is_other_never_an_exception(raw):
    assert normalise_claim_type(raw) == ClaimType.OTHER


def test_return_value_is_always_inside_the_closed_picklist():
    samples = [
        'Motor Accident Claim', 'MOTOR ACCIDENT', 'Accident', 'Non Motor', 'Glass',
        'Building Combined', 'Fire and special Perils Claim', None, '', 'zzz',
    ]
    assert {normalise_claim_type(s) for s in samples} <= ALLOWED


def test_non_string_input_is_tolerated():
    """xlsb hands back floats and ints for some cells — must not explode."""
    assert normalise_claim_type(0) == ClaimType.OTHER
    assert normalise_claim_type(20241409.0) == ClaimType.OTHER
