"""
reporting/entity_pl_templates.py

Per-entity Profit & Loss templates for the non-ADIC group entities.

Background
==========
The omni Management-Accounts P&L was originally hardcoded against the
Alpha Direct Insurance (ADIC) chart of accounts: GWP, claims, reinsurance,
acquisition cost, etc. CFO directive 2026-05-21 ("Group P&L Prompt"
PDF) requires that the other ten group entities each render their own
P&L structure — investment holdings, salvage, software, distribution,
coin exchange, commission agency, insurtech, etc.

This module is the authoritative config. Each entity has:
  * `entity_code`   — DB Company.code
  * `pdf_alias`     — short code used in the PDF spec (AD/QTM/VER/...)
  * `report_title`  — header label
  * `currency`      — display currency (ISO 4217). Falls back to
                      Company.base_currency.code if blank.
  * `sections`      — ordered list of section dicts.

Each section:
  * `id`, `label`               — internal id + display label
  * `sign`                      — 'income' / 'expense' (drives subtotal sign)
  * `subtotal_label`            — label rendered after the lines
  * `lines`                     — ordered list of line dicts

Each line:
  * `id`, `label`               — internal id + display label
  * `keywords`                  — list of substrings; the engine matches
                                  Account.name (case-insensitive). Any
                                  match qualifies.
  * `exclude` (optional)        — substrings that disqualify a match
                                  (e.g. "Interest Income" line excludes
                                  "from Bank" / "from Related Party"
                                  rows so they don't double-count).

Totals are computed by the engine from `formula` blocks per template —
see `reporting/entity_pl_engine.py`.

ADIC IS NOT REPRESENTED HERE — it continues to use the legacy MA P&L
spec in `reporting/ma_pl_spec.py`. The engine dispatches on entity
code: ADIC → ma_pl, anything else → this module.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# Common section / line shape
# ---------------------------------------------------------------------------

def _line(id_, label, *keywords, exclude=None):
    return {'id': id_, 'label': label, 'keywords': list(keywords),
            'exclude': list(exclude or [])}


# ---------------------------------------------------------------------------
# Templates — keyed by DB Company.code
# ---------------------------------------------------------------------------

TEMPLATES = {

    # ── Quantum Insurance Holdings (PDF alias QTM) ────────────────────────
    'QIH': {
        'entity_code': 'QIH',
        'pdf_alias':   'QTM',
        'report_title': 'Profit & Loss Statement',
        'currency':    'BWP',
        'sections': [
            {
                'id': 'revenue', 'label': 'Revenue', 'sign': 'income',
                'subtotal_label': 'Total Revenue',
                'lines': [
                    _line('product_sales',          'Product Sales',                       'product sales'),
                    _line('interest_income',        'Interest Income',                     'interest income',
                          exclude=['from bank', 'related party', 'bank']),
                    _line('interest_income_bank',   'Interest Income from Bank',           'interest income from bank', 'interest from bank'),
                    _line('interest_income_rp',     'Interest Income from Related Party',  'interest income from related party', 'related party'),
                    _line('fx_gain',                'Foreign Exchange Gain',               'foreign exchange gain', 'fx gain'),
                    _line('other_income',           'Other Income',                        'other income'),
                ],
            },
            {
                'id': 'opex', 'label': 'Operating Expenses', 'sign': 'expense',
                'subtotal_label': 'Total Operating Expenses',
                'lines': [
                    _line('accounting_secretarial', 'Accounting & Secretarial', 'accounting', 'secretarial'),
                    _line('professional_fees',     'Professional Fees',         'professional fees'),
                    _line('tax_expense',           'Tax Expense',               'tax expense', 'income tax'),
                    _line('consultancy_fees',      'Consultancy Fees',          'consultancy'),
                    _line('legal_fees',            'Legal Fees',                'legal fees'),
                    _line('bank_fees',             'Bank Fees',                 'bank fees', 'bank charges'),
                    _line('salary_expenses',       'Salary Expenses',           'salary'),
                ],
            },
            {
                'id': 'finance_costs', 'label': 'Finance Costs', 'sign': 'expense',
                'subtotal_label': 'Total Finance Costs',
                'lines': [
                    _line('interest_expenses', 'Interest Expenses', 'interest expense', 'interest expenses'),
                ],
            },
        ],
        'totals': [
            {'id': 'operating_profit', 'label': 'Operating Profit / (Loss)',
             'formula': 'revenue - opex'},
            {'id': 'pbt', 'label': 'Net Profit / (Loss) Before Tax',
             'formula': 'operating_profit - finance_costs'},
        ],
    },

    # ── Veritas Capital (PDF alias VER — salvage) ─────────────────────────
    'VCM': {
        'entity_code': 'VCM',
        'pdf_alias':   'VER',
        'report_title': 'Profit & Loss Statement',
        'currency':    'BWP',
        'sections': [
            {
                'id': 'revenue', 'label': 'Revenue', 'sign': 'income',
                'subtotal_label': 'Total Revenue',
                'lines': [
                    _line('non_motor_sales',      'Non-Motor Sales',          'non-motor sales', 'non motor sales'),
                    _line('sale_salvage_vehicle', 'Sale of Salvage Vehicles', 'sale of salvage vehicles', 'salvage vehicle'),
                    _line('sale_salvage_parts',   'Sale of Salvage Parts',    'sale of salvage parts', 'salvage parts'),
                    _line('other_income',         'Other Income',             'other income'),
                    _line('management_fee',       'Management Fee',           'management fee'),
                ],
            },
            {
                'id': 'cogs', 'label': 'Cost of Sales', 'sign': 'expense',
                'subtotal_label': 'Total Cost of Sales',
                'lines': [
                    _line('cogs',                 'Cost of Goods Sold',                  'cost of goods sold', 'cogs',
                          exclude=['salvage', 'provision', 'towing']),
                    _line('cogs_salvage_vehicle', 'COGS - Salvage Vehicle',              'cogs - salvage vehicle', 'cogs salvage vehicle'),
                    _line('cogs_salvage_adj',     'COGS - Salvage Adjustments',          'cogs - salvage adjustments', 'salvage adjustments'),
                    _line('cogs_obsolete_stock',  'COGS - Provision for Obsolete Stock', 'provision for obsolete', 'obsolete stock'),
                    _line('cogs_towing',          'COGS - Towing Charges',               'cogs - towing', 'towing charges'),
                ],
            },
            {
                'id': 'opex', 'label': 'Operating Expenses', 'sign': 'expense',
                'subtotal_label': 'Total Operating Expenses',
                'lines': [
                    _line('general_expenses',       'General Expenses',          'general expenses'),
                    _line('accounting_secretarial', 'Accounting & Secretarial',  'accounting', 'secretarial'),
                    _line('bank_charges',           'Bank Charges',              'bank charges', 'bank fees'),
                    _line('depreciation',           'Depreciation',              'depreciation',
                          exclude=['rou']),
                    _line('depreciation_rou',       'Depreciation (ROU)',        'depreciation (rou)', 'rou'),
                    _line('motor_vehicle_expenses', 'Motor Vehicle Expenses',    'motor vehicle expenses'),
                    _line('printing_stationery',    'Printing & Stationery',     'printing', 'stationery'),
                    _line('professional_fees',      'Professional Fees',         'professional fees'),
                    _line('rent_rates',             'Rent & Rates',              'rent & rates', 'rent and rates',
                          exclude=['ifrs 16']),
                    _line('rent_rates_ifrs16_rev', 'Rent & Rates — IFRS 16 Reversal', 'ifrs 16 reversal'),
                    _line('repairs_maintenance',    'Repairs & Maintenance',     'repairs', 'maintenance'),
                    _line('salaries',               'Salaries',                  'salaries'),
                    _line('wages',                  'Wages',                     'wages'),
                    _line('security_expenses',      'Security Expenses',         'security'),
                    _line('training_levy',          'Training Levy',             'training levy'),
                    _line('telephone',              'Telephone',                 'telephone'),
                    _line('utilities',              'Utilities',                 'utilities'),
                    _line('fuel_usage',             'Fuel Usage',                'fuel'),
                    _line('fines_penalties',        'Fines & Penalties',         'fines', 'penalties'),
                    _line('pickup_truck_rental',    'Pick-Up Truck Rental',      'pick-up truck rental', 'truck rental'),
                    _line('garbage_disposal',       'Garbage Disposal',          'garbage'),
                    _line('staff_welfare',          'Staff Welfare',             'staff welfare'),
                    _line('disposal_account',       'Disposal Account',          'disposal account'),
                    _line('loss_on_disposal',       'Loss on Disposal',          'loss on disposal'),
                    _line('severance',              'Severance',                 'severance'),
                    _line('purchase_equipment',     'Purchase of Equipment',     'purchase of equipment'),
                    _line('sales_commission_exp',   'Sales Commissions Expense', 'sales commission'),
                    _line('cash_difference_loss',   'Cash Difference Loss',      'cash difference'),
                    _line('insurance_expense',      'Insurance Expense',         'insurance expense'),
                    _line('advance_tax',            'Advance Tax',               'advance tax'),
                    _line('deferred_tax_expense',   'Deferred Tax Expense',      'deferred tax'),
                    _line('undistributed_pl',       'Undistributed Profits/Losses', 'undistributed profits', 'undistributed losses'),
                ],
            },
            {
                'id': 'finance_costs', 'label': 'Finance Costs', 'sign': 'expense',
                'subtotal_label': 'Total Finance Costs',
                'lines': [
                    _line('interest_fnb_loan',  'Interest on FNB Loan',         'interest on fnb', 'fnb loan'),
                    _line('interest_lease_ifrs16', 'Interest on Lease — IFRS 16', 'interest on lease', 'lease ifrs 16'),
                ],
            },
        ],
        'totals': [
            {'id': 'gross_profit',     'label': 'Gross Profit',
             'formula': 'revenue - cogs'},
            {'id': 'operating_profit', 'label': 'Operating Profit / (Loss)',
             'formula': 'gross_profit - opex'},
            {'id': 'pbt',              'label': 'Net Profit / (Loss) Before Tax',
             'formula': 'operating_profit - finance_costs'},
        ],
    },

    # ── Risk Software Africa (PDF alias RSA — software) ───────────────────
    'RSA': {
        'entity_code': 'RSA',
        'pdf_alias':   'RSA',
        'report_title': 'Profit & Loss Statement',
        'currency':    'BWP',
        'sections': [
            {
                'id': 'revenue', 'label': 'Revenue', 'sign': 'income',
                'subtotal_label': 'Total Revenue',
                'lines': [
                    _line('product_sales',     'Product Sales',           'product sales'),
                    _line('software_maint',    'Software Maintenance Income — GFS', 'software maintenance income', 'gfs'),
                    _line('other_income',      'Other Income',            'other income'),
                    _line('finance_income',    'Finance Income',          'finance income'),
                ],
            },
            {
                'id': 'cogs', 'label': 'Cost of Sales', 'sign': 'expense',
                'subtotal_label': 'Total Cost of Sales',
                'lines': [
                    _line('cogs',                  'Cost of Goods Sold',                   'cost of goods sold', 'cogs',
                          exclude=['avatar']),
                    _line('software_maint_avatar', 'Software Maintenance Expenses — Avatar', 'avatar', 'software maintenance expenses'),
                ],
            },
            {
                'id': 'opex', 'label': 'Operating Expenses', 'sign': 'expense',
                'subtotal_label': 'Total Operating Expenses',
                'lines': [
                    _line('general_expenses',       'General Expenses',            'general expenses'),
                    _line('cell_phone_expenses',    'Cell Phone Expenses',         'cell phone expenses'),
                    _line('cellphone_usage',        'Cellphone Usage',             'cellphone usage'),
                    _line('deferred_tax_expenses',  'Deferred Tax Expenses',       'deferred tax'),
                    _line('petty_cash_transfer',    'Petty Cash Transfer',         'petty cash transfer'),
                    _line('accounting_secretarial', 'Accounting & Secretarial',    'accounting', 'secretarial'),
                    _line('purchase_equipment',     'Purchase of Equipment',       'purchase of equipment'),
                    _line('it_equipment_purchases', 'IT Equipment Purchases',      'it equipment'),
                    _line('rent',                   'Rent',                        'rent', exclude=['& rates','rates']),
                    _line('utilities',              'Utilities',                   'utilities'),
                    _line('printing_postage',       'Printing, Postage & Stationery', 'printing', 'postage', 'stationery'),
                    _line('office_expenses',        'Office Expenses',             'office expenses'),
                    _line('training_levy',          'Training Levy',               'training levy'),
                    _line('promotional_expenses',   'Promotional Expenses',        'promotional'),
                    _line('donations_charity',      'Donations & Charity',         'donations', 'charity'),
                    _line('salary_expenses',        'Salary Expenses',             'salary'),
                    _line('consultancy_fees',       'Consultancy Fees',            'consultancy'),
                    _line('travel_expenses',       'Travel Expenses',              'travel'),
                    _line('transportation_costs',   'Transportation Costs',        'transportation'),
                    _line('motor_vehicle_expenses', 'Motor Vehicle Expenses',      'motor vehicle expenses'),
                    _line('bank_fees',              'Bank Fees',                   'bank fees', 'bank charges'),
                    _line('professional_fees',      'Professional Fees',           'professional fees'),
                    _line('subscriptions',          'Subscriptions',               'subscriptions'),
                    _line('licensing_fee',          'Licensing Fee Expense',       'licensing fee'),
                    _line('training_dev',           'Training & Professional Development', 'training & professional', 'professional development'),
                    _line('security_services',      'Security Services',           'security services', 'security'),
                    _line('customer_entertainment', 'Customer Entertainment',      'customer entertainment'),
                    _line('fx_loss',                'Foreign Exchange Loss',       'foreign exchange loss', 'fx loss'),
                    _line('taxation',               'Taxation',                    'taxation', 'tax expense'),
                    _line('staff_welfare',          'Staff Welfare',               'staff welfare'),
                    _line('medical_aid',            'Medical Aid Expenses',        'medical aid'),
                    _line('fixed_asset_disposal',   'Fixed Asset Disposal',        'fixed asset disposal'),
                    _line('instant_insurance_reddy','Instant Insurance — Shoes for Reddy\'s', 'shoes for reddy', 'instant insurance'),
                    _line('depreciation',           'Depreciation Expense',        'depreciation'),
                    _line('software_dev_costs',     'Software Development Costs',  'software development'),
                    _line('software_maint_costs',   'Software Maintenance Costs',  'software maintenance costs'),
                    _line('repairs_maintenance',    'Repairs and Maintenance',     'repairs', 'maintenance'),
                    _line('sales_expenses',         'Sales Expenses',              'sales expenses'),
                    _line('undistributed_pl',       'Undistributed Profits/Losses', 'undistributed profits', 'undistributed losses'),
                ],
            },
            {
                'id': 'finance_costs', 'label': 'Finance Costs', 'sign': 'expense',
                'subtotal_label': 'Total Finance Costs',
                'lines': [
                    _line('finance_costs',        'Finance Costs',           'finance costs',
                          exclude=['quantum', 'alpha insurtech']),
                    _line('interest_to_quantum',  'Interest to Quantum',     'interest to quantum'),
                    _line('interest_to_insurtech','Interest to Alpha Insurtech', 'interest to alpha insurtech'),
                ],
            },
        ],
        'totals': [
            {'id': 'gross_profit',     'label': 'Gross Profit',
             'formula': 'revenue - cogs'},
            {'id': 'operating_profit', 'label': 'Operating Profit / (Loss)',
             'formula': 'gross_profit - opex'},
            {'id': 'pbt',              'label': 'Net Profit / (Loss) Before Tax',
             'formula': 'operating_profit - finance_costs'},
        ],
    },

    # ── Alpha Direct South Africa (PDF alias ADSA — distribution, ZAR) ────
    'ADSA': {
        'entity_code': 'ADSA',
        'pdf_alias':   'ADSA',
        'report_title': 'Profit & Loss Statement',
        'currency':    'ZAR',
        'sections': [
            {
                'id': 'revenue', 'label': 'Revenue', 'sign': 'income',
                'subtotal_label': 'Total Revenue',
                'lines': [
                    _line('nmi_binder_fee',    'NMI Binder Fee',          'nmi binder', 'binder fee'),
                    _line('commission_income', 'Commission Income',       'commission income'),
                    _line('other_income',      'Other Income',            'other income'),
                    _line('fx_gain',           'Foreign Exchange Gain',   'foreign exchange gain', 'fx gain'),
                ],
            },
            {
                'id': 'cogs', 'label': 'Cost of Sales', 'sign': 'expense',
                'subtotal_label': 'Total Cost of Sales',
                'lines': [
                    _line('cogs', 'Cost of Goods Sold', 'cost of goods sold', 'cogs'),
                ],
            },
            {
                'id': 'opex', 'label': 'Operating Expenses', 'sign': 'expense',
                'subtotal_label': 'Total Operating Expenses',
                'lines': [
                    _line('staff_welfare',          'Staff Welfare',         'staff welfare'),
                    _line('fines_penalties',        'Fines & Penalties',     'fines', 'penalties'),
                    _line('transportation_costs',   'Transportation Costs',  'transportation'),
                    _line('telephone_costs',        'Telephone Costs',       'telephone'),
                    _line('promotional_expenses',   'Promotional Expenses',  'promotional'),
                    _line('training_dev',           'Training & Development', 'training'),
                    _line('office_expenses',        'Office Expenses',       'office expenses'),
                    _line('accounting_secretarial', 'Accounting & Secretarial', 'accounting', 'secretarial'),
                    _line('insurance_expense',      'Insurance Expense',     'insurance expense'),
                    _line('production_cost',        'Production Cost',       'production cost'),
                    _line('professional_fees',      'Professional Fees',     'professional fees'),
                    _line('rent_rates',             'Rent & Rates',          'rent'),
                    _line('travel_expenses',        'Travel Expenses',       'travel'),
                    _line('fuel',                   'Fuel',                  'fuel'),
                    _line('depreciation',           'Depreciation',          'depreciation'),
                    _line('general',                'General',               'general expenses'),
                    _line('import_duty',            'Import Duty',           'import duty'),
                    _line('printing_postage',       'Printing, Postage & Stationery', 'printing', 'postage', 'stationery'),
                    _line('bank_fees',              'Bank Fees',             'bank fees'),
                    _line('salary_expenses',        'Salary Expenses',       'salary'),
                    _line('motor_vehicle_expenses', 'Motor Vehicle Expenses','motor vehicle'),
                    _line('subscriptions',          'Subscriptions',         'subscriptions'),
                    _line('commission_expense',     'Commission Expense',    'commission expense', exclude=['income']),
                ],
            },
            {
                'id': 'finance_costs', 'label': 'Finance Costs', 'sign': 'expense',
                'subtotal_label': 'Total Finance Costs',
                'lines': [
                    _line('finance_costs', 'Finance Costs', 'finance costs', 'interest expense'),
                ],
            },
        ],
        'totals': [
            {'id': 'gross_profit',     'label': 'Gross Profit / (Loss)',
             'formula': 'revenue - cogs'},
            {'id': 'operating_profit', 'label': 'Operating Profit / (Loss)',
             'formula': 'gross_profit - opex'},
            {'id': 'pbt',              'label': 'Net Profit / (Loss) Before Tax',
             'formula': 'operating_profit - finance_costs'},
        ],
    },

    # ── Gaborone Coin Exchange (PDF alias GCE) ────────────────────────────
    'GCX': {
        'entity_code': 'GCX',
        'pdf_alias':   'GCE',
        'report_title': 'Profit & Loss Statement',
        'currency':    'BWP',
        'sections': [
            {
                'id': 'revenue', 'label': 'Revenue', 'sign': 'income',
                'subtotal_label': 'Total Revenue',
                'lines': [
                    _line('discounts_obtained', 'Revenue — Discounts Obtained', 'discounts obtained'),
                    _line('other_income',       'Other Income',                 'other income'),
                ],
            },
            {
                'id': 'cogs', 'label': 'Cost of Sales', 'sign': 'expense',
                'subtotal_label': 'Total Cost of Sales',
                'lines': [
                    _line('interest_borrowings', 'Interest on Borrowings', 'interest on borrowings'),
                    _line('commission_expenses', 'Commission Expenses',    'commission expense'),
                ],
            },
            {
                'id': 'opex', 'label': 'Operating Expenses', 'sign': 'expense',
                'subtotal_label': 'Total Operating Expenses',
                'lines': [
                    _line('general_expenses',       'General Expenses',         'general expenses'),
                    _line('accounting_secretarial', 'Accounting & Secretarial', 'accounting', 'secretarial'),
                    _line('income_tax_expense',     'Income Tax Expense',       'income tax', 'tax expense'),
                    _line('bank_fees',              'Bank Fees',                'bank fees'),
                    _line('professional_fees',      'Professional Fees',        'professional fees'),
                    _line('salary_expenses',        'Salary Expenses',          'salary'),
                    _line('severance_benefits',     'Severance Benefits Expense', 'severance'),
                ],
            },
            {
                'id': 'finance_costs', 'label': 'Finance Costs', 'sign': 'expense',
                'subtotal_label': 'Total Finance Costs',
                'lines': [
                    _line('interest_expenses', 'Interest Expenses', 'interest expense'),
                ],
            },
        ],
        'totals': [
            {'id': 'gross_profit',     'label': 'Gross Profit',
             'formula': 'revenue - cogs'},
            {'id': 'operating_profit', 'label': 'Operating Profit / (Loss)',
             'formula': 'gross_profit - opex'},
            {'id': 'pbt',              'label': 'Net Profit / (Loss) Before Tax',
             'formula': 'operating_profit - finance_costs'},
        ],
    },

    # ── Unicoin (PDF alias UNI — commission agency) ───────────────────────
    'UNI': {
        'entity_code': 'UNI',
        'pdf_alias':   'UNI',
        'report_title': 'Profit & Loss Statement',
        'currency':    'BWP',
        'sections': [
            {
                'id': 'revenue', 'label': 'Revenue', 'sign': 'income',
                'subtotal_label': 'Total Revenue',
                'lines': [
                    _line('comm_income_instant',   'Commission Income — Instant Insurance', 'commission income - instant', 'instant insurance'),
                    _line('comm_income_direct',    'Commission Income — Direct Book',       'commission income - direct', 'direct book'),
                    _line('liberty_revenues',      'Liberty Revenues',                      'liberty'),
                    _line('other_income',          'Other Income',                          'other income'),
                ],
            },
            {
                'id': 'commission_expenses', 'label': 'Commission Expenses', 'sign': 'expense',
                'subtotal_label': 'Total Commission Expenses',
                'lines': [
                    _line('comm_instant_agents',   'Commission — Instant Insurance Agents', 'commission - instant', 'instant insurance agents'),
                    _line('comm_direct_book',      'Commission — Direct Book',              'commission - direct book', 'direct book'),
                    _line('comm_adhoc_agents',     'Commission — AD HOC Agents',            'ad hoc agents', 'adhoc agents'),
                    _line('comm_motor_comp',       'Commission — Motor Comprehensive',      'motor comprehensive'),
                    _line('support_staff_comm',    'Support Staff Commission',              'support staff commission'),
                ],
            },
            {
                'id': 'opex', 'label': 'Operating Expenses', 'sign': 'expense',
                'subtotal_label': 'Total Operating Expenses',
                'lines': [
                    _line('accounting_secretarial', 'Accounting & Secretarial', 'accounting', 'secretarial'),
                    _line('insurance_expense',     'Insurance Expense',         'insurance expense'),
                    _line('audit_fees',            'Audit Fees',                'audit fees'),
                    _line('admin_fees',            'Administration Fees',       'administration fees', 'admin fees'),
                    _line('printing_postage',      'Printing, Postage & Stationery', 'printing', 'postage', 'stationery'),
                    _line('transport_costs',       'Transport Costs',           'transport costs'),
                    _line('advertising_promotions','Advertising & Promotions',  'advertising', 'promotions'),
                    _line('software_maintenance',  'Software Maintenance',      'software maintenance'),
                    _line('repairs_maintenance',   'Repairs & Maintenance',     'repairs', 'maintenance'),
                    _line('training_dev',          'Training & Professional Development', 'training'),
                    _line('subscriptions',         'Subscriptions',             'subscriptions'),
                    _line('consultancy_fees',      'Consultancy Fees',          'consultancy'),
                    _line('training_levy',         'Training Levy',             'training levy'),
                    _line('professional_fees',     'Professional Fees',         'professional fees'),
                    _line('nbfira_expenses',       'NBFIRA Expenses',           'nbfira'),
                    _line('motor_vehicle_expenses','Motor Vehicle Expenses',    'motor vehicle'),
                    _line('purchase_equipment',    'Purchase of Equipment',     'purchase of equipment'),
                    _line('rent',                  'Rent',                      'rent'),
                    _line('utilities',             'Utilities',                 'utilities'),
                    _line('security_services',     'Security Services',         'security'),
                    _line('bad_debts',             'Bad Debts',                 'bad debts'),
                    _line('bank_fees',             'Bank Fees',                 'bank fees'),
                    _line('staff_welfare',         'Staff Welfare',             'staff welfare'),
                    _line('salary_expenses',       'Salary Expenses',           'salary'),
                    _line('sales_expenses',       'Sales Expenses',             'sales expenses'),
                    _line('depreciation',          'Depreciation',              'depreciation'),
                    _line('tax_expense',           'Tax Expense',               'tax expense', 'income tax'),
                    _line('undistributed_pl',      'Undistributed Profits/Losses', 'undistributed'),
                ],
            },
            {
                'id': 'finance_costs', 'label': 'Finance Costs', 'sign': 'expense',
                'subtotal_label': 'Total Finance Costs',
                'lines': [
                    _line('interest_expense', 'Interest Expense', 'interest expense'),
                ],
            },
        ],
        'totals': [
            {'id': 'gross_profit',     'label': 'Gross Profit',
             'formula': 'revenue - commission_expenses'},
            {'id': 'operating_profit', 'label': 'Operating Profit / (Loss)',
             'formula': 'gross_profit - opex'},
            {'id': 'pbt',              'label': 'Net Profit / (Loss) Before Tax',
             'formula': 'operating_profit - finance_costs'},
        ],
    },

    # ── Alpha Direct Insurtech Pte Ltd (PDF alias SGP — Singapore, USD)
    # PDF says "Singapore" — DB has ADIIC / ADIPL both labelled "Insurtech".
    # ADIPL = Pte Ltd → Singapore.
    'ADIPL': {
        'entity_code': 'ADIPL',
        'pdf_alias':   'SGP',
        'report_title': 'Profit & Loss Statement',
        'currency':    'USD',
        'sections': [
            {
                'id': 'revenue', 'label': 'Revenue', 'sign': 'income',
                'subtotal_label': 'Total Revenue',
                'lines': [
                    _line('product_sales',     'Product Sales',           'product sales'),
                    _line('interest_income',   'Interest Income',         'interest income'),
                    _line('fx_gain',           'Foreign Exchange Gain',   'foreign exchange gain'),
                    _line('bank_cashbacks',    'Bank Cashbacks',          'bank cashbacks', 'cashback'),
                ],
            },
            {
                'id': 'opex', 'label': 'Operating Expenses', 'sign': 'expense',
                'subtotal_label': 'Total Operating Expenses',
                'lines': [
                    _line('travel_expenses',        'Travel Expenses',         'travel'),
                    _line('accounting_secretarial', 'Accounting & Secretarial','accounting', 'secretarial'),
                    _line('professional_fees',      'Professional Fees',       'professional fees'),
                    _line('licensing_software',     'Licensing & Software Fees', 'licensing', 'software fees'),
                    _line('legal_fees',             'Legal Fees',              'legal fees'),
                    _line('subscriptions',          'Subscriptions',           'subscriptions'),
                    _line('advertising_promotions', 'Advertising & Promotions','advertising'),
                    _line('bank_fees',              'Bank Fees',               'bank fees'),
                    _line('fx_loss',                'Foreign Exchange Loss',   'foreign exchange loss'),
                    _line('software_maintenance',   'Software Maintenance',    'software maintenance'),
                    _line('directors_fees',         "Director's Fees",         'directors fees', "director's fees"),
                ],
            },
        ],
        'totals': [
            {'id': 'pbt', 'label': 'Net Profit / (Loss) Before Tax',
             'formula': 'revenue - opex'},
        ],
    },

    # ── Alpha Insurtech Zambia (PDF alias ZMB — distribution, ZMW) ────────
    'AIZ': {
        'entity_code': 'AIZ',
        'pdf_alias':   'ZMB',
        'report_title': 'Profit & Loss Statement',
        'currency':    'ZMW',
        'sections': [
            {
                'id': 'revenue', 'label': 'Revenue', 'sign': 'income',
                'subtotal_label': 'Total Revenue',
                'lines': [
                    _line('sales_revenue', 'Sales Revenue', 'sales revenue', 'product sales'),
                    _line('other_income',  'Other Income',  'other income'),
                ],
            },
            {
                'id': 'cogs', 'label': 'Cost of Sales', 'sign': 'expense',
                'subtotal_label': 'Total Cost of Sales',
                'lines': [
                    _line('cogs',              'Cost of Goods Sold', 'cost of goods sold', 'cogs'),
                    _line('collection_charges','Collection Charges', 'collection charges'),
                ],
            },
            {
                'id': 'opex', 'label': 'Operating Expenses', 'sign': 'expense',
                'subtotal_label': 'Total Operating Expenses',
                'lines': [
                    _line('general_expenses',       'General Expenses',         'general expenses'),
                    _line('it_expenses',            'IT Expenses',              'it expenses'),
                    _line('motor_vehicle_expenses', 'Motor Vehicle Expenses',   'motor vehicle expenses'),
                    _line('insurance_expense',      'Insurance Expense',        'insurance expense'),
                    _line('fuel_expense',           'Fuel Expense',             'fuel'),
                    _line('accounting_secretarial', 'Accounting & Secretarial', 'accounting', 'secretarial'),
                    _line('purchase_equipment',     'Purchase of Equipment',    'purchase of equipment'),
                    _line('rent',                   'Rent',                     'rent'),
                    _line('utilities',              'Utilities',                'utilities'),
                    _line('subscriptions',          'Subscriptions',            'subscriptions'),
                    _line('promotional_expenses',   'Promotional Expenses',     'promotional'),
                    _line('salary_expenses',        'Salary Expenses',          'salary'),
                    _line('leave_severance',        'Leave & Severance',        'leave', 'severance'),
                    _line('travel_expenses',        'Travel Expenses',          'travel'),
                    _line('transportation_costs',   'Transportation Costs',     'transportation'),
                    _line('agency_training_cost',   'Agency Training Cost',     'agency training'),
                    _line('bank_fees',              'Bank Fees',                'bank fees'),
                    _line('printing_stationery',    'Printing & Stationery',    'printing', 'stationery'),
                    _line('training_dev',           'Training & Professional Development', 'training & professional', 'professional development'),
                    _line('consultation_fee',       'Consultation Fee Expense', 'consultation'),
                    _line('fx_loss',                'Foreign Exchange Loss',    'foreign exchange loss'),
                    _line('staff_welfare',          'Staff Welfare',            'staff welfare'),
                    _line('licensing_fees',         'Licensing Fees',           'licensing'),
                    _line('sales_expenses',        'Sales Expenses',            'sales expenses'),
                    _line('software_dev_costs',     'Software Development Costs', 'software development'),
                    _line('depreciation',           'Depreciation Expense',     'depreciation'),
                ],
            },
            {
                'id': 'other_inc_exp', 'label': 'Other Income / (Expenses)', 'sign': 'income',
                'subtotal_label': 'Total Other Income / (Expenses)',
                'lines': [
                    _line('fx_gain_loss', 'Foreign Exchange Gain / (Loss)', 'foreign exchange gain', 'fx gain'),
                    _line('finance_costs','Finance Costs',                  'finance costs', 'interest expense'),
                ],
            },
        ],
        'totals': [
            {'id': 'gross_profit',     'label': 'Gross Profit',
             'formula': 'revenue - cogs'},
            {'id': 'operating_profit', 'label': 'Operating Profit / (Loss)',
             'formula': 'gross_profit - opex'},
            {'id': 'pbt',              'label': 'Net Profit / (Loss) Before Tax',
             'formula': 'operating_profit + other_inc_exp'},
        ],
    },

    # ── Alpha Direct Life (PDF alias ADLIFE — life insurance, BWP) ────────
    'ADIL': {
        'entity_code': 'ADIL',
        'pdf_alias':   'ADLIFE',
        'report_title': 'Profit & Loss Statement',
        'currency':    'BWP',
        'sections': [
            {
                'id': 'revenue', 'label': 'Revenue', 'sign': 'income',
                'subtotal_label': 'Total Revenue',
                'lines': [
                    _line('product_sales', 'Product Sales', 'product sales'),
                ],
            },
            {
                'id': 'opex', 'label': 'Operating Expenses', 'sign': 'expense',
                'subtotal_label': 'Total Operating Expenses',
                'lines': [
                    _line('general_expenses', 'General Expenses', 'general expenses'),
                    _line('bank_fees',        'Bank Fees',        'bank fees'),
                ],
            },
            {
                'id': 'finance_costs', 'label': 'Finance Costs', 'sign': 'expense',
                'subtotal_label': 'Total Finance Costs',
                'lines': [
                    _line('interest_expenses', 'Interest Expenses', 'interest expense'),
                ],
            },
        ],
        'totals': [
            {'id': 'operating_profit', 'label': 'Operating Profit / (Loss)',
             'formula': 'revenue - opex'},
            {'id': 'pbt',              'label': 'Net Profit / (Loss) Before Tax',
             'formula': 'operating_profit - finance_costs'},
        ],
    },

    # ── ADRisk Global (PDF alias ADRISK — actuarial India INR) ────────────
    'ADRG': {
        'entity_code': 'ADRG',
        'pdf_alias':   'ADRISK',
        'report_title': 'Profit & Loss Statement',
        'currency':    'INR',
        'sections': [
            {
                'id': 'revenue', 'label': 'Revenue', 'sign': 'income',
                'subtotal_label': 'Total Revenue',
                'lines': [
                    _line('product_sales', 'Product Sales', 'product sales'),
                ],
            },
            {
                'id': 'opex', 'label': 'Operating Expenses', 'sign': 'expense',
                'subtotal_label': 'Total Operating Expenses',
                'lines': [
                    _line('general_expenses',       'General Expenses',         'general expenses'),
                    _line('staff_welfare',          'Staff Welfare',            'staff welfare'),
                    _line('travel_expenses',        'Travel Expenses',          'travel'),
                    _line('rent',                   'Rent',                     'rent'),
                    _line('bank_fees',              'Bank Fees',                'bank fees'),
                    _line('salary_expenses',        'Salary Expenses',          'salary'),
                    _line('contractor_fees',        'Contractor Fees',          'contractor fees'),
                    _line('recruitment_service',    'Recruitment Service Fees', 'recruitment'),
                    _line('lease_payments',         'Lease Payments',           'lease payments'),
                    _line('rbi_filing',             'RBI Filing',               'rbi filing'),
                    _line('accounting_secretarial', 'Accounting & Secretarial Fees', 'accounting', 'secretarial'),
                    _line('fx_loss',                'Foreign Exchange Loss',    'foreign exchange loss'),
                    _line('undistributed_pl',       'Undistributed Profits/Losses', 'undistributed'),
                ],
            },
        ],
        'totals': [
            {'id': 'pbt', 'label': 'Net Profit / (Loss) Before Tax',
             'formula': 'revenue - opex'},
        ],
    },
}


def get_template(entity_code: str):
    """Look up by DB code, then by pdf_alias. None if no template."""
    if not entity_code:
        return None
    t = TEMPLATES.get(entity_code)
    if t:
        return t
    for tpl in TEMPLATES.values():
        if tpl.get('pdf_alias', '').upper() == entity_code.upper():
            return tpl
    return None


def list_entities():
    """All entities that have a non-ADIC template."""
    return [(c, t['pdf_alias'], t['currency']) for c, t in TEMPLATES.items()]
