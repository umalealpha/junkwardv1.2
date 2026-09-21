"""integrations/test_graphite_lookup.py — the phone "look up a client" reads.

The Graphite replica is MOCKED (`integrations.graphite_lookup_views.query`);
nothing here touches Graphite. Run: manage.py test integrations.test_graphite_lookup
"""
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

SEARCH = '/api/v1/graphite/search/'
POLICY = '/api/v1/graphite/policy/'

CLAIM_ROW = {'claim_id': 77, 'claim_number': 'G2026004594', 'status': 'Open', 'claim_type': 'Motor',
             'incident_date': '2026-08-28', 'reported_date': '2026-08-30 10:00:00', 'created_at': '2026-08-30 09:00:00',
             'policyNumber': 'COMG2025189299', 'customer_name': 'Bonanza Equipment'}
MONEY_ROW = {'claim_id': 77, 'paid_amt': None, 'outstanding_amt': 12500}
POLICY_ROW = {'policy_id': 5, 'customer_id': 9, 'policyNumber': 'COMG2025189299', 'status': 1, 'premium': 1200.5,
              'annual_premium': 14406, 'premium_freq': 1, 'term_start_date': '2025-07-01', 'term_end_date': '2026-06-30',
              'business_name': 'Bonanza Equipment', 'product_name': 'Commercial Combined', 'customer_name': '',
              'agent_name': 'Kago Broker', 'broker': 'Kago Brokers'}
# Columns brain_ro is GRANTED on claims (SHOW GRANTS, 5-Sep-2026). Anything else 1143s live.
CLAIMS_GRANTED = {'id', 'claim_number', 'incident_date', 'created_at', 'reported_date', 'claim_type', 'status', 'policy_id'}


