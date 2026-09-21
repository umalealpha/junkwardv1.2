"""
fnb/endpoints.py

FNB API endpoint paths.

These are the real production paths from the FNB API spec pack supplied
by Christopher Marumo / FNB Botswana on 2026-05-18. Every constant is
the path AFTER `FNB_API_BASE` (e.g. https://api.fnb.co.za/apigateway).
The client joins base + path.

If FNB Botswana confirms a different base URL for BW commercial accounts,
only `FNB_API_BASE` in /etc/alpha-finance/.env changes — paths stay.
"""

# ---------------------------------------------------------------------------
# EFT Payments — pain.001 / ISO 20022 style
# ---------------------------------------------------------------------------
# POST: CustomerCreditTransferInitiation → returns instructionId
PAYMENT_INITIATE = '/paymentExecution/initiate/v1'

# GET: status of a previously submitted batch (by instructionId)
PAYMENT_STATUS = '/paymentExecution/retrieveReport/v1/{instruction_id}'

# GET: unpaids report
PAYMENT_UNPAIDS = '/paymentExecution/retrieveFilteredUnpaids/v1'


# ---------------------------------------------------------------------------
# Statements
# ---------------------------------------------------------------------------
# POST: CustomerStatementRequest → returns CustomerStatement
STATEMENT_RETRIEVE = '/statements/retrieveStatement/v1/'


# ---------------------------------------------------------------------------
# Transaction history
# ---------------------------------------------------------------------------
TRANSACTION_HISTORY = '/transaction-history/retrieve/v2/{account_number}'


# ---------------------------------------------------------------------------
# Notifications — pull model (we poll FNB for new + filtered)
# ---------------------------------------------------------------------------
# v2 — confirmed by Kabelo Sekoto (RMB) 2026-05-26 with sample body shape
NOTIFICATIONS_NEW      = '/notificationExecution/retrieveNewNotifications/v2'
NOTIFICATIONS_FILTERED = '/notificationExecution/retrieveFilteredNotifications/v2'


# ---------------------------------------------------------------------------
# Beneficiary management
# ---------------------------------------------------------------------------
# Referenced by fnb/beneficiaries.sync_beneficiary(). The module previously
# imported this name but it was never defined here, so `import fnb.beneficiaries`
# raised ImportError (broke the fnb smoke test and any package-level import).
# NOTE: path UNCONFIRMED by RMB — sync_beneficiary() is not wired into any live
# code path yet, so this constant only needs to exist for import-time. Confirm
# the real endpoint with RMB (as was done for Notifications v2) before enabling
# beneficiary sync in production.
BENEFICIARY_CREATE = '/beneficiaryExecution/createBeneficiary/v1'


# ---------------------------------------------------------------------------
# Backwards-compat aliases so existing imports keep working
# ---------------------------------------------------------------------------
PAYMENT_BATCH_SUBMIT = PAYMENT_INITIATE
PAYMENT_BATCH_STATUS = PAYMENT_STATUS
PAYMENT_SINGLE       = PAYMENT_INITIATE
PAYMENT_DETAIL       = PAYMENT_STATUS
STATEMENT_LIST       = STATEMENT_RETRIEVE
STATEMENT_DETAIL     = STATEMENT_RETRIEVE
STATEMENT_LINES      = STATEMENT_RETRIEVE
ACCOUNT_BALANCE      = TRANSACTION_HISTORY   # latest tx ≈ live position
ACCOUNT_DETAIL       = TRANSACTION_HISTORY
PING                 = '/health'             # may not exist on FNB side
