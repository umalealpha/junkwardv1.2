"""WHY: the old manual process reported the cancellations as nil while 71
policies were candidates and R7,029 a month was at risk.

Three things must be true and all three are tested here:

  1. With the pay window BLANK, the report comes back BLOCKED — never as a nil
     return, never with a guessed default. An empty list is indistinguishable
     from "nothing to cancel", and that is the exact failure this build exists
     to fix.
  2. With the window set, the July book (152 active: 81 paid, 40 failed, 31
     dormant) surfaces 71 candidates and R7,029.00 a month at risk.
  3. The window is APPLIED, not merely printed. The CFO set 30 days on
     14 Sep 2026; a policy unpaid for 31 days is recommended for cancellation,
     one unpaid for 29 is not, and one unpaid for exactly 30 is not either —
     it still has that day to pay. Move the setting and the boundary moves with
     it, because the number comes off an editable setting row and nothing
     anywhere compares against the literal 30.

The class this file used to carry — PayWindowIsNotAppliedTests — is gone on
purpose. It pinned the opposite behaviour (the list was identical at 0 days and
at 10,000) and its own docstring said: "if the window now moves rows, drop this
test and apply it properly instead". It does now, so it was.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase

from genric import policies as P
from genric import reports as R
from genric.config import (
    UNPAID_DAYS_KEY,
    GenricConfigurationError,
    unpaid_days_before_cancellation,
)
from genric.models import GenricSetting

# The pack is built for July 2026, so every window below is measured to
# 31 July 2026 — the period END, never today. See PolicyRow.unpaid_days.
PERIOD_END = date(2026, 7, 31)


def set_window(days):
    """Set the pay window the way Finance does — the row, not a Django setting.

    ``None`` blanks it, which is how the BLOCKED path is reached now that the
    row seeds itself to the CFO's 30.
    """
    GenricSetting.objects.update_or_create(
        key=UNPAID_DAYS_KEY,
        defaults={'value': '' if days is None else str(days)},
    )


def _policy(n, status, created=date(2026, 1, 15), failed_on=None):
    return P.PolicyRow(policy_number=f'POL{n:04d}', status=status, raw_status=status,
                       created_at=created, premium_incl_vat=Decimal('99.00'),
                       active=True, last_failed_collection_at=failed_on)


def _unpaid(n, days, status=P.FAILED):
    """A failed policy that has been unpaid for exactly `days` at the period end."""
    return _policy(n, status, failed_on=PERIOD_END - timedelta(days=days))


def _july_book():
    """152 active: 81 paid, 40 failed, 31 dormant — the worked example."""
    rows = [_policy(i, P.PAID) for i in range(81)]
    rows += [_policy(100 + i, P.FAILED) for i in range(40)]
    rows += [_policy(200 + i, P.DORMANT) for i in range(31)]
    return rows


def _ctx(policies, prior=None):
    from genric.cession import compute_cession
    from genric.collections import CollectionSummary
    summary = CollectionSummary(
        confirmed_count=93, reversal_count=0,
        gross_credits=Decimal('9207.00'), reversals=Decimal('0.00'),
        confirmed_gwp_incl_vat=Decimal('9207.00'),
        excluded_count=0, excluded_total=Decimal('0.00'),
        unclassified_count=0, unclassified_total=Decimal('0.00'),
        by_channel={},
    )
    return R.PackContext(
        year=2026, month=7, classified=[], summary=summary,
        cession=compute_cession(Decimal('9207.00')),
        book=P.position(policies, 93),
        current_policies=policies, prior_policies=list(prior or []),
        has_prior_export=bool(prior),
        invoice_number='GENRIC-1-024', policy_source='graphite_export',
        bank_statement_label='ST-000001',
    )


def _listed(rep):
    return {row[0] for row in rep.rows}


class TheCfosNumberIsASettingFinanceOwnsTests(TestCase):
    """CFO decision 14 Sep 2026: 30 days, matching 30-day statement terms."""

    def test_the_window_seeds_itself_to_the_cfos_thirty_days(self):
        self.assertEqual(unpaid_days_before_cancellation(), 30)

    def test_the_seeded_row_is_visible_to_finance_with_its_reason(self):
        unpaid_days_before_cancellation()
        row = GenricSetting.objects.get(key=UNPAID_DAYS_KEY)
        self.assertEqual(row.value, '30')
        self.assertIn('statement terms', row.description)

    def test_finance_editing_the_row_changes_the_window_with_no_deploy(self):
        set_window(45)
        self.assertEqual(unpaid_days_before_cancellation(), 45)

    def test_an_edit_is_never_overwritten_by_the_seed(self):
        set_window(45)
        unpaid_days_before_cancellation()
        unpaid_days_before_cancellation()
        self.assertEqual(GenricSetting.objects.get(key=UNPAID_DAYS_KEY).value, '45')

    def test_a_nonsense_value_is_refused_not_coerced(self):
        set_window('not a number')
        with self.assertRaises(GenricConfigurationError):
            unpaid_days_before_cancellation()

    def test_a_negative_window_is_refused(self):
        set_window(-1)
        with self.assertRaises(GenricConfigurationError):
            unpaid_days_before_cancellation()


class BlockedWhenTheWindowIsBlankTests(TestCase):
    """Blanking the row must go BLOCKED, never silently back to 30."""

    def setUp(self):
        set_window(None)

    def test_the_setting_raises_loudly_rather_than_defaulting(self):
        with self.assertRaises(GenricConfigurationError) as cm:
            unpaid_days_before_cancellation()
        self.assertIn('how many days', str(cm.exception).lower())

    def test_report_is_blocked_not_nil(self):
        rep = R.build_cancellations(_ctx(_july_book()))
        self.assertEqual(rep.status, R.BLOCKED)
        self.assertNotEqual(rep.status, R.NIL_NO_ACTIVITY)
        self.assertEqual(rep.rows, [], 'a blocked report must not publish a candidate list')

    def test_the_blocked_report_still_shows_the_counts_that_are_real(self):
        """Blocked is not useless. The counts do not need the pay window."""
        rep = R.build_cancellations(_ctx(_july_book()))
        kpis = dict(rep.kpis)
        self.assertEqual(kpis['Active policies'], '152')
        self.assertEqual(kpis['Potential candidates'], '71')
        self.assertEqual(kpis['Monthly premium at risk'], 'R7,029.00')

    def test_the_question_and_the_setting_name_are_printed_on_the_report(self):
        rep = R.build_cancellations(_ctx(_july_book()))
        self.assertTrue(any('BLOCKED' in n for n in rep.notes))
        self.assertTrue(any('how many days' in n.lower() for n in rep.notes))
        self.assertTrue(any(UNPAID_DAYS_KEY in n for n in rep.notes),
                        'the report must name the setting a person has to go and fix')


class ThirtyDayBoundaryTests(TestCase):
    """RED-PROVE. The exact line the CFO drew, and both sides of it.

    The predicate is ``unpaid > days`` — strictly greater. A policy at exactly
    30 days falls on the SAFE side: still inside the window, NOT recommended for
    cancellation, because "more than 30 days" means day 31 is the first day past
    and the customer still has the whole of day 30 to pay. Cancelling them a day
    early is the more expensive error — lost cover and a TCF complaint — while
    carrying a non-payer one extra day costs one day of premium.
    """

    def setUp(self):
        set_window(30)

    def test_thirty_one_days_unpaid_is_past_the_window_and_listed(self):
        rep = R.build_cancellations(_ctx([_unpaid(1, 31)]))
        self.assertEqual(_listed(rep), {'POL0001'})

    def test_twenty_nine_days_unpaid_is_inside_the_window_and_not_listed(self):
        rep = R.build_cancellations(_ctx([_unpaid(2, 29)]))
        self.assertEqual(_listed(rep), set())

    def test_exactly_thirty_days_is_inside_the_window_and_not_listed(self):
        """THE BOUNDARY. 30 is not MORE THAN 30 — the customer still has day 30."""
        rep = R.build_cancellations(_ctx([_unpaid(3, 30)]))
        self.assertEqual(_listed(rep), set(),
                         'a policy at exactly the window was cancelled a day early')

    def test_the_three_of_them_together_sort_out_correctly(self):
        rep = R.build_cancellations(_ctx([_unpaid(1, 31), _unpaid(2, 29), _unpaid(3, 30)]))
        self.assertEqual(_listed(rep), {'POL0001'})
        kpis = dict(rep.kpis)
        self.assertEqual(kpis['Unpaid more than 30 days — recommend cancellation'], '1')
        self.assertEqual(kpis['Unpaid but still inside the 30 days'], '2')

    def test_the_days_unpaid_are_shown_on_the_row(self):
        rep = R.build_cancellations(_ctx([_unpaid(1, 31)]))
        self.assertIn('Days unpaid', rep.columns)
        self.assertEqual(rep.rows[0][rep.columns.index('Days unpaid')], 31)

    def test_the_window_is_measured_to_the_period_end_not_to_today(self):
        """A July pack must give the same answer whenever the button is pressed."""
        p = _policy(9, P.FAILED, failed_on=PERIOD_END - timedelta(days=31))
        self.assertEqual(p.unpaid_days(PERIOD_END), 31)
        self.assertEqual(p.unpaid_days(date(2026, 8, 31)), 62)


class MovingTheSettingMovesTheBoundaryTests(TestCase):
    """The number is a lever, not decoration. Turn it and the list changes."""

    def _rep(self, days, policies):
        set_window(days)
        return R.build_cancellations(_ctx(policies))

    def test_widening_the_window_drops_a_policy_off_the_list(self):
        book = [_unpaid(1, 31)]
        self.assertEqual(_listed(self._rep(30, book)), {'POL0001'})
        self.assertEqual(_listed(self._rep(60, book)), set(),
                         'at 60 days a 31-day-unpaid policy is still inside the window')

    def test_narrowing_the_window_pulls_a_policy_onto_the_list(self):
        book = [_unpaid(2, 29)]
        self.assertEqual(_listed(self._rep(30, book)), set())
        self.assertEqual(_listed(self._rep(10, book)), {'POL0002'})

    def test_the_boundary_tracks_the_setting_exactly(self):
        """At a window of W, W days is in and W+1 days is out — for any W."""
        for w in (7, 14, 30, 60, 90):
            with self.subTest(window=w):
                self.assertEqual(_listed(self._rep(w, [_unpaid(1, w)])), set())
                self.assertEqual(_listed(self._rep(w, [_unpaid(1, w + 1)])), {'POL0001'})

    def test_the_report_is_not_identical_at_zero_and_at_ten_thousand_days(self):
        """The defect PR #974 pinned: the window used to move nothing at all."""
        book = [_unpaid(1, 31), _unpaid(2, 29)]
        self.assertNotEqual(self._rep(0, book).rows, self._rep(10000, book).rows)


