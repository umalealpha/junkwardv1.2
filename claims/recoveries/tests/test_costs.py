"""Objective check for compute_total_recoverable.

Keetile Mokhendo's formula, verbatim:
    Total recovery = assessor fees + repairs + excess + towing + legal fees
                     LESS salvage.

This is the single arithmetic that turns six typed numbers into the amount we
chase. It must be Decimal, never float — the register is a P42.1m balance-sheet
item and float drift is not acceptable on money.
"""
from decimal import Decimal

import pytest

from claims.recoveries.costs import compute_total_recoverable


def D(x):
    return Decimal(str(x))


def test_keetile_formula_all_six_components():
    got = compute_total_recoverable(
        assessor_fees=D('1260.00'),
        repair_costs=D('35437.11'),
        client_excess=D('0'),
        towing_fees=D('0'),
        legal_fees=D('7369.07'),
        salvage_amount=D('0'),
    )
    # Real register row: claim 20150018, Total Recover 44,066.18
    assert got == Decimal('44066.18')


def test_salvage_is_deducted_not_added():
    got = compute_total_recoverable(
        assessor_fees=D('1000'), repair_costs=D('10000'), client_excess=D('0'),
        towing_fees=D('0'), legal_fees=D('0'), salvage_amount=D('2500'),
    )
    assert got == Decimal('8500.00')


def test_none_is_treated_as_zero_not_an_exception():
    """Most register rows leave most components blank."""
    got = compute_total_recoverable(
        assessor_fees=None, repair_costs=D('5857.31'), client_excess=D('1000.00'),
        towing_fees=None, legal_fees=None, salvage_amount=None,
    )
    # Register shape: an unnumbered ('TBA') row, Total Recover 6,857.31
    assert got == Decimal('6857.31')


def test_all_blank_gives_zero():
    assert compute_total_recoverable(None, None, None, None, None, None) == Decimal('0.00')


def test_result_is_a_decimal_never_a_float():
    got = compute_total_recoverable(D('0.1'), D('0.2'), None, None, None, None)
    assert isinstance(got, Decimal)
    assert got == Decimal('0.30')          # float would give 0.30000000000000004


def test_rounds_to_two_places_half_up():
    """VAT and money round HALF UP at Alpha Direct — never banker's rounding."""
    got = compute_total_recoverable(D('0.005'), None, None, None, None, None)
    assert got == Decimal('0.01')
    got2 = compute_total_recoverable(D('0.015'), None, None, None, None, None)
    assert got2 == Decimal('0.02')         # banker's rounding would give 0.02 too
    got3 = compute_total_recoverable(D('0.025'), None, None, None, None, None)
    assert got3 == Decimal('0.03')         # banker's rounding WOULD give 0.02 — must not


def test_salvage_larger_than_costs_gives_a_negative_and_does_not_clamp():
    """If salvage exceeds the loss we owe money back. Silently clamping to zero
    would hide exactly the 24 over-recovered cases already in the register."""
    got = compute_total_recoverable(None, D('1000'), None, None, None, D('2500'))
    assert got == Decimal('-1500.00')


def test_accepts_int_float_and_string_inputs_from_a_spreadsheet():
    got = compute_total_recoverable(1260, 35437.11, '0', 0, '7369.07', None)
    assert got == Decimal('44066.18')


def test_comma_formatted_money_is_read_not_silently_zeroed():
    """Defect found in final review. '1,260.00' is the ordinary Excel money
    format, not junk — but it degraded to zero, silently understating the
    recoverable by the whole cell. Falling back to zero is right for 'n/a' or
    'TBA'; it is wrong for a real amount."""
    assert compute_total_recoverable('1,260.00', None, None, None, None, None) == Decimal('1260.00')
    assert compute_total_recoverable('35,437.11', '1,260.00', None, None, None, None) == Decimal('36697.11')


@pytest.mark.parametrize('raw,expected', [
    ('P 1,260.00', '1260.00'),      # Pula prefix
    ('BWP 1,260.00', '1260.00'),
    (' 1 260.00 ', '1260.00'),      # space as thousands separator
    ('(1,260.00)', '-1260.00'),     # accounting negative
    ('-1,260.00', '-1260.00'),
])
def test_real_spreadsheet_money_formats(raw, expected):
    assert compute_total_recoverable(raw, None, None, None, None, None) == Decimal(expected)


@pytest.mark.parametrize('raw', ['n/a', 'TBA', '-', '', 'No Number', 'unknown'])
def test_genuine_junk_still_falls_back_to_zero(raw):
    assert compute_total_recoverable(raw, None, None, None, None, None) == Decimal('0.00')


def test_nan_and_infinity_do_not_poison_the_total():
    assert compute_total_recoverable(float('nan'), None, None, None, None, None) == Decimal('0.00')
    assert compute_total_recoverable(float('inf'), None, None, None, None, None) == Decimal('0.00')


def test_outstanding_balance_helper_is_recoverable_less_receipts():
    from claims.recoveries.costs import compute_outstanding
    assert compute_outstanding(D('44066.18'), D('36000.00')) == Decimal('8066.18')
    assert compute_outstanding(D('2898.83'), D('163502.74')) == Decimal('-160603.91')
    assert compute_outstanding(D('100'), None) == Decimal('100.00')
