"""The FAC master importer, exercised end to end.

Written 16-Sep-2026 after the FY27 load: a row naming two reinsurers in one
cell was recorded with a placed amount of zero, so the live register understated
ceded exposure by BWP 3,000,000 and nothing on the row said why. The sheet does
not say how such a row splits between the two counterparties — that is the only
unknown. The risk itself is placed in full, so the amount belongs on the row.
"""
from contextlib import contextmanager
from decimal import Decimal
from unittest import mock

from django.core.management import call_command
from django.test import TestCase

from reinsurance.models import FacReinsurerAllocation, FacRiskExposure

HEADER = ['Broker', 'Reinsurer', 'Share', 'Slip', 'Period', '', 'Policy',
          'Insured', 'Class', 'Cession', 'Premium', '', '', '', 'Commission']


def _row(reinsurer, cession, premium):
    return ['Broker Ltd', reinsurer, 0.5, 'SLIP/1', '01/07/2026 to 30/06/2027', '',
            'POL-1', 'Insured Ltd', 'Fire', cession, premium, '', '', '', 0.0]


class _Cell:
    def __init__(self, v):
        self.v = v


class _Sheet:
    def __init__(self, rows):
        self._rows = rows

    def rows(self):
        for r in self._rows:
            yield [_Cell(x) for x in r]


class _Book:
    def __init__(self, rows):
        self._rows = rows

    @contextmanager
    def get_sheet(self, name):
        yield _Sheet(self._rows)


def _fake_workbook(rows):
    @contextmanager
    def _open(path):
        yield _Book([HEADER] + rows)
    return _open


class SharedPlacementTests(TestCase):
    """One reinsurer named, and two reinsurers named in the same cell."""

    def _run(self, rows):
        with mock.patch('pyxlsb.open_workbook', _fake_workbook(rows)):
            call_command('import_fac_master', '/tmp/sheet.xlsb',
                         '--fy', 'FY99', '--commit')

    def test_single_reinsurer_row_is_placed_and_allocated(self):
        self._run([_row('Grand Re', 1000000, 5000)])

        exposure = FacRiskExposure.objects.get(reference='FY99-0002')
        self.assertEqual(exposure.fac_placed_amount, Decimal('1000000.00'))
        self.assertEqual(exposure.status, FacRiskExposure.Status.PLACED)
        self.assertEqual(exposure.import_warnings, [])
        self.assertEqual(FacReinsurerAllocation.objects.count(), 1)

    def test_two_reinsurers_in_one_cell_keep_the_full_amount(self):
        """The bug this test exists for: the amount was being zeroed."""
        self._run([_row('P & C Re, Saha Re', 2000000, 12500)])

        exposure = FacRiskExposure.objects.get(reference='FY99-0002')
        self.assertEqual(exposure.fac_placed_amount, Decimal('2000000.00'),
                         'the risk is placed in full — only the split is unknown')
        self.assertEqual(exposure.ceded_premium, Decimal('12500.00'))
        self.assertIn('NOT split', ' '.join(exposure.import_warnings))
        self.assertEqual(exposure.status, FacRiskExposure.Status.PARTIALLY_PLACED)
        self.assertEqual(FacReinsurerAllocation.objects.count(), 0,
                         'the share is unknown, so it must not be allocated')

    def test_register_total_matches_the_sheet_when_a_row_is_shared(self):
        self._run([_row('Grand Re', 1000000, 5000),
                   _row('P & C Re, Saha Re', 2000000, 12500)])

        total = sum(e.fac_placed_amount for e in FacRiskExposure.objects.all())
        self.assertEqual(total, Decimal('3000000.00'))

    def test_grand_total_row_is_skipped(self):
        self._run([_row('Grand Re', 1000000, 5000),
                   _row('Grand Total', 1000000, 5000)])

        self.assertEqual(FacRiskExposure.objects.count(), 1)
