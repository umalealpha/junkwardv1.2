'use client'

import { useEffect, useState } from 'react'
import { useCountUp } from '@/hooks/useCountUp'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import {
  Wallet,
  Shield,
  Banknote,
  AlertTriangle,
  CreditCard,
  Activity,
  Calendar,
  Layers,
  TrendingUp,
  BarChart3,
  CheckCircle2,
  AlertCircle,
  RefreshCw,
  DollarSign,
  Receipt,
  FileText,
  Upload,
  Landmark,
  type LucideIcon,
} from 'lucide-react'
import { PieChart, Pie, Cell, ResponsiveContainer } from 'recharts'
import { getDashboardData, getMe, getToken } from '@/lib/api'
import type { DashboardData, UserProfile } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { AIInsightRibbon } from '@/components/AIInsightRibbon'
import { BriefNoteCard } from '@/components/BriefNoteCard'
import { MyRequestsBlock } from '@/components/MyRequestsBlock'
import { Button } from '@/components/ui/button'
import { KpiPopup } from '@/components/KpiPopup'
import { ProcessorDpaBanner } from '@/components/ProcessorDpaBanner'
import { BankBalancesSection } from '@/components/finance/BankBalancesSection'
import { useTheme } from '@/contexts/ThemeContext'
import { useCompany } from '@/contexts/CompanyContext'
import { formatDate, formatMillions, localYmd, parseAmount, sumAmounts } from '@/lib/utils'

// ─── Helper: extract BS sub-totals ──────────────────────────────────────────

function extractBsSummary(section: any): { label: string; value: string }[] {
  if (!section || typeof section !== 'object') return []
  const items: { label: string; value: string }[] = []
  for (const [key, val] of Object.entries(section)) {
    if (key === 'total' || key === 'subtotal') continue
    if (typeof val === 'object' && val !== null && !Array.isArray(val)) {
      const sub = val as any
      const total = sub.total || sub.subtotal || '0'
      const label = key.replace(/_/g, ' ').replace(/\b\w/g, (c: string) => c.toUpperCase())
      items.push({ label, value: total })
    }
  }
  return items
}

// The count-up hook moved to @/hooks/useCountUp (20-Sep-2026) so the bank
// balances panel reuses this dashboard's motion instead of copying it. Same
// implementation, same behaviour — imported above.

// ─── Glass KPI card (redesign pack 03 — the signature element) ───────────────
// Token-driven so it renders correctly in Classic / Fun / Heavenly. Animated
// count-up on the real value; NO sparkline — the dashboard endpoints don't
// return a per-KPI history series, and the pack forbids inventing one.
interface GlassKpiProps {
  label: string
  value: string
  raw?: number
  icon: LucideIcon
  sub: string
  isOrange: boolean
  index: number
  theme: any
  reduceMotion: boolean
  fmt: (v: string | number) => string
  onHoverEnter: () => void
  onHoverLeave: () => void
}

function GlassKpiCard({
  label, value, raw, icon: Icon, sub, isOrange, index, theme, reduceMotion, fmt,
  onHoverEnter, onHoverLeave,
}: GlassKpiProps) {
  const animate = raw !== undefined && isFinite(raw) && !reduceMotion
  const counted = useCountUp(raw ?? 0, 900, animate)
  const display = raw !== undefined && isFinite(raw) ? fmt(counted) : value
  const accent = isOrange ? theme.orange : theme.inf
  return (
    <div
      onMouseEnter={onHoverEnter}
      onMouseLeave={onHoverLeave}
      className="glass-card relative overflow-hidden p-4 flex flex-col"
      style={{
        // Stagger each card's entrance ~50ms (pack 03). Snapped off under
        // reduce-motion by the global guard in globals.css.
        animation: reduceMotion ? undefined : `dash-slide-up 360ms var(--ease-out, ease-out) both`,
        animationDelay: reduceMotion ? undefined : `${index * 50}ms`,
        minHeight: 116,
      }}
    >
      {/* top hairline accent in the tile's own colour */}
      <span
        aria-hidden="true"
        className="absolute top-0 left-0 right-0 h-[2px]"
        style={{ background: `linear-gradient(90deg, ${accent}, transparent 80%)`, opacity: 0.7 }}
      />
      <div className="flex items-center gap-2.5">
        <div
          className="w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0"
          style={{ background: isOrange ? theme.oL : theme.inB }}
        >
          <Icon className="w-[18px] h-[18px]" style={{ color: accent }} strokeWidth={1.6} />
        </div>
        <span
          className="text-[11px] font-semibold uppercase tracking-[0.1em] leading-tight"
          style={{ color: theme.t2 }}
        >
          {label}
        </span>
      </div>
      <div
        className="font-display-tight font-bold mt-3 tabular-nums whitespace-nowrap"
        style={{ color: theme.navy, fontSize: 'clamp(16px, 1.6vw, 28px)' }}
      >
        {display}
      </div>
      <div className="text-xs mt-1" style={{ color: theme.t3 }}>{sub}</div>
      {/* TODO: wire <kpi>_series from the API when a per-KPI history endpoint
          exists, then render a Recharts sparkline here (orange, ~40px). Until
          then we deliberately show no sparkline rather than fabricate a trend. */}
    </div>
  )
}

// ─── Dashboard page ─────────────────────────────────────────────────────────

