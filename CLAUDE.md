# Alpha Direct Financial Management System — Claude Code Context

## What this project is

A full-stack financial management system for **Alpha Direct Insurance**, a Botswana-based insurance company. It handles premium billing, claims payments, broker commissions, bank reconciliation, financial reporting, and integration with their policy administration system (Graphite).

**Company context:** Botswana, currency BWP (Botswana Pula), tax authority BURS (Botswana Unified Revenue Service), VAT standard rate 14%, withholding tax (WHT) 10% on broker commissions above P48,000/year threshold, fiscal year July–June.

---

## Tech stack

| Layer | Technology |
|---|---|
| Backend | Python 3.13, Django 5.2, Django REST Framework 3.16 |
| Database | PostgreSQL (DB: `alpha_finance`, user: `alpha_admin`) |
| Frontend | Next.js 15, React 19, TypeScript, Tailwind CSS v4 |
| UI components | shadcn/ui pattern (custom), Radix UI primitives, Recharts |
| PDF generation | ReportLab 4.4.10 |
| Auth | DRF Token auth + Django session auth |
| Node.js | v22.11.0 portable at `C:\Users\OTHER\AppData\Local\node\node-v22.11.0-win-x64` |

---

## How to run the system

**Terminal 1 — Django backend:**
```cmd
cd C:\Users\OTHER\alpha_finance_v2
venv\Scripts\activate
python manage.py runserver
```
Backend runs at: http://localhost:8000
Admin panel: http://localhost:8000/admin/ (user: admin, password: admin123)
API root: http://localhost:8000/api/v1/
Token auth: POST http://localhost:8000/api-token-auth/

**Terminal 2 — Next.js frontend:**
```cmd
cd C:\Users\OTHER\alpha_finance_v2\frontend
set PATH=C:\Users\OTHER\AppData\Local\node\node-v22.11.0-win-x64;%PATH%
npm run dev
```
Frontend runs at: http://localhost:3001 (3000 may be taken by another process)

**Important:** The `dev` script does NOT use `--turbopack` (removed due to Radix UI incompatibility causing "module factory not available" errors on banking page).

---

## Django project structure

```
alpha_finance/          ← project root
├── alpha_finance/      ← Django settings package
│   ├── settings.py
│   ├── urls.py
│   └── api_router.py   ← central DRF router for /api/v1/
├── core/               ← users, currencies, exchange rates, tax rates
├── ledger/             ← chart of accounts, journal entries, fiscal periods
├── billing/            ← contacts, invoices, invoice lines, PDF generation
│   ├── pdf.py          ← ReportLab A4 invoice PDF (navy/orange branding)
│   └── views.py        ← InvoicePDFView: GET /api/v1/invoices/{id}/pdf/
├── payments/           ← payments, allocations, withholding tax
├── banking/            ← bank accounts, statements, reconciliation
├── reporting/          ← 11 financial report views (7 original + 4 Phase 1)
├── budgets/            ← budget management (Budget, BudgetLine models)
├── integrations/       ← Graphite event integration
│   ├── models.py       ← IntegrationEvent model
│   ├── services.py     ← EventProcessor (turns events into invoices)
│   ├── api_views.py    ← POST /api/v1/events/ + retry action
│   └── management/commands/simulate_graphite_events.py
├── manage.py
└── frontend/           ← Next.js app
```

---

## All API endpoints

