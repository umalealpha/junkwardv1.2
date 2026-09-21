"""Rule-by-rule tests for the performance review scorer (CFO 2026-09-09).

Every test here is written so that removing the rule it covers makes it fail.
"""
import datetime as dt

from django.test import SimpleTestCase

from hris.review_scorer import (BASE_SCORE, EXCO_MISS_SCORE, FLOOR_SCORE,
                                INTEGRITY_SCORE, forgiven_late_days,
                                rating_for, score)

D = dt.date


class RatingBandTests(SimpleTestCase):
    def test_bands_match_the_cfo_mapping(self):
        self.assertEqual(rating_for(10), 'ME')
        self.assertEqual(rating_for(8), 'ME')
        self.assertEqual(rating_for(7), 'PA')
        self.assertEqual(rating_for(6), 'PA')
        self.assertEqual(rating_for(5), 'BE')
        self.assertEqual(rating_for(3), 'BE')


class CleanMonthTests(SimpleTestCase):
    def test_a_clean_month_scores_eight_and_meets(self):
        s = score({'late_dates': [], 'low_hours_days': 0, 'overdue_tasks': 0})
        self.assertEqual(s.points, BASE_SCORE)
        self.assertEqual(s.rating, 'ME')
        self.assertEqual(s.marks, [])

    def test_a_rule_with_no_data_is_reported_not_silently_passed(self):
        s = score({})
        rules = {n.rule for n in s.not_scored}
        self.assertIn('1', rules)
        self.assertIn('2', rules)
        self.assertIn('9', rules)
        self.assertEqual(s.points, BASE_SCORE)   # a gap is not a failure


class Rule1LatenessTests(SimpleTestCase):
    def test_three_late_mornings_costs_two_points(self):
        s = score({'late_dates': [D(2026, 9, 1), D(2026, 9, 2), D(2026, 9, 3)]})
        self.assertEqual(s.points, 6)
        self.assertEqual([m.rule for m in s.marks], ['1'])

    def test_two_late_mornings_is_not_a_breach(self):
        s = score({'late_dates': [D(2026, 9, 1), D(2026, 9, 2)]})
        self.assertEqual(s.points, BASE_SCORE)

    def test_notified_mornings_are_forgiven_up_to_three(self):
        late = [D(2026, 9, d) for d in (1, 2, 3)]
        s = score({'late_dates': late, 'late_excused_dates': late})
        self.assertEqual(s.points, BASE_SCORE)

    def test_the_fourth_notified_morning_still_counts(self):
        """CFO: 'forgiven for the FIRST 3 late days a month; from the 4th it
        counts even if notified'. Six notified late days must leave three
        counted, which is a breach."""
        late = [D(2026, 9, d) for d in (1, 2, 3, 4, 5, 6)]
        counted, forgiven = forgiven_late_days(late, late)
        self.assertEqual(len(forgiven), 3)
        self.assertEqual(len(counted), 3)
        s = score({'late_dates': late, 'late_excused_dates': late})
        self.assertEqual(s.points, 6)

    def test_forgiveness_takes_the_earliest_mornings_deterministically(self):
        late = [D(2026, 9, d) for d in (10, 2, 7, 1, 5)]
        counted, forgiven = forgiven_late_days(late, late)
        self.assertEqual(forgiven, [D(2026, 9, 1), D(2026, 9, 2), D(2026, 9, 5)])
        self.assertEqual(counted, [D(2026, 9, 7), D(2026, 9, 10)])


class Rule2And3Tests(SimpleTestCase):
    def test_five_unexplained_short_days_costs_two(self):
        self.assertEqual(score({'low_hours_days': 5}).points, 6)

    def test_four_short_days_is_not_a_breach(self):
        self.assertEqual(score({'low_hours_days': 4}).points, BASE_SCORE)

    def test_three_overdue_tasks_costs_one(self):
        self.assertEqual(score({'overdue_tasks': 3}).points, 7)

    def test_skipped_team_feedback_is_its_own_mark_not_a_task(self):
        """CFO 2026-09-09: it must be named separately, not buried in the task
        count — otherwise the manager is hit twice for the same failure."""
        s = score({'overdue_tasks': 0, 'feedback_skipped': 4})
        self.assertEqual([m.rule for m in s.marks], ['3b'])
        self.assertEqual(s.points, 7)