class TheReportSaysWhichWindowProducedItTests(TestCase):
    """Nobody should have to guess which number made these figures."""

    def setUp(self):
        set_window(30)

    def test_the_title_states_the_window_in_plain_words(self):
        rep = R.build_cancellations(_ctx([_unpaid(1, 31)]))
        self.assertEqual(rep.title,
                         'Cancellations — policies unpaid for more than 30 days')

    def test_a_note_states_the_window_and_where_it_came_from(self):
        rep = R.build_cancellations(_ctx([_unpaid(1, 31)]))
        blob = ' '.join(rep.notes)
        self.assertIn('PAY WINDOW APPLIED: 30 days', blob)
        self.assertIn('MORE THAN 30 days', blob)
        self.assertIn(UNPAID_DAYS_KEY, blob)

    def test_a_note_states_which_side_of_the_line_exactly_thirty_falls(self):
        rep = R.build_cancellations(_ctx([_unpaid(1, 31)]))
        self.assertTrue(
            any('exactly 30 days is NOT listed' in n for n in rep.notes),
            'the report never says what happens at exactly the window')

    def test_the_kpi_block_states_the_window_in_one_line(self):
        rep = R.build_cancellations(_ctx([_unpaid(1, 31)]))
        self.assertEqual(dict(rep.kpis)['Pay window'], 'More than 30 days unpaid')

    def test_the_reconciliation_line_names_the_window_and_the_as_at_date(self):
        rep = R.build_cancellations(_ctx([_unpaid(1, 31)]))
        self.assertIn('30-day pay window', rep.reconciled_against)
        self.assertIn('2026-07-31', rep.reconciled_against)

    def test_the_title_follows_the_setting_rather_than_saying_thirty_forever(self):
        set_window(45)
        rep = R.build_cancellations(_ctx([_unpaid(1, 46)]))
        self.assertIn('more than 45 days', rep.title)
        self.assertNotIn('30', rep.title)


