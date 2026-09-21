"""Splitting the gate across runners must not lose an app (CFO 2026-07-26).

The gate used to name an explicit app allow-list, and 437 of 1370 tests across
19 apps sat off it — that is how 11 tests stayed red on main for weeks, and how
a staff-loan crash and the balance-sheet-balances regression went unnoticed. The
CFO widened it to the whole suite for that reason.

Sharding could quietly undo that: an app in nobody's shard is an app nobody
tests, and it would look exactly like a green build. These hold the line —
every installed local app lands in exactly one shard, for any shard count.
"""
from django.test import SimpleTestCase

from ops.ci_shard import _measured, local_app_labels, shards, test_count, weight


class ShardCoverageTests(SimpleTestCase):
    def test_every_local_app_is_in_some_shard(self):
        """The allow-list hole, re-armed as a test."""
        for n in (1, 2, 3, 4, 5, 8):
            covered = {label for bucket in shards(n) for label in bucket}
            missing = set(local_app_labels()) - covered
            self.assertEqual(missing, set(),
                             f'with {n} shards these apps would never be tested: '
                             f'{sorted(missing)}')

    def test_no_app_is_tested_twice(self):
        """Duplication is not a safety hole, but it is a runner paying twice."""
        for n in (2, 4, 8):
            flat = [label for bucket in shards(n) for label in bucket]
            self.assertEqual(len(flat), len(set(flat)),
                             f'with {n} shards an app is in two shards at once')

    def test_the_labels_come_from_the_registry_not_a_hand_written_list(self):
        """If this ever reads a literal list, a new app silently stops being
        gated — which is the whole failure this file exists to prevent."""
        self.assertIn('core', local_app_labels())
        self.assertGreater(len(local_app_labels()), 20)

    def test_a_single_shard_is_the_whole_suite(self):
        self.assertEqual(sorted(shards(1)[0]), sorted(local_app_labels()))

    def test_shards_are_not_wildly_uneven(self):
        """One runner carrying most of the suite is the split doing nothing."""
        loads = [sum(weight(x) for x in b) for b in shards(4)]
        self.assertGreater(min(loads), 0, 'a shard has no tests at all')
        self.assertLess(max(loads), sum(loads) * 0.45,
                        f'one shard carries most of the suite: {loads}')


class WeightTests(SimpleTestCase):
    """Balancing counts was not enough (2026-09-09).

    The count split was near-perfect — 1656/1653/1653/1652 — and the runners
    still finished five minutes apart: 14m06 / 10m11 / 11m38 / 09m02. hris
    averages 0.28s a test, integrations 0.06s. So the weights are measured
    seconds now, not counts.
    """

    def test_the_measured_weights_file_is_readable(self):
        self.assertGreater(len(_measured()), 20,
                           'ops/ci_shard_weights.json is missing or empty — the '
                           'split silently falls back to counts, which is what '
                           'left one runner five minutes behind')

    def test_a_measured_app_uses_its_measured_seconds(self):
        m = _measured()
        heaviest = max(m, key=m.get)
        self.assertEqual(weight(heaviest), float(m[heaviest]))

    def test_an_unmeasured_app_still_weighs_something(self):
        """A new app must not weigh zero and land on an already-full runner.
        Coverage is guaranteed elsewhere; this is about not wrecking balance."""
        unknown = 'an_app_that_does_not_exist_yet'
        self.assertNotIn(unknown, _measured())
        with self.assertRaises(LookupError):
            test_count(unknown)          # not installed, so it cannot be sharded
        self.assertGreater(weight(max(_measured(), key=_measured().get)), 0)

    def test_the_heaviest_app_sets_the_floor_and_we_know_it(self):
        """hris alone is ~412s. No number of shards goes below one app, so if
        the gate must get faster than that, hris is the thing to split — not
        the shard count."""
        m = _measured()
        self.assertLess(max(m.values()), sum(m.values()) * 0.5,
                        'one app is now half the suite — split that app, not the runners')
