'use client'

/**
 * /budgets/simulator — FY2026/27 financial cockpit (CFO 2026-06-28).
 *
 * Prophix-style BI dashboard: KPI ring gauges + every statement visible in a grid
 * (P&L, Balance Sheet, Cash Flow, Reinsurance, Claims, Products, Cash). Drag the
 * four levers in the sticky bar and the whole picture recomputes live. Balance
 * sheet + cash flow are anchored on the MAY-2026 management-accounts actuals.
 * Deterministic engine in lib/budgetModel.ts; Aria advises, never computes.
 */
import { useMemo, useState, type ReactNode } from 'react'
import { useRouter } from 'next/navigation'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { Donut, Legend, HBars, Gauge } from '@/components/budget/Charts'
import {
  type Levers, type CashAssumptions, type Ratios, type MonthlyMetric, BASE, COST_CUT, TARGET, CASH_DEFAULTS,
  FY26_ACTUAL, FY26_FORECAST, TAX_RATE, MONTHS, MUNICH_XL, XL_MDP, compute, buildYears, computeMonthly, computeMonthlyProducts, bsTotals, fmt, pct, signed,
} from '@/lib/budgetModel'
import { askBudgetAdvisor, getToken, type BudgetAdvice } from '@/lib/api'
import { Sparkles, RotateCcw, Loader2, Bot } from 'lucide-react'

// ORANGE doubles as small-text colour + small-button bg — #B04E00 holds ≥4.5:1
// against white in both directions (#F47C20 was 2.99:1, WCAG AA fail).
const NAVY = '#1D3270', ORANGE = '#B04E00', GREEN = '#2E9E5B', RED = '#D14343'
const PIE = ['#1D3270', '#F47C20', '#2E9E5B', '#3B5BA9', '#E8A33D', '#7A8CC2', '#C0563B', '#5BA88A', '#9FB0D8', '#D14343']

const SLIDERS: { key: keyof Levers; label: string; min: number; max: number; step: number; kind: 'money' | 'pct'; hint?: string }[] = [
  { key: 'gwp', label: 'Revenue (GWP)', min: 120, max: 180, step: 0.5, kind: 'money' },
  { key: 'lossRatio', label: 'Loss ratio', min: 0.40, max: 0.75, step: 0.005, kind: 'pct' },
  { key: 'cession', label: 'Reinsurance', min: 0.50, max: 0.85, step: 0.005, kind: 'pct' },
  // Relabelled 'Expenses' → 'Fixed OpEx' (Oprah 2026-06-30): this sets the FIXED
  // operating expense. The P&L Operating Expenses line also carries a 2.5%-of-GWP
  // risk levy that flexes with Revenue, so that line moves with the GWP slider even
  // when this one is fixed — the hint explains it on hover.
  { key: 'opex', label: 'Fixed OpEx', min: 28, max: 50, step: 0.1, kind: 'money',
    hint: 'Sets the FIXED operating expense. The P&L Operating Expenses line also carries a 2.5%-of-GWP risk levy that flexes with Revenue, so that line still moves when you change the Revenue slider — this slider controls only the fixed part.' },
]

// Round monthly cells to 1 dp so they SUM to the rounded FY total on screen
// (largest-remainder) — fixes the "12 × displayed ≠ FY" rounding artifact where a
// flat line (e.g. OpEx 3.225/mo shown 3.2) read 38.4 vs the exact FY 38.7 (Oprah 49853a9c).
function distR(vals: number[], total: number, dp = 1): number[] {
  const k = 10 ** dp
  const r = vals.map(v => Math.round(v * k))
  const diff = Math.round(total * k) - r.reduce((a, b) => a + b, 0)
  const order = vals
    .map((v, i) => ({ i, frac: v * k - Math.round(v * k) }))
    .sort((a, b) => (diff > 0 ? b.frac - a.frac : a.frac - b.frac))
  for (let j = 0; j < Math.abs(diff); j++) r[order[j % order.length].i] += Math.sign(diff)
  return r.map(x => x / k)
}
const NAV = [['kpis', 'KPIs'], ['pnl', 'P&L'], ['bs', 'Balance Sheet'], ['cf', 'Cash Flow'], ['monthly', 'Monthly'], ['monthly-prod', 'By product'], ['reins', 'Reinsurance'], ['products', 'Products']]

