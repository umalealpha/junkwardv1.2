"""Working-day arithmetic for the claim clocks (B4/B5).

Pure: takes an explicit holiday set, never reads the database or the clock, so
the rule can be exercised without a live claim. The DB wrapper that reads
hris.PublicHoliday sits on top of this and is swappable.

House rule: Botswana works Monday to Saturday (core.team_glance_views._is_workday
excludes Sundays only), so a "working day" here is any day that is not a Sunday
and not an active BW public holiday.
"""
import datetime as dt
import sys
import unittest

from claims_lifecycle.workdays import add_working_days, count_working_days, is_working_day

MON = dt.date(2026, 9, 21)   # Monday
SAT = dt.date(2026, 9, 26)   # Saturday
SUN = dt.date(2026, 9, 27)   # Sunday
HOL = {dt.date(2026, 9, 23)}  # Wednesday, a public holiday

CHECKS = []


def check(name, got, want):
    CHECKS.append((name, got == want, got, want))


check('monday is a working day', is_working_day(MON, set()), True)
check('saturday is a working day', is_working_day(SAT, set()), True)
check('sunday is not a working day', is_working_day(SUN, set()), False)
check('a public holiday is not a working day', is_working_day(dt.date(2026, 9, 23), HOL), False)

# count_working_days is INCLUSIVE of neither end-point subtlety we can guess at,
# so it is defined here: days elapsed FROM start TO end, counting end, excluding
# start. Mon -> Tue is 1 working day.
check('mon to tue is 1', count_working_days(MON, dt.date(2026, 9, 22), set()), 1)
check('mon to sat is 5', count_working_days(MON, SAT, set()), 5)
check('sunday does not count', count_working_days(MON, SUN, set()), 5)
check('a holiday in the middle does not count',
      count_working_days(MON, dt.date(2026, 9, 24), HOL), 2)
check('same day is 0', count_working_days(MON, MON, set()), 0)
check('an end before the start is 0', count_working_days(SAT, MON, set()), 0)

check('add 1 working day to monday', add_working_days(MON, 1, set()), dt.date(2026, 9, 22))
check('add 5 skips sunday', add_working_days(MON, 5, set()), SAT)
check('add 6 skips sunday', add_working_days(MON, 6, set()), dt.date(2026, 9, 28))
check('add skips a holiday', add_working_days(MON, 2, HOL), dt.date(2026, 9, 24))
check('add 0 returns the start', add_working_days(MON, 0, set()), MON)

# Starting ON a non-working day must not silently swallow a day.
check('adding 1 from a sunday lands on monday',
      add_working_days(SUN, 1, set()), dt.date(2026, 9, 28))

def run():
    """Print every check and exit non-zero on the first failure.

    Guarded, because Django's test runner IMPORTS every test module it finds in
    an installed app: a sys.exit at import time reads as "Failed to import test
    module" and turns the whole CI shard red.
    """
    failed = [c for c in CHECKS if not c[1]]
    for name, ok, got, want in CHECKS:
        print(('ok   ' if ok else 'FAIL ') + name
              + ('' if ok else f'  got={got!r} want={want!r}'))
    print(f'{len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed')
    return 1 if failed else 0


class PureChecks(unittest.TestCase):
    """So the Django runner exercises these too, instead of skipping them."""

    def test_every_check_passes(self):
        for name, ok, got, want in CHECKS:
            with self.subTest(check=name):
                self.assertTrue(ok, f'{name}: got={got!r} want={want!r}')


if __name__ == '__main__':
    sys.exit(run())
