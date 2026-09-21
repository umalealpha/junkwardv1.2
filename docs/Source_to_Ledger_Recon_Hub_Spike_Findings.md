# Source-to-Ledger Reconciliation Hub — Discovery Spike Findings & Phase 1 Scope

**Status:** Discovery spike only. Read-only. No module created, no DB written, nothing deployed.
**Scope entity:** ADIC only. Summary totals only — no policyholder PII persisted.
**Date:** 2026-07-02
**Awaiting:** CFO sign-off before any module code or prod change.

**Governance constraint (CFO, this session):** Graphite and the Reporting Portal are **read-only — no adjustments**. Any change required on those systems is requested from **Arjun & Pramod** to make live. **Omni (alpha-finance) is full CFO authority** — the entire Reconciliation Hub is built inside omni.

---

## 1. Reporting Portal (reporting.alphadirect.co.bw)

### 1.1 What it is
- **Separate Graphite-family app.** NOT the omni/alpha-finance repo and NOT the Graphitev2 repo. Source is **not on this machine** (machine-wide sweep of `/Users/excowing/work` + `/Volumes/*/work` found no portal repo). It lives on a Graphite production host / the Graphite team's remote.
- Frontend: React + Vite SPA (`/assets/index-*.js`, `/assets/index-*.css`, Montserrat font).
- Backend: JSON API under **`https://reporting.alphadirect.co.bw/api/`** (unversioned — no `/v1/`).
- Auth: **session cookie** (all calls succeed with `credentials:'include'`; no bearer token in the SPA). Logged-in identity via `GET /api/auth/me`.
- Storage: pre-generated report files on disk at **`/opt/adrisk/reports/{report-type}/…`** (DB/app namespace "adrisk"). Country scoping field `country: "BW"`.

### 1.2 Navigation surface
`Dashboard` · `My Dashboard` (dept) · `Custom Builder` (`/reports/custom-builder`) · `Downloads` · `Analytics` · `Users` · `Report Schedules` (`/admin/report-schedules`) · `Dev Logs` · `Settings`.

Logged-in role (Prathap): `role=admin`, `dept=finance`, AI tier `exco`. Report permissions: `age_analysis, claims, custom_builder, mis_compliance, policy_reports`. Admin tools: `system_settings, user_management, report_schedules`.

### 1.3 Canned reports (the "Reports" catalogue — from Downloads report-type filter)
| Report type key | Notes |
|---|---|
| `all-policies` | Book snapshot; 213k rows all-dates |
| `summary-age-analysis` | Premium-debtor ageing, summarised (~3.8k rows) |
| `detailed-age-analysis` | Premium-debtor ageing, line-level (~133k rows) |
| `written-premium` | GWP source; supports FY / YTD ranges |
| `earned-premium` | NEP source |
| `book-of-business` | |
| `transactions` (Payment Transactions) | ~4k rows/day |
| `motor-comprehensive` | |
| `claims-as-on-date` | ~15.6k rows |
| `mis-report` (Dom-Com Premium Bordereaux) | |
| `mis-premium-board` (MIS – WP Board) | |
| `mis-payment-transaction` | |
| `premium-bordereaux` | XLSX |
| `claims-bordereaux` | XLSX |
| `lapsed-cancelled-policies` | XLSX |
| `refund-report` | |
| `customer-report`, `customer-all-kyc` | KYC/customer master |

### 1.4 Custom Builder — tables & columns (`GET /api/custom-reports/config`)
Envelope `{success, message, data:{tables[], operators, aggregates}}`. Operators per type (string/number/date/enum); `Σ` = aggregatable.

- **Policy/policies**: policyNumber, status(enum 0-4), premium`Σ`, annual_premium`Σ`, sum_assured`Σ`, premium_freq(enum 1/2/3/12), billingStartDate, leadSource, quoteNumber, created_at, trans_type
- **Policy/products**: name — **Policy/product_plans**: name, premium`Σ`
- **Customer/customer**: firstName, lastName, cellphone, email, customer_category, created_at
- **Customer/customer_profile**: dob, sourceOfIncome, address, city, province — **Customer/customer_kyc**: compliance(enum), created_at
- **Vehicle/vehicle**: vehiclePlate, make, model, year, estimated_value`Σ`, condition, is_imported — **Vehicle/motor_comp_quotes**: premium_rate`Σ`, estimatedValue`Σ`
- **Payments/payment_transactions**: amount`Σ`, status, paymentDate, referenceNumber, paymentFrequency, is_refund, created_at — **Payments/vcs_new_transactions**: amount, transType, status, created_at
- **Claims/claims**: claim_number, claim_type, status, created_at, updated_at
- **Organisation**: users(firstName,lastName,email), agencies(name), stores(name,address)

