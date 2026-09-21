"""The VAT control accounts — ONE definition, used by everything.

CFO decision 2026-09-14: the VAT reconciliation must tie back to the SAME
accounts the VAT return already uses, and the two must never be able to
disagree. There was no single place to reuse: `reporting.build_vat_return`
reads no GL account at all (it is built purely from the Invoice and
ReverseChargeEntry sub-ledgers), and the only code that decides which account
VAT lands in is the POSTING in `billing/models.py`, where the codes were four
separate string literals. This module is that single place.

  output VAT -> 2180 'VAT output payable'    (credited on a customer invoice,
                                              debited back on a credit note)
  input  VAT -> 1250 'VAT input receivable'  (debited on a vendor bill,
                                              credited back on a credit note)

Both are in `ledger/management/commands/setup_chart_of_accounts.py` and must
exist in any live chart: the posting helper raises ValidationError rather than
creating a missing account, so an invoice could not post at all without them.

WHY NOT 209001/132000, which an earlier draft used:
  * 132000 was wrong outright. It comes from `ledger/invoice_parser.py`, the AI
    invoice-SUGGESTION parser — that file names an account, it posts nothing.
  * 209001 'VAT' is a real account in the 6-digit chart (ops/seeds/seed_coa_v2),
    but nothing in the code posts VAT to it. If a trial-balance import does put
    VAT there, it is added by configuration (VAT_OUTPUT_CONTROL_ACCOUNTS), not
    by editing this module — and the reconciliation now reports a configured
    code that is missing from the chart as VAT-TIE-04 instead of skipping it in
    silence. See the 🔴 CFO note in alpha_finance/settings.py.

This module is deliberately plain data with NO imports: `alpha_finance.settings`
imports it, so it must be safe to read before the app registry is loaded.

Changing a code here changes where VAT POSTS. That is a GL mapping change and
needs explicit CFO sign-off — it is not a refactor.
"""

#: Account VAT charged to customers is posted to.
VAT_OUTPUT_ACCOUNT = '2180'

#: Account VAT paid to suppliers is posted to.
VAT_INPUT_ACCOUNT = '1250'

#: The accounts the reconciliation reads movement from, per side. Tuples, not
#: lists, so no caller can mutate the shared definition it was handed. These
#: are the defaults; a deployment may add to them via the environment (see
#: alpha_finance/settings.py) without this module changing.
VAT_OUTPUT_CONTROL_ACCOUNTS = (VAT_OUTPUT_ACCOUNT,)
VAT_INPUT_CONTROL_ACCOUNTS = (VAT_INPUT_ACCOUNT,)
