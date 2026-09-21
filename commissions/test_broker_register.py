"""Broker Commission register — the tests /fabe required before ship.

Each one pins a rule that was got wrong at least once while building this, or
that would cost money if it silently changed.
"""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from commissions import brokers as svc
from commissions.models import Broker, BrokerAlias, BrokerPolicy
from realpay import graphite_feed as gf


# ── merge_key: fold the same firm, never fold two firms ─────────────────────
class MergeKeyTests(SimpleTestCase):
    def test_a_typo_in_the_registered_name_still_folds_on_the_trading_name(self):
        """Redhill is filed under BOTH 'Hilrange' and 'Hildrage' — one letter
        apart in a name nobody reads, while the trading name is identical."""
        a = svc.merge_key('Hilrange Enterprises (Pty) Ltd T/a Redhill Risk')
        b = svc.merge_key('Hildrage Enterprises (Pty) Ltd T/a Redhill Risk')
        self.assertEqual(a, b)
        self.assertTrue(a)

    def test_branches_fold_into_one_broker_including_the_plain_row(self):
        """Grouping, not the strict key, is what folds these: the plain
        'Dynamic Insurance Brokers (Pty) Ltd' row carries no marker of its own
        and only joins because its Gaborone and Palapye siblings do."""
        names = ['Dynamic Insurance Brokers (Pty) Ltd - Gaborone',
                 'Dynamic Insurance Brokers (Pty) Ltd - Palapye',
                 'Dynamic Insurance Brokers (Pty) Ltd']
        self.assertEqual(len(set(svc.group_agencies(names).values())), 1)

    def test_a_group_only_folds_when_the_data_says_it_is_a_variant(self):
        """The guard that stops the over-merge: neither of these carries a
        trading-as marker or a branch, so dropping the industry nouns must NOT
        be allowed to join them."""
        names = ['Botswana Insurance Brokers', 'Botswana Risk Services']
        self.assertEqual(len(set(svc.group_agencies(names).values())), 2,
                         'two different firms were folded into one broker')

    def test_a_near_miss_with_no_marker_is_left_for_a_human(self):
        names = ['Minet Botswana PTY LTD', 'Minet- Francistown']
        self.assertEqual(len(set(svc.group_agencies(names).values())), 2)

    def test_case_and_legal_form_fold_on_the_strict_key_alone(self):
        keys = {svc.merge_key(n) for n in (
            'Finsef (Pty) Ltd', 'FinSef (Pty) Ltd', 'Finsef (Pty) Ltd-Francistown')}
        self.assertEqual(len(keys), 1)

    def test_the_variant_marker_is_what_licenses_the_loose_fold(self):
        self.assertTrue(svc.has_variant_marker('Hilrange (Pty) Ltd T/a Redhill Risk'))
        self.assertTrue(svc.has_variant_marker('Dynamic Insurance Brokers - Gaborone'))
        self.assertFalse(svc.has_variant_marker('Botswana Insurance Brokers'))

    def test_two_different_firms_sharing_a_first_word_do_NOT_fold(self):
        """The over-merge that pays two brokers as one. Neither name carries a
        trading-as marker or a branch, so the industry nouns must be kept."""
        a = svc.merge_key('Botswana Insurance Brokers')
        b = svc.merge_key('Botswana Risk Services')
        self.assertNotEqual(a, b, 'two different firms folded into one broker')

    def test_direct_channels_are_not_brokers(self):
        for n in ('Unicoin', 'Unicoin Call Centre', 'Alpha Direct Insurance Co. (Pty) Ltd'):
            self.assertTrue(svc.is_direct_channel(n), n)
        self.assertFalse(svc.is_direct_channel('Letsema Insurance Brokers (Pty) Ltd'))


