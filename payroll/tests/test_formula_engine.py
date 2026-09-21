"""
payroll/tests/test_formula_engine.py

CFO directive 2026-05-24 — Payroll + HR upgrades pass, item #6.

Cover the safe-evaluator's happy path, the sandbox boundary, and the
PRIOR_LINES_DICT lookup pattern that the upcoming auto-evaluator will
use.
"""

from decimal import Decimal

from django.test import SimpleTestCase

from payroll.formula_engine import (
    FormulaError,
    evaluate_formula,
)


class FormulaEngineTest(SimpleTestCase):

    # ----- happy paths -----

    def test_empty_formula_returns_zero(self):
        self.assertEqual(evaluate_formula('', {}), Decimal('0.00'))

    def test_literal(self):
        self.assertEqual(evaluate_formula('42', {}), Decimal('42'))

    def test_basic_arithmetic(self):
        ctx = {'BASIC': Decimal('10000.00'), 'GROSS': Decimal('0')}
        self.assertEqual(evaluate_formula('BASIC * 0.10', ctx), Decimal('1000.000'))

    def test_percent_of_gross(self):
        ctx = {'BASIC': Decimal('0'), 'GROSS': Decimal('15000.00')}
        self.assertEqual(evaluate_formula('GROSS * 0.05', ctx), Decimal('750.0000'))

    def test_min_max_round(self):
        ctx = {'BASIC': Decimal('20000')}
        # 20000 * 0.07 = 1400, which is below the 1500 cap, so min() returns
        # 1400 (the cap does not bind here).
        self.assertEqual(
            evaluate_formula('min(BASIC * 0.07, 1500)', ctx),
            Decimal('1400.00'),
        )
        self.assertEqual(
            evaluate_formula('max(BASIC * 0.01, 250)', ctx),
            Decimal('250'),
        )
        self.assertEqual(
            evaluate_formula('round(BASIC / 3, 2)', ctx),
            Decimal('6666.67'),
        )

    def test_conditional_expression(self):
        ctx = {'BASIC': Decimal('25000')}
        self.assertEqual(
            evaluate_formula('500 if BASIC > 10000 else 200', ctx),
            Decimal('500'),
        )

    def test_prior_lines_dict_lookup(self):
        ctx = {
            'BASIC': Decimal('10000'),
            'PRIOR_LINES_DICT': {'TRANSPORT': Decimal('1500')},
        }
        # Tax 25% of the transport allowance.
        self.assertEqual(
            evaluate_formula('PRIOR_LINES_DICT["TRANSPORT"] * 0.25', ctx),
            Decimal('375.00'),
        )

    # ----- sandbox boundary -----

    def test_unknown_name_rejected(self):
        with self.assertRaises(FormulaError):
            evaluate_formula('SECRET * 2', {'BASIC': Decimal('0')})

    def test_attribute_access_rejected(self):
        with self.assertRaises(FormulaError):
            evaluate_formula('BASIC.real', {'BASIC': Decimal('1')})

    def test_arbitrary_function_call_rejected(self):
        with self.assertRaises(FormulaError):
            evaluate_formula('open("/etc/passwd")', {})

    def test_indirect_call_rejected(self):
        # PRIOR_LINES_DICT.get(...) is an attribute call → blocked.
        with self.assertRaises(FormulaError):
            evaluate_formula(
                'PRIOR_LINES_DICT.get("X", 0)',
                {'PRIOR_LINES_DICT': {}},
            )

    def test_extra_ctx_key_rejected(self):
        with self.assertRaises(FormulaError):
            evaluate_formula('BASIC', {'BASIC': Decimal('1'), 'EXTRA': 1})

    def test_syntax_error(self):
        with self.assertRaises(FormulaError):
            evaluate_formula('BASIC * (', {'BASIC': Decimal('1')})