class GraphiteLookupTests(TestCase):
    def setUp(self):
        # Grant discovery is cached per process — start every test from "base grants only".
        from integrations import graphite_lookup_views as v
        v._grant_cache.update(at=0.0, tables=set(), columns={})
        self._grants = mock.patch.object(v, '_grants', return_value=(set(), {}))
        self._grants.start(); self.addCleanup(self._grants.stop)
        from core.models import UserProfile
        self.staff = User.objects.create_user('claims.person', email='cp@alphadirect.co.bw', password='x')
        UserProfile.objects.get_or_create(user=self.staff, defaults={'title': UserProfile.Title.CLAIMS_TEAM_LEADER, 'is_active': True})
        self.c = APIClient()
        self.c.force_authenticate(self.staff)

    def test_anonymous_is_refused(self):
        self.assertEqual(APIClient().get(SEARCH + '?q=Bonanza').status_code, 401)

    def test_read_only_qc_identity_is_refused(self):
        from core.models import UserProfile
        from core.screenshot_bot import READ_ONLY_USERNAMES
        name = sorted(READ_ONLY_USERNAMES)[0]
        bot = User.objects.create_user(name, email=f'{name}@example.com', password='x')
        UserProfile.objects.get_or_create(user=bot, defaults={'title': UserProfile.Title.ACCOUNTANT, 'is_active': True})
        c = APIClient(); c.force_authenticate(bot)
        with mock.patch('integrations.graphite_lookup_views.query') as q:
            self.assertEqual(c.get(SEARCH + '?q=Bonanza').status_code, 403)
            self.assertEqual(c.get(POLICY + '?policy=COMG2025189299').status_code, 403)
            self.assertEqual(q.call_count, 0)

    def test_short_query_is_refused_before_touching_graphite(self):
        with mock.patch('integrations.graphite_lookup_views.query') as q:
            self.assertEqual(self.c.get(SEARCH + '?q=Bo').status_code, 400)
            self.assertEqual(q.call_count, 0)

    def test_a_number_searches_number_columns_and_a_name_searches_names(self):
        with mock.patch('integrations.graphite_lookup_views.query', return_value=[]) as q:
            self.c.get(SEARCH + '?q=G2026004594')
            sqls = ' | '.join(call.args[0] for call in q.call_args_list)
            self.assertIn('c.claim_number LIKE', sqls)
            self.assertNotIn('firstName', sqls.split('WHERE', 1)[1].split('ORDER')[0])
            q.reset_mock()
            self.c.get(SEARCH + '?q=Bonanza')
            sqls = ' | '.join(call.args[0] for call in q.call_args_list)
            self.assertIn("business_name LIKE", sqls)
            for call in q.call_args_list:
                self.assertTrue(all(p == '%Bonanza%' for p in call.args[1]), 'parameterised, never string-built')

    def test_rows_are_mapped_and_reserve_absent_is_not_zero(self):
        def fake(sql, params=None, **kw):
            if 'claim_reserves_coverages' in sql:
                self.assertEqual(params, [77, 77], 'money is fetched separately, by claim id')
                return [MONEY_ROW]
            return [CLAIM_ROW] if 'FROM claims' in sql else [POLICY_ROW]
        with mock.patch('integrations.graphite_lookup_views.query', side_effect=fake):
            r = self.c.get(SEARCH + '?q=Bonanza')
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        cl = body['claims'][0]
        self.assertEqual(cl['status_label'], 'Open, being worked on')
        self.assertEqual(cl['outstanding'], '12500.00')
        self.assertIsNone(cl['paid'], 'no payment rows → None, never 0')
        self.assertEqual(cl['reported'], '2026-08-30')
        self.assertEqual(cl['date_of_loss'], '2026-08-28')
        po = body['policies'][0]
        self.assertEqual(po['status_label'], 'Active')
        self.assertEqual(po['customer_name'], 'Bonanza Equipment', 'business name fills in when no person name')
        self.assertEqual(po['premium'], '1200.50')
        self.assertEqual(po['premium_freq'], 'Monthly')
        self.assertEqual(po['agent'], 'Kago Broker')
        self.assertEqual(po['product'], 'Commercial Combined')
        self.assertEqual(po['broker'], 'Kago Brokers')
        # no PII columns are ever selected
        for k in body['claims'][0].keys() | body['policies'][0].keys():
            self.assertNotRegex(k, r'omang|phone|email|address|bank|account')

    def test_policy_card(self):
        def fake(sql, params=None, **kw):
            if 'brain_customer_kyc' in sql:
                self.assertEqual(params, [9], 'KYC is looked up by the policy\'s customer_id')
                return [{'status': 'Compliant'}]
            if 'SELECT balance' in sql:
                return [{'balance': 350}]
            if 'claim_reserves_coverages' in sql:
                return [MONEY_ROW]
            if 'FROM claims' in sql:
                return [CLAIM_ROW]
            return [POLICY_ROW]
        with mock.patch('integrations.graphite_lookup_views.query', side_effect=fake):
            r = self.c.get(POLICY + '?policy=COMG2025189299')
        self.assertEqual(r.status_code, 200, r.content)
        b = r.json()
        self.assertEqual(b['policy_number'], 'COMG2025189299')
        self.assertEqual(b['kyc_status'], 'Compliant')
        self.assertEqual(b['balance'], '350.00')
        self.assertEqual(len(b['claims']), 1)
        with mock.patch('integrations.graphite_lookup_views.query', return_value=[]):
            self.assertEqual(self.c.get(POLICY + '?policy=NOPE').status_code, 404)
        self.assertEqual(self.c.get(POLICY).status_code, 400)

    def test_graphite_down_is_a_readable_502(self):
        with mock.patch('integrations.graphite_lookup_views.query', side_effect=RuntimeError('replica gone')):
            r = self.c.get(SEARCH + '?q=Bonanza')
        self.assertEqual(r.status_code, 502)
        self.assertIn('Graphite did not answer', r.json()['detail'])

    def test_a_refused_side_table_never_fails_the_search(self):
        """LIVE 5-Sep-2026: a side table the replica login may not read must never
        take the search down — the figures show as not in the data instead."""
        def fake(sql, params=None, **kw):
            if 'claim_reserves_coverages' in sql or 'brain_customer_kyc' in sql:
                raise RuntimeError("(1142, \"SELECT command denied to user 'brain_ro'\")")
            if 'SELECT balance' in sql:
                return [{'balance': 0}]
            return [CLAIM_ROW] if 'FROM claims' in sql else [POLICY_ROW]
        with mock.patch('integrations.graphite_lookup_views.query', side_effect=fake):
            r = self.c.get(SEARCH + '?q=Bonanza')
            self.assertEqual(r.status_code, 200, r.content)
            cl = r.json()['claims'][0]
            self.assertIsNone(cl['outstanding']); self.assertIsNone(cl['paid'])
            self.assertEqual(cl['status_label'], 'Open, being worked on')
            card = self.c.get(POLICY + '?policy=COMG2025189299')
            self.assertEqual(card.status_code, 200, card.content)
            self.assertIsNone(card.json()['kyc_status'])
            self.assertEqual(len(card.json()['claims']), 1)

    def test_claims_select_touches_only_granted_columns(self):
        """1143 live on 5-Sep: brain_ro may read eight columns of `claims`. With no extra
        grant the select must stay inside them — a stray column is a live 502 for everyone."""
        import re
        from integrations.graphite_lookup_views import _claim_select
        select = _claim_select().split('FROM claims')[0]
        used = set(re.findall(r'\bc\.(\w+)', select))
        self.assertTrue(used, 'sanity: the claim select reads c.<column>s')
        self.assertLessEqual(used, CLAIMS_GRANTED, f'not granted to brain_ro: {used - CLAIMS_GRANTED}')
        self.assertNotIn('new_claims', select)

    def test_extra_detail_appears_the_moment_the_grant_exists(self):
        """CFO 5-Sep: code it now, ask TheRiskCo to widen the grant. When the login may
        read claim_sub_status / type_of_loss / vehicle_plate and the KYC view, the
        select and the card use them — no deploy needed."""
        from integrations import graphite_lookup_views as v
        self._grants.stop()
        granted = (set(), {'claims': set(CLAIMS_GRANTED) | {'claim_sub_status', 'type_of_loss', 'vehicle_plate'}})
        with mock.patch.object(v, '_grants', return_value=granted):
            sel = v._claim_select()
            for col in ('claim_sub_status', 'type_of_loss', 'vehicle_plate'):
                self.assertIn(f'c.{col}', sel)
            row = {**CLAIM_ROW, 'claim_sub_status': 'Awaiting assessor', 'type_of_loss': 'Accident', 'vehicle_plate': 'B 555 BUU'}
            def fake(sql, params=None, **kw):
                if 'claim_reserves_coverages' in sql:
                    return [MONEY_ROW]
                return [row] if 'FROM claims' in sql else [POLICY_ROW]
            with mock.patch.object(v, 'query', side_effect=fake):
                r = self.c.get(SEARCH + '?q=Bonanza').json()['claims'][0]
            self.assertEqual(r['status_label'], 'Open, being worked on · Awaiting assessor')
            self.assertEqual(r['type_of_loss'], 'Accident')
            self.assertEqual(r['vehicle_plate'], 'B 555 BUU')
        # KYC: the per-policy view is preferred once granted as a whole table.
        with mock.patch.object(v, '_grants', return_value=({'v_policy_kyc_status'}, {})):
            seen = []
            def fake2(sql, params=None, **kw):
                seen.append(sql)
                if 'v_policy_kyc_status' in sql:
                    return [{'status': 'Compliant'}]
                if 'SELECT balance' in sql:
                    return [{'balance': 0}]
                if 'claim_reserves_coverages' in sql:
                    return []
                return [CLAIM_ROW] if 'FROM claims' in sql else [POLICY_ROW]
            with mock.patch.object(v, 'query', side_effect=fake2):
                card = self.c.get(POLICY + '?policy=COMG2025189299').json()
            self.assertEqual(card['kyc_status'], 'Compliant')
            self.assertTrue(any('v_policy_kyc_status' in s for s in seen))
            self.assertFalse(any('brain_customer_kyc' in s for s in seen))

    def test_grant_discovery_failure_falls_back_to_the_base_columns(self):
        from integrations import graphite_lookup_views as v
        self._grants.stop()
        v._grant_cache.update(at=0.0, tables=set(), columns={})
        with mock.patch.object(v, 'query', side_effect=RuntimeError('information_schema refused')):
            self.assertEqual(v._grants(), (set(), {}))
            self.assertNotIn('claim_sub_status', v._claim_select())


