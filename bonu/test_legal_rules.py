"""Tests for bonu/legal_rules.py — the Legal intake + bill-capture rules.

Every test here is about an EDGE, because the middle of each of these functions
is obvious and the edges are where money goes to the wrong client. In
particular: an unrecognised town must be refused rather than defaulted, an
unknown received date must not read as nought days, a shared name must not
auto-match, and a split that does not add up must not be absorbed.
"""
from __future__ import annotations

import datetime
from decimal import Decimal

from django.test import SimpleTestCase

from bonu.legal_rules import (BOTSWANA_REGIONS, bill_dup_key, cap_tier,
                              check_split, clean_region, days_to_process,
                              match_client_name)

D = Decimal
d = datetime.date


class RegionTests(SimpleTestCase):
    def test_the_agreed_list_is_the_list(self):
        for town in ('Gaborone', 'Francistown', 'Molepolole', 'Maun', 'Serowe',
                     'Selebi-Phikwe', 'Kanye', 'Mochudi', 'Mahalapye', 'Mogoditshane',
                     'Palapye', 'Lobatse', 'Ramotswa', 'Tlokweng', 'Jwaneng', 'Kasane',
                     'Letlhakane', 'Orapa', 'Ghanzi', 'Sowa Town', 'Other'):
            self.assertIn(town, BOTSWANA_REGIONS)
        self.assertEqual(len(BOTSWANA_REGIONS), 21)

    def test_case_and_spacing_do_not_make_a_new_town(self):
        self.assertEqual(clean_region('Gaborone'), 'Gaborone')
        self.assertEqual(clean_region('  GABORONE '), 'Gaborone')
        self.assertEqual(clean_region('selebi-phikwe'), 'Selebi-Phikwe')
        self.assertEqual(clean_region('sowa town'), 'Sowa Town')

    def test_blank_is_blank_because_region_is_optional(self):
        self.assertEqual(clean_region(''), '')
        self.assertEqual(clean_region(None), '')
        self.assertEqual(clean_region('   '), '')

    def test_an_unrecognised_town_is_refused_not_defaulted(self):
        # The whole point. A typo must NOT become Gaborone and must NOT quietly
        # become "Other" — the caller has to be able to reject it.
        self.assertIsNone(clean_region('Gaboron'))
        self.assertIsNone(clean_region('Nairobi'))
        self.assertIsNone(clean_region('anywhere else'))
        self.assertIsNone(clean_region(7))


class CapTierTests(SimpleTestCase):
    AMBER, CAP = D('60000'), D('80000')

    def test_below_the_warning_is_clear(self):
        self.assertEqual(cap_tier(D('0'), self.AMBER, self.CAP), 'clear')
        self.assertEqual(cap_tier(D('59999.99'), self.AMBER, self.CAP), 'clear')

    def test_amber_starts_at_the_warning_not_above_it(self):
        self.assertEqual(cap_tier(D('60000'), self.AMBER, self.CAP), 'amber')
        self.assertEqual(cap_tier(D('79999.99'), self.AMBER, self.CAP), 'amber')

    def test_red_starts_at_the_cap_not_above_it(self):
        self.assertEqual(cap_tier(D('80000'), self.AMBER, self.CAP), 'red')
        self.assertEqual(cap_tier(D('125000'), self.AMBER, self.CAP), 'red')

    def test_a_breach_reads_red_even_if_amber_is_set_above_the_cap(self):
        # The thresholds are editable on screen, so they can be edited wrongly.
        # A real breach must never soften to a warning.
        self.assertEqual(cap_tier(D('90000'), D('100000'), D('80000')), 'red')

    def test_a_float_is_refused_rather_than_silently_converted(self):
        with self.assertRaises(TypeError):
            cap_tier(60000.0, self.AMBER, self.CAP)


class DaysToProcessTests(SimpleTestCase):
    def test_runs_live_while_the_matter_is_open(self):
        self.assertEqual(days_to_process(d(2026, 9, 1), None, as_of=d(2026, 9, 9)), 8)

    def test_freezes_on_the_closure_date(self):
        self.assertEqual(
            days_to_process(d(2026, 9, 1), d(2026, 9, 4), as_of=d(2026, 12, 31)), 3)

    def test_an_unknown_received_date_is_not_known_never_nought(self):
        self.assertIsNone(days_to_process(None, None, as_of=d(2026, 9, 9)))
        self.assertIsNone(days_to_process(None, d(2026, 9, 4), as_of=d(2026, 9, 9)))

    def test_a_closure_before_receipt_clamps_instead_of_going_negative(self):
        self.assertEqual(
            days_to_process(d(2026, 9, 10), d(2026, 9, 1), as_of=d(2026, 9, 20)), 0)

    def test_as_of_defaults_to_today_so_the_model_can_omit_it(self):
        today = datetime.date.today()
        self.assertEqual(days_to_process(today - datetime.timedelta(days=3), None), 3)


