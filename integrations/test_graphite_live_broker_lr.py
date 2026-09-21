"""Tests for the broker loss ratio computed live from the Graphite replica.

The pushed `broker_lr` snapshot behind the CFO's 70% escalation rule is wrong two
ways, both proved on the live replica 2026-09-08:

  1. It reaches the broker through the SELLING AGENT (policies.agent_id ->
     users.agency_id) instead of the intermediary on the policy. 22 brokers with
     live claims — Marsh, Minet, Spectrum, Botshabelo, UTL — never reach the flag.
  2. It adds payments to RESERVE MOVEMENTS and calls the total "incurred". A
     reserve is what later gets paid, so a claim reserved at P2m and then settled
     for P2m is scored as P4m. Exactly 2.00x on every settled claim.

`graphite_ro.query` is faked. The fake is not a SQL engine: it reads which link
and which reserve method the statement asks for, and answers with the result set
that choice really returns on the replica. A query written the old way therefore
gets the old way's data, exactly as production does, and the assertions below are
about the numbers the panel ends up showing.
"""
from unittest import mock

from django.test import SimpleTestCase

from integrations import graphite_live_broker_lr as live

# Fixture: Marsh holds a real book on the policy's own agency and NOTHING via the
# selling agent. Its single claim was reserved at P2m and then settled in full —
# so the honest incurred cost is P2m, and the double-counting method says P4m.
_MARSH = 'Marsh Botswana (PTY) LTD'
_PREMIUM = 529_064.0
_PAID = 2_000_000.0
_RESERVE_MOVEMENTS = 2_000_000.0   # what SUM(reserve_amt) returns — the wrong way
_TRUE_OUTSTANDING = 0.0            # latest balance per claim+coverage — settled


def _fake_query(sql, params=None, *, limit=500):
    """Answer as the replica would for the link/method this statement asks for."""
    by_agent = 'u.agency_id' in sql
    # 'inforce' first: the in-force statement also contains the word "premium".
    if 'inforce' in sql:
        return [] if by_agent else [{'broker': _MARSH, 'inforce': _PREMIUM}]
    if 'premium' in sql:
        return [] if by_agent else [{'broker': _MARSH, 'premium_fy': _PREMIUM}]
    if 'payment_amt' in sql:
        return [] if by_agent else [
            {'broker': _MARSH, 'claim_count': 1, 'payment': _PAID}]
    if 'balance' in sql:
        # The honest outstanding reads the LATEST balance per claim+coverage.
        # Summing every reserve row instead is the double-count.
        latest_only = 'MAX(id)' in sql
        return [] if by_agent else [
            {'broker': _MARSH,
             'outstanding': _TRUE_OUTSTANDING if latest_only else _RESERVE_MOVEMENTS}]
    raise AssertionError(f'unexpected statement: {sql[:80]}')


class BrokerLrLiveTest(SimpleTestCase):
    def _rows(self):
        with mock.patch.object(live, 'is_configured', return_value=True), \
                mock.patch.object(live, 'query', _fake_query):
            return live.build('broker_lr')

    def test_broker_comes_from_the_policy_not_the_selling_agent(self):
        rows = self._rows()
        self.assertEqual([r['broker'] for r in rows], [_MARSH],
                         'broker resolved via the selling agent, not the policy')

    def test_settled_claims_are_not_counted_twice(self):
        """P2m reserved then P2m paid is a P2m claim, never a P4m one."""
        row = self._rows()[0]
        self.assertEqual(row['payment'], _PAID)
        self.assertEqual(row['reserve'], _TRUE_OUTSTANDING,
                         'reserve is summing movements, so settled claims double-count')
        self.assertEqual(row['payment'] + row['reserve'], _PAID)

    def test_rows_match_the_pushed_snapshot_shape(self):
        """The panel must need no reshaping — same keys the push sends."""
        self.assertEqual(
            set(self._rows()[0]),
            {'broker', 'payment', 'reserve', 'premium_fy', 'inforce_book',
             'claim_count', 'lr_on_reserve'})

    def test_lr_on_reserve_is_outstanding_over_premium(self):
        row = self._rows()[0]
        self.assertEqual(row['lr_on_reserve'], 0.0)

    def test_other_datasets_are_left_alone(self):
        with mock.patch.object(live, 'is_configured', return_value=True), \
                mock.patch.object(live, 'query', _fake_query):
            self.assertIsNone(live.build('debtors_aging'))

    def test_falls_back_when_the_replica_is_not_reachable(self):
        """Unavailable is a real answer the callers act on: the escalation
        panel goes dark, the feeds viewer falls through to the labelled push."""
        with mock.patch.object(live, 'is_configured', return_value=False):
            self.assertIsNone(live.build('broker_lr'))
        with mock.patch.object(live, 'is_configured', return_value=True), \
                mock.patch.object(live, 'query', side_effect=RuntimeError('down')):
            self.assertIsNone(live.build('broker_lr'))


