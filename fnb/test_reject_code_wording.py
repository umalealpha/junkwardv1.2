"""Wording guardrails for the FNB reject-code table and the branch-code guard
(CFO brief 2026-09-18, "FNB wording and safe UI follow-through").

Two promises this file exists to keep:

1. No message in `KNOWN_REASONS` may imply a PERMANENT block. Any entry that
   uses the word "block" (or "blocked") must ALSO name the exception committee
   or a hold action, and must never use the word "permanent". The committee
   path is always available — the CFO rule is *"you will never block a
   payment, if there is a blocker the exception committee kicks in."*

2. The branch-code guard (`fnb/destination_bank.py`) must always route to
   either "pay" (a usable branch code is returned) or "route to committee"
   (a plain-English problem sentence is returned) — NEVER a hard refusal with
   no route out. This test exercises: valid FNB Botswana, valid FNB SA,
   valid FNB Moçambique, a non-FNB bank, blank, leading-zero, and malformed
   values.

Nothing here changes payment behaviour. This is a wording + routing guardrail
so a future edit cannot re-introduce a permanent-block message or a hard
refusal in the branch-code path.
"""
from __future__ import annotations

import unittest

from fnb.destination_bank import branch_code_problem, usable_branch_code
from fnb.reject_codes import KNOWN_REASONS


class RejectCodeWordingTests(unittest.TestCase):
    """No reject-code entry may imply a permanent block."""

    def test_no_entry_uses_the_word_permanent(self):
        for code, entry in KNOWN_REASONS.items():
            blob = f'{entry.get("plain", "")} {entry.get("action", "")}'.lower()
            self.assertNotIn(
                'permanent', blob,
                f'{code}: reject-code wording must not use "permanent" — '
                f'the exception committee path is always available.',
            )

    def test_block_wording_routes_to_committee_or_hold(self):
        """An entry that mentions a block must also name the exception
        committee, a hold action, or route the reader to the payee's own bank
        to have it lifted. It must never read as a permanent refusal."""
        for code, entry in KNOWN_REASONS.items():
            blob = f'{entry.get("plain", "")} {entry.get("action", "")}'.lower()
            if 'block' not in blob:
                continue
            has_route = any(token in blob for token in (
                'exception committee',
                'committee',
                'hold ',
                'lifted',
            ))
            self.assertTrue(
                has_route,
                f'{code}: mentions "block" but names no route — the message '
                f'must name the exception committee, a hold action, or say '
                f'the block may be lifted. Got: {blob!r}',
            )


class BranchCodeRoutingTests(unittest.TestCase):
    """Branch-code guard: every outcome is either PAY or COMMITTEE. No
    silent refusal, no exception raised, no invented fallback."""

    def _route(self, bank_name: str, branch: str) -> str:
        """Read the guard the way the batch builder does.

        `usable_branch_code` returns None when the guard has a problem —
        the caller records the problem as a `soft_reason` and the exception
        committee decides (fnb/destination_bank.py docstring). A returned
        string is the branch code that goes on the instruction.
        """
        problem = branch_code_problem(bank_name, branch)
        usable = usable_branch_code(bank_name, branch)
        if usable is not None:
            # A well-formed branch code (or a legitimate FNB Botswana blank
            # that resolves to the universal branch): the payment is paid.
            self.assertEqual(problem, '',
                             f'usable code but a problem sentence: {problem!r}')
            return 'pay'
        # No usable code: caller routes to the exception committee. The
        # problem sentence is the human-readable reason.
        self.assertTrue(
            problem,
            'guard returned no code AND no problem sentence — that is a '
            'silent refusal with no route out',
        )
        return 'committee'

    # --- Valid FNB Botswana --------------------------------------------------
    def test_valid_fnb_botswana_pays(self):
        # 282467 is a real FNB Botswana branch (Broadhurst); shape valid.
        self.assertEqual(self._route('FNB Botswana', '282467'), 'pay')

    def test_fnb_botswana_blank_is_allowed(self):
        # FNB-to-FNB may omit the branch; the universal branch fills in.
        self.assertEqual(self._route('FNB', ''), 'pay')

    # --- Foreign FNB (SA, Moçambique) --- these are NOT FNB Botswana, so a
    # branch code is required. A well-formed six-digit code passes; a blank
    # routes to committee, never to FNB Botswana's universal branch.
    def test_fnb_sa_with_valid_branch_pays(self):
        self.assertEqual(self._route('FNB SA', '250655'), 'pay')

    def test_fnb_sa_blank_routes_to_committee(self):
        self.assertEqual(self._route('FNB SA', ''), 'committee')

    def test_fnb_mozambique_with_valid_branch_pays(self):
        # Accent-folded so the country is seen.
        self.assertEqual(self._route('FNB Moçambique', '123456'), 'pay')

    def test_fnb_mozambique_blank_routes_to_committee(self):
        self.assertEqual(self._route('FNB Moçambique', ''), 'committee')

    # --- Non-FNB payee -------------------------------------------------------
    def test_non_fnb_valid_code_pays(self):
        # 064967 is a real Stanbic branch; shape valid.
        self.assertEqual(self._route('Stanbic Bank Botswana', '064967'),
                         'pay')

    def test_non_fnb_blank_routes_to_committee(self):
        self.assertEqual(self._route('Stanbic Bank Botswana', ''),
                         'committee')

    # --- Blank on unknown bank -----------------------------------------------
    def test_unknown_bank_blank_routes_to_committee(self):
        self.assertEqual(self._route('Some New Bank Ltd', ''), 'committee')

    # --- Leading-zero: '64967' is the AC08 case, '064967' is the fix --------
    def test_leading_zero_dropped_routes_to_committee(self):
        # Five digits — a leading zero has been dropped.
        self.assertEqual(self._route('Stanbic', '64967'), 'committee')

    def test_leading_zero_preserved_pays(self):
        self.assertEqual(self._route('Stanbic', '064967'), 'pay')

    # --- Malformed shapes ----------------------------------------------------
    def test_malformed_hyphen_routes_to_committee(self):
        self.assertEqual(self._route('Stanbic', '202-067'), 'committee')

    def test_malformed_bare_hyphen_routes_to_committee(self):
        self.assertEqual(self._route('Stanbic', '-'), 'committee')

    def test_malformed_account_number_in_branch_routes_to_committee(self):
        # An 11-digit value typed into the branch box.
        self.assertEqual(self._route('Stanbic', '12345678901'), 'committee')

    def test_all_zeroes_routes_to_committee(self):
        self.assertEqual(self._route('Stanbic', '000000'), 'committee')


if __name__ == '__main__':                          # pragma: no cover
    unittest.main()