class DuplicateKeyTests(SimpleTestCase):
    def test_the_same_bill_typed_differently_gives_the_same_key(self):
        # Case, spacing and the punctuation in a bill number are how the same
        # bill gets keyed twice. The firm name itself comes off our own record,
        # so it varies only in case, never in wording.
        self.assertEqual(
            bill_dup_key('Jeremiah & Taldi', D('1500.00'), 'INV-001'),
            bill_dup_key('  jeremiah & taldi ', D('1500'), 'inv 001'))

    def test_one_thebe_apart_is_a_different_bill(self):
        self.assertNotEqual(bill_dup_key('F', D('1500.00'), 'INV-001'),
                            bill_dup_key('F', D('1500.01'), 'INV-001'))

    def test_a_different_firm_or_reference_is_a_different_bill(self):
        self.assertNotEqual(bill_dup_key('Firm A', D('1'), 'X'),
                            bill_dup_key('Firm B', D('1'), 'X'))
        self.assertNotEqual(bill_dup_key('F', D('1'), 'A1'),
                            bill_dup_key('F', D('1'), 'A2'))

    def test_the_key_is_stable_across_calls(self):
        self.assertEqual(bill_dup_key('F', D('2.50'), 'R'),
                         bill_dup_key('F', D('2.50'), 'R'))


class NameMatchTests(SimpleTestCase):
    CANDIDATES = [
        {'id': '1', 'name': 'Keneetswe Ralolemo'},
        {'id': '2', 'name': 'Thabo Moeng'},
        {'id': '3', 'name': 'Thabo Moeng'},          # two clients, one name
        {'id': '4', 'name': 'Naledi Kgosi Sebina'},
        # A one-word name on the roll. Without this row the single-word guard
        # is never exercised: 'MOENG' simply fails the equality check against
        # 'MOENG THABO' and the test passed with the guard removed.
        # (Fable, /fabe gate, 9 Sep 2026 — the one weak test in the branch.)
        {'id': '5', 'name': 'Moeng'},
    ]

    def test_one_exact_match_may_be_tied_automatically(self):
        r = match_client_name('Keneetswe Ralolemo', self.CANDIDATES)
        self.assertEqual(r['outcome'], 'exact')
        self.assertEqual([m['id'] for m in r['matches']], ['1'])

    def test_surname_first_still_reaches_the_same_client(self):
        # This is why the normaliser sorts the words — a firm writing the
        # surname first must not look like a different person.
        r = match_client_name('RALOLEMO, keneetswe', self.CANDIDATES)
        self.assertEqual(r['outcome'], 'exact')
        self.assertEqual(r['matches'][0]['id'], '1')

    def test_two_clients_sharing_a_name_never_auto_commits(self):
        r = match_client_name('Thabo Moeng', self.CANDIDATES)
        self.assertEqual(r['outcome'], 'ambiguous')
        self.assertEqual({m['id'] for m in r['matches']}, {'2', '3'})

    def test_a_missing_middle_name_is_a_candidate_not_a_certainty(self):
        r = match_client_name('Naledi Sebina', self.CANDIDATES)
        self.assertEqual(r['outcome'], 'ambiguous')
        self.assertEqual([m['id'] for m in r['matches']], ['4'])

    def test_a_surname_on_its_own_is_never_an_identification(self):
        # Candidate 5 IS literally called 'Moeng', so this would normalise to a
        # single, unique, exact match — and must still not auto-commit. One
        # word is not an identification, however unique it looks.
        r = match_client_name('Moeng', self.CANDIDATES)
        self.assertNotEqual(r['outcome'], 'exact')

    def test_nothing_matched_goes_to_the_exception_list(self):
        r = match_client_name('Someone Else Entirely', self.CANDIDATES)
        self.assertEqual(r['outcome'], 'none')
        self.assertEqual(r['matches'], [])

    def test_a_blank_billed_name_matches_nobody(self):
        self.assertEqual(match_client_name('', self.CANDIDATES)['outcome'], 'none')
        self.assertEqual(match_client_name(None, self.CANDIDATES)['outcome'], 'none')

    def test_an_honorific_the_firm_added_does_not_break_the_match(self):
        # The safety property is the OUTCOME, not the exact candidate list: an
        # extra weak candidate (here the one-word 'Moeng', id 5) is harmless
        # because a person is picking anyway. What must never happen is this
        # resolving to one client on its own.
        r = match_client_name('Mr Thabo Moeng', self.CANDIDATES)
        self.assertEqual(r['outcome'], 'ambiguous')
        self.assertTrue({'2', '3'} <= {m['id'] for m in r['matches']})


class SplitTests(SimpleTestCase):
    def test_one_matter_taking_the_whole_bill_is_fine(self):
        self.assertIsNone(check_split(D('1000.00'), [D('1000.00')]))

    def test_a_multi_matter_bill_that_adds_up_is_fine(self):
        self.assertIsNone(check_split(D('1000.00'), [D('400.00'), D('600.00')]))

    def test_under_and_over_allocation_are_both_refused(self):
        self.assertIsInstance(check_split(D('1000.00'), [D('400.00')]), str)
        self.assertIsInstance(check_split(D('1000.00'), [D('400.00'), D('700.00')]), str)

    def test_a_bill_must_say_which_matter_it_is_for(self):
        self.assertIsInstance(check_split(D('1000.00'), []), str)

    def test_a_nought_or_negative_line_is_refused(self):
        self.assertIsInstance(check_split(D('1000.00'), [D('1000.00'), D('0')]), str)
        self.assertIsInstance(check_split(D('1000.00'), [D('1200.00'), D('-200.00')]), str)

    def test_a_rounding_gap_is_the_persons_to_fix_not_ours_to_absorb(self):
        # Three matters cannot each take 333.33 of 1000.00. Only the person
        # capturing it knows which matter the odd thebe belongs to.
        self.assertIsInstance(
            check_split(D('1000.00'), [D('333.33'), D('333.33'), D('333.33')]), str)
