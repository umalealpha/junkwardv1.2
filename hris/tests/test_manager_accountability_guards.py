"""Guards that stop the Manager Accountability Note accusing the innocent.

Two live incidents on 2026-07-28 drove these (CFO):
  * Oratile Ria Tlhomelang was escalated to the C-suite for an absence she had
    taken leave for — she just had no LeaveRequest row, because the register
    held 25 rows company-wide. Absence with no record is not proof.
  * Unami Butale was escalated over the CEO's dark days. The CEO does not clock
    on Time Doctor, so his absence must never be flagged.

DB-free: exercises the pure helpers in hris/manager_accountability.py. Run:

    python hris/tests/test_manager_accountability_guards.py
"""
import datetime
import os
import sys

import django

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'alpha_finance.settings')
django.setup()

from hris import manager_accountability as ma            # noqa: E402


def test_ceo_is_exempt_by_name():
    names = ma.exempt_names()
    assert 'arun iyer' in names
    # The note matcher lower-cases before comparing, so casing must not matter.
    assert 'Arun Iyer'.strip().lower() in names


def test_exempt_list_takes_settings_override():
    from django.test import override_settings
    with override_settings(MA_EXEMPT_NAMES=['Someone Else']):
        names = ma.exempt_names()
    assert 'someone else' in names
    assert 'arun iyer' in names                          # override adds, never replaces


def test_reliability_check_fails_closed():
    """A broken or empty lookup must read as NOT reliable, so escalation falls
    back to the CFO instead of naming a manager to the board."""
    got = ma.leave_register_reliable(datetime.date(2026, 7, 28))
    assert got is False                                  # no DB / no heads in the test env


def test_reliability_threshold_is_a_real_ratio():
    assert 0 < ma.LEAVE_ROWS_PER_HEAD < 1
    assert ma.LEAVE_WINDOW_DAYS >= 30
    # Prod on 2026-07-28 held 25 leave rows in total, company-wide. The floor
    # exists to catch a register nobody fills in, not to second-guess a full one.


class _Day:
    """Stand-in for a WorkdayJustification row."""
    def __init__(self, required, tracked, status):
        self.required_hours, self.tracked_hours, self.status = required, tracked, status


# Oratile Ria Tlhomelang's real week, from prod on 2026-07-28. She met her hours
# Wed–Fri, then Sat was a 4h rota day she missed, Sun required nothing, and Mon
# she missed. The old counter called Sat+Sun+Mon "3 consecutive dark days" and
# escalated her manager to the C-suite.
ORATILE_WEEK = {
    datetime.date(2026, 7, 27): _Day(6.5, 0.0, 'unjustified'),   # Mon
    datetime.date(2026, 7, 26): _Day(0.0, 0.0, 'not_required'),  # Sun
    datetime.date(2026, 7, 25): _Day(4.0, 0.0, 'unjustified'),   # Sat
    datetime.date(2026, 7, 24): _Day(6.5, 6.92, 'met'),          # Fri
    datetime.date(2026, 7, 23): _Day(6.5, 7.61, 'met'),          # Thu
    datetime.date(2026, 7, 22): _Day(6.5, 7.47, 'met'),          # Wed
    datetime.date(2026, 7, 21): _Day(0.0, 0.0, 'not_required'),  # Tue
}


def test_sunday_no_longer_bridges_two_missed_days():
    dark, observed = ma.dark_working_streak(ORATILE_WEEK, {}, datetime.date(2026, 7, 27))
    assert dark == 2, f'Sat + Mon only — got {dark}'
    assert dark < ma.MIN_DARK_DAYS                       # so no note, no escalation
    assert observed >= ma.MIN_DARK_DAYS                  # her data was not thin


def test_a_worked_day_ends_the_streak():
    week = dict(ORATILE_WEEK)
    week[datetime.date(2026, 7, 27)] = _Day(6.5, 7.0, 'met')
    dark, _ = ma.dark_working_streak(week, {}, datetime.date(2026, 7, 27))
    assert dark == 0


def test_three_real_working_days_still_flag():
    """The feature must keep working — a genuine 3-working-day gap still counts."""
    week = {datetime.date(2026, 7, 27): _Day(6.5, 0.0, 'unjustified'),   # Mon
            datetime.date(2026, 7, 26): _Day(0.0, 0.0, 'not_required'),  # Sun — skipped
            datetime.date(2026, 7, 25): _Day(4.0, 0.0, 'unjustified'),   # Sat
            datetime.date(2026, 7, 24): _Day(6.5, 0.0, 'unjustified'),   # Fri
            datetime.date(2026, 7, 23): _Day(6.5, 7.6, 'met')}           # Thu
    dark, observed = ma.dark_working_streak(week, {}, datetime.date(2026, 7, 27))
    assert dark == 3 and observed >= 3


def test_single_day_of_data_never_accuses():
    """Gorata Taele had exactly one workday row and was still flagged 3+ dark."""
    one = {datetime.date(2026, 7, 27): _Day(6.5, 0.0, 'unjustified')}
    dark, observed = ma.dark_working_streak(one, {}, datetime.date(2026, 7, 27))
    assert observed == 1 and observed < ma.MIN_DARK_DAYS


def test_time_doctor_hours_also_end_the_streak():
    """If TD shows time on a day Omni recorded as unjustified, trust the hours."""
    week = dict(ORATILE_WEEK)
    dark, _ = ma.dark_working_streak(week, {datetime.date(2026, 7, 27): 3600},
                                     datetime.date(2026, 7, 27))
    assert dark == 0


def test_quiet_escalation_is_cfo_only():
    assert ma.QUIET_ESCALATION_EMAILS == ['pganesharajah@alphadirect.co.bw']
    assert len(ma.ESCALATION_EMAILS) > len(ma.QUIET_ESCALATION_EMAILS)


if __name__ == '__main__':
    for name, fn in sorted(globals().items()):
        if name.startswith('test_') and callable(fn):
            fn()
            print(f'ok  {name}')
    print('all manager-accountability guard tests passed')