class Rule8SystemFaultTests(SimpleTestCase):
    def test_system_faults_do_not_touch_an_ordinary_staff_review(self):
        s = score({'system_fault_open': True})
        self.assertEqual(s.points, BASE_SCORE)

    def test_the_named_owner_loses_three(self):
        s = score({'system_fault_open': True, 'system_fault_owner': True})
        self.assertEqual(s.points, 5)
        self.assertEqual([m.points for m in s.marks], [-3])


class Rule9ScreensTests(SimpleTestCase):
    def test_missing_a_required_screen_costs_two(self):
        s = score({'expected_screens': ['/claims', '/purchase-orders'],
                   'screens_opened': ['/claims']})
        self.assertEqual(s.points, 6)

    def test_no_approved_role_list_means_the_rule_does_not_run(self):
        s = score({'screens_opened': []})
        self.assertIn('9', {n.rule for n in s.not_scored})
        self.assertEqual(s.points, BASE_SCORE)


class FloorAndOverrideTests(SimpleTestCase):
    def test_the_score_never_falls_below_three(self):
        s = score({'late_dates': [D(2026, 9, d) for d in (1, 2, 3)],
                   'low_hours_days': 9, 'overdue_tasks': 9,
                   'claim_breaches': 6, 'quote_but_paid': 3,
                   'feedback_skipped': 2})
        self.assertEqual(s.points, FLOOR_SCORE)

    def test_exco_missing_budget_is_a_flat_five(self):
        s = score({'is_exco': True, 'gwp_missed_budget': True,
                   'late_dates': [], 'low_hours_days': 0})
        self.assertEqual(s.points, EXCO_MISS_SCORE)
        self.assertEqual(s.override, 'rule 7')

    def test_exco_flat_five_replaces_a_better_arithmetic_score(self):
        """A clean Exco month would be 8. Rule 7 must pull it DOWN to 5."""
        clean = score({'late_dates': [], 'low_hours_days': 0})
        self.assertEqual(clean.points, 8)
        s = score({'is_exco': True, 'gwp_missed_budget': True,
                   'late_dates': [], 'low_hours_days': 0})
        self.assertEqual(s.points, 5)

    def test_a_frozen_screen_day_overrides_everything_including_a_clean_month(self):
        s = score({'late_dates': [], 'low_hours_days': 0,
                   'integrity_suspicious_days': [D(2026, 9, 5)]})
        self.assertEqual(s.points, INTEGRITY_SCORE)
        self.assertEqual(s.rating, 'BE')
        self.assertEqual(s.override, 'rule 10')

    def test_integrity_beats_the_exco_flat_five(self):
        s = score({'is_exco': True, 'gwp_missed_budget': True,
                   'integrity_suspicious_days': [D(2026, 9, 5)]})
        self.assertEqual(s.points, INTEGRITY_SCORE)
        self.assertEqual(s.override, 'rule 10')

    def test_an_exempt_person_is_not_hit_by_rule_ten(self):
        s = score({'integrity_exempt': True,
                   'integrity_suspicious_days': [D(2026, 9, 5)],
                   'late_dates': [], 'low_hours_days': 0})
        self.assertEqual(s.points, BASE_SCORE)
        self.assertIn('10', {n.rule for n in s.not_scored})

    def test_a_watch_flag_costs_nothing(self):
        """Only CONFIRMED suspicious days land on 3. A watch is a note."""
        s = score({'late_dates': [], 'low_hours_days': 0,
                   'integrity_suspicious_days': []})
        self.assertEqual(s.points, BASE_SCORE)
