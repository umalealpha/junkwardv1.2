"""
payroll/test_bank_mask.py — DPA L-1: bank account number is masked over the API.

The API must never emit the full account number (mask to last-4), and a masked
value coming back on save must NOT overwrite the real number (corruption guard).
CFO / Fable DPA batch 2026-07-19.
"""
from django.test import TestCase

from payroll.models import Employee
from payroll.serializers import EmployeeSerializer


class BankAccountMaskTest(TestCase):

    def test_api_output_masks_to_last4(self):
        emp = Employee.objects.create(employee_number='E-MASK1', full_name='Mask Tester',
                                      bank_account_no='1234567890')
        self.assertEqual(EmployeeSerializer(emp).data['bank_account_no'], '••••7890')

    def test_short_number_fully_masked(self):
        emp = Employee.objects.create(employee_number='E-MASK3', full_name='Short Tester',
                                      bank_account_no='12')
        self.assertEqual(EmployeeSerializer(emp).data['bank_account_no'], '••••')

    def test_write_guard_ignores_masked_value(self):
        s = EmployeeSerializer()
        self.assertNotIn('bank_account_no', s._drop_masked_account(
            {'bank_account_no': '••••7890', 'full_name': 'X'}))
        self.assertEqual(s._drop_masked_account({'bank_account_no': '5559998888'})['bank_account_no'],
                         '5559998888')

    def test_update_preserves_real_number_when_masked_submitted(self):
        emp = Employee.objects.create(employee_number='E-MASK2', full_name='Keep Tester',
                                      bank_account_no='9998887777')
        s = EmployeeSerializer(emp, data={'full_name': 'Keep Tester',
                                          'bank_account_no': '••••7777'}, partial=True)
        s.is_valid(raise_exception=True)
        s.save()
        emp.refresh_from_db()
        self.assertEqual(emp.bank_account_no, '9998887777')   # real number preserved
