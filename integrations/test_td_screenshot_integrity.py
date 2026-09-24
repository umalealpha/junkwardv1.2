"""
Offline unit tests for integrations/td_screenshot_integrity.py — the
"phantom keystroke" / frozen-screen cheat detector.

Pure Python: no Django, no DB, no network. Run from the repo root:

    python -m unittest integrations.test_td_screenshot_integrity -v

Load-bearing pair: test_weight_on_key_is_caught (must fire) and
test_genuine_data_entry_is_clean (must NOT fire — a clerk types hard but the
screen changes as cells fill). Plus the jiggler-adaptation test.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from integrations.td_screenshot_integrity import (      # noqa: E402
    analyze_day, flagged, KEYS_MIN, IDLE_DAY_PCT, IDLE_HOURS_MIN,
)


def _day(uid, name, n, *, keys, moves, clicks, md5='same', vary_md5=False,
         hamming=None, start_h=8):
    """Build a one-user /api/1.0/files day of n screenshots, 3 minutes apart."""
    numbers = []
    for i in range(n):
        h, m = start_h + (i * 3) // 60, (i * 3) % 60
        meta = {
            'keys': keys, 'movements': moves, 'clicks': clicks,
            'imageMd5': f'md5-{i}' if vary_md5 else md5,
            'createdAt': f'2026-08-29T{h:02d}:{m:02d}:00Z',
            'screenNumber': 0,
        }
        if hamming is not None:
            meta['hammingDistance'] = [hamming]
        numbers.append({'meta': meta})
    files = [{'userId': uid, 'date': numbers[0]['meta']['createdAt'], 'numbers': numbers}]
    users = [{'id': uid, 'name': name}]
    return files, users


def _one(sigs, name):
    return next(s for s in sigs if s.name == name)


class PhantomDetection(unittest.TestCase):

    def test_weight_on_key_is_caught(self):
        # 60 shots, ~2.95h: heavy typing, no mouse, screen never changes.
        files, users = _day('M1', 'Weight Onkey', 60, keys=60, moves=0, clicks=0)
        sigs = analyze_day(files, users, productive_hours_by_uid={'M1': 8.0})
        s = _one(sigs, 'Weight Onkey')
        self.assertEqual(s.suspicion, 'suspicious')
        self.assertGreaterEqual(s.frozen_typing_pct, 0.25)
        self.assertGreaterEqual(s.frozen_typing_hours, 2.0)
        self.assertGreaterEqual(s.mouse_dead_pct, 0.99)
        self.assertIn(s, flagged(sigs))

    def test_mouse_jiggler_still_caught(self):
        # Fable's predicted next move: a jiggler makes movements>0, but real
        # typing STILL can't leave the screen unchanged — anchor holds.
        files, users = _day('M2', 'Jiggler Joe', 60, keys=60, moves=8, clicks=0)
        sigs = analyze_day(files, users)
        s = _one(sigs, 'Jiggler Joe')
        self.assertEqual(s.suspicion, 'suspicious')
        self.assertLess(s.mouse_dead_pct, 0.5)          # mouse WAS moving
        self.assertTrue(any('mouse barely moving' in r for r in s.reasons))

    def test_frozen_screen_by_phash_when_md5_differs(self):
        # Screen "identical" via phash hammingDistance<=4 even if md5 differs.
        files, users = _day('M3', 'Phash Frozen', 60, keys=55, moves=0, clicks=0,
                            vary_md5=True, hamming=2)
        s = _one(analyze_day(files, users), 'Phash Frozen')
        self.assertEqual(s.suspicion, 'suspicious')

    def test_genuine_data_entry_is_clean(self):
        # A clerk types just as hard, but the screen CHANGES as cells fill
        # (md5 differs every shot) → never frozen → not flagged.
        files, users = _day('M4', 'Real Clerk', 60, keys=70, moves=0, clicks=0,
                            vary_md5=True)
        s = _one(analyze_day(files, users), 'Real Clerk')
        self.assertEqual(s.frozen_typing_pct, 0.0)
        self.assertEqual(s.suspicion, 'clean')

    def test_idle_frozen_day_is_caught(self):
        # THE TWIN CHEAT (CFO 2026-09-21, case ref TD-IDLE-2026-09-21). 60 shots =
        # 3 hours, every one with no keys, no mouse, no clicks, on a screen that
        # never changes — while Time Doctor credits the whole block as worked.
        # This case used to return 'clean': the typing rule's anchor is
        # keys >= KEYS_MIN, so a person supplying NO input was invisible to it,
        # and the shortfall flow never fires because the hours are full.
        # REVERSES the old test_reader_or_idle_is_clean expectation deliberately.
        files, users = _day('M5', 'Idle Sitter', 60, keys=0, moves=0, clicks=0)
        s = _one(analyze_day(files, users), 'Idle Sitter')
        self.assertEqual(s.frozen_typing_pct, 0.0)      # the typing rule stays silent
        self.assertGreaterEqual(s.idle_frozen_pct, IDLE_DAY_PCT)
        self.assertGreaterEqual(s.idle_frozen_hours, IDLE_HOURS_MIN)
        self.assertEqual(s.suspicion, 'suspicious')
        self.assertTrue(any('NO typing' in r for r in s.reasons))

    def test_long_meeting_inside_a_busy_day_is_clean(self):
        # THE honest-staff guard the percentage gate exists for (Fable 5.1): a two
        # hour dead stretch — a meeting, a call, lunch at the desk — sitting inside
        # an otherwise busy day. 40 dead-and-frozen shots among 160 is 25% of the
        # day, under the 60% gate, so it never becomes an accusation even though it
        # clears the two-hour materiality floor on its own.
        busy, users = _day('M8', 'Busy Person', 120, keys=30, moves=40, clicks=5,
                           vary_md5=True, start_h=8)
        dead, _ = _day('M8', 'Busy Person', 40, keys=0, moves=0, clicks=0,
                       start_h=14)
        busy[0]['numbers'].extend(dead[0]['numbers'])
        s = _one(analyze_day(busy, users), 'Busy Person')
        self.assertLess(s.idle_frozen_pct, IDLE_DAY_PCT)
        self.assertEqual(s.suspicion, 'clean')

    def test_idle_rule_never_downgrades_the_typing_rule(self):
        # The code comment promises the idle rule "never downgrades an existing
        # verdict" — pin it. A day carrying BOTH cheats must stay suspicious and
        # carry BOTH reasons, not have one quietly replace the other.
        # 45 frozen-typing shots = 2.25h (clears the typing rule on its own) and
        # 90 dead shots = 4.5h and 67% of the day (clears the idle rule too).
        typing, users = _day('M9', 'Both Cheats', 45, keys=KEYS_MIN + 20,
                             moves=0, clicks=0, start_h=8)
        idle, _ = _day('M9', 'Both Cheats', 90, keys=0, moves=0, clicks=0,
                       start_h=11)
        typing[0]['numbers'].extend(idle[0]['numbers'])
        s = _one(analyze_day(typing, users), 'Both Cheats')
        self.assertEqual(s.suspicion, 'suspicious')
        self.assertTrue(any('never changes' in r and 'keys' in r for r in s.reasons),
                        f'typing reason lost: {s.reasons}')
        self.assertTrue(any('NO typing' in r for r in s.reasons),
                        f'idle reason lost: {s.reasons}')

    def test_short_idle_stretch_is_clean(self):
        # Materiality still holds: 20 shots = 1 hour of frozen idle is a long
        # meeting or a document read, never an accusation.
        files, users = _day('M5b', 'Brief Pause', 20, keys=0, moves=0, clicks=0)
        s = _one(analyze_day(files, users), 'Brief Pause')
        self.assertLess(s.idle_frozen_hours, IDLE_HOURS_MIN)
        self.assertNotEqual(s.suspicion, 'suspicious')

    def test_reader_who_scrolls_is_clean(self):
        # The honest reader: screen frozen between captures but the mouse IS
        # moving (scrolling, following a line). Not dead, so never flagged.
        files, users = _day('M5c', 'Real Reader', 60, keys=0, moves=12, clicks=0)
        s = _one(analyze_day(files, users), 'Real Reader')
        self.assertEqual(s.idle_frozen_pct, 0.0)
        self.assertEqual(s.suspicion, 'clean')

    def test_meeting_screen_changes_is_clean(self):
        # A call or a video: no keyboard, no mouse, but the picture CHANGES every
        # capture — never frozen, so the idle rule cannot touch it.
        files, users = _day('M5d', 'On A Call', 60, keys=0, moves=0, clicks=0,
                            vary_md5=True)
        s = _one(analyze_day(files, users), 'On A Call')
        self.assertEqual(s.idle_frozen_pct, 0.0)
        self.assertEqual(s.suspicion, 'clean')

    def test_below_materiality_floor_is_not_flagged(self):
        # A short frozen-typing patch (3 shots ≈ 9 min) is never an accusation.
        files, users = _day('M6', 'Brief Blip', 3, keys=60, moves=0, clicks=0)
        s = _one(analyze_day(files, users), 'Brief Blip')
        self.assertLess(s.frozen_typing_hours, 2.0)
        self.assertNotEqual(s.suspicion, 'suspicious')

    def test_privacy_output_has_no_titles_or_hashes(self):
        files, users = _day('M7', 'Any One', 30, keys=60, moves=0, clicks=0)
        d = _one(analyze_day(files, users), 'Any One').as_dict()
        # Only counts/percentages leave — never md5, phash or window titles.
        for banned in ('md5', 'imageMd5', 'phash', 'windowTitle', 'title', 'url'):
            self.assertNotIn(banned, d)

    def test_multi_monitor_typing_on_second_screen_is_clean(self):
        # Two monitors captured at the same instants: monitor 0 frozen, monitor 1
        # CHANGING (real work there). Interval-global keys are high, but the
        # interval is NOT all-frozen, so this genuine worker is NOT flagged.
        numbers = []
        for i in range(60):
            h, m = 8 + (i * 3) // 60, (i * 3) % 60
            ts = f'2026-08-29T{h:02d}:{m:02d}:00Z'
            numbers.append({'meta': {'keys': 60, 'movements': 0, 'clicks': 0,
                                     'imageMd5': 'frozen', 'createdAt': ts, 'screenNumber': 0}})
            numbers.append({'meta': {'keys': 60, 'movements': 0, 'clicks': 0,
                                     'imageMd5': f'changing-{i}', 'createdAt': ts, 'screenNumber': 1}})
        files = [{'userId': 'MM1', 'date': numbers[0]['meta']['createdAt'], 'numbers': numbers}]
        s = _one(analyze_day(files, [{'id': 'MM1', 'name': 'Two Monitors'}]), 'Two Monitors')
        self.assertEqual(s.frozen_typing_pct, 0.0)
        self.assertEqual(s.suspicion, 'clean')

    def test_multi_monitor_both_frozen_is_caught(self):
        # Both monitors frozen while "typing" — the cheat, still caught.
        numbers = []
        for i in range(60):
            h, m = 8 + (i * 3) // 60, (i * 3) % 60
            ts = f'2026-08-29T{h:02d}:{m:02d}:00Z'
            for scr in (0, 1):
                numbers.append({'meta': {'keys': 60, 'movements': 0, 'clicks': 0,
                                         'imageMd5': f'frozen-{scr}', 'createdAt': ts, 'screenNumber': scr}})
        files = [{'userId': 'MM2', 'date': numbers[0]['meta']['createdAt'], 'numbers': numbers}]
        s = _one(analyze_day(files, [{'id': 'MM2', 'name': 'Both Frozen'}]), 'Both Frozen')
        self.assertEqual(s.suspicion, 'suspicious')

    def test_empty_is_safe(self):
        self.assertEqual(analyze_day([], []), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
