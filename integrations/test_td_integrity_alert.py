"""
Offline tests for integrations/td_integrity_alert.py — the morning-brief
'Time-tracking integrity' alert block. Pure Python: no Django, no DB, no network.

    python -m unittest integrations.test_td_integrity_alert -v

The load-bearing tests: the clean-day confirmation line (so silence never reads
as "not checked") and never-raises (so the check can never break the brief).
"""
import datetime
import os
import sys
import unittest
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from integrations import td_integrity_alert as A      # noqa: E402

DAY = datetime.date(2026, 8, 29)


def _sig(name, susp, pct, hrs):
    return NS(name=name, user_id="x", suspicion=susp,
              frozen_typing_pct=pct, frozen_typing_hours=hrs)


class AlertRender(unittest.TestCase):

    def test_flagged_lists_names_and_verdicts(self):
        flags = [_sig("Meduduetso Tlagae", "suspicious", 0.95, 3.8),
                 _sig("Kago Tshutlhedi", "suspicious", 0.60, 2.2),
                 _sig("Bharath Balasubramanian", "watch", 0.64, 1.1)]
        h = A.render(DAY, flags, checked=71)
        self.assertIn("Meduduetso Tlagae", h)
        self.assertIn("Kago Tshutlhedi", h)
        self.assertIn("3 to review", h)
        self.assertIn("Employment Act", h)

    def test_clean_day_still_confirms_the_check_ran(self):
        # No silent blank — a clean day prints a green "checked N, nothing flagged".
        h = A.render(DAY, [], checked=99)
        self.assertIn("nothing flagged", h)
        self.assertIn("Checked 99", h)

    def test_empty_screenshot_data_is_amber_not_green(self):
        # checked=0 with no flags means the pull returned nothing -> must NOT
        # render the green 'nothing flagged' tick.
        h = A.render(DAY, [], checked=0)
        self.assertIn('could not run', h)
        self.assertNotIn('nothing flagged', h)

    def test_privacy_no_titles_or_hashes_in_output(self):
        flags = [_sig("Someone", "suspicious", 0.9, 3.0)]
        h = A.render(DAY, flags, checked=10)
        for banned in ("imageMd5", "phash", "windowTitle", "title="):
            self.assertNotIn(banned, h)


class AlertNeverBreaksBrief(unittest.TestCase):

    def test_broken_client_returns_amber_not_exception(self):
        class Boom:
            configured = True

            def users(self):
                raise RuntimeError("TD down")

        h = A.morning_integrity_html(DAY, client=Boom())
        self.assertIn("could not run", h)
        self.assertIn("retry", h)

    def test_unconfigured_client_is_skipped_notice(self):
        h = A.morning_integrity_html(DAY, client=NS(configured=False))
        self.assertIn("not configured", h)


if __name__ == "__main__":
    unittest.main(verbosity=2)