export default function DashboardPage() {
  const router = useRouter()
  const { theme, reduceMotion } = useTheme()
  const { selectedId: companyId, selected: selectedCompany } = useCompany()
  // CFO directive 2026-05-19 (Manus master guide § 2): trading entities
  // do not see GWP / Claims / Combined Ratio. They get Revenue / GP /
  // EBITDA / PAT only. Default to insurance behaviour when the API
  // hasn't yet supplied an entity_type (back-compat with cached payloads).
  const isTrading = selectedCompany?.entity_type === 'trading'
  const isInsurance = !selectedCompany || selectedCompany.entity_type !== 'trading'
  // Bug fix 2026-05-19 (Charmaine #5): the dashboard was hard-coding 'BWP'
  // on every number because formatMillions defaults to BWP. ADSA's
  // functional currency is ZAR, AIZ is ZMW, etc. Read the entity's base
  // currency from the API and route every amount-renderer through fmtM().
  const dashboardCurrency = (selectedCompany?.base_currency || 'BWP') as string
  const fmtM = (v: string | number) => formatMillions(v, dashboardCurrency)
  const [data, setData] = useState<DashboardData | null>(null)
  const [me, setMe] = useState<UserProfile | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [hoveredKpi, setHoveredKpi] = useState<string | null>(null)
  const [actionTab, setActionTab] = useState<'attention' | 'activity'>('attention')

  // Date range state
  const now = new Date()
  const fyStart = now.getMonth() + 1 >= 7
    ? `${now.getFullYear()}-07-01`
    : `${now.getFullYear() - 1}-07-01`
  const todayStr = localYmd(now)
  const [dateFrom, setDateFrom] = useState(fyStart)
  const [dateTo, setDateTo] = useState(todayStr)

  // FY quick-select presets (Alpha Direct FY = 1 Jul – 30 Jun).
  // CFO directive 2026-05-17: Mar26 YTD added so dashboard can match the
  // 9-month management-accounts cut-off (Jul'25 – Mar'26). Without this,
  // selecting FY2026 returned full-year figures that diverged from the
  // MA workbook (e.g. EBITDA 11.5M instead of 2.28M).
  const FY_PRESETS = [
    { label: 'FY2025',     from: '2024-07-01', to: '2025-06-30' },
    { label: 'Mar26 YTD',  from: '2025-07-01', to: '2026-03-31' },
    { label: 'FY2026',     from: '2025-07-01', to: '2026-06-30' },
    { label: 'FY2027',     from: '2026-07-01', to: '2027-06-30' },
    { label: 'FY2028',     from: '2027-07-01', to: '2028-06-30' },
  ]

  const load = async (silent = false) => {
    if (!silent) setLoading(true)
    else setRefreshing(true)
    setError(null)
    try {
      const dashData = await getDashboardData({
        company: companyId,
        dateFrom,
        dateTo,
      })
      setData(dashData)

    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load dashboard data')
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }

  useEffect(() => {
    const token = getToken()
    if (!token) { router.replace('/login'); return }
    // Fetch the current user once so we can personalise the hero greeting
    // for the CEO (aiyer@alphadirect.co.bw → "Welcome, Alpha Male.").
    getMe().then(setMe).catch(() => setMe(null))
    // Use silent=true so tiles don't flash to skeleton on date/company changes.
    // Initial skeleton still shows because loading useState initialises to true.
    load(true)
    // Auto-refresh every 60s, but only when the tab is visible — otherwise
    // we hammer the backend (8 calls/cycle) on every open tab in the office.
    const interval = setInterval(() => {
      if (typeof document !== 'undefined' && document.hidden) return
      load(true)
    }, 60000)
    return () => clearInterval(interval)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [companyId, dateFrom, dateTo])

  // ─── Derived values ───────────────────────────────────────────────────────

  const totalCash = data ? parseAmount(data.cashPosition.total_bwp) : 0
  // 'Gross Written Premium' tile reads ONLY GWP from the MA P&L —
  // never the lumped 'revenue' total (which includes subrogation,
  // RI commission, other income, FX). The MA workbook is the spec.
  const revenue = data ? parseAmount(data.maProfitLoss.totals.gross_written_premium) : 0
  // 'Total Claims' tile now reads NET claims incurred (gross − RI recoveries −
  // subro/salvage), matching MA workbook line 11 — not the lumped P&L
  // 'cost_of_insurance' bucket which double-counts ceded RI and acquisition.
  const totalClaims = data ? Math.abs(parseAmount(data.maProfitLoss.totals.net_claim_incurred)) : 0

  // Total Receivables / Payables — derived from Balance Sheet subtotals
  // rather than scanning leaf account codes/names. This matches the MA
  // workbook's groupings without depending on omni's imported CoA codes.
  //
  // Payables = Current Liabilities total + Provisions total.
  //   omni's BS bucketises Claims Payable / IBNR / UPR under 'provisions'
  //   (sub_type), but the MA workbook lumps them into Current Liabilities
  //   along with trade payables. Sum both to match MA's "Total Current
  //   Liabilities" line.
  //
  // Presented positive (BS stores liabilities credit-natural negative).
  type BSSection = { total?: string }
  const clTotal = parseAmount(
    ((data?.balanceSheet?.liabilities as any)?.current_liabilities as BSSection)?.total
    || '0'
  )
  const provTotal = parseAmount(
    ((data?.balanceSheet?.liabilities as any)?.provisions as BSSection)?.total
    || '0'
  )
  const totalAp = Math.abs(clTotal) + Math.abs(provTotal)

  // Receivables — CFO directive 2026-05-24. Read the canonical
  // /reports/receivables-summary/ endpoint instead of the old
  // `current_assets − cash` heuristic, which clamped to 0 when banks
  // sat in the wrong BS section. Every other surface that shows a
  // receivables total must read the same endpoint so the numbers agree.
  const totalAr = parseAmount(data?.receivablesSummary?.total_bwp || '0')

  // As-at date for the point-in-time balance cards (Cash / Receivables /
  // Payables). These are balance-sheet figures AS AT the selected period end
  // (or today on the default view) — NOT period-flow figures — so they don't
  // move with the P&L period filter the way GWP/Claims do. Surfacing the date
  // makes that explicit (Oprah 2026-06-22) and matches the BS mini-card / GWP.
  const asAtLabel = data ? formatDate(data.balanceSheet?.as_of || dateTo) : ''

  // Net Profit / PAT tile reads the LEGACY profit_loss.net_profit endpoint,
  // not maProfitLoss.totals.pat. Reason: the MA P&L endpoint has a known
  // downstream mapping bug (below the EBITDA line) that inflates PAT by
  // roughly 8× — investigated 2026-05-15 — while the legacy endpoint
  // net_profit reconciles to the CFO's MA workbook to within rounding
  // (omni 1.0M vs MA 0.95M for ADIC 9M FY26). The 11-line MA card below
  // still uses maProfitLoss.totals for presentation structure because the
  // top-of-funnel lines (GWP, NEP, gross/net claims) ARE correct on the
  // MA endpoint — only the EBITDA→PAT roll-up is broken.
  const pat = data ? parseAmount(data.profitLoss.net_profit) : 0
  const isProfit = data ? data.profitLoss.is_profit : true
  const netProfit = pat

  const overdueArCount = data?.overdueAr.count || 0
  const overdueArTotal = data ? sumAmounts(data.overdueAr.results.map(i => i.balance_due)) : 0
  const overdueApCount = data?.overdueAp.count || 0
  const overdueApTotal = data ? sumAmounts(data.overdueAp.results.map(i => i.balance_due)) : 0
  // BUG-010 (2026-06-08): real Action Center counts, no longer hardcoded "—".
  const unmatchedBankCount = data?.unmatchedBankCount ?? 0
  const draftJeCount = data?.draftJeCount ?? 0

  // ─── KPI card definitions ─────────────────────────────────────────────────

  interface KpiCard {
    label: string
    value: string
    // Raw numeric the formatted `value` was derived from — drives the count-up
    // animation so the tile lands on the exact same real figure. Optional so a
    // tile can opt out (string-only) without breaking the type.
    raw?: number
    icon: LucideIcon
    sub: string
    isOrange: boolean
  }

  // CFO directive 2026-05-19 (Manus master guide § 2): trading entities
  // get a Revenue / GP / EBITDA / PAT tile set instead of the insurance
  // GWP / Claims tiles. Insurance label kept for ADIC + ADSA only.
  const ebitdaRaw = data ? parseAmount(data.maProfitLoss?.totals?.ebitda || 0) : 0
  const kpiCards: KpiCard[] = data ? (isTrading ? [
    {
      label: 'Total Cash',
      value: fmtM(totalCash),
      raw: totalCash,
      icon: Wallet,
      sub: `As at ${asAtLabel} · ${data.cashPosition.accounts.length} account${data.cashPosition.accounts.length !== 1 ? 's' : ''}`,
      isOrange: true,
    },
    {
      label: 'Revenue',
      value: fmtM(revenue),
      raw: revenue,
      icon: Banknote,
      sub: `YTD ${formatDate(data.profitLoss.from_date)} – ${formatDate(data.profitLoss.to_date)}`,
      isOrange: true,
    },
    {
      label: 'Total Receivables',
      value: fmtM(totalAr),
      raw: totalAr,
      icon: Banknote,
      sub: overdueArCount > 0 ? `${overdueArCount} overdue · as at ${asAtLabel}` : `As at ${asAtLabel}`,
      isOrange: true,
    },
    {
      label: 'Total Payables',
      value: fmtM(totalAp),
      raw: totalAp,
      icon: CreditCard,
      sub: overdueApCount > 0 ? `${overdueApCount} overdue · as at ${asAtLabel}` : `As at ${asAtLabel}`,
      isOrange: false,
    },
    {
      label: 'EBITDA',
      value: fmtM(ebitdaRaw),
      raw: ebitdaRaw,
      icon: Activity,
      sub: 'Pre-finance + pre-depreciation',
      isOrange: false,
    },
    {
      label: `Net ${isProfit ? 'Profit' : 'Loss'} (PAT)`,
      value: fmtM(Math.abs(netProfit)),
      raw: Math.abs(netProfit),
      icon: Activity,
      sub: isProfit ? 'Profit after tax YTD' : 'Loss after tax YTD',
      isOrange: false,
    },
  ] : [
    {
      label: 'Total Cash',
      value: fmtM(totalCash),
      raw: totalCash,
      icon: Wallet,
      sub: `As at ${asAtLabel} · ${data.cashPosition.accounts.length} account${data.cashPosition.accounts.length !== 1 ? 's' : ''}`,
      isOrange: true,
    },
    {
      label: 'Gross Written Premium',
      value: fmtM(revenue),
      raw: revenue,
      icon: Shield,
      sub: `YTD ${formatDate(data.profitLoss.from_date)} – ${formatDate(data.profitLoss.to_date)}`,
      isOrange: true,
    },
    {
      label: 'Total Receivables',
      value: fmtM(totalAr),
      raw: totalAr,
      icon: Banknote,
      sub: overdueArCount > 0 ? `${overdueArCount} overdue · as at ${asAtLabel}` : `As at ${asAtLabel}`,
      isOrange: true,
    },
    {
      label: 'Total Claims',
      value: fmtM(totalClaims),
      raw: totalClaims,
      icon: AlertTriangle,
      sub: 'Claims incurred YTD',
      isOrange: false,
    },
    {
      label: 'Total Payables',
      value: fmtM(totalAp),
      raw: totalAp,
      icon: CreditCard,
      sub: overdueApCount > 0 ? `${overdueApCount} overdue · as at ${asAtLabel}` : `As at ${asAtLabel}`,
      isOrange: false,
    },
    {
      label: `Net ${isProfit ? 'Profit' : 'Loss'}`,
      value: fmtM(Math.abs(netProfit)),
      raw: Math.abs(netProfit),
      icon: Activity,
      sub: isProfit ? 'Profit this period' : 'Loss this period',
      isOrange: false,
    },
  ]) : []

  // ─── P&L line items ───────────────────────────────────────────────────────

  interface SummaryLine { label: string; value: string; bold?: boolean; sub?: boolean; highlight?: boolean; profit?: boolean }

  // MA-faithful P&L: mirrors the CFO's Management Accounts workbook line for
  // line. Source is reporting.ma_pl.build_ma_pl which the CFO 3x-confirmed
  // (revenue mapping FROZEN per memory). Display only — sign convention
  // already applied server-side.
  const mt = data?.maProfitLoss?.totals
  const fmtBracketed = (v: number) =>
    v < 0 ? `(${fmtM(Math.abs(v))})` : fmtM(v)
  // BUG-007 (Oprah 1a, 2026-06-05): deduction lines (claims, acquisition,
  // opex, provisions, depreciation, finance cost) ALWAYS show in parentheses,
  // even when the frozen MA value is stored as a positive magnitude. Display
  // only — the frozen numbers are unchanged.
  const fmtDeduction = (v: number) => `(${fmtM(Math.abs(v))})`
  // CFO directive 2026-05-19 (Manus master guide § 2): trading entities
  // present a non-insurance P&L. Suppress the insurance-only rows
  // (Net Earned Premium, Net Claims Incurred, Net Acquisition) and lead
  // with Revenue → Cost of Sales → Gross Profit.
  const plLinesInsurance: SummaryLine[] = (data && mt) ? [
    { label: 'Net Earned Premium', value: fmtM(parseAmount(mt.net_earned_premium)), bold: true },
    { label: 'Net Claims Incurred', value: fmtDeduction(parseAmount(mt.net_claim_incurred)), sub: true },
    { label: 'Net Acquisition', value: fmtDeduction(parseAmount(mt.net_acquisition)), sub: true },
    { label: 'Gross Profit', value: fmtM(parseAmount(mt.gross_profit)), bold: true, highlight: true },
    { label: 'Other Income', value: fmtBracketed(parseAmount(mt.total_other_income)), sub: true },
    { label: 'Operating Expenses', value: fmtDeduction(parseAmount(mt.total_operating_expenses)), sub: true },
    { label: 'Provisions', value: fmtDeduction(parseAmount(mt.total_provisions)), sub: true },
    { label: 'EBITDA', value: fmtBracketed(parseAmount(mt.ebitda)), bold: true },
    { label: 'Depreciation', value: fmtDeduction(parseAmount(mt.depreciation)), sub: true },
    { label: 'Finance Cost', value: fmtDeduction(parseAmount(mt.finance_cost)), sub: true },
    // PAT final line uses the legacy P&L's net_profit (same number that
    // powers the headline tile) rather than mt.pat — see comment on `pat`
    // above for the 'broken EBITDA→PAT roll-up' note.
    { label: `${isProfit ? 'PAT' : 'PAT (Loss)'}`, value: fmtBracketed(netProfit), bold: true, highlight: true, profit: isProfit },
  ] : []
  const plLinesTrading: SummaryLine[] = (data && mt) ? [
    { label: 'Revenue', value: fmtM(revenue), bold: true },
    { label: 'Gross Profit', value: fmtM(parseAmount(mt.gross_profit)), bold: true, highlight: true },
    { label: 'Other Income', value: fmtBracketed(parseAmount(mt.total_other_income)), sub: true },
    { label: 'Operating Expenses', value: fmtDeduction(parseAmount(mt.total_operating_expenses)), sub: true },
    { label: 'EBITDA', value: fmtBracketed(parseAmount(mt.ebitda)), bold: true },
    { label: 'Depreciation', value: fmtDeduction(parseAmount(mt.depreciation)), sub: true },
    { label: 'Finance Cost', value: fmtDeduction(parseAmount(mt.finance_cost)), sub: true },
    { label: `${isProfit ? 'PAT' : 'PAT (Loss)'}`, value: fmtBracketed(netProfit), bold: true, highlight: true, profit: isProfit },
  ] : []
  const plLines: SummaryLine[] = isTrading ? plLinesTrading : plLinesInsurance

  // ─── Cash Flow line items ───────────────────────────────────────────────

  // The dashboard does not perform a true Cash Flow Statement reconciliation
  // (indirect method needs prior-period BS + working-capital deltas). The
  // dashboard tile shows the *current* cash position and net P&L impact as
  // an *indicative* read — full Cash Position page has the detail.
  // Sign: positive number means cash went UP, negative means cash went DOWN.
  // This is the closest single number we can compute without a second BS call.
  const indicativeChange = netProfit
  const cfLines: SummaryLine[] = data ? [
    { label: 'Current Cash Balance', value: fmtM(totalCash), bold: true, profit: totalCash >= 0 },
    { label: 'Period P&L Impact', value: fmtBracketed(indicativeChange), sub: true },
    { label: 'See Cash Position for full reconciliation', value: '', sub: true },
  ] : []

  // ─── BS line items ────────────────────────────────────────────────────────

  const bsAssets = data ? extractBsSummary(data.balanceSheet.assets) : []
  const bsLiabilities = data ? extractBsSummary(data.balanceSheet.liabilities) : []

  const bsLines: SummaryLine[] = data ? [
    { label: 'Total Assets', value: fmtM(data.balanceSheet.totals.total_assets), bold: true },
    ...bsAssets.map(a => ({ label: a.label, value: fmtM(a.value), sub: true })),
    { label: 'Total Liabilities', value: fmtM(parseAmount(data.balanceSheet.totals.liabilities_and_equity) - parseAmount(data.balanceSheet.equity?.total || '0')), bold: true },
    ...bsLiabilities.map(l => ({ label: l.label, value: fmtM(l.value), sub: true })),
    { label: 'Total Equity', value: fmtM(data.balanceSheet.equity?.total || '0'), bold: true, highlight: true },
  ] : []

  // ─── Skeleton ─────────────────────────────────────────────────────────────

  const Skel = ({ className = '' }: { className?: string }) => (
    <div className={`skeleton rounded-lg ${className}`} />
  )

  // ─── Render ───────────────────────────────────────────────────────────────

  return (
    <div className="flex flex-col min-h-screen">
      <style>{`
        @keyframes dash-slide-up {
          from { opacity: 0; transform: translateY(8px); }
          to { opacity: 1; transform: translateY(0); }
        }
        .dash-row { animation: dash-slide-up 200ms ease-out both; }
        .dash-row-1 { animation-delay: 0ms; }
        .dash-row-2 { animation-delay: 50ms; }
        .dash-row-3 { animation-delay: 100ms; }
        .dash-row-4 { animation-delay: 150ms; }

        /* ─── 3D floating hero — Alpha Direct Dashboard.dc.html (2026-06-24) ───
           Peach-gradient card that enters with a 3D tilt, then gently floats;
           the eyebrow / title / subtitle sit on layered translateZ planes for
           a parallax depth read. Motion is disabled under reduced-motion. */
        .dash-hero-stage { perspective: 1600px; }
        .dash-hero-enter {
          transform-style: preserve-3d;
          animation: dash-hero-enter 1.1s cubic-bezier(0.2, 0.7, 0.2, 1) both;
        }
        .dash-hero-float {
          transform-style: preserve-3d;
          animation: dash-hero-float 7s ease-in-out 1.1s infinite;
        }
        .dash-hero-card {
          background: linear-gradient(125deg, #ffffff 0%, #ffffff 38%, #fdf0e2 72%, #fbe2cb 100%);
          border: 1px solid rgba(13, 27, 42, 0.06);
          box-shadow: 0 44px 84px -34px rgba(13, 27, 42, 0.34), 0 16px 34px -18px rgba(13, 27, 42, 0.20);
          transform-style: preserve-3d;
        }
        .dash-hero-eyebrow { transform: translateZ(20px); }
        .dash-hero-h1 { transform: translateZ(48px); }
        .dash-hero-sub { transform: translateZ(28px); }
        @keyframes dash-hero-enter {
          0% { opacity: 0; transform: perspective(1600px) rotateX(22deg) translateY(60px) scale(0.9); }
          100% { opacity: 1; transform: perspective(1600px) rotateX(0deg) translateY(0) scale(1); }
        }
        @keyframes dash-hero-float {
          0%, 100% { transform: translateY(0); }
          50% { transform: translateY(-12px); }
        }
        @media (prefers-reduced-motion: reduce) {
          .dash-hero-enter, .dash-hero-float { animation: none; }
          .dash-hero-eyebrow, .dash-hero-h1, .dash-hero-sub { transform: none; }
        }
      `}</style>
      <TopBar
        title="Dashboard"
        actions={
          <Button
            variant="ghost"
            size="sm"
            loading={refreshing}
            leftIcon={<RefreshCw className="w-3.5 h-3.5" />}
            onClick={() => load(true)}
          >
            Refresh
          </Button>
        }
      />

      <div className="flex-1 p-6 space-y-6" style={{ maxWidth: 1440 }}>
        {/* External-audit follow-up 2026-05-19: surface failed endpoints so a
            down API doesn't look like real zeros. The banner only renders if
            at least one of the 9 dashboard endpoints rejected. */}
        {data && Object.entries(data.endpoint_errors || {}).filter(([, v]) => v).length > 0 && (
          <div role="alert" style={{
            background: '#FDECEA', border: '1px solid #F5C2BE',
            borderLeft: '6px solid #8E1F12', borderRadius: 10,
            padding: '12px 16px', color: '#8E1F12', fontSize: 13,
          }}>
            <strong style={{ display: 'block', marginBottom: 4 }}>
              Some dashboard endpoints failed — values shown for the affected
              tiles are placeholders, not actual zeros.
            </strong>
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {Object.entries(data.endpoint_errors).filter(([, v]) => v).map(([k, v]) => (
                <li key={k}><b>{k}</b>: {String(v).slice(0, 180)}</li>
              ))}
            </ul>
          </div>
        )}
        {/* ─── Dashboard banner — Dashboard Banner.dc.html (claude.ai/design import 2026-07-06) ──
            Warm cream card, Book Antiqua serif, gold-shimmer accent, staggered
            entrance. Sized ~60% of the prior 3D hero per CFO note ("too big,
            make it 60%"). Scope-aware title + CEO greeting preserved. The Aria
            line uses the REAL AIInsightRibbon — NOT the .dc.html's 10 demo
            messages (those carry illustrative figures; never show fake numbers
            on the live dashboard). Keyframes bnr-rise/shimmer/glow in globals.css. */}
        <div
          className="relative overflow-hidden"
          style={{
            borderRadius: 14,
            background: 'linear-gradient(135deg,#fdfbf7 0%,#fbf6ee 42%,#fbe9d4 100%)',
            boxShadow: '0 6px 22px rgba(13,27,42,0.07),0 1px 3px rgba(13,27,42,0.05)',
            border: '1px solid rgba(13,27,42,0.05)',
            fontFamily: "'Book Antiqua','Palatino Linotype',Palatino,'Cormorant Garamond',Georgia,serif",
          }}
        >
          <div style={{ position: 'absolute', inset: 0, background: 'radial-gradient(360px 200px at 88% 8%,rgba(244,166,35,0.14),transparent 62%)', pointerEvents: 'none', animation: 'bnr-glow 7s ease-in-out infinite' }} />
          <div className="relative" style={{ padding: '19px 20px 18px' }}>
            <div style={{ color: '#6C757D', fontSize: 9, letterSpacing: '3.2px', textTransform: 'uppercase', fontWeight: 600, marginBottom: 6, animation: 'bnr-rise .6s ease-out .05s both' }}>
              {new Date().toLocaleDateString('en-BW', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' }).toUpperCase()}
            </div>
            <h1 style={{ margin: 0, lineHeight: 0.98 }}>
              <span style={{ display: 'block', fontSize: 'clamp(26px,4.8vw,40px)', fontWeight: 700, color: '#25344a', letterSpacing: '0.4px', animation: 'bnr-rise .7s ease-out .15s both' }}>
                {me?.email?.toLowerCase() === 'aiyer@alphadirect.co.bw'
                  ? 'Welcome,'
                  : (selectedCompany ? (selectedCompany.name || selectedCompany.code || 'Group') : 'Group')}
              </span>
              <span style={{ display: 'block', fontStyle: 'italic', fontSize: 'clamp(23px,4.2vw,35px)', fontWeight: 600, letterSpacing: '0.4px', backgroundImage: 'linear-gradient(90deg,#B8862E 0%,#E9B85A 25%,#F4D27A 40%,#E9B85A 55%,#A9791E 100%)', backgroundSize: '200% auto', WebkitBackgroundClip: 'text', backgroundClip: 'text', WebkitTextFillColor: 'transparent', animation: 'bnr-rise .7s ease-out .28s both, bnr-shimmer 5.5s linear 1s infinite' }}>
                {me?.email?.toLowerCase() === 'aiyer@alphadirect.co.bw' ? 'Alpha Male.' : 'Dashboard.'}
              </span>
            </h1>
            <div style={{ fontStyle: 'italic', color: '#4a5568', fontSize: 13, letterSpacing: '0.3px', marginTop: 10, animation: 'bnr-rise .7s ease-out .42s both' }}>
              Where the numbers tell the story of Alpha Direct.
            </div>
            <div style={{ marginTop: 14, animation: 'bnr-rise .7s ease-out .56s both' }}>
              <AIInsightRibbon ctx="dashboard" />
            </div>
          </div>
        </div>

        {/* Processor-DPA signature call-to-action — renders only when an
            agreement is awaiting THIS user's e-signature (CFO 2026-07-29). */}
        <ProcessorDpaBanner />

        {/* CFO Frozen-Figure drift banner removed 2026-05-20 — CFO directive,
            audit-lock no longer enforced on dashboard. Backend FrozenFigure
            data remains intact for /api/v1/reports/frozen-drift/ consumers. */}

        {/* ─── Date range picker + FY presets ──────────────────────────── */}
        <div className="flex flex-col md:flex-row md:justify-end md:items-center gap-3">
          {/* FY quick-select chips */}
          <div className="flex items-center gap-1.5 flex-nowrap">
            {FY_PRESETS.map(fy => {
              const isActive = dateFrom === fy.from && dateTo === fy.to
              return (
                <button
                  key={fy.label}
                  onClick={() => { setDateFrom(fy.from); setDateTo(fy.to) }}
                  className="text-[11px] font-semibold px-2.5 py-1 rounded-full transition-all"
                  style={{
                    background: isActive ? theme.orange : theme.card,
                    color: isActive ? '#fff' : theme.t2,
                    border: `1px solid ${isActive ? theme.orange : theme.cardBdr}`,
                    cursor: 'pointer',
                  }}
                >
                  {fy.label}
                </button>
              )
            })}
          </div>
          <div className="flex flex-col gap-1">
            <div
              className="flex items-center gap-2 rounded-lg px-3.5 py-2"
              style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}
            >
              <Calendar className="w-4 h-4 flex-shrink-0" style={{ color: theme.orange }} />
              <span className="text-xs font-medium" style={{ color: theme.g700 }}>From</span>
              <input
                type="date"
                aria-label="From date"
                value={dateFrom}
                onChange={e => setDateFrom(e.target.value)}
                className="text-xs rounded-md px-1.5 py-1"
                style={{
                  border: `1px solid ${theme.cardBdr}`,
                  background: theme.card,
                  color: theme.text,
                }}
              />
              <span className="text-xs font-medium" style={{ color: theme.g700 }}>To</span>
              <input
                type="date"
                aria-label="To date"
                value={dateTo}
                onChange={e => setDateTo(e.target.value)}
                className="text-xs rounded-md px-1.5 py-1"
                style={{
                  border: `1px solid ${theme.cardBdr}`,
                  background: theme.card,
                  color: theme.text,
                }}
              />
              <button
                className="text-xs font-medium text-white rounded-md px-3 py-1.5 cursor-pointer"
                style={{ background: theme.orangeText }}
                onClick={() => load(true)}
              >
                Apply
              </button>
            </div>
            <p className="text-[11px] tracking-wide text-right" style={{ color: theme.t2 }}>
              {formatDate(dateFrom)} → {formatDate(dateTo)}
            </p>
          </div>
        </div>

        {/* Error banner */}
        {error && (
          <div
            className="rounded-lg p-4 flex items-center gap-3"
            style={{ background: theme.erB, border: `1px solid ${theme.er}30` }}
          >
            <AlertCircle className="w-5 h-5 flex-shrink-0" style={{ color: theme.er }} />
            <div>
              <p className="font-medium text-sm" style={{ color: theme.er }}>Failed to load dashboard</p>
              <p className="text-xs mt-0.5" style={{ color: theme.er, opacity: 0.7 }}>{error}</p>
            </div>
            <Button variant="danger" size="sm" className="ml-auto" onClick={() => load()}>
              Retry
            </Button>
          </div>
        )}

        {/* ─── Morning Bank Balances (CFO 2026-09-20) ──────────────────────
            "One of the most important things for me as the CFO is how much
             money we have in the morning to run the operation."
            Above the KPI grid on purpose: the KPIs are the year's story, this
            is today's cash. Self-hides for anyone without CanViewFinancials. */}
        <BankBalancesSection me={me} />

        {/* ─── Row 1: 6 KPI Cards — 2 cols on phone, 3 on tablet, 6 on xl ── */}
        <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3 sm:gap-4 dash-row dash-row-1">
          {(loading && !data) ? (
            <>
              {[...Array(6)].map((_, i) => (
                <div
                  key={i}
                  className="rounded-lg p-4"
                  style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}
                >
                  <Skel className="h-8 w-8 mb-2" />
                  <Skel className="h-5 w-3/4 mb-2" />
                  <Skel className="h-4 w-1/2" />
                </div>
              ))}
            </>
          ) : (
            kpiCards.map((card, idx) => (
              <GlassKpiCard
                key={idx}
                index={idx}
                label={card.label}
                value={card.value}
                raw={card.raw}
                icon={card.icon}
                sub={card.sub}
                isOrange={card.isOrange}
                theme={theme}
                reduceMotion={reduceMotion}
                fmt={fmtM}
                onHoverEnter={() => setHoveredKpi(card.label)}
                onHoverLeave={() => setHoveredKpi(null)}
              />
            ))
          )}
        </div>

        {/* ─── Row 2: Balance Sheet + P&L + Cash Flow ──────────────────── */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 dash-row dash-row-2">
          {/* Balance Sheet Summary */}
          <div
            className="rounded-lg overflow-hidden"
            style={{
              background: theme.card,
              border: `1px solid ${theme.cardBdr}`,
              boxShadow: theme.cardSh,
            }}
          >
            <div
              className="px-5 py-3.5 flex items-center justify-between"
              style={{ borderBottom: `1px solid ${theme.cardBdr}` }}
            >
              <div className="flex items-center gap-2">
                <Layers className="w-[17px] h-[17px]" style={{ color: theme.navy }} />
                <span className="text-[15px] font-semibold" style={{ color: theme.navy }}>
                  Balance Sheet
                </span>
              </div>
              <span className="text-[11px]" style={{ color: theme.t3 }}>
                As of {formatDate(data?.balanceSheet.as_of || todayStr)}
              </span>
            </div>
            <div className="px-5 py-4">
              {/* Asset-composition donut — driven entirely by bsAssets (the real
                  Balance Sheet asset breakdown the page already fetched). Slice
                  colours come from the active theme, so it shows orange in
                  Classic/Obsidian and sky-blue in Heavenly. No invented data. */}
              {(!loading && data && bsAssets.filter(a => Math.abs(parseAmount(a.value)) > 0).length > 0) && (() => {
                const slices = bsAssets
                  .filter(a => Math.abs(parseAmount(a.value)) > 0)
                  .map(a => ({ name: a.label, value: Math.abs(parseAmount(a.value)) }))
                const palette = [theme.orange, theme.navy, theme.t2, theme.inf, theme.teal, theme.t3, theme.g700]
                const totalAssets = fmtM(parseAmount(data.balanceSheet.totals.total_assets))
                return (
                  <div className="relative mx-auto mb-4" style={{ width: '100%', maxWidth: 220, height: 180 }}>
                    <ResponsiveContainer width="100%" height="100%">
                      <PieChart>
                        <Pie data={slices} dataKey="value" nameKey="name" cx="50%" cy="50%"
                             innerRadius={58} outerRadius={84} paddingAngle={2} stroke="none"
                             isAnimationActive={!reduceMotion}>
                          {slices.map((_, i) => <Cell key={i} fill={palette[i % palette.length]} />)}
                        </Pie>
                      </PieChart>
                    </ResponsiveContainer>
                    <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
                      <span className="text-[10px] uppercase tracking-wider" style={{ color: theme.t3 }}>Total Assets</span>
                      <span className="font-semibold" style={{ fontSize: 18, color: theme.navy }}>{totalAssets}</span>
                    </div>
                  </div>
                )
              })()}
              {(loading && !data) ? (
                <div className="space-y-3">
                  {[...Array(5)].map((_, i) => <Skel key={i} className="h-5" />)}
                </div>
              ) : (
                bsLines.map((line, i) => (
                  <div
                    key={i}
                    className="flex justify-between py-[7px]"
                    style={{
                      borderBottom: i < bsLines.length - 1 ? `1px solid ${theme.g100}` : 'none',
                      marginLeft: line.sub ? 18 : 0,
                    }}
                  >
                    <span
                      className="text-sm"
                      style={{
                        fontSize: line.bold ? 14 : 13,
                        fontWeight: line.bold ? 600 : 400,
                        color: line.highlight ? theme.navy : line.bold ? theme.text : theme.t2,
                      }}
                    >
                      {line.label}
                    </span>
                    <span
                      className="font-mono-nums"
                      style={{
                        fontSize: line.bold ? 14 : 13,
                        fontWeight: line.bold ? 700 : 400,
                        color: line.highlight ? theme.navy : theme.g700,
                      }}
                    >
                      {line.value}
                    </span>
                  </div>
                ))
              )}
            </div>
            {/* Balanced indicator — driven by the REAL balanceSheet.totals
                .balanced flag, never hardcoded true (pack 03 §2). */}
            {!loading && data && (
              <div
                className="px-5 py-2 flex items-center gap-2"
                style={{ borderTop: `1px solid ${theme.cardBdr}` }}
              >
                {data.balanceSheet.totals.balanced ? (
                  <>
                    <CheckCircle2 className="w-3.5 h-3.5 flex-shrink-0" style={{ color: theme.ok }} />
                    <span className="text-[11px] font-medium" style={{ color: theme.ok }}>
                      Balance Sheet is balanced
                    </span>
                  </>
                ) : (
                  <>
                    <AlertCircle className="w-3.5 h-3.5 flex-shrink-0" style={{ color: theme.wr }} />
                    <span className="text-[11px] font-medium" style={{ color: theme.wr }}>
                      Assets ≠ Liabilities + Equity — review
                    </span>
                  </>
                )}
              </div>
            )}
            <div
              className="px-5 py-2 text-right"
              style={{ borderTop: `1px solid ${theme.cardBdr}`, background: theme.g50 }}
            >
              <Link
                href="/reports/balance-sheet"
                className="text-xs font-medium cursor-pointer"
                style={{ color: theme.orangeText }}
              >
                View Full Balance Sheet →
              </Link>
            </div>
          </div>

          {/* Profit & Loss Summary */}
          <div
            className="rounded-lg overflow-hidden"
            style={{
              background: theme.card,
              border: `1px solid ${theme.cardBdr}`,
              boxShadow: theme.cardSh,
            }}
          >
            <div
              className="px-5 py-3.5 flex items-center justify-between"
              style={{ borderBottom: `1px solid ${theme.cardBdr}` }}
            >
              <div className="flex items-center gap-2">
                <TrendingUp className="w-[17px] h-[17px]" style={{ color: theme.navy }} />
                <span className="text-[15px] font-semibold" style={{ color: theme.navy }}>
                  Profit & Loss
                </span>
              </div>
              <span className="text-[11px]" style={{ color: theme.t3 }}>
                {data ? `${formatDate(data.profitLoss.from_date)} – ${formatDate(data.profitLoss.to_date)}` : '—'}
              </span>
            </div>
            <div className="px-5 py-4">
              {/* Monthly bar/line combo (pack 03 §3): the dashboard endpoints
                  return a single-period P&L snapshot, not a month-by-month
                  series — so we show an honest empty state rather than invent
                  months. The real MA P&L lines (the truth we DO have) render
                  below. Wire a monthly series endpoint to light up the chart. */}
              {!loading && data && (
                <div
                  className="mb-3 rounded-lg flex flex-col items-center justify-center text-center px-4 py-5"
                  style={{ background: theme.g50, border: `1px dashed ${theme.cardBdr}` }}
                >
                  <BarChart3 className="w-5 h-5 mb-1.5" style={{ color: theme.t3 }} strokeWidth={1.5} />
                  <p className="text-[11px] font-medium" style={{ color: theme.t2 }}>
                    Monthly trend chart
                  </p>
                  <p className="text-[10.5px] mt-0.5" style={{ color: theme.t3 }}>
                    Monthly series not available — showing period totals below
                  </p>
                </div>
              )}
              {(loading && !data) ? (
                <div className="space-y-3">
                  {[...Array(5)].map((_, i) => <Skel key={i} className="h-5" />)}
                </div>
              ) : (
                plLines.map((line, i) => (
                  <div
                    key={i}
                    className="flex justify-between py-[7px]"
                    style={{
                      borderBottom: i < plLines.length - 1 ? `1px solid ${theme.g100}` : 'none',
                      marginLeft: line.sub ? 18 : 0,
                    }}
                  >
                    <span
                      className="text-sm"
                      style={{
                        fontSize: line.bold ? 14 : 13,
                        fontWeight: line.bold ? 600 : 400,
                        color: line.highlight ? theme.navy : line.bold ? theme.text : theme.t2,
                      }}
                    >
                      {line.label}
                    </span>
                    <span
                      className="font-mono-nums"
                      style={{
                        fontSize: line.bold ? 14 : 13,
                        fontWeight: line.bold ? 700 : 400,
                        color: line.profit !== undefined
                          ? (line.profit ? theme.ok : theme.er)
                          : line.sub ? theme.er : theme.g700,
                      }}
                    >
                      {line.value}
                    </span>
                  </div>
                ))
              )}
            </div>
            <div
              className="px-5 py-2 text-right"
              style={{ borderTop: `1px solid ${theme.cardBdr}`, background: theme.g50 }}
            >
              <Link
                href="/reports/profit-loss"
                className="text-xs font-medium cursor-pointer"
                style={{ color: theme.orangeText }}
              >
                View Full P&L →
              </Link>
            </div>
          </div>

          {/* Cash Flow Summary */}
          <div
            className="rounded-lg overflow-hidden"
            style={{
              background: theme.card,
              border: `1px solid ${theme.cardBdr}`,
              boxShadow: theme.cardSh,
            }}
          >
            <div
              className="px-5 py-3.5 flex items-center justify-between"
              style={{ borderBottom: `1px solid ${theme.cardBdr}` }}
            >
              <div className="flex items-center gap-2">
                <DollarSign className="w-[17px] h-[17px]" style={{ color: theme.navy }} />
                <span className="text-[15px] font-semibold" style={{ color: theme.navy }}>
                  Cash Flow
                </span>
              </div>
              <span className="text-[11px]" style={{ color: theme.t3 }}>
                {data ? `${formatDate(data.profitLoss.from_date)} – ${formatDate(data.profitLoss.to_date)}` : '—'}
              </span>
            </div>
            <div className="px-5 py-4">
              {(loading && !data) ? (
                <div className="space-y-3">
                  {[...Array(3)].map((_, i) => <Skel key={i} className="h-5" />)}
                </div>
              ) : (
                cfLines.map((line, i) => (
                  <div
                    key={i}
                    className="flex justify-between py-[7px]"
                    style={{
                      borderBottom: i < cfLines.length - 1 ? `1px solid ${theme.g100}` : 'none',
                      marginLeft: line.sub ? 18 : 0,
                    }}
                  >
                    <span
                      className="text-sm"
                      style={{
                        fontSize: line.bold ? 14 : 13,
                        fontWeight: line.bold ? 600 : 400,
                        color: line.highlight ? theme.navy : line.bold ? theme.text : theme.t2,
                      }}
                    >
                      {line.label}
                    </span>
                    <span
                      className="font-mono-nums"
                      style={{
                        fontSize: line.bold ? 14 : 13,
                        fontWeight: line.bold ? 700 : 400,
                        color: line.profit !== undefined
                          ? (line.profit ? theme.ok : theme.er)
                          : line.sub ? theme.t2 : theme.g700,
                      }}
                    >
                      {line.value}
                    </span>
                  </div>
                ))
              )}
            </div>
            <div
              className="px-5 py-2 text-right"
              style={{ borderTop: `1px solid ${theme.cardBdr}`, background: theme.g50 }}
            >
              <Link
                href="/reports/cash-position"
                className="text-xs font-medium cursor-pointer"
                style={{ color: theme.orangeText }}
              >
                View Cash Position →
              </Link>
            </div>
          </div>
        </div>

        {/* ─── Row 3: Action Center ───────────────────────────────────────── */}
        <div
          className="rounded-lg overflow-hidden dash-row dash-row-3"
          style={{
            background: theme.card,
            border: `1px solid ${theme.cardBdr}`,
            boxShadow: theme.cardSh,
          }}
        >
          <div
            className="px-5 py-3.5 flex items-center justify-between"
            style={{ borderBottom: `1px solid ${theme.cardBdr}` }}
          >
            <div className="flex items-center gap-2">
              <AlertTriangle className="w-[17px] h-[17px]" style={{ color: theme.navy }} />
              <span className="text-[15px] font-semibold" style={{ color: theme.navy }}>
                Action Center
              </span>
            </div>
            <div className="flex items-center rounded-lg p-0.5" style={{ background: theme.g100 }}>
              {(['attention', 'activity'] as const).map((tab) => (
                <button
                  key={tab}
                  onClick={() => setActionTab(tab)}
                  className="px-3 py-1.5 text-xs font-medium rounded-md transition-colors"
                  style={{
                    background: actionTab === tab ? theme.card : 'transparent',
                    color: actionTab === tab ? theme.navy : theme.g700,
                    boxShadow: actionTab === tab ? '0 1px 2px rgba(0,0,0,0.06)' : 'none',
                  }}
                >
                  {tab === 'attention' ? 'Attention' : 'Activity'}
                </button>
              ))}
            </div>
          </div>
          <div className="px-5 py-3.5">
            {actionTab === 'attention' ? (
              (loading && !data) ? (
                <div className="space-y-3">
                  {[...Array(4)].map((_, i) => <Skel key={i} className="h-8" />)}
                </div>
              ) : (
                [
                  {
                    label: 'Overdue Receivables',
                    count: String(overdueArCount),
                    amount: overdueArTotal > 0 ? fmtM(overdueArTotal) : '',
                    color: theme.er,
                  },
                  {
                    label: 'Overdue Payables',
                    count: String(overdueApCount),
                    amount: overdueApTotal > 0 ? fmtM(overdueApTotal) : '',
                    color: theme.wr,
                  },
                  {
                    label: 'Unmatched Bank Lines',
                    count: String(unmatchedBankCount),
                    amount: '',
                    color: theme.inf,
                  },
                  {
                    label: 'Pending Drafts',
                    count: String(draftJeCount),
                    amount: '',
                    color: theme.t2,
                  },
                ].map((item, i) => (
                  <div
                    key={i}
                    className="flex justify-between items-center py-[9px] px-1"
                    style={{
                      borderBottom: i < 3 ? `1px solid ${theme.g100}` : 'none',
                    }}
                  >
                    <div className="flex items-center gap-2">
                      <span
                        className="w-[22px] h-[22px] rounded-full flex items-center justify-center text-[11px] font-bold"
                        style={{
                          background: item.color + '20',
                          color: item.color,
                        }}
                      >
                        {item.count}
                      </span>
                      <span className="text-[13px]" style={{ color: theme.g700 }}>
                        {item.label}
                      </span>
                    </div>
                    {item.amount && (
                      <span
                        className="text-xs font-semibold font-mono-nums"
                        style={{ color: theme.g700 }}
                      >
                        {item.amount}
                      </span>
                    )}
                  </div>
                ))
              )
            ) : (
              (loading && !data) ? (
                <div className="space-y-3">
                  {[...Array(4)].map((_, i) => <Skel key={i} className="h-8" />)}
                </div>
              ) : !data || data.recentEntries.results.length === 0 ? (
                <div className="py-6 text-center text-sm" style={{ color: theme.t3 }}>
                  No recent activity
                </div>
              ) : (
                data.recentEntries.results.slice(0, 5).map((entry, i) => {
                  const statusColor =
                    entry.status === 'posted' ? theme.ok
                    : entry.status === 'draft' ? theme.wr
                    : theme.inf
                  return (
                    <div
                      key={entry.id}
                      className="flex justify-between items-center py-[9px] px-1 cursor-pointer hover:opacity-80 transition-opacity"
                      style={{
                        borderBottom:
                          i < Math.min(data.recentEntries.results.length - 1, 4)
                            ? `1px solid ${theme.g100}`
                            : 'none',
                      }}
                      onClick={() => router.push(`/journal-entries/${entry.id}`)}
                    >
                      <div className="flex items-center gap-2 min-w-0">
                        <CheckCircle2
                          className="w-4 h-4 flex-shrink-0"
                          style={{ color: statusColor }}
                        />
                        <div className="min-w-0">
                          <div className="text-[13px] truncate" style={{ color: theme.g700 }}>
                            {entry.description || entry.journal_type?.replace(/_/g, ' ') || 'Journal Entry'}
                          </div>
                          <div
                            className="text-[11px] font-medium"
                            style={{ color: theme.orangeText }}
                          >
                            {entry.entry_number}
                          </div>
                        </div>
                      </div>
                      <span className="text-[11px] flex-shrink-0 ml-2" style={{ color: theme.t3 }}>
                        {formatDate(entry.entry_date)}
                      </span>
                    </div>
                  )
                })
              )
            )}
          </div>
        </div>

        {/* ─── Row 4: Quick Actions ──────────────────────────────────── */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4 dash-row dash-row-4">
          {[
            {
              icon: Receipt,
              label: 'Record Payment',
              desc: 'Record a customer or vendor payment',
              href: '/quick-entry',
              accent: false,
            },
            {
              icon: FileText,
              label: 'Create Invoice',
              desc: 'Draft a new customer invoice',
              href: '/invoices/new',
              accent: false,
            },
            {
              icon: Upload,
              label: 'Upload Document',
              desc: 'AI-powered document processing',
              href: '/quick-entry?tab=upload',
              accent: true,
            },
            {
              icon: Landmark,
              label: 'Reconcile Bank',
              desc: 'Match bank statement lines',
              href: '/banking',
              accent: false,
            },
          ].map((action) => {
            const Icon = action.icon
            return (
              <Link
                key={action.label}
                href={action.href}
                className="rounded-lg p-4 flex items-center gap-4 transition-all hover:scale-[1.01] group"
                style={{
                  background: action.accent ? theme.oL : theme.card,
                  border: `1px solid ${action.accent ? theme.orange + '40' : theme.cardBdr}`,
                  boxShadow: theme.cardSh,
                }}
              >
                <div
                  className="w-11 h-11 rounded-lg flex items-center justify-center flex-shrink-0"
                  style={{
                    background: action.accent ? theme.orange : theme.g100,
                  }}
                >
                  <Icon
                    className="w-5 h-5"
                    style={{ color: action.accent ? '#FFFFFF' : theme.orange }}
                    strokeWidth={1.5}
                  />
                </div>
                <div className="min-w-0">
                  <p
                    className="text-sm font-semibold group-hover:opacity-80 transition-opacity"
                    style={{ color: action.accent ? theme.orangeText : theme.text }}
                  >
                    {action.label}
                  </p>
                  <p className="text-xs mt-0.5" style={{ color: theme.t3 }}>
                    {action.desc}
                  </p>
                </div>
              </Link>
            )
          })}
        </div>

        {/* ─── Row 5: My Requests (CFO Workstream A, 2026-09-15) ────────
            A summary only — the full list stays at /my-requests so there is
            one place, not two, where a request's status is maintained. */}
        <MyRequestsBlock />

        {/* ─── Row 6: Morning-brief notes (CFO 2026-09-10) ───────────────
            Each card self-gates on what the person may write or read, so
            there is no role check here. */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <BriefNoteCard audience="ceo" />
          <BriefNoteCard audience="cfo" />
        </div>
      </div>

      {/* KPI hover mascot popup */}
      <KpiPopup cardLabel={hoveredKpi} />

      {/* CFO directive 2026-05-22: AriaChat removed from the dashboard.
          The global AriaFloatingA mounted in (dashboard)/layout.tsx is
          the single ARIA text box visible on every page — running two
          chat widgets at once on /dashboard confused the CFO. */}
    </div>
  )
}
