"""Tests for the reusable people-data guardrail (CFO 2026-08-01).

Pure-logic tests (no DB): the settle gate and orchestrator must (1) HOLD a zero
that is really late/stuck data, (2) let a genuine chronic no-show through, and
(3) fail SAFE — hold, never accuse — when the AI check cannot run. The Wangu
Moses incident (zero at 03:00/04:00 because his PC was off, strong recent
history, hours arrived later) is encoded verbatim as the headline case.
"""
import datetime

from django.test import SimpleTestCase

from hris import people_data_guard as pdg

DAY = datetime.date(2026, 7, 31)
H = 3600


class SettleGateTests(SimpleTestCase):
    def test_one_pull_is_not_settled(self):
        self.assertFalse(pdg.is_settled({'0300': {'u': 100}}, 'u'))

    def test_steady_within_tolerance_is_settled(self):
        self.assertTrue(pdg.is_settled({'0300': {'u': 100}, '0400': {'u': 130}}, 'u'))

    def test_moving_figure_is_not_settled(self):
        self.assertFalse(pdg.is_settled({'0400': {'u': 0}, '0830': {'u': 5 * H}}, 'u'))

    def test_tracked_ratio_none_when_history_thin(self):
        self.assertIsNone(pdg.tracked_ratio({'d1': 10}))

    def test_wangu_zero_with_strong_history_is_held(self):
        # His real case: 0 at 03:00 & 04:00 (machine off), tracked most recent days.
        samples = {'0300': {'w': 0}, '0400': {'w': 0}}
        history = {'24': 7 * H, '25': 6 * H, '27': 7 * H, '28': 6 * H}
        ok, reason = pdg.settle_gate('w', samples, history, low_threshold_sec=0)
        self.assertFalse(ok)
        self.assertIn('late', reason.lower())

    def test_chronic_zero_passes_settle_gate(self):
        samples = {'0300': {'s': 0}, '0400': {'s': 0}}
        history = {'a': 0, 'b': 0, 'c': 0, 'd': 0}
        ok, _ = pdg.settle_gate('s', samples, history, low_threshold_sec=0)
        self.assertTrue(ok)

    def test_new_joiner_zero_not_treated_suspicious(self):
        samples = {'0300': {'n': 0}, '0400': {'n': 0}}
        ok, _ = pdg.settle_gate('n', samples, {'d1': 0}, low_threshold_sec=0)
        self.assertTrue(ok)  # too little history to call it suspicious

    def test_single_pull_fallback_still_holds_suspicious_zero(self):
        # No settle samples captured yet, but base report says zero + strong
        # history -> still HELD (moving-check needs two pulls; this doesn't).
        history = {'24': 7 * H, '25': 6 * H, '27': 7 * H, '28': 6 * H}
        ok, _ = pdg.settle_gate('w', {}, history, low_threshold_sec=0, is_zero=True)
        self.assertFalse(ok)

    def test_single_pull_fallback_lets_chronic_zero_through(self):
        history = {'a': 0, 'b': 0, 'c': 0, 'd': 0}
        ok, _ = pdg.settle_gate('s', {}, history, low_threshold_sec=0, is_zero=True)
        self.assertTrue(ok)

    def test_two_early_pulls_at_zero_not_yet_settled(self):
        # 03:00 & 04:00 both zero (PCs off) is NOT settled — must still HOLD a
        # strong-history zero (Wangu at 3/4am).
        history = {'24': 7 * H, '25': 6 * H, '27': 7 * H, '28': 6 * H}
        two = {'0300': {'w': 0}, '0400': {'w': 0}}
        ok, _ = pdg.settle_gate('w', two, history, low_threshold_sec=0, is_zero=True)
        self.assertFalse(ok)

    def test_all_three_pulls_zero_is_settled_and_passes_to_ai(self):
        # All three pulls in AND agree at zero (incl the 08:30 post-boot pull) ->
        # fully settled -> not auto-held on history; handed to the AI gate.
        history = {'24': 7 * H, '25': 6 * H, '27': 7 * H, '28': 6 * H}
        three = {'0300': {'w': 0}, '0400': {'w': 0}, '0830': {'w': 0}}
        ok, _ = pdg.settle_gate('w', three, history, low_threshold_sec=0, is_zero=True)
        self.assertTrue(ok)


