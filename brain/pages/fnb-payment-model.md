---
id: fnb-payment-model
title: "FNB payment model + controls"
category: decision
status: active
tags: [fnb, payments, controls]
created: "2026-08-22T21:32:23"
updated: "2026-08-22T21:32:23"
---

<!-- compiled_truth -->
- **Reference (endToEndId, 35 char):** leads with the payee name + payment-number tail, and always ends with the **" (O)"** marker = loaded via Omni (vs keyed straight into FNB). Recon strips the (O) before matching. bank_beneficiary_name(140)/bank_our_reference(35)/bank_narration(140) are editable; all folded through **fnb_text** (ASCII only — an em-dash caused an FNB RR10/RJCT).
- **POP email:** Payment.remittance_email (default accountsdept@alphadirect.co.bw, editable, remembered per vendor on VendorBankAccount.email) feeds FNB's EMAL slot.
- **Maker-checker:** creator != releaser, enforced in submit_eft_batch (all four paths).
- **Batch cap:** FNB_BATCH_MAX_BWP = **2,000,000** (CFO's number; malformed => fail closed to 0, never a default).
- **Source account by KIND:** claims -> claims a/c GL 280007; else cheque/current GL 280006 (PAYMENT_REQUEST_FNB_SOURCE_ACCOUNTS).
- **Refund auto-load threshold:** BWP 5,000 (Graphite's 1-approver<=5k rule); above it stages + emails CFO.
- **Changed bank account:** held/refused (PAY-BANK-01), shows only last 4 digits; autofill from prior payment by LOOKUP, never AI. Principle: code decides, AI explains. See [[omni-never-moves-money]].


## Timeline

- time: 2026-08-22T21:32:23
  kind: decision
  summary: "Created this page: FNB payment model + controls"
  source: MACHINE-TALK.md
  affects: [fnb-payment-model]

- time: 2026-08-22T21:32:23
  kind: decision
  summary: How omni loads payments to FNB and the controls
  source: MACHINE-TALK.md
  affects: [fnb-payment-model]
