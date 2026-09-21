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
    analyze_day, flagged, KEYS_MIN,
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

    def test_reader_or_idle_is_clean(self):
        # Frozen screen but NO typing (reading/thinking) — not the cheat; the
        # existing shortfall flow handles idleness.
        files, users = _day('M5', 'Quiet Reader', 60, keys=0, moves=0, clicks=0)
        s = _one(analyze_day(files, users), 'Quiet Reader')
        self.assertEqual(s.frozen_typing_pct, 0.0)
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
