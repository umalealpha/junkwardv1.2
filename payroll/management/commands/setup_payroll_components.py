"""
payroll/management/commands/setup_payroll_components.py

Idempotent — seeds the 29 payslip components matching the CFO's payroll
headers. Safe to run repeatedly; existing components are left alone.
"""

from django.core.management.base import BaseCommand

from payroll.models import PayslipComponent


# (sort_order, code, name, kind, is_taxable)
COMPONENTS = [
    # Earnings (taxable unless flagged)
    ( 10, 'BASIC',                'Basic Salary',                          'earning',             True),
    ( 20, 'COMMISSION',           'Commission',                            'earning',             True),
    ( 30, 'INCENTIVE',            'Incentive',                             'earning',             True),
    ( 35, 'BONUS',                'Bonus',                                 'earning',             True),
    ( 40, 'PO_ALLOWANCE',         'Principal Officer Allowance',           'earning',             True),
    ( 50, 'ALLOWANCE',            'Allowance',                             'earning',             True),
    ( 60, 'HOUSING_ALLOWANCE',    'Housing Allowance',                     'earning',             True),
    # PAY-008 (CFO directive 2026-05-29) — BURS § 32 housing benefit.
    # Replaces HOUSING_ALLOWANCE when the employee is on a salary-sacrifice
    # arrangement (company leases the property + pays the landlord direct).
    # Value computed by Payslip.apply_housing_sacrifice() from Employee fields.
    ( 65, 'HOUSING_BENEFIT',      'Housing Benefit (BURS § 32)',           'earning',             True),
    ( 66, 'FURNITURE_BENEFIT',    'Furniture Benefit (BURS)',              'earning',             True),
    ( 70, 'LEAVE_PAY',            'Leave Pay',                             'earning',             True),
    ( 80, 'MEDICAL_AID_ALLOWANCE','Medical aid allowance',                 'earning',             True),
    ( 90, 'VEHICLE_ALLOWANCE',    'Vehicle Allowance',                     'earning',             True),
    (100, 'SEVERANCE',            'Severance Pay',                         'earning',             True),
    (110, 'SALES_ALLOWANCE',      'Sales Allowance',                       'earning',             True),
    (120, 'HEALTH_INS_ALLOWANCE', 'Health Insurance Allowance',            'earning',             True),
    (130, 'FUEL_ALLOWANCE',       'Fuel Allowance',                        'earning',             True),
    (140, 'MOBILE_ALLOWANCE',     'Mobile Allowance',                      'earning',             True),
    (150, 'INTERNET_ALLOWANCE',   'Internet Allowance',                    'earning',             True),
    (160, 'NON_CASH_BENEFIT',     'Non-Cash Benefits',                     'earning',             True),

    # Deductions
    (200, 'LOANS_DEDUCTION',      'Loans Deduction',                       'employee_deduction',  False),
    (210, 'HOUSING_TAX',          'Housing Tax Deduction',                 'employee_deduction',  False),
    (215, 'HOUSING_DEDUCTION',    'Housing Deduction',                     'employee_deduction',  False),
    (220, 'MEDICAL_AID_EE',       'Medical Aid Employee Contribution',     'employee_pretax',     False),
    (230, 'PENSION_EE',           'Pension Employee Contribution',         'employee_pretax',     False),
    (240, 'PROVIDENT_EE',         'Provident Fund Employee Contribution',  'employee_pretax',     False),

    # Tax
    (300, 'PAYE',                 'PAYE',                                  'tax',                 False),

    # Company contributions
    (400, 'MEDICAL_AID_ER',       'Medical Aid Company Contribution',      'company_contribution',False),
    (410, 'PENSION_ER',           'Pension Company Contribution',          'company_contribution',False),
    (420, 'PROVIDENT_ER',         'Provident Fund Company Contribution',   'company_contribution',False),

    # Computed totals (system-managed, not editable per payslip)
    (500, 'GROSS',                'Gross',                                 'computed_gross',      False),
    (510, 'NET',                  'Net Salary',                            'computed_net',        False),
    (520, 'CTC',                  'CTC',                                   'computed_ctc',        False),
]


# ONE-OFF pay: belongs to the month it was earned and must NEVER roll forward
# into the next period (CFO 2026-09-20 — a commission/incentive/leave-pay/
# severance copied by the roll-forward was paid a second time). Everything not
# listed here is recurring. Mirrors payroll/migrations/0030's data step.
ONE_OFF_CODES = frozenset({
    'COMMISSION', 'INCENTIVE', 'LEAVE_PAY', 'SEVERANCE',
    'BONUS', 'OT', 'ARREARS', 'REIMBURSEMENT',
})


class Command(BaseCommand):
    help = 'Seed the 29 payslip components matching the CFO payroll headers.'

    def handle(self, *args, **options):
        created = 0
        skipped = 0
        for sort_order, code, name, kind, taxable in COMPONENTS:
            obj, was_created = PayslipComponent.objects.get_or_create(
                code=code,
                defaults={
                    'name': name,
                    'kind': kind,
                    'sort_order': sort_order,
                    'is_taxable': taxable,
                    'is_active': True,
                    'is_recurring': code not in ONE_OFF_CODES,
                },
            )
            if was_created:
                created += 1
                self.stdout.write(self.style.SUCCESS(f'  + {code} {name}'))
            else:
                skipped += 1
        self.stdout.write(self.style.SUCCESS(
            f'\nDone. {created} created, {skipped} already present.'
        ))