class APolicyWithNoFailedCollectionDateTests(TestCase):
    """The honest half: unknown is reported as unknown, not as in or out."""

    def setUp(self):
        set_window(30)

    def test_it_is_not_listed_because_nothing_proves_it_ran_out_of_time(self):
        rep = R.build_cancellations(_ctx([_policy(1, P.FAILED, failed_on=None)]))
        self.assertEqual(_listed(rep), set())

    def test_it_is_not_quietly_cleared_either_it_is_counted_as_unknown(self):
        rep = R.build_cancellations(_ctx([_policy(1, P.FAILED, failed_on=None)]))
        kpis = dict(rep.kpis)
        self.assertEqual(kpis['No failed-collection date — cannot be measured'], '1')
        self.assertEqual(kpis['Unpaid but still inside the 30 days'], '0',
                         'an unmeasurable policy was counted as inside the window')

    def test_the_report_says_so_in_red_where_a_person_reads_it(self):
        rep = R.build_cancellations(_ctx([_policy(1, P.FAILED, failed_on=None)]))
        self.assertTrue(
            any('NO failed-collection date' in n and 'NOT cleared' in n
                for n in rep.notes),
            'the unmeasurable policies are not surfaced on the report')

    def test_the_inception_date_is_never_used_as_a_stand_in(self):
        """createdAt is when cover started, not when a collection failed.

        Standing it in would make every long-standing policy look years unpaid.
        """
        old = _policy(1, P.FAILED, created=date(2020, 1, 1), failed_on=None)
        self.assertIsNone(old.unpaid_days(PERIOD_END))
        rep = R.build_cancellations(_ctx([old]))
        self.assertEqual(_listed(rep), set())

    def test_the_whole_july_book_with_no_dates_lists_nobody_but_hides_nobody(self):
        rep = R.build_cancellations(_ctx(_july_book()))
        kpis = dict(rep.kpis)
        self.assertEqual(_listed(rep), set())
        self.assertEqual(kpis['No failed-collection date — cannot be measured'], '71')
        self.assertEqual(kpis['Monthly premium at risk (all candidates)'], 'R7,029.00')


