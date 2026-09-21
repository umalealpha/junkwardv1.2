"""Tests for the Broker Scorecard aware mode (CFO 2026-08-22).

Pure-logic tests: broker_report and claims_registry_report (which hit the
Graphite read replica) are mocked, so this needs no database and pins the
scorecard's own behaviour — duplicate-agency merge, Net = premium − paid,
worst-first ordering, the loss-maker count, the Top-20 cap, and that NO
loss-ratio field leaks back in.
"""
from unittest import mock

from django.test import SimpleTestCase

from aware.modes import broker_scorecard as sc


def _base():
    return {
        'brokers': [
            # Acme appears twice (split agency) — same name, differing only by
            # case + spacing, exactly like the real Brightside duplicate. Must
            # merge to ONE row under the normaliser (case/space/suffix stripped).
            {'broker': 'Acme Brokers (Pty) Ltd', 'active_pol': 10, 'inforce_gwp': 1_000_000,
             'nb_cnt': 1, 'nb_gwp': 0, 'net_paid_12m': 200_000, 'outstanding': 50_000},
            {'broker': 'ACME  BROKERS (Pty) Ltd', 'active_pol': 5, 'inforce_gwp': 500_000,
             'nb_cnt': 0, 'nb_gwp': 0, 'net_paid_12m': 100_000, 'outstanding': 0},
            # A clear money-drain: small premium, big claims paid.
            {'broker': 'BadBook Brokers', 'active_pol': 3, 'inforce_gwp': 100_000,
             'nb_cnt': 0, 'nb_gwp': 0, 'net_paid_12m': 900_000, 'outstanding': 0},
        ],
        'totals': {}, 'channel': {},
    }


def _reg():
    # Deliberately UNSORTED outstanding values so the scorecard's own ordering
    # is what's being tested, not the fixture's.
    return {
        'rows': [{'claim': f'C{i}', 'policy': f'POL{i}', 'status': 'Open',
                  'broker': 'X', 'paid': 0, 'outstanding': (i * 37) % 100} for i in range(30)],
        'open_count': 30, 'total_outstanding': 99_999,
    }


class BrokerScorecardTest(SimpleTestCase):
    def _run(self):
        with mock.patch.object(sc, 'broker_report', return_value=_base()), \
             mock.patch.object(sc, 'claims_registry_report', return_value=_reg()):
            return sc.broker_scorecard_report(None)

    def test_duplicate_agency_is_merged(self):
        rows = self._run()['brokers']
        self.assertEqual(len(rows), 2, 'the two Acme rows must merge into one')
        acme = next(r for r in rows if 'acme' in r['broker'].lower())
        self.assertEqual(acme['inforce_gwp'], 1_500_000)   # 1.0m + 0.5m
        self.assertEqual(acme['net_paid_12m'], 300_000)    # 200k + 100k

    def test_net_is_premium_minus_paid(self):
        rows = self._run()['brokers']
        acme = next(r for r in rows if 'acme' in r['broker'].lower())
        bad = next(r for r in rows if r['broker'] == 'BadBook Brokers')
        self.assertEqual(acme['net'], 1_200_000)   # 1.5m − 0.3m (earner)
        self.assertEqual(bad['net'], -800_000)     # 0.1m − 0.9m (drain)

    def test_worst_first_and_loss_maker_count(self):
        out = self._run()
        self.assertEqual(out['brokers'][0]['broker'], 'BadBook Brokers')
        self.assertEqual(out['loss_makers'], 1)

    def test_no_loss_ratio_leaks(self):
        rows = self._run()['brokers']
        self.assertNotIn('ratio', rows[0])
        self.assertNotIn('watch', rows[0])

    def test_top_claims_capped_at_20(self):
        out = self._run()
        self.assertEqual(len(out['top_claims']), 20)

    def test_top_claims_sorted_by_outstanding_desc(self):
        vals = [c['outstanding'] for c in self._run()['top_claims']]
        self.assertEqual(vals, sorted(vals, reverse=True),
                         'top claims must be largest-outstanding first')

    def test_top_claims_carry_no_policy_number(self):
        # PII tightening: the Finance box drops the policy number.
        top = self._run()['top_claims']
        self.assertTrue(all('policy' not in c for c in top))
        self.assertTrue(all('claim' in c and 'outstanding' in c for c in top))


class BrokerScorecardSharedMergeRuleTest(SimpleTestCase):
    """The scorecard merges on the SAME rule as the loss-ratio flag (CFO 2026-09-08).

    Its old private rule stripped 't/a', '(pty)' and 'ltd' only, so it merged
    "FinSef"/"Finsef" but left Redhill split across "Hilrange Enterprises" and
    "Hildrage Enterprises" — two reports, two answers, same broker.
    """

    def test_redhill_filed_under_two_spellings_merges_to_one_row(self):
        base = {
            'brokers': [
                {'broker': 'Hilrange Enterprises (Pty) Ltd T/a Redhill Risk Solutions',
                 'active_pol': 249, 'inforce_gwp': 1_170_576, 'nb_cnt': 0, 'nb_gwp': 0,
                 'net_paid_12m': 0, 'outstanding': 0},
                {'broker': 'Hildrage Enterprises (Pty) Ltd T/a Redhill Risk Solutions',
                 'active_pol': 52, 'inforce_gwp': 293_237, 'nb_cnt': 0, 'nb_gwp': 0,
                 'net_paid_12m': 0, 'outstanding': 0},
            ],
            'totals': {}, 'channel': {},
        }
        with mock.patch.object(sc, 'broker_report', return_value=base), \
                mock.patch.object(sc, 'claims_registry_report',
                                  return_value={'rows': [], 'open_count': 0,
                                                'total_outstanding': 0}):
            rows = sc.broker_scorecard_report(None)['brokers']

        self.assertEqual(len(rows), 1, [r['broker'] for r in rows])
        self.assertEqual(rows[0]['active_pol'], 301)
        self.assertEqual(rows[0]['inforce_gwp'], 1_463_813)