```
/api-token-auth/                    POST  — get auth token (username/password)

/api/v1/currencies/                 GET, POST
/api/v1/exchange-rates/             GET, POST
/api/v1/tax-rates/                  GET, POST

/api/v1/accounts/                   GET, POST
/api/v1/fiscal-periods/             GET, POST
/api/v1/journal-entries/            GET, POST
/api/v1/journal-entries/{id}/       GET

/api/v1/contacts/                   GET, POST
/api/v1/invoices/                   GET, POST
/api/v1/invoices/{id}/              GET
/api/v1/invoices/{id}/post/         POST  — post a draft invoice
/api/v1/invoices/{id}/pdf/          GET   — download invoice as PDF

/api/v1/payments/                   GET, POST
/api/v1/payments/{id}/confirm/      POST  — confirm a draft payment
/api/v1/payments/{id}/allocate/     POST  — allocate to invoice
/api/v1/payment-allocations/        GET

/api/v1/bank-accounts/              GET, POST
/api/v1/bank-statements/            GET, POST
/api/v1/bank-statements/{id}/run-matching/  POST — auto-match statement lines
/api/v1/bank-statement-lines/       GET
/api/v1/bank-statement-lines/{id}/match/    POST — manual match to payment

/api/v1/events/                     GET, POST — integration events
/api/v1/events/{id}/retry/          POST  — retry a failed event

/api/v1/budgets/                    GET, POST — budget management
/api/v1/budgets/{id}/               GET, PUT, PATCH
/api/v1/budgets/{id}/approve/       POST  — approve a budget
/api/v1/budgets/{id}/add-line/      POST  — add a budget line item

/api/v1/journal-entries/batch-upload/  POST  — CSV batch upload of journal entries

/api/v1/reports/trial-balance/      GET  ?from=&to=
/api/v1/reports/profit-loss/        GET  ?from=&to=
/api/v1/reports/balance-sheet/      GET  ?as_of=
/api/v1/reports/ar-aging/           GET  ?as_of=
/api/v1/reports/ap-aging/           GET  ?as_of=
/api/v1/reports/cash-position/      GET
/api/v1/reports/general-ledger/     GET  ?account=&from=&to=
/api/v1/reports/budget-vs-actual/   GET  ?from=&to=&department=
/api/v1/reports/vat-return/         GET  ?from=&to=
/api/v1/reports/expense-analysis/   GET  ?from=&to=
/api/v1/reports/management-pack/    GET  ?from=&to=
```

---

## Frontend pages

All pages are under `frontend/src/app/(dashboard)/` using Next.js App Router.

| Route | Page |
|---|---|
| `/` | Dashboard — CFO stats, bar chart, cash position, recent transactions |
| `/accounts` | Chart of accounts tree view + GL detail drill-down |
| `/invoices` | Invoice list (tabs: All/Draft/Posted/Paid/Overdue) |
| `/invoices/new` | Create invoice form with line items |
| `/invoices/[id]` | Invoice detail + Post button + payment history |
| `/contacts` | Contact list with type filter |
| `/contacts/[id]` | Contact detail + invoice/payment history |
| `/payments` | Payment list |
| `/payments/new` | Record payment form |
| `/payments/[id]` | Payment detail + allocations |
| `/banking` | Bank reconciliation workspace |
| `/reports` | Report hub |
| `/reports/trial-balance` | Trial balance |
| `/reports/profit-loss` | P&L with Recharts bar chart |
| `/reports/balance-sheet` | Balance sheet |
| `/reports/ar-aging` | AR aging by age bucket |
| `/reports/ap-aging` | AP aging |
| `/reports/cash-position` | Cash position with Recharts pie chart |
| `/reports/budget-vs-actual` | Budget vs actual variance report by department |
| `/reports/vat-return` | VAT return preparation (output/input/net for BURS filing) |
| `/reports/expense-analysis` | Expense breakdown by category with period comparison |
| `/reports/management-pack` | Monthly EXCO management accounts package with insurance KPIs |
| `/tax-calendar` | BURS & NBFIRA regulatory deadline calendar |
| `/quick-entry` | 3-tab quick entry: Customer Payment / Vendor Payment / Quick Expense |
| `/integrations` | Integration event log + retry + send test event |
| `/login` | Token auth login page |

**Auth:** Token stored in `localStorage` as `alpha_token`. All pages check for token and redirect to `/login` if missing.

---

## Key models

### IntegrationEvent (integrations/models.py)
Stores events from Graphite (policy system). EventProcessor in `services.py` turns them into invoices:
- `policy_issued` → `Invoice(CUSTOMER_INVOICE)` against account 4100
- `claim_approved` → `Invoice(VENDOR_BILL)` against account 5100/5110
- `commission_calculated` → `Invoice(VENDOR_BILL)` against account 5400
- `policy_cancelled` → `Invoice(CREDIT_NOTE)` against account 4100

### Invoice (billing/models.py)
- Auto-numbered: `INV-{year}-{6digits}`, `BILL-{year}-{6digits}`, `CN-{year}-{6digits}`
- Posting auto-creates a balanced journal entry
- `save(audit_user=user)` required — uses `AuditableMixin`

### JournalEntry (ledger/models.py)
- Immutable once posted
- Must balance (sum debits = sum credits)
- Auto-numbered: `JE-{year}-{6digits}`

---

## Critical implementation details

### Tax rate codes in the database
The actual codes are: `VAT_ZERO`, `VAT_EXEMPT`, `VAT_STD` (not VAT0/EXEMPT/VAT14).
EventProcessor uses: `_tax_rate('VAT_ZERO')`, `_tax_rate('VAT_STD')`, `_tax_rate('VAT_EXEMPT')`.

