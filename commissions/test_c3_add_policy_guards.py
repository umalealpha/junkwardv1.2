"""C3 — Add Policy: Graphite prefill, and the two blocks (board item [C3]).

Why these exist, precisely:

1. BrokerPolicy's uniqueness is (broker, policy_number, period_label). That
   means the SAME policy number could be added under TWO different brokers and
   nothing anywhere would object — both brokers would then be commissioned on
   the same premium. C3 calls this out ("Reject a duplicate Policy No already
   assigned to another broker and show which broker currently holds it") and the
   database constraint cannot express it, so it has to be a check with a test.

2. C3 also says insertion must be BLOCKED when Graphite cannot find the policy.
   The trap is conflating "Graphite says no such policy" with "Graphite could
   not be reached" — if both block, a Graphite outage silently stops all data
   entry and reads to Finance as a data problem. `policy_lookup` returns None
   for unreachable and {} for genuinely-missing; these pin that the two differ.
"""
from unittest import mock

from django.contrib.auth.models import User
from rest_framework.test import APIClient, APITestCase

from commissions.models import Broker, BrokerPolicy

LOOKUP = 'realpay.graphite_feed.policy_lookup'
GRAPHITE_ROW = {
    'policy_number': 'COMG2024101598', 'insured_name': 'A Test Client',
    'agency': 'Kgare', 'premium': 21155.25, 'annual_premium': 21155.25,
}


class C3Base(APITestCase):

    @classmethod
    def setUpTestData(cls):
        cls.redhill = Broker.objects.create(name='Redhill')
        cls.kgare = Broker.objects.create(name='Kgare')
        # A superuser passes CanUseBrokerModule without seeding the C8 role,
        # which keeps these tests about C3 and not about C8's permissions.
        cls.user = User.objects.create_superuser('root', 'root@test.example', 'x')

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _add(self, broker, policy='COMG2024101598', period='2026-08', **extra):
        return self.client.post(
            f'/api/v1/commissions/brokers/{broker.id}/policies/',
            {'policy_number': policy, 'period_label': period, **extra},
            format='json')


class OnePolicyBelongsToOneBroker(C3Base):

    @mock.patch(LOOKUP, return_value=GRAPHITE_ROW)
    def test_the_same_policy_cannot_be_added_to_a_second_broker(self, _lk):
        self.assertEqual(self._add(self.redhill).status_code, 201)
        r = self._add(self.kgare)
        self.assertEqual(r.status_code, 409, r.content)
        self.assertEqual(r.json()['held_by']['name'], 'Redhill',
                         'The refusal must NAME the broker that already holds it.')

    @mock.patch(LOOKUP, return_value=GRAPHITE_ROW)
    def test_the_clash_is_case_insensitive(self, _lk):
        self.assertEqual(self._add(self.redhill, policy='comg2024101598').status_code, 201)
        self.assertEqual(self._add(self.kgare, policy='COMG2024101598').status_code, 409)

    @mock.patch(LOOKUP, return_value=GRAPHITE_ROW)
    def test_the_same_broker_may_still_add_another_month(self, _lk):
        self.assertEqual(self._add(self.redhill, period='2026-08').status_code, 201)
        # Same broker, next month — normal, and must NOT be refused.
        self.assertIn(self._add(self.redhill, period='2026-09').status_code, (200, 201))


class GraphiteMustKnowThePolicy(C3Base):

    @mock.patch(LOOKUP, return_value={})
    def test_a_policy_graphite_has_never_heard_of_is_refused(self, _lk):
        r = self._add(self.redhill, policy='NOTREAL123')
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn('Graphite has no policy', r.json()['detail'])
        self.assertFalse(BrokerPolicy.objects.filter(policy_number='NOTREAL123').exists())

    @mock.patch(LOOKUP, return_value=None)
    def test_an_unreachable_graphite_does_NOT_block_data_entry(self, _lk):
        # None = could not check. Refusing here would stop Finance working
        # during any Graphite outage and look like a data fault.
        r = self._add(self.redhill)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertFalse(r.json()['graphite_verified'],
                         'A row added without a Graphite check must be flagged unverified.')

    @mock.patch(LOOKUP, return_value=GRAPHITE_ROW)
    def test_a_verified_row_says_so(self, _lk):
        self.assertTrue(self._add(self.redhill).json()['graphite_verified'])


class PolicyLookupEndpoint(C3Base):

    URL = '/api/v1/commissions/brokers/policy-lookup/'

    @mock.patch(LOOKUP, return_value=GRAPHITE_ROW)
    def test_it_prefills_from_graphite(self, _lk):
        r = self.client.get(self.URL, {'policy_number': 'COMG2024101598'})
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertTrue(body['found'])
        self.assertTrue(body['reachable'])
        self.assertEqual(body['policy']['insured_name'], 'A Test Client')

    def test_missing_and_unreachable_are_different_answers(self):
        with mock.patch(LOOKUP, return_value={}):
            missing = self.client.get(self.URL, {'policy_number': 'NOPE'}).json()
        self.assertTrue(missing['reachable'])
        self.assertFalse(missing['found'])
        with mock.patch(LOOKUP, return_value=None):
            down = self.client.get(self.URL, {'policy_number': 'NOPE'}).json()
        self.assertFalse(down['reachable'])
        self.assertFalse(down['found'])
        self.assertNotEqual(missing['detail'], down['detail'],
                            'An outage must not be reported as a missing policy.')

    @mock.patch(LOOKUP, return_value=GRAPHITE_ROW)
    def test_it_warns_before_typing_that_another_broker_holds_it(self, _lk):
        self._add(self.redhill)
        body = self.client.get(self.URL, {'policy_number': 'COMG2024101598'}).json()
        self.assertEqual(body['held_by']['name'], 'Redhill')

    def test_it_requires_a_policy_number(self):
        self.assertEqual(self.client.get(self.URL).status_code, 400)

    def test_it_requires_a_login(self):
        self.assertIn(APIClient().get(self.URL).status_code, (401, 403))


class AGraphiteOutageDoesNotBlockDataEntry(C3Base):
    """The tests above mock `policy_lookup` itself, so they can never see whether
    policy_lookup survives a REAL outage. It did not: `graphite_ro.query()` never
    returns None — it returns a list or RAISES — so a connection failure came out
    of the endpoint as a 500 and refused every row, which is the exact behaviour
    the tri-state was written to prevent. These patch the DB layer instead.
    """

    def _outage(self):
        return mock.patch('integrations.graphite_ro.query',
                          side_effect=RuntimeError('connection refused'))

    def _configured(self):
        return mock.patch('integrations.graphite_ro.is_configured', return_value=True)

    def test_adding_a_policy_still_works_while_graphite_is_down(self):
        with self._configured(), self._outage():
            r = self._add(self.redhill)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertFalse(r.json()['graphite_verified'])

    def test_the_lookup_reports_unreachable_rather_than_exploding(self):
        with self._configured(), self._outage():
            r = self.client.get('/api/v1/commissions/brokers/policy-lookup/',
                                {'policy_number': 'COMG2024101598'})
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertFalse(body['reachable'])
        self.assertFalse(body['found'])

    def test_an_outage_is_never_reported_as_a_missing_policy(self):
        # The whole point: "Graphite has no such policy" BLOCKS, "Graphite is
        # down" does not. Conflating them stops Finance working during an outage.
        with self._configured(), self._outage():
            down = self.client.get('/api/v1/commissions/brokers/policy-lookup/',
                                   {'policy_number': 'X'}).json()
        self.assertNotIn('has no policy', down.get('detail', ''))
