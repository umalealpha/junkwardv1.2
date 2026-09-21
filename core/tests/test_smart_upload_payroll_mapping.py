"""
core/tests/test_smart_upload_payroll_mapping.py

Regression test for the bug Pako Kago reported on 2026-07-29 (ADIC July 2026
payroll register): Smart Upload mapped only 6 of 30 columns, so every monetary
column came back "— unmapped —" and Commission (87,774.22) / Incentive
(30,300.00) never reached the Payroll dashboard.

These tests use heuristic_map() only — no network, no DeepSeek, no DB. If a
future edit re-breaks header matching, this fails locally in milliseconds.
"""

from django.test import SimpleTestCase

from core.smart_upload.mapper import heuristic_map
from core.smart_upload.sections import get_section


# The exact header row of the CFO's register, verbatim from the bug report.
ADIC_JULY_HEADERS = [
    'Employee No', 'Employee', 'Department', 'Period',
    'Basic Salary', 'Commission', 'Incentive', 'PO Allow', 'Allow',
    'Housing Allow', 'Leave Pay', 'Med Aid allowance', 'Vehicle Allow',
    'Health Ins Allow', 'Fuel Allow', 'Internet Allow',
    'GROSS', 'PAYE', 'Loans Ded', 'Housing Tax Ded',
    'Med Aid EE', 'Pension EE', 'Provident Fund EE',
    'TOTAL DED', 'NET PAY',
    'Med Aid ER', 'Pension ER', 'Provident Fund ER',
    'CTC', 'Status',
]

EXPECTED = {
    'Employee No':          'employee_code',
    'Employee':             'employee_name',
    'Department':           'department',
    'Period':               'period_label',
    'Basic Salary':         'basic',
    'Commission':           'commission',
    'Incentive':            'incentive',
    'PO Allow':             'po_allowance',
    'Allow':                'allowance',
    'Housing Allow':        'housing_allowance',
    'Leave Pay':            'leave_pay',
    'Med Aid allowance':    'medical_aid_allowance',
    'Vehicle Allow':        'vehicle_allowance',
    'Health Ins Allow':     'health_ins_allowance',
    'Fuel Allow':           'fuel_allowance',
    'Internet Allow':       'internet_allowance',
    'GROSS':                'gross',
    'PAYE':                 'paye',
    'Loans Ded':            'loans_deduction',
    'Housing Tax Ded':      'housing_tax',
    'Med Aid EE':           'medical_aid_ee',
    'Pension EE':           'pension_ee',
    'Provident Fund EE':    'provident_ee',
    'TOTAL DED':            'total_deductions',
    'NET PAY':              'net',
    'Med Aid ER':           'medical_aid_er',
    'Pension ER':           'pension_er',
    'Provident Fund ER':    'provident_er',
    'CTC':                  'ctc',
    'Status':               'status',
}


class PayrollHeaderMappingTests(SimpleTestCase):

    def setUp(self):
        self.section = get_section('payroll')
        self.mapping = heuristic_map(ADIC_JULY_HEADERS, self.section)

    def test_every_register_column_maps(self):
        """All 30 columns map — the headline defect in the bug report."""
        unmapped = [h for h in ADIC_JULY_HEADERS if h not in self.mapping]
        self.assertEqual(
            unmapped, [],
            f'{len(unmapped)} of {len(ADIC_JULY_HEADERS)} columns unmapped: {unmapped}',
        )

    def test_each_column_maps_to_the_right_field(self):
        """Header-by-header, not just a count — a wrong target is worse than
        an unmapped column, because it silently books money to the wrong line."""
        for header, expected_field in EXPECTED.items():
            with self.subTest(header=header):
                self.assertEqual(self.mapping.get(header), expected_field)

    def test_commission_and_incentive_map(self):
        """Problem 2 of the report, asserted on its own so a failure names it."""
        self.assertEqual(self.mapping.get('Commission'), 'commission')
        self.assertEqual(self.mapping.get('Incentive'), 'incentive')

    def test_ee_er_and_allowance_families_are_not_confused(self):
        """The near-identical families are the ones a loose substring matcher
        gets wrong: EE vs ER contributions, and the five "* Allow" columns."""
        self.assertEqual(self.mapping.get('Med Aid EE'), 'medical_aid_ee')
        self.assertEqual(self.mapping.get('Med Aid ER'), 'medical_aid_er')
        self.assertEqual(self.mapping.get('Med Aid allowance'), 'medical_aid_allowance')
        self.assertEqual(self.mapping.get('Pension EE'), 'pension_ee')
        self.assertEqual(self.mapping.get('Pension ER'), 'pension_er')
        self.assertEqual(self.mapping.get('Provident Fund EE'), 'provident_ee')
        self.assertEqual(self.mapping.get('Provident Fund ER'), 'provident_er')

    def test_internet_allow_does_not_steal_net(self):
        """The net→"Internet Allow" regression (Legakwa 2026-06-25) must stay
        fixed now that both columns are in the same register."""
        self.assertEqual(self.mapping.get('Internet Allow'), 'internet_allowance')
        self.assertEqual(self.mapping.get('NET PAY'), 'net')

    def test_housing_tax_ded_does_not_steal_paye(self):
        """'Housing Tax Ded' contains the token 'tax', a PAYE synonym."""
        self.assertEqual(self.mapping.get('Housing Tax Ded'), 'housing_tax')
        self.assertEqual(self.mapping.get('PAYE'), 'paye')

    def test_no_canonical_field_claimed_twice(self):
        targets = list(self.mapping.values())
        self.assertEqual(len(targets), len(set(targets)),
                         f'a canonical field was claimed twice: {targets}')

    def test_required_fields_all_resolve(self):
        """No required field left over → the UI Commit gate stays open for a
        clean register (and closes for a broken one)."""
        required = {f.name for f in self.section.fields if f.required}
        self.assertTrue(required.issubset(set(self.mapping.values())),
                        f'unresolved required: {required - set(self.mapping.values())}')

    def test_gross_and_net_are_required_so_commit_can_be_gated(self):
        required = {f.name for f in self.section.fields if f.required}
        self.assertIn('gross', required)
        self.assertIn('net', required)

    def test_employee_code_is_optional(self):
        """HR backfills staff numbers later; the July register has five rows
        with "nill" codes and they must not block the upload."""
        required = {f.name for f in self.section.fields if f.required}
        self.assertNotIn('employee_code', required)