### System user for integration records
All invoices require a `created_by` FK. For Graphite-generated records, the `_system_user()` helper returns the first superuser. All Invoice creates must pass `created_by=sys_user` and call `save(audit_user=sys_user)`.

### AuditableMixin
The billing models use `save(audit_user=user)` not plain `save()`. Forgetting `audit_user` causes an `IntegrityError` on `created_by_id`.

### WHT calculation
- Applies to broker payments (contact_type='broker')
- Rate: 10%
- Threshold: P48,000 cumulative in current tax year (July 1 – June 30)
- Creates a `WithholdingTaxRecord`
- Journal: Debit 2130 (broker payable), Credit bank (net), Credit 2170 (WHT payable)

### GL account codes used in auto-posting
| Code | Name | Used for |
|---|---|---|
| 1110 | FNB BWP operating | default bank in payments |
| 1210 | Premium receivable | customer invoice DR |
| 1250 | VAT input receivable | vendor bill VAT DR |
| 2130 | Broker commission payable | broker bill CR |
| 2140 | Vendor payable | vendor bill CR |
| 2170 | WHT payable | WHT on broker payments |
| 2180 | VAT output payable | customer invoice VAT CR |
| 4100 | Gross written premium | policy revenue |
| 5100 | Claims incurred — gross | claims expense |
| 5400 | Commission expense — broker | broker commission |

---

## Management commands

```bash
python manage.py setup_initial_data          # currencies, tax rates, admin user
python manage.py setup_chart_of_accounts     # full CoA + fiscal periods
python manage.py create_test_invoices        # 3 test invoices (customer, vendor, broker)
python manage.py create_test_payments        # payments against test invoices + WHT
python manage.py import_test_statement       # sample FNB CSV import + auto-matching
python manage.py run_all_reports             # print all 7 reports to console
python manage.py simulate_graphite_events    # 5 Graphite events → invoices
```

---

## Git history (all 9 commits)

```
03337e8  Graphite integration, invoice PDF, and UI polish
0c1d954  React frontend - all pages, API integration, basic UI
8eae143  REST API - all endpoints with serializers, filters, and actions
69ede6a  Reporting module - trial balance, P&L, balance sheet, aging, cash position
ea2ae58  Banking module - statement import, reconciliation engine, auto-matching
4b0d6f8  Payments module - payment recording, WHT, invoice allocation
402c2ac  Billing module - contacts, invoices, auto journal posting
eb79529  Initial Django project setup - Django scaffold
416f09b  Initial Django project setup for Alpha Direct Financial Management System
```

---

## Known quirks and environment notes

- **Windows PowerShell execution policy:** `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser` needed to run npm scripts.
- **Node.js path in cmd:** `set PATH=C:\Users\OTHER\AppData\Local\node\node-v22.11.0-win-x64;%PATH%` before running npm.
- **Turbopack disabled:** `npm run dev` runs webpack (not turbopack) — Radix UI `@radix-ui/react-dialog` causes "module factory not available" errors with Turbopack.
- **Windows encoding:** Management command output uses ASCII only (no unicode box-drawing chars) — Windows cp1252 terminal can't encode them.
- **Port conflict:** Frontend may start on 3001 instead of 3000 if another process holds 3000.
- **Admin password:** `admin` / `admin123`

---

## AI vendor exception — DeepSeek for non-sensitive reasoning (CFO-authored, 2026-05-09)

**Default policy** (AD-POL-AI-GOV-001): Anthropic-only. Google AI explicitly excluded.

**Exception granted by the CFO on 2026-05-09 for `alpha-finance` only**:
DeepSeek may be used for *reasoning* tasks where no sensitive data leaves the system.
Approved use cases:

- Chart-of-accounts suggestions on free-text journal-entry descriptions
- (Future) anomaly detection over aggregated, anonymised figures
- (Future) classification hints, e.g. "is this expense capex vs opex?"

**Hard rules**:

- DeepSeek key lives in env var `DEEPSEEK_API_KEY`. Never hardcoded.
- Every prompt routes through `core.ai_assist.is_safe_for_ai()` first.
  The filter scrubs national IDs, phone numbers, emails, bank-account-shaped
  digit runs, and decimal monetary amounts BEFORE send. If the input is
  >50% redacted patterns the call is refused outright.
- Never send: customer / employee names, ID numbers, salary figures,
  claim amounts, policy numbers, contact details.
- Anthropic remains primary for document AI / OCR (`documents/services.py`).

