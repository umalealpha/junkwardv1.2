"""Every case here is a REAL value taken off production on 17-Sep-2026 — the
branch codes Omni actually sent FNB on the 23 failed batches, and the ones on
batches that settled. A synthetic fixture would have passed the old code.
"""
from django.test import SimpleTestCase

from fnb.destination_bank import (
    FNB_UNIVERSAL_BRANCH,
    branch_code_problem,
    is_fnb_botswana,
    usable_branch_code,
)


class IsFnbBotswanaTests(SimpleTestCase):
    """Bank names exactly as they are spelt in the live payment requests."""

    def test_the_real_fnb_spellings_all_pass(self):
        for name in ('FNB', 'FNB Botswana', 'FNB BOTSWANA', 'First National Bank',
                     'First National Bank Botswana', 'FNB- Unicoin'):
            with self.subTest(name=name):
                self.assertTrue(is_fnb_botswana(name))

    def test_fnb_south_africa_is_not_fnb_botswana(self):
        # 'FNB SA' appears on six live requests. The substring check this
        # replaced matched it, so Omni told a South African account it needed
        # no branch code — and branch 287867 means nothing outside Botswana.
        self.assertFalse(is_fnb_botswana('FNB SA'))
        self.assertFalse(is_fnb_botswana('FNB South Africa'))

    def test_the_real_other_banks_all_fail(self):
        for name in ('Stanbic Bank', 'Stanbic Bank Botswana', 'STANBIC', 'ABSA',
                     'Access Bank', 'Bank Gaborone', 'BANK GABORONE',
                     'Standard Chartered Botswana', 'Standard Chartered Bank'):
            with self.subTest(name=name):
                self.assertFalse(is_fnb_botswana(name))

    def test_an_accented_country_name_cannot_hide(self):
        # 🔴 A REAL BUG, found by the off-subscription panel on 17-Sep-2026.
        # 'moçambique' sat in the foreign list but could never match: the
        # tokeniser split on the cedilla, so the name became
        # ['fnb', 'mo', 'ambique'] and "FNB Moçambique" was treated as FNB
        # Botswana — blank branch, FNB's own 287867, guaranteed AC08. Names are
        # folded to plain ASCII now.
        self.assertFalse(is_fnb_botswana('FNB Moçambique'))
        self.assertFalse(is_fnb_botswana('FNB Mocambique'))
        self.assertFalse(is_fnb_botswana('FNB Mozambique'))

    def test_every_country_fnb_trades_in_needs_its_own_branch_code(self):
        # The denylist is defensible only if it is COMPLETE for this brand.
        for where in ('SA', 'South Africa', 'Namibia', 'Lesotho', 'Eswatini',
                      'Swaziland', 'Zambia', 'Zimbabwe', 'Tanzania', 'Ghana',
                      'India', 'Guernsey', 'Jersey', 'UK', 'London'):
            with self.subTest(where=where):
                self.assertFalse(is_fnb_botswana(f'FNB {where}'),
                                 f'FNB {where} is not FNB Botswana')

    def test_another_banks_name_wins_over_the_fnb_words(self):
        # 🔴 A LIVE CASE, not a hypothetical. Three payment requests on
        # production are spelt 'Bank Gaborone First National Bank'. Read as FNB,
        # a blank branch code on those goes to the bank as FNB's own 287867 —
        # the exact AC08 this module exists to stop. Found by querying prod for
        # every live spelling, 17-Sep-2026.
        self.assertFalse(is_fnb_botswana('Bank Gaborone First National Bank'))
        self.assertFalse(is_fnb_botswana('Stanbic First National'))

    def test_an_fnb_branch_in_gaborone_is_still_fnb(self):
        # 'bank gaborone' is matched as a PHRASE for this reason: 'FNB Gaborone'
        # is a branch of FNB, not a different bank.
        self.assertTrue(is_fnb_botswana('FNB Gaborone'))

    def test_a_punctuated_country_cannot_slip_past(self):
        # 'FNB S.A.' tokenises to ['fnb','s','a'], so the pinned 'sa' never
        # matched. One spelling pinned is not the class pinned.
        self.assertFalse(is_fnb_botswana('FNB S.A.'))

    def test_fnbb_is_still_fnb(self):
        # A blank-branch FNBB payee that settles today must not start failing.
        self.assertTrue(is_fnb_botswana('FNBB'))

    def test_every_live_fnb_spelling_on_production_still_resolves_to_fnb(self):
        # Read off production on 17-Sep-2026. A miss here turns a working
        # payment into a stopped one, which is the one thing this must not do.
        for name in ('FNB Botswana', 'FNB', 'First National Bank', 'FNB BOTSWANA',
                     'First National Bank Botswana', 'FNB- Unicoin', 'FNB Unicoin',
                     'FNB Bank Botswana', 'First National Bank Botswana Limited',
                     'FIRST NATIONAL BANK', 'First National Bank of Botswana Limited',
                     'fnb', 'FNB- Risk Software', 'FNB-RS', 'Unicoin FNB',
                     'FNB CHEQ A/C', 'FNB BOTSWAN'):
            with self.subTest(name=name):
                self.assertTrue(is_fnb_botswana(name), name)

    def test_saying_botswana_outright_wins(self):
        self.assertTrue(is_fnb_botswana('FNB Botswana'))
        self.assertTrue(is_fnb_botswana('First National Bank Botswana'))

    def test_unknown_and_junk_resolve_to_not_fnb(self):
        # An unknown bank must ASK for a branch code, never inherit FNB's.
        # '-' and '' are both live values in the payment-request table.
        for name in ('', '   ', '-', 'Alpha Direct Claims', 'CLAIMS',
                     'Bank of Somewhere'):
            with self.subTest(name=name):
                self.assertFalse(is_fnb_botswana(name))


