"""Objective check for normalise_status.

Raw values taken verbatim from the live register. Target picklist is exactly the
one Keetile Mokhendo asked for: In Progress, Settled, Awaiting Form of Release,
Written Off, Closed — plus UNKNOWN for the 10 open cases that carry no status.
"""
import pytest

from claims.recoveries.status import RecoveryStatus, normalise_status


ALLOWED = {
    RecoveryStatus.IN_PROGRESS,
    RecoveryStatus.SETTLED,
    RecoveryStatus.AWAITING_RELEASE,
    RecoveryStatus.WRITTEN_OFF,
    RecoveryStatus.CLOSED,
    RecoveryStatus.UNKNOWN,
}


@pytest.mark.parametrize('raw', [
    'In progress',      # 663 rows
    'in progress',      # 121 rows
    'In Progress',      # 16 rows
    'IN PROGRESS',
    ' In progress ',
    'Lawyer',           # 9 rows — with a lawyer IS in progress
])
def test_in_progress_variants(raw):
    assert normalise_status(raw) == RecoveryStatus.IN_PROGRESS


@pytest.mark.parametrize('raw', [
    'Settled',          # 92 rows
    'settled',          # 13 rows
    'SETTLED',          # 4 rows
    'Settled ',         # 3 rows, trailing space
    'settled Closed',   # 4 rows
    'Settled_Closed',   # 2 rows
])
def test_settled_variants_including_the_settled_closed_hybrids(raw):
    assert normalise_status(raw) == RecoveryStatus.SETTLED


@pytest.mark.parametrize('raw', ['AWAITING FORM OF RELEASE', 'Awaiting Form of Release', 'awaiting form of release'])
def test_awaiting_release(raw):
    assert normalise_status(raw) == RecoveryStatus.AWAITING_RELEASE


@pytest.mark.parametrize('raw', [
    'BAD DEBT_SUBROGATION WRITE OFF',   # 6 rows
    'Written Off',
    'write off',
    'WRITE-OFF',
])
def test_written_off(raw):
    assert normalise_status(raw) == RecoveryStatus.WRITTEN_OFF


@pytest.mark.parametrize('raw', ['closed', 'Closed', 'CLOSED'])
def test_closed(raw):
    assert normalise_status(raw) == RecoveryStatus.CLOSED


@pytest.mark.parametrize('raw', [None, '', '   ', 'Attached', 'zzz', 0])
def test_blank_or_meaningless_is_unknown_never_an_exception(raw):
    assert normalise_status(raw) == RecoveryStatus.UNKNOWN


def test_settled_closed_is_settled_not_closed():
    """'settled Closed' means the money came in. It must not be filed as a bare
    Closed, which would hide a successful recovery from the recovery rate."""
    assert normalise_status('settled Closed') == RecoveryStatus.SETTLED
    assert normalise_status('closed') == RecoveryStatus.CLOSED


def test_return_value_is_always_inside_the_closed_picklist():
    samples = ['In progress', 'Lawyer', 'Settled_Closed', 'BAD DEBT_SUBROGATION WRITE OFF',
               'AWAITING FORM OF RELEASE', 'closed', 'Attached', None, '']
    assert {normalise_status(s) for s in samples} <= ALLOWED
