"""
core/smart_upload/sections.py

Canonical schema per importable section. The DeepSeek mapper uses these
to figure out which raw header maps to which canonical field. The
committers use them to validate the rows before write.

Each entry:
  label:        human-readable name (shown in CFO UI)
  fields:       list[FieldSpec] — canonical fields the committer expects
  hints:        list[str] — examples of how raw headers may be spelled
  committer:    function imported lazily via section_committer()
  needs_company: True if every row gets stamped with company_id
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FieldSpec:
    name: str
    label: str
    required: bool = False
    kind: str = 'str'             # str|decimal|int|date|bool
    hint: str = ''                # plain-English description fed to AI


@dataclass
class SectionSpec:
    key: str
    label: str
    fields: list[FieldSpec]
    needs_company: bool = True
    accept_companies: tuple[str, ...] = ()   # empty = all companies
    description: str = ''


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

SECTIONS: dict[str, SectionSpec] = {
    'coa': SectionSpec(
        key='coa',
        label='Chart of Accounts',
        description='Adds / updates GL accounts. Codes are stamped with the '
                    'company prefix automatically (e.g. ADSA_400000).',
        fields=[
            FieldSpec('code',         'Account code',     required=True,
                      hint='Numeric GL code, e.g. 400000 or ADSA_400000'),
            FieldSpec('name',         'Account name',     required=True,
                      hint='Description, e.g. "Sales — Motor"'),
            FieldSpec('account_type', 'Account type',     required=False,
                      hint='asset / liability / equity / revenue / expense'),
            FieldSpec('sub_type',     'Account sub-type', required=False,
                      hint='current_asset / receivable / bank / etc.'),
            FieldSpec('parent_code',  'Parent code',      required=False,
                      hint='Parent GL code for tree views'),
            FieldSpec('is_summary_only', 'Summary only',  required=False, kind='bool',
                      hint='Y/N — header accounts that take no postings'),
        ],
    ),

    'tb': SectionSpec(
        key='tb',
        label='Trial Balance',
        description='Posts a balanced JE summarising the period activity.',
        fields=[
            FieldSpec('account_code', 'Account code',  required=True),
            FieldSpec('account_name', 'Account name',  required=False),
            FieldSpec('debit',        'Debit',         required=True,  kind='decimal'),
            FieldSpec('credit',       'Credit',        required=True,  kind='decimal'),
            FieldSpec('period_end',   'Period end',    required=True,  kind='date',
                      hint='Last day of the TB period, e.g. 2025-06-30'),
            FieldSpec('period_label', 'Period label',  required=False,
                      hint='e.g. "FY25" or "Jun 2025"'),
        ],
    ),

    'gl': SectionSpec(
        key='gl',
        label='General Ledger',
        description='Posts one JE per entry_ref. Each ref must balance.',
        fields=[
            FieldSpec('entry_date',   'Entry date',    required=True, kind='date'),
            FieldSpec('entry_ref',    'Entry ref',     required=True),
            FieldSpec('description',  'Description',   required=False),
            FieldSpec('line_no',      'Line no',       required=False, kind='int'),
            FieldSpec('account_code', 'Account code',  required=True),
            FieldSpec('debit',        'Debit',         required=True,  kind='decimal'),
            FieldSpec('credit',       'Credit',        required=True,  kind='decimal'),
            FieldSpec('memo',         'Memo',          required=False),
        ],
    ),

    'vendors': SectionSpec(
        key='vendors',
        label='Vendors / Suppliers',
        description='Creates / updates AP contacts scoped to the selected company.',
        fields=[
            FieldSpec('code',         'Supplier code', required=False),
            FieldSpec('name',         'Supplier name', required=True),
            FieldSpec('tax_id',       'VAT / Tax ID',  required=False),
            FieldSpec('email',        'Email',         required=False),
            FieldSpec('phone',        'Phone',         required=False),
            FieldSpec('payment_terms','Payment terms', required=False),
            FieldSpec('default_gl_expense_code', 'Default expense GL', required=False),
            FieldSpec('is_active',    'Active',        required=False, kind='bool'),
        ],
    ),

    'customers': SectionSpec(
        key='customers',
        label='Customers',
        description='Creates / updates AR contacts scoped to the selected company.',
        fields=[
            FieldSpec('code',         'Customer code', required=False),
            FieldSpec('name',         'Customer name', required=True),
            FieldSpec('tax_id',       'VAT / Tax ID',  required=False),
            FieldSpec('email',        'Email',         required=False),
            FieldSpec('phone',        'Phone',         required=False),
            FieldSpec('payment_terms','Payment terms', required=False),
            FieldSpec('default_gl_ar_code', 'Default AR GL', required=False),
            FieldSpec('is_active',    'Active',        required=False, kind='bool'),
        ],
    ),

    'ppe': SectionSpec(
        key='ppe',
        label='Fixed Assets (PP&E)',
        description='Creates Asset records under the selected company.',
        fields=[
            FieldSpec('tag_number',           'Tag / asset code',   required=True),
            FieldSpec('name',                 'Asset name',         required=True),
            FieldSpec('gl_account',           'Cost GL code',       required=False),
            FieldSpec('cost',                 'Cost / original value', required=True, kind='decimal'),
            FieldSpec('salvage_value',        'Salvage value',      required=False, kind='decimal'),
            FieldSpec('opening_accumulated_depreciation',
                                              'Opening accum depr', required=False, kind='decimal'),
            FieldSpec('purchase_date',        'Purchase date',      required=False, kind='date'),
            FieldSpec('in_service_date',      'In-service date',    required=False, kind='date'),
            FieldSpec('useful_life_months',   'Useful life (months)', required=False, kind='int'),
            FieldSpec('method',               'Depr method',        required=False,
                      hint='straight_line / declining_balance / units'),
            FieldSpec('location',             'Location',           required=False),
            FieldSpec('custodian',            'Custodian',          required=False),
        ],
    ),

    'bank_accounts': SectionSpec(
        key='bank_accounts',
        label='Bank Accounts',
        description='Registers a bank account against an existing GL account.',
        fields=[
            FieldSpec('account_name',  'Account name',    required=True),
            FieldSpec('gl_account',    'GL account code', required=True,
                      hint='Must already exist for the selected company.'),
            FieldSpec('bank_name',     'Bank name',       required=False),
            FieldSpec('branch_code',   'Branch code',     required=False),
            FieldSpec('account_number','Account number',  required=False),
            FieldSpec('currency_code', 'Currency',        required=False,
                      hint='BWP / ZAR / USD — defaults to company base.'),
            FieldSpec('is_active',     'Active',          required=False, kind='bool'),
        ],
    ),

    'employees': SectionSpec(
        key='employees',
        label='Employees',
        description='Creates / updates Employee + HRIS profile rows.',
        fields=[
            FieldSpec('employee_code', 'Employee code', required=True),
            FieldSpec('first_name',    'First name',    required=True),
            FieldSpec('last_name',     'Surname',       required=True),
            FieldSpec('email',         'Email',         required=False),
            FieldSpec('phone',         'Phone',         required=False),
            FieldSpec('job_title',     'Job title',     required=False),
            FieldSpec('department',    'Department',    required=False),
            FieldSpec('hire_date',     'Hire date',     required=False, kind='date'),
            FieldSpec('national_id',   'National ID',   required=False),
            FieldSpec('is_active',     'Active',        required=False, kind='bool'),
        ],
    ),

    # Payroll — the canonical field list mirrors the CFO's payroll register
    # one-for-one, and every monetary field below is named after the
    # PayslipComponent code it commits to (see PAYROLL_FIELD_TO_COMPONENT in
    # core/smart_upload/committers.py).
    #
    # Bug report Pako Kago 2026-07-29 (ADIC July 2026): the register has 30
    # columns but this section only declared 8 fields, so 24 columns had
    # nowhere to land and came back "— unmapped —". Commission (87,774.22)
    # and Incentive (30,300.00) then never reached the Payroll dashboard,
    # because the dashboard reads PayslipLine components, and a summary-only
    # commit wrote no lines at all. Declaring the real register here is the
    # fix for BOTH halves of that report.
    #
    # employee_code is NOT required: HR backfills staff numbers later and the
    # July register carries "nill" codes on five rows. employee_name is the
    # identity key. gross + net stay required so the UI's Commit gate blocks
    # a register whose GROSS / NET PAY columns didn't map.
    'payroll': SectionSpec(
        key='payroll',
        label='Payroll runs',
        description='Imports a payroll register per employee: every earning, '
                    'deduction and company contribution becomes a payslip '
                    'line, so the Payroll dashboard shows the full breakdown.',
        fields=[
            # ── Identity + period ───────────────────────────────────────────
            FieldSpec('employee_name', 'Employee',      required=True,
                      hint='Full name as it appears on the register.'),
            FieldSpec('employee_code', 'Employee code', required=False,
                      hint='Staff number. Blank / "nill" is allowed — HR backfills later.'),
            FieldSpec('department',    'Department',    required=False),
            FieldSpec('period_label',  'Period label',  required=False,
                      hint='e.g. "2026-07". Taken from the upload form when absent.'),
            FieldSpec('period_end',    'Period end',    required=False, kind='date'),

            # ── Earnings ────────────────────────────────────────────────────
            FieldSpec('basic',                 'Basic salary',        required=False, kind='decimal'),
            FieldSpec('commission',            'Commission',          required=False, kind='decimal'),
            FieldSpec('incentive',             'Incentive',           required=False, kind='decimal'),
            FieldSpec('bonus',                 'Bonus',               required=False, kind='decimal'),
            FieldSpec('po_allowance',          'PO allowance',        required=False, kind='decimal',
                      hint='Principal Officer allowance ("PO Allow").'),
            FieldSpec('allowance',             'Allowance',           required=False, kind='decimal'),
            FieldSpec('housing_allowance',     'Housing allowance',   required=False, kind='decimal'),
            FieldSpec('leave_pay',             'Leave pay',           required=False, kind='decimal'),
            FieldSpec('medical_aid_allowance', 'Med aid allowance',   required=False, kind='decimal'),
            FieldSpec('vehicle_allowance',     'Vehicle allowance',   required=False, kind='decimal'),
            FieldSpec('health_ins_allowance',  'Health ins allowance',required=False, kind='decimal'),
            FieldSpec('fuel_allowance',        'Fuel allowance',      required=False, kind='decimal'),
            FieldSpec('mobile_allowance',      'Mobile allowance',    required=False, kind='decimal'),
            FieldSpec('internet_allowance',    'Internet allowance',  required=False, kind='decimal'),
            FieldSpec('sales_allowance',       'Sales allowance',     required=False, kind='decimal'),
            FieldSpec('severance',             'Severance',           required=False, kind='decimal'),
            FieldSpec('non_cash_benefit',      'Non-cash benefits',   required=False, kind='decimal'),

            # ── Employee deductions + tax ───────────────────────────────────
            FieldSpec('paye',           'PAYE',                required=False, kind='decimal'),
            FieldSpec('loans_deduction','Loans deduction',     required=False, kind='decimal'),
            FieldSpec('housing_tax',    'Housing tax deduction', required=False, kind='decimal'),
            FieldSpec('medical_aid_ee', 'Med aid EE',          required=False, kind='decimal'),
            FieldSpec('pension_ee',     'Pension EE',          required=False, kind='decimal'),
            FieldSpec('provident_ee',   'Provident fund EE',   required=False, kind='decimal'),

            # ── Company contributions ───────────────────────────────────────
            FieldSpec('medical_aid_er', 'Med aid ER',          required=False, kind='decimal'),
            FieldSpec('pension_er',     'Pension ER',          required=False, kind='decimal'),
            FieldSpec('provident_er',   'Provident fund ER',   required=False, kind='decimal'),

            # ── Totals from the file — cross-check only, never source of truth.
            # Totals are recomputed from the committed lines (Payslip
            # .recompute_totals), and any variance is reported back.
            FieldSpec('gross',            'GROSS',            required=True,  kind='decimal'),
            FieldSpec('total_deductions', 'Total deductions', required=False, kind='decimal'),
            FieldSpec('net',              'NET PAY',          required=True,  kind='decimal'),
            FieldSpec('ctc',              'CTC',              required=False, kind='decimal'),
            FieldSpec('status',           'Status',           required=False),
        ],
    ),
}


def get_section(key: str) -> SectionSpec:
    if key not in SECTIONS:
        raise KeyError(f'Unknown smart-upload section: {key!r}')
    return SECTIONS[key]


def list_sections() -> list[dict]:
    out = []
    for s in SECTIONS.values():
        out.append({
            'key':           s.key,
            'label':         s.label,
            'description':   s.description,
            'needs_company': s.needs_company,
            'fields': [
                {'name': f.name, 'label': f.label, 'required': f.required,
                 'kind': f.kind, 'hint': f.hint}
                for f in s.fields
            ],
        })
    return out