class BranchCodeProblemTests(SimpleTestCase):

    def test_blank_is_fine_for_fnb_botswana(self):
        self.assertEqual(branch_code_problem('FNB Botswana', ''), '')

    def test_blank_is_a_problem_for_anyone_else(self):
        # PAY/ADIC/2026/09/15/0006 — Stanbic, blank branch, BWP 3,844.08.
        # It went out on 287867 and came back AC08.
        problem = branch_code_problem('Stanbic Bank', '')
        self.assertIn('Stanbic Bank', problem)
        self.assertIn(FNB_UNIVERSAL_BRANCH, problem)

    def test_five_digits_names_the_dropped_leading_zero(self):
        # EOH, BWP 97,348.00, sent '64967'. The same payee settled once on
        # '064967'. Two AC08 rejects came from that single lost zero.
        problem = branch_code_problem('Stanbic Bank Botswana', '64967')
        self.assertIn('064967', problem)

    def test_four_digits_is_a_problem(self):
        # A live reject: a payee whose branch code went out as '6700'.
        self.assertIn('4 digits', branch_code_problem('Stanbic Bank Botswana', '6700'))

    def test_an_account_number_in_the_branch_box_is_a_problem(self):
        # A live reject: the payee's ACCOUNT number was typed into the
        # branch box. The real value is not repeated here — only its shape,
        # which is the whole point of the rule.
        problem = branch_code_problem('FNB Botswana', '60000000000')
        self.assertIn('account number', problem)

    def test_a_hyphenated_code_is_a_problem(self):
        # A live reject: a branch code that went out as '202-067'.
        self.assertIn('not all digits', branch_code_problem('Bank Gaborone Ltd', '202-067'))

    def test_a_bare_hyphen_is_a_problem(self):
        # ALPHA-EFT-20260824, BWP 6,498.75, sent '-'.
        self.assertTrue(branch_code_problem('-', '-'))

    def test_all_zeroes_is_a_problem(self):
        self.assertIn('all zeroes', branch_code_problem('Stanbic Bank', '000000'))

    def test_the_branch_codes_that_settled_are_left_alone(self):
        # Every one of these is on a batch FNB settled. A control that flags a
        # working payment is a control nobody will keep.
        for code in ('287867', '283767', '064967', '283567', '282867', '552067',
                     '288267', '281467', '282267', '060167', '060367', '291467',
                     '288967', '281667', '293567', '660026'):
            with self.subTest(code=code):
                self.assertEqual(branch_code_problem('Stanbic Bank', code), '')

    def test_whitespace_around_a_good_code_is_not_a_problem(self):
        self.assertEqual(branch_code_problem('Stanbic Bank', ' 064967 '), '')


class UsableBranchCodeTests(SimpleTestCase):

    def test_fnb_botswana_with_no_code_gets_the_universal_branch(self):
        self.assertEqual(usable_branch_code('FNB Botswana', ''), FNB_UNIVERSAL_BRANCH)

    def test_another_bank_with_no_code_gets_nothing_not_the_fnb_branch(self):
        # THE BUG, in one assertion. The old code substituted 287867 here and
        # the payment could only ever be rejected.
        self.assertIsNone(usable_branch_code('Stanbic Bank', ''))

    def test_a_malformed_code_is_never_silently_replaced(self):
        self.assertIsNone(usable_branch_code('Stanbic Bank Botswana', '64967'))

    def test_a_good_code_is_returned_unchanged_with_its_leading_zero(self):
        self.assertEqual(usable_branch_code('Stanbic Bank', '064967'), '064967')
