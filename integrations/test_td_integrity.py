"""
Offline unit tests for integrations/td_integrity.py — the "weight on the
spacebar" fake-activity detector.

Pure Python: no Django, no DB, no network — so it runs on Windows without the
sqlite test-suite trap. Run from the repo root:

    python -m unittest integrations.test_td_integrity -v

The load-bearing tests are test_spacebar_day_is_flagged (must fire) and
test_genuine_broken_day_is_clean (must NOT fire) — together they prove the
detector separates a faked day from a real one.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from integrations.td_integrity import (      # noqa: E402
    analyze_day, flagged, _session_signal, _distinct_windows,
    BLOCK_FLAG_MINUTES,
)


def _wl(user_id, rows):
    """Wrap rows as Time Doctor worklog: a list of per-user arrays."""
    return [[dict(userId=user_id, **r) for r in rows]]


def _one(signals, name):
    return next(s for s in signals if s.name == name)


class SpacebarDetection(unittest.TestCase):

    def test_spacebar_day_is_flagged(self):
        # One 6-hour unbroken block, zero breaks, AND stuck in a single window —
        # the full fingerprint of a weight held on a key all morning.
        users = [{'id': 'U1', 'name': 'Weight Onkey'}]
        worklog = _wl('U1', [{'time': 6 * 3600, 'start': '2026-08-28T06:00:00Z'}])
        timeuse = [[{'time': 6 * 3600, 'score': 4, 'title': 'Outlook'}]]
        sigs = analyze_day(users, worklog, timeuse, ordered_ids=['U1'],
                           productive_hours_by_uid={'U1': 6.0})
        s = _one(sigs, 'Weight Onkey')
        self.assertEqual(s.suspicion, 'suspicious')
        self.assertGreaterEqual(s.longest_block_min, BLOCK_FLAG_MINUTES)
        self.assertEqual(s.idle_breaks, 0)
        self.assertEqual(s.distinct_windows, 1)
        self.assertTrue(s.reasons)
        self.assertIn(s, flagged(sigs))

    def test_long_block_with_many_windows_is_a_busy_worker_not_a_cheat(self):
        # The live 28-Aug prod lesson: a long unbroken block with MANY windows is
        # a genuinely busy worker (dev/accountant), NOT the spacebar cheat.
        users = [{'id': 'U7', 'name': 'Busy Dev'}]
        worklog = _wl('U7', [{'time': 7 * 3600, 'start': '2026-08-28T06:00:00Z'}])
        timeuse = [[{'time': 60, 'score': 4, 'title': f'window-{i}'} for i in range(50)]]
        sigs = analyze_day(users, worklog, timeuse, ordered_ids=['U7'],
                           productive_hours_by_uid={'U7': 7.0})
        s = _one(sigs, 'Busy Dev')
        self.assertEqual(s.distinct_windows, 50)
        self.assertEqual(s.suspicion, 'clean')
        self.assertNotIn(s, flagged(sigs))

    def test_long_block_unknown_variety_is_watch_not_accusation(self):
        # Long unbroken block but we can't read window variety → a human should
        # look ('watch'), never a straight accusation ('suspicious').
        users = [{'id': 'U8', 'name': 'No Windows Feed'}]
        worklog = _wl('U8', [{'time': 6 * 3600, 'start': '2026-08-28T06:00:00Z'}])
        sigs = analyze_day(users, worklog, [], ordered_ids=['U8'],
                           productive_hours_by_uid={'U8': 6.0})
        s = _one(sigs, 'No Windows Feed')
        self.assertIsNone(s.distinct_windows)
        self.assertEqual(s.suspicion, 'watch')

    def test_genuine_broken_day_is_clean(self):
        # A real 6-productive-hour day: many short stretches with real idle gaps
        # between them (phone, coffee, thinking, meetings).
        rows = []
        # 12 x 20-min stretches, each separated by a 15-min idle gap.
        h, m = 6, 0
        for _ in range(12):
            rows.append({'time': 20 * 60, 'start': f'2026-08-28T{h:02d}:{m:02d}:00Z'})
            m += 35                      # 20 worked + 15 idle
            h, m = h + m // 60, m % 60
        users = [{'id': 'U2', 'name': 'Real Worker'}]
        sigs = analyze_day(users, _wl('U2', rows), [], ordered_ids=['U2'],
                           productive_hours_by_uid={'U2': 6.0})
        s = _one(sigs, 'Real Worker')
        self.assertEqual(s.suspicion, 'clean')
        self.assertLess(s.longest_block_min, BLOCK_FLAG_MINUTES)
        self.assertGreaterEqual(s.idle_breaks, 5)
        self.assertNotIn(s, flagged(sigs))

    def test_long_block_below_hours_floor_is_watch_not_accusation(self):
        # A long unbroken block but under the productive-hours floor: worth a
        # look, never an accusation.
        users = [{'id': 'U3', 'name': 'Short Day'}]
        worklog = _wl('U3', [{'time': 3 * 3600 + 30 * 60, 'start': '2026-08-28T06:00:00Z'}])
        sigs = analyze_day(users, worklog, [], ordered_ids=['U3'],
                           productive_hours_by_uid={'U3': 2.0})
        s = _one(sigs, 'Short Day')
        self.assertEqual(s.suspicion, 'watch')

    def test_manual_time_never_makes_a_block(self):
        # Manual entries are typed-in hours, not an observed session — they must
        # never fabricate an unbroken block.
        users = [{'id': 'U4', 'name': 'Manual Entry'}]
        worklog = _wl('U4', [{'time': 8 * 3600, 'start': '2026-08-28T06:00:00Z',
                              'mode': 'manual'}])
        sigs = analyze_day(users, worklog, [], ordered_ids=['U4'],
                           productive_hours_by_uid={'U4': 8.0})
        s = _one(sigs, 'Manual Entry')
        self.assertEqual(s.longest_block_min, 0.0)
        self.assertEqual(s.suspicion, 'clean')

    def test_window_variety_raises_confidence_but_never_accuses_alone(self):
        # A flagged spacebar day WITH a single window all day gets the extra
        # reason; but a broken (genuine) day with a single window is NOT flagged.
        users = [{'id': 'U5', 'name': 'One Window Cheat'}]
        worklog = _wl('U5', [{'time': 6 * 3600, 'start': '2026-08-28T06:00:00Z'}])
        timeuse = [[{'time': 6 * 3600, 'score': 4, 'title': 'Outlook'}]]
        sigs = analyze_day(users, worklog, timeuse, ordered_ids=['U5'],
                           productive_hours_by_uid={'U5': 6.0})
        s = _one(sigs, 'One Window Cheat')
        self.assertEqual(s.suspicion, 'suspicious')
        self.assertEqual(s.distinct_windows, 1)
        self.assertTrue(any('window' in r for r in s.reasons))

    def test_unparseable_timestamps_never_accuse(self):
        # If Time Doctor drifts its `start` format so no timestamp parses, the
        # detector must go SILENT, not flag the whole company (L20 — fail-silent,
        # never fail-accusing).
        users = [{'id': 'U6', 'name': 'Bad Timestamps'}]
        worklog = _wl('U6', [{'time': 6 * 3600, 'start': 'not-a-parseable-date'}])
        sigs = analyze_day(users, worklog, [], ordered_ids=['U6'],
                           productive_hours_by_uid={'U6': 6.0})
        s = _one(sigs, 'Bad Timestamps')
        self.assertEqual(s.longest_block_min, 0.0)
        self.assertEqual(s.suspicion, 'clean')

    def test_empty_day_is_safe(self):
        sigs = analyze_day([], [], [], ordered_ids=[], productive_hours_by_uid={})
        self.assertEqual(sigs, [])


class SessionMath(unittest.TestCase):

    def test_merges_gaps_under_five_minutes(self):
        # Two stretches 3 minutes apart merge into one block (a 3-min pause is
        # not a real break).
        rows = [{'time': 30 * 60, 'start': '2026-08-28T06:00:00Z'},
                {'time': 30 * 60, 'start': '2026-08-28T06:33:00Z'}]  # 3-min gap
        sig = _session_signal(rows)
        self.assertEqual(sig.idle_breaks, 0)
        self.assertAlmostEqual(sig.longest_block_min, 63.0, places=1)

    def test_splits_on_gap_over_five_minutes(self):
        rows = [{'time': 30 * 60, 'start': '2026-08-28T06:00:00Z'},
                {'time': 30 * 60, 'start': '2026-08-28T07:00:00Z'}]  # 30-min gap
        sig = _session_signal(rows)
        self.assertEqual(sig.idle_breaks, 1)
        self.assertAlmostEqual(sig.longest_block_min, 30.0, places=1)


class WindowCounting(unittest.TestCase):

    def test_counts_distinct_titles_without_keeping_them(self):
        rows = [{'title': 'Outlook'}, {'title': 'omni'}, {'title': 'Outlook'},
                {'title': 'Graphite'}]
        self.assertEqual(_distinct_windows(rows), 3)

    def test_returns_none_when_no_title_field(self):
        # timeuse rows with only time+score (the shape proven in our fixtures)
        # must NOT read as "one window" — they read as unknown, so the catcher
        # stays silent.
        rows = [{'time': 3600, 'score': 4}, {'time': 1800, 'score': 3}]
        self.assertIsNone(_distinct_windows(rows))


if __name__ == '__main__':
    unittest.main(verbosity=2)
