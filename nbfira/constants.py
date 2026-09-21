"""
nbfira/constants.py — canonical line definitions extracted from the
NBFIRA prescribed quarterly + annual templates.

These are the layout schemas the export must match cell-by-cell.
Phase 2 = schedules IS / A / A.1 / B / C.
"""

from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────────
# Insurance classes (NBFIRA Schedule of Classes — 9)
# ─────────────────────────────────────────────────────────────────────────
INSURANCE_CLASSES = [
    'accident',
    'engineering',
    'health',
    'property',
    'guarantee',
    'liability',
    'miscellaneous',
    'motor',
    'transportation',
]

CLASS_LABEL = {
    'accident':       'Accident',
    'engineering':    'Engineering',
    'health':         'Health',
    'property':       'Property',
    'guarantee':      'Guarantee',
    'liability':      'Liability',
    'miscellaneous':  'Miscellaneous',
    'motor':          'Motor',
    'transportation': 'Transportation',
}

# Blueprint §A.1 defaults — Insurance Risk Capital factor per class
DEFAULT_IRC = {
    'property':       0.15,
    'transportation': 0.30,
    'motor':          0.15,
    'accident':       0.50,
    'health':         0.50,
    'guarantee':      0.30,
    'liability':      0.70,
    'engineering':    0.30,
    'miscellaneous':  0.50,
}

# Blueprint §A.1 defaults — Market Risk Capital factor per asset bucket
DEFAULT_MRC = [
    ('cash',                 'Cash or near cash (less current and other liabilites)', 0.00),
    ('fixed_interest_1yr',   'Fixed Interest (Outstanding Term = 1 year)',           0.07),
    ('fixed_interest_2yr',   'Fixed Interest (Outstanding Term = 2 years)',          0.11),
    ('fixed_interest_5yr',   'Fixed Interest (Outstanding Term = 5 years)',          0.20),
    ('fixed_interest_7yr',   'Fixed Interest (Outstanding Term = 7 years)',          0.24),
    ('fixed_interest_10yr',  'Fixed Interest (Outstanding Term = 10 years)',         0.27),
    ('property',             'Property',                                              0.32),
    ('listed_equities',      'Listed Equities',                                       0.35),
    ('other_assets',         'Other assets',                                          0.35),
    ('unlisted_equities',    'Unlisted equities',                                     0.40),
]

# RETIRED 2026-08-18 — the g-factors are DERIVED from the asset mix by the filed
# workbook, not stored. They are not constants: the FY2026 filed returns show
# 0.8250/0.6500 for Q1, Q2 and Q4 but 0.8241/0.6635 for Q3. Kept only so nothing
# importing them breaks; a1_math.compute_a1 derives them and never reads these.
DEFAULT_G_INSURANCE = 0.825
DEFAULT_G_MARKET    = 0.65
DEFAULT_MCR_BWP     = 5_000_000.00

