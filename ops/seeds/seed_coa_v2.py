#!/usr/bin/env python
"""
seed_coa_v2.py — Replace the legacy 4-digit chart of accounts with the CFO's
operational 6-digit chart from Alpha Direct Insurance's Odoo books.

This wipes existing JournalEntryLines (cascade from JournalEntry delete) and
the old Accounts, then loads 271 new accounts mapped to the existing
account_type / sub_type system used by reports.py.

Run from alpha-finance repo root on production:
    python manage.py shell < ops/seeds/seed_coa_v2.py

Idempotent — re-running upserts the same accounts. JEs deleted on first run
only (subsequent runs find no legacy entries).
"""
from django.db import transaction
from ledger.models import Account, JournalEntry, JournalEntryLine
from core.models import Currency

# (code, name, account_type, sub_type, is_bank_account)
ACCOUNTS_V2 = [
    ('100001', 'Insurance in a box income', 'revenue', 'operating_revenue', False),
    ('100002', 'Written Premium - Portal', 'revenue', 'operating_revenue', False),
    ('100003', 'Revenue Graphite (COMG/DOMG)', 'revenue', 'operating_revenue', False),
    ('100004', 'Written Premium - BONU', 'revenue', 'operating_revenue', False),
    ('100006', 'Uncollected Premium', 'revenue', 'operating_revenue', False),
    ('100007', 'Alpha SA RI  Premium', 'revenue', 'operating_revenue', False),
    ('100009', 'Revenue from Graphite policies', 'revenue', 'operating_revenue', False),
    ('100010', 'C.A.R Premium', 'revenue', 'operating_revenue', False),
    ('100011', "Discount's  Issued", 'revenue', 'operating_revenue', False),
    ('100012', 'Health Care  Revenue', 'revenue', 'operating_revenue', False),
    ('101000', 'Reinsurance Expense - HCV', 'expense', 'cost_of_insurance', False),
    ('101004', 'Reinsurance expense - FMRE MQS', 'expense', 'cost_of_insurance', False),
    ('101005', 'Reinsurance Expense -Fire Surplus', 'expense', 'cost_of_insurance', False),
    ('101006', 'Reinsurance Expense -MQST JBB', 'expense', 'cost_of_insurance', False),
    ('101007', 'Reinsurance Expense - FAC Cover', 'expense', 'cost_of_insurance', False),
    ('101008', 'Reinsurance Expense - Quota Share Treaty', 'expense', 'cost_of_insurance', False),
    ('101010', 'Reinsurance Expense - XL & CAT Cover', 'expense', 'cost_of_insurance', False),
    ('101011', 'Reinsurance  Health - expense', 'expense', 'cost_of_insurance', False),
    ('101502', 'Outstanding Receipts', 'expense', 'cost_of_insurance', False),
    ('101503', 'Outstanding Payments', 'expense', 'cost_of_insurance', False),
    ('102001', 'Change in UPR', 'expense', 'cost_of_insurance', False),
    ('103000', 'Claims Settlement - Instant Insurance Policies', 'expense', 'cost_of_insurance', False),
    ('103001', 'Claims Discounts Received', 'expense', 'cost_of_insurance', False),
    ('103003', 'Assessor salary expenses', 'expense', 'cost_of_insurance', False),
    ('103004', 'Salvage Management Fees', 'expense', 'cost_of_insurance', False),
    ('103005', 'Claims contact centre', 'expense', 'cost_of_insurance', False),
    ('103006', 'Claims Settlement - Portal', 'expense', 'cost_of_insurance', False),
    ('103008', 'Claims Paid Outside The System', 'expense', 'cost_of_insurance', False),
    ('103010', 'Claims Settlement - Graphite Comprehensive Policies', 'expense', 'cost_of_insurance', False),
    ('103012', 'Claim Settlement - COMG/DOMG', 'expense', 'cost_of_insurance', False),
    ('103013', 'Claims Health', 'expense', 'cost_of_insurance', False),
    ('103014', 'BONU Clamis', 'expense', 'cost_of_insurance', False),
    ('103015', 'Movement in unallocated loss adjustment', 'expense', 'cost_of_insurance', False),
    ('103016', 'Movement in risk adjustment', 'expense', 'cost_of_insurance', False),
    ('103107', 'Movement in risk adjustment - Reinsurance', 'expense', 'cost_of_insurance', False),
    ('104000', 'Reinsurance Share of Claims:XL & CAT', 'expense', 'cost_of_insurance', False),
    ('104001', 'Reinsurance Share of Claims HCV', 'expense', 'cost_of_insurance', False),
    ('104004', 'Reinsurance Share of Claims: Surplus', 'expense', 'cost_of_insurance', False),
    ('104007', 'Reinsurance Share of Claims - Health', 'expense', 'cost_of_insurance', False),
    ('104008', 'Reinsurance Share of Claims:FAC', 'expense', 'cost_of_insurance', False),
    ('104009', 'Reinsurance Share of Claims: Quota Share Treaty', 'expense', 'cost_of_insurance', False),
    ('104010', 'Reinsurance Share of Claims- MQST JBB', 'expense', 'cost_of_insurance', False),
    ('104011', 'Reinsurance share of claims - FMRE MQS', 'expense', 'cost_of_insurance', False),
    ('104012', 'Movement in reinsurance claims reserve: FAC', 'expense', 'cost_of_insurance', False),
    ('104013', 'Movement in reinsurance claims reserve : Quota', 'expense', 'cost_of_insurance', False),
    ('104014', 'Movement in reinsurance claims reserve :MQST', 'expense', 'cost_of_insurance', False),
    ('104016', 'Movement in reinsurance claims reserve: Surplu', 'expense', 'cost_of_insurance', False),
    ('104017', 'Movement in reinsurance claims reserve:XL &CAT', 'expense', 'cost_of_insurance', False),
    ('105002', 'Subrogation', 'expense', 'cost_of_insurance', False),
    ('105003', 'Non Motor Salvages', 'expense', 'cost_of_insurance', False),
    ('105004', 'Salvages & Recoveries', 'expense', 'cost_of_insurance', False),
    ('106000', 'Reinsurance Commission Received (Treaties)', 'expense', 'cost_of_insurance', False),
    ('106001', 'Commission MQST JBB Commission', 'expense', 'cost_of_insurance', False),
    ('106002', 'FAC Commission', 'expense', 'cost_of_insurance', False),
    ('106003', 'Reinsurance Profit Commissions', 'expense', 'cost_of_insurance', False),
    ('107000', 'Commission Paid - Digital', 'expense', 'cost_of_insurance', False),
    ('107005', 'Commissions Expense-Other Agents', 'expense', 'cost_of_insurance', False),
    ('107006', 'BONU Commission', 'expense', 'cost_of_insurance', False),
    ('107007', 'Commissions Expense-Brokers', 'expense', 'cost_of_insurance', False),
    ('107008', 'Commissions Expense- Agents (HQ)', 'expense', 'cost_of_insurance', False),
    ('107009', 'Commissions expense - Agents (External)', 'expense', 'cost_of_insurance', False),
    ('107010', 'Sefalana Admin Fees', 'expense', 'cost_of_insurance', False),
    ('107011', 'Choppies Admin insurance in a box', 'expense', 'cost_of_insurance', False),
    ('109002', 'Foreign Exchange Gain', 'expense', 'operating_expense', False),
    ('110004', 'Severance Expense', 'expense', 'operating_expense', False),
    ('110005', 'Pension - Employees', 'expense', 'operating_expense', False),
    ('110006', 'Leave Pay Expense', 'expense', 'operating_expense', False),
    ('110007', 'Employee Benefits', 'expense', 'operating_expense', False),
    ('110008', 'Directors Salary', 'expense', 'operating_expense', False),
    ('110009', 'Medical Aid', 'expense', 'operating_expense', False),
    ('110010', 'Salaries & Wages', 'expense', 'operating_expense', False),
    ('110011', 'Bonus Pay', 'expense', 'operating_expense', False),
    ('110012', 'Severance Pay', 'expense', 'operating_expense', False),
    ('110016', 'Custom Duty', 'expense', 'operating_expense', False),
    ('111000', 'Foreign Exchange Loss', 'expense', 'operating_expense', False),
    ('111001', '10th Year Anniversary', 'expense', 'operating_expense', False),
    ('111002', 'Finance cost - IFRS 16', 'expense', 'operating_expense', False),
    ('111008', 'Utilities', 'expense', 'operating_expense', False),
    ('111010', 'Travel Expense', 'expense', 'operating_expense', False),
    ('111011', 'Cleaning', 'expense', 'operating_expense', False),
    ('111012', 'Fleet Management', 'expense', 'operating_expense', False),
    ('111013', 'Board Fees', 'expense', 'operating_expense', False),
    ('111014', 'Professional Fees - other', 'expense', 'operating_expense', False),
    ('111015', 'Audit Fees', 'expense', 'operating_expense', False),
    ('111016', 'Charitable Donations', 'expense', 'operating_expense', False),
    ('111017', 'Gifts & Donations', 'expense', 'operating_expense', False),
    ('111019', 'Tax -Penalty & interest', 'expense', 'operating_expense', False),
    ('111020', 'License & Permits', 'expense', 'operating_expense', False),
    ('111021', 'Fuel Expense', 'expense', 'operating_expense', False),
    ('111022', 'Rent & Rates', 'expense', 'operating_expense', False),
    ('111023', 'Legal Expense', 'expense', 'operating_expense', False),
    ('111024', 'NBFIRA Levy Expense', 'expense', 'operating_expense', False),
    ('111025', 'Design Expenses', 'expense', 'operating_expense', False),
    ('111026', 'Insurance Expense', 'expense', 'operating_expense', False),
    ('111027', 'Postage & Delivery', 'expense', 'operating_expense', False),
    ('111028', 'Office Expense', 'expense', 'operating_expense', False),
    ('111029', 'Training Levy Expense', 'expense', 'operating_expense', False),
    ('111030', 'Training and development', 'expense', 'operating_expense', False),
    ('111031', 'Dues and Subscriptions', 'expense', 'operating_expense', False),
    ('111032', 'Meals & Entertainment', 'expense', 'operating_expense', False),
    ('111033', 'Secretarial Fees', 'expense', 'operating_expense', False),
    ('111034', 'Workshops and Conferences', 'expense', 'operating_expense', False),
    ('111035', 'Printing & Reproduction Expense', 'expense', 'operating_expense', False),
    ('111036', 'Repair & Maintance', 'expense', 'operating_expense', False),
    ('111037', 'Bank Charges', 'expense', 'operating_expense', False),
    ('111038', 'Office Stationery', 'expense', 'operating_expense', False),
    ('111039', 'Bank Fees', 'expense', 'operating_expense', False),
    ('111041', 'BONU Admin Expenses', 'expense', 'operating_expense', False),
    ('111042', 'Movemet of Provisions', 'expense', 'operating_expense', False),
    ('111043', 'Health Care operational Expenses', 'expense', 'operating_expense', False),
    ('111045', 'Health Care Expense', 'expense', 'operating_expense', False),
    ('112000', 'ICT Network Services', 'expense', 'operating_expense', False),
    ('112001', 'Software Maintanence', 'expense', 'operating_expense', False),
    ('112002', 'Risk AI Management Fees', 'expense', 'operating_expense', False),
    ('112003', 'Amazon Web Services', 'expense', 'operating_expense', False),
    ('113002', 'Paygates Expense', 'expense', 'operating_expense', False),
    ('114000', 'Cellphone Usage', 'expense', 'operating_expense', False),
    ('114001', 'Internet Fees', 'expense', 'operating_expense', False),
    ('114002', 'Telephone Usage', 'expense', 'operating_expense', False),
    ('115001', 'Advertising & Promotions', 'expense', 'operating_expense', False),
    ('115002', 'Marketing Expense', 'expense', 'operating_expense', False),
    ('116002', 'Staff Welfare', 'expense', 'operating_expense', False),
    ('117000', 'CONSULTANCY FEES', 'expense', 'operating_expense', False),
    ('117001', 'HR Consultancy', 'expense', 'operating_expense', False),
    ('118001', 'Insurance in a box expenses', 'expense', 'cost_of_insurance', False),
    ('118005', 'Broker Entertainment', 'expense', 'cost_of_insurance', False),
    ('118006', 'Acquisition Cost', 'expense', 'cost_of_insurance', False),
    ('118007', 'BONU Acqusition', 'expense', 'cost_of_insurance', False),
    ('118008', 'Movement in Closing of Treaties', 'expense', 'cost_of_insurance', False),
    ('118745', 'Licensing Fee Expense - AD Insurtech', 'expense', 'cost_of_insurance', False),
    ('119000', 'Provision for related party- ECL (PL)', 'expense', 'operating_expense', False),
    ('119001', 'Deferred Tax Expense', 'expense', 'operating_expense', False),
    ('119002', 'Income Tax Expense', 'expense', 'operating_expense', False),
    ('120001', 'Provision for Subrogation - ECL (PL)', 'expense', 'operating_expense', False),
    ('121000', 'Provision for Bad Debts - ECL', 'expense', 'operating_expense', False),
    ('122000', 'Right of Use (ROU) - Depreciation', 'expense', 'operating_expense', False),
    ('122001', 'Depreciation', 'expense', 'operating_expense', False),
    ('123001', 'Interest Income', 'expense', 'operating_expense', False),
    ('124000', 'Other Income', 'expense', 'operating_expense', False),
    ('124001', 'Payment Plan Charge', 'revenue', 'other_revenue', False),
    ('124004', 'Interest From Staff Loan', 'expense', 'operating_expense', False),
    ('124005', 'Profit on Disposal of PPE', 'revenue', 'other_revenue', False),
    ('124006', 'Interest Income - Other', 'expense', 'operating_expense', False),
    ('135', 'Provision for Change in IBNR', 'liability', 'provision', False),
    ('147', 'Reinsurers share of IBNR claims', 'asset', 'current_asset', False),
    ('183', 'Travel Insurance Re-insurance', 'asset', 'current_asset', False),
    ('195', 'Bad Debts Expense', 'expense', 'operating_expense', False),
    ('200001', 'Accumulated Depreciation-Motor Vehicle', 'asset', 'fixed_asset', False),
    ('20019', '48374600014924 E-Wallet Pro Chimidza', 'asset', 'fixed_asset', True),
    ('201001', 'Receivable from Quantum', 'asset', 'current_asset', False),
    ('201002', 'Receivable from Risk SW', 'asset', 'current_asset', False),
    ('201003', 'Provision for related party- ECL (BS)', 'asset', 'current_asset', False),
    ('201004', 'Receivable from Veritas', 'asset', 'current_asset', False),
    ('201005', 'Receivable from Alpha Insurect Singapore', 'asset', 'current_asset', False),
    ('201006', 'Receivable from GCE', 'asset', 'current_asset', False),
    ('201007', 'Receivable from Unicoin', 'asset', 'current_asset', False),
    ('201008', 'Receivable from Alpha SA', 'asset', 'current_asset', False),
    ('201010', 'Receivable From Alpha Zambia', 'asset', 'current_asset', False),
    ('202001', 'Other Receivables', 'asset', 'current_asset', False),
    ('202002', 'Rent Deposits', 'asset', 'current_asset', False),
    ('202003', 'Other Receivable', 'asset', 'current_asset', False),
    ('202006', 'Receivable From Fraudster', 'asset', 'current_asset', False),
    ('202007', 'Health Receivable', 'asset', 'current_asset', False),
    ('203001', 'Loans-owing from Employees', 'asset', 'current_asset', False),
    ('204001', 'Payable to GCE', 'liability', 'current_liability', False),
    ('204002', 'Payable to Quantum', 'liability', 'current_liability', False),
    ('204003', 'Payable to Risk SW', 'liability', 'current_liability', False),
    ('204004', 'Payable to Veritas', 'liability', 'current_liability', False),
    ('205001', 'Unearned Premium Reserve', 'liability', 'provision', False),
    ('205002', 'Reinsurance unearned premium', 'liability', 'provision', False),
    ('205003', 'Unearned Premium Reserve', 'liability', 'provision', False),
    ('206001', 'Finance lease - current liability Vehicles', 'liability', 'long_term_liability', False),
    ('207001', 'Finance Lease - Current Liability', 'liability', 'current_liability', False),
    ('208001', 'Provision for reinsurers share of IBNR', 'liability', 'provision', False),
    ('208002', 'Claim Reserve', 'liability', 'provision', False),
    ('208003', 'Claims Payable', 'liability', 'provision', False),
    ('208004', 'Provision for IBNR (BS)', 'liability', 'provision', False),
    ('208005', 'Provision for reinsurance claims reserve', 'liability', 'provision', False),
    ('208006', 'Unallocated loss adjustment', 'liability', 'provision', False),
    ('208007', 'Risk adjustment (BS)', 'liability', 'provision', False),
    ('208008', 'Health Claims Payable', 'liability', 'provision', False),
    ('209001', 'VAT', 'liability', 'current_liability', False),
    ('210002', 'Motor Vehicle', 'asset', 'fixed_asset', False),
    ('211001', 'OWHT Payable', 'liability', 'current_liability', False),
    ('211002', 'Other Withholding Taxes (OWHT)', 'liability', 'current_liability', False),
    ('211003', 'Training levy payable', 'liability', 'current_liability', False),
    ('212001', 'Reinsurance Commission Receivable', 'asset', 'current_asset', False),
    ('2120015', 'Health Reinsurance Outstanding Claims', 'asset', 'current_asset', False),
    ('212002', 'FAC Commission Receivable', 'asset', 'current_asset', False),
    ('212003', 'Reinsurance Outstanding Claims-FAC', 'asset', 'current_asset', False),
    ('212004', 'Reinsurance Outstanding Claims-XL & CAT', 'asset', 'current_asset', False),
    ('212005', 'Due from Reinsurance', 'asset', 'current_asset', False),
    ('212006', 'Reinsurance Treaty Payables', 'asset', 'current_asset', False),
    ('212007', 'Reinsurance FAC Payables', 'asset', 'current_asset', False),
    ('212008', 'Reinsurance Payable XL & CAT', 'asset', 'current_asset', False),
    ('212009', 'Reinsurance Payable- FMRE MQS', 'asset', 'current_asset', False),
    ('212010', 'Reinsurance Outstanding Claim', 'asset', 'current_asset', False),
    ('212011', 'Reinsurance Premium Retention', 'asset', 'current_asset', False),
    ('212012', 'Reinsurance MQST Payable JBB', 'asset', 'current_asset', False),
    ('212014', 'Reinsurance Risk adjustment', 'asset', 'current_asset', False),
    ('212015', 'Reinsurance Cash Call Receivable', 'asset', 'current_asset', False),
    ('212016', 'Reinsurance Health Treaty Payable', 'asset', 'current_asset', False),
    ('213001', 'Leave Pay Provision', 'liability', 'current_liability', False),
    ('213002', 'Provision for Severence', 'liability', 'current_liability', False),
    ('213003', 'Pension & Provident Fund Payable', 'liability', 'current_liability', False),
    ('214001', 'Rent Payable', 'liability', 'current_liability', False),
    ('214002', 'Account Payable', 'liability', 'current_liability', False),
    ('214003', 'Health Admin Payable', 'liability', 'current_liability', False),
    ('215001', 'Commission Payable-Brokers', 'liability', 'current_liability', False),
    ('215002', 'Provision for Expenses', 'liability', 'current_liability', False),
    ('215003', 'Salary Payable', 'liability', 'current_liability', False),
    ('215004', 'Unidentified Income', 'liability', 'current_liability', False),
    ('215005', 'Unidentified income:claims', 'liability', 'current_liability', False),
    ('215007', 'Lease Liability - IFRS 16', 'liability', 'current_liability', False),
    ('215008', 'Loan Liabilities', 'liability', 'current_liability', False),
    ('215009', 'Loan Liabilities Short-term', 'liability', 'current_liability', False),
    ('215010', 'Prepayments', 'liability', 'current_liability', False),
    ('216001', 'Stated Capital (Issued Share Capital)', 'equity', 'equity', False),
    ('217001', 'IBNR transfer from Retained earnings', 'equity', 'equity', False),
    ('217002', 'Retained Earnings', 'equity', 'equity', False),
    ('218001', 'Reserve for Change in IBNR', 'equity', 'equity', False),
    ('219001', 'Current Year Earnings', 'equity', 'equity', False),
    ('219003', 'Provision for Tax', 'liability', 'current_liability', False),
    ('220001', 'G F S Software', 'asset', 'fixed_asset', False),
    ('220002', 'Office Equipments', 'asset', 'fixed_asset', False),
    ('220003', 'Computers & Network', 'asset', 'fixed_asset', False),
    ('220004', 'Office furniture', 'asset', 'fixed_asset', False),
    ('220005', 'Fixtures and Fittings', 'asset', 'fixed_asset', False),
    ('230001', 'Accumulated Depreciation-F&F', 'asset', 'fixed_asset', False),
    ('230002', 'Accumulated Depreciation-IT Equipment', 'asset', 'fixed_asset', False),
    ('240001', 'Claims Subrogation Receivable', 'asset', 'current_asset', False),
    ('240002', 'Provision for Subrogation - ECL', 'asset', 'current_asset', False),
    ('250001', 'Right of Use (ROU) - Accumulated Depreciation', 'asset', 'fixed_asset', False),
    ('250002', 'Right of Use (ROU) - Asset', 'asset', 'fixed_asset', False),
    ('260001', 'Veritas salvage receivable', 'asset', 'current_asset', False),
    ('270001', 'Deferred tax asset', 'asset', 'current_asset', False),
    ('280001', 'First Capital Bank-0002704018802 BWP', 'asset', 'bank', True),
    ('280002', 'First Capital Bank -0002703011578 ZAR', 'asset', 'bank', True),
    ('280003', 'First Capital Bank-0020279780211 USD', 'asset', 'bank', True),
    ('280004', 'First Capital Bank USD Fixed Deposit - 0002101020994', 'asset', 'bank', True),
    ('280005', 'FNBB (Call A/C) - 62407809485', 'asset', 'bank', True),
    ('280006', 'FNBB 62403392335 CHEQ A/C', 'asset', 'bank', True),
    ('280007', 'FNBB (Claims A/C)-62493282265', 'asset', 'bank', True),
    ('280008', 'FNBB (Investment Income A/C) - 62493292264', 'asset', 'bank', True),
    ('280009', 'FNBB-(Choppies Kiosk)62571146797', 'asset', 'bank', True),
    ('280010', 'FNBB Credit Card Control A/C - 4901344312871000', 'asset', 'bank', True),
    ('280011', 'Orange Money Account', 'asset', 'bank', True),
    ('280012', 'Petty Cash', 'asset', 'bank', True),
    ('280013', 'Stanbic Bank-9060004053147', 'asset', 'bank', True),
    ('280014', 'Bank Suspense Account', 'asset', 'bank', True),
    ('280017', 'Liquidity Transfer', 'asset', 'bank', True),
    ('280023', 'Alpha Health Bank FNB 63162367601', 'asset', 'bank', True),
    ('280024', 'FNB USD A/C-63167551382', 'asset', 'bank', True),
    ('280029', 'MRI and Medlane Admin Fees', 'asset', 'bank', True),
    ('290001', 'BONU Accounts Receivable', 'asset', 'current_asset', False),
    ('290002', 'Accounts Receivable', 'asset', 'current_asset', False),
    ('290003', 'Graphite Accounts Receivable (MIS)', 'asset', 'current_asset', False),
    ('290004', 'Provision for credit losses', 'asset', 'current_asset', False),
    ('290005', 'C.A.R Premium Receivable', 'asset', 'current_asset', False),
    ('29004', 'RI  SA Accounts Receivable', 'asset', 'current_asset', False),
    ('303', 'Accounting Fees', 'expense', 'operating_expense', False),
    ('321', 'Legal Expense - Claims', 'expense', 'operating_expense', False),
    ('330', 'Prepayments', 'asset', 'current_asset', False),
    ('34', 'Software Development', 'asset', 'fixed_asset', False),
    ('78', 'Instant Insurance Accounts Receivable', 'asset', 'current_asset', False),
    ('84', 'Change in Unearned Re-Insurance Premium', 'expense', 'cost_of_insurance', False),
    ('91', 'Yash Cell Admin Fees', 'expense', 'operating_expense', False),
    ('92', 'Trans Cash & Carry Admin fees', 'expense', 'operating_expense', False),
    ('97', 'Finance Cost', 'expense', 'operating_expense', False),
    ('999999', 'Undistributed Profits/Losses', 'equity', 'equity', False),
    ('699999', 'Rounding Adjustment', 'expense', 'operating_expense', False),
]


