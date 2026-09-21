# Production seed scripts

One-shot data-loading scripts for the alpha-finance production deploy.
Each script is idempotent — safe to re-run.

## Active scripts (v2 — 6-digit chart matching the CFO's books)

Run in this order:

| Step | Script | Purpose |
|---|---|---|
| 1 | `seed_fiscal_periods.py` | Creates the 24 monthly `FiscalPeriod` rows covering FY25 + FY26. Run first so subsequent JE seeds find an open period for their `entry_date`. |
| 2 | `seed_coa_v2.py` | Replaces the legacy 4-digit chart with the **271-account 6-digit chart** from Alpha Direct Insurance's operational books. Wipes the FY24/FY25 trial-balance JEs from `seed_production_fy.py` (which reference the soon-to-be-removed 4-digit codes), removes the 76 legacy accounts, then upserts the new chart. |
| 3 | `seed_fy25_gl.py` | Posts the FY25 GL (12 months, Jul 2024 – Jun 2025) as a single balanced JE against the new chart. Dr = Cr = P 341,824,683.23. |
| 4 | `seed_fy26_gl.py` | Posts the FY26 YTD GL (10 months, Jul 2025 – Apr 2026) as a single balanced JE. Dr = Cr = P 314,092,891.35. |

Each script runs with `python manage.py shell < ops/seeds/<script>.py`.

### Verification after running all four

- `/accounts` — 271 accounts listed (270 from CFO books + 1 rounding-adjust)
- `/journal-entries` — 2 entries (2025-06-30 FY25 closing, 2026-04-30 FY26 YTD), both `posted`
- `/reports/profit-loss` `From=2024-07-01 To=2025-06-30` — revenue P 128,547,790.68, expense P 128,294,367.94, **net profit P 253,422.74**
- `/reports/profit-loss` `From=2025-07-01 To=2026-04-30` — revenue P 111,487,894.38, expense P 108,434,858.26, **net profit P 3,053,036.12**
- `/reports/trial-balance` `as_of=2025-06-30` — balances to the cent
- `/period-close` — dropdown lists 24 monthly periods, FY25 closable

## Why the chart was replaced

The original `seed_production_fy.py` mapped the CFO's 222 active 6-digit GL
codes down to production's 76-account 4-digit chart. The compression dropped
~P 48M of activity that didn't have a clean mapping target, and forced the
CFO to maintain two parallel charts (Odoo as the source of truth; production
as a summarised view). For audit, NBFIRA reporting, and IFRS 17 readiness,
production now mirrors the operational chart 1:1.

`reporting/reports.py` and `procurement/models.py` were already type-based
(they switch on `account_type` and `sub_type`, never on hardcoded 4-digit
codes), so the BS / P&L / Cash Flow / Trial Balance / PO guards work
transparently with the new chart — no report code changes needed.

## Legacy scripts (do not run)

| Script | Status |
|---|---|
| `seed_production_fy.py` | **Superseded** by `seed_fy25_gl.py` + `seed_fy26_gl.py`. Kept for git-history reference; do not re-run. `seed_coa_v2.py` removes its JEs as part of the migration. |
| `seed_group_companies.py` | Still active — runs once to populate the 13 group companies. |
| `load_purchase_orders.py` | Still active — needs the Odoo PO Excel from the CFO before it can run. |

## Where the v2 data came from

`seed_coa_v2.py` and `seed_fy{25,26}_gl.py` were generated from two Odoo
General Ledger exports the CFO provided:

- `FY25 GL.xlsx` — full FY25 GL (Jul 2024 – Jun 2025, 238 accounts, P 341M)
- `FY26 GL.xlsx` — FY26 YTD GL (Jul 2025 – Apr 2026, 242 accounts, P 314M)

Classification rules (which 6-digit prefix → which `account_type` / `sub_type`)
follow the CFO's existing Odoo chart structure: 100xxx revenue, 101–108xxx +
118xxx cost-of-insurance, 109–124xxx operating expense, 2xxxxx balance-sheet
split by 3-digit sub-prefix (200/210/220/230/240/250/260/270/280/290 = asset;
204/205/206/207/208/209/211/213/214/215 = liability; 216/217/218/219001 =
equity), 999999 = retained earnings. A single rounding-adjustment account
`699999` absorbs the P 89.55 rounding gap in the FY25 file.
