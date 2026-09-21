"""Renewal report (B1) — the row-shaping and query wiring, proven without a live
Graphite database via a stub runner. The SQL itself is validated separately
against the live replica; these tests pin the logic Finance's rules depend on:
month-only, first-year flag, expiry = next anniversary, section prefix, exactly
the agreed columns.
"""
from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from reporting import renewal_report as rr


class ShapeRowTests(SimpleTestCase):
    def _raw(self, **over):
        base = {
            'policyNumber': 'COMG2024104052',
            'insured_name': 'Bonang Tshasa',
            'broker_name': 'Spectrum Insurance',
            'agent_name': 'Dimpho Motseothata',
            'product_name': 'Commercial Insurance',
            'renewal_effective': date(2025, 9, 2),
            'premium_freq': 3,
            'sum_insured': None,
            'renewal_premium': Decimal('1800.00'),
            'inforce_premium': Decimal('1800.00'),
            'is_first_year': 0,
        }
        base.update(over)
        return base

    def test_expiry_is_the_next_anniversary_not_the_billing_period(self):
        row = rr.shape_row(self._raw(renewal_effective=date(2025, 9, 2)))
        self.assertEqual(row['Renewal Date'], '02-09-2025')
        self.assertEqual(row['Renewal Expiry'], '02-09-2026')   # +1 year, not +1 month

    def test_first_year_is_flagged_only_when_no_anniversary(self):
        self.assertEqual(rr.shape_row(self._raw(is_first_year=1))['First-Year'], 'Yes')
        self.assertEqual(rr.shape_row(self._raw(is_first_year=0))['First-Year'], '')

    def test_frequency_label_uses_the_domcom_map_not_the_legacy_one(self):
        # 5 = Quarterly for DomCom (a legacy file wrongly maps 2=Quarterly).
        self.assertEqual(rr.shape_row(self._raw(premium_freq=5))['Payment Frequency'], 'Quarterly')
        self.assertEqual(rr.shape_row(self._raw(premium_freq=1))['Payment Frequency'], 'Monthly')
        self.assertEqual(rr.shape_row(self._raw(premium_freq=3))['Payment Frequency'], 'Annual')

    def test_money_and_blank_sum_insured(self):
        row = rr.shape_row(self._raw(renewal_premium=Decimal('13892.04'), sum_insured=None))
        self.assertEqual(row['Renewal Premium (Annual)'], '13,892.04')
        self.assertEqual(row['Sum Insured'], '')      # header null -> blank (fast-follow)

    def test_leap_day_anniversary_falls_back_to_28_feb(self):
        row = rr.shape_row(self._raw(renewal_effective=date(2024, 2, 29)))
        self.assertEqual(row['Renewal Expiry'], '28-02-2025')


class PrefixTests(SimpleTestCase):
    def test_section_prefix(self):
        self.assertIn("DOMG", rr._prefix_clause('domestic'))
        self.assertNotIn("COMG", rr._prefix_clause('domestic'))
        self.assertIn("COMG", rr._prefix_clause('commercial'))
        both = rr._prefix_clause(None)
        self.assertIn("DOMG", both)
        self.assertIn("COMG", both)


class FetchTests(SimpleTestCase):
    def test_fetch_passes_month_and_section_and_shapes_rows(self):
        seen = {}

        def stub(sql, params, limit):
            seen['sql'] = sql
            seen['params'] = params
            return [{
                'policyNumber': 'DOMG2025200001', 'insured_name': 'Kabo Modise',
                'broker_name': 'Minet', 'agent_name': 'A B', 'product_name': 'Domestic',
                'renewal_effective': date(2025, 9, 15), 'premium_freq': 1,
                'sum_insured': None, 'renewal_premium': Decimal('4023.00'),
                'inforce_premium': Decimal('335.25'), 'is_first_year': 1,
            }]

        out = rr.fetch_renewals(9, 'domestic', runner=stub)
        self.assertEqual(seen['params'], [9])
        self.assertIn("DOMG", seen['sql'])
        self.assertNotIn("COMG", seen['sql'])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['Policy Number'], 'DOMG2025200001')
        self.assertEqual(out[0]['First-Year'], 'Yes')
        self.assertEqual(out[0]['In-force Premium (Per Period)'], '335.25')

    def test_month_out_of_range_is_refused(self):
        with self.assertRaises(ValueError):
            rr.fetch_renewals(13, runner=lambda *a, **k: [])

    def test_to_matrix_has_header_then_rows(self):
        shaped = rr.fetch_renewals(9, runner=lambda *a, **k: [{
            'policyNumber': 'COMG1', 'insured_name': 'X', 'broker_name': '',
            'agent_name': '', 'product_name': '', 'renewal_effective': date(2025, 9, 1),
            'premium_freq': 3, 'sum_insured': None, 'renewal_premium': None,
            'inforce_premium': None, 'is_first_year': 0,
        }])
        matrix = rr.to_matrix(shaped)
        self.assertEqual(matrix[0], rr.COLUMNS)
        self.assertEqual(matrix[1][0], 'COMG1')
        self.assertEqual(len(matrix), 2)