class BrokerLrPanelTest(SimpleTestCase):
    """The panel must escalate on the corrected figure."""

    def test_panel_flags_on_the_live_figure_not_the_pushed_one(self):
        from integrations import graphite_panels_views as panels
        rows = [{'broker': _MARSH, 'payment': _PAID, 'reserve': _TRUE_OUTSTANDING,
                 'premium_fy': _PREMIUM, 'claim_count': 1, 'lr_on_reserve': 0.0}]
        out = panels._shape_broker_lr(rows)
        self.assertEqual(len(out), 1)
        # P2,000,000 incurred on P529,064 of premium = 378.03%
        self.assertAlmostEqual(out[0]['incurred_lr_pct'], 378.03, places=1)
        self.assertTrue(out[0]['flagged'])

    def test_a_settled_book_is_not_double_flagged(self):
        """The old method would read 756% on the same claim — twice the truth."""
        from integrations import graphite_panels_views as panels
        honest = panels._shape_broker_lr(
            [{'broker': _MARSH, 'payment': _PAID, 'reserve': _TRUE_OUTSTANDING,
              'premium_fy': _PREMIUM, 'claim_count': 1, 'lr_on_reserve': 0.0}])
        doubled = panels._shape_broker_lr(
            [{'broker': _MARSH, 'payment': _PAID, 'reserve': _RESERVE_MOVEMENTS,
              'premium_fy': _PREMIUM, 'claim_count': 1, 'lr_on_reserve': 3.78}])
        self.assertAlmostEqual(
            doubled[0]['incurred_lr_pct'] / honest[0]['incurred_lr_pct'], 2.0,
            places=2)


class BrokerLrTruncationTest(SimpleTestCase):
    """A short broker list must never reach the escalation rule silently."""

    def test_a_read_that_hits_the_cap_refuses_rather_than_truncating(self):
        over = [{'broker': f'B{i}', 'premium_fy': 1.0}
                for i in range(live._ROW_CAP + 1)]
        with mock.patch.object(live, 'is_configured', return_value=True), \
                mock.patch.object(live, 'query', return_value=over):
            self.assertIsNone(live.build('broker_lr'),
                              'a truncated read was served instead of refused')

    def test_the_reads_ask_for_one_more_row_than_the_cap(self):
        """Without the +1 the cap is invisible — a full page looks complete."""
        seen = []

        def spy(sql, params=None, *, limit=500):
            seen.append(limit)
            return _fake_query(sql, params, limit=limit)

        with mock.patch.object(live, 'is_configured', return_value=True), \
                mock.patch.object(live, 'query', spy):
            live.build('broker_lr')
        self.assertTrue(seen)
        self.assertTrue(all(v == live._ROW_CAP + 1 for v in seen), seen)