# ─────────────────────────────────────────────────────────────────────────
# Schedule A (current quarter) — line definitions
# Matches Short-term Quarterly Q3 March 2026.xlsx rows 7..84.
# ─────────────────────────────────────────────────────────────────────────
SCHEDULE_A_OPERATING = [
    ('A_OP_01', 'Net unearned premiums - opening'),
    ('A_OP_02', 'Premiums written -Gross'),
    ('A_OP_03', 'Premiums written -Net'),
    ('A_OP_04', 'Other net premiums (describe below; cell B86)'),
    ('A_OP_05', 'Net unearned premiums - closing'),
    ('A_OP_06', 'NET EARNED PREMIUMS'),
    ('A_OP_07', 'Net outstanding claims and IBNR - opening'),
    ('A_OP_08', 'Net claims and claims expenses paid'),
    ('A_OP_09', 'Net other claims (describe below; cell B87)'),
    ('A_OP_10', 'Outstanding claims and IBNR - closing'),
    ('A_OP_11', 'NET CLAIMS INCURRED'),
    ('A_OP_12', 'COMMISSIONS'),
    ('A_OP_13', 'Commissions paid'),
    ('A_OP_14', 'Commission recovered from reinsurers'),
    ('A_OP_15', 'EXPENSES'),
    ('A_OP_16', 'NET UNDERWRITING PROFIT/(LOSS)'),
    ('A_OP_17', 'Investment income - Total'),
    ('A_OP_18', 'Other income (specify)'),
    ('A_OP_19', 'Payment Plan Charges'),
    ('A_OP_20', 'Forex income'),
    ('A_OP_21', 'Other income'),
    ('A_OP_22', 'Other expenditure (specify)'),
    ('A_OP_23', 'Profit/(loss) before tax'),
    ('A_OP_24', 'Income tax expense'),
    ('A_OP_25', 'Net profit/(loss) for the period'),
    ('A_OP_26', 'Accumulated profit/(loss) at the beginning of the period'),
    ('A_OP_27', 'Sub-total'),
    ('A_OP_28', 'Transfer to/(from) non-distributable reserve'),
    ('A_OP_29', 'Transfer to/(from) other reserves'),
    ('A_OP_30', 'Dividends'),
    ('A_OP_31', 'ACCUMULATED PROFIT/(LOSS) AT THE END OF THE PERIOD'),
]

# Per-class breakdown columns: (premiums_written, premiums_earned, claims_incurred, commissions_and_expenses)
SCHEDULE_A_CLASS_COLS = [
    ('written',  'Premiums written'),
    ('earned',   'Premiums earned'),
    ('claims',   'Claims incurred'),
    ('comm_exp', 'Comm. & expenses'),
]

# Schedule A — Assets block (rows 130..168 in template)
SCHEDULE_A_ASSETS = [
    ('A_AS_01', 'Land and buildings'),
    ('A_AS_02', 'Debts secured on land'),
    ('A_AS_03', 'Due from company or persons (not individual)'),
    ('A_AS_04', 'Due from individual (not employee/ connected person)'),
    ('A_AS_05', 'Due from employee/ connected person'),
    ('A_AS_06', 'Debts (excl debentures and connected persons)'),
    ('A_AS_07', 'Due from any one company and any of its connected companies'),
    ('A_AS_08', 'Due from any one unincorporated body of persons'),
    ('A_AS_09', 'Due from the state or any public body'),
    ('A_AS_10', 'Tax recoveries due from taxation authorities'),
    ('A_AS_11', 'Due from individuals (not being an employee or connected person)'),
    ('A_AS_12', 'Due from employees and connected persons of the company'),
    ('A_AS_13', 'Public sector securities (issued by state and local authorities)'),
    ('A_AS_14', 'Debentures (listed)'),
    ('A_AS_15', 'Debentures (unlisted)'),
    ('A_AS_16', 'Equities and convertible debentures (listed)'),
    ('A_AS_17', 'Equities and convertible debentures (unlisted)'),
    ('A_AS_18', 'Investment in unit trust schemes'),
    ('A_AS_19', 'Share options and debenture options'),
    ('A_AS_20', 'Investments in connected and dependant companies'),
    ('A_AS_21', 'Insurance companies (shares)'),
    ('A_AS_22', 'Insurance companies (debts other than insurance debts below)'),
    ('A_AS_23', 'Non insurance companies (shares)'),
    ('A_AS_24', 'Non insurance companies (debts other insurance debts below)'),
    ('A_AS_25', 'Insurance debts including those due from connected and dependant'),
    ('A_AS_26', 'Premium income in respect of direct and facultative reinsurance'),
    ('A_AS_27', 'Amounts due from ceding insurers and intermediaries under reinsurance'),
    ('A_AS_28', 'Amounts due from reinsurers and intermediaries under reinsurance'),
    ('A_AS_29', 'Recoveries due by way of salvage'),
    ('A_AS_30', 'Other sums due from insurers'),
    ('A_AS_31', 'Cash and deposits (banks and building societies)'),
    ('A_AS_32', 'Cash'),
    ('A_AS_33', 'Deposits of less than 12 months'),
    ('A_AS_34', 'Deposits of more than 12 months'),
    ('A_AS_35', 'Shares in building societies'),
    ('A_AS_36', 'Loans secured by policies issued by the company'),
    ('A_AS_37', 'Computer equipment'),
    ('A_AS_38', 'Other office equipment, furniture, motor vehicle and other equipment'),
    ('A_AS_39', 'TOTAL ADMISSIBLE ASSETS'),
    ('A_AS_40', 'Inadmissible assets (specify)'),
]

