'use client'

/**
 * /budgets/five-year — 5-Year Plan Cockpit, AD Insurtech (CFO 2026-08-03).
 *
 * Requested by Finance (Legakwa Ntabeni) with a build spec and a draft mock:
 * the Alpha Direct Insurtech consolidated plan FY2026–FY2030 on FY2025 audited
 * (Forvis Mazars), in the Budget Cockpit's shape — levers in a sticky bar, six
 * segment ON/OFF switches, and a tab strip over the statements.
 *
 * Two departures from the draft they sent, both deliberate:
 *  1. The draft carried group totals only, so its segment switches could not
 *     really recompute anything. This is built up from per-segment figures
 *     (lib/fiveYearModel.ts) so switching a segment off genuinely removes it
 *     from every KPI, table and gauge.
 *  2. FY2025 is audited. No lever moves it — a slider must never restate an
 *     audited year. Reset returns every figure to the workbook exactly.
 *
 * Aria advises, never computes. Arithmetic lives in the model, not here.
 */

import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { Gauge } from '@/components/budget/Charts'
import {
  YEARS, SEGMENTS, BASE, CONSERVATIVE, AGGRESSIVE, BALANCE_SHEET, CASH_WATERFALL,
  RUNWAY, CAPITAL, USE_OF_FUNDS, COMPARABLES, INVESTOR, PL_STATIC,
  compute, valuation, mn, pct, times, usd, num, cagr,
  type Levers, type SegmentId, type StatementLine,
} from '@/lib/fiveYearModel'
import { RotateCcw, Sparkles, Bot, TrendingUp, AlertTriangle } from 'lucide-react'
import { useCompany } from '@/contexts/CompanyContext'
import { isAdiplPlanVisible, isCompanyResolving } from '@/lib/entityScope'
import { EntityScopeNotice } from '@/components/EntityScopeNotice'

const NAVY = '#0D1B2A', ORANGE = '#B04E00', GREEN = '#2E9E5B', RED = '#D14343'
const SEG_COLOUR: Record<SegmentId, string> = {
  gi: '#0D1B2A', health: '#B04E00', life: '#2E9E5B',
  neobank: '#7A8CC2', sa: '#C0563B', ri: '#5BA88A',
}

const TABS = ['KPIs', 'P&L', 'Balance Sheet', 'Cash Flow', 'By Segment',
              'Funding', 'Valuation', 'Scenarios'] as const
type Tab = typeof TABS[number]

const SLIDERS: {
  key: keyof Levers; label: string; min: number; max: number; step: number
  kind: 'pct' | 'money' | 'count' | 'x'; hint?: string
}[] = [
  { key: 'giGrowth', label: 'GI GWP growth (FY26)', min: 0.05, max: 0.35, step: 0.01, kind: 'pct',
    hint: 'FY26 growth. Later years keep the workbook’s own taper (18/15/13/12%) relative to this.' },
  { key: 'lossRatio', label: 'Gross loss ratio (FY26)', min: 0.40, max: 0.75, step: 0.005, kind: 'pct',
    hint: 'FY26 gross loss ratio. FY27–30 follow the workbook’s glide down to 49%.' },
  { key: 'cession', label: 'RI cession rate (FY26)', min: 0.45, max: 0.75, step: 0.005, kind: 'pct' },
  { key: 'healthMembers', label: 'Health members / month', min: 100, max: 1000, step: 25, kind: 'count' },
  { key: 'fixedOpex', label: 'Fixed OpEx (P Mn, FY26)', min: 35, max: 60, step: 0.1, kind: 'money',
    hint: 'Total payroll + admin from the Assumptions tab. Charged to the group, not one segment.' },
]

const SEEDED_QUESTIONS = [
  'Which segment adds the most PAT by FY30?',
  'What GI growth rate do I need for the group to break P100M EBITDA by FY29?',
  'What happens to the funding need if Health launches a year late?',
  'What is the maximum loss ratio before the group makes a loss?',
]

const fmtLever = (k: keyof Levers, v: number) => {
  const s = SLIDERS.find((x) => x.key === k)
  if (!s) return times(v)
  if (s.kind === 'pct') return pct(v, 0)
  if (s.kind === 'money') return `P${v.toFixed(1)}m`
  return num(v)
}

/** Levers for a scenario slug handed over by the Strategic Plan Library. */
const SCENARIO_LEVERS: Record<string, Levers> = {
  base: BASE, conservative: CONSERVATIVE, aggressive: AGGRESSIVE,
}

/**
 * The plan belongs to AD Insurtech. Hiding the sidebar link is not enough — the
 * route is still reachable by URL, bookmark and the command palette, which is
 * how Finance found it under other entities (bug 2026-08-06). Gating in a
 * wrapper means the cockpit's own hooks never run for the wrong company.
 */
