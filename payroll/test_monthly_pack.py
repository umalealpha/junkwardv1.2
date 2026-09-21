# -*- coding: utf-8 -*-
"""Unit tests for the monthly-pack pure logic (no DB needed).

Deterministic guards for the two things most likely to regress silently:
name matching (which drives the incentive + authority reconciliation) and the
prior-period roll-back. These fail if the matching thresholds are loosened or
the month arithmetic breaks.
"""
from decimal import Decimal
from django.test import SimpleTestCase

from payroll.monthly_pack import _prior_period_name, same_person, _atr_base_monthly


class PriorPeriodTests(SimpleTestCase):
    def test_normal(self):
        self.assertEqual(_prior_period_name("2026-08"), "2026-07")

    def test_january_rolls_to_previous_december(self):
        self.assertEqual(_prior_period_name("2026-01"), "2025-12")

    def test_bad_input_is_empty(self):
        self.assertEqual(_prior_period_name("nope"), "")


class SamePersonTests(SimpleTestCase):
    def test_middle_name_variant_matches(self):
        # 'Pako Lisley Kago' (payroll) vs 'Pako Kago' (module) -> same person
        self.assertTrue(same_person("Pako Lisley Kago", "Pako Kago"))

    def test_shared_surname_only_does_not_match(self):
        # only 'Kago' in common -> must NOT collapse two different people
        self.assertFalse(same_person("Pako Kago", "Kago Tshutlhedi"))

    def test_empty_never_matches(self):
        self.assertFalse(same_person("", "Someone Here"))


class _FakeAuth:
    def __init__(self, lines, ctc=Decimal("0")):
        self.salary_lines = lines
        self.quoted_ctc_monthly = ctc


class AtrBaseTests(SimpleTestCase):
    def test_reads_base_salary_line(self):
        a = _FakeAuth([{"item": "Base Salary (Pre-Tax)", "monthly": "30000"},
                       {"item": "Car Allowance", "monthly": "4000"}])
        self.assertEqual(_atr_base_monthly(a), Decimal("30000"))

    def test_falls_back_to_ctc_when_no_base_line(self):
        a = _FakeAuth([{"item": "Something", "monthly": "1"}], ctc=Decimal("37500"))
        self.assertEqual(_atr_base_monthly(a), Decimal("37500"))