**Implementation**:

- `core/ai_assist.py` — DeepSeek HTTP client + safety filter + high-level
  helpers (`suggest_je_accounts`).
- `POST /api/v1/ai/suggest-accounts/  {"description": "..."}` — returns up
  to 3 CoA suggestions with reasons. Returns empty list (silent fallback)
  if DeepSeek is unconfigured or unreachable — never blocks a JE flow.

**Settings**:
```
DEEPSEEK_API_KEY  = config('DEEPSEEK_API_KEY',  default='')
DEEPSEEK_API_BASE = config('DEEPSEEK_API_BASE', default='https://api.deepseek.com')
DEEPSEEK_MODEL    = config('DEEPSEEK_MODEL',    default='deepseek-chat')
```

If this exception is revoked, set `DEEPSEEK_API_KEY=''` in the environment;
the suggest-accounts endpoint will return empty suggestions and nothing
breaks.

---

## Journal Entry Approval Workflow — RBAC (CFO-authored, May 2026)

**Treat as CFO-authoritative — changes require CFO approval, not vendor sign-off.**

Every manual journal entry now flows through a maker / checker workflow with
segregation of duties (SoD) enforced at the model layer.

### Status transitions

```
DRAFT  ──submit──▶  PENDING_APPROVAL  ──approve──▶  POSTED  ──reverse──▶  REVERSED
   ▲                       │
   │                       ▼
   └──reopen──── REJECTED  ◀──reject (with reason)
```

- `submit_for_approval(user)` — DRAFT → PENDING_APPROVAL. Validates balance,
  fiscal period, and lines as if posting, so we never park an unbalanced entry.
- `approve(user)` — PENDING_APPROVAL → POSTED. Enforces (a) approver title is
  CFO / Finance Manager / Financial Controller, and (b) approver is not the
  creator (segregation of duties). Posts to GL in the same transaction.
- `reject(user, reason)` — PENDING_APPROVAL → REJECTED. Same authority + SoD
  rules; mandatory reason recorded.
- `reopen_after_rejection(user)` — REJECTED → DRAFT. Only the creator may reopen.
- `post(user)` — direct DRAFT → POSTED. Permitted only for system / API users
  (UserProfile.title = system_api) and Django superusers. Used by integration
  events from Graphite. Manual users get a 400 if they try.

### Permission model (UserProfile)

Two orthogonal axes:

- **role** (legacy): finance_admin / accountant / finance_reviewer / etc.
- **title** (new): job title that drives approval permission

Approval-eligible titles:
- `cfo` — Chief Financial Officer
- `finance_manager` — Finance Manager
- `financial_controller` — Financial Controller

Administrator authority (manage users, assign roles/titles):
- explicit `is_administrator=True`, OR
- title = `cfo` (automatic), OR
- Django superuser

The CFO is the absolute authority on this system. Even the CFO cannot approve
their own journal entries — SoD is enforced regardless of seniority.

### API endpoints

```
POST  /api/v1/journal-entries/{id}/submit/       maker  → DRAFT → PENDING_APPROVAL
POST  /api/v1/journal-entries/{id}/approve/      checker → PENDING_APPROVAL → POSTED
POST  /api/v1/journal-entries/{id}/reject/       checker, with reason
POST  /api/v1/journal-entries/{id}/reopen/       creator only, REJECTED → DRAFT
POST  /api/v1/journal-entries/{id}/post/         system/superuser direct post

GET   /api/v1/user-profiles/                     list (any authenticated user)
GET   /api/v1/user-profiles/me/                  current user's profile
POST  /api/v1/user-profiles/                     admin only — create user + profile
PATCH /api/v1/user-profiles/{id}/                admin only — title/role/admin/active
DELETE /api/v1/user-profiles/{id}/               admin only — soft-delete (deactivate)
```

### Frontend

- `/journal-entries/[id]` — Submit / Approve / Reject / Reopen buttons,
  workflow status banners, audit trail (created_by, submitted_by, approved_by)
- `/journal-entries` — status filter includes Pending Approval and Rejected
- `/settings/users` — admin-only user-management page (title, role, admin flag,
  password reset, deactivate)
- Sidebar **Administration** group is hidden for non-admins (gated on
  `getMe().can_administer_users`)

### Bootstrap

`python manage.py setup_initial_data` creates the initial `admin` user with
`title=CFO` and `is_administrator=True`. On upgrade from older deployments,
the same command backfills these fields onto the existing admin user.

---

## Procurement Module — Phase 2 (CFO-authored, May 2026)

