"""Objective check for prescription_status — CFO-approved idea 1.

The largest single exposure in the register: 272 open cases worth P11.86m were
appointed more than two years ago, and 296 open cases date from 2015-2019.
Nobody is watching the clock.

Botswana's Prescription Act sets the ordinary period for a contractual or
delictual debt at THREE YEARS. The clock here runs from the date of loss where
we have one, otherwise from the date appointed — never from nothing.

This module only computes and warns. It never decides to abandon a claim: an
expired case stays visible so the decision is a person's, on the record.
"""
from datetime import date

import pytest

from claims.recoveries.prescription import (
    PRESCRIPTION_YEARS, PrescriptionRisk, prescription_status,
)


AS_AT = date(2026, 8, 17)


def test_the_period_is_three_years():
    assert PRESCRIPTION_YEARS == 3


def test_fresh_case_is_safe():
    r = prescription_status(date_of_loss=date(2026, 6, 1), date_appointed=None, as_at=AS_AT)
    assert r.expires_on == date(2029, 6, 1)
    assert r.expired is False
    assert r.risk is PrescriptionRisk.SAFE
    assert r.days_remaining == 1019


def test_expired_case_is_flagged_expired_not_deleted():
    """Real register row: claim 20150018, appointed 2015-07-15."""
    r = prescription_status(date_of_loss=None, date_appointed=date(2015, 7, 15), as_at=AS_AT)
    assert r.expired is True
    assert r.risk is PrescriptionRisk.EXPIRED
    assert r.days_remaining < 0


@pytest.mark.parametrize('loss,expected_risk', [
    (date(2023, 8, 18), PrescriptionRisk.CRITICAL),   # 1 day left
    (date(2023, 8, 17), PrescriptionRisk.CRITICAL),   # expires today, 0 days
    (date(2023, 9, 15), PrescriptionRisk.CRITICAL),   # 29 days
    (date(2023, 9, 16), PrescriptionRisk.URGENT),     # 30 days — boundary
    (date(2023, 11, 15), PrescriptionRisk.URGENT),    # 90 days
    (date(2023, 11, 16), PrescriptionRisk.WATCH),     # 91 days — boundary
    (date(2024, 2, 17), PrescriptionRisk.WATCH),      # 184 days
    (date(2024, 8, 17), PrescriptionRisk.WATCH),      # 365 days
    (date(2024, 8, 18), PrescriptionRisk.SAFE),       # 366 days — boundary
    (date(2024, 8, 19), PrescriptionRisk.SAFE),       # 367 days
])
def test_risk_ladder(loss, expected_risk):
    """Ladder, by days remaining until prescription:
        < 0    EXPIRED
        0-29   CRITICAL
        30-90  URGENT
        91-365 WATCH
        366+   SAFE
    """
    r = prescription_status(date_of_loss=loss, date_appointed=None, as_at=AS_AT)
    assert r.risk is expected_risk


def test_date_of_loss_wins_over_date_appointed():
    """Prescription runs from when the debt arose, not from when we got round to
    appointing somebody. Using the appointment date would flatter every old case."""
    r = prescription_status(
        date_of_loss=date(2018, 1, 1),
        date_appointed=date(2026, 1, 1),
        as_at=AS_AT,
    )
    assert r.expires_on == date(2021, 1, 1)
    assert r.expired is True
    assert r.basis == 'date_of_loss'


def test_falls_back_to_date_appointed_when_there_is_no_loss_date():
    r = prescription_status(date_of_loss=None, date_appointed=date(2024, 3, 1), as_at=AS_AT)
    assert r.expires_on == date(2027, 3, 1)
    assert r.basis == 'date_appointed'


def test_no_date_at_all_is_unknown_and_must_be_surfaced():
    """542 open cases worth P21.8m have no date appointed. Reporting them as SAFE
    would be the worst possible outcome — they are the least controlled cases."""
    r = prescription_status(date_of_loss=None, date_appointed=None, as_at=AS_AT)
    assert r.risk is PrescriptionRisk.UNKNOWN
    assert r.expired is False
    assert r.expires_on is None
    assert r.days_remaining is None


def test_unknown_is_not_treated_as_safe():
    unknown = prescription_status(None, None, as_at=AS_AT)
    safe = prescription_status(date(2026, 6, 1), None, as_at=AS_AT)
    assert unknown.risk is not safe.risk
    assert unknown.needs_attention is True
    assert safe.needs_attention is False


def test_needs_attention_covers_everything_except_safe():
    for r in (
        prescription_status(date(2023, 8, 18), None, as_at=AS_AT),   # critical
        prescription_status(date(2023, 10, 1), None, as_at=AS_AT),   # urgent
        prescription_status(date(2024, 1, 1), None, as_at=AS_AT),    # watch
        prescription_status(date(2015, 1, 1), None, as_at=AS_AT),    # expired
        prescription_status(None, None, as_at=AS_AT),                # unknown
    ):
        assert r.needs_attention is True


def test_leap_day_does_not_crash():
    r = prescription_status(date_of_loss=date(2024, 2, 29), date_appointed=None, as_at=AS_AT)
    assert r.expires_on in (date(2027, 2, 28), date(2027, 3, 1))


def test_accepts_iso_strings():
    r = prescription_status(date_of_loss='2018-01-01', date_appointed=None, as_at=AS_AT)
    assert r.expired is True


# --- defects found in final review: prescription and ageing read the SAME
# --- register columns but disagreed on what a date looks like.

def test_datetime_input_does_not_crash():
    """Was raising TypeError (datetime minus date), breaking the never-raise
    contract on the highest-value control in the module."""
    from datetime import datetime
    r = prescription_status(datetime(2018, 1, 1, 0, 0), None, as_at=AS_AT)
    assert r.expired is True
    assert r.basis == 'date_of_loss'


def test_accepts_an_iso_datetime_string_like_the_register_stores():
    r = prescription_status(None, '2015-07-15 00:00:00', as_at=AS_AT)
    assert r.expired is True
    assert r.basis == 'date_appointed'


@pytest.mark.parametrize('serial', [45900, 45900.0])
def test_accepts_excel_serials_int_and_float(serial):
    """ageing.py accepted serials for this very column and prescription did not,
    so the same case could be aged but carry UNKNOWN prescription risk."""
    r = prescription_status(None, serial, as_at=AS_AT)
    assert r.expires_on == date(2028, 8, 31)
    assert r.risk is PrescriptionRisk.SAFE


def test_the_two_modules_agree_on_what_counts_as_a_date():
    """Regression guard on the root cause: anything ageing can age must also get
    a real prescription verdict, and vice versa."""
    from claims.recoveries.ageing import AgeBucket, age_bucket
    for value in (date(2015, 7, 15), '2015-07-15', '2015-07-15 00:00:00',
                  45900, 45900.0):
        aged = age_bucket(value, as_at=AS_AT) is not AgeBucket.NO_DATE
        presc = prescription_status(None, value, as_at=AS_AT).risk is not PrescriptionRisk.UNKNOWN
        assert aged == presc, f'{value!r}: ageing={aged} prescription={presc}'