BRIEF = '/api/v1/graphite/claim-brief/'


class ClaimBriefTests(TestCase):
    """The DeepSeek claim brief. Replica AND the model are mocked."""

    def setUp(self):
        from core.models import UserProfile
        from integrations import graphite_lookup_views as v
        v._grant_cache.update(at=0.0, tables=set(), columns={})
        self._g = mock.patch.object(v, '_grants', return_value=(set(), {})); self._g.start(); self.addCleanup(self._g.stop)
        self.staff = User.objects.create_user('claims.brief', email='cb@alphadirect.co.bw', password='x')
        UserProfile.objects.get_or_create(user=self.staff, defaults={'title': UserProfile.Title.CLAIMS_TEAM_LEADER, 'is_active': True})
        self.c = APIClient(); self.c.force_authenticate(self.staff)

    @staticmethod
    def _fake(sql, params=None, **kw):
        if 'claim_reserves_coverages' in sql and 'coverage_name' in sql:
            return [{'coverage_name': 'Own damage', 'reserve_amt': 45000, 'payment_amt': 12500, 'balance': 32500, 'write_off': 0, 'is_payment_voided': 0, 'created_at': '2026-09-01'}]
        if 'claim_reserves_coverages' in sql:
            return [MONEY_ROW]
        if 'brain_customer_kyc' in sql:
            return [{'status': 'Compliant'}]
        if 'SELECT balance' in sql:
            return [{'balance': 0}]
        if 'FROM claims' in sql:
            if params and len(params) == 2 and params[1] == 'G2026004594':
                return []                         # other claims on the policy
            return [{**CLAIM_ROW, 'customer_name': 'Boipuso Pelo'}]
        return [{**POLICY_ROW, 'customer_name': 'Boipuso Pelo', 'business_name': ''}]

    def test_brief_is_written_from_masked_facts_and_capped(self):
        from taskboard.models import PaymentRequest
        PaymentRequest.objects.create(ref='PAY/ADIC/2026/09/05/0007', entity='ADIC', category='claim', claim_payee_type='client',
                                      subject='Claim G2026004594 settlement', payee='Boipuso Pelo', total=12500,
                                      status='pending_cfo', created_by=self.staff)
        long_answer = ' '.join(['word'] * 400) + '. Tail.'
        with mock.patch('integrations.graphite_lookup_views.query', side_effect=self._fake), \
             mock.patch('core.ai_assist.deepseek_complete', return_value=long_answer) as ds:
            r = self.c.post(BRIEF, {'claim_number': 'G2026004594', 'question': 'Should we pay?'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(body['engine'], 'DeepSeek')
        self.assertLessEqual(body['words'], 230, 'about 200 words, hard-capped')
        prompt = ds.call_args.args[0]
        self.assertNotIn('Boipuso Pelo', prompt, 'a person is reduced to initials before leaving Omni')
        self.assertIn('B. P.', prompt)
        self.assertIn('G2026004594', prompt)
        self.assertIn('BWP 12500', prompt, 'money survives the PII sweep in whole pula')
        self.assertIn('OMNI PAYMENT REQUESTS FOR THIS CLAIM:', prompt)
        self.assertIn('PAY/ADIC/2026/09/05/0007', prompt)
        self.assertIn('Should we pay?', prompt)
        self.assertIn('Own damage', prompt)
        self.assertEqual(ds.call_args.kwargs['system_prompt'][:22], 'You are a senior short')
        self.assertEqual(body['facts']['claim']['claim_number'], 'G2026004594')

    def test_falls_back_to_the_backup_engine_when_deepseek_is_down(self):
        from core.ai_assist import DeepSeekUnavailable
        with mock.patch('integrations.graphite_lookup_views.query', side_effect=self._fake), \
             mock.patch('core.ai_assist.deepseek_complete', side_effect=DeepSeekUnavailable('no key')), \
             mock.patch('core.ai_assist.reasoning_complete', return_value='**Claim** fine.') as rc:
            r = self.c.post(BRIEF, {'claim_number': 'G2026004594'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['engine'], 'backup engine')
        self.assertEqual(rc.call_args.kwargs.get('feature'), 'claim_brief')

    def test_no_engine_gives_the_facts_with_a_readable_503(self):
        from core.ai_assist import DeepSeekUnavailable
        with mock.patch('integrations.graphite_lookup_views.query', side_effect=self._fake), \
             mock.patch('core.ai_assist.deepseek_complete', side_effect=DeepSeekUnavailable('no key')), \
             mock.patch('core.ai_assist.reasoning_complete', side_effect=DeepSeekUnavailable('all down')):
            r = self.c.post(BRIEF, {'claim_number': 'G2026004594'}, format='json')
        self.assertEqual(r.status_code, 503)
        self.assertIn('facts', r.json())

    def test_gates_and_bad_input(self):
        from core.models import UserProfile
        from core.screenshot_bot import READ_ONLY_USERNAMES
        self.assertEqual(APIClient().post(BRIEF, {'claim_number': 'G1'}, format='json').status_code, 401)
        name = sorted(READ_ONLY_USERNAMES)[0]
        bot = User.objects.create_user(name, email=f'{name}@example.com', password='x')
        UserProfile.objects.get_or_create(user=bot, defaults={'title': UserProfile.Title.ACCOUNTANT, 'is_active': True})
        bc = APIClient(); bc.force_authenticate(bot)
        self.assertEqual(bc.post(BRIEF, {'claim_number': 'G2026004594'}, format='json').status_code, 403)
        self.assertEqual(self.c.post(BRIEF, {}, format='json').status_code, 400)
        with mock.patch('integrations.graphite_lookup_views.query', return_value=[]):
            self.assertEqual(self.c.post(BRIEF, {'claim_number': 'NOPE'}, format='json').status_code, 404)

    def test_word_cap_cuts_at_a_sentence(self):
        from integrations.graphite_lookup_views import _cap_words
        text = ('Alpha beta gamma delta. ' * 60).strip()
        out = _cap_words(text, 50)
        self.assertLessEqual(len(out.split()), 50)
        self.assertTrue(out.endswith('.'))
        self.assertEqual(_cap_words('Short brief.', 50), 'Short brief.')
