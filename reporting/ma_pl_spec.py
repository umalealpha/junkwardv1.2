"""
ma_pl_spec.py — CFO Management Accounts P&L layout (hardcoded).

This module is the alpha-finance translation of the Alpha Direct
Management Accounts workbook's `PL` sheet — the canonical reporting
layout the CFO produces each month.

Source: /Library/CloudStorage/OneDrive-AlphaDirectInsurance/
        Prathap Ganesharajah's files - Management ACs FY26/
        9. Mar 2026/Management ACs/Alpha Direct MA - Mar 2026.xlsx

The MA workbook drives reporting; alpha-finance must follow its
layout exactly. Don't relabel accounts to fit — change the report
to match the MA.

Each MA_LINES entry maps an MA line label to a list of GL account
codes. Sums are computed as sum of (credit_bwp - debit_bwp) for
revenue/credit-balance accounts, or sum of (debit_bwp - credit_bwp)
for expense/debit-balance accounts. The `sign` field captures which:

    'income'  → positive number is good; net = credit - debit
    'expense' → positive number is a cost; net = debit - credit

Section subtotals roll up via SECTIONS.
"""
from __future__ import annotations

from typing import TypedDict


class MALine(TypedDict):
    label: str
    codes: list[str]      # GL account codes that roll up to this line
    sign: str             # 'income' | 'expense'


# ---------------------------------------------------------------------------
# Line definitions — mirrors the MA PL sheet exactly
# ---------------------------------------------------------------------------

