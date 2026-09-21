# Extension Request — Graphite V2 ⇄ Omni Payment-Transaction Feed

> Follows the canonical Extension Request Guide (7 fields + validation gates).
> Status: **built, gated, dormant** — awaiting Sanctum token from ADRisk IT.
> Source trigger: Pramod Bisen (ADRisk IT) email *"Incorrect Data on Payment
> Transactions MIS Report"*, 2026-06-11. CFO directive 2026-06-15: link
> Graphite, Omni, and the Reporting module; read-only ingest + report +
> reconcile, **no GL posting**.

---

## 1. Name
Graphite V2 Finance payment-transaction feed → omni mirror + omni report.

## 2. Objective
Give Finance one omni-native view of every Graphite payment transaction
(RealPay / DPO / Orange / manual / VCS / cash), kept current by a scheduled
machine-to-machine pull, so reconciliation no longer depends on the old
report-side filter that dropped rows in the MIS report. Omni becomes a
downstream **consumer** of Graphite; Graphite stays the system of record.

## 3. Scope
**In scope (this build):**
- Read-only HTTP client for Graphite's Finance API (`integrations/graphite_finance.py`).
- Local mirror models: `GraphitePaymentTransaction` + `GraphitePaymentSyncRun`.
- Idempotent pull command `pull_graphite_payments` (month-chunked, cursor sweep).
- Omni report `GET /api/reports/graphite-payments/` + builder + admin.
- Unit tests (HTTP fully mocked; pass with no token).

**Out of scope (v1, by CFO directive):**
- ❌ No GL journal posting from this feed (respects frozen-numbers /
  revenue-format-frozen rules).
- ❌ No write-back to Graphite (the API is read-only / `finance:read`).
- ❌ No auto-matching to `Payment` / `BankStatementLine` yet — the mirror is
  the foundation; reconciliation matching is a fast follow once Finance
  confirms the columns they need.

## 4. Dependencies
- **BLOCKER — Sanctum token.** Endpoint requires a token with ability
  `finance:read`. ADRisk IT mints it via `php artisan finance:mint-erp-token`
  and hands off separately. Until set, the feed is dormant (command exits
  `SKIPPED`, never crashes a cron).
- Env (omni `/etc/alpha-finance/.env`):
  - `GRAPHITE_FINANCE_API_BASE=https://graphite-v2-prod-be.alphadirect.co.bw`
  - `GRAPHITE_FINANCE_API_TOKEN=<sanctum finance:read token>`
  - `GRAPHITE_FINANCE_TIMEOUT_SECONDS=30` (optional)
- These are **separate** from the existing `GRAPHITE_API_*` (salvage
  vehicle-lookup) — different service account, base path, and token.
- Migration `integrations/0002_graphite_payments` (ships in repo; applied by
  the deploy entrypoint's `migrate`).

## 5. Contracts
**Upstream (Graphite V2, fixed — `PaymentTransactionController.php`):**
```
GET /api/v1/finance/payment-transactions
  Auth   : Bearer <sanctum finance:read>
  Query  : date_from, date_to (Y-m-d, required; window < 31 days)
           partner | status | policy_number | reference_number
           | product_id | is_refund | limit (≤1000) | cursor
  Resp   : { data: [ { id, policy_number, reference_number, amount,
                       payment_method, status, is_refund, is_reverse,
                       paid_at, paid_on, recorded_at, updated_at, note,
                       payment_frequency,
                       policy: {id, product_id, plan_id, product_name, plan_name},
                       customer_name, agent_name,
                       dpo: {trans_id, company_ref, token} } ],
           meta: { count, per_page, next_cursor, window, has_more } }
```
`customer_name` / `agent_name` are **DPA-regulated PII** — stored for internal
reconciliation only; never echoed to external responses.

**Downstream (omni, stable for the front-end):**
```
GET /api/reports/graphite-payments/
  ?from=YYYY-MM-DD&to=YYYY-MM-DD&partner=&status=&policy_number=
  &include_refunds=true&limit=&offset=
  → { report, source, filters, summary{total_count,total_amount,
      by_partner[],by_status[]}, rows[], meta{...,freshness{last_synced_at,
      last_run_status,last_window}} }
```

## 6. Success criteria
- Pulling a known 31-day window returns the **full** row set (no MIS-style
  drops); count matches the Graphite Reporting Portal for the same filter.
- Re-running a window is idempotent (no duplicates; status changes update in
  place — verified by `IngestTests.test_ingest_is_idempotent`).
- Omni report `by_partner` / `by_status` totals reconcile to the row list.
- Every sweep leaves an auditable `GraphitePaymentSyncRun` row.
- Command never crashes a cron when unconfigured (exits `SKIPPED`).

## 7. Rollout
1. Merge to `main`; **rebuild** backend image (migration change — never just
   `docker restart`).
2. Add the three env vars to `/etc/alpha-finance/.env` once the token lands.
3. `docker compose --env-file /etc/alpha-finance/.env build backend && up -d`
   (entrypoint runs `migrate` → creates the two tables).
4. Backfill: `python manage.py pull_graphite_payments --from <start> --to <today>`.
5. Schedule a daily catch-up cron: `pull_graphite_payments --days 3`
   (overlap re-pulls recent windows to catch late status flips — see note).
6. Confirm the report at `/api/reports/graphite-payments/` and wire a
   front-end page if Finance wants a UI surface.

---

## Validation gates
- [x] **Builds** — all modules `py_compile` clean.
- [ ] **Tests** — `python manage.py test integrations` (run in CI / on EC2;
      cannot run on the CFO Mac, no local Django env).
- [ ] **Live smoke** — blocked on token; run step 4 with a 1-day window first.
- [x] **No financial side-effects** — grep confirms no `JournalEntry` / GL
      writes in the feed path.

## Known limitation — late status updates
The API windows on `created_at`. A transaction whose **status** later flips
(e.g. Success → Reversed) keeps its original `created_at`, so a forward-only
daily pull won't re-see it unless its window is re-pulled. Mitigation: the
daily cron uses `--days 3` (rolling overlap) and a weekly wider re-sweep can
be added. A future enhancement: ask ADRisk IT to add an `updated_since`
filter for true change-data-capture.

## Files
- `integrations/graphite_finance.py` — client + window chunking + ingest.
- `integrations/models.py` — `GraphitePaymentTransaction`, `GraphitePaymentSyncRun`.
- `integrations/migrations/0002_graphite_payments.py`
- `integrations/management/commands/pull_graphite_payments.py`
- `integrations/admin.py` — admin browse.
- `integrations/tests.py`
- `reporting/graphite_payments.py` — report builder.
- `reporting/views.py` — `GraphitePaymentsView`; `reporting/urls.py` — route.
- `alpha_finance/settings.py` — `GRAPHITE_FINANCE_*` env.