The `procurement/` and `fx/` Django apps and the `ledger/period_close.py` workflow are **CFO-authored, CFO-approved** — direct work by Prathap Ganesharajah, not the contracted dev team. Changes require CFO approval, not vendor sign-off.

### Why purchase orders exist
A PO is a legal commitment to a supplier raised BEFORE goods/services are delivered. It serves three roles:
1. **Authorisation** — every spend is pre-approved by FM and CFO before being committed.
2. **Audit trail** — the PR → PO → GRN → Bill → Payment chain is the document chain auditors and NBFIRA reviewers ask for.
3. **3-way match** — when the supplier's bill arrives, finance verifies PO ↔ GRN ↔ Bill. Any mismatch (qty, price, no delivery) is flagged before payment.

For a claims PO (assessor, panel-beater, recovery agent, salvage operator) the same chain protects the indemnity-spend leg of the loss ratio. Claims POs carry an optional `related_claim_reference` (Graphite claim number).

### Models (`procurement/models.py`)
- `PurchaseOrder`      — header, with `Department` (**Admin / Claims / HR only** — see organisational rule below), multi-currency, `fiscal_period` lock
- `PurchaseOrderLine`  — one line per item, `account` FK drives where receipt hits the GL
- `GoodsReceiptNote`   — receipt header
- `GoodsReceiptNoteLine` — receipt against a specific PO line, with condition (good/damaged/rejected)
- `POBillMatch`        — 3-way match record, captures quantity and price variance, supports CFO override

### Approval (services.py — joint CFO + FM)
- `submit_for_approval` → DRAFT → PENDING_FM_APPROVAL (locks fiscal period, requires justification > P5,000)
- `fm_approve` → PENDING_FM_APPROVAL → PENDING_CFO_APPROVAL (requires title in {Finance Manager, Financial Controller, CFO}; segregation of duties — cannot approve own PO)
- `cfo_approve` → PENDING_CFO_APPROVAL → APPROVED (CFO title only; cannot be same person as FM approver or creator)
- `reject(reason)` and `cancel(reason)` — cancellation is CFO-only and blocked once any goods received
- `post_grn` posts JE: DR expense/asset accounts (per line), CR `2145 Goods Received Not Invoiced`
- `match_bill_to_po` clears GR-IR when the supplier's bill is later matched (header-level v1; line-level matching is a follow-up)

### FX revaluation (`fx/services.py`)
For each foreign-currency balance-sheet account at period end:
- compute `revalued_bwp = foreign_balance × closing_rate`
- difference vs `book_bwp` is posted as gain/loss to **6950 Foreign exchange gain/loss**
- one revaluation per (period, company); reverse before re-running
- `dry_run=True` builds lines without posting the JE

### Period close (`ledger/period_close.py`)
A period cannot move OPEN → CLOSED while any of these are outstanding:
- DRAFT or PENDING_APPROVAL journal entries dated in the period
- Active POs raised in the period (DRAFT, pending approval, APPROVED, partially received, FULLY_RECEIVED-but-unbilled)
- Foreign-currency balances exist but no FX revaluation has been POSTED for the period

CLI: `python manage.py close_period 2026-04 [--dry-run]`. Only the CFO (or Django superuser) can close.

### Demo
`python manage.py seed_demo_po` creates a USD-denominated PO with two lines (Annual SaaS + Implementation hours) so the full flow can be clicked through without sample data prep.

### CoA addition
- `2145 Goods Received Not Invoiced` (current liability, sub of 2100 Payables) — clearing account for the gap between receipt and bill.

### API
```
/api/v1/purchase-orders/                       GET, POST
/api/v1/purchase-orders/{id}/                  GET
/api/v1/purchase-orders/{id}/submit/           POST
/api/v1/purchase-orders/{id}/fm-approve/       POST
/api/v1/purchase-orders/{id}/cfo-approve/      POST
/api/v1/purchase-orders/{id}/reject/           POST  {reason}
/api/v1/purchase-orders/{id}/cancel/           POST  {reason}
/api/v1/goods-receipt-notes/                   GET, POST
/api/v1/goods-receipt-notes/{id}/post_grn/     POST
/api/v1/po-bill-matches/                       GET
/api/v1/po-bill-matches/create-match/          POST  {purchase_order, bill, override_reason?}
/api/v1/fx-revaluations/                       GET
/api/v1/fx-revaluations/run/                   POST  {period_id, company_id?, dry_run?}
```

