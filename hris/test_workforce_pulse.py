"""
hris/test_workforce_pulse.py — the team-pulse maths (CFO 2026-07-16).
Pure functions, no DB: momentum, team leaderboard, focus stats.
"""
from unittest import mock

from django.test import SimpleTestCase

from hris import workforce_pulse as wp
from hris.management.commands.send_exceptions_report import _subject


class MomentumTests(SimpleTestCase):
    def test_pct_and_most_improved(self):
        this = {'a': 7200, 'b': 3600}          # 2h, 1h
        prev = {'a': 3600, 'b': 3600}          # 1h, 1h
        names = {'a': 'Alice', 'b': 'Bob'}
        mo = wp.momentum(this, prev, names)
        self.assertEqual(mo['this_h'], 3.0)
        self.assertEqual(mo['prev_h'], 2.0)
        self.assertEqual(mo['pct'], 50.0)
        self.assertTrue(mo['up'])
        # only positive movers, biggest first; Bob (0 delta) excluded
        self.assertEqual(mo['most_improved'], [('Alice', 1.0)])

    def test_no_baseline(self):
        mo = wp.momentum({'a': 3600}, {}, {'a': 'Alice'})
        self.assertIsNone(mo['pct'])
        self.assertTrue(mo['up'])          # no baseline is treated as neutral/up


class LeaderboardTests(SimpleTestCase):
    def test_ranks_by_avg_productive_and_hides_small_teams(self):
        matched = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h']
        secs = {'a': 7200, 'b': 3600, 'c': 3600,          # Finance (3)
                'd': 7200,                                  # Solo (1) -> hidden
                'e': 3600,                                  # blank dept -> hidden
                'f': 10800, 'g': 7200, 'h': 7200}          # Claims (3)
        prod = {'a': 2.0, 'b': 1.0, 'c': 1.0,
                'd': 2.0, 'e': 1.0,
                'f': 3.0, 'g': 2.0, 'h': 1.0}
        dept = {'a': 'Finance', 'b': 'Finance', 'c': 'Finance',
                'd': 'Solo', 'e': '',
                'f': 'Claims', 'g': 'Claims', 'h': 'Claims'}
        rows, hidden = wp.team_leaderboard(matched, secs, prod, dept, min_size=3)
        self.assertEqual([r['dept'] for r in rows], ['Claims', 'Finance'])
        self.assertEqual(rows[0]['avg_prod_h'], 2.0)
        self.assertEqual(rows[1]['avg_prod_h'], 1.33)
        self.assertEqual(rows[0]['heads'], 3)
        self.assertEqual(hidden, 2)        # Solo(1) + blank(1)

    def test_always_show_pins_small_team_at_bottom_without_medal(self):
        # Finance(3) ranks normally; C-Suite(2) is below min_size but pinned in.
        matched = ['a', 'b', 'c', 'x', 'y']
        secs = {'a': 7200, 'b': 3600, 'c': 3600, 'x': 10800, 'y': 10800}
        prod = {'a': 2.0, 'b': 1.0, 'c': 1.0, 'x': 3.0, 'y': 3.0}
        dept = {'a': 'Finance', 'b': 'Finance', 'c': 'Finance',
                'x': 'C-Suite', 'y': 'C-Suite'}
        rows, hidden = wp.team_leaderboard(
            matched, secs, prod, dept, min_size=3, always_show=['C-Suite'])
        # C-Suite would out-score Finance on avg, but pinned rows sit LAST and
        # never steal the ranking; its 2 heads are not counted as hidden.
        self.assertEqual([r['dept'] for r in rows], ['Finance', 'C-Suite'])
        self.assertTrue(rows[-1].get('pinned'))
        self.assertFalse(rows[0].get('pinned'))
        self.assertEqual(hidden, 0)

    def test_always_show_absent_when_no_trackers(self):
        # C-Suite pinned only if at least one person actually tracked.
        rows, hidden = wp.team_leaderboard(
            ['a', 'b', 'c'], {'a': 7200, 'b': 3600, 'c': 3600},
            {'a': 2.0, 'b': 1.0, 'c': 1.0},
            {'a': 'Finance', 'b': 'Finance', 'c': 'Finance'},
            min_size=3, always_show=['C-Suite'])
        self.assertEqual([r['dept'] for r in rows], ['Finance'])

    def test_zero_hours_person_not_counted(self):
        rows, hidden = wp.team_leaderboard(
            ['a', 'b', 'c'], {'a': 3600, 'b': 0, 'c': 3600},
            {'a': 1.0, 'b': 0, 'c': 1.0}, {'a': 'X', 'b': 'X', 'c': 'X'}, min_size=3)
        # only 2 actually tracked -> below min_size -> nothing ranked
        self.assertEqual(rows, [])
        self.assertEqual(hidden, 2)


