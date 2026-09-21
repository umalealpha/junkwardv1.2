"""
ma_bs_spec.py — CFO Management Accounts Balance Sheet layout.

CFO directive 2026-05-20 (Manus TB-audit follow-up). Mirrors the BS
sheet in the Alpha Direct MA workbook. Each section lists the
`Account.fs_line_item` labels that roll up to it. Aggregation is by
fs_line_item, NOT by account_type — the docx-driven labels are the
canonical grouping, set by reporting/management/commands/seed_ma_classifications.

A given fs_line_item ("Claims Payable - All Risk") can appear in BOTH
Current Assets and Current Liabilities — net debit balance lands in
Assets, net credit balance in Liabilities. The renderer puts it on the
side the docx defines as primary; the other side displays a 0/contra.
"""
from __future__ import annotations

from typing import TypedDict


class BSSection(TypedDict):
    id: str
    label: str
    side: str                 # 'asset' | 'liability' | 'equity'
    lines: list[str]          # fs_line_item labels in display order
    subtotal_label: str


# ---------------------------------------------------------------------------
# Section layout — mirrors docx BS exactly
# ---------------------------------------------------------------------------

SECTIONS: list[BSSection] = [
    {
        'id': 'current_assets',
        'label': 'Current Assets',
        'side': 'asset',
        'lines': [
            'Bank and Cash Accounts',
            'Trade Receivables',
            'Other Receivables',
            'Reinsurance Provisions',
            'Related Party Receivables',
            'Staff Loans & Advances',
            'Claims Payable - All Risk',
            'Subrogation Receivables',
            'Salvage Receivables',
            'Deferred tax asset',
        ],
        'subtotal_label': 'Total Current Assets',
    },
    {
        'id': 'non_current_assets',
        'label': 'Non-Current Assets',
        'side': 'asset',
        'lines': [
            'Property, Plant & Equipment',
            'Property, Plant & Equipment - Accumulated Depreciation',
            'Right of Use - Asset',
        ],
        'subtotal_label': 'Total Non-Current Assets',
    },
    {
        'id': 'current_liabilities',
        'label': 'Current Liabilities',
        'side': 'liability',
        'lines': [
            'Unearned Premium Reserve',
            'Current Lease Liability',
            'Claims Payable - All Risk',
            'WHT',
            'VAT',
            'Due to Reinsurers',
            'IBNR - BS',
            'Severance & Leave liabilities',
            'Short-term Loan',
            'Tax Payable',
            'Trade & Other Payables',
        ],
        'subtotal_label': 'Total Current Liabilities',
    },
    {
        'id': 'non_current_liabilities',
        'label': 'Non-current Liabilities',
        'side': 'liability',
        'lines': [
            'Long-term Loan',
            'Lease Liabilities',
        ],
        'subtotal_label': 'Total Non-current Liabilities',
    },
    {
        'id': 'equity',
        'label': 'Equity',
        'side': 'equity',
        'lines': [
            'Stated Capital (Issued Share Capital)',
            'Retained Earnings',
            'IBNR Reserve',
        ],
        'subtotal_label': 'Total Equity',
    },
]


def all_lines() -> set[str]:
    out: set[str] = set()
    for sec in SECTIONS:
        out.update(sec['lines'])
    return out