export default function FiveYearCockpitPage() {
  const { selected, selectedId } = useCompany()
  // Until the company is resolved, "unknown" must not read as "allowed" — else
  // the cockpit mounts and fetches under the wrong entity for the length of the
  // companies request, which is the bug all over again.
  if (isCompanyResolving(selectedId, selected)) return null
  if (!isAdiplPlanVisible(selected?.code)) {
    return (
      <>
        <TopBar />
        <EntityScopeNotice
          entityName="Alpha Direct Insurtech"
          activeName={selected?.name}
          what="The 5-Year Plan Cockpit"
        />
      </>
    )
  }
  return <FiveYearCockpit />
}

function FiveYearCockpit() {
  const [levers, setLevers] = useState<Levers>(BASE)
  const [on, setOn] = useState<Set<SegmentId>>(new Set(SEGMENTS.map((s) => s.id)))
  const [tab, setTab] = useState<Tab>('KPIs')
  const [question, setQuestion] = useState('')
  const [fromPack, setFromPack] = useState<string | null>(null)

  // Arriving from the Library's "Open in Cockpit" (/budgets/five-year?pack=…&scenario=…):
  // pre-apply that scenario's levers so the Cockpit opens on the pack the user
  // clicked. Read off window rather than useSearchParams — this is a client page
  // and useSearchParams would force a Suspense boundary at build time.
  useEffect(() => {
    const q = new URLSearchParams(window.location.search)
    const slug = (q.get('scenario') || '').toLowerCase()
    if (slug && SCENARIO_LEVERS[slug]) {
      setLevers(SCENARIO_LEVERS[slug])
      setFromPack(slug)
    }
  }, [])

  const base = useMemo(() => compute(BASE, new Set(SEGMENTS.map((s) => s.id))), [])
  const now = useMemo(() => compute(levers, on), [levers, on])
  const isBase = JSON.stringify(levers) === JSON.stringify(BASE) &&
    on.size === SEGMENTS.length

  const val = useMemo(
    () => valuation(now.revenue[5], levers.revMultiple), [now.revenue, levers.revMultiple])

  const setLever = (k: keyof Levers, v: number) => setLevers((p) => ({ ...p, [k]: v }))
  const toggleSeg = (id: SegmentId) => setOn((prev) => {
    const next = new Set(prev)
    next.has(id) ? next.delete(id) : next.add(id)
    return next
  })

  return (
    <div className="min-h-screen bg-[#F4F5F7] dark:bg-slate-950">
      <TopBar title="5-Year Plan Cockpit" />

      {/* ── lever bar ─────────────────────────────────────────────── */}
      <div className="sticky top-0 z-20 border-b border-slate-200 bg-white/95 backdrop-blur
                      dark:border-slate-800 dark:bg-slate-900/95">
        <div className="mx-auto max-w-[1600px] px-6 py-4">
          <div className="mb-3 flex flex-wrap items-center gap-3">
            <span className="rounded-full px-3 py-1 text-xs font-bold tracking-wide text-white"
                  style={{ background: NAVY }}>AD INSURTECH</span>
            <span className="text-xs text-slate-500 dark:text-slate-400">
              FY2025A – FY2030E · P Mn · BWP
            </span>
            {!isBase && (
              <span className="rounded-full bg-amber-100 px-2.5 py-1 text-xs font-semibold
                               text-amber-900 dark:bg-amber-900/40 dark:text-amber-200">
                scenario active — deltas shown vs base{fromPack ? ` · opened from the Plan Library (${fromPack})` : ''}
              </span>
            )}
            <div className="ml-auto flex items-center gap-2">
              {([['Base', BASE], ['Conservative', CONSERVATIVE], ['Aggressive', AGGRESSIVE]] as const)
                .map(([name, preset]) => {
                  const active = JSON.stringify(levers) === JSON.stringify(preset)
                  return (
                    <button key={name} onClick={() => setLevers(preset as Levers)}
                      className={`rounded-full px-3.5 py-1.5 text-xs font-semibold transition
                        ${active ? 'text-white shadow-sm'
                                 : 'border border-slate-300 text-slate-700 hover:border-slate-400 dark:border-slate-600 dark:text-slate-200'}`}
                      style={active ? { background: NAVY } : undefined}>
                      {name}
                    </button>
                  )
                })}
              <button onClick={() => { setLevers(BASE); setOn(new Set(SEGMENTS.map((s) => s.id))) }}
                title="Return every figure to the workbook"
                className="flex items-center gap-1.5 rounded-full border border-slate-300 px-3 py-1.5
                           text-xs font-semibold text-slate-600 hover:border-slate-400
                           dark:border-slate-600 dark:text-slate-300">
                <RotateCcw size={12} /> Reset
              </button>
            </div>
          </div>

          {/* Label ABOVE the track, value on the same line as the label. The first
              cut put label / track / value / delta all on one row in a 3-column
              grid; at 1440 the value and "vs base" spans ran straight through the
              next column's label ("Gross20%loss ba base tio"). Stacking is both
              legible and gives the track its full width. */}
          <div className="grid gap-x-8 gap-y-3 sm:grid-cols-2 xl:grid-cols-3">
            {SLIDERS.map((s) => {
              const v = levers[s.key]
              const b = BASE[s.key]
              const moved = Math.abs(v - b) > 1e-9
              return (
                <label key={s.key} className="block text-xs" title={s.hint}>
                  <span className="mb-1 flex items-baseline justify-between gap-2">
                    <span className="truncate text-slate-600 dark:text-slate-300">{s.label}</span>
                    <span className="flex shrink-0 items-baseline gap-1.5">
                      <span className="font-semibold tabular-nums text-slate-900 dark:text-slate-100">
                        {fmtLever(s.key, v)}
                      </span>
                      <span className={`text-[10px] ${moved ? 'font-semibold' : 'text-slate-400'}`}
                            style={{ color: moved ? ORANGE : undefined }}>
                        {moved ? `vs ${fmtLever(s.key, b)}` : 'base'}
                      </span>
                    </span>
                  </span>
                  <input type="range" min={s.min} max={s.max} step={s.step} value={v}
                    onChange={(e) => setLever(s.key, Number(e.target.value))}
                    aria-label={s.label}
                    className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-slate-200
                               accent-[#B04E00] dark:bg-slate-700" />
                </label>
              )
            })}
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3
                          dark:border-slate-800">
            <span className="text-xs text-slate-500 dark:text-slate-400">Segments:</span>
            {SEGMENTS.map((s) => {
              const active = on.has(s.id)
              return (
                <button key={s.id} onClick={() => toggleSeg(s.id)}
                  aria-pressed={active}
                  className={`rounded-full border px-3 py-1 text-xs font-semibold transition
                    ${active ? 'text-white' : 'border-slate-300 text-slate-400 line-through dark:border-slate-600'}`}
                  style={active ? { background: SEG_COLOUR[s.id], borderColor: SEG_COLOUR[s.id] } : undefined}>
                  {s.name}
                </button>
              )
            })}
            {on.size < SEGMENTS.length && (
              <span className="ml-1 text-xs font-medium" style={{ color: ORANGE }}>
                {SEGMENTS.length - on.size} segment{SEGMENTS.length - on.size > 1 ? 's' : ''} off —
                every figure below excludes {SEGMENTS.length - on.size > 1 ? 'them' : 'it'}
              </span>
            )}
          </div>
        </div>
      </div>

      <div className="mx-auto max-w-[1600px] space-y-5 px-6 py-6">

        {/* ── ask the plan ───────────────────────────────────────── */}
        <section className="rounded-xl border border-slate-200 bg-white p-4
                            dark:border-slate-800 dark:bg-slate-900">
          <div className="flex flex-wrap items-center gap-3">
            <span className="flex items-center gap-1.5 text-sm font-semibold"
                  style={{ color: ORANGE }}>
              <Sparkles size={15} /> Ask the plan
            </span>
            <span className="text-[11px] text-slate-500 dark:text-slate-400">
              Aria · advisory only, never changes the numbers
            </span>
            <input value={question} onChange={(e) => setQuestion(e.target.value)}
              placeholder="e.g. Which segment adds the most PAT by FY30?"
              aria-label="Ask the plan"
              className="min-w-[280px] flex-1 rounded-lg border border-slate-200 px-3 py-2 text-sm
                         dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100" />
            <button className="flex items-center gap-1.5 rounded-lg px-4 py-2 text-sm font-semibold text-white"
                    style={{ background: NAVY }}>
              <Bot size={14} /> Ask
            </button>
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            {SEEDED_QUESTIONS.map((q) => (
              <button key={q} onClick={() => setQuestion(q)}
                className="rounded-full border border-slate-200 px-3 py-1 text-xs text-slate-600
                           hover:border-slate-400 dark:border-slate-700 dark:text-slate-300">
                {q}
              </button>
            ))}
          </div>
        </section>

        {/* ── tabs ───────────────────────────────────────────────── */}
        <nav className="flex flex-wrap gap-1 rounded-xl border border-slate-200 bg-white p-1.5
                        dark:border-slate-800 dark:bg-slate-900" role="tablist">
          {TABS.map((t) => (
            <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
              className={`rounded-lg px-4 py-2 text-sm font-semibold transition
                ${tab === t ? 'text-white' : 'text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800'}`}
              style={tab === t ? { background: NAVY } : undefined}>
              {t}
            </button>
          ))}
        </nav>

        {tab === 'KPIs' && <KpiTab now={now} base={base} isBase={isBase} />}
        {tab === 'P&L' && <PlTab now={now} />}
        {tab === 'Balance Sheet' && <StatementTab lines={BALANCE_SHEET} years={YEARS}
          title="Consolidated Statement of Financial Position"
          footnote="Workbook’s Consolidated BS. The balance check row is the workbook’s own — it must always read zero. Cash is disclosed on its own line, as in the Budget Cockpit." />}
        {tab === 'Cash Flow' && <CashTab />}
        {tab === 'By Segment' && <SegmentTab now={now} base={base} />}
        {tab === 'Funding' && <FundingTab />}
        {tab === 'Valuation' && <ValuationTab now={now} levers={levers} setLever={setLever} val={val} />}
        {tab === 'Scenarios' && <ScenariosTab levers={levers} on={on} />}

        <Footnote />
      </div>
    </div>
  )
}

// ── shared pieces ─────────────────────────────────────────────────────

function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800
                     dark:bg-slate-900 ${className}`}>{children}</div>
  )
}

function SectionTitle({ children }: { children: ReactNode }) {
  return <h2 className="mb-3 text-sm font-bold tracking-wide text-slate-900 dark:text-slate-100">{children}</h2>
}

/** A figure with its "vs base" delta underneath — the spec asks for these everywhere. */
function Kpi({ label, value, sub, delta, accent }: {
  label: string; value: string; sub?: string; delta?: number; accent?: boolean
}) {
  return (
    <Card className={accent ? 'border-t-[3px]' : ''}>
      <div className="text-[11px] uppercase tracking-wide text-slate-500 dark:text-slate-400">{label}</div>
      <div className="mt-1 text-2xl font-bold tabular-nums text-slate-900 dark:text-slate-50">{value}</div>
      {sub && <div className="mt-0.5 text-[11px] text-slate-500 dark:text-slate-400">{sub}</div>}
      {delta !== undefined && Math.abs(delta) > 0.05 && (
        <div className="mt-1 text-[11px] font-semibold tabular-nums"
             style={{ color: delta >= 0 ? GREEN : RED }}>
          {delta >= 0 ? '▲' : '▼'} {Math.abs(delta).toFixed(1)} vs base
        </div>
      )}
    </Card>
  )
}

function KpiTab({ now, base, isBase }: { now: any; base: any; isBase: boolean }) {
  const gauges = [
    { label: 'Gross loss ratio (FY30)', frac: now.lossRatio[5], text: pct(now.lossRatio[5]), colour: ORANGE, fy25: pct(now.lossRatio[0]) },
    { label: 'Cession rate (FY30)', frac: now.cession[5], text: pct(now.cession[5]), colour: NAVY, fy25: pct(now.cession[0]) },
    { label: 'Expense ratio (FY30)', frac: now.expenseRatio[5], text: pct(now.expenseRatio[5]), colour: GREEN, fy25: pct(now.expenseRatio[0]) },
    { label: 'EBITDA margin (FY30)', frac: Math.max(0, now.ebitdaMargin[5]), text: pct(now.ebitdaMargin[5]), colour: GREEN, fy25: pct(now.ebitdaMargin[0]) },
    { label: 'GI combined ratio (FY30)', frac: Math.min(1.2, now.combinedRatio[5]) / 1.2, text: pct(now.combinedRatio[5]), colour: ORANGE, fy25: pct(now.combinedRatio[0]) },
    { label: 'Solvency ratio (FY30)', frac: Math.min(1, now.solvency[5] / 12), text: times(now.solvency[5]), colour: GREEN, fy25: `${times(now.solvency[0])} · min 1.00x` },
  ]
  const summary: { label: string; values: number[]; bold?: boolean; fmt?: 'mn' | 'pct' | 'x' }[] = [
    { label: 'Group revenue', values: now.revenue },
    { label: 'Gross profit', values: now.grossProfit },
    { label: 'EBITDA', values: now.ebitda, bold: true },
    { label: 'NPAT', values: now.pat, bold: true },
    { label: 'EBITDA margin', values: now.ebitdaMargin, fmt: 'pct' },
    { label: 'Gross loss ratio (GI)', values: now.lossRatio, fmt: 'pct' },
    { label: 'Solvency ratio', values: now.solvency, fmt: 'x' },
    { label: 'Closing cash', values: now.cash },
    { label: 'Total assets', values: now.assets },
  ]
  return (
    <>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        <Kpi accent label="FY30 group revenue" value={`P${mn(now.revenue[5])}m`}
          sub={cagr(now.revenue) ? `CAGR FY25→FY30: ${pct(cagr(now.revenue)!)}` : undefined}
          delta={(now.revenue[5] - base.revenue[5]) / 1e6} />
        <Kpi accent label="FY30 group EBITDA" value={`P${mn(now.ebitda[5])}m`}
          sub={`vs FY25: ${now.ebitda[5] >= now.ebitda[0] ? '+' : ''}P${mn(now.ebitda[5] - now.ebitda[0])}m`}
          delta={(now.ebitda[5] - base.ebitda[5]) / 1e6} />
        <Kpi accent label="FY30 group NPAT" value={`P${mn(now.pat[5])}m`}
          sub={`Margin: ${pct(now.revenue[5] ? now.pat[5] / now.revenue[5] : 0)}`}
          delta={(now.pat[5] - base.pat[5]) / 1e6} />
        <Kpi label="5-yr cumulative NPAT" value={`P${mn(now.cumulativePat)}m`} sub="FY26–FY30 sum"
          delta={(now.cumulativePat - base.cumulativePat) / 1e6} />
        <Kpi label="Total lives impacted (FY30)" value={num(now.lives[5])}
          sub={`vs FY25: ${num(now.lives[0])}`} />
        <Kpi label="Investor capital required" value={usd(INVESTOR.capitalUsd)}
          sub={`BWP ${mn(now.fundingNeed)}m total injections`} />
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        {gauges.map((g) => (
          <Card key={g.label}>
            <div className="mb-2 text-center text-[11px] text-slate-500 dark:text-slate-400">{g.label}</div>
            <div className="flex justify-center">
              <Gauge frac={Math.max(0, Math.min(1, g.frac))} color={g.colour} size={104}>
                <span className="text-lg font-bold tabular-nums text-slate-900 dark:text-slate-50">{g.text}</span>
              </Gauge>
            </div>
            <div className="mt-2 text-center text-[10px] text-slate-500 dark:text-slate-400">
              FY25 actual: {g.fy25}
            </div>
          </Card>
        ))}
      </div>

      <Card>
        <SectionTitle>5-year summary (P Mn)</SectionTitle>
        <YearTable rows={summary} />
      </Card>
    </>
  )
}

/** The one table shape used by every statement tab — FY columns + CAGR. */
function YearTable({ rows, years = YEARS, showCagr = true }: {
  rows: { label: string; values: number[]; bold?: boolean; fmt?: 'mn' | 'pct' | 'x'; indent?: boolean; note?: string }[]
  years?: readonly string[]
  showCagr?: boolean
}) {
  const cell = (v: number, fmt?: string) => {
    if (fmt === 'pct') return pct(v)
    if (fmt === 'x') return times(v)
    return mn(v)
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr style={{ background: NAVY }} className="text-white">
            <th className="rounded-l-md px-3 py-2 text-left font-semibold">Line</th>
            {years.map((y) => (
              <th key={y} className="px-3 py-2 text-right font-semibold tabular-nums">{y}</th>
            ))}
            {showCagr && <th className="rounded-r-md px-3 py-2 text-right font-semibold">CAGR</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => {
            if (!r.values.length) return (
              <tr key={r.label + i}>
                <td colSpan={years.length + (showCagr ? 2 : 1)}
                    className="px-3 pb-1 pt-3 text-[11px] font-bold uppercase tracking-wide
                               text-slate-500 dark:text-slate-400">{r.label}</td>
              </tr>
            )
            const c = r.fmt ? null : cagr(r.values)
            return (
              <tr key={r.label + i}
                  className={`border-b border-slate-100 dark:border-slate-800
                    ${r.bold ? 'bg-slate-50 font-bold dark:bg-slate-800/50' : ''}`}>
                <td className={`px-3 py-1.5 text-slate-800 dark:text-slate-200 ${r.indent ? 'pl-7' : ''}`}>
                  {r.label}
                  {r.note && <span className="ml-2 text-[10px] text-slate-400">{r.note}</span>}
                </td>
                {r.values.map((v, j) => (
                  <td key={j} className="px-3 py-1.5 text-right tabular-nums"
                      style={{ color: v < 0 ? RED : undefined }}>
                    {cell(v, r.fmt)}
                  </td>
                ))}
                {showCagr && (
                  <td className="px-3 py-1.5 text-right tabular-nums text-slate-500 dark:text-slate-400">
                    {c !== null ? pct(c) : '—'}
                  </td>
                )}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function PlTab({ now }: { now: any }) {
  const rows = [
    { label: 'A. Gross revenue', values: [] as number[] },
    ...now.bySegment.map((s: any) => ({ label: s.name, values: s.revenue, indent: true })),
    { label: 'Total group gross revenue', values: now.revenue, bold: true },
    { label: 'B. Insurance expenses & claims', values: [] as number[] },
    { label: 'Total insurance expenses & claims', values: PL_STATIC.insuranceExpenses, bold: true },
    { label: 'C. Commission & fee income', values: [] as number[] },
    { label: 'Total commission & fee income', values: PL_STATIC.commissionFeeIncome, bold: true },
    { label: 'GROUP GROSS PROFIT', values: now.grossProfit, bold: true },
    { label: 'D. Operating expenses', values: [] as number[] },
    { label: 'Total operating expenses', values: PL_STATIC.operatingExpenses, bold: true },
    { label: 'GROUP EBITDA', values: now.ebitda, bold: true },
    { label: 'E. NPAT by segment', values: [] as number[] },
    ...now.bySegment.map((s: any) => ({ label: s.name, values: s.pat, indent: true })),
    { label: 'GROUP NPAT', values: now.pat, bold: true },
  ]
  return (
    <Card>
      <SectionTitle>Consolidated income statement (P Mn)</SectionTitle>
      <YearTable rows={rows} />
      <p className="mt-3 text-[11px] leading-relaxed text-slate-500 dark:text-slate-400">
        FY25 = audited, Forvis Mazars · FY26+ per the Assumptions tab of
        AD_Insurtech_5Yr_Model_CONSOLIDATED.xlsx · segment tax rates as per workbook
        (GI 15%, others 22%) · negatives in red, subtotals in bold.
        Insurance expenses, commission income and operating expenses are group lines in
        the workbook and are shown as stated; the segment lines above and below them are
        what the segment switches move.
      </p>
    </Card>
  )
}

function StatementTab({ lines, years, title, footnote }: {
  lines: StatementLine[]; years: readonly string[]; title: string; footnote: string
}) {
  return (
    <Card>
      <SectionTitle>{title}</SectionTitle>
      <YearTable rows={lines} years={years} showCagr={false} />
      <p className="mt-3 text-[11px] leading-relaxed text-slate-500 dark:text-slate-400">{footnote}</p>
    </Card>
  )
}

const FY26_30 = ['FY2026E', 'FY2027E', 'FY2028E', 'FY2029E', 'FY2030E'] as const

function CashTab() {
  return (
    <>
      <Card>
        <SectionTitle>Group cash waterfall (P Mn)</SectionTitle>
        <YearTable rows={CASH_WATERFALL} years={FY26_30} showCagr={false} />
      </Card>
      <Card>
        <SectionTitle>Cash runway</SectionTitle>
        <YearTable
          rows={RUNWAY.map((r) => ({ ...r, fmt: r.label.includes('Runway') ? undefined : undefined }))}
          years={FY26_30} showCagr={false} />
        <p className="mt-3 flex items-start gap-2 rounded-lg bg-amber-50 p-3 text-[11px]
                      leading-relaxed text-amber-900 dark:bg-amber-900/20 dark:text-amber-200">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          <span>
            FY26 closing cash is negative (−P1.2m) and runway is under a month before the
            first investor tranche. The plan only clears the P5m minimum buffer from FY28.
            This is the workbook’s own position, shown as stated.
          </span>
        </p>
      </Card>
      <Card>
        <SectionTitle>NBFIRA capital &amp; solvency (P Mn)</SectionTitle>
        <YearTable rows={CAPITAL} showCagr={false} />
        <p className="mt-3 text-[11px] text-slate-500 dark:text-slate-400">
          Required capital = 25% of NEP for general insurance + 5% of GWP for healthcare,
          per the Capital Requirements sheet. Solvency = available ÷ required capital;
          NBFIRA minimum 1.00x.
        </p>
      </Card>
    </>
  )
}

function SegmentTab({ now, base }: { now: any; base: any }) {
  const maxCum = Math.max(...now.bySegment.map((s: any) => Math.abs(s.cumPat)), 1)
  return (
    <>
      <Card>
        <SectionTitle>Revenue by segment (P Mn)</SectionTitle>
        <YearTable rows={now.bySegment.map((s: any) => ({ label: s.name, values: s.revenue }))} />
      </Card>
      <Card>
        <SectionTitle>Net profit by segment (P Mn)</SectionTitle>
        <YearTable rows={now.bySegment.map((s: any) => ({ label: s.name, values: s.pat }))} />
      </Card>
      <Card>
        <SectionTitle>Where the profit comes from — 5-year cumulative NPAT</SectionTitle>
        <div className="space-y-2">
          {now.bySegment.map((s: any) => {
            const w = (Math.abs(s.cumPat) / maxCum) * 100
            const neg = s.cumPat < 0
            return (
              <div key={s.id} className="flex items-center gap-3 text-sm">
                <span className="w-40 shrink-0 text-slate-700 dark:text-slate-300">{s.name}</span>
                <div className="relative h-5 flex-1 rounded bg-slate-100 dark:bg-slate-800">
                  <div className="absolute inset-y-0 rounded"
                       style={{ width: `${w}%`, background: neg ? RED : SEG_COLOUR[s.id as SegmentId] }} />
                </div>
                <span className="w-24 shrink-0 text-right font-semibold tabular-nums"
                      style={{ color: neg ? RED : undefined }}>
                  P{mn(s.cumPat)}m
                </span>
              </div>
            )
          })}
        </div>
        <p className="mt-3 text-[11px] text-slate-500 dark:text-slate-400">
          NeoBank is the only segment cumulatively loss-making across the plan — it is the
          venture the investor tranches largely fund. Switch it off in the bar above to see
          the group position without it.
        </p>
      </Card>
    </>
  )
}

function FundingTab() {
  const t = USE_OF_FUNDS
  return (
    <>
      <div className="grid gap-3 md:grid-cols-2">
        {[t.tranche1, t.tranche2].map((tr) => (
          <Card key={tr.when}>
            <div className="mb-2 text-[11px] font-bold uppercase tracking-wide"
                 style={{ color: ORANGE }}>{tr.when}</div>
            <div className="text-2xl font-bold tabular-nums text-slate-900 dark:text-slate-50">
              P{mn(tr.total)}m
            </div>
            <div className="mb-3 text-[11px] text-slate-500 dark:text-slate-400">
              {usd(tr.total / t.rate * 1e6 / 1e6 * 1e6 / 1e6 * 1e6)} at {t.rate.toFixed(1)} BWP/USD
            </div>
            <table className="w-full text-sm">
              <tbody>
                {tr.items.map((it) => (
                  <tr key={it.label} className="border-b border-slate-100 dark:border-slate-800">
                    <td className="py-1.5 pr-2 text-slate-700 dark:text-slate-300">{it.label}</td>
                    <td className="py-1.5 text-right tabular-nums">P{mn(it.bwp)}m</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        ))}
      </div>
      <Card>
        <SectionTitle>Funding gap by year (P Mn) — positive means cash is needed</SectionTitle>
        <YearTable rows={CASH_WATERFALL.filter((l) =>
          ['Investor capital injection', 'CASH BEFORE FUNDING', 'CLOSING CASH',
           'Minimum cash buffer (P5M)', 'FUNDING GAP / (SURPLUS)'].includes(l.label))}
          years={FY26_30} showCagr={false} />
      </Card>
    </>
  )
}

function ValuationTab({ now, levers, setLever, val }: {
  now: any; levers: Levers; setLever: (k: keyof Levers, v: number) => void; val: any
}) {
  const multiples = [2.25, 3.0, 3.5, 4.0, 5.0]
  const revenues = [now.revenue[5] * 0.8, now.revenue[5] * 0.9, now.revenue[5],
                    now.revenue[5] * 1.1, now.revenue[5] * 1.2]
  return (
    <>
      <Card>
        <SectionTitle>Revenue-multiple valuation</SectionTitle>
        <label className="flex items-center gap-3 text-xs">
          <span className="w-40 shrink-0 text-slate-600 dark:text-slate-300">Revenue multiple</span>
          <input type="range" min={2.25} max={5} step={0.05} value={levers.revMultiple}
            onChange={(e) => setLever('revMultiple', Number(e.target.value))}
            aria-label="Revenue multiple"
            className="h-1.5 max-w-md flex-1 cursor-pointer appearance-none rounded-full
                       bg-slate-200 accent-[#B04E00] dark:bg-slate-700" />
          <span className="w-16 text-right font-bold tabular-nums">{times(levers.revMultiple)}</span>
        </label>
        <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Kpi label="Enterprise value (BWP)" value={`P${mn(val.evBwp)}m`} />
          <Kpi label="Enterprise value (USD)" value={usd(val.evUsd)} />
          <Kpi label="Investor share" value={usd(val.investorShareUsd)}
            sub={`${pct(INVESTOR.stake)} stake`} />
          <Kpi label="MOIC · IRR" value={`${val.moic.toFixed(1)}x`}
            sub={`IRR ${pct(val.irr)} · 3-yr hold FY27→FY30`} />
        </div>
      </Card>
      <Card>
        <SectionTitle>Sensitivity — revenue multiple × FY30 revenue (enterprise value, P Mn)</SectionTitle>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr style={{ background: NAVY }} className="text-white">
                <th className="rounded-l-md px-3 py-2 text-left font-semibold">Multiple</th>
                {revenues.map((r, i) => (
                  <th key={i} className="px-3 py-2 text-right font-semibold tabular-nums">
                    P{mn(r)}m
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {multiples.map((m) => (
                <tr key={m} className="border-b border-slate-100 dark:border-slate-800">
                  <td className="px-3 py-1.5 font-semibold">{times(m)}</td>
                  {revenues.map((r, i) => {
                    const mid = i === 2 && Math.abs(m - 3.5) < 0.01
                    return (
                      <td key={i}
                          className={`px-3 py-1.5 text-right tabular-nums ${mid ? 'font-bold' : ''}`}
                          style={mid ? { background: '#FDF6E9' } : undefined}>
                        {mn(r * m)}
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-[11px] text-slate-500 dark:text-slate-400">
          Mid case (3.50x at plan revenue) highlighted. Pre-money USD 40m, post-money USD 45m,
          investor stake {pct(INVESTOR.stake)} on USD 5m.
        </p>
      </Card>
      <Card>
        <SectionTitle>Comparable revenue multiples</SectionTitle>
        <table className="w-full text-sm">
          <tbody>
            {COMPARABLES.map((c) => (
              <tr key={c.name} className="border-b border-slate-100 dark:border-slate-800">
                <td className="py-1.5 pr-3 font-medium text-slate-800 dark:text-slate-200">{c.name}</td>
                <td className="py-1.5 pr-3 tabular-nums" style={{ color: ORANGE }}>{c.multiple}</td>
                <td className="py-1.5 text-slate-500 dark:text-slate-400">{c.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </>
  )
}

function ScenariosTab({ levers, on }: { levers: Levers; on: Set<SegmentId> }) {
  const all = new Set(SEGMENTS.map((s) => s.id))
  const cases = [
    { name: 'Base', levers: BASE }, { name: 'Conservative', levers: CONSERVATIVE },
    { name: 'Aggressive', levers: AGGRESSIVE }, { name: 'Current view', levers },
  ]
  const rows = cases.map((c) => {
    const r = compute(c.levers, c.name === 'Current view' ? on : all)
    return { name: c.name, rev: r.revenue[5], ebitda: r.ebitda[5], pat: r.pat[5],
             cum: r.cumulativePat, lives: r.lives[5], solv: r.solvency[5] }
  })
  // Which lever moves FY30 PAT most — a one-at-a-time sweep, the honest tornado.
  const basePat = compute(BASE, all).pat[5]
  const tornado = SLIDERS.map((s) => {
    const hi = compute({ ...BASE, [s.key]: s.max } as Levers, all).pat[5]
    const lo = compute({ ...BASE, [s.key]: s.min } as Levers, all).pat[5]
    return { label: s.label, swing: Math.abs(hi - lo) }
  }).sort((a, b) => b.swing - a.swing)
  const maxSwing = Math.max(...tornado.map((t) => t.swing), 1)

  return (
    <>
      <Card>
        <SectionTitle>Scenario compare — headline KPIs</SectionTitle>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr style={{ background: NAVY }} className="text-white">
                <th className="rounded-l-md px-3 py-2 text-left font-semibold">Scenario</th>
                {['FY30 revenue', 'FY30 EBITDA', 'FY30 NPAT', '5-yr cum. NPAT', 'Lives (FY30)', 'Solvency'].map((h) => (
                  <th key={h} className="px-3 py-2 text-right font-semibold">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.name}
                    className={`border-b border-slate-100 dark:border-slate-800
                      ${r.name === 'Current view' ? 'bg-amber-50 font-semibold dark:bg-amber-900/20' : ''}`}>
                  <td className="px-3 py-2">{r.name}</td>
                  <td className="px-3 py-2 text-right tabular-nums">P{mn(r.rev)}m</td>
                  <td className="px-3 py-2 text-right tabular-nums">P{mn(r.ebitda)}m</td>
                  <td className="px-3 py-2 text-right tabular-nums"
                      style={{ color: r.pat < 0 ? RED : undefined }}>P{mn(r.pat)}m</td>
                  <td className="px-3 py-2 text-right tabular-nums">P{mn(r.cum)}m</td>
                  <td className="px-3 py-2 text-right tabular-nums">{num(r.lives)}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{times(r.solv)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      <Card>
        <SectionTitle>Which lever moves FY30 NPAT most</SectionTitle>
        <div className="space-y-2">
          {tornado.map((t) => (
            <div key={t.label} className="flex items-center gap-3 text-sm">
              <span className="w-44 shrink-0 text-slate-700 dark:text-slate-300">{t.label}</span>
              <div className="relative h-5 flex-1 rounded bg-slate-100 dark:bg-slate-800">
                <div className="absolute inset-y-0 rounded"
                     style={{ width: `${(t.swing / maxSwing) * 100}%`, background: ORANGE }} />
              </div>
              <span className="w-24 shrink-0 text-right tabular-nums">P{mn(t.swing)}m</span>
            </div>
          ))}
        </div>
        <p className="mt-3 flex items-center gap-1.5 text-[11px] text-slate-500 dark:text-slate-400">
          <TrendingUp size={12} /> Swing in FY30 NPAT from moving each lever across its full
          range, one at a time, with all segments on.
        </p>
      </Card>
    </>
  )
}

function Footnote() {
  return (
    <p className="px-1 text-[11px] leading-relaxed text-slate-500 dark:text-slate-400">
      <strong>Source</strong> AD_Insurtech_5Yr_Model_CONSOLIDATED.xlsx (Finance, 3 Aug 2026).
      FY2025 = audited, Forvis Mazars; FY2026–FY2030 per the workbook’s Assumptions tab,
      implemented as-is. <strong>Definitions</strong> EBITDA margin on group revenue ·
      gross loss ratio = GI segment only · solvency = available ÷ required capital
      (NBFIRA 25% NEP + 5% healthcare) · combined ratio = (net claims + net acquisition) ÷ NEP ·
      expense ratio = OpEx ÷ revenue. <strong>Conventions</strong> P Mn to one decimal,
      negatives in red, bold subtotals, deltas always vs base. Sliders change the scenario
      layer only — Reset returns every figure to the workbook, and FY2025 never moves because
      it is audited. Aria is advisory and never writes to the plan.
      <br />
      <strong>Known workbook variance</strong> the Segment Contribution and Revenue by Segment
      sheets differ by P500,000 on FY26 GI revenue. The Consolidated P&amp;L agrees with Segment
      Contribution, so that is the basis used here. Raised with Finance as one of the
      &ldquo;minor corrections for a later date&rdquo;.
    </p>
  )
}