MA_LINES: dict[str, MALine] = {
    # ── Top of P&L: Gross Written Premium ───────────────────────────────
    # CSV group 100000-100012 (Gross Written Premium). FY25 sum end-Cr =
    # 125,708,402.78 = the MA truth. Previously the spec was missing
    # 100002 (Written Premium - Portal, 26.6M) and 100011 (Discount's
    # Issued, contra), which produced 99.1M instead of 125.7M on the
    # management pack — the documented gap.
    'gross_written_premium': {
        'label': 'Gross Written Premium',
        # CFO directive 2026-05-20 (MA-classifications docx):
        # 100007 Alpha SA RI Premium MOVES OUT (now Other Income).
        'codes': ['100001', '100002', '100003', '100004', '100006',
                  '100009', '100010', '100011', '100012'],
        'sign': 'income',
    },
    'premiums_ceded': {
        'label': 'Premiums Ceded To Reinsurance',
        'codes': ['101000', '101004', '101005', '101006', '101007',
                  '101008', '101010', '101011'],
        'sign': 'expense',
    },
    'change_in_upr': {
        'label': 'Change in UPR',
        # CFO directive 2026-05-20 (MA-classifications docx): legacy GL
        # code '84' (Change in Unearned Re-Insurance Premium) rolls into
        # the same Change-in-UPR line — the docx confirms it.
        'codes': ['102001', '84'],
        'sign': 'income',  # credit-balance — increase in UPR is income-side
    },

    # ── Claims block ────────────────────────────────────────────────────
    # CFO audit 2026-05-17: Net Claims for FY25 was understated by 5.5M
    # because reserve-movement codes (103015/103016/103107 and
    # 104012-104017) were inflating "RI Claims Recovered" and shrinking
    # the net cost. Those codes are technical reserve movements and belong
    # in the Provisions section, not in the current-period Claims line.
    # Cross-checked manually: removing them brings FY25 Net Claim from
    # -15.14M to -20.94M, which matches the MA workbook -20.64M to ~300K.
    'gross_claims': {
        'label': 'Gross Insurance claim expenses',
        'codes': ['103000', '103001', '103003', '103004', '103005',
                  '103006', '103008', '103010', '103012', '103013',
                  '103014'],
        'sign': 'expense',
    },
    'ri_claims_recovered': {
        'label': 'Gross Insurance claims recovered from reinsurers',
        # CFO directive 2026-05-20 (MA-classifications docx):
        # REVERSES the 2026-05-17 reserve-split audit. The docx puts
        # the technical reserve-movement codes back into the
        # "claims recovered from reinsurers" line:
        #   - 103015 / 103016 / 103107 — loss adjustment / risk adj
        #   - 104012 — RI claims reserve: Quota
        #   - 118008 — Movement in Closing of Treaties
        # 104013-104017 stay in 'movement_ri_claims_reserve' per docx
        # (docx lists them under their own MA line elsewhere).
        'codes': ['104000', '104001', '104004', '104007', '104008',
                  '104009', '104010', '104011', '104012',
                  '103015', '103016', '103107',
                  '118008'],
        'sign': 'income',
    },
    'subrogations_salvages': {
        'label': 'Subrogations & Salvages',
        'codes': ['105002', '105003', '105004'],
        'sign': 'income',
    },

    # ── Acquisition Cost ────────────────────────────────────────────────
    'insurance_in_a_box': {
        'label': 'Insurance in a box expenses',
        # CFO directive 2026-05-20 (MA-classifications docx):
        # Sefalana (107010), Choppies (107011), Yash Cell (91), Trans Cash
        # & Carry (92) admin fees, plus BONU admin (111041), all roll up
        # to "Insurance in a box expenses" in the MA P&L.
        'codes': ['118001', '107010', '107011', '91', '92'],
        'sign': 'expense',
    },
    'bonu_acquisition': {
        'label': 'BONU Acquisition',
        # CFO directive 2026-05-20 (MA-classifications docx):
        # 111041 BONU Admin Expenses moves here from Operating Expenses.
        'codes': ['118007', '111041'],
        'sign': 'expense',
    },
    'commission_from_reinsurers': {
        'label': 'Commission From Reinsurers',
        'codes': ['106000', '106001', '106002', '106003'],
        'sign': 'income',
    },
    'commissions_paid': {
        'label': 'Commissions Paid',
        # CFO directive 2026-05-19 (Charmaine #1): ADSA uses Odoo's
        # default 962001 Commission Expense, not ADIC's 107xxx range.
        # The endswith() fallback in ma_pl._line_total matches the
        # ADSA_-prefixed version automatically.
        # CFO directive 2026-05-20 (MA-classifications docx): 107010
        # Sefalana + 107011 Choppies move OUT of commissions_paid and
        # INTO insurance_in_a_box — they're admin fees for the in-a-box
        # channel, not broker commissions.
        'codes': ['107000', '107005', '107006', '107007', '107008',
                  '107009',
                  '962001'],
        'sign': 'expense',
    },
    'broker_entertainment': {
        'label': 'Broker Entertainment',
        # CFO directive 2026-05-20 (MA-classifications docx):
        # 118006 Acquisition Cost is grouped with Broker Entertainment.
        'codes': ['118005', '118006'],
        'sign': 'expense',
    },
    'health_insurance_acquisition': {
        'label': 'Health Insurance',
        # CFO directive 2026-05-20 (MA-classifications docx):
        # 111043 Health Care operational Expenses moves here from
        # Operating Expenses.
        'codes': ['111045', '111043'],
        'sign': 'expense',
    },

    # ── Other Income (below Gross Profit) ───────────────────────────────
    'other_income': {
        'label': 'Other Income',
        # CFO directive 2026-05-19 (Charmaine #1): ADSA's 445000
        # (NMI Binder Fee) and 445001 (Commission Income) sit outside
        # ADIC's 124xxx Other-Income range.
        # CFO directive 2026-05-20 (MA-classifications docx):
        #   * 100007 Alpha SA RI Premium MOVES IN here (was GWP).
        #   * 111000 Foreign Exchange Loss MOVES IN (was Operating Exp).
        #   * 123001, 124004, 124006 (interest) MOVE OUT to Finance Cost.
        'codes': ['124000', '124001', '124005',
                  '109002',
                  '100007', '111000',
                  '445000', '445001'],
        'sign': 'income',
    },

    # ── Operating Expenses ──────────────────────────────────────────────
    'employee_costs': {
        'label': 'Employee costs',
        # 630000 = ADSA Salary Expenses.
        'codes': ['110004', '110005', '110006', '110007', '110008',
                  '110009', '110010', '110012', '110013',
                  '630000'],
        'sign': 'expense',
    },
    'bonus_pay': {
        'label': 'Bonus Pay',
        'codes': ['110011'],
        'sign': 'expense',
    },
    'operating_expenses_other': {
        'label': 'Operating Expenses',
        # CFO directive 2026-05-19 (Charmaine #1): ADSA's expense layout
        # uses Odoo's default 600xxx / 612xxx range:
        #   600002 Transportation Costs   600006 Office Expenses
        #   600010 Accounting & Secretarial  600011 Insurance Expense
        #   600012 Production Cost        600070 Professional Fees
        #   600080 Rent & Rates           600090 Travel Expenses
        #   612002 Printing/Postage
        # Staff Welfare for ADSA = 401000 (mapped under staff_welfare).
        # CFO directive 2026-05-20 (MA-classifications docx):
        #   * ADD 111015 Audit Fees (bucket dissolved into Operating Expenses)
        #   * ADD 110016 Custom Duty, 111019 Tax Penalty, 111025 Design Exp
        #   * ADD 303 Accounting Fees, 321 Legal Expense - Claims
        #   * REMOVE 111041 (BONU admin → bonu_acquisition)
        #   * REMOVE 111043 (Health care ops → health_insurance_acquisition)
        'codes': ['111001', '111008', '111010', '111011', '111012',
                  '111013', '111014', '111015', '111016', '111017',
                  '110016', '111019', '111020',
                  '111021', '111022', '111023', '111024', '111025',
                  '111026', '111027', '111028', '111029', '111030',
                  '111031', '111032', '111033', '111034', '111035',
                  '111036', '111037', '111038', '111039', '111042',
                  '303', '321',
                  '600002', '600006', '600010', '600011', '600012',
                  '600070', '600080', '600090', '612002'],
        'sign': 'expense',
    },
    'it_expenses': {
        'label': 'IT Expenses',
        'codes': ['112000', '112001', '112003'],
        'sign': 'expense',
    },
    'risk_licensing_fees': {
        'label': 'Risk Licensing Fees',
        'codes': ['112002'],
        'sign': 'expense',
    },
    'licensing_fee': {
        'label': 'Licensing Fee',
        'codes': ['118745'],
        'sign': 'expense',
    },
    'paygates_expense': {
        'label': 'Paygates Expense',
        'codes': ['113002'],
        'sign': 'expense',
    },
    'telephone_internet': {
        'label': 'Telephone & Internet',
        'codes': ['114000', '114001', '114002'],
        'sign': 'expense',
    },
    'marketing_advertising': {
        'label': 'Marketing & Advertising',
        'codes': ['115001', '115002'],
        'sign': 'expense',
    },
    'staff_welfare': {
        'label': 'Staff Welfare',
        # 401000 = ADSA Staff Welfare (Odoo default range).
        'codes': ['116002', '401000'],
        'sign': 'expense',
    },
    'consultancy_fees': {
        'label': 'Consultancy Fees',
        'codes': ['117000', '117001'],
        'sign': 'expense',
    },
    # CFO directive 2026-05-20: 'audit_fees' bucket DISSOLVED into
    # operating_expenses_other per the MA-classifications docx
    # (111015 now rolls up under Operating Expenses).

    # ── Provisions ──────────────────────────────────────────────────────
    'bad_debt_expense': {
        'label': 'Bad Debt Expenses',
        # CFO directive 2026-05-20: legacy 195 also rolls here.
        'codes': ['121000', '195'],
        'sign': 'expense',
    },
    'provision_related_party': {
        'label': 'Provision for related party',
        'codes': ['119000'],
        'sign': 'expense',
    },
    'provision_subrogation': {
        'label': 'Provision for Subrogation',
        'codes': ['120001'],
        'sign': 'expense',
    },
    'movement_ibnr': {
        'label': 'Movement in IBNR Reserve',
        # CFO directive 2026-05-20: 147 (Reinsurers share of IBNR claims)
        # rolls here per the MA docx.
        'codes': ['135', '147'],
        'sign': 'expense',
    },
    # CFO directive 2026-05-20 (MA-classifications docx):
    # The reserve-split audit from 2026-05-17 has been REVERSED. The
    # CFO confirmed the canonical MA workbook keeps these reserve-
    # movement codes inside "RI Claims Recovered" (see ri_claims_recovered
    # above for 103015 / 103016 / 103107 / 104012 / 118008). The previous
    # 'movement_loss_adjustment_reserve' bucket is DELETED.
    # 104013-104017 remain in 'movement_ri_claims_reserve' below — the
    # docx lists them separately at their own MA line.
    'movement_ri_claims_reserve': {
        'label': 'Movement in Reinsurance Claims Reserve',
        'codes': ['104013', '104014', '104016', '104017'],
        'sign': 'income',
    },

    # ── Below-the-line ──────────────────────────────────────────────────
    'depreciation': {
        'label': 'Depreciation',
        # 600092 = ADSA Depreciation Expense.
        'codes': ['122001', '122000', '600092'],
        'sign': 'expense',
    },
    'finance_cost': {
        'label': 'Finance Cost',
        # 620000 = ADSA Bank Fees, 650000 = ADSA Finance Costs.
        # CFO directive 2026-05-20 (MA-classifications docx):
        # All interest-related lines roll under Finance Cost per the MA
        # presentation (net finance cost view, not Other Income):
        #   97     Finance Cost          123001 Interest Income
        #   124004 Interest From Staff Loan   124006 Interest Income - Other
        'codes': ['111002', '97', '123001', '124004', '124006',
                  '620000', '650000'],
        'sign': 'expense',
    },
    'taxation': {
        'label': 'Taxation',
        'codes': ['119001', '119002'],
        'sign': 'expense',
    },
}


