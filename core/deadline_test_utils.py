"""core/deadline_test_utils.py — test helper for the monthly submission deadline.

The 16th-of-month submission deadline (CFO 2026-08-28, core/payroll_deadline.py)
hard-blocks a non-CFO from submitting commissions/incentives after the 16th. The
older commission/incentive workflow tests submit as a manager/agent and never
froze the calendar, so they turn red on their own on the 17th of any month — a
time-bomb, not a real regression.

Decorate those workflow test classes with @submission_window_open to hold the
window open, so they exercise the workflow they were written for. NEVER decorate
core/tests/test_payroll_deadline.py — that is the test that proves the deadline
itself works, and it controls its own dates.
"""
from unittest import mock


def submission_window_open(cls):
    """Class decorator: keep the monthly submission window open for the duration
    of each test, so the deadline gate never blocks a workflow test."""
    return mock.patch('core.payroll_deadline.window_open',
                      lambda today=None: True)(cls)
