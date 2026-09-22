import type { HealthDashboard } from '@/lib/api'

/**
 * The four money tiles on /health/adh-dashboard, taken from /health/dashboard/ —
 * the SAME feed the Health Cover screen publishes, so the two screens can never
 * disagree.
 *
 * 🔴 NEVER re-sum the stored gross. `HealthcareUpload.gross_amount` is
 * VAT-INCLUSIVE, so a local sum reads premium 14% too high and the loss ratio an
 * eighth too low — this page shipped on 9-Sep-2026 showing 7.6% where the Health
 * Cover screen showed 8.72% for the same month. Read `gwpYtdExcl` /
 * `gwpMonthExcl` / `lossRatioYtdPct` and nothing else.
 *
 * Lives in its own module because a Next.js page file may export nothing but the
 * page (and its route config) — the build fails on any other named export.
 */
export function financialFigures(dash: HealthDashboard | null): {
  ytdGwp: number | null; ytdClaims: number | null
  lossRatio: number | null; latestGwp: number | null
} {
  return {
    ytdGwp: dash?.kpi?.gwpYtdExcl ?? null,
    ytdClaims: dash?.fyTotals?.find(t => t.isCurrent)?.claims ?? null,
    lossRatio: dash?.kpi?.lossRatioYtdPct ?? null,
    latestGwp: dash?.kpi?.gwpMonthExcl ?? null,
  }
}