# ── the RealPay join key and the dirty statuses ─────────────────────────────
class PolicyNumberAndStatusTests(SimpleTestCase):
    def test_the_renewal_year_suffix_is_stripped(self):
        self.assertEqual(gf.policy_number_of('DOMG2099000001/2024'), 'DOMG2099000001')
        self.assertEqual(gf.policy_number_of('  COMG2099000002  '), 'COMG2099000002')
        self.assertEqual(gf.policy_number_of(None), '')

    def test_norm_contract_would_get_this_wrong(self):
        """Pins WHY we do not reuse the recon normaliser: it strips punctuation,
        so the suffix is glued on instead of removed."""
        from realpay.recon import _norm_contract
        raw = 'DOMG2099000001/2024'
        self.assertNotEqual(_norm_contract(raw), gf.policy_number_of(raw))

    def test_the_dirty_status_values_that_are_really_in_the_table(self):
        for raw, code, label in [
                ('S', 'S', 'Successful'), ('F', 'F', 'Failed'), ('E', 'E', 'Error'),
                ('A', 'A', 'Scheduled'), ('I', 'I', 'Cancelled'),
                ('CANCELLED', 'I', 'Cancelled'), ('SUCCESS', 'S', 'Successful'),
                ('SUCCESSFUL', 'S', 'Successful'), ('FAILED(FAILED-73)', 'F', 'Failed')]:
            self.assertEqual(gf.normalise_status(raw), (code, label), raw)

    def test_an_unrecognised_status_reads_as_unknown_never_as_successful(self):
        self.assertEqual(gf.normalise_status('WAT'), ('?', 'Unknown'))
        self.assertEqual(gf.normalise_status(''), ('?', 'Unknown'))


class PolicyStatusRankingTests(SimpleTestCase):
    """An attempt must beat a mandate row, even when the mandate row is newer."""

    def _rows(self):
        return [
            {'pn': 'COMG2099000003', 'st': 'S', 'amt': Decimal('100.00'),
             'action_date': '2026-05-10', 'reason': ''},
            {'pn': 'COMG2099000003', 'st': 'A', 'amt': Decimal('100.00'),
             'action_date': '2026-05-30', 'reason': ''},
        ]

    def test_a_success_on_the_10th_beats_a_scheduled_row_on_the_30th(self):
        with patch.object(gf.graphite_ro, 'is_configured', return_value=True), \
             patch.object(gf.graphite_ro, 'query', return_value=self._rows()):
            out = gf.policy_statuses(['COMG2099000003'])
        self.assertEqual(out['COMG2099000003']['status_label'], 'Successful')

    def test_a_scheduled_row_whose_date_has_passed_says_no_outcome_received(self):
        """Since June 2026 the corporate/domestic outcome feed has been dead, so
        every such row would otherwise read a reassuring 'Scheduled'."""
        rows = [{'pn': 'COMG2099000004', 'st': 'A', 'amt': Decimal('50.00'),
                 'action_date': '2020-01-31', 'reason': ''}]
        with patch.object(gf.graphite_ro, 'is_configured', return_value=True), \
             patch.object(gf.graphite_ro, 'query', return_value=rows):
            out = gf.policy_statuses(['COMG2099000004'])
        self.assertEqual(out['COMG2099000004']['status_label'], 'No outcome received')

    def test_an_unconfigured_bridge_returns_nothing_rather_than_zeroes(self):
        with patch.object(gf.graphite_ro, 'is_configured', return_value=False):
            self.assertEqual(gf.policy_statuses(['X']), {})
            self.assertEqual(gf.collected_in_window(['X']), {})


# ── money: VAT rounds HALF UP, never banker's rounding ──────────────────────
class MoneyRoundingTests(SimpleTestCase):
    def test_half_up_not_half_even(self):
        """0.125 is HALF UP 0.13; Python's default HALF_EVEN gives 0.12.
        Rounding is a tax decision (CFO standing order)."""
        self.assertEqual(svc._money('0.125'), Decimal('0.13'))
        self.assertEqual(svc._money('0.135'), Decimal('0.14'))

    def test_accounting_shapes_from_the_real_sheet(self):
        self.assertEqual(svc._money('1,234.56'), Decimal('1234.56'))
        self.assertEqual(svc._money('(500.00)'), Decimal('-500.00'))
        self.assertEqual(svc._money(' -   '), Decimal('0.00'))
        self.assertEqual(svc._money(None), Decimal('0.00'))


