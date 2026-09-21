"""infer_type must not read the 1104xx fixed-asset block as employee cost.

Found by the Omni bug-board QC pass on 31-Aug-2026 (row ae1b8bc4): the DB has
110402.01 'Accumulated Depreciation - Motor Vehicles' correctly as an asset,
but the prefix map still returned expense for anything starting 1104 — the
greedy '110' (Employee costs) entry matched first. Existing accounts are never
retyped on re-import, so only newly created 1104xx accounts were at risk.
"""
from decimal import Decimal

from django.test import SimpleTestCase

from ledger.management.commands.import_tb_csv import infer_type

DR = Decimal('1000.00')
CR = Decimal('0.00')


class InferTypeFixedAssetPrefixTests(SimpleTestCase):
    def test_accumulated_depreciation_motor_vehicles_is_an_asset(self):
        self.assertEqual(
            infer_type('110402.01', DR, CR), ('asset', 'accumulated_depreciation')
        )

    def test_accumulated_depreciation_furniture_and_laptops_are_assets(self):
        self.assertEqual(
            infer_type('110400.01', DR, CR), ('asset', 'accumulated_depreciation')
        )
        self.assertEqual(
            infer_type('110401.01', DR, CR), ('asset', 'accumulated_depreciation')
        )

    def test_fixed_asset_cost_accounts_are_fixed_assets(self):
        # 110402 (Motor Vehicles, cost) is in this list deliberately. The first
        # cut of this fix mapped the bare '110402' to accumulated_depreciation,
        # which would have typed the motor-vehicle COST account as its own
        # contra the moment Finance created it — prod holds 110402.01 today but
        # not 110402, so nothing would have caught it (checked on prod
        # 2026-09-15, five 1104xx rows, no 110402).
        for code in ('110400', '110401', '110402', '110403'):
            with self.subTest(code=code):
                self.assertEqual(infer_type(code, DR, CR), ('asset', 'fixed_asset'))

    def test_employee_cost_codes_are_untouched(self):
        # 1100xx payroll codes must still infer expense — the new entries are
        # narrower than '110' and must not shadow it.
        for code in ('110010', '110004', '110016'):
            with self.subTest(code=code):
                self.assertEqual(
                    infer_type(code, DR, CR), ('expense', 'operating_expense')
                )