class FocusTests(SimpleTestCase):
    """focus_stats now runs off TIMEUSE and counts PRODUCTIVE rows only
    (CFO 2026-07-30 — a 10h 06m 'unbroken' block went out to managers)."""

    def test_longest_productive_block_merges_small_gaps_and_peak_hour(self):
        timeuse = [[                                      # bucket 0 -> uid 'a'
            {'start': '2026-07-14T08:00:00', 'time': 3600, 'score': 4},   # -> 09:00
            {'start': '2026-07-14T09:02:00', 'time': 1800, 'score': 3},   # gap 120s -> merge -> 09:32
            {'start': '2026-07-14T11:00:00', 'time': 3600, 'score': 3},   # new block
        ]]
        out = wp.focus_stats(timeuse, {'a': 'Alice'}, ['a'], ['a'], gap_secs=300)
        # 3600 + 1800 productive seconds in the merged run. NOT the 5520s span —
        # the 120s break inside it is not productive time (see the sum-not-span test).
        self.assertEqual(out['top_focus'], [('Alice', 5400)])
        # local = UTC+2: 08:00->10h(3600), 09:02->11h(1800), 11:00->13h(3600)
        self.assertEqual(out['best_hour'], 10)
        self.assertEqual(out['best_hour_label'], '10am–11am')

    def test_unproductive_and_neutral_time_never_counts(self):
        """The bug: idle/unproductive time padded the block to 10h. Only scores
        3-4 are productive; 1-2 unproductive and 0 unrated must be dropped."""
        timeuse = [[
            {'start': '2026-07-14T08:00:00', 'time': 1800, 'score': 3},    # 30m productive
            {'start': '2026-07-14T08:30:00', 'time': 7200, 'score': 1},    # 2h unproductive
            {'start': '2026-07-14T10:30:00', 'time': 3600, 'score': 0},    # 1h unrated
        ]]
        out = wp.focus_stats(timeuse, {'a': 'Alice'}, ['a'], ['a'])
        self.assertEqual(out['top_focus'], [('Alice', 1800)])

    def test_two_machines_are_not_merged_into_a_longer_block(self):
        """Two machines running the same afternoon carry no overlap information —
        the person keeps their BEST machine, never the sum (CFO 2026-07-29). 'a2'
        is Alice's second machine, folded onto 'a' by the alias map."""
        timeuse = [
            [{'start': '2026-07-14T08:00:00', 'time': 3600, 'score': 3}],   # machine 1: 1h
            [{'start': '2026-07-14T09:00:00', 'time': 5400, 'score': 3}],   # machine 2: 1h30
        ]
        with mock.patch('integrations.td_matching.fold_uid',
                        side_effect=lambda u: 'a' if u in ('a', 'a2') else u):
            out = wp.focus_stats(timeuse, {'a': 'Alice'}, ['a'], ['a', 'a2'])
        # NOT 08:00->10:30 (2h30) merged across machines — the best machine stands.
        self.assertEqual(out['top_focus'], [('Alice', 5400)])

    def test_unmatched_person_ignored(self):
        timeuse = [[{'start': '2026-07-14T08:00:00', 'time': 3600, 'score': 3}]]
        out = wp.focus_stats(timeuse, {'a': 'Alice'}, ['a'], ['z'])
        self.assertEqual(out['top_focus'], [])

    def test_block_is_summed_productive_time_not_wall_clock_span(self):
        """Live 29-Jul data: measuring the SPAN let the <=5min breaks between
        productive rows count, so Leungo's block (7.39h) came out ABOVE his own
        printed productive hours (7.10h). The block is the SUM of productive
        seconds in the stretch."""
        timeuse = [[
            {'start': '2026-07-14T08:00:00', 'time': 3600, 'score': 3},   # 1h
            {'start': '2026-07-14T09:04:00', 'time': 3600, 'score': 3},   # 4min gap -> same run
        ]]
        out = wp.focus_stats(timeuse, {'a': 'Alice'}, ['a'], ['a'], gap_secs=300)
        self.assertEqual(out['top_focus'], [('Alice', 7200)])      # 2h, not 2h04

    def test_block_is_capped_at_published_productive_hours(self):
        timeuse = [[{'start': '2026-07-14T08:00:00', 'time': 7200, 'score': 3}]]
        out = wp.focus_stats(timeuse, {'a': 'Alice'}, ['a'], ['a'],
                             prod_secs_by_uid={'a': 5400})
        self.assertEqual(out['top_focus'], [('Alice', 5400)])      # never above 1.5h

    def test_empty(self):
        out = wp.focus_stats([], {}, [], [])
        self.assertEqual(out['top_focus'], [])
        self.assertIsNone(out['best_hour'])


