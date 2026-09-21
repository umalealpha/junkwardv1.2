"""Tests for the commission/incentive submission deadline (CFO 2026-08-28).

The 16th is the last day staff may submit/upload; after it only the CFO may.
Dates are passed in explicitly so the rule is tested deterministically, not
against the wall clock.
"""
import datetime as dt
from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings

from core.payroll_deadline import (
    DEADLINE_MESSAGE, SubmissionDeadlinePassed, SUBMISSION_DEADLINE_DAY,
    assert_can_submit, is_cfo, window_open,
)

# is_cfo() now delegates to commissions.access.can_review_stage(FINAL), which
# needs an authenticated user with a get_full_name(). CFO matches on the known
# final email (pganesharajah@); staff match on neither email nor the exact name.
STAFF = SimpleNamespace(is_superuser=False, is_authenticated=True,
                        email="mpho@alphadirect.co.bw",
                        get_full_name=lambda: "Mpho Staff")
CFO = SimpleNamespace(is_superuser=False, is_authenticated=True,
                      email="pganesharajah@alphadirect.co.bw",
                      get_full_name=lambda: "Prathap Ganesharajah")
SUPERUSER = SimpleNamespace(is_superuser=True, is_authenticated=True,
                            email="root@alphadirect.co.bw",
                            get_full_name=lambda: "Root")

ON_16TH = dt.date(2026, 8, 16)
DAY_17 = dt.date(2026, 8, 17)
FIRST = dt.date(2026, 8, 1)
MONTH_END = dt.date(2026, 8, 31)


class WindowTests(SimpleTestCase):
    def test_window_open_on_and_before_the_16th(self):
        self.assertTrue(window_open(FIRST))
        self.assertTrue(window_open(ON_16TH))

    def test_window_closed_from_the_17th(self):
        self.assertFalse(window_open(DAY_17))
        self.assertFalse(window_open(MONTH_END))

    def test_deadline_day_is_the_16th(self):
        self.assertEqual(SUBMISSION_DEADLINE_DAY, 16)


@override_settings(SUBMISSION_DEADLINE_ENFORCED=True)
class GateTests(SimpleTestCase):
    def test_staff_can_submit_up_to_the_16th(self):
        assert_can_submit(STAFF, today=ON_16TH)   # must not raise

    def test_staff_blocked_after_the_16th(self):
        with self.assertRaises(SubmissionDeadlinePassed) as ctx:
            assert_can_submit(STAFF, today=DAY_17)
        self.assertEqual(ctx.exception.message, DEADLINE_MESSAGE)
        self.assertIn("Ask CFO", DEADLINE_MESSAGE)

    def test_cfo_override_after_the_16th(self):
        assert_can_submit(CFO, today=DAY_17)        # by email — must not raise
        assert_can_submit(SUPERUSER, today=DAY_17)  # superuser — must not raise

    def test_is_cfo(self):
        self.assertTrue(is_cfo(CFO))
        self.assertTrue(is_cfo(SUPERUSER))
        self.assertFalse(is_cfo(STAFF))


@override_settings(SUBMISSION_DEADLINE_ENFORCED=False)
class SwitchedOffTests(SimpleTestCase):
    """CFO 19-Sep-2026: the lock is switched off so staff load their own."""

    def test_staff_can_submit_after_the_16th_when_switched_off(self):
        assert_can_submit(STAFF, today=DAY_17)      # must not raise
        assert_can_submit(STAFF, today=MONTH_END)   # must not raise

    def test_switch_is_off_by_default(self):
        from alpha_finance import settings as project_settings
        self.assertFalse(project_settings.SUBMISSION_DEADLINE_ENFORCED)