> Custom Builder emits PII columns (name, cellphone, email, dob, address). The hub must **only** pull aggregatable numeric columns / totals — never persist identity columns.

### 1.5 Extraction interface (confirmed vs inferred)
**Confirmed GET (observed on the wire):**
- `/api/version` → `{version}`
- `/api/auth/me` → identity + permissions
- `/api/custom-reports/config` → tables/columns/operators/aggregates
- `/api/custom-reports/templates` → saved custom templates (currently empty `[]`)
- `/api/recurring-schedules` → schedule list
- `/api/reports?page=&pageSize=` → generated-report list + download

**Report record shape** (`/api/reports`):
```
{id, title, type, country:"BW", status(completed|failed|processing|pending),
 format(csv|xlsx), filePath:"/opt/adrisk/reports/…", rowCount, filters:{status,segment,productId},
 dateFrom, dateTo, scheduledFor, startedAt, completedAt, userId, scheduleId, requestedBy}
```
**Recurring-schedule shape** (`/api/recurring-schedules`):
```
{id, name, userId, country, reportType, config, format(csv|xlsx),
 cronExpression, rangeMode(all|fy|ytd|mtd), fixedDateFrom, fixedDateTo,
 isEnabled, lastRunAt, nextRunAt}
```
- Formats: **CSV, XLSX**. Scheduling: **cron**, generated overnight, **kept 7 days**. 14 active schedules already exist (incl. Detailed/Summary Age Analysis, Written Premium, Premium/Claims Bordereaux).

**Inferred (not called — POST/side-effecting, left untested per read-only spike):**
- `POST /api/custom-reports/run` (Run Preview), `POST /api/custom-reports/export`, `POST /api/custom-reports/schedule`
- `GET /api/reports/{id}/download` (the per-row Download button)
- `POST /api/recurring-schedules` (create schedule)

### 1.6 Portal dependency / ask to Arjun & Pramod
1. **Service credential** for machine ingestion (read-only API token or a service login) — the SPA uses only a session cookie; omni's cron cannot log in interactively.
2. Confirm the **download endpoint** (`GET /api/reports/{id}/download`) is stable, or expose a small **"latest completed report by type"** endpoint so omni can pull without scraping the paginated list.
3. Because the portal is read-only-no-adjustment, **any new endpoint or schedule is their change to make live**, not ours.

---

## 2. Omni backend (alpha-finance) — attach points

### 2.1 Already exists (reuse, don't rebuild)
- **`reporting/models.py:21` `Reconciliation`** — A-vs-B figure tracker (source_a/source_b value, delta_bwp, delta_pct, severity, status, DeepSeek `ai_cause`/`explanation`). Pairwise; keep for AI narrative, link from the new multi-column run.
- **`reporting/graphite_age_analysis.py` `build_graphite_age_analysis()`** + **`integrations/graphite_age.fetch_age_analysis()`** — reads Graphite ageing from the **read-only RDS replica** (see §3). Exposed at **`GET /api/reports/graphite-age-analysis/`**.
- **`reporting/graphite_payments.py`** + `integrations.GraphitePaymentTransaction` + `pull_graphite_payments` cmd — already mirrors the portal `/reports/transactions` feed.
- **`reporting/reports.py`** GL engine: `build_trial_balance` (191), `build_ar_aging` (927), `_signed_balance` (60), `_agg_je_lines` (45), `_posted_lines` (173), `_ap_gl_balance` (937); AR aging buckets (`_AGING_BUCKETS`, `_aging_bucket` ~830); `build_profit_loss`, `MAProfitLossView` (management-accounts shape).

