"""
reporting/entity_bs_templates.py

Per-entity Balance Sheet templates for the non-ADIC group entities.

CFO directive 2026-06-09 (Legakwa Ntabeni "Omni System — Balance Sheet
Format Reconfiguration" — explicit approval given the same day): non-
insurance entities should not show Reinsurance Provisions, Claims Payable
- All Risk, Unearned Premium Reserve, Subrogation / Salvage Receivables,
Deferred Acquisition Costs on their balance sheet. Each entity gets a BS
structure aligned to its actual business.

Same mechanic as `reporting/entity_pl_templates.py` (shipped 2026-06-08
commit d8b3ec7): match lines by Account.name substring (case-insensitive),
NOT by bare GL code. Bare codes are unreliable across entities due to the
Odoo-prefix convention (live prod = `XXX-NNNNNN` dash; ADSA pack csv =
`XXX_NNNNNN` underscore; and several codes Legakwa cited in her docx do
not exist on prod at all).

ADIC + ADIL stay on `reporting/ma_bs_spec.py` (frozen insurance layout)
— the engine dispatches by Company.code (not entity_type — ADIL is
miscoded as 'trading' but is life insurance).

Sections list is consistent across all non-insurance templates so the
frontend can render any of them with the same component:
  current_assets · non_current_assets · current_liabilities ·
  non_current_liabilities · equity
Section-level signs follow the natural side: assets debit-natural,
liabilities + equity credit-natural. Within a section, a "contra" line
(e.g. Accumulated Depreciation) is matched and SUBTRACTED.

Each template = a dict of:
  entity_code: DB Company.code
  pdf_alias:   Legakwa-docx code (Co1/Co2/...)
  report_title
  currency:    ISO; falls back to Company.base_currency
  sections:    [ {id, label, side, subtotal_label, lines:[...]} ]

Line:
  id, label
  keywords: substrings; ANY match qualifies (icontains, Account.name)
  exclude:  substrings that disqualify a match
  contra:   if True, the line's signed amount is SUBTRACTED from the
            section subtotal (Accumulated Depreciation, Provision for
            Doubtful Debts, etc.)
"""
from __future__ import annotations


def _line(id_, label, *keywords, exclude=None, contra=False):
    return {
        'id': id_, 'label': label,
        'keywords': list(keywords),
        'exclude':  list(exclude or []),
        'contra':   bool(contra),
    }


# Reusable section builders — every non-insurance entity has roughly the
# same chart, so define the shared structure once and let each entity
# override the section labels / line set as needed.

def _current_assets(lines): return {
    'id': 'current_assets', 'label': 'Current Assets', 'side': 'asset',
    'subtotal_label': 'Total Current Assets', 'lines': lines,
}
def _non_current_assets(lines): return {
    'id': 'non_current_assets', 'label': 'Non-Current Assets', 'side': 'asset',
    'subtotal_label': 'Total Non-Current Assets', 'lines': lines,
}
def _current_liabilities(lines): return {
    'id': 'current_liabilities', 'label': 'Current Liabilities', 'side': 'liability',
    'subtotal_label': 'Total Current Liabilities', 'lines': lines,
}
def _non_current_liabilities(lines): return {
    'id': 'non_current_liabilities', 'label': 'Non-Current Liabilities', 'side': 'liability',
    'subtotal_label': 'Total Non-Current Liabilities', 'lines': lines,
}
def _equity(lines): return {
    'id': 'equity', 'label': 'Equity', 'side': 'equity',
    'subtotal_label': 'Total Equity', 'lines': lines,
}


# ---------------------------------------------------------------------------
# Standard commercial line library — used by most non-insurance templates
# ---------------------------------------------------------------------------

# Current assets ----------------------------------------------------------
L_BANK   = _line('bank',     'Bank & Cash',
                 'Bank', 'Cash', 'FNB', 'TransferWise', 'Aspire',
                 'Petty Cash', 'KWA Bank', 'USD Bank',
                 exclude=['Overdraft', 'Reconciliation'])
L_TRADE_RECV = _line('trade_recv', 'Trade Receivables',
                 'Account Receivable', 'Accounts Receivable',
                 'Trade Receivable', 'Trade Debtors',
                 exclude=['Related', 'Staff', 'Group', 'Singapore',
                          'Quantum', 'Insurtech', 'Risk Software',
                          'Zambia', 'South Africa', 'Alpha Direct',
                          'Veritas', 'Unicoin', 'GCE', 'ADRisk',
                          'AlphaLife', 'Life'])
L_RP_RECV    = _line('rp_recv', 'Related Party Receivables',
                 'Receivable from', 'Due from')