class TheCountsThatDoNotNeedTheWindowTests(TestCase):
    """The July worked example still ties, window or no window."""

    def setUp(self):
        set_window(30)

    def test_july_still_shows_seventy_one_candidates_and_the_premium_at_risk(self):
        rep = R.build_cancellations(_ctx(_july_book()))
        kpis = dict(rep.kpis)
        self.assertEqual(kpis['Active policies'], '152')
        self.assertEqual(kpis['Failed collections'], '40')
        self.assertEqual(kpis['Dormant — no collection'], '31')
        self.assertEqual(kpis['Monthly premium at risk (all candidates)'], 'R7,029.00')

    def test_paid_policies_are_never_candidates(self):
        book = _july_book() + [_unpaid(1, 31, status=P.PAID)]
        rep = R.build_cancellations(_ctx(book))
        paid = {p.policy_number for p in book if p.status == P.PAID}
        self.assertFalse(_listed(rep) & paid)

    def test_the_premium_past_the_window_is_totalled_separately(self):
        rep = R.build_cancellations(_ctx([_unpaid(1, 31), _unpaid(2, 31), _unpaid(3, 29)]))
        kpis = dict(rep.kpis)
        self.assertEqual(kpis['Monthly premium past the 30 days'], 'R198.00')
        self.assertEqual(kpis['Monthly premium at risk (all candidates)'], 'R297.00')

    def test_without_a_prior_month_nothing_claims_two_consecutive_failures(self):
        rep = R.build_cancellations(_ctx([_unpaid(1, 31)]))
        self.assertEqual(rep.rows[0][rep.columns.index('2 consecutive months failed')], 'No')
        self.assertTrue(any('prior-month' in n for n in rep.notes))

    def test_with_a_prior_month_the_repeat_failures_are_identified(self):
        rep = R.build_cancellations(_ctx([_unpaid(100, 31)],
                                         prior=[_policy(100, P.FAILED)]))
        self.assertEqual(rep.rows[0][rep.columns.index('2 consecutive months failed')], 'Yes')

    def test_policies_over_eighteen_months_old_are_flagged(self):
        old = _policy(900, P.FAILED, created=date(2024, 1, 1),
                      failed_on=PERIOD_END - timedelta(days=31))
        rep = R.build_cancellations(_ctx([old]))
        self.assertEqual(rep.rows[0][rep.columns.index('Over 18 months')], 'Yes')

    def test_a_policy_with_no_inception_date_is_not_assumed_young(self):
        undated = _policy(901, P.FAILED, created=None,
                          failed_on=PERIOD_END - timedelta(days=31))
        rep = R.build_cancellations(_ctx([undated]))
        self.assertEqual(rep.rows[0][rep.columns.index('Inception')], '(no inception date)')
        self.assertEqual(rep.rows[0][rep.columns.index('Over 18 months')], 'No')

    def test_the_report_never_cancels_anything(self):
        rep = R.build_cancellations(_ctx(_july_book()))
        self.assertTrue(any('NEVER cancels' in n for n in rep.notes))
        self.assertTrue(any('TCF' in n for n in rep.notes))