### Frontend pages
- `/purchase-orders` — list with search, status & department filters
- `/purchase-orders/new` — create form
- `/purchase-orders/[id]` — detail with action buttons (Submit / FM Approve / CFO Approve / Reject / Cancel / Receive goods / Match bill)
- `/fx-revaluation` — run dry-run or post a period revaluation; history table

### Vendor Bank Account control (maker-checker, CFO-mandated)
Outbound payments to vendors / brokers / reinsurers must hit a banking record that has been independently verified by Finance, NOT by the same person who entered it. The classic vendor-payment fraud pattern is "change a real vendor's bank to attacker's account, hijack one big payment" — the maker-checker workflow stops that.

Model: `procurement.VendorBankAccount` with status:
`DRAFT → submit → PENDING_APPROVAL → approve → ACTIVE` (or `REJECTED`); `ACTIVE → retire → RETIRED`.

Rules enforced at the service layer (`procurement/services.py`):
- Approver must hold title `Finance Manager`, `Financial Controller`, or `CFO`.
- Approver MUST be a different user from the **creator** AND the **submitter** (segregation of duties — both checked).
- Once `ACTIVE`, the row is **immutable** via a `save()`-level guard; banking changes require **retire + new draft → re-approval**.
- Only `ACTIVE` accounts may be selected as the destination on outbound `Payment` (Payment service-layer integration to be wired in a follow-up).
- Unique constraint: no two `ACTIVE` rows for the same vendor with the same account number.
- Unique constraint: only one `ACTIVE` default per (vendor, currency).

Frontend: `/vendor-banking` shows the register with status filters; Detail modal exposes `Submit`, `Approve`, `Reject`, and `Retire` (gated by SoD + title). A `name_mismatch` flag warns Finance when the bank-side holder name differs from the vendor name — a strong fraud-cue.

Audit trail: every workflow transition writes to `core.AuditLog` with the masked account number; the `replaces` self-FK on `VendorBankAccount` chains a new ACTIVE record to the prior one it replaced.

### Who raises POs (organisational rule, CFO-mandated)
Purchase orders are raised **only** by the **Admin**, **Claims**, and **Human Resources** departments. Finance does **not** raise POs — Finance (the Finance Manager and CFO) is the verifier and approver via the FM + CFO workflow. The `Department` enum is restricted to those three values; do not re-introduce Operations / Finance / IT / Other without explicit CFO sign-off.

### Auto-match + tiered variance approval (CFO-mandated)
Every vendor bill MUST reference an approved Purchase Order — `Invoice.post()` refuses to post a `vendor_bill` whose `purchase_order` field is null, whose PO is not yet approved, whose PO supplier ≠ bill contact, or whose issue date pre-dates PO approval. As soon as the bill posts, `auto_match_on_bill_post()` runs the deterministic 3-way match and a DeepSeek second-pass — no human kick-off needed.

**Variance routing (POBillMatch.match_status):**
| Bill vs PO | Status | Who clears it |
|---|---|---|
| `bill ≤ po` | `MATCHED` | nobody — auto-pass (paying less is no risk) |
| `bill > po`, ≤ tier-1 ceiling (default 5%) | `NEEDS_TIER1_APPROVAL` | dept manager alone (Claims Mgr / Operations Mgr / HR Mgr per `procurement.services._DEPT_TIER1_TITLES`) |
| `bill > po`, > tier-1 ceiling | `NEEDS_TIER2_APPROVAL` → `TIER1_APPROVED` → `MATCHED` | dept manager + co-approver (Operations Mgr / Finance Mgr / CFO; SoD: must differ from tier-1 approver) |

The tier-1 ceiling lives in `procurement.VariancePolicy` (single-row settings, editable via admin / `/api/v1/variance-policy/`). Default 5.00%.

**DeepSeek second-pass.** `verify_bill_with_ai()` is called synchronously with a 15s timeout right after the deterministic match. It builds a JSON snapshot of (PO header + lines) and (bill header + lines), sends it to DeepSeek with an anti-fraud system prompt, and parses the structured verdict (`clean | variance | anomaly | fraud_cue`) into `BillAIVerification`. AI failure (timeout, missing key, bad JSON) is logged with `verdict=UNAVAILABLE` and never blocks the deterministic match. Advisory only — humans (and the deterministic 3-way match) hold the final say.

**Payment.confirm gate.** `payments.Payment.confirm()` refuses to post the JE for any outbound payment whose allocations include a `vendor_bill` not in `MATCHED` or `OVERRIDE` state. Bill in `NEEDS_TIER*` / `TIER1_APPROVED` / `REJECTED` cannot be paid — the variance must be cleared first.

