# Customer Refund → FNB — Impact Map

What happens across the business when a MIS customer refund is paid. CFO ask
2026-07-24: "understand what this refund could do… will the reporting portal
change, will it reduce the FNB balance, what other impacts — map + develop."

| # | Impact | Does it happen? | Handled how (this module) | Status |
|---|--------|-----------------|---------------------------|--------|
| 1 | **FNB current-account balance drops** | **YES** — a real EFT debits FNB debtor `62403392335` when the batch is sent. | `load_refund_to_fnb(live=True)` → `submit_eft_batch`. **GATED**: preview-only until `REFUND_FNB_SEND_ENABLED` on + Finance/CFO. | Built, send OFF |
| 2 | **GL vs bank reconciliation** | **YES, and this is the key gap.** Cash leaves FNB but no journal entry is posted → Omni GL bank balance ≠ FNB statement → a rec exception. | The once-off `payments.Payment` record is created (matchable in bank-rec). The **GL journal (Dr premium-refund / Cr bank) is the ONE reserved step** — posting a JE is CFO sign-off (RULE #2b). Not auto-posted. | **Reserved — see below** |
| 3 | **Reporting portal** (reporting.alphadirect.co.bw) | **YES, once posted to the policy in Graphite** — written-premium / collections figures reduce. | Fires via `post_refund_back_to_graphite` callback → Graphite `makeRefund`. We can't deploy Graphite, so the callback is **dormant** until Graphite exposes the endpoint. | Built, dormant |
| 4 | **Frozen numbers / Management Accounts (GWP, NEP)** | **YES** — refunds reduce earned/written premium. | **FROZEN.** This module never touches the GWP tile or MA mapping. Material refund volumes → flag to CFO; any revenue restatement is 3×-confirm. | Flag only |
| 5 | **Graphite policy ledger** (`payment_transactions` is_refund, `ledger`, `sub_ledger`) | **YES** — the refund posts onto the policy. | Same callback as #3 (Graphite-side `makeRefund`). | Dormant (Graphite) |
| 6 | **Customer portal** | Policy flips to "Refunded" and shows the refund line. | Graphite-side, triggered by the callback. | Dormant (Graphite) |
| 7 | **Double / over-refund** | Risk. | Idempotent on `graphite_ref`; `bank_submitted_at` guard blocks re-send of a paid payment; the AI green-light (Graphite) checks amount ≤ paid + no prior refund. | Built |
| 8 | **DPO / RealPay collections** | Not directly — refund goes out via FNB EFT, not the collection gateway. | Separate reconciliation; no change here. | N/A |

## The one reserved piece — the GL journal (impact #2)

When money leaves FNB, the correct double entry is roughly:

    Dr  Premium refunds / premium-revenue-contra   (income statement)
    Cr  Bank — FNB current account                 (balance sheet)

This is **deliberately not automated** here because:
- Posting a journal entry is a CFO-reserved action (RULE #2b, frozen-numbers).
- The exact account (premium-revenue-contra vs a refund-expense line) affects the
  MA/GWP presentation, which is FROZEN and needs the CFO's explicit mapping call.

**Recommendation to develop next (needs CFO sign-off):** add a gated
`post_refund_gl()` that drafts this JE against a CFO-chosen account and posts it
only when `CUSTOMER_REFUND_GL_POST_ENABLED` is on. Until then the bank-rec is
kept honest by the `payments.Payment` record + a manual month-end refund JE.

## Money & PII gates (all preserved)
- FNB live send: OFF (`REFUND_FNB_SEND_ENABLED`). No money moves without the CFO.
- GL posting: reserved (above).
- Account number: encrypted at rest (Fernet); last-4 only in any response/log.
- Inbound handoff: shared-token auth; idempotent.
