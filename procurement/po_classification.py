"""
po_classification.py — PO routing + account blocklist.

Business rule (CFO directive, 2026-05-13):
    Purchase Orders exist for two things only:

      1. CLAIMS  — paying claim suppliers (panel beaters, motor parts,
                   towing, glass, medical providers, assessors, salvage).
                   In Odoo this is `purchase_type = 'claims'`. 84% of
                   PO volume by count.

      2. GENERAL / OPERATIONS — paying for the running of the company
                   (IT, audit, legal, rent, utilities, marketing).
                   In Odoo this is `purchase_type = 'general'`. 15%.

      Untagged POs in Odoo (purchase_type blank, ~1%) are NOT auto-
      routed — they are flagged for CFO review and left on the
      catch-all 111014 account.

    Reinsurance (premium ceded, RI commission, RI recoveries) is NOT a
    PO workflow — it flows through the Reinsurance module (cessions,
    treaties, bordereaux). Likewise payroll, depreciation, tax and bad-
    debt provisions are not PO-eligible.

The classification truth lives in Odoo's `purchase_type` field on every
PO. The previous heuristic over supplier names is removed — the answer
is already in the source system.
"""
from __future__ import annotations

from typing import Literal

Category = Literal['claims', 'operations', 'review']


# ---------------------------------------------------------------------------
# Default GL account codes
# ---------------------------------------------------------------------------
# 103000 = Claims Settlement - Instant Insurance Policies  (cost_of_insurance)
# 111014 = Professional Fees - other                       (operating_expense)
CLAIMS_DEFAULT_ACCOUNT_CODE = '103000'
OPERATIONS_DEFAULT_ACCOUNT_CODE = '111014'

# Sentinel justification marker for POs whose Odoo purchase_type is blank.
# A reporting view filters on this string to surface them for CFO review.
REVIEW_MARKER = 'PENDING CFO REVIEW: Odoo purchase_type was blank'


def category_from_odoo_purchase_type(odoo_value) -> Category:
    """Map Odoo `purchase_type` selection value → alpha-finance category."""
    if odoo_value == 'claims':
        return 'claims'
    if odoo_value == 'general':
        return 'operations'
    return 'review'


def default_account_code_for(category: Category) -> str:
    if category == 'claims':
        return CLAIMS_DEFAULT_ACCOUNT_CODE
    # 'operations' and 'review' both default to the catch-all; 'review' is
    # additionally tagged in justification so it surfaces in the review list.
    return OPERATIONS_DEFAULT_ACCOUNT_CODE


# ---------------------------------------------------------------------------
# Blocked account code prefixes — never permitted on a PO line
# ---------------------------------------------------------------------------
#   101xxx — Reinsurance Expense (premium ceded)        → Reinsurance module
#   104xxx — Reinsurance Share of Claims (recoveries)   → Reinsurance module
#   106xxx — Reinsurance Commission Received            → Reinsurance module
#   107xxx — Commission Paid (brokers, agents, digital) → Commission accruals
#   110xxx — Payroll (salaries, bonus, severance, etc.) → Payroll module
#   119xxx — Tax expense (deferred, income)             → Tax close
#   120xxx — Provision for subrogation ECL              → ECL workflow
#   121xxx — Provision for bad debts ECL                → ECL workflow
#   122xxx — Depreciation, ROU amortisation             → Fixed assets
# 4-digit legacy equivalents (5200/5400/5500/5600/6100/6110) are likewise blocked.
BLOCKED_PO_ACCOUNT_PREFIXES = (
    '101', '104', '106', '107', '110', '119', '120', '121', '122',
)
BLOCKED_PO_ACCOUNT_CODES_EXACT = frozenset({
    '5200', '5400', '5500', '5600', '6100', '6110',
})


def is_po_eligible_code(code: str) -> bool:
    """True if a GL account code is permitted as a PO line target."""
    if not code:
        return False
    c = code.strip()
    if c in BLOCKED_PO_ACCOUNT_CODES_EXACT:
        return False
    if any(c.startswith(p) for p in BLOCKED_PO_ACCOUNT_PREFIXES):
        return False
    return True


def filter_po_eligible(account_qs):
    """Apply the PO-eligibility blocklist to an Account queryset."""
    from django.db.models import Q

    qs = account_qs.filter(account_type__in=('expense', 'asset'))
    qs = qs.exclude(code__in=BLOCKED_PO_ACCOUNT_CODES_EXACT)
    blocked_q = Q()
    for prefix in BLOCKED_PO_ACCOUNT_PREFIXES:
        blocked_q |= Q(code__startswith=prefix)
    return qs.exclude(blocked_q)