class TheExportCarriesTheFailedCollectionDateTests(TestCase):
    """The loader has to actually read the column, with no fallback."""

    def test_a_failed_collection_column_is_read(self):
        csv = ('Policy Number,Payment Status,createdAt,Premium,Last Failed Collection Date\n'
               'POL0001,Payment Failed,2026-01-10,99.00,2026-06-30\n')
        row = P.load_export(csv)[0]
        self.assertEqual(row.last_failed_collection_at, date(2026, 6, 30))
        self.assertEqual(row.unpaid_days(PERIOD_END), 31)

    def test_an_export_without_the_column_leaves_it_none_never_zero(self):
        csv = ('Policy Number,Payment Status,createdAt,Premium\n'
               'POL0001,Payment Failed,2026-01-10,99.00\n')
        row = P.load_export(csv)[0]
        self.assertIsNone(row.last_failed_collection_at)
        self.assertIsNone(row.unpaid_days(PERIOD_END),
                          'a missing date became a real day count')

    def test_a_last_successful_payment_column_is_not_mistaken_for_a_failure(self):
        """The near-miss that would cancel every long-standing paying customer."""
        csv = ('Policy Number,Payment Status,createdAt,Premium,Last Payment Date\n'
               'POL0001,Payment Failed,2020-01-10,99.00,2020-02-01\n')
        row = P.load_export(csv)[0]
        self.assertIsNone(row.last_failed_collection_at)

    def test_a_late_utc_timestamp_is_read_as_the_BOTSWANA_day(self):
        """Botswana is UTC+2, and this feature counts DAYS.

        22:30 UTC on 30 June is already 1 July here. Taking .date() on the
        tz-aware UTC value reports 30 June, which at the 31 July period end is
        31 days unpaid instead of 30 — the far side of the pay-window boundary.
        The policy would be recommended for cancellation a day early.
        """
        csv = ('Policy Number,Payment Status,Premium,Last Failed Collection Date\n'
               'POL0001,Payment Failed,99.00,2026-06-30T22:30:00Z\n')
        row = P.load_export(csv)[0]
        self.assertEqual(row.last_failed_collection_at, date(2026, 7, 1),
                         'a UTC timestamp was read as the UTC day, not the Botswana day')
        self.assertEqual(row.unpaid_days(PERIOD_END), 30)

    def test_that_late_timestamp_is_therefore_NOT_recommended_for_cancellation(self):
        """The whole point of the fix, proved at the boundary it moves."""
        set_window(30)
        csv = ('Policy Number,Payment Status,Premium,Last Failed Collection Date\n'
               'POL0001,Payment Failed,99.00,2026-06-30T22:30:00Z\n')
        rep = R.build_cancellations(_ctx(P.load_export(csv)))
        self.assertEqual(_listed(rep), set(),
                         'a UTC day-slip cancelled a policy one day early')

    def test_an_early_utc_timestamp_is_unaffected(self):
        """Only 22:00-24:00 UTC crosses the local day. Mid-day must not move."""
        csv = ('Policy Number,Payment Status,Premium,Last Failed Collection Date\n'
               'POL0001,Payment Failed,99.00,2026-06-30T09:00:00Z\n')
        row = P.load_export(csv)[0]
        self.assertEqual(row.last_failed_collection_at, date(2026, 6, 30))

    def test_a_plain_date_with_no_zone_is_left_exactly_as_written(self):
        """A date-only column carries no zone and must not be shifted."""
        csv = ('Policy Number,Payment Status,Premium,Unpaid Since\n'
               'POL0001,Payment Failed,99.00,2026-06-30\n')
        row = P.load_export(csv)[0]
        self.assertEqual(row.last_failed_collection_at, date(2026, 6, 30))

    def test_createdAt_gets_the_SAME_botswana_day_treatment(self):
        """_parse_date serves createdAt too, so the fix reaches it as well.

        That is deliberate, not a side effect. A policy created at 22:30 UTC on
        31 Dec 2024 incepted on 1 Jan 2025 HERE, and the 18-month flag and the
        New Business month must both use the day it actually happened in
        Botswana. It also means a policy created and failed at the same instant
        can never be read as two different days.
        """
        csv = ('Policy Number,Payment Status,Premium,createdAt\n'
               'POL0001,Payment Failed,99.00,2024-12-31T22:30:00Z\n')
        row = P.load_export(csv)[0]
        self.assertEqual(row.created_at, date(2025, 1, 1),
                         'createdAt was read as the UTC day, not the Botswana day')
        # 31 Jul 2026 minus 1 Jan 2025 = 18 months exactly, so NOT over 18.
        self.assertEqual(row.age_months(PERIOD_END), 18)

    def test_that_policy_is_therefore_NOT_flagged_as_over_eighteen_months(self):
        """The boundary the day-slip moved: 18 is not OVER 18."""
        set_window(30)
        csv = ('Policy Number,Payment Status,Premium,createdAt,Unpaid Since\n'
               'POL0001,Payment Failed,99.00,2024-12-31T22:30:00Z,2026-06-25\n')
        rep = R.build_cancellations(_ctx(P.load_export(csv)))
        self.assertEqual(rep.rows[0][rep.columns.index('Over 18 months')], 'No',
                         'a UTC day-slip aged the policy an extra month')

    def test_the_dpa_allow_list_still_drops_everything_else(self):
        csv = ('Policy Number,Client Name,ID Number,Payment Status,Premium,Unpaid Since\n'
               'POL0001,Thabo Nkosi,8001015009087,Payment Failed,99.00,2026-06-30\n')
        row = P.load_export(csv)[0]
        blob = repr(row)
        for pii in ('Thabo', 'Nkosi', '8001015009087'):
            self.assertNotIn(pii, blob)
        self.assertEqual(row.last_failed_collection_at, date(2026, 6, 30))