**New titles.** `core.UserProfile.Title` gained `CLAIMS_MANAGER`, `OPERATIONS_MANAGER`, `HR_MANAGER` for tier-1 routing. Title-to-department mapping is fixed in `procurement.services._DEPT_TIER1_TITLES`. CFO is treated as a super-tier-1 approver for any department.

## Exceptions Engine (CFO-authored, May 2026)

The `exceptions/` Django app is the cross-cutting alert queue for *every* operational anomaly that needs a human eye. Other modules don't write to its tables directly — they write to their own models, and the exceptions engine raises a row via `post_save` signals.

**Auto-triggers** (`exceptions/signals.py`):
| Source | Event | Severity | Cleared by |
|---|---|---|---|
| `procurement.POBillMatch` | status = `NEEDS_TIER1_APPROVAL` / `NEEDS_TIER2_APPROVAL` / `TIER1_APPROVED` | Medium / High | Dept Manager / Finance Manager |
| `procurement.VendorBankAccount` | status = `DRAFT` / `PENDING_APPROVAL` | High | Finance Manager (or CFO) |
| `procurement.VendorBankAccount` | status moves to `ACTIVE` | (auto-resolves the linked exception) | — |
| `procurement.BillAIVerification` | verdict = `anomaly` | High | Finance Manager |
| `procurement.BillAIVerification` | verdict = `fraud_cue` | Critical | CFO only |

**Workflow** (`exceptions/services.py`): `OPEN → ACKNOWLEDGED → RESOLVED` (or `DISMISSED`). Every transition writes to `core.AuditLog`. Resolution is gated by `requires_role` — `ANY` / `DEPT_MANAGER` / `FINANCE_MANAGER` / `CFO`.

**Linker webhook**: when both `LINKER_API_KEY` and `LINKER_API_BASE` are set in `.env`, every new exception is `POST`ed (Bearer-token auth) to `LINKER_API_BASE`. Best-effort, never blocks. The dispatch attempt + response code is recorded on the exception row for replay. Configure to a Slack incoming webhook, Telegram bot proxy, WhatsApp Business API, or any HTTP receiver.

**Defensive registration**: `exceptions/apps.py.ready()` calls `signals.register_signals()`, which uses `apps.get_model()` with try/except so the app loads cleanly even if the source models haven't shipped yet on a given branch.

**API**:
```
/api/v1/exceptions/                       GET, POST
/api/v1/exceptions/{id}/                  GET
/api/v1/exceptions/{id}/acknowledge/      POST
/api/v1/exceptions/{id}/resolve/          POST  {notes}
/api/v1/exceptions/{id}/dismiss/          POST  {reason}
/api/v1/exceptions/{id}/retry-linker/     POST
/api/v1/exceptions/counts/                GET   — {total, critical, high, medium, low}
```

Frontend: `/exceptions` page with KPI strip + filterable list + detail modal (acknowledge / resolve / dismiss / retry-linker buttons gated by role). Sidebar entry under standalone items.

### Internal-control authority
Treat any change to procurement, fx, or `period_close.py` as **CFO-controlled**. The dual-approval workflow, segregation of duties, and the period-close gates exist to satisfy NBFIRA's expected control framework and IFRS audit trail. Do not loosen these checks (e.g. by allowing a single approver, lowering the justification threshold, or letting the period close with open POs) without explicit CFO sign-off.

---

## Fixed Assets Module — Phase 2 (CFO-authored, May 2026)

The `assets/` Django app is the Fixed Assets register. **Built and owned directly by the CFO (Prathap Ganesharajah, Alpha Direct Insurance), not by the contracted dev team.** Treat this module as CFO-authoritative — changes require CFO approval, not vendor sign-off.

### Models (`assets/models.py`)
- `AssetCategory`     — group with default depreciation method, useful life, and GL accounts
- `Asset`             — single asset (tag, cost, salvage, useful life, location, custodian, **opening accumulated depreciation** for assets migrated from another system)
- `DepreciationEntry` — one month of depreciation, linked 1:1 to a `JournalEntry`
- `AssetDisposal`     — sale / write-off / transfer / trade-in with auto gain/loss to P&L
- `AssetImportBatch`  — stages CSV uploads (typically Odoo) for preview → commit

### Depreciation engine (`assets/services.py`)
- **Straight-line** (default for OE/IT/FF)
- **Reducing balance** (default for motor vehicles, per BURS practice)
- Idempotent — re-running a period is a no-op
- Caps so NBV never drops below salvage
- Bulk run via `run_monthly_depreciation(period, user, dry_run=False)`