# Schedule A — Liabilities block (rows 196..237)
SCHEDULE_A_LIAB = [
    ('A_LI_01', 'Net unearned premium provisions'),
    ('A_LI_02', 'Net outstanding claims'),
    ('A_LI_03', 'Net I B N R - Claims incurred but not reported'),
    ('A_LI_04', 'Net unexpired risk provision'),
    ('A_LI_05', 'Total technical provisions'),
    ('A_LI_06', 'Due to other insurers and reinsurers'),
    ('A_LI_07', 'Bank overdraft'),
    ('A_LI_08', 'Current provisions'),
    ('A_LI_09', 'Provision for current and deferred taxation'),
    ('A_LI_10', 'Impairments'),
    ('A_LI_11', 'Other current liabilities'),
    ('A_LI_12', 'Other liabilities (specify)'),
    ('A_LI_13', 'IFRS 16'),
    ('A_LI_14', 'Long-term loan'),
    ('A_LI_15', 'Total other liabilities'),
    ('A_LI_16', 'TOTAL LIABILITIES'),
]

# Schedule A — Surplus / PCT Cover block (rows 240..248)
SCHEDULE_A_SURPLUS = [
    ('A_SU_01', 'Total admissible assets'),
    ('A_SU_02', 'Less: Total Liabilities'),
    ('A_SU_03', 'Sub Total - net assets'),
    ('A_SU_04', 'Less: Requirement for additional assets per PCT'),
    ('A_SU_05', 'SURPLUS / SHORTFALL OF ASSETS (after PCT)'),
    ('A_SU_06', 'PCT Cover (full requirement)'),
]

# ─────────────────────────────────────────────────────────────────────────
# Schedule IS — Income Statement (Management Accounts layout)
# ─────────────────────────────────────────────────────────────────────────
SCHEDULE_IS = [
    ('IS_01', 'Gross Written Premium'),
    ('IS_02', 'Premium Ceded to Reinsurance'),
    ('IS_03', 'Change in UPR'),
    ('IS_04', 'NET EARNED PREMIUM'),
    ('IS_05', 'Gross Insurance Claim Expenses'),
    ('IS_06', 'Gross Insurance Claims Recovered from Reinsurers'),
    ('IS_07', 'Salvages and Other Recoveries'),
    ('IS_08', 'NET CLAIMS INCURRED'),
    ('IS_09', 'Reinsurance Commission Received'),
    ('IS_10', 'Commission Paid'),
    ('IS_11', 'NET ACQUISITION COST'),
    ('IS_12', 'GROSS PROFIT'),
    ('IS_13', 'Total Other Income'),
    ('IS_14', 'Total Management Expenses'),
    ('IS_15', 'Total Provisions'),
    ('IS_16', 'EBITDA'),
    ('IS_17', 'Depreciation'),
    ('IS_18', 'EBIT'),
    ('IS_19', 'Finance Cost'),
    ('IS_20', 'PBT'),
    ('IS_21', 'Taxation'),
    ('IS_22', 'PAT'),
    ('IS_23', 'Loss Ratio (Gross)'),
    ('IS_24', 'Loss Ratio (Net)'),
    ('IS_25', 'Cost Ratio'),
    ('IS_26', 'Combined Ratio'),
    ('IS_27', 'Gross Profit Margin'),
]