L_STAFF      = _line('staff_loans', 'Staff Loans & Advances',
                 'Staff Loan', 'Employee Loan', 'Staff Advance')
L_PREPAY     = _line('prepayments', 'Prepayments & Deposits',
                 'Prepayment', 'Prepaid', 'Deposit',
                 exclude=['Bank', 'Customer'])
L_TAX_ASSET  = _line('tax_assets', 'Tax Assets (VAT / WHT / Income Tax)',
                 'Tax Paid', 'VAT Receivable', 'WHT Receivable',
                 'Deferred Tax Asset', 'Tax Refund')

# Non-current assets ------------------------------------------------------
L_PPE        = _line('ppe', 'Property, Plant & Equipment',
                 'Motor Vehicle', 'Furniture', 'IT Equipment',
                 'Laptop', 'Computer', 'Equipment',
                 'Display Rack', 'Fixtures', 'Office Equipment',
                 exclude=['Accumulated', 'Depreciation', 'Salvage'])
L_ACC_DEPR   = _line('acc_depr', 'Accumulated Depreciation',
                 'Accumulated Depreciation', 'Accum Depreciation',
                 contra=True)
L_ROU        = _line('rou', 'Right of Use Asset',
                 'Right of Use', 'RoU')
L_INVEST     = _line('investments', 'Investments in Subsidiaries / Related Parties',
                 'Investment in', 'Investment In')
L_LOANS_GIVEN= _line('loans_given', 'Loans to Related Parties',
                 'Loan to', 'Loans to',
                 exclude=['Receivable', 'Account'])
L_GOODWILL   = _line('goodwill', 'Goodwill & Intangibles',
                 'Goodwill', 'Intangible', 'Software License')

# Current liabilities ----------------------------------------------------
L_TRADE_PAY  = _line('trade_pay', 'Trade Payables',
                 'Account Payable', 'Accounts Payable',
                 'Trade Payable', 'Trade Creditor',
                 exclude=['Related', 'Group', 'Singapore', 'Quantum',
                          'Insurtech', 'Risk Software', 'Zambia',
                          'South Africa', 'Alpha Direct', 'Veritas',
                          'Unicoin', 'GCE', 'ADRisk', 'AlphaLife',
                          'Life', 'Director', 'Shareholder'])
L_RP_PAY     = _line('rp_pay', 'Related Party Payables',
                 'Payable to', 'Due to', 'Loan from Related',
                 exclude=['Long Term', 'Long-Term', 'Singapore Director'])
L_TAX_LIAB   = _line('tax_liab', 'Tax Liabilities (VAT / PAYE / WHT)',
                 'Tax Payable', 'VAT Payable', 'WHT Payable',
                 'PAYE', 'Income tax payable', 'Income Tax Payable')
L_ACCRUAL    = _line('accruals', 'Accruals & Provisions',
                 'Accrual', 'Accrued', 'Provision',
                 exclude=['Reinsurance', 'IBNR', 'UPR', 'Claim'])
L_LEASE_CUR  = _line('lease_current', 'Lease Liability — Current Portion',
                 'Lease Liability', 'Lease Liabilities',
                 exclude=['Long Term', 'Long-Term', 'Non-current', 'Non Current'])
L_DT_CUR     = _line('dt_current', 'Deferred Tax — Current',
                 'Deferred Tax', exclude=['Non-current', 'Non Current', 'Asset'])

# Non-current liabilities -------------------------------------------------
L_LOANS_RP   = _line('loans_rp', 'Loans from Related Parties',
                 'Loan from', 'Loans from',
                 exclude=['Bank', 'Director'])
L_LEASE_NC   = _line('lease_nc', 'Lease Liability — Non-current',
                 'Lease Liability Long', 'Lease Liabilities Long',
                 'Lease Liability Non')
L_DIR_LIAB   = _line('director_liab', 'Director Liabilities',
                 'Director Liabilities', 'Director Loan',
                 'Directors Liabilities')
L_SHAREHOLDER= _line('shareholder_loans', 'Shareholder Loans',
                 'Shareholder', 'Notes Issued')
L_SEVERANCE  = _line('severance', 'Severance Benefits Payable',
                 'Severance')

# Equity ------------------------------------------------------------------
L_CAPITAL    = _line('capital', 'Share Capital', 'Capital',
                 exclude=['Reserve', 'Working'])
L_RETAINED   = _line('retained_earnings', 'Retained Earnings',
                 'Retained Earnings', 'Accumulated Earnings')
L_DIVIDENDS  = _line('dividends', 'Dividends', 'Dividend')
L_FX_RES     = _line('fx_reserve', 'FX Translation Reserve',
                 'FX Translation', 'Forex Translation',
                 'Foreign Currency Translation', 'Translation Reserve')