### 2.2 GL / ledger
- `ledger/models.py:30` **Account** — `code`, `account_type`, `normal_balance_dc`, **`is_receivable`** (indexed, canonical AR flag), `owner_company_id`, `is_summary_only`.
- `ledger/models.py:544` **JournalEntry** (immutable once POSTED; `status`, `entry_date`, `company_id`), `:1236` **JournalEntryLine** (`account`, `contact`, `debit_bwp`/`credit_bwp` functional-currency).
- Posted total for a metric = `_signed_balance()` over `_posted_lines(company_id)` for the mapped account codes + period.

### 2.3 Report/app conventions to match
- Views: `FinancialReportView(APIView)` + `permission_classes=[IsAuthenticated, CanViewFinancials]`; helpers `_parse_company`, `_parse_date`; company scoping via `company_id` (id or code, `None` = rolled-up).
- URLs: legacy path-based under `/api/reports/…` (`reporting/urls.py`); DRF router under `/api/v1/` (`alpha_finance/api_router.py`).
- New app registration: `LOCAL_APPS` in `alpha_finance/settings.py` (~line 40); AppConfig; `makemigrations`. House pattern is **standalone domain apps** (procurement/, fx/, assets/, exceptions/).
- Config/mapping precedent: **`assets/models.py:50` `AssetCategory`** (named FK pointers to `Account`). Audit: **`core/models.py` `AuditableMixin`** — `save(audit_user=…, audit_ip=…, audit_description=…)`.

---

## 3. Graphite ageing source — status

- **De-risked.** The Cloudflare **522 on `graphite.alphadirect.co.bw` is the app UI origin — NOT needed.** Omni already reads Graphite ageing from the **read-only RDS replica** (`graphitebw-rds-ro.alphadirect.co.bw`) via `integrations/graphite_age.fetch_age_analysis()`, surfaced through `build_graphite_age_analysis()` and `/api/reports/graphite-age-analysis/`.
- Net: **no new Graphite integration required** for Phase 1. Reuse the existing replica pipe. Origin uptime is irrelevant to the tie-out.

### Three independent ageing sources available for the tie-out
1. **Reporting portal** — `summary-age-analysis` / `detailed-age-analysis` CSV (policy-admin/adrisk view).
2. **Graphite RDS-RO replica** — via omni `fetch_age_analysis()` (raw Graphite ledger).
3. **Omni GL** — `build_ar_aging()` (from posted invoices/JEs).

---

## 4. Phase 1 build scope (proposed — build on branch `feat/recon-hub-phase1`)

### 4.1 New app `reconciliation_hub/` (omni, standalone — house pattern)

**Models** (all `AuditableMixin`, ADIC-scoped, totals-only):
- **`MetricSourceMap`** — one row per reconciled metric. Fields: `metric_key`, `label`, `gl_accounts` (M2M/CSV → `Account`), `normal_side`, `source_system` (`reporting_portal|graphite_rds|omni_gl`), `source_report_type`, `source_field`, `is_active`. *(Precedent: `AssetCategory`.)*
- **`SourceFigure`** — extracted external totals. Fields: `company` (ADIC), `period_label`, `metric_key`, `source_system`, `source_value`, `row_count`, `source_ref` (filePath/schedule id), `extracted_at`. **No PII rows — totals + counts only.**
- **`ReconciliationRun`** (header) + **`ReconciliationLine`** (rows: `metric`, `source_total`, `omni_posted`, `variance`, `variance_pct`, `status` ∈ {matched, within_tolerance, breach}, `note`, optional FK → existing `Reconciliation` for AI narrative).
- **`AgeingTieOut`** — per bucket: `portal_total`, `graphite_total`, `omni_total`, pairwise variances, `status`.

### 4.2 Ingestion (read-only — never writes Graphite/portal)
- **`pull_reporting_extracts`** management command: authenticates to portal API with the **service token from Arjun/Pramod**, downloads latest completed `written-premium`, `earned-premium`, `summary/detailed-age-analysis`, `premium-bordereaux` files, parses **totals only** → `SourceFigure` / `AgeingTieOut`. ADIC-scoped via portal `filters` (segment/productId — see open decision §5).
- **Graphite ageing**: reuse `fetch_age_analysis()` (RDS-RO). No new access.
- **Omni posted**: call existing `build_trial_balance` / `build_ar_aging` / MA-P&L builders per period.