# ─────────────────────────────────────────────────────────────────────────
# Annual Return — AFS (Statement of Financial Position)
# Matches IMF General Insurance — March 2026.xlsx · sheet AFS
# ─────────────────────────────────────────────────────────────────────────
SCHEDULE_AFS = [
    # section, code, label
    ('ppe',       'AFS_PPE_01', 'Motor Vehicle'),
    ('ppe',       'AFS_PPE_02', 'Accumulated Depreciation-Motor Vehicle'),
    ('ppe',       'AFS_PPE_03', 'Computers & Networks'),
    ('ppe',       'AFS_PPE_04', 'Accumulated Depreciation-IT Equipment'),
    ('ppe',       'AFS_PPE_05', 'Furniture and Fittings'),
    ('ppe',       'AFS_PPE_06', 'Accumulated Depreciation-F&F'),
    ('ppe',       'AFS_PPE_TOT','Property Plant & Equipment — Net'),
    ('intangible','AFS_INT_01', 'Capitalisation of Graphite'),
    ('non_current','AFS_NC_01', 'Subrogation and other insurance Receivables'),
    ('non_current','AFS_NC_02', 'Right of Use - Asset'),
    ('non_current','AFS_NC_03', 'Salvages & Recoveries Receiveable'),
    ('non_current','AFS_NC_04', 'Deferred Tax Asset'),
    ('non_current','AFS_NC_TOT','Total Non-Current Assets'),
    ('current',   'AFS_CA_01', 'Bank'),
    ('current',   'AFS_CA_02', 'Insurance Premiums Due From Policyholders'),
    ('current',   'AFS_CA_03', 'Related party Receivables'),
    ('current',   'AFS_CA_04', 'Deposits'),
    ('current',   'AFS_CA_05', 'Investments'),
    ('current',   'AFS_CA_06', 'Graphite Receivable from Risk AI'),
    ('current',   'AFS_CA_07', 'Staff Loans & Advances'),
    ('current',   'AFS_CA_TOT','Total Current Assets'),
    ('total_assets','AFS_TA_01','Total Assets'),
    ('non_current_liab','AFS_NCL_01', 'Related party Payables'),
    ('non_current_liab','AFS_NCL_02', 'Unearned Premium Reserve'),
    ('non_current_liab','AFS_NCL_03', 'Finance Lease'),
    ('non_current_liab','AFS_NCL_04', 'Lease Liabilities'),
    ('non_current_liab','AFS_NCL_05', 'Collateral CAR & BOND'),
    ('non_current_liab','AFS_NCL_TOT','Total Non-Current Liabilities'),
    ('liab',      'AFS_LI_01', 'Accruals and Provisions'),
    ('liab',      'AFS_LI_02', 'Finance Lease - Short-term Portion'),
    ('liab',      'AFS_LI_03', 'Claims Payable - All Risk'),
    ('liab',      'AFS_LI_04', 'VAT'),
    ('liab',      'AFS_LI_05', 'Due to Reinsurers'),
    ('liab',      'AFS_LI_06', 'Severence & Leave liabilities'),
    ('liab',      'AFS_LI_07', 'Other Payables'),
    ('liab',      'AFS_LI_TOT','Total Current Liabilities'),
    ('equity',    'AFS_EQ_01', 'Stated Capital (Issued Share Capital)'),
    ('equity',    'AFS_EQ_02', 'Retained Earnings'),
    ('equity',    'AFS_EQ_03', 'IBNR Reserve'),
    ('equity',    'AFS_EQ_04', 'Profit / (loss) for the year'),
    ('equity',    'AFS_EQ_TOT','Total Equity'),
    ('total_liab','AFS_TL_01','Total Liabilities + Equity'),
]