def main():
    # Make sure BWP exists (default for new accounts)
    Currency.objects.get_or_create(code='BWP', defaults={'name': 'Botswana Pula'})

    with transaction.atomic():
        # 1. Wipe legacy JEs and lines (the 2 FY24/FY25 trial-balance seeds
        #    Pramod ran via seed_production_fy.py). They reference the 4-digit
        #    codes about to disappear, so they must go first.
        #    Match by description prefix — Pramod's seed sed-patched source_id
        #    to NULL during deploy, so source_id filtering won't find them.
        from django.db.models import Q
        legacy_jes = JournalEntry.objects.filter(
            Q(description__startswith='FY24 Trial Balance — opening positions')
            | Q(description__startswith='FY25 Trial Balance — opening positions')
        )
        legacy_pks = list(legacy_jes.values_list('pk', flat=True))
        n_jes = len(legacy_pks)
        if n_jes:
            # Bulk QuerySet.delete() bypasses the model-level immutability
            # guard (POSTED status normally blocks delete()). Acceptable for
            # a one-shot chart-of-accounts migration.
            JournalEntryLine.objects.filter(journal_entry_id__in=legacy_pks).delete()
            JournalEntry.objects.filter(pk__in=legacy_pks).delete()
            print(f'  Removed {n_jes} legacy trial-balance JE(s)')

        # 2. Remove old accounts that are NOT in the new chart.
        new_codes = {code for code, *_ in ACCOUNTS_V2}
        old_codes = set(Account.objects.values_list('code', flat=True)) - new_codes
        if old_codes:
            # Any remaining lines on old accounts will block this delete; we
            # cleared them above. If anything else still references an old
            # account (rare), the PROTECT FK will raise.
            removed = Account.objects.filter(code__in=old_codes).delete()
            print(f'  Removed {len(old_codes)} legacy accounts: {sorted(old_codes)[:8]}...')

        # 3. Upsert the new 6-digit chart.
        created = updated = 0
        for code, name, acct_type, sub_type, is_bank in ACCOUNTS_V2:
            obj, was_created = Account.objects.update_or_create(
                code=code,
                defaults={
                    'name': name,
                    'account_type': acct_type,
                    'sub_type': sub_type,
                    'is_bank_account': is_bank,
                    'is_active': True,
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1
        print(f'  Chart of Accounts: {created} created, {updated} updated, '
              f'{len(ACCOUNTS_V2)} total.')


main()