export default function BudgetCockpit() {
  const router = useRouter()
  const { theme } = useTheme()
  const [levers, setLevers] = useState<Levers>({ ...BASE })
  const [cash, setCash] = useState<CashAssumptions>({ ...CASH_DEFAULTS })
  const [advice, setAdvice] = useState<BudgetAdvice | null>(null)
  const [advBusy, setAdvBusy] = useState(false)
  const [question, setQuestion] = useState('')
  const [mMetric, setMMetric] = useState<MonthlyMetric>('gwp')
  const [mPct, setMPct] = useState(false)

  if (typeof window !== 'undefined' && !getToken()) { router.replace('/login'); return null }

  const pnl = useMemo(() => compute(levers), [levers])
  const base = useMemo(() => compute(BASE), [])
  // FY27 closing cash for the BASE plan (BASE levers + default cash assumptions) — the
  // reference the FY27-cash KPI delta compares against, so it reads "vs base" like the
  // other three tiles (was wrongly diffing vs prior-year FY26 cash). Fixes Oprah's report.
  const baseYears = useMemo(() => buildYears(BASE, CASH_DEFAULTS), [])
  const years = useMemo(() => buildYears(levers, cash), [levers, cash])
  const r = years.fy27.ratios, f26r = years.fy26.ratios
  const products = years.fy27.products
  const monthly = useMemo(() => computeMonthly(levers), [levers])
  const monthlyProd = useMemo(() => computeMonthlyProducts(levers), [levers])
  // Sensitivity tables for the Ask box: sweep ONE lever across a range holding
  // the others at their current value, recording PAT + EBITDA from the real
  // engine. Handed to the DeepSeek advisor so break-even / what-if questions
  // (e.g. "max loss ratio before I make losses") are answered off true numbers,
  // never the model's own arithmetic.
  const sweeps = useMemo(() => {
    const range = (a: number, b: number, n: number) => Array.from({ length: n }, (_, i) => a + ((b - a) * i) / (n - 1))
    const mk = (key: keyof Levers, vals: number[]) => vals.map(v => {
      const p = compute({ ...levers, [key]: v })
      return { x: +v.toFixed(4), pat: +p.pat.toFixed(2), ebitda: +p.ebitda.toFixed(2) }
    })
    return {
      lossRatio: mk('lossRatio', range(0.40, 0.95, 12)),
      cession:   mk('cession',   range(0.50, 0.90, 9)),
      opex:      mk('opex',      range(25, 55, 13)),
      gwp:       mk('gwp',       range(110, 190, 17)),
    }
  }, [levers])
  const topProducts = [...products.rows].sort((a, b) => b.gwp - a.gwp)
  const bs26 = years.fy26.bs, bs27 = years.fy27.bs
  const t26 = bsTotals(bs26), t27 = bsTotals(bs27)
  const cf = years.fy27.cf

  const card = { background: theme.card, border: `1px solid ${theme.cardBdr}` }
  const setL = (k: keyof Levers, v: number) => setLevers(s => ({ ...s, [k]: v }))
  // NB: behavior:'smooth' silently fails on this nested scroll container — use instant
  // (scroll-mt-24 on each section offsets the sticky bar). Fixes the jump-nav not scrolling.
  const jump = (id: string) => document.getElementById(id)?.scrollIntoView({ block: 'start' })
  const dCol = (d: number, up = true) => (Math.abs(d) < 0.05 ? theme.t2 : (d > 0) === up ? GREEN : RED)

  async function ask(qOverride?: string) {
    const q = (qOverride !== undefined ? qOverride : question).trim()
    setAdvBusy(true)
    try {
      setAdvice(await askBudgetAdvisor({
        levers: { ...levers },
        outputs: { nep: pnl.nep, netClaims: pnl.netClaims, commission: pnl.commission, grossProfit: pnl.grossProfit, ebitda: pnl.ebitda, pat: pnl.pat, nepMargin: pnl.nepMargin, fy27Cash: cf.closingCash, combinedRatio: r.combinedRatio },
        question: q || undefined,
        sweeps,
      }))
    } catch (e) { setAdvice({ ok: false, source: 'error', text: e instanceof Error ? e.message : 'unavailable' }) }
    finally { setAdvBusy(false) }
  }

  // ── helpers ──
  const Card = ({ id, kicker, title, children, className = '' }: { id?: string; kicker: string; title: string; children: ReactNode; className?: string }) => (
    <section id={id} className={`scroll-mt-24 rounded-2xl p-5 ${className}`} style={card}>
      <div className="text-[10px] uppercase tracking-wider font-semibold" style={{ color: ORANGE }}>{kicker}</div>
      <h2 className="text-sm font-bold mb-3 leading-snug" style={{ color: theme.text }}>{title}</h2>
      {children}
    </section>
  )
  const N = ({ v, dp = 1, bold = false, color }: { v: number; dp?: number; bold?: boolean; color?: string }) =>
    <span className="font-mono-nums" style={{ color: color || theme.text, fontWeight: bold ? 700 : 400 }}>{v.toFixed(dp)}</span>
  const StmtRow = ({ label, vals, bold }: { label: string; vals: number[]; bold?: boolean }) => (
    <tr style={{ borderTop: `1px solid ${theme.cardBdr}` }}>
      <td className="py-1 pr-2" style={{ color: theme.text, fontWeight: bold ? 700 : 400 }}>{label}</td>
      {vals.map((v, i) => <td key={i} className="py-1 text-right"><N v={v} bold={bold} color={bold ? theme.text : (v < 0 ? RED : theme.t2)} /></td>)}
    </tr>
  )

  const f26 = years.fy26.pnl, fc26 = FY26_FORECAST
  const plRows: [string, number, number, number, boolean?][] = [
    ['Gross Written Premium', f26.gwp, fc26.gwp, pnl.gwp],
    ['Premiums Ceded to RI', -f26.ceded, -fc26.ceded, -pnl.ceded],
    ['Change in UPR', f26.dUPR, fc26.dUPR, pnl.dUPR],
    ['Net Earned Premium', f26.nep, fc26.nep, pnl.nep, true],
    ['Gross Claims Incurred', -f26.grossClaims, -fc26.grossClaims, -pnl.grossClaims],
    ['Claims Recovered from RI', f26.recovered, fc26.recovered, pnl.recovered],
    ['Net Claims Incurred', -f26.netClaims, -fc26.netClaims, -pnl.netClaims, true],
    ['Commission from RI', f26.commission, fc26.commission, pnl.commission],
    ['Acquisition Costs', -f26.acq, -fc26.acq, -pnl.acq],
    ['Gross Profit', f26.grossProfit, fc26.grossProfit, pnl.grossProfit, true],
    ['Other Income', f26.otherIncome, fc26.otherIncome, pnl.otherIncome],
    ['Operating Expenses', -f26.opex, -fc26.opex, -pnl.opex],
    ['EBITDA', f26.ebitda, fc26.ebitda, pnl.ebitda, true],
    ['Provisions', 0, -fc26.provisions, -pnl.provisions],   // FY26 actual: provisions within EBITDA
    ['Depreciation', -f26.deprec, -fc26.deprec, -pnl.deprec],
    ['EBIT', f26.ebit, fc26.ebit, pnl.ebit, true],
    ['Finance income/(cost)', f26.financeCost, fc26.financeCost, pnl.financeCost],
    ['Profit before tax', f26.pbt, fc26.pbt, pnl.pbt, true],
    ['Taxation (15%)', -f26.tax, -fc26.tax, -pnl.tax],
    ['PAT', f26.pat, fc26.pat, pnl.pat, true],
  ]
  const bsRows: [string, number, number, boolean?][] = [
    ['Cash & bank', bs26.cash, bs27.cash],
    ['Trade & other receivables', bs26.tradeRec + bs26.otherRec, bs27.tradeRec + bs27.otherRec],
    ['Reinsurance recovery assets', bs26.riProvisions, bs27.riProvisions],
    ['Subrogation & salvage', bs26.subrogation + bs26.salvage, bs27.subrogation + bs27.salvage],
    ['Related-party, PPE, deferred tax', bs26.relatedParty + bs26.staffLoans + bs26.deferredTax + bs26.netPPE + bs26.rou, bs27.relatedParty + bs27.staffLoans + bs27.deferredTax + bs27.netPPE + bs27.rou],
    ['Total assets', t26.assets, t27.assets, true],
    ['Unearned premium reserve', bs26.upr, bs27.upr],
    ['Claims payable', bs26.claimsPayable, bs27.claimsPayable],
    ['IBNR (BS)', bs26.ibnr, bs27.ibnr],
    ['Reinsurer & trade payables', bs26.dueToRI + bs26.tradePayables, bs27.dueToRI + bs27.tradePayables],
    ['Other payables & tax', bs26.wht + bs26.vat + bs26.severance + bs26.taxPayable, bs27.wht + bs27.vat + bs27.severance + bs27.taxPayable],
    ['Loans', bs26.shortLoan + bs26.longLoan, bs27.shortLoan + bs27.longLoan],
    ['Vehicle & other leases', bs26.leaseShort + bs26.leaseLong, bs27.leaseShort + bs27.leaseLong],
    ['Total liabilities', t26.liabilities, t27.liabilities, true], ['Total equity', t26.equity, t27.equity, true],
  ]

  // Monthly P&L rows: [label, MonthRow field, show-as-negative, bold]
  const mRows: [string, 'gwp' | 'ceded' | 'nep' | 'grossClaims' | 'recovered' | 'netClaims' | 'commission' | 'netAcq' | 'opex' | 'ebitda' | 'pat', boolean, boolean?][] = [
    ['Gross Written Premium', 'gwp', false],
    ['Premiums Ceded to RI', 'ceded', true],
    ['Net Earned Premium', 'nep', false, true],
    ['Gross Claims Incurred', 'grossClaims', true],
    ['Claims Recovered from RI', 'recovered', false],
    ['Net Claims', 'netClaims', true, true],
    ['Commission from RI', 'commission', false],
    ['Net Acquisition', 'netAcq', false],
    ['Operating Expenses', 'opex', true],
    ['EBITDA', 'ebitda', false, true],
    ['PAT', 'pat', false, true],
  ]
  // Monthly-by-product metric selector
  const M_METRICS: [MonthlyMetric, string][] = [['gwp', 'GWP'], ['ceded', 'Ceded to RI'], ['grossClaims', 'Gross Claims'], ['recovered', 'Claims Recovered'], ['netClaims', 'Net Claims'], ['commission', 'Commission']]
  const mMetricLabel = M_METRICS.find(([k]) => k === mMetric)![1]

  const gauges = [
    { label: 'Gross Loss Ratio', val: r.grossLossRatio, cmp: f26r.grossLossRatio, max: 1, up: false, f: (v: number) => pct(v, 0) },
    { label: 'Cession Ratio', val: r.cession, cmp: f26r.cession, max: 1, up: false, f: (v: number) => pct(v, 0) },
    { label: 'Expense Ratio', val: r.expenseRatio, cmp: f26r.expenseRatio, max: 0.5, up: false, f: (v: number) => pct(v, 0) },
    { label: 'EBITDA Margin', val: r.ebitdaMarginGwp, cmp: f26r.ebitdaMarginGwp, max: 0.15, up: true, f: (v: number) => pct(v, 1) },
    { label: 'Combined Ratio', val: r.combinedRatio, cmp: f26r.combinedRatio, max: 1.2, up: false, f: (v: number) => pct(v, 0) },
    { label: 'Leverage (L/E)', val: r.dToE, cmp: f26r.dToE, max: 3, up: false, f: (v: number) => v.toFixed(2) + 'x' },
  ]
  const tiles = [
    { l: 'GWP', v: pnl.gwp, b: base.gwp }, { l: 'EBITDA', v: pnl.ebitda, b: base.ebitda },
    { l: 'PAT', v: pnl.pat, b: base.pat }, { l: 'FY27 cash', v: cf.closingCash, b: baseYears.fy27.cf.closingCash },
  ]
  const cashTrend = [{ y: 'FY25', cash: 8.4 }, { y: 'FY26 (act.)', cash: bs26.cash }, { y: 'FY27 (fcast)', cash: bs27.cash }]

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar title="Budget Cockpit" breadcrumbs={[{ label: 'Accounting' }, { label: 'Budget Library' }, { label: 'FY27 Cockpit' }]} />

      {/* sticky lever + nav bar */}
      <div className="sticky top-0 z-20 px-5 py-2.5 flex items-center gap-4 flex-wrap" style={{ background: theme.card, borderBottom: `1px solid ${theme.cardBdr}` }}>
        {SLIDERS.map(s => (
          <div key={s.key} className="flex items-center gap-2" title={s.hint}>
            <span className="text-[11px] font-medium" style={{ color: theme.t2 }}>{s.label}</span>
            <input type="range" aria-label={s.label} min={s.min} max={s.max} step={s.step} value={levers[s.key]} onChange={e => setL(s.key, parseFloat(e.target.value))} style={{ accentColor: ORANGE, width: 84 }} />
            <span className="text-[11px] font-bold font-mono-nums w-12" style={{ color: ORANGE }}>{s.kind === 'pct' ? pct(levers[s.key], 0) : fmt(levers[s.key])}</span>
          </div>
        ))}
        <div className="flex items-center gap-1.5 ml-auto">
          <span className="text-[11px] font-medium" style={{ color: theme.t2 }}>Collections</span>
          <select aria-label="Collections improvement" value={cash.collections} onChange={e => setCash(c => ({ ...c, collections: parseFloat(e.target.value) }))}
                  className="text-[11px] px-2 py-1 rounded-md font-semibold outline-none cursor-pointer"
                  style={{ background: cash.collections ? GREEN : theme.g100, color: cash.collections ? '#fff' : theme.text, border: `1px solid ${theme.cardBdr}` }}>
            <option value={0}>None</option>
            <option value={0.1}>+10% improvement</option>
            <option value={0.2}>+20% improvement</option>
            <option value={0.3}>+30% improvement</option>
          </select>
          <button onClick={() => { setLevers({ ...BASE }); setCash({ ...CASH_DEFAULTS }) }} className="text-[11px] px-2 py-1 rounded-md font-semibold" style={{ background: theme.g100, color: theme.text }}>Base</button>
          <button onClick={() => setLevers({ ...COST_CUT })} className="text-[11px] px-2 py-1 rounded-md font-semibold" style={{ background: theme.g100, color: theme.text }}>Cost-cut</button>
          <button onClick={() => { setLevers({ ...BASE }); setCash({ ...CASH_DEFAULTS }) }} title="Reset" className="text-[11px] px-1.5 py-1 rounded-md" style={{ background: theme.g100, color: theme.t2 }}><RotateCcw className="w-3.5 h-3.5" /></button>
        </div>
        <div className="flex items-center gap-1 w-full overflow-x-auto">
          {NAV.map(([id, lbl]) => <button key={id} onClick={() => jump(id)} className="text-[11px] px-2 py-0.5 rounded whitespace-nowrap" style={{ color: theme.t2 }}>{lbl}</button>)}
        </div>
      </div>

      {/* section, not <main> — the dashboard layout already provides the page's
          single <main> landmark; nesting a second one fails axe landmark rules */}
      <section className="flex-1 overflow-y-auto p-5 space-y-5">
        {/* Ask box — free-text question about the LIVE scenario, answered by DeepSeek
            (Aria). Advisory only: reads the levers + engine outputs + sensitivity
            tables, never changes a number. */}
        <div className="rounded-2xl p-4" style={card}>
          <div className="flex items-center gap-2 mb-2 flex-wrap">
            <Sparkles className="w-4 h-4" style={{ color: ORANGE }} />
            <div className="text-sm font-bold" style={{ color: theme.text }}>Ask the budget</div>
            <span className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: theme.g100, color: theme.g700 }}>Aria · DeepSeek · advisory only, never changes the numbers</span>
          </div>
          <div className="flex gap-2">
            <input
              value={question}
              onChange={e => setQuestion(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && !advBusy && question.trim()) ask() }}
              placeholder="e.g. What is the maximum loss ratio before I make losses?"
              className="flex-1 rounded-lg px-3 py-2 text-sm outline-none"
              style={{ background: theme.g100, color: theme.text, border: `1px solid ${theme.cardBdr}` }}
            />
            <button onClick={() => ask()} disabled={advBusy || !question.trim()}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold shrink-0"
              style={{ background: ORANGE, color: '#fff', opacity: (advBusy || !question.trim()) ? 0.6 : 1 }}>
              {advBusy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />} Ask
            </button>
          </div>
          <div className="flex flex-wrap gap-1.5 mt-2">
            {[
              'What is the maximum loss ratio before I make losses?',
              'How much can I cut OpEx before EBITDA turns negative?',
              'What GWP do I need for P1m PAT at the current loss ratio?',
            ].map(ex => (
              <button key={ex} onClick={() => { setQuestion(ex); ask(ex) }} disabled={advBusy}
                className="text-[11px] px-2 py-1 rounded-md" style={{ background: theme.g100, color: theme.g700, opacity: advBusy ? 0.6 : 1 }}>
                {ex}
              </button>
            ))}
          </div>
          {advice && (
            <div className="mt-3 text-sm whitespace-pre-line rounded-lg p-3" style={{ background: theme.g100, color: theme.text }}>
              {advice.text}
              <div className="text-[11px] mt-2" style={{ color: theme.t2 }}>source: {advice.source}</div>
            </div>
          )}
        </div>

        {/* headline tiles */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {tiles.map(t => (
            <div key={t.l} className="rounded-2xl p-4" style={card}>
              <div className="text-[11px] uppercase tracking-wide" style={{ color: theme.t2 }}>{t.l}</div>
              <div className="text-2xl font-bold mt-1" style={{ color: t.v < 0 ? RED : theme.text }}>{fmt(t.v)}</div>
              <div className="text-xs mt-0.5" style={{ color: dCol(t.v - t.b) }}>{signed(t.v - t.b)} vs base</div>
            </div>
          ))}
        </div>

        {/* KPI gauges */}
        <div id="kpis" className="scroll-mt-24 grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
          {gauges.map(g => {
            const better = g.up ? g.val >= g.cmp : g.val <= g.cmp
            const col = Math.abs(g.val - g.cmp) < 0.005 ? NAVY : better ? GREEN : ORANGE
            return (
              <div key={g.label} className="rounded-2xl p-3 flex flex-col items-center" style={card}>
                <div className="text-[11px] font-semibold text-center mb-1" style={{ color: theme.text }}>{g.label}</div>
                <Gauge frac={g.val / g.max} color={col} size={104}>
                  <span className="text-lg font-bold" style={{ color: theme.text }}>{g.f(g.val)}</span>
                </Gauge>
                <div className="text-[10px] mt-1" style={{ color: theme.t2 }}>FY26 actual: {g.f(g.cmp)}</div>
              </div>
            )
          })}
        </div>
        <p className="text-[10px] -mt-2" style={{ color: theme.t2 }}>
          Formulae (same basis both years): Combined ratio = (net claims − net acquisition income + opex + deprec) ÷ NEP · Expense ratio = (opex + deprec) ÷ GWP (per the MA) · Loss/Cession/EBITDA-margin on GWP (robust to the high cession) · Leverage = total liabilities ÷ equity — includes UPR/IBNR policyholder reserves, so it is a leverage ratio, not financial-debt gearing (debt-only L/E is far lower).
        </p>

        {/* P&L — full width, FY26 actual + FY26 forecast + FY27 */}
        <Card id="pnl" kicker="Profit & Loss · P Mn" title={`FY27 EBITDA ${fmt(pnl.ebitda)} · PBT ${fmt(pnl.pbt)} · PAT (after 15% tax) ${fmt(pnl.pat)}`}>
          <table className="w-full text-xs">
            <thead><tr className="text-[10px] uppercase" style={{ color: theme.t2 }}>
              <th className="text-left pb-1">Line</th><th className="text-right pb-1">FY26 actual (11mo)</th>
              <th className="text-right pb-1">FY26 forecast (FY)</th><th className="text-right pb-1">FY27 budget</th></tr></thead>
            <tbody>{plRows.map(([l, a, b, c, bold]) => <StmtRow key={l as string} label={l as string} vals={[a as number, b as number, c as number]} bold={bold as boolean} />)}</tbody>
          </table>
          <p className="text-[10px] mt-1.5" style={{ color: theme.t2 }}>FY26 actual = May-2026 MA (11-month YTD); FY26 forecast = the team's full-year forecast (GWP P133.1m). Change in UPR = prior-year unearned premium earned in-year. FY26 actual reports provisions within EBITDA (MA basis); FY26 forecast + FY27 show them below EBITDA (Kago basis). FY27 operating expenses include a ring-fenced <b>P0.5m AI &amp; software subscriptions provision</b> (CFO 2026-07-13), on top of Kago&rsquo;s workbook opex. Both FY26 columns include a <b>P3m bad-debts provision</b> (CFO 2026-07-03) — 11-month actual within EBITDA, full-year forecast in Provisions — bringing FY26 PAT to ~P1.3m (actual) / ~P1.7m (forecast). Tax computed at a 15% flat effective rate on the CURRENT year (FY27, CFO basis): pre-tax P{pnl.pbt.toFixed(1)}m &rarr; after 15% tax ~P{(pnl.pbt*(1-TAX_RATE)).toFixed(1)}m. FY26 shown nil (assessed-loss basis).</p>
        </Card>

        {/* BS + Cash flow */}
        <div className="grid lg:grid-cols-2 gap-5">
          <Card id="bs" kicker="Balance Sheet · P Mn" title={`FY27 assets ${fmt(t27.assets)} · always balances`}>
            <table className="w-full text-xs">
              <thead><tr className="text-[10px] uppercase" style={{ color: theme.t2 }}><th className="text-left pb-1">Line</th><th className="text-right pb-1">FY26 act</th><th className="text-right pb-1">FY27</th></tr></thead>
              <tbody>{bsRows.map(([l, a, b, bold]) => <StmtRow key={l as string} label={l as string} vals={[a as number, b as number]} bold={bold as boolean} />)}</tbody>
            </table>
            <p className="text-[10px] mt-1.5" style={{ color: theme.t2 }}>FY26 = May-2026 management accounts (actual). FY27 rolled off it; cash is the balancing plug. Receivables grow with GWP (a cash outflow) — the <b>Collections</b> dropdown (top bar) reduces them to show the cash release{cash.collections ? ` — currently +${(cash.collections * 100).toFixed(0)}%` : ''}. Vehicle &amp; other leases shown separately (ESTIMATE — IFRS-16 lease schedule pending from Kago).</p>
          </Card>
          <Card id="cf" kicker="Cash Flow · P Mn · indirect" title={`FY27 cash ${cf.netChange >= 0 ? '+' : ''}${cf.netChange.toFixed(1)} → ${fmt(cf.closingCash)}`}>
            <table className="w-full text-xs"><tbody>
              <StmtRow label="Opening cash (FY26 actual)" vals={[cf.openingCash]} bold />
              {cf.operating.map(x => <StmtRow key={x.label} label={x.label} vals={[x.amount]} />)}
              <StmtRow label="Net operating" vals={[cf.netOperating]} bold />
              {cf.investing.map(x => <StmtRow key={x.label} label={x.label} vals={[x.amount]} />)}
              {cf.financing.map(x => <StmtRow key={x.label} label={x.label} vals={[x.amount]} />)}
              <StmtRow label="Closing cash (FY27)" vals={[cf.closingCash]} bold />
            </tbody></table>
            <p className="text-[10px] mt-1.5" style={{ color: theme.t2 }}>The team's budget had no cash flow. Built off the FY26 actual balance sheet; reconciles to it (operating payables + financing loan/lease movements now split on the BS). Capex defaults to ≈ depreciation (steady-state maintenance, adjustable) + Health BU equipment.</p>
          </Card>
        </div>

        {/* monthly P&L — phased on the team's real revenue ramp (FY27 Revenue MoM tab) */}
        <Card id="monthly" kicker="Monthly P&L · P Mn · phased on the FY27 revenue ramp (not ÷12)"
              title={`Monthly GWP · RI · claims · commission · expenses — full year ties to ${fmt(pnl.gwp)} GWP / ${fmt(pnl.ebitda)} EBITDA / ${fmt(pnl.pat)} PAT`}>
          <div className="overflow-x-auto">
            <table className="text-[11px]" style={{ minWidth: 940 }}>
              <thead><tr className="text-[10px] uppercase" style={{ color: theme.t2 }}>
                <th className="text-left pb-1 pr-2 sticky left-0" style={{ background: theme.card }}>Line</th>
                {monthly.months.map(m => <th key={m.label} className="text-right pb-1 px-1.5 font-medium">{m.label}</th>)}
                <th className="text-right pb-1 pl-2 font-bold" style={{ color: ORANGE }}>FY</th>
              </tr></thead>
              <tbody>
                {mRows.map(([label, key, neg, bold]) => {
                  const sign = neg ? -1 : 1
                  // round the 12 cells so they sum to the displayed FY total (Oprah 49853a9c)
                  const cells = distR(monthly.months.map(m => sign * m[key]), sign * monthly.fy[key])
                  const fyv = sign * monthly.fy[key]
                  return (
                    <tr key={label} style={{ borderTop: `1px solid ${theme.cardBdr}` }}>
                      <td className="py-1 pr-2 sticky left-0" style={{ background: theme.card, color: theme.text, fontWeight: bold ? 700 : 400 }}>{label}</td>
                      {cells.map((v, i) => <td key={i} className="py-1 text-right px-1.5"><N v={v} bold={bold} color={bold ? theme.text : (v < 0 ? RED : theme.t2)} /></td>)}
                      <td className="py-1 text-right pl-2"><N v={fyv} bold color={fyv < 0 ? RED : theme.text} /></td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          <p className="text-[10px] mt-1.5" style={{ color: theme.t2 }}>Phased on the team&rsquo;s <b>FY27 Revenue MoM</b> tab (real ramp — July renewals heavy, Health BU building through the year). Revenue-linked lines (GWP, ceded, claims, commission, acquisition) follow each month&rsquo;s share of the ramp; fixed lines (UPR, opex, provisions, depreciation, tax) spread evenly. Columns sum to the Full-Year budget.</p>
        </Card>

        {/* monthly BY PRODUCT — GWP / RI / gross & net claims / commission per class per month, P Mn or % */}
        <Card id="monthly-prod" kicker={`Monthly · by product · ${mPct ? '% of total' : 'P Mn'}`}
              title={`Monthly ${mMetricLabel} by class${mPct ? ' — % share of each month' : ` — full year ${fmt(monthlyProd.totals.fy[mMetric])}`}`}>
          <div className="flex items-center gap-2 mb-3 flex-wrap">
            <span className="text-[11px] font-medium" style={{ color: theme.t2 }}>Metric</span>
            <select aria-label="Monthly metric" value={mMetric} onChange={e => setMMetric(e.target.value as MonthlyMetric)}
                    className="text-[11px] px-2 py-1 rounded-md font-semibold outline-none cursor-pointer"
                    style={{ background: theme.g100, color: theme.text, border: `1px solid ${theme.cardBdr}` }}>
              {M_METRICS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select>
            <span className="text-[11px] font-medium ml-2" style={{ color: theme.t2 }}>Show</span>
            <div className="flex rounded-md overflow-hidden" style={{ border: `1px solid ${theme.cardBdr}` }}>
              <button onClick={() => setMPct(false)} className="text-[11px] px-2.5 py-1 font-semibold"
                      style={{ background: mPct ? theme.g100 : ORANGE, color: mPct ? theme.text : '#fff' }}>P Mn</button>
              <button onClick={() => setMPct(true)} className="text-[11px] px-2.5 py-1 font-semibold"
                      style={{ background: mPct ? ORANGE : theme.g100, color: mPct ? '#fff' : theme.text }}>%</button>
            </div>
            <span className="text-[10px]" style={{ color: theme.t2 }}>{mPct ? 'each class as a % of the monthly total' : 'absolute P Mn per class per month'}</span>
          </div>
          <div className="overflow-x-auto">
            <table className="text-[11px]" style={{ minWidth: 940 }}>
              <thead><tr className="text-[10px] uppercase" style={{ color: theme.t2 }}>
                <th className="text-left pb-1 pr-2 sticky left-0" style={{ background: theme.card }}>Class</th>
                {MONTHS.map(m => <th key={m} className="text-right pb-1 px-1.5 font-medium">{m}</th>)}
                <th className="text-right pb-1 pl-2 font-bold" style={{ color: ORANGE }}>FY</th>
              </tr></thead>
              <tbody>
                {monthlyProd.rows.map(p => (
                  <tr key={p.key} style={{ borderTop: `1px solid ${theme.cardBdr}` }}>
                    <td className="py-1 pr-2 sticky left-0" style={{ background: theme.card, color: theme.text }}>{p.name}</td>
                    {p.months[mMetric].map((v, i) => <td key={i} className="py-1 text-right px-1.5">{mPct
                      ? <span className="font-mono-nums" style={{ color: theme.t2 }}>{(monthlyProd.totals.months[mMetric][i] ? v / monthlyProd.totals.months[mMetric][i] * 100 : 0).toFixed(1)}%</span>
                      : <N v={v} color={theme.t2} />}</td>)}
                    <td className="py-1 text-right pl-2">{mPct
                      ? <span className="font-mono-nums font-bold" style={{ color: theme.text }}>{(monthlyProd.totals.fy[mMetric] ? p.fy[mMetric] / monthlyProd.totals.fy[mMetric] * 100 : 0).toFixed(1)}%</span>
                      : <N v={p.fy[mMetric]} bold />}</td>
                  </tr>
                ))}
                <tr style={{ borderTop: `2px solid ${theme.cardBdr}` }}>
                  <td className="py-1.5 font-bold sticky left-0" style={{ background: theme.card, color: theme.text }}>{monthlyProd.totals.name}</td>
                  {monthlyProd.totals.months[mMetric].map((v, i) => <td key={i} className="py-1.5 text-right px-1.5">{mPct
                    ? <span className="font-mono-nums font-bold" style={{ color: theme.text }}>100.0%</span>
                    : <N v={v} bold />}</td>)}
                  <td className="py-1.5 text-right pl-2">{mPct
                    ? <span className="font-mono-nums font-bold" style={{ color: ORANGE }}>100.0%</span>
                    : <N v={monthlyProd.totals.fy[mMetric]} bold color={ORANGE} />}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p className="text-[10px] mt-1.5" style={{ color: theme.t2 }}>Each class phased on its <b>own</b> monthly ramp from the workbook&rsquo;s FY27 Revenue MoM tab (Motor front-loaded on July renewals; Health BU builds all year). Every row sums to the class&rsquo;s annual book (= the Reinsurance-by-class table below); the GWP total ties to the {fmt(pnl.gwp)} budget. Toggle <b>%</b> to read each class as a share of the monthly total. Ceded / claims / commission are the <b>proportional-treaty</b> book — the P&amp;L layers XL/CAT + facultative + profit commission on top (see Reinsurance note).</p>
        </Card>

        {/* reinsurance */}
        <Card id="reins" kicker="Reinsurance · P Mn · by class (proportional treaty)"
              title={`${pct(products.totals.ceded / products.totals.gwp, 0)} ceded · commission ${fmt(pnl.commission)} slides with the loss ratio`}>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead><tr className="text-[10px] uppercase" style={{ color: theme.t2 }}>
                {['Class', 'GWP', 'Cess%', 'Ceded', 'Loss%', 'Recov', 'Net clm', 'Comm'].map((h, i) => <th key={h} className={i ? 'text-right pb-1 px-1' : 'text-left pb-1'}>{h}</th>)}
              </tr></thead>
              <tbody>
                {products.rows.map(p => (
                  <tr key={p.key} style={{ borderTop: `1px solid ${theme.cardBdr}` }}>
                    <td className="py-1" style={{ color: theme.text }}>{p.name}</td>
                    <td className="py-1 text-right px-1"><N v={p.gwp} color={theme.t2} /></td>
                    <td className="py-1 text-right px-1"><N v={p.cession * 100} dp={0} color={theme.t2} /></td>
                    <td className="py-1 text-right px-1"><N v={p.ceded} color={theme.t2} /></td>
                    <td className="py-1 text-right px-1"><N v={p.lossRatio * 100} dp={0} color={p.lossRatio > 0.8 ? RED : theme.t2} /></td>
                    <td className="py-1 text-right px-1"><N v={p.recovered} color={theme.t2} /></td>
                    <td className="py-1 text-right px-1"><N v={p.netClaims} color={theme.t2} /></td>
                    <td className="py-1 text-right px-1"><N v={p.commission} color={theme.text} /></td>
                  </tr>
                ))}
                <tr style={{ borderTop: `2px solid ${theme.cardBdr}` }}>
                  <td className="py-1.5 font-bold" style={{ color: theme.text }}>Total (proportional)</td>
                  <td className="py-1.5 text-right px-1"><N v={products.totals.gwp} bold /></td>
                  <td className="py-1.5 text-right px-1"><N v={products.totals.cession * 100} dp={0} bold /></td>
                  <td className="py-1.5 text-right px-1"><N v={products.totals.ceded} bold /></td>
                  <td className="py-1.5 text-right px-1"><N v={products.totals.lossRatio * 100} dp={0} bold /></td>
                  <td className="py-1.5 text-right px-1"><N v={products.totals.recovered} bold /></td>
                  <td className="py-1.5 text-right px-1"><N v={products.totals.netClaims} bold /></td>
                  <td className="py-1.5 text-right px-1"><N v={products.totals.commission} bold /></td>
                </tr>
              </tbody>
            </table>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px]" style={{ color: theme.text }}>
            <span>Proportional recoveries <b className="font-mono-nums">{fmt(pnl.propRecovered ?? 0)}</b></span>
            <span>+ Non-proportional XL/CAT stop-loss <b className="font-mono-nums" style={{ color: (pnl.slRecovered ?? 0) > 0 ? GREEN : theme.t2 }}>{fmt(pnl.slRecovered ?? 0)}</b></span>
            <span>= Total RI recoveries <b className="font-mono-nums">{fmt(pnl.recovered)}</b></span>
            <span style={{ color: theme.t2 }}>· the stop-loss caps the net loss ratio (attaches at 55% on NEP) so RI absorbs a loss-ratio spike</span>
          </div>
          <div className="mt-3">
            <div className="text-[10px] uppercase tracking-wider font-semibold mb-1" style={{ color: ORANGE }}>Non-proportional · Munich Re lead (XL) · 1.7.2026/27</div>
            <table className="w-full text-[11px]">
              <thead><tr className="text-[10px] uppercase" style={{ color: theme.t2 }}>
                <th className="text-left pb-1">Treaty</th><th className="text-right pb-1">Limit xs ded</th><th className="text-right pb-1">EGNPI</th><th className="text-right pb-1">MDP (prem)</th>
              </tr></thead>
              <tbody>
                {MUNICH_XL.map(x => (
                  <tr key={x.cls} style={{ borderTop: `1px solid ${theme.cardBdr}` }}>
                    <td className="py-1" style={{ color: theme.text }}>{x.cls}</td>
                    <td className="py-1 text-right font-mono-nums" style={{ color: theme.t2 }}>{x.structure}</td>
                    <td className="py-1 text-right font-mono-nums" style={{ color: theme.t2 }}>{fmt(x.egnpi)}</td>
                    <td className="py-1 text-right font-mono-nums" style={{ color: theme.text }}>{fmt(x.mdp)}</td>
                  </tr>
                ))}
                <tr style={{ borderTop: `2px solid ${theme.cardBdr}` }}>
                  <td className="py-1 font-bold" style={{ color: theme.text }}>Total XL premium (MDP)</td>
                  <td></td><td></td>
                  <td className="py-1 text-right font-bold font-mono-nums" style={{ color: theme.text }}>{fmt(XL_MDP)}</td>
                </tr>
              </tbody>
            </table>
            <p className="text-[10px] mt-1" style={{ color: theme.t2 }}>Munich Re lead XL terms (JB Boda renewal). The non-prop layer is modelled as a <b>portfolio aggregate proxy</b> on the net-of-QS account (caps the net loss ratio); real XL is per-risk, so exact recoveries need the large-loss bordereau + Kago/Arun confirming the 55%/P12m attach &amp; limit. MDP is the XL premium, already within the cession.</p>
          </div>
          <p className="text-[10px] mt-1.5" style={{ color: theme.t2 }}>P&L total ceded {fmt(pnl.ceded)} = this proportional treaty + XL/CAT (MDP) + facultative premium. The commission gap to the P&L is facultative commission + General-QS profit commission — XL/CAT pays premium, not commission. <b>Accident &amp; Liability are RETAINED (0% cession)</b>; <b>Property moves to the Fire &amp; Eng Surplus at ~24% cession</b> (was a flat 70% QS). Overall cession ~63%. Commission is read off each treaty&rsquo;s scale — <b>SIGNED 2026/27 schedule (per JB Boda, Kago 2026-07-03)</b>: Motor QS 40%→22.5% (LR 50%→75%; at the 65% loss ratio, 29.5%), General QS 37.5% (32.5% above 60% LR) + 30% profit commission, Fire &amp; Eng Surplus 30% + 28.5% profit commission, Legal Expenses 80% / 27.5%. Motor &amp; Non-Motor XL MDP P0.74m / P1.02m.</p>
        </Card>

        {/* products + claims + cash */}
        <div id="products" className="scroll-mt-24 grid lg:grid-cols-3 gap-5">
          <Card kicker="Products · GWP mix" title={`Motor ${pct(topProducts[0].share, 0)} of the book`}>
            <div className="flex items-center gap-3">
              <div className="shrink-0" style={{ width: 124, height: 124 }}>
                <Donut data={topProducts.map((p, i) => ({ label: p.name, value: +p.gwp.toFixed(1), color: PIE[i % PIE.length] }))} />
              </div>
              <div className="flex-1 min-w-0"><Legend data={topProducts.slice(0, 6).map((p, i) => ({ label: p.name, value: +p.gwp.toFixed(1), color: PIE[i % PIE.length] }))} fmt={v => fmt(v)} /></div>
            </div>
          </Card>
          <Card kicker="Products · profitability" title="Net technical contribution by line">
            <HBars data={[...products.rows].sort((a, b) => b.netContribution - a.netContribution).map(p => ({ label: p.name.split(' ')[0], value: +p.netContribution.toFixed(1), color: p.netContribution >= 0 ? GREEN : RED }))} fmt={v => `P${v.toFixed(1)}m`} valueColor={theme.text} />
          </Card>
          <Card kicker="Claims · gross loss ratio by class" title="Which lines carry the loss ratio">
            <HBars data={[...products.rows].sort((a, b) => b.lossRatio - a.lossRatio).slice(0, 6).map(p => ({ label: p.name.split(' ')[0], value: +(p.lossRatio * 100).toFixed(0), color: p.lossRatio > 0.7 ? RED : p.lossRatio > 0.5 ? ORANGE : NAVY }))} fmt={v => `${v.toFixed(0)}%`} valueColor={theme.text} />
          </Card>
        </div>

        <div className="grid lg:grid-cols-3 gap-5">
          <Card kicker="Cash balances · P Mn" title={`Cash to ${fmt(bs27.cash)} by FY27`}>
            <HBars data={cashTrend.map(c => ({ label: c.y, value: +c.cash.toFixed(1), color: c.y.startsWith('FY27') ? ORANGE : NAVY }))} fmt={v => fmt(v)} valueColor={theme.text} labelW={64} />
          </Card>
          <Card kicker="Aria advisor · advisory only" title="Ask Aria to read this scenario" className="lg:col-span-2">
            <button onClick={() => ask('')} disabled={advBusy} className="inline-flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-semibold" style={{ background: ORANGE, color: '#fff', opacity: advBusy ? 0.6 : 1 }}>
              {advBusy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />} Read this scenario
            </button>
            <p className="text-[11px] mt-2 flex items-center gap-1" style={{ color: theme.t2 }}><Bot className="w-3 h-3" /> General read with no question — or type one in the Ask box at the top. Reads the levers + computed figures + sensitivity tables and recommends moves. Never changes the numbers, never saves anything. The answer appears in the Ask box above.</p>
          </Card>
        </div>

        <div className="text-[11px] pb-4" style={{ color: theme.t2 }}>
          FY26 actual (May-2026 MA, 11-mo): GWP {fmt(FY26_ACTUAL.gwp)} · EBITDA {fmt(FY26_ACTUAL.ebitda)} · PAT {fmt(FY26_ACTUAL.pat)} · combined {pct(FY26_ACTUAL.combinedRatio, 0)}. FY27 is Kago's budget. Target EBITDA {fmt(TARGET.ebitda)}. Figures P Millions.
        </div>
      </section>
    </div>
  )
}