# ─────────────────────────────────────────────────────────────────────────
# IMF Assets — line items A.1 ... A.x (IMF General Insurance template)
# Captures top-level line codes only; sub-lines (A.2.1, A.2.2) handled
# at runtime once Finance maps GL accounts.
# ─────────────────────────────────────────────────────────────────────────
SCHEDULE_IMF_ASSETS = [
    ('A.1',  'Notes and coins in till'),
    ('A.2',  'Deposits (A.2 = A.2.1 + A.2.2)'),
    ('A.2.1','Deposits — short-term (<= 1 year)'),
    ('A.2.2','Deposits — long-term (> 1 year)'),
    ('A.3',  'Debt securities — issued by residents'),
    ('A.4',  'Debt securities — issued by non-residents'),
    ('A.5',  'Investments in Collective Investment Schemes'),
    ('A.5.1','Money Market Funds (MMFs) — domestic'),
    ('A.5.2','Money Market Funds (MMFs) — foreign'),
    ('A.5.3','Equity Funds'),
    ('A.5.4','Bond Funds'),
    ('A.6',  'Listed equities — domestic'),
    ('A.7',  'Listed equities — foreign'),
    ('A.8',  'Unlisted equities'),
    ('A.9',  'Loans — secured'),
    ('A.10', 'Loans — unsecured'),
    ('A.11', 'Insurance debts (premiums, recoveries, salvages)'),
    ('A.12', 'Reinsurance share of technical provisions'),
    ('A.13', 'Property — own use'),
    ('A.14', 'Property — investment'),
    ('A.15', 'Plant, equipment, motor vehicles'),
    ('A.16', 'Intangible assets'),
    ('A.17', 'Deferred tax asset'),
    ('A.18', 'Other assets'),
    ('A.TOT','TOTAL ASSETS'),
]

# ─────────────────────────────────────────────────────────────────────────
# IMF Liabilities — line items L.1 ... L.x
# ─────────────────────────────────────────────────────────────────────────
SCHEDULE_IMF_LIAB = [
    ('L.1',  'Deposits (long-term external borrowings)'),
    ('L.2',  'Debt Securities and Preference shares'),
    ('L.3',  'Loans — bank'),
    ('L.4',  'Loans — non-bank'),
    ('L.5',  'Financial Derivatives'),
    ('L.5.1','Derivatives — commercial banks / statutory'),
    ('L.6',  'Insurance technical provisions — UPR'),
    ('L.7',  'Insurance technical provisions — Outstanding Claims'),
    ('L.8',  'Insurance technical provisions — IBNR'),
    ('L.9',  'Reinsurance payables'),
    ('L.10', 'Trade payables'),
    ('L.11', 'Provisions (other)'),
    ('L.12', 'Tax payable'),
    ('L.13', 'Lease liabilities'),
    ('L.14', 'Other liabilities'),
    ('L.15', 'Equity (share capital + retained + reserves)'),
    ('L.TOT','TOTAL LIABILITIES + EQUITY'),
]


# Schedule C — Cell Captive (zeros for ADIC; structure required)
SCHEDULE_C = [
    ('C_01', 'Active 1st-party cells — count'),
    ('C_02', 'Active 1st-party cells — GWP'),
    ('C_03', 'Active 3rd-party cells — count'),
    ('C_04', 'Active 3rd-party cells — GWP'),
    ('C_05', 'Dormant cells — count'),
    ('C_06', 'Dormant cells — GWP'),
    ('C_07', 'Top-10 cells by GWP — total'),
    ('C_08', 'Statutory surplus ratio band >110%'),
    ('C_09', 'Statutory surplus ratio band 100–110%'),
    ('C_10', 'Statutory surplus ratio band 76–100%'),
    ('C_11', 'Statutory surplus ratio band 51–75%'),
    ('C_12', 'Statutory surplus ratio band 26–50%'),
    ('C_13', 'Statutory surplus ratio band 15–25%'),
    ('C_14', 'Statutory surplus ratio band <15%'),
]
