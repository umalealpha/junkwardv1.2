# Supplier Payables Reconciliation (`supplier_recon`)

Monthly, per-supplier board showing **who was paid, who was not, and why** —
where every unpaid or held bill must carry a reason code and a written
justification before the month can be signed off, and overdue or flagged bills
raise an escalation.

Origin: spec + first draft delivered by Bharath Balasubramanian (2026-07-25),
rewritten against the live schema. See *What changed from the delivered draft*
below.

## Where it lives

| Piece | Path |
|---|---|
| Models / engine | `supplier_recon/models.py`, `services.py` |
| API | `supplier_recon/api_views.py` → `/api/v1/supplier-recon/…` |
| Access control | `supplier_recon/permissions.py` |
| Page | `frontend/src/app/(dashboard)/payables/recon/page.tsx` → `/payables/recon` |
| API client | `frontend/src/lib/api.ts` (`getSupplierRecon*`, `SupplierRecon*`) |
| Tests | `supplier_recon/tests/test_supplier_recon.py` (61) |

## The rules

1. **Any bill not fully paid needs a reason code AND a written justification**
   (≥ 20 characters — "n/a" is rejected). Enforced in
   `InvoiceReconItem.clean()`, again in `action_item()`, and again at finalise.
2. **Every bill must be actioned** by payables. Unactioned bills block sign-off
   and show red.
3. **Escalation** is auto-raised when a bill is overdue, or its reason code is
   flagged `requires_escalation`. A month cannot close with an
   escalation-worthy bill that has no escalation on record.
4. **Salvage rule honoured** — `SALVAGE_NOT_IN_YARD` is escalation-worthy
   (CFO standing rule: salvage in the yard before settlement).
5. **Segregation of duties** — whoever built the run cannot sign it off.
6. **Reopening a signed-off month needs a written reason** and is audited.
7. **This module moves no money and posts no GL.** Payment execution stays in
   `payments` and the FNB flow. It observes and explains.

## The 3-way match

`billing.Invoice` ↔ `procurement.PurchaseOrder` / `GoodsReceiptNote` ↔ claim
authorisation. Evaluated worst-first, so the status names the one thing to fix:

| Status | Meaning |
|---|---|
| `no_po` | No purchase order — no authority to spend |
| `no_claim` | Claim supplier, PO has no `related_claim_reference` |
| `no_grn` | No **posted** goods receipt — nothing proves it arrived |
| `partial` | `POBillMatch` recorded a price/quantity variance |
| `matched` | All three legs present, no variance |

The claim leg reads `PurchaseOrder.related_claim_reference` — omni holds a
reference because the master claims register lives in Graphite.

## Access

| Level | Who | Can |
|---|---|---|
| read | anyone with `UserProfile.can_view_financials` | see the board |
| prepare | CFO, FC, FM, Accountant, Senior Accountant, Bookkeeper, Finance Analyst | build a month, action a bill, escalate |
| review | CFO, FC, FM only | finalise, reopen, resolve an escalation |

Title-based (`core.models.UserProfile.Title`), **not** Django auth Groups —
groups are not maintained in this deployment. Every queryset is scoped by
`core.models.allowed_company_ids`, so a run cannot be read across legal
entities by guessing its id.

## Rollout

1. Deploy (migrations auto-run; `0002` seeds 14 reason codes — no `loaddata`).
2. `python manage.py classify_recon_suppliers --dry-run` then without the flag.
   Seeds profiles from real PO history, biased conservatively to `general`.
3. Payables refines panel beater / parts / towing / assessor / glass / medical
   in Django admin (bulk-editable) or via `supplier-profiles/`.
4. Open `/payables/recon`, pick a month, **Build month**.
5. Optional: schedule `python manage.py build_supplier_recon` daily so the board
   is always current. Finalised months are skipped, never rebuilt.

## Reconciling to AP Aging

Deliberately built to agree with the existing `/payables/aging`:

* same open statuses (`posted`, `partially_paid`, `overdue`) and the same
  `issue_date <= as_of` cut;
* same ageing bands (`current` / `31-60` / `61-90` / `91-120` / `over_120`) —
  `InvoiceReconItem.ageing_bucket` is tested against
  `reporting.reports._aging_bucket` directly.

Differences, both intentional:

* the recon also pulls bills **settled during the month** (the board has to show
  who *was* paid), and bills already `paid`;
* paid amounts are re-derived from `PaymentAllocation` **as at the period end**
  rather than read from `Invoice.amount_paid` (which is "as at now"). A July
  board is not flattered by an August payment.
* roll-ups are BWP at the bill's locked `exchange_rate`; the transaction
  currency and face amount stay on the item so an FX bill is never silently
  added to a Pula total.

## What changed from the delivered draft

The draft was written off-host against a description of omni, not the code. Its
own README flagged the adapters as unverified. Every one was wrong:

| Draft assumed | Reality | Effect if shipped |
|---|---|---|
| `Invoice.total_bwp` | `total_amount` | **Every supplier shows P0.00, silently** |
| `Invoice.invoice_date` | `issue_date` | `FieldError` on build |
| `PaymentAllocation.amount_bwp` | `amount_allocated` | `FieldError` on build |
| `Payment.value_date` | `payment_date` | swallowed by a bare `except` — no as-at cut |
| `POBillMatch.invoice` | `bill` | `FieldError` |
| `procurement.GRN` | `GoodsReceiptNote` | app will not migrate |
| `claims.Salvage` for the claim leg | deprecated table; claim ref is on the PO | every claims bill flagged `no_claim` |
| `AuditLog(actor=, model=, object_id=, detail=)` | `user`, `table_name`, `record_id`, `description` | audit silently wrote **nothing** (bare `except: pass`) |
| Django auth Groups for RBAC | title-based `UserProfile` | everyone except superusers locked out |
| `models.Model`, int pks | `BaseModel`, UUID pks | off-convention, no audit |

Also fixed: a `@action`-decorator name shadowing that crashed
`views.py` at import; money summed as `float`; no entity scoping (any
authenticated user could read every supplier bill in every entity); the
frontend page under `frontend/app/` instead of
`frontend/src/app/(dashboard)/` (no sidebar, no auth wrapper); no
segregation of duties; reopen with no reason; and a population that only
pulled bills *issued* in the month — so an unpaid March bill never appeared on
the July board.

Kept from the draft: the concept, the reason-code taxonomy, the escalation
model, the run/line/item shape, and the finalise-blocking rules. That thinking
was sound and is what makes this worth having.

## Known limits

* Reason-code *taxonomy* is seeded; the exact **supplier categories** need a
  human pass (step 3 above) before `no_claim` flags are trustworthy.
* The provision/ageing view is a **report** — it posts no journal.
* `blocking_exceptions()` walks items in Python. Fine at ADIC's bill volume
  (hundreds/month); revisit if a month ever exceeds a few thousand bills.