L_UNDIST_PL  = _line('undist_pl', 'Undistributed Profits / Losses',
                 'Undistributed')


# ---------------------------------------------------------------------------
# Templates — one per non-ADIC entity
# ---------------------------------------------------------------------------

_STANDARD_LIAB_NC = [L_LOANS_RP, L_DIR_LIAB, L_SHAREHOLDER]
_STANDARD_EQUITY  = [L_CAPITAL, L_RETAINED, L_DIVIDENDS, L_FX_RES, L_UNDIST_PL]


TEMPLATES: dict[str, dict] = {

    # --- Co1 Quantum Insurance Holdings (BWP) — investment holding -------
    'QIH': {
        'entity_code': 'QIH', 'pdf_alias': 'Co1',
        'report_title': 'Balance Sheet — Investment Holding',
        'currency': 'BWP',
        'sections': [
            _current_assets([L_BANK, L_TRADE_RECV, L_RP_RECV, L_STAFF, L_PREPAY, L_TAX_ASSET]),
            _non_current_assets([L_INVEST, L_LOANS_GIVEN, L_PPE, L_ACC_DEPR, L_GOODWILL]),
            _current_liabilities([L_TRADE_PAY, L_RP_PAY, L_TAX_LIAB, L_ACCRUAL]),
            _non_current_liabilities([L_LOANS_RP, L_DIR_LIAB, L_SHAREHOLDER]),
            _equity(_STANDARD_EQUITY),
        ],
    },

    # --- Co2 Veritas Capital Management (BWP) — salvage ------------------
    'VCM': {
        'entity_code': 'VCM', 'pdf_alias': 'Co2',
        'report_title': 'Balance Sheet — Salvage Operations',
        'currency': 'BWP',
        'sections': [
            _current_assets([
                L_BANK, L_TRADE_RECV, L_RP_RECV, L_STAFF, L_PREPAY, L_TAX_ASSET,
                _line('inventory', 'Salvage Inventory',
                      'Salvage', 'Stock', 'Inventory'),
            ]),
            _non_current_assets([L_PPE, L_ACC_DEPR, L_ROU]),
            _current_liabilities([L_TRADE_PAY, L_RP_PAY, L_TAX_LIAB, L_ACCRUAL, L_LEASE_CUR]),
            _non_current_liabilities([L_LOANS_RP, L_LEASE_NC, L_DIR_LIAB]),
            _equity(_STANDARD_EQUITY),
        ],
    },

    # --- Co3 Risk Software Africa (ZAR) — software services --------------
    'RSA': {
        'entity_code': 'RSA', 'pdf_alias': 'Co3',
        'report_title': 'Balance Sheet — Software Services',
        'currency': 'ZAR',
        'sections': [
            _current_assets([L_BANK, L_TRADE_RECV, L_RP_RECV, L_STAFF, L_PREPAY, L_TAX_ASSET]),
            _non_current_assets([L_INVEST, L_LOANS_GIVEN, L_PPE, L_ACC_DEPR, L_GOODWILL]),
            _current_liabilities([L_TRADE_PAY, L_RP_PAY, L_TAX_LIAB, L_ACCRUAL]),
            _non_current_liabilities(_STANDARD_LIAB_NC),
            _equity(_STANDARD_EQUITY),
        ],
    },

    # --- Co5 Alpha Direct South Africa (ZAR) — brokerage / distribution --
    'ADSA': {
        'entity_code': 'ADSA', 'pdf_alias': 'Co5',
        'report_title': 'Balance Sheet — Distribution / Brokerage',
        'currency': 'ZAR',
        'sections': [
            _current_assets([
                L_BANK, L_TRADE_RECV, L_RP_RECV, L_STAFF, L_PREPAY, L_TAX_ASSET,
                _line('comm_recv', 'Commission Receivables',
                      'Commission Receivable', 'Commission Recv'),
            ]),
            _non_current_assets([
                _line('display_racks', 'Display Racks & Fixtures',
                      'Display Rack'),
                L_PPE, L_ACC_DEPR,
            ]),
            _current_liabilities([L_TRADE_PAY, L_RP_PAY, L_TAX_LIAB, L_ACCRUAL]),
            _non_current_liabilities(_STANDARD_LIAB_NC),
            _equity(_STANDARD_EQUITY),
        ],
    },

    # --- Co6 Gaborone Coin Exchange (BWP) — forex bureau -----------------
    'GCX': {
        'entity_code': 'GCX', 'pdf_alias': 'Co6',
        'report_title': 'Balance Sheet — Coin / FX Exchange',
        'currency': 'BWP',
        'sections': [
            _current_assets([L_BANK, L_TRADE_RECV, L_RP_RECV, L_PREPAY, L_TAX_ASSET]),
            _non_current_assets([L_PPE, L_ACC_DEPR]),
            _current_liabilities([L_TRADE_PAY, L_RP_PAY, L_TAX_LIAB, L_SEVERANCE, L_ACCRUAL]),
            _non_current_liabilities([
                _line('loan_qih', 'Loan from Quantum Insurance Holdings',
                      'Loan from Quantum'),
                L_DIR_LIAB,
            ]),
            _equity(_STANDARD_EQUITY),
        ],
    },

    # --- Co7 Unicoin (BWP) — commission agency / brokerage ---------------
    'UNI': {
        'entity_code': 'UNI', 'pdf_alias': 'Co7',
        'report_title': 'Balance Sheet — Commission Agency',
        'currency': 'BWP',
        'sections': [
            _current_assets([
                L_BANK, L_TRADE_RECV, L_RP_RECV, L_STAFF, L_PREPAY, L_TAX_ASSET,
                _line('comm_recv', 'Commission Receivables',
                      'Commission Receivable'),
            ]),
            _non_current_assets([L_ROU, L_PPE, L_ACC_DEPR]),
            _current_liabilities([L_TRADE_PAY, L_RP_PAY, L_TAX_LIAB, L_ACCRUAL, L_LEASE_CUR, L_DT_CUR]),
            _non_current_liabilities([L_LOANS_RP, L_LEASE_NC,
                _line('dt_nc', 'Deferred Tax — Non-current',
                      'Deferred Tax', exclude=['Asset', 'Current']),
            ]),
            _equity(_STANDARD_EQUITY),
        ],
    },

    # --- Co8 Alpha Direct Insurtech (SGD/USD) — tech / R&D -------------
    'ADIPL': {
        'entity_code': 'ADIPL', 'pdf_alias': 'Co8',
        'report_title': 'Balance Sheet — Insurtech R&D',
        'currency': 'USD',
        'sections': [
            _current_assets([L_BANK, L_TRADE_RECV, L_RP_RECV, L_PREPAY, L_TAX_ASSET]),
            _non_current_assets([L_INVEST, L_LOANS_GIVEN, L_PPE, L_ACC_DEPR, L_GOODWILL]),
            _current_liabilities([L_TRADE_PAY, L_RP_PAY, L_TAX_LIAB, L_ACCRUAL]),
            _non_current_liabilities(_STANDARD_LIAB_NC),
            _equity([
                L_CAPITAL,
                _line('pref_shares', 'Preference Shares Issued',
                      'Preference Share', 'Pref Shares'),
                _line('valuation_res', 'Valuation Reserve',
                      'Valuation Reserve', 'Reserve - Valuation'),
                _line('share_equity', 'Share Equity',
                      'Share Equity'),
                L_RETAINED, L_DIVIDENDS, L_FX_RES, L_UNDIST_PL,
            ]),
        ],
    },

    # --- Co9 Alpha Insurtech Zambia (ZMW) — tech distribution ------------
    'AIZ': {
        'entity_code': 'AIZ', 'pdf_alias': 'Co9',
        'report_title': 'Balance Sheet — Zambia Operations',
        'currency': 'ZMW',
        'sections': [
            _current_assets([L_BANK, L_TRADE_RECV, L_RP_RECV, L_PREPAY, L_TAX_ASSET]),
            _non_current_assets([L_PPE, L_ACC_DEPR, L_GOODWILL]),
            _current_liabilities([L_TRADE_PAY, L_RP_PAY, L_TAX_LIAB, L_ACCRUAL]),
            _non_current_liabilities(_STANDARD_LIAB_NC),
            _equity(_STANDARD_EQUITY),
        ],
    },

    # --- Co11 ADRisk Global (INR) — actuarial services -------------------
    'ADRG': {
        'entity_code': 'ADRG', 'pdf_alias': 'Co11',
        'report_title': 'Balance Sheet — Actuarial Services',
        'currency': 'INR',
        'sections': [
            _current_assets([L_BANK, L_TRADE_RECV, L_RP_RECV, L_PREPAY, L_TAX_ASSET]),
            _non_current_assets([L_PPE, L_ACC_DEPR]),
            _current_liabilities([L_TRADE_PAY, L_RP_PAY, L_TAX_LIAB, L_ACCRUAL]),
            _non_current_liabilities([L_DIR_LIAB, L_LOANS_RP]),
            _equity(_STANDARD_EQUITY),
        ],
    },
}


def get_template(code: str) -> dict | None:
    """Return the BS template for a given DB Company.code, or None."""
    if not code:
        return None
    c = code.strip().upper()
    return TEMPLATES.get(c)
