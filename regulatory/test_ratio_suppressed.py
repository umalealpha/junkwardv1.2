"""No capital adequacy ratio is published while any parameter is a placeholder,
and the requirement itself is Regulation 4 — not the invented factors.

Manus read the primary legislation on 2026-08-10. Regulation 4 of the Insurance
Industry Regulations 2019 sets the minimum capital target as the higher of
P5,000,000 or 25% of next year's estimated operating expenses. There is no premium
factor and no claims factor in Botswana insurance law, so the 0.18 and 0.26 that
produced the withdrawn 612% were inventions.

Quarterly statutory returns fall due within thirty days of quarter end and the
annual return carries an approved person's report. A figure that wrong must not
reach either, so the ratio is withheld until the CFO has verified the parameters.

Manus's follow-up brief of 2026-08-15 then put ADIC opex at ~P79.7m and the
requirement at P19.93m. That was checked against the ledger and rejected: ADIC
standalone opex is P28.24m (FY26) and P29.52m (FY25). The requirement is built
from the MA P&L, so the capital return cannot drift from the board pack.
"""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from regulatory.models import CapitalRequirementParameter
from regulatory.services import _unverified_parameters, compute_capital_check


def _adopt_all(effective=date(2026, 8, 15)):
    """Store + adopt every parameter the live formula reads."""
    for code, value in (('minimum_capital_floor', '5000000'),
                        ('opex_factor', '0.25'),
                        ('compliant_threshold', '1.25'),
                        ('margin_threshold', '1.00'),
                        ('intangibles_account_codes', '')):
        CapitalRequirementParameter.objects.update_or_create(
            code=code,
            defaults={'label': code, 'value': value, 'is_active': True,
                      'effective_from': effective,
                      'notes': 'Adopted per S.I. 68 of 2019, Regulation 4.'})


class RatioIsSuppressedWhileParametersAreUnverifiedTests(TestCase):
    def setUp(self):
        CapitalRequirementParameter.objects.create(
            code='opex_factor', label='Opex factor', value='0.25',
            is_active=True,
            notes='Reg 4(b). VERIFY against current rule.')

    def test_a_parameter_with_no_effective_date_counts_as_unverified(self):
        self.assertIn('opex_factor', _unverified_parameters(),
                      'no effective date means it was never formally adopted')

    def test_a_formula_parameter_with_no_row_at_all_counts_as_unverified(self):
        """A parameter running on its code default has never been signed off."""
        self.assertIn('minimum_capital_floor', _unverified_parameters(),
                      'a parameter nobody has stored cannot have been adopted')

    def test_retired_factors_no_longer_hold_the_ratio_hostage(self):
        """premium_factor / claims_factor are not read by reg 4, so an unadopted
        row for either must not appear on the waiting list."""
        CapitalRequirementParameter.objects.create(
            code='premium_factor', label='Premium factor', value='0.18',
            is_active=True, notes='Legacy. VERIFY against current rule.')
        self.assertNotIn('premium_factor', _unverified_parameters())

    def test_no_ratio_is_published_while_it_is_unverified(self):
        out = compute_capital_check(date(2026, 8, 10))
        self.assertIsNone(out['capital_adequacy_ratio'])
        self.assertIsNone(out['car_percent'])
        self.assertEqual(out['status'], 'under_revision')
        self.assertIn('not for regulatory or board use', out['status_note'])
        self.assertIn('opex_factor', out['unverified_parameters'])

    def test_the_reinsurance_share_is_disclosed_but_excluded(self):
        out = compute_capital_check(date(2026, 8, 10))
        self.assertIn('reinsurance_share_basis', out['components'])
        self.assertIn('Schedule 1', out['components']['reinsurance_share_note'])
        self.assertIn('EXCLUDED', out['components']['reinsurance_share_note'])

    def test_no_snapshot_can_be_taken_while_the_ratio_is_withheld(self):
        """Decimal(None) used to raise a bare TypeError and the take view has no
        handler, so the button 500'd. It failed closed, but a crash is not a
        designed refusal (Fable 2026-08-10)."""
        from rest_framework.exceptions import ValidationError

        from regulatory.models import RegulatoryCapitalSnapshot
        from regulatory.services import take_snapshot

        from django.contrib.auth import get_user_model
        cfo = get_user_model().objects.create_superuser(
            'snapcfo', 'snap@example.invalid', 'x')
        before = RegulatoryCapitalSnapshot.objects.count()
        with self.assertRaises(ValidationError):
            take_snapshot(as_of=date(2026, 8, 10), user=cfo,
                          notes='attempt while suppressed')
        self.assertEqual(RegulatoryCapitalSnapshot.objects.count(), before,
                         'no snapshot may persist while the ratio is withheld')


