"""Objective check for age_bucket.

Keetile Mokhendo's requested buckets, verbatim: 0-90, 91-180, 181-365,
1-2 years, over 2 years — measured in days since DATE APPOINTED.

542 of the 833 open cases have no date appointed at all, so NO_DATE is a real
bucket carrying P21.8m, not an error condition.
"""
from datetime import date

import pytest

from claims.recoveries.ageing import AgeBucket, age_bucket, days_since_appointed


AS_AT = date(2026, 8, 17)


@pytest.mark.parametrize('appointed,expected', [
    (date(2026, 8, 17), AgeBucket.B0_90),        # today, 0 days
    (date(2026, 8, 16), AgeBucket.B0_90),        # 1 day
    (date(2026, 5, 19), AgeBucket.B0_90),        # 90 days — boundary, inclusive
    (date(2026, 5, 18), AgeBucket.B91_180),      # 91 days
    (date(2026, 2, 18), AgeBucket.B91_180),      # 180 days — boundary
    (date(2026, 2, 17), AgeBucket.B181_365),     # 181 days
    (date(2025, 8, 17), AgeBucket.B181_365),     # 365 days — boundary
    (date(2025, 8, 16), AgeBucket.B1_2Y),        # 366 days
    (date(2024, 8, 17), AgeBucket.B1_2Y),        # 730 days — boundary
    (date(2024, 8, 16), AgeBucket.OVER_2Y),      # 731 days
    (date(2015, 7, 15), AgeBucket.OVER_2Y),      # real register row, claim 20150018
])
def test_bucket_boundaries_are_exact(appointed, expected):
    assert age_bucket(appointed, as_at=AS_AT) == expected


def test_no_date_is_its_own_bucket_not_an_error():
    """P21.8m of the open book sits here. It must be visible, not dropped."""
    assert age_bucket(None, as_at=AS_AT) == AgeBucket.NO_DATE


def test_future_date_appointed_is_rejected_as_no_date():
    """Keetile asked for 'no future dates'. A future appointment is a keying
    error; it must not report as a fresh 0-90 case."""
    assert age_bucket(date(2027, 1, 1), as_at=AS_AT) == AgeBucket.NO_DATE


def test_days_since_appointed():
    assert days_since_appointed(date(2026, 8, 17), as_at=AS_AT) == 0
    assert days_since_appointed(date(2026, 8, 10), as_at=AS_AT) == 7
    assert days_since_appointed(None, as_at=AS_AT) is None


def test_accepts_an_iso_string_and_a_datetime():
    from datetime import datetime
    assert age_bucket('2015-07-15', as_at=AS_AT) == AgeBucket.OVER_2Y
    assert age_bucket('2015-07-15 00:00:00', as_at=AS_AT) == AgeBucket.OVER_2Y
    assert age_bucket(datetime(2015, 7, 15, 0, 0), as_at=AS_AT) == AgeBucket.OVER_2Y


def test_accepts_an_excel_serial_date():
    """The register stores some dates as raw Excel serials. Epoch is 1899-12-30,
    so 45900 is 2025-08-31 — 351 days before the as-at date."""
    assert days_since_appointed(45900, as_at=AS_AT) == 351
    assert age_bucket(45900, as_at=AS_AT) == AgeBucket.B181_365


def test_the_real_future_dated_serial_in_the_register_is_rejected():
    """Live defect: claim 20241409 carries date-appointed serial 46299, which is
    2026-10-04 — seven weeks in the future. Keetile's spec says no future dates,
    so it must land in NO_DATE for correction, not report as a fresh case."""
    assert age_bucket(46299, as_at=AS_AT) == AgeBucket.NO_DATE


def test_excel_serial_as_a_float_must_age_the_same_as_an_int():
    """Defect found in final review. The register is an .xlsb, so numeric cells
    arrive as floats. 45900 aged correctly but 45900.0 fell into NO_DATE, quietly
    moving real cases into the P21.8m unaged pile."""
    assert age_bucket(45900.0, as_at=AS_AT) == age_bucket(45900, as_at=AS_AT)
    assert age_bucket(45900.0, as_at=AS_AT) == AgeBucket.B181_365
    assert days_since_appointed(45900.0, as_at=AS_AT) == 351


def test_unparseable_text_is_no_date_not_an_exception():
    assert age_bucket('No Appointment Date Set', as_at=AS_AT) == AgeBucket.NO_DATE
    assert age_bucket('', as_at=AS_AT) == AgeBucket.NO_DATE


def test_every_bucket_label_is_human_readable():
    """These strings go straight onto the CFO's screen."""
    assert AgeBucket.B0_90.label == '0-90 days'
    assert AgeBucket.OVER_2Y.label == 'Over 2 years'
    assert AgeBucket.NO_DATE.label == 'No date appointed'