# ---------------------------------------------------------------------------
# Section structure — drives the report rendering order
# ---------------------------------------------------------------------------

SECTIONS = [
    # (section_id, label, [line_ids], subtotal_label, subtotal_signs)
    {
        'id': 'net_earned_premium_block',
        'label': 'Net Earned Premium',
        'lines': ['gross_written_premium', 'premiums_ceded', 'change_in_upr'],
        'subtotal_label': 'Net Earned Premium',
    },
    {
        'id': 'claims',
        'label': 'Claims',
        'lines': ['gross_claims', 'ri_claims_recovered', 'subrogations_salvages'],
        'subtotal_label': 'Net Claim incurred',
    },
    {
        'id': 'acquisition',
        'label': 'Acquisition Cost',
        'lines': ['insurance_in_a_box', 'bonu_acquisition',
                  'commission_from_reinsurers', 'commissions_paid',
                  'broker_entertainment', 'health_insurance_acquisition'],
        'subtotal_label': 'Net Acquisition (Costs)/Income',
    },
    {
        'id': 'other_income_block',
        'label': 'Other Income',
        'lines': ['other_income'],
        'subtotal_label': 'Total Other Income',
    },
    {
        'id': 'operating_expenses',
        'label': 'Expenses',
        # CFO directive 2026-05-20: 'audit_fees' dissolved into
        # operating_expenses_other; removed from the section list.
        'lines': ['employee_costs', 'bonus_pay', 'operating_expenses_other',
                  'it_expenses', 'risk_licensing_fees', 'licensing_fee',
                  'paygates_expense', 'telephone_internet',
                  'marketing_advertising', 'staff_welfare',
                  'consultancy_fees'],
        'subtotal_label': 'Total Operating Expenses',
    },
    {
        'id': 'provisions',
        'label': 'Provisions',
        # CFO directive 2026-05-20: 'movement_loss_adjustment_reserve'
        # dissolved into ri_claims_recovered above; removed from the
        # section list.
        'lines': ['bad_debt_expense', 'provision_related_party',
                  'provision_subrogation', 'movement_ibnr',
                  'movement_ri_claims_reserve'],
        'subtotal_label': 'Total Provisions',
    },
]

# Calculated checkpoints in MA order:
#   Net Earned Premium = sum(net_earned_premium_block)
#   Net Claim incurred = sum(claims)
#   Gross Loss Ratio   = -gross_claims / gross_written_premium
#   Net Loss Ratio     = -Net Claim incurred / Net Earned Premium
#   Net Acquisition    = sum(acquisition)
#   Gross Profit       = Net Earned Premium + Net Claim incurred + Net Acquisition
#   Total Other Income = sum(other_income_block)
#   Total Opex         = -sum(operating_expenses)
#   Total Provisions   = -sum(provisions)
#   EBITDA             = Gross Profit + Total Other Income - Total Opex - Total Provisions
#   EBIT               = EBITDA - Depreciation
#   PBT                = EBIT - Finance Cost
#   PAT                = PBT - Taxation