### Odoo importer
- Auto-detects Odoo column variants (Reference/Code, Gross Value, Method Number/Useful Life, etc.)
- 2-step: `preview` → `commit` (atomic, all-or-nothing)
- `last_depreciation_date` is set to the migration cutover date so historical Odoo depreciation is **not** re-posted to the GL

### API
```
/api/v1/asset-categories/        GET, POST
/api/v1/assets/                  GET, POST
/api/v1/assets/{id}/depreciate/  POST  ?period=YYYY-MM
/api/v1/assets/{id}/dispose/     POST
/api/v1/assets/run-monthly-depreciation/  POST  {period_name, dry_run}
/api/v1/depreciation-entries/    GET
/api/v1/asset-disposals/         GET
/api/v1/asset-imports/preview/   POST  multipart: file + cutover_date + company
/api/v1/asset-imports/{id}/commit/  POST
/api/v1/reports/asset-register/  GET   ?as_of=&category=&status=
```

### Frontend pages
- `/assets`                       — register list (tabs: All / Active / Disposed / Written off)
- `/assets/new`                   — create form with category-driven defaults
- `/assets/[id]`                  — KPI strip + Details / Depreciation / Disposal tabs + modals
- `/assets/import`                — 3-step Odoo CSV wizard (upload → preview → commit)
- `/reports/asset-register`       — date/category/status filters, by-category subtotals, CSV export

### Management commands
```bash
python manage.py setup_asset_categories           # idempotent — creates OE/IT/MV/FF categories
python manage.py run_monthly_depreciation 2026-04 # bulk run; supports --dry-run, --company ADI
```

### Default categories (created by `setup_asset_categories`)
| Code | Name | Cost acct | Accum-depr acct | Months | Method |
|---|---|---|---|---|---|
| OE | Office equipment | 1410 | 1451 | 60 | Straight-line |
| IT | IT equipment | 1420 | 1452 | 36 | Straight-line |
| MV | Motor vehicles | 1430 | 1453 | 48 | Reducing balance |
| FF | Furniture and fittings | 1440 | 1454 | 120 | Straight-line |

All categories book depreciation expense to **6600 Depreciation**.

---

## What is NOT yet built (future phases)

- Celery + Redis for async event processing (currently synchronous)
- Reinsurance module (cessions, recoveries, bordereau)
- Payroll / PAYE calculation
- Document storage (policy documents, claim photos)
- Email notifications
- Real Graphite webhook (currently simulated via management command)
- (Automated bank feeds — v1 backbone shipped, see `bank_feeds/` app; bank-specific protocol parsers still need wiring)
- (XLSX import for Odoo assets — shipped, see `assets/services.py:_rows_from_xlsx`)
- (Reinsurance module — v1 backbone shipped, see `reinsurance/` app)
- (Investment register — v1 backbone shipped, see `investments/` app)

## Design System (MANDATORY)

Before building, styling, or modifying ANY UI component, read and follow the Alpha Direct Design System at `alpha-direct-design-system/SKILL.md`. Reference files for colors, components, and layout are in `alpha-direct-design-system/references/`. Logo assets are in `alpha-direct-design-system/assets/`. Never deviate from these specs unless explicitly told to.

<!-- BEGIN brain.md -->
## Project Brain

This project keeps a **Project Brain**: a persistent memory layer of its durable decisions, requirements, and constraints. Read `./BRAIN.md` for the full read/write contract.
@import ./BRAIN.md

Maintain the brain as part of normal coding work — not as a separate task. While discussing or implementing features:
- **Start of a task:** load relevant context with the `brain` CLI (`list-pages`, `read-page`, `read-root`). Prefer a narrow read over scanning everything.
- **When a decision, requirement, constraint, or durable insight settles** (in chat or while coding): capture it immediately via the `brain` CLI. Do not wait to be asked and do not batch it for later.
- **Pure implementation with no new decision:** do not write to the brain.
- **When overturning a prior conclusion:** update the page (`update-truth` and/or `append-timeline` with `kind: reversal`, or `archive-page`).
- Only store what will still matter in six months and is hard to reconstruct from the code alone.
- All reads and writes go through the `brain` CLI — never hand-edit brain files.

The brain skills (`brain-setup`, `brain-page`, `brain-ingest`, `brain-bootstrap`) are installed in your global skills directory. Prefer `brain init` to scaffold a new project.
<!-- END brain.md -->
