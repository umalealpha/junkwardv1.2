"""Tests for the broker attribution link used by Broker Analysis + Claims Registry.

A broker is the intermediary recorded ON THE POLICY (`policies.agency_id`), not
the agency the selling USER happens to sit under (`policies.agent_id` ->
`users.agency_id`). On the live replica the two disagree for 21,059 policies and
whole brokers vanish under the agent path — Spectrum, Botshabelo, Minet, Marsh
and UTL each show a real book via `policies.agency_id` and ZERO via the agent.

`run_select` is faked. The fake is not a SQL engine: it reads which link the
query asks for and answers with the result set that link really returns on the
replica (agency link -> Spectrum present; agent link -> Spectrum absent, its
premium absorbed into the direct channel). So a query on the wrong link gets the
wrong-link data, exactly as production does, and the assertions below are about
the report's OUTPUT, not its SQL text.
"""
from unittest import mock

from django.test import SimpleTestCase

from aware.modes import broker_analysis as ba
from aware.modes import claims_registry as cr

# Which link does this statement use to reach `agencies`?
_AGENT_LINK = 'u.agency_id=a.id'
_AGENCY_LINK = 'p.agency_id=a.id'


def _link_of(sql: str) -> str:
    if _AGENCY_LINK in sql:
        return 'agency'
    if _AGENT_LINK in sql:
        return 'agent'
    return 'none'


class _FakeReplica:
    """Answers the four Broker Analysis queries per the link they ask for."""

    def __call__(self, sql):
        link = _link_of(sql)
        if 'FROM policies p' in sql and 'active_pol' in sql:
            return [], self._inforce(link)
        if 'policy_actions' in sql:
            return [], self._nb(link)
        if 'net_paid' in sql:
            return [], self._paid(link)
        if 'os_reserve' in sql:
            return [], self._outstanding(link)
        if 'agency_id IS NULL' in sql:
            return [], [{'pol': 5677, 'gwp': 1_307_941}]
        raise AssertionError(f'unexpected statement: {sql[:80]}')

    # Spectrum: 200 policies on its own agency_id, none via the agent path,
    # where its premium lands under the in-house direct channel instead.
    def _inforce(self, link):
        if link == 'agency':
            return [
                {'broker': 'Alpha Direct Insurance Co. (Pty) Ltd',
                 'active_pol': 2514, 'inforce_gwp': 29_362_758},
                {'broker': 'Spectrum Insurance Brokers',
                 'active_pol': 200, 'inforce_gwp': 612_064},
            ]
        return [
            {'broker': 'Alpha Direct Insurance Co. (Pty) Ltd',
             'active_pol': 2714, 'inforce_gwp': 29_974_822},
        ]

    def _nb(self, link):
        if link == 'agency':
            return [{'broker': 'Spectrum Insurance Brokers',
                     'nb_cnt': 12, 'nb_gwp': 90_000}]
        return []

    def _paid(self, link):
        if link == 'agency':
            return [{'broker': 'Spectrum Insurance Brokers',
                     'net_paid': 3_281_439}]
        return []

    def _outstanding(self, link):
        if link == 'agency':
            return [{'broker': 'Spectrum Insurance Brokers',
                     'os_reserve': 400_000}]
        return []


class BrokerAnalysisLinkTest(SimpleTestCase):
    def _report(self):
        with mock.patch.object(ba, 'run_select', _FakeReplica()):
            return ba.broker_report(None)

    def test_broker_is_taken_from_the_policys_own_agency(self):
        """Spectrum's book must appear. Via the agent link it does not exist."""
        rows = self._report()['brokers']
        names = [r['broker'] for r in rows]
        self.assertIn('Spectrum Insurance Brokers', names,
                      'broker attributed via the sales agent, not the policy')
        spectrum = next(r for r in rows if r['broker'] == 'Spectrum Insurance Brokers')
        self.assertEqual(spectrum['active_pol'], 200)
        self.assertEqual(spectrum['inforce_gwp'], 612_064)

    def test_broker_claims_follow_the_same_link_as_the_premium(self):
        """Paid + outstanding must land on the broker, not on the direct channel."""
        spectrum = next(r for r in self._report()['brokers']
                        if r['broker'] == 'Spectrum Insurance Brokers')
        self.assertEqual(spectrum['net_paid_12m'], 3_281_439)
        self.assertEqual(spectrum['outstanding'], 400_000)

    def test_broker_premium_is_not_absorbed_into_the_direct_channel(self):
        rep = self._report()
        self.assertEqual(rep['channel']['broker']['gwp'], 612_064)
        self.assertEqual(rep['channel']['direct']['gwp'], 29_362_758)

    def test_policies_with_no_broker_are_declared_not_dropped(self):
        """Switching the key drops NULL-agency policies out of every bucket.

        They must be stated, so the direct/broker split is read against a
        denominator the reader can see rather than one that quietly shrank.
        """
        rep = self._report()
        self.assertEqual(rep['totals']['unattributed_pol'], 5677)
        self.assertEqual(rep['totals']['unattributed_gwp'], 1_307_941)
        self.assertTrue(
            any('carry no broker on the record' in n for n in rep['notes']),
            'the unattributed book is not disclosed to the reader')

    def test_direct_channel_exclusion_still_applies(self):
        """The in-house channels must stay out of the broker list."""
        names = [r['broker'] for r in self._report()['brokers']]
        self.assertNotIn('Alpha Direct Insurance Co. (Pty) Ltd', names)


class ClaimsRegistryLinkTest(SimpleTestCase):
    """The registry's broker column must come from the claim's POLICY."""

    _CLAIM = {'claim_number': 'CLM-1', 'policyNumber': 'POL-1', 'status': 'Open',
              'claim_sub_status': '', 'reported_date': '2026-01-05',
              'created_at': '2026-01-05', 'paid': 0, 'outstanding': 250_000}

    def _fake(self, sql):
        if 'GROUP BY status' in sql:
            return [], [{'status': 'Open', 'n': 1}]
        if 'AS v' in sql:
            return [], [{'v': 0}]
        broker = ('Marsh Botswana (PTY) LTD'
                  if _link_of(sql) == 'agency' else None)
        return [], [dict(self._CLAIM, broker=broker)]

    def test_registry_broker_comes_from_the_policy(self):
        with mock.patch.object(cr, 'run_select', self._fake):
            rows = cr.claims_registry_report(None)['rows']
        self.assertEqual(rows[0]['broker'], 'Marsh Botswana (PTY) LTD',
                         'claim broker resolved via the agent, not the policy')


class TopDomLinkTest(SimpleTestCase):
    """Top-50 Dom shows a `broker` column — same rule as everywhere else."""

    def test_top_dom_broker_comes_from_the_policy(self):
        from aware.modes import top_dom as td
        captured = {}

        def fake(sql):
            captured['link'] = _link_of(sql)
            return ([], [])

        with mock.patch.object(td, 'run_select', fake), \
                mock.patch.object(td, 'mask_rows', lambda c, r: r):
            td.top_domestic_report(None)
        self.assertEqual(captured['link'], 'agency',
                         'Top Dom broker resolved via the agent, not the policy')