### 4.3 GL-mapping approach
`MetricSourceMap` maps each metric (GWP, NEP, Premium Debtors, Claims Paid/Incurred, Commission, VAT Output, Net Collection) → omni GL account code(s) + normal side. Posted = `_signed_balance()` over posted JE lines, ADIC company + period. Seed via `setup_recon_metric_map` cmd (idempotent); editable by CFO/finance in admin.

### 4.4 API (omni; `FinancialReportView` + `CanViewFinancials`)
- `GET  /api/v1/recon/dashboard/?period=` — control rows: `metric | source_total | omni_posted | variance | variance_pct | status`
- `GET  /api/v1/recon/ageing/?as_of=` — buckets: portal vs graphite vs omni + variance flags
- `POST /api/v1/recon/run/` — trigger a run for a period (finance/CFO only)
- `GET  /api/v1/recon/runs/{id}/`
- `GET|POST|PATCH /api/v1/recon/metric-map/` — admin GL mapping

### 4.5 CFO control dashboard (frontend `/reconciliation`)
- **Bridge table**: rows = metric | source total | posted in omni | variance | status chip (green matched / amber tolerance / red breach); drill each row → GL detail / source report.
- **Ageing panel**: report-vs-Graphite-vs-omni buckets side by side, variance highlighted; drill to the three source lists.
- Period selector (FY / month), ADIC-locked, refresh + "run reconciliation" (gated).
- Follows Alpha Direct Design System (navy #0D1B2A / orange #F4A623, Book Antiqua per finance brand).

### 4.6 Test plan
- **Unit**: metric→posted computation (`_signed_balance` over mapped accounts); variance & tolerance logic; ageing-bucket alignment across the 3 sources; ADIC company scoping.
- **Integration**: fixture portal CSV + fixture GL → expected bridge rows; `build_ar_aging` grand total == sum of buckets; idempotent re-run (no dup `SourceFigure`).
- **Permissions**: `CanViewFinancials` gate on every endpoint; run/mutation restricted to finance/CFO.
- **No-PII assertion**: persisted `SourceFigure`/`AgeingTieOut` rows contain only numeric totals + counts + refs — test fails if any identity column is stored.
- **Portal-offline degradation**: ingestion failure logs + surfaces "stale/unavailable", never blocks omni.

---

## 5. Decisions — SIGNED OFF (CFO, 2026-07-03)
1. **Entity scope: ADIC INCLUDING Instant.** Combine ADIC (16244) + Alpha Direct Instant Insurance (24936) into one reconciliation — GWP, Premium Debtors, Claims, Policy count all cover both. *Flag: source GWP will exceed the ADIC-standalone frozen 125.15M; acceptable because this is a Graphite↔GL tie-out, not the MA number — the omni GL side must also include Instant postings for the tie to balance.*
2. **Metrics (Phase 1):** **GWP · Premium Debtors (ageing) · Claims · Policy count (all-policies).** (NEP/Commission/VAT/Net-Collection deferred.)
3. **Variance tolerance: 5%** (green ≤5%, red >5%).
4. **Cadence (Claude's call, approved to decide):** nightly recon ~05:00 after source overnight crons settle → fresh for 09:00; plus on-demand "Run now" button.
5. **External dependency: NONE for Phase 1.** Source = **Graphite RDS-RO replica (already wired in omni)**; posted = **omni GL**. Two-way tie-out sits entirely inside omni (CFO authority). Reporting-portal file pull (needs Arjun/Pramod service token) is **deferred to Phase 2** as an optional 3rd cross-check.

### Phase 1 revised shape (per decisions)
- **2-way tie-out**, no portal, no new Graphite work.
- Metric bridge rows: GWP / Premium Debtors / Claims / Policy count → `source_total` (Graphite RDS-RO or expected-figures register) vs `omni_posted` (GL) vs `variance` vs `status` (5% band).
- ADIC scope = both legal entities combined; company filter must union 16244 + 24936 on the omni side.
- `SourceFigure.source_system` Phase 1 = `graphite_rds` only; `reporting_portal` reserved for Phase 2.

---

## 6. What I did NOT do
No module created, no migrations, no DB writes, no prod deploy, no POST to the portal, no Graphite origin calls, no secrets printed. Read-only spike only.