class ShortfallTests(SimpleTestCase):
    """The leaderboard's mirror — who missed their own target (CFO 2026-07-30)."""

    def test_ranks_by_gap_and_respects_per_person_targets(self):
        rows = wp.shortfall_board(
            ['a', 'b', 'c'],
            {'a': 2.0, 'b': 5.0, 'c': 4.6},          # productive hours
            {'a': 'Alice', 'b': 'Bob', 'c': 'Mgr Mo'},
            {'a': 6.5, 'b': 6.5, 'c': 4.5},          # Mo is on the manager rate
            streaks={'a': 4})
        # Mo BEAT her 4.5h target -> absent. Alice's 4.5h gap leads Bob's 1.5h.
        self.assertEqual([r['name'] for r in rows], ['Alice', 'Bob'])
        self.assertEqual(rows[0]['gap_h'], 4.5)
        self.assertEqual(rows[0]['streak'], 4)
        self.assertEqual(rows[1]['streak'], 1)       # no streak recorded -> first day

    def test_no_target_or_no_data_is_skipped(self):
        rows = wp.shortfall_board(['a', 'b'], {'a': None}, {'a': 'Alice', 'b': 'Bob'},
                                  {'a': 6.5, 'b': 0})
        self.assertEqual(rows, [])

    def test_bottom_cap(self):
        uids = [str(i) for i in range(9)]
        rows = wp.shortfall_board(uids, {u: 0.5 for u in uids},
                                  {u: f'P{u}' for u in uids},
                                  {u: 6.5 for u in uids}, bottom=5)
        self.assertEqual(len(rows), 5)


class SubjectTests(SimpleTestCase):
    """The subject line carries the verdict + an emoji (CFO 2026-07-30)."""

    def _data(self, **kw):
        d = {'summary': {'did_not_track': 0, 'prod_h': 5.4}, 'alarm': [],
             'shortfall': [], 'momentum': {'up': True}, 'unexplained': {}}
        d.update(kw)
        return d

    def test_unexplained_names_the_managers_on_the_hook(self):
        import datetime
        s = _subject(datetime.date(2026, 7, 29), self._data(unexplained={
            'Kabo Manager': [{'name': 'A'}, {'name': 'B'}],
            'Neo Manager': [{'name': 'C'}]}))
        self.assertTrue(s.startswith('🚩'))
        self.assertIn('3 unexplained', s)
        self.assertIn('2 managers must answer', s)

    def test_unowned_reports_are_not_counted_as_managers(self):
        import datetime
        s = _subject(datetime.date(2026, 7, 29), self._data(unexplained={
            'No manager on file': [{'name': 'A'}]}))
        self.assertIn('1 unexplained', s)
        self.assertNotIn('must answer', s)

    def test_unexplained_outranks_shortfall_and_escalates_with_alarm(self):
        import datetime
        day = datetime.date(2026, 7, 29)
        d = self._data(unexplained={'M': [{'name': 'A'}]},
                       shortfall=[{'name': 'X'}], alarm=['A'])
        s = _subject(day, d)
        self.assertTrue(s.startswith('🚨'))          # 3+ dark days present
        self.assertIn('1 unexplained', s)

    def test_clean_day_is_a_trophy(self):
        import datetime
        s = _subject(datetime.date(2026, 7, 29), self._data())
        self.assertTrue(s.startswith('🏆'))
        self.assertIn('everyone hit their hours', s)
        self.assertIn('5.4h productive', s)
        self.assertIn('Wed 29 Jul', s)

    def test_worst_signal_wins(self):
        import datetime
        day = datetime.date(2026, 7, 29)
        self.assertTrue(_subject(day, self._data(alarm=['X', 'Y'])).startswith('🚨'))
        self.assertTrue(_subject(day, self._data(
            summary={'did_not_track': 3, 'prod_h': 4.0})).startswith('🚩'))
        self.assertTrue(_subject(day, self._data(
            shortfall=[{'name': 'A'}])).startswith('🐢'))

    def test_weekly_label(self):
        import datetime
        s = _subject(datetime.date(2026, 8, 1), self._data(), weekly=True)
        self.assertIn('Weekly', s)
        self.assertIn('week to 01 Aug', s)
