"""Working-day arithmetic for claim clocks (B4/B5).

Pure functions that take an explicit holiday set, so they can be tested without
a database or clock. Botswana Monday-Saturday are working days; Sundays and
public holidays are not.
"""
import datetime as dt


def is_working_day(day, holidays):
    return day.weekday() != 6 and day not in holidays


def count_working_days(start, end, holidays):
    if end <= start:
        return 0
    count = 0
    for offset in range(1, (end - start).days + 1):
        if is_working_day(start + dt.timedelta(days=offset), holidays):
            count += 1
    return count


def add_working_days(start, days, holidays):
    if days == 0:
        return start
    current = start
    remaining = days
    while remaining > 0:
        current += dt.timedelta(days=1)
        if is_working_day(current, holidays):
            remaining -= 1
    return current