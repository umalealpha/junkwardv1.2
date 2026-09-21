"""Payroll must exclude ONE bad branch code, never refuse the whole salary run.

WHY THIS EXISTS. `fnb.payments.build_batch_payload` now RAISES when a creditor
branch code is unusable. For a payment request that is safe — each payment is
submitted as its own batch, so a bad line fails alone and the request stays open
with the reason recorded. Payroll is the opposite shape: it builds ONE batch for
every employee in the period (`load_period_to_fnb` -> a single `submit_eft_batch`
call), so a single malformed branch code — say '64967', the Excel leading-zero
trap this module already warns about — would refuse EVERYBODY'S salary, not just
that person's.

Caught by Fable 5.1 on 17-Sep-2026 and filed as checklist class L66: *a per-line
rule added to a shared BATCH builder refuses the whole batch for every caller*.
The fix is the pattern this file already uses everywhere else — exclude the one
person and say why — applied at the caller, per line.

🔴 HONEST LIMIT OF THIS TEST FILE. These are structural checks, parsed with
`ast`. They prove the shared rule is really called at both call sites, and that
the preview and the submit path cannot drift apart. They do NOT drive a real
payroll period end to end — there is no existing fixture for
`build_period_preview` in this app, and `PAYROLL_FNB_LOAD_ENABLED` defaults to
False so the path is latent today. The behaviour of the rule itself is covered
properly in fnb/test_destination_bank.py. Anyone touching this path should add
the end-to-end case.
"""
import ast
import pathlib

from django.test import SimpleTestCase

SRC = pathlib.Path(__file__).with_name('fnb_disbursement.py')


def _fn(name):
    tree = ast.parse(SRC.read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f'{name} is not in payroll/fnb_disbursement.py')


def _calls(fn):
    return [n.func.id for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]


class TheSharedRuleIsActuallyCalledTests(SimpleTestCase):
    """Mentioned is not invoked. Importing the rule is not applying it."""

    def test_the_preview_applies_the_branch_code_rule(self):
        self.assertIn('branch_code_problem', _calls(_fn('build_period_preview')),
                      'build_period_preview must EXCLUDE a bad branch code, or '
                      'one employee stops the whole salary run')

    def test_the_submit_path_applies_the_same_rule(self):
        self.assertIn('branch_code_problem', _calls(_fn('_payees_from_preview')),
                      'the submit path must filter exactly as the preview does, '
                      'or a payee the CFO was shown as excluded still reaches '
                      'the bank')

    def test_the_rule_is_imported_from_the_one_shared_module(self):
        # Not re-implemented locally. Two copies of a branch-code rule is how
        # the preview and the batch end up disagreeing.
        tree = ast.parse(SRC.read_text(encoding='utf-8'))
        sources = {n.module for n in ast.walk(tree)
                   if isinstance(n, ast.ImportFrom)
                   and any(a.name == 'branch_code_problem' for a in n.names)}
        self.assertEqual(sources, {'fnb.destination_bank'})


class TheExclusionSaysWhoAndWhyTests(SimpleTestCase):
    """An excluded employee must be NAMED with a reason — a silent drop is a
    person who quietly does not get paid."""

    def test_the_branch_code_exclusion_records_a_reason(self):
        src = SRC.read_text(encoding='utf-8')
        i = src.index('_branch_problem = branch_code_problem')
        window = src[i:i + 400]
        self.assertIn("pre.excluded.append", window)
        self.assertIn("'name': name", window)
        self.assertIn('branch code:', window)

    def test_it_continues_rather_than_raising(self):
        # The whole point: skip this person, keep going. A raise here would be
        # the very failure the fix removes.
        src = SRC.read_text(encoding='utf-8')
        i = src.index('_branch_problem = branch_code_problem')
        window = src[i:i + 400]
        self.assertIn('continue', window)
        self.assertNotIn('raise', window)