class OrchestratorTests(SimpleTestCase):
    def test_wangu_held_even_with_ai_off(self):
        cands = [{'uid': 'w', 'name': 'Wangu'}]
        samples = {'0300': {'w': 0}, '0400': {'w': 0}}
        hist = {'w': {'24': 7 * H, '25': 6 * H, '27': 7 * H, '28': 6 * H}}
        dec = pdg.guard_candidates(cands, samples, hist, DAY, use_ai=False)
        self.assertEqual(dec['w']['action'], 'hold')

    def test_genuine_reported_when_ai_skipped(self):
        cands = [{'uid': 's', 'name': 'Slacker'}]
        samples = {'0300': {'s': 0}, '0400': {'s': 0}}
        hist = {'s': {'a': 0, 'b': 0, 'c': 0, 'd': 0}}
        dec = pdg.guard_candidates(cands, samples, hist, DAY, use_ai=False)
        self.assertEqual(dec['s']['action'], 'report')

    def test_slots_ordered_chronologically_not_dict_order(self):
        # jsonb doesn't preserve insertion order; zero-padded HHMM sorts right.
        unordered = {'0830': {'u': 100}, '0300': {'u': 0}, '0400': {'u': 0}}
        self.assertEqual(pdg._ordered_slots(unordered), ['0300', '0400', '0830'])
        self.assertFalse(pdg.is_settled(unordered, 'u'))   # last two (0400,0830): 0 vs 100

    def test_duplicate_names_both_held_never_cross_attributed(self):
        # Two people share a full name -> an AI reply keyed on name cannot be
        # attributed, so BOTH are held rather than risk clearing a late-data twin.
        cands = [{'uid': 'a', 'name': 'John Doe', 'is_zero': True},
                 {'uid': 'b', 'name': 'John Doe', 'is_zero': True}]
        samples = {'0300': {'a': 0, 'b': 0}, '0400': {'a': 0, 'b': 0}}
        hist = {'a': {'x': 0, 'y': 0, 'z': 0}, 'b': {'x': 0, 'y': 0, 'z': 0}}
        dec = pdg.guard_candidates(cands, samples, hist, DAY, use_ai=True)
        self.assertEqual(dec['a']['action'], 'hold')
        self.assertEqual(dec['b']['action'], 'hold')
        self.assertIn('duplicate name', dec['a']['reason'])

    def test_combine_ai_holds_only_on_positive_late_flag(self):
        # Both engines failed to run -> HOLD (fail-safe).
        self.assertFalse(pdg._combine_ai(None, None, True)[0])
        # An engine positively flags late data -> HOLD.
        self.assertFalse(pdg._combine_ai('likely_late', 'genuine', False)[0])
        self.assertFalse(pdg._combine_ai('genuine', 'likely_late', False)[0])
        # Genuine -> REPORT.
        self.assertTrue(pdg._combine_ai('genuine', 'genuine', False)[0])
        # Uncertain -> REPORT (the fix: uncertainty no longer over-holds).
        self.assertTrue(pdg._combine_ai('uncertain', 'uncertain', False)[0])
        # Engines ran but didn't mention this person (chronic non-tracker like
        # Oratile) -> REPORT, not a silent hold.
        self.assertTrue(pdg._combine_ai(None, None, False)[0])

    def test_ai_unavailable_fails_safe_to_hold(self):
        # use_ai=True but ai_cross_check monkeypatched to the unavailable path.
        cands = [{'uid': 's', 'name': 'Slacker'}]
        samples = {'0300': {'s': 0}, '0400': {'s': 0}}
        hist = {'s': {'a': 0, 'b': 0, 'c': 0, 'd': 0}}
        orig = pdg.ai_cross_check
        try:
            pdg.ai_cross_check = lambda *a, **k: {
                c['uid']: (False, 'AI cross-check unavailable — name held') for c in a[0]}
            dec = pdg.guard_candidates(cands, samples, hist, DAY, use_ai=True)
        finally:
            pdg.ai_cross_check = orig
        self.assertEqual(dec['s']['action'], 'hold')


class HeldUidsWrapperTests(SimpleTestCase):
    """The shared wrapper every OTHER communication uses (CFO 2026-08-01: the
    guardrail applies to all of them, not just the report and the morning brief).
    Patched at the sample/history read so no Time Doctor or DB is needed."""

    # Only the two early pulls are in — the 08:30 post-boot-up pull has not run,
    # so a strong-history zero is exactly the Wangu case and must be held.
    SAMPLES = {'0300': {'late': 0, 'chronic': 0},
               '0400': {'late': 0, 'chronic': 0}}
    HIST = {'late': {'2026-07-27': 7 * H, '2026-07-28': 6 * H, '2026-07-29': 7 * H},
            'chronic': {'2026-07-27': 0, '2026-07-28': 0, '2026-07-29': 0}}

    def _run(self, **kw):
        from unittest.mock import patch
        from hris import exceptions_report
        with patch.object(exceptions_report, '_guard_history_and_samples',
                          return_value=(self.SAMPLES, self.HIST)):
            return pdg.held_uids([('late', 'Late Person'), ('chronic', 'Chronic Person')],
                                 DAY, client=object(), **kw)

    def test_late_person_is_held_and_chronic_is_not(self):
        from django.test import override_settings
        # AI off: the settle gate alone must hold the late uploader and release
        # the settled chronic zero.
        with override_settings(WORKFORCE_DATA_GUARD_AI=False):
            held = self._run()
        self.assertIn('late', held)
        self.assertNotIn('chronic', held)

    def test_empty_pairs_short_circuits(self):
        self.assertEqual(pdg.held_uids([], DAY, client=object()), set())

    def test_guard_can_be_switched_off(self):
        from django.test import override_settings
        with override_settings(WORKFORCE_DATA_GUARD_ENABLED=False):
            self.assertEqual(self._run(), set())


class FailClosedTests(SimpleTestCase):
    """A broken guardrail must HOLD, never wave names through (CFO: hold the
    name). Covers the two ways the inputs can die: the sample/history read and
    the gates themselves."""

    PAIRS = [('u1', 'Person One'), ('u2', 'Person Two')]

    def test_sample_read_failure_holds_everyone(self):
        from unittest.mock import patch
        from hris import exceptions_report
        with patch.object(exceptions_report, '_guard_history_and_samples',
                          side_effect=RuntimeError('time doctor down')):
            held = pdg.held_uids(self.PAIRS, DAY, client=object())
        self.assertEqual(held, {'u1', 'u2'})

    def test_gate_failure_holds_everyone(self):
        from unittest.mock import patch
        from hris import exceptions_report
        with patch.object(exceptions_report, '_guard_history_and_samples', return_value=({}, {})), \
             patch.object(pdg, 'guard_candidates', side_effect=RuntimeError('gate blew up')):
            held = pdg.held_uids(self.PAIRS, DAY, client=object())
        self.assertEqual(held, {'u1', 'u2'})