# ── the workbook parser against Rose's column headings ──────────────────────
class BrokerSheetParseTests(SimpleTestCase):
    HEADER = ['BROKER NAME', 'Policy No', 'Insured Name', 'Premium',
              'Premium (lncl. VAT and Admin Cost)', 'Amount Received (Incl. VAT & Admin Cost)',
              'Premium (Excl. VAT & Admin Cost)', 'Motor Premium (Excl. VAT)',
              'Motor Commission (Excl. VAT)', 'Non Motor Premium (Excl. VAT)',
              'Non Motor Commision (Excl. VAT)', 'Commision Payable', 'VAT']

    def test_it_reads_rose_s_layout_including_the_misspelt_commision(self):
        rows = [self.HEADER,
                ['FINSEF', 'COMG2099000005', 'A Client', 2736.18, 2736.18, 2222.37,
                 2222.37, 0, 0, 2222.37, 444.47, 444.47, 62.23],
                ['', 'Total', '', '', '', '', '', '', '', '', '', '', '']]
        out = svc.parse_broker_sheet(rows)
        self.assertEqual(len(out), 1, 'the Total row must not become a policy')
        r = out[0]
        self.assertEqual(r['policy_number'], 'COMG2099000005')
        self.assertEqual(r['insured_name'], 'A Client')
        self.assertEqual(r['commission_payable'], Decimal('444.47'))
        self.assertEqual(r['vat'], Decimal('62.23'))
        self.assertEqual(r['non_motor_commission'], Decimal('444.47'))

    def test_a_policy_name_column_is_not_mistaken_for_the_policy_number(self):
        rows = [['Policy Name', 'Policy No', 'Commision Payable'],
                ['A Client', 'DOMG2099000006', 10]]
        out = svc.parse_broker_sheet(rows)
        self.assertEqual(out[0]['policy_number'], 'DOMG2099000006')

    def test_a_sheet_with_no_policy_table_yields_nothing(self):
        self.assertEqual(svc.parse_broker_sheet([['Summary'], ['BROKER', 'TOTAL']]), [])


# ── absorb: fold two brokers without losing a row ───────────────────────────
class AbsorbTests(TestCase):
    def setUp(self):
        self.keep = Broker.objects.create(name='Minet Botswana')
        self.other = Broker.objects.create(name='Minet Francistown')
        BrokerAlias.objects.create(broker=self.other, graphite_agency_name='Minet- Francistown')
        BrokerPolicy.objects.create(broker=self.other, policy_number='COMG2099000007')
        BrokerPolicy.objects.create(broker=self.keep, policy_number='COMG2099000008')
        BrokerPolicy.objects.create(broker=self.other, policy_number='COMG2099000008')

    def test_aliases_and_rows_move_and_a_duplicate_is_reported_not_hidden(self):
        out = svc.absorb(self.keep, self.other)
        self.assertEqual(out['aliases_moved'], 1)
        self.assertEqual(out['rows_moved'], 1)
        self.assertEqual(out['rows_dropped_duplicate'], 1)
        self.assertFalse(Broker.objects.filter(pk=self.other.pk).exists())
        self.assertEqual(self.keep.policies.count(), 2)

    def test_a_broker_cannot_absorb_itself(self):
        with self.assertRaises(ValueError):
            svc.absorb(self.keep, self.keep)


# ── the gate on every route ─────────────────────────────────────────────────
class BrokerRouteAccessTests(TestCase):
    ROUTES = [
        ('get', '/api/v1/commissions/brokers/'),
        ('post', '/api/v1/commissions/brokers/sync/'),
        ('post', '/api/v1/commissions/brokers/upload/'),
    ]

    def setUp(self):
        User = get_user_model()
        self.plain = User.objects.create_user('zz_plain', 'zz_plain@alphadirect.co.bw', 'x')
        self.allowed = User.objects.create_user('zz_fin', 'bmakosha@alphadirect.co.bw', 'x')
        # [C8], 15-Sep-2026: the register now answers to the named
        # "Broker Commission - Full Access" role, not to the commission-stage
        # roster. Bokani is one of the four people Finance named, so the fixture
        # grants him the role rather than the permission being loosened back.
        from core.models import Permission, Role, UserRoleAssignment
        from commissions.broker_views import (BROKER_COMMISSION_PERMISSION,
                                              BROKER_COMMISSION_ROLE)
        role, _ = Role.objects.get_or_create(
            code=BROKER_COMMISSION_ROLE,
            defaults={'name': 'Broker Commission - Full Access', 'level': 3})
        permission, _ = Permission.objects.get_or_create(
            code=BROKER_COMMISSION_PERMISSION,
            defaults={'category': 'commissions', 'is_active': True})
        role.permissions.add(permission)
        UserRoleAssignment.objects.create(user=self.allowed, role=role,
                                          justification='[C8] test fixture')
        self.api = APIClient()

    def test_a_bare_login_is_refused_every_route(self):
        self.api.force_authenticate(self.plain)
        for method, url in self.ROUTES:
            r = getattr(self.api, method)(url)
            self.assertEqual(r.status_code, 403, f'{method} {url} let a bare login in')

    def test_an_anonymous_caller_is_refused(self):
        self.api.force_authenticate(None)
        for method, url in self.ROUTES:
            self.assertIn(getattr(self.api, method)(url).status_code, (401, 403))

    def test_a_full_access_person_is_let_through(self):
        self.api.force_authenticate(self.allowed)
        with patch('realpay.graphite_feed.broker_book',
                   return_value={'configured': False, 'rows': []}):
            r = self.api.get('/api/v1/commissions/brokers/')
        self.assertEqual(r.status_code, 200)

    def test_the_month_must_be_a_real_month(self):
        b = Broker.objects.create(name='Test Broker')
        self.api.force_authenticate(self.allowed)
        r = self.api.post(f'/api/v1/commissions/brokers/{b.id}/policies/',
                          {'policy_number': 'COMG2099000009', 'period_label': '2026-8'},
                          format='json')
        self.assertEqual(r.status_code, 400, "'2026-8' must be refused, not truncated")