class Regulation4RequirementTests(TestCase):
    """The requirement is the higher of P5m and 25% of next year's opex."""

    def setUp(self):
        _adopt_all()

    def _check(self, opex, equity=Decimal('17010997.59'),
               ri_share=Decimal('14152382.85')):
        """ri_share defaults to the real prod add-back so the exclusion is
        exercised with a number big enough to change the verdict — an empty
        parameter table would make the assertion pass on zero."""
        with patch('regulatory.services._estimated_next_year_opex',
                   return_value={'amount': Decimal(opex), 'basis': 'test',
                                 'period': 'FY26', 'note': 'test'}), \
             patch('regulatory.services._total_equity_at', return_value=equity), \
             patch('regulatory.services._negative_reserves_addback',
                   return_value=ri_share):
            return compute_capital_check(date(2026, 8, 15))

    def test_opex_binds_when_a_quarter_of_it_exceeds_the_floor(self):
        out = self._check('28244967.74')
        # 25% x 28,244,967.74 = 7,061,241.94, which beats the P5m floor.
        self.assertEqual(out['required_capital'], '7061241.94')
        self.assertEqual(out['components']['binding_constraint'], 'opex')

    def test_the_floor_binds_when_opex_is_small(self):
        out = self._check('12000000')
        self.assertEqual(out['required_capital'], '5000000.00')
        self.assertEqual(out['components']['binding_constraint'], 'minimum_floor')

    def test_the_ledger_basis_passes_where_the_manus_figure_would_have_failed(self):
        """P79.7m of opex was in Manus's 2026-08-15 brief and is not supported by
        the accounts. It would have shown a breach on a book that is compliant —
        the exact false alarm this test exists to catch."""
        real = self._check('28244967.74')
        self.assertEqual(real['status'], 'compliant')
        claimed = self._check('79700000')
        self.assertEqual(claimed['required_capital'], '19925000.00',
                         'reproduces the P19.93m in the brief, from P79.7m opex')
        self.assertEqual(claimed['status'], 'breach')

    def test_the_reinsurers_share_does_not_inflate_own_funds(self):
        """The P14.15m add-back is entirely reinsurance share. Schedule 1 does
        not list it as an allowed asset, so available capital must be equity
        less intangibles and nothing else."""
        out = self._check('28244967.74', equity=Decimal('17010997.59'),
                          ri_share=Decimal('14152382.85'))
        self.assertEqual(out['available_capital'], '17010997.59',
                         'own funds are equity less intangibles — the P14.15m '
                         'reinsurers\' share must not be added in')
        self.assertEqual(out['components']['reinsurance_share_excluded'],
                         '14152382.85',
                         'and it must still be disclosed, for the audit trail '
                         'and any item 8.11(d) application')
        self.assertNotIn('negative_reserves_addback', out['components'],
                         'the add-back must not return under its old name')

    def test_no_requirement_is_invented_when_opex_cannot_be_built(self):
        with patch('regulatory.services._estimated_next_year_opex',
                   return_value={'amount': None, 'basis': 'unavailable',
                                 'period': None, 'note': 'no P&L'}), \
             patch('regulatory.services._total_equity_at',
                   return_value=Decimal('17010997.59')):
            out = compute_capital_check(date(2026, 8, 15))
        self.assertEqual(out['status'], 'under_revision')
        self.assertIsNone(out['required_capital'])
        self.assertIsNone(out['capital_adequacy_ratio'])
