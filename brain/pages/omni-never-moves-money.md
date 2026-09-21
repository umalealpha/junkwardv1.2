---
id: omni-never-moves-money
title: Omni never moves money
category: decision
status: active
tags: [fnb, payments, money]
created: "2026-08-22T21:32:21"
updated: "2026-08-22T21:32:22"
---

<!-- compiled_truth -->
CFO rule (repeated ~100x): **Omni has no power to move money.** An Omni "approve" is a STATE CHANGE, not a payment. Money leaves ONLY via the bank (First Capital Bank / FNB), authorised by the CFO **on his phone with 2FA + a separate bank password**. Omni only LOADS a payment into his FNB queue.
FNB EFT is live and proven end-to-end (P10 settled 20-Aug, groupStatus ACSC) — but **"Authorised != Paid"**: an Omni/FNB "submitted" only means it reached the queue; settlement is confirmed by FNB's retrieveReport (ACSC) + his bank-side auth. Never gate an Omni approval as irreversible money movement. See [[fnb-payment-model]].


## Timeline

- time: 2026-08-22T21:32:21
  kind: decision
  summary: "Created this page: Omni never moves money"
  source: MACHINE-TALK.md
  affects: [omni-never-moves-money]

- time: 2026-08-22T21:32:22
  kind: decision
  summary: "Omni approve = state change, not a payment"
  source: MACHINE-TALK.md
  affects: [omni-never-moves-money]