class TabMatchingTests(TestCase):
    """Matching a workbook TAB to a broker is looser than merging two Graphite
    rows, deliberately: Finance's tabs say 'Redhill', 'Minet', 'Mikardow' — short
    working names that never equal the registered name. Being loose is safe here
    because a person named the tab, an unmatched tab is reported, and an
    ambiguous one is refused rather than guessed."""

    def setUp(self):
        self.redhill = Broker.objects.create(name='Redhill Risk Solutions')
        BrokerAlias.objects.create(broker=self.redhill,
                                   graphite_agency_name='Hilrange Enterprises (Pty) Ltd T/a Redhill Risk')
        self.minet = Broker.objects.create(name='Minet Botswana PTY')
        self.mika = Broker.objects.create(name='Ignytwealth')
        BrokerAlias.objects.create(
            broker=self.mika,
            graphite_agency_name='Mikardow Investments (Pty) Ltd t/a Ignytwealth')

    def _match(self, sheet):
        rows = [['Policy No', 'Insured Name', 'Commision Payable'],
                [f'COMG2099000{abs(hash(sheet)) % 900 + 100}', 'X', 10]]
        return svc.import_workbook('/dev/null', period_label='2026-08', commit=False,
                                   _sheets=[(sheet, rows)])

    def test_short_working_names_match(self):
        for sheet, broker in [('Redhill', 'Redhill Risk Solutions'),
                              ('Minet', 'Minet Botswana PTY'),
                              ('Mikardow', 'Ignytwealth')]:
            out = self._match(sheet)
            self.assertEqual([m['broker'] for m in out['matched']], [broker],
                             f'tab {sheet!r} did not find {broker}: {out}')

    def test_an_unknown_tab_is_reported_with_a_reason_not_dropped(self):
        out = self._match('Totally Unknown Broker')
        self.assertEqual(out['matched'], [])
        self.assertEqual(len(out['unmatched']), 1)
        self.assertIn('no broker', out['unmatched'][0]['reason'])

    def test_an_ambiguous_tab_is_refused_not_guessed(self):
        Broker.objects.create(name='Minet Francistown')
        out = self._match('Minet')
        self.assertEqual(out['matched'], [], 'an ambiguous tab must not pick one')
        self.assertIn('more than one', out['unmatched'][0]['reason'])


class DisplayNameTests(SimpleTestCase):
    def test_a_merged_broker_is_not_named_after_one_of_its_branches(self):
        """Live prod showed a three-branch firm filed as 'Dynamic Insurance
        Brokers - Gaborone Branch' because the display name kept the branch."""
        for n in ('Dynamic Insurance Brokers (Pty) Ltd - Gaborone Branch',
                  'Dynamic Insurance Brokers (Pty) Ltd - Palapye Branch',
                  'Dynamic Insurance Brokers (Pty) Ltd'):
            self.assertEqual(svc.display_name(n), 'Dynamic Insurance Brokers', n)

    def test_the_trading_name_is_what_finance_sees(self):
        self.assertEqual(
            svc.display_name('Hilrange Enterprises (Pty) Ltd T/a Redhill Risk'),
            'Redhill Risk')