class BrokerLrDuplicateAgencyTest(SimpleTestCase):
    """A broker filed twice must be scored once.

    Graphite has no uniqueness control on `agencies`: on 2026-09-08 the same firm
    existed twice in 8 groups across 17 records. A split record puts the premium
    on one row and the claims on the other, so both halves score wrongly and the
    CFO's 70% rule fires on a ratio that is not real.
    """

    _SPLIT = [
        # Premium sits on one record...
        {'broker': 'Hilrange Enterprises (Pty) Ltd T/a Redhill Risk Solutions',
         'premium_fy': 1_000_000.0, 'payment': 0.0, 'claim_count': 0},
        # ...and the claims on the other. Different SPELLING, same firm.
        {'broker': 'Hildrage Enterprises (Pty) Ltd T/a Redhill Risk Solutions',
         'premium_fy': 0.0, 'payment': 900_000.0, 'claim_count': 4},
    ]

    def _rows(self):
        def fake(sql, params=None, *, limit=500):
            if 'inforce' in sql:
                return [{'broker': r['broker'], 'inforce': r['premium_fy']}
                        for r in self._SPLIT]
            if 'premium' in sql:
                return [{'broker': r['broker'], 'premium_fy': r['premium_fy']}
                        for r in self._SPLIT]
            if 'payment_amt' in sql:
                return [{'broker': r['broker'], 'claim_count': r['claim_count'],
                         'payment': r['payment']} for r in self._SPLIT]
            if 'balance' in sql:
                return [{'broker': r['broker'], 'outstanding': 0.0}
                        for r in self._SPLIT]
            raise AssertionError(sql[:80])

        with mock.patch.object(live, 'is_configured', return_value=True), \
                mock.patch.object(live, 'query', fake):
            return live.build('broker_lr')

    def test_a_broker_filed_twice_is_scored_once(self):
        rows = self._rows()
        self.assertEqual(len(rows), 1, [r['broker'] for r in rows])
        self.assertEqual(rows[0]['premium_fy'], 1_000_000.0)
        self.assertEqual(rows[0]['payment'], 900_000.0)
        self.assertEqual(rows[0]['claim_count'], 4)

    def test_the_split_would_otherwise_produce_a_false_escalation(self):
        """Unmerged, the claims half reads an infinite ratio on zero premium and
        the premium half reads 0% — neither is this broker. Merged: 90%."""
        from integrations import graphite_panels_views as panels
        out = panels._shape_broker_lr(self._rows())
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(out[0]['incurred_lr_pct'], 90.0, places=1)
        self.assertTrue(out[0]['flagged'])


class BrokerLrRuleBasisTest(SimpleTestCase):
    """The CFO's rule runs on the annual book, with a size floor (2026-09-08)."""

    def _row(self, book, incurred, premium_fy=10_000.0):
        return [{'broker': 'Test Broker', 'premium_fy': premium_fy,
                 'inforce_book': book, 'payment': incurred, 'reserve': 0.0,
                 'claim_count': 3, 'lr_on_reserve': 0.0}]

    def test_the_ratio_is_taken_on_the_annual_book_not_the_premium_written(self):
        """Ten weeks of written premium put Letsema at 11,683%. The book is the
        denominator the rule uses."""
        from integrations import graphite_panels_views as panels
        # P18,099 written so far, P390,049 of book, P2,114,495 incurred — Letsema.
        out = panels._shape_broker_lr(
            self._row(book=390_049.0, incurred=2_114_495.0, premium_fy=18_099.0))
        self.assertAlmostEqual(out[0]['incurred_lr_pct'], 542.1, places=0)
        self.assertTrue(out[0]['flagged'])

    def test_a_broker_too_small_to_escalate_still_shows_but_does_not_flag(self):
        """Sparkle Legacy: P13,120 of book, one claim. Visible, not escalated."""
        from integrations import graphite_panels_views as panels
        out = panels._shape_broker_lr(self._row(book=13_120.0, incurred=16_240.0))
        self.assertEqual(len(out), 1, 'the small broker was hidden, not just un-flagged')
        self.assertGreater(out[0]['incurred_lr_pct'], 70)
        self.assertTrue(out[0]['below_escalation_size'])
        self.assertFalse(out[0]['flagged'])

    def test_a_broker_over_the_floor_still_escalates(self):
        from integrations import graphite_panels_views as panels
        out = panels._shape_broker_lr(self._row(book=800_377.0, incurred=1_235_227.0))
        self.assertFalse(out[0]['below_escalation_size'])
        self.assertTrue(out[0]['flagged'])


class BrokerLrIdenticalNamesTest(SimpleTestCase):
    """Two agency records with the SAME name must not overwrite each other.

    Four records in Graphite carry a name that already exists. The rows here are
    keyed by broker name in Python, so a query grouped by agency id returns two
    rows for the same key and the second silently replaces the first — one
    record's money simply vanishes. Letsema showed a P10,699 book instead of
    P390,049 that way.
    """

    def test_every_read_groups_by_name_so_the_database_adds_duplicates_up(self):
        seen = []

        def spy(sql, params=None, *, limit=500):
            if 'GROUP BY' in sql:
                # the LAST one is the statement's own grouping; an earlier one
                # belongs to the premium de-duplication subquery.
                seen.append(sql[sql.rindex('GROUP BY'):])
            return _fake_query(sql, params, limit=limit)

        with mock.patch.object(live, 'is_configured', return_value=True), \
                mock.patch.object(live, 'query', spy):
            live.build('broker_lr')

        self.assertEqual(len(seen), 4, seen)
        for clause in seen:
            self.assertNotIn('ag.id', clause,
                             'grouped by agency id — identically-named records '
                             'will overwrite each other and lose money')
