'use client'

/**
 * /salvage/parts — Veritas · Parts & Savings.
 *
 * CFO directive 2026-09-10: the Parts & Assessments team (Lemogang Machola)
 * sends Bharath two workbooks a month — assessment savings, and parts
 * purchases by supplier. They were living in a mailbox. This is the screen.
 *
 * Three questions it answers, in this order:
 *   1. What did contract pricing save us?  (repairer quote − our assessment)
 *   2. What did we spend on parts, and with whom?
 *   3. Which numbers in the workbook do not tie?  (shown, never silently fixed)
 *
 * Charts are hand-drawn SVG on purpose — no chart library, so the page stays
 * inside the landing-page JS budget. Colour comes from the theme tokens only.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import {
  ArrowLeft, ArrowUpRight, Banknote, CheckCircle2, FileSpreadsheet, Handshake,
  Info, Loader2, Medal, ScanLine, TriangleAlert, Upload, Wrench, type LucideIcon,
} from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { API_BASE, apiFetch, getToken, SSO_SENTINEL_TOKEN } from '@/lib/api'
import { PartsRegister } from './PartsRegister'
import { SavingsSimulator } from './SavingsSimulator'

// ── types ──────────────────────────────────────────────────────────────────
interface TimelineRow {
  month: string; fy: string
  dealership: string; aftermarket: string; windscreen: string
  parts_total: string; contract_pricing: string; assessment_saving: string
}
interface SavingsRow {
  month: string; jobs: number; quote_total: string; report_total: string
  saving_parts: string; saving_labour: string; saving_paint: string; saving_total: string
}
interface SupplierRow { supplier: string; category: string; amount: string }
interface RepairerRow { repairer: string; jobs: number; quoted: string; saving: string; saving_pct: string }
interface VarianceRow {
  assessment_id: string; month: string; repairer: string
  file_total: string; computed: string; difference: string
}
interface ReconLine {
  month: string; block: string; stated: string | null
  computed: string; difference?: string; variance_rows?: number
}
interface UploadRow {
  kind: string; kind_display: string; file_name: string; uploaded_at: string
  uploaded_by: string; months: string[]; rows_created: number; reconciliation: ReconLine[]
}
interface Summary {
  totals: { parts_spend: string; contract_pricing: string; assessment_saving: string; jobs: number; months: number }
  latest_month: TimelineRow | null
  timeline: TimelineRow[]
  savings_by_month: SavingsRow[]
  top_suppliers: SupplierRow[]
  top_repairers: RepairerRow[]
  fy_history: Record<string, string[]>
  variance_rows: VarianceRow[]
  uploads: UploadRow[]
}

// ── helpers ────────────────────────────────────────────────────────────────
const FY_MONTH_LABELS = ['J', 'A', 'S', 'O', 'N', 'D', 'J', 'F', 'M', 'A', 'M', 'J']

const n = (v: string | number | null | undefined) => Number(v ?? 0)

function pula(value: string | number, decimals = 0) {
  return n(value).toLocaleString('en-BW', {
    minimumFractionDigits: decimals, maximumFractionDigits: decimals,
  })
}

function shortMonth(iso: string) {
  const d = new Date(`${iso}T00:00:00`)
  return d.toLocaleDateString('en-BW', { month: 'short' })
}

function longMonth(iso: string) {
  const d = new Date(`${iso}T00:00:00`)
  return d.toLocaleDateString('en-BW', { month: 'long', year: 'numeric' })
}

// ── page ───────────────────────────────────────────────────────────────────
export default function VeritasPartsPage() {
  const { theme } = useTheme()
  const [data, setData] = useState<Summary | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(() => {
    setLoading(true)
    apiFetch<Summary>('/salvage/parts/summary/')
      .then(r => { setData(r); setError(null) })
      .catch(e => setError(e instanceof Error ? e.message : 'Could not load the figures'))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  const timeline = data?.timeline ?? []
  const latest = data?.latest_month ?? null
  const savingsRate = latest && n(latest.parts_total) + n(latest.contract_pricing) > 0
    ? (n(latest.assessment_saving) / (n(latest.parts_total) + n(latest.contract_pricing)) * 100)
    : 0

  return (
    <div className="min-h-screen" style={{ background: theme.bg }}>
      <TopBar title="Parts & Savings" />

      <div className="p-4 lg:p-6 max-w-[1400px] mx-auto space-y-5">
        <Link href="/salvage" className="inline-flex items-center gap-1.5 text-sm font-medium"
              style={{ color: theme.t2 }}>
          <ArrowLeft className="w-4 h-4" /> Back to Veritas
        </Link>

        {/* ── Hero ─────────────────────────────────────────────────────── */}
        <section className="relative overflow-hidden rounded-2xl"
                 style={{ background: theme.card, border: `1px solid ${theme.cardBdr}`, boxShadow: theme.cardSh }}>
          <div className="absolute -top-40 -right-32 w-[44rem] h-[44rem] rounded-full pointer-events-none"
               style={{
                 background: `radial-gradient(circle at 30% 30%, ${theme.orange}2E 0%, transparent 55%),
                              radial-gradient(circle at 70% 60%, ${theme.navy}24 0%, transparent 55%)`,
                 filter: 'blur(40px)',
               }} />
          <div className="relative px-6 py-9 lg:px-12 lg:py-11">
            <p className="text-[11px] uppercase tracking-[0.25em] font-semibold mb-3"
               style={{ color: theme.t3 }}>
              Veritas · Parts &amp; Assessments
            </p>
            <h1 className="font-display-tight text-4xl lg:text-6xl font-bold leading-[0.95] mb-4"
                style={{ color: theme.navy }}>
              Parts{' '}
              <span className="italic bg-clip-text text-transparent"
                    style={{ backgroundImage: `linear-gradient(120deg, ${theme.navy} 0%, ${theme.orange} 62%, #FF9A2E 100%)` }}>
                &amp; Savings.
              </span>
            </h1>
            <p className="font-display text-lg italic max-w-2xl" style={{ color: theme.t2 }}>
              A register the Parts &amp; Assessments team keeps: what the panel beater
              quoted, what we assessed it at, and what we actually bought. Memorandum
              only — it posts no journal and moves no money.
            </p>
            {latest && (
              <div className="mt-6 inline-flex flex-wrap items-center gap-x-6 gap-y-2 rounded-xl px-4 py-3"
                   style={{ background: theme.g50, border: `1px solid ${theme.cardBdr}` }}>
                <span className="text-xs font-semibold uppercase tracking-wider" style={{ color: theme.t3 }}>
                  Latest month
                </span>
                <span className="text-sm font-bold" style={{ color: theme.navy }}>
                  {longMonth(latest.month)} · {latest.fy}
                </span>
                <span className="text-sm tabular-nums" style={{ color: theme.t2 }}>
                  Parts <strong style={{ color: theme.navy }}>P {pula(latest.parts_total)}</strong>
                </span>
                <span className="text-sm tabular-nums" style={{ color: theme.t2 }}>
                  Saved <strong style={{ color: theme.ok }}>P {pula(latest.assessment_saving)}</strong>
                </span>
                {savingsRate > 0 && (
                  <span className="text-xs font-semibold px-2 py-1 rounded-md tabular-nums"
                        style={{ background: theme.okB, color: theme.ok }}>
                    {savingsRate.toFixed(1)}% of the spend, saved
                  </span>
                )}
              </div>
            )}
          </div>
        </section>

        {error && (
          <div className="rounded-xl p-4 flex items-start gap-2"
               style={{ background: theme.erB, border: `1px solid ${theme.er}30` }}>
            <TriangleAlert className="w-4 h-4 mt-0.5 shrink-0" style={{ color: theme.er }} />
            <span className="text-sm" style={{ color: theme.er }}>{error}</span>
          </div>
        )}

        {loading && !data && (
          <div className="rounded-xl p-8 text-center text-sm"
               style={{ background: theme.card, border: `1px solid ${theme.cardBdr}`, color: theme.t3 }}>
            <Loader2 className="w-5 h-5 animate-spin mx-auto mb-2" />
            Reading the figures…
          </div>
        )}

        {data && (
          <>
            {/* ── KPI row ─────────────────────────────────────────────── */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
              <Kpi theme={theme} icon={Banknote} label="Parts bought"
                   value={`P ${pula(data.totals.parts_spend)}`}
                   foot={`${data.totals.months} month${data.totals.months === 1 ? '' : 's'} on record`} />
              <Kpi theme={theme} icon={Handshake} label="Contract pricing" tone="violet"
                   value={`P ${pula(data.totals.contract_pricing)}`}
                   foot="Parts we did not have to source" />
              <Kpi theme={theme} icon={ScanLine} label="Assessment savings" tone="emerald"
                   value={`P ${pula(data.totals.assessment_saving)}`}
                   foot="Quote less our assessment" />
              <Kpi theme={theme} icon={Wrench} label="Jobs assessed" tone="amber"
                   value={pula(data.totals.jobs)}
                   foot={`${data.top_repairers.length} repairers on the list`} />
            </div>

            {/* ── Spend vs saving, month by month ────────────────────── */}
            <section className="rounded-xl p-5"
                     style={{ background: theme.card, border: `1px solid ${theme.cardBdr}`, boxShadow: theme.cardSh }}>
              <header className="flex flex-wrap items-baseline justify-between gap-2 mb-1">
                <h2 className="font-display text-lg font-bold" style={{ color: theme.navy }}>
                  Month by month
                </h2>
                <Legend theme={theme} />
              </header>
              <p className="text-xs mb-5" style={{ color: theme.t3 }}>
                Bars are what we bought, split by where it came from. The line is what
                the assessments saved in the same month.
              </p>
              <SpendChart theme={theme} rows={timeline} />
            </section>

            {/* ── The register — people type into this ───────────────── */}
            <PartsRegister theme={theme} onChanged={load} />

            {/* ── Gamified what-if ───────────────────────────────────── */}
            <SavingsSimulator theme={theme} timeline={timeline} savings={data.savings_by_month} />

            {/* ── Suppliers + repairers ───────────────────────────────── */}
            <div className="grid lg:grid-cols-2 gap-4">
              <Panel theme={theme} title="Where the parts money goes"
                     sub="Top suppliers over the months on record.">
                <RankedBars theme={theme}
                            rows={data.top_suppliers.map(s => ({
                              key: s.supplier,
                              tag: s.category === 'WINDSCREEN' ? 'Glass'
                                 : s.category === 'AFTERMARKET' ? 'Aftermarket' : 'Dealership',
                              tone: s.category === 'WINDSCREEN' ? theme.inf
                                  : s.category === 'AFTERMARKET' ? theme.teal : theme.orange,
                              value: n(s.amount),
                            }))} />
              </Panel>

              <Panel theme={theme} title="Savings league — by repairer"
                     sub="How much came off each repairer's own quote.">
                {data.top_repairers.length === 0 && <Empty theme={theme} />}
                <ul className="divide-y" style={{ borderColor: theme.cardBdr }}>
                  {data.top_repairers.map((r, i) => (
                    <li key={r.repairer} className="py-2.5 flex items-center gap-3">
                      <Rank theme={theme} place={i + 1} />
                      <div className="flex-1 min-w-0">
                        <div className="text-sm font-semibold truncate" style={{ color: theme.text }}>
                          {r.repairer}
                        </div>
                        <div className="text-[11px]" style={{ color: theme.t3 }}>
                          {r.jobs} job{r.jobs === 1 ? '' : 's'} · quoted P {pula(r.quoted)}
                        </div>
                      </div>
                      <div className="text-right">
                        <div className="font-display-tight text-base font-bold tabular-nums"
                             style={{ color: theme.ok }}>
                          P {pula(r.saving)}
                        </div>
                        <div className="text-[10px] uppercase tracking-wider tabular-nums"
                             style={{ color: theme.t3 }}>
                          {r.saving_pct}% off quote
                        </div>
                      </div>
                    </li>
                  ))}
                </ul>
              </Panel>
            </div>

            {/* ── Five-year history ───────────────────────────────────── */}
            {Object.keys(data.fy_history).length > 0 && (
              <Panel theme={theme} title="Parts spend — five financial years"
                     sub="Each row is a financial year, July through June, as stated on the SUMMARY sheet.">
                <FyHistory theme={theme} history={data.fy_history} />
              </Panel>
            )}

            {/* ── Numbers that don't tie ──────────────────────────────── */}
            <NotTying theme={theme} data={data} />

            {/* ── Upload ──────────────────────────────────────────────── */}
            <UploadCard theme={theme} onDone={load} />

            {/* ── Upload history ─────────────────────────────────────── */}
            {data.uploads.length > 0 && (
              <Panel theme={theme} title="What has been loaded"
                     sub="Newest first. Loading a month again replaces it — it never doubles.">
                <ul className="divide-y" style={{ borderColor: theme.cardBdr }}>
                  {data.uploads.map(u => (
                    <li key={`${u.file_name}-${u.uploaded_at}`} className="py-3">
                      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                        <FileSpreadsheet className="w-4 h-4 shrink-0" style={{ color: theme.t3 }} />
                        <span className="text-sm font-semibold" style={{ color: theme.text }}>
                          {u.file_name}
                        </span>
                        <span className="text-[11px] px-2 py-0.5 rounded"
                              style={{ background: theme.g100, color: theme.t2 }}>
                          {u.kind_display}
                        </span>
                        <span className="text-[11px]" style={{ color: theme.t3 }}>
                          {u.rows_created} rows · {u.months.map(longMonth).join(', ')} ·{' '}
                          {u.uploaded_by} · {new Date(u.uploaded_at).toLocaleString('en-BW')}
                        </span>
                      </div>
                    </li>
                  ))}
                </ul>
              </Panel>
            )}
          </>
        )}
      </div>
    </div>
  )
}

// ── pieces ─────────────────────────────────────────────────────────────────

function Kpi({ theme, icon: Icon, label, value, foot, tone }: {
  theme: any; icon: LucideIcon; label: string; value: string; foot?: string
  tone?: 'emerald' | 'amber' | 'violet'
}) {
  const TONES: Record<string, { bg: string; fg: string }> = {
    default: { bg: theme.oL,    fg: theme.orangeText },
    emerald: { bg: theme.okB,   fg: theme.ok },
    amber:   { bg: theme.wrB,   fg: theme.wr },
    violet:  { bg: theme.tealL, fg: theme.teal },
  }
  const t = TONES[tone ?? 'default']
  return (
    <div className="rounded-xl p-4"
         style={{ background: theme.card, border: `1px solid ${theme.cardBdr}`, boxShadow: theme.cardSh }}>
      <div className="flex items-start justify-between mb-3">
        <div className="w-9 h-9 rounded-lg flex items-center justify-center" style={{ background: t.bg }}>
          <Icon className="w-4 h-4" style={{ color: t.fg }} strokeWidth={2} />
        </div>
        <span className="text-[10px] uppercase tracking-wider font-semibold text-right"
              style={{ color: theme.t3 }}>{label}</span>
      </div>
      <div className="font-display-tight text-2xl font-bold tabular-nums leading-none"
           style={{ color: theme.navy }}>{value}</div>
      {foot && <div className="text-[11px] mt-2" style={{ color: theme.t3 }}>{foot}</div>}
    </div>
  )
}

/** Podium chip for the savings league — 1st, 2nd, 3rd get a colour. */
function Rank({ theme, place }: { theme: any; place: number }) {
  const PODIUM: Record<number, { bg: string; fg: string }> = {
    1: { bg: '#FEF3C7', fg: '#92400E' },
    2: { bg: '#E5E7EB', fg: '#374151' },
    3: { bg: '#FFE4D0', fg: '#9A3412' },
  }
  const tone = PODIUM[place]
  return (
    <span className="w-7 h-7 shrink-0 rounded-lg flex items-center justify-center
                     text-[11px] font-bold tabular-nums"
          style={{
            background: tone?.bg ?? theme.g100,
            color:      tone?.fg ?? theme.t3,
            border:     `1px solid ${tone ? `${tone.fg}22` : theme.cardBdr}`,
          }}
          aria-label={`Position ${place}`}>
      {place <= 3 ? <Medal className="w-3.5 h-3.5" /> : place}
    </span>
  )
}

function Panel({ theme, title, sub, children }: {
  theme: any; title: string; sub?: string; children: React.ReactNode
}) {
  return (
    <section className="rounded-xl p-5"
             style={{ background: theme.card, border: `1px solid ${theme.cardBdr}`, boxShadow: theme.cardSh }}>
      <h2 className="font-display text-lg font-bold" style={{ color: theme.navy }}>{title}</h2>
      {sub && <p className="text-xs mt-1 mb-4" style={{ color: theme.t3 }}>{sub}</p>}
      {children}
    </section>
  )
}

function Empty({ theme }: { theme: any }) {
  return (
    <p className="text-sm py-6 text-center" style={{ color: theme.t3 }}>
      Nothing loaded yet. Drop the month&apos;s workbook in below.
    </p>
  )
}

function Legend({ theme }: { theme: any }) {
  const items = [
    { label: 'Dealership',  colour: theme.orange },
    { label: 'Aftermarket', colour: theme.teal },
    { label: 'Glass',       colour: theme.inf },
    { label: 'Saved',       colour: theme.ok },
  ]
  return (
    <div className="flex flex-wrap items-center gap-3">
      {items.map(i => (
        <span key={i.label} className="inline-flex items-center gap-1.5 text-[11px] font-medium"
              style={{ color: theme.t2 }}>
          <span className="w-2.5 h-2.5 rounded-sm" style={{ background: i.colour }} />
          {i.label}
        </span>
      ))}
    </div>
  )
}

/** Stacked spend bars with a savings line drawn over them. */
function SpendChart({ theme, rows }: { theme: any; rows: TimelineRow[] }) {
  if (rows.length === 0) return <Empty theme={theme} />

  const W = 100 * rows.length + 40
  const H = 260
  const PAD_B = 34
  const PAD_T = 16
  const peak = Math.max(
    ...rows.map(r => n(r.parts_total)),
    ...rows.map(r => n(r.assessment_saving)),
    1,
  )
  const scale = (v: number) => (H - PAD_B - PAD_T) * (v / peak)
  const slot = 100
  const barW = 44

  const points = rows.map((r, i) => {
    const x = 20 + i * slot + slot / 2
    const y = H - PAD_B - scale(n(r.assessment_saving))
    return { x, y, row: r }
  })
  const line = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x},${p.y}`).join(' ')

  return (
    <div className="overflow-x-auto -mx-1 px-1">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H}
           style={{ minWidth: Math.min(W, 1200), display: 'block' }}
           role="img" aria-label="Parts spend and assessment savings by month">
        {[0.25, 0.5, 0.75, 1].map(f => (
          <line key={f} x1={16} x2={W - 8}
                y1={H - PAD_B - scale(peak * f)} y2={H - PAD_B - scale(peak * f)}
                stroke={theme.cardBdr} strokeWidth={1} />
        ))}
        <line x1={16} x2={W - 8} y1={H - PAD_B} y2={H - PAD_B} stroke={theme.cardBdr} strokeWidth={1} />

        {rows.map((r, i) => {
          const x = 20 + i * slot + (slot - barW) / 2
          const blocks = [
            { v: n(r.dealership),  colour: theme.orange },
            { v: n(r.aftermarket), colour: theme.teal },
            { v: n(r.windscreen),  colour: theme.inf },
          ].filter(b => b.v > 0)
          let cursor = H - PAD_B
          return (
            <g key={r.month}>
              {blocks.map((b, bi) => {
                const h = scale(b.v)
                cursor -= h
                return (
                  <rect key={bi} x={x} y={cursor} width={barW} height={Math.max(h, 1)}
                        rx={bi === blocks.length - 1 ? 4 : 0} fill={b.colour}
                        opacity={0.92}>
                    <title>{`${longMonth(r.month)} — P ${pula(b.v)}`}</title>
                  </rect>
                )
              })}
              <text x={x + barW / 2} y={H - PAD_B + 14} textAnchor="middle"
                    fontSize={11} fontWeight={600} fill={theme.t2}>
                {shortMonth(r.month)}
              </text>
              <text x={x + barW / 2} y={H - PAD_B + 27} textAnchor="middle"
                    fontSize={9} fill={theme.t3}>
                {r.fy}
              </text>
            </g>
          )
        })}

        <path d={line} fill="none" stroke={theme.ok} strokeWidth={2.5}
              strokeLinecap="round" strokeLinejoin="round" />
        {points.map(p => (
          <g key={p.row.month}>
            <circle cx={p.x} cy={p.y} r={4} fill={theme.card} stroke={theme.ok} strokeWidth={2.5}>
              <title>{`${longMonth(p.row.month)} — saved P ${pula(p.row.assessment_saving)}`}</title>
            </circle>
          </g>
        ))}
      </svg>
    </div>
  )
}

function RankedBars({ theme, rows }: {
  theme: any; rows: { key: string; tag: string; tone: string; value: number }[]
}) {
  if (rows.length === 0) return <Empty theme={theme} />
  const peak = Math.max(...rows.map(r => r.value), 1)
  return (
    <ul className="space-y-2.5">
      {rows.map(r => (
        <li key={`${r.key}-${r.tag}`}>
          <div className="flex items-baseline justify-between gap-3 mb-1">
            <span className="text-sm font-semibold truncate" style={{ color: theme.text }}>
              {r.key}
              <span className="ml-2 text-[10px] uppercase tracking-wider font-semibold px-1.5 py-0.5 rounded"
                    style={{ background: `${r.tone}1A`, color: r.tone }}>
                {r.tag}
              </span>
            </span>
            <span className="text-sm font-bold tabular-nums shrink-0" style={{ color: theme.navy }}>
              P {pula(r.value)}
            </span>
          </div>
          <div className="h-1.5 rounded-full overflow-hidden" style={{ background: theme.g100 }}>
            <div className="h-full rounded-full"
                 style={{ width: `${Math.max((r.value / peak) * 100, 1.5)}%`, background: r.tone }} />
          </div>
        </li>
      ))}
    </ul>
  )
}

function FyHistory({ theme, history }: { theme: any; history: Record<string, string[]> }) {
  const years = Object.keys(history).sort()
  const peak = Math.max(...years.flatMap(fy => history[fy].map(n)), 1)
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-1 pl-[4.5rem]">
        {FY_MONTH_LABELS.map((m, i) => (
          <span key={i} className="flex-1 text-center text-[9px] font-semibold"
                style={{ color: theme.t3 }}>{m}</span>
        ))}
      </div>
      {years.map(fy => {
        const total = history[fy].reduce((a, v) => a + n(v), 0)
        return (
          <div key={fy} className="flex items-center gap-3">
            <span className="w-16 shrink-0 text-xs font-bold tabular-nums" style={{ color: theme.navy }}>
              {fy}
            </span>
            <div className="flex-1 flex items-end gap-1 h-10">
              {history[fy].map((v, i) => {
                const value = n(v)
                const pct = (value / peak) * 100
                return (
                  <div key={i} className="flex-1 rounded-sm relative group"
                       style={{
                         height: `${Math.max(pct, value > 0 ? 4 : 2)}%`,
                         background: value > 0 ? theme.navy : theme.g200,
                         opacity: value > 0 ? 0.35 + 0.65 * (value / peak) : 1,
                       }}
                       title={`${fy} · ${FY_MONTH_LABELS[i]} — P ${pula(value)}`} />
                )
              })}
            </div>
            <span className="w-28 shrink-0 text-right text-xs font-semibold tabular-nums"
                  style={{ color: theme.t2 }}>
              P {pula(total)}
            </span>
          </div>
        )
      })}
    </div>
  )
}

function NotTying({ theme, data }: { theme: any; data: Summary }) {
  const reconLines = data.uploads.flatMap(u =>
    (u.reconciliation || []).map(line => ({ ...line, source: u.file_name })))
  const offLines = reconLines.filter(l =>
    Math.abs(n(l.difference)) > 1 || (l.variance_rows ?? 0) > 0)

  // With nothing loaded there is nothing to tie, and a green "everything ties"
  // on an empty register reads as a claim about data we do not have.
  const nothingLoaded = data.totals.months === 0 && data.totals.jobs === 0
  if (nothingLoaded) return null

  if (offLines.length === 0 && data.variance_rows.length === 0) {
    return (
      <section className="rounded-xl p-5 flex items-start gap-3"
               style={{ background: theme.okB, border: `1px solid ${theme.ok}33` }}>
        <CheckCircle2 className="w-5 h-5 shrink-0 mt-0.5" style={{ color: theme.ok }} />
        <div>
          <h2 className="font-display text-base font-bold" style={{ color: theme.ok }}>
            Everything ties
          </h2>
          <p className="text-xs mt-1" style={{ color: theme.t2 }}>
            Every total in the workbooks matches the rows underneath it.
          </p>
        </div>
      </section>
    )
  }

  return (
    <section className="rounded-xl p-5"
             style={{ background: theme.card, border: `1px solid ${theme.wr}44`, boxShadow: theme.cardSh }}>
      <header className="flex items-start gap-3 mb-4">
        <TriangleAlert className="w-5 h-5 shrink-0 mt-0.5" style={{ color: theme.wr }} />
        <div>
          <h2 className="font-display text-lg font-bold" style={{ color: theme.navy }}>
            Numbers that do not tie
          </h2>
          <p className="text-xs mt-1" style={{ color: theme.t3 }}>
            Shown, not corrected. The dashboard totals the rows; this is where the
            workbook&apos;s own totals disagree with them.
          </p>
        </div>
      </header>

      {offLines.length > 0 && (
        <div className="overflow-x-auto mb-5">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[10px] uppercase tracking-wider" style={{ color: theme.t3 }}>
                <th className="py-2 pr-3 font-semibold">Month</th>
                <th className="py-2 pr-3 font-semibold">Block</th>
                <th className="py-2 pr-3 font-semibold text-right">Workbook says</th>
                <th className="py-2 pr-3 font-semibold text-right">Rows add to</th>
                <th className="py-2 font-semibold text-right">Gap</th>
              </tr>
            </thead>
            <tbody>
              {offLines.map((l, i) => (
                <tr key={`${l.month}-${l.block}-${i}`} className="border-t"
                    style={{ borderColor: theme.cardBdr }}>
                  <td className="py-2 pr-3 whitespace-nowrap" style={{ color: theme.text }}>
                    {longMonth(l.month)}
                  </td>
                  <td className="py-2 pr-3" style={{ color: theme.t2 }}>
                    {l.block}
                    {(l.variance_rows ?? 0) > 0 && (
                      <span className="ml-2 text-[10px] px-1.5 py-0.5 rounded"
                            style={{ background: theme.wrB, color: theme.wr }}>
                        {l.variance_rows} row{l.variance_rows === 1 ? '' : 's'} off
                      </span>
                    )}
                  </td>
                  <td className="py-2 pr-3 text-right tabular-nums" style={{ color: theme.t2 }}>
                    {l.stated === null ? '—' : `P ${pula(l.stated)}`}
                  </td>
                  <td className="py-2 pr-3 text-right tabular-nums" style={{ color: theme.navy }}>
                    P {pula(l.computed)}
                  </td>
                  <td className="py-2 text-right tabular-nums font-semibold"
                      style={{ color: Math.abs(n(l.difference)) > 1 ? theme.wr : theme.t3 }}>
                    {l.difference === undefined ? '—' : `P ${pula(l.difference)}`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {data.variance_rows.length > 0 && (
        <>
          <h3 className="text-xs font-bold uppercase tracking-wider mb-2" style={{ color: theme.t2 }}>
            Jobs where the file&apos;s savings column is not quote less assessment
          </h3>
          <ul className="divide-y" style={{ borderColor: theme.cardBdr }}>
            {data.variance_rows.slice(0, 8).map(v => (
              <li key={v.assessment_id} className="py-2 flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <span className="text-sm font-semibold" style={{ color: theme.text }}>
                  {v.assessment_id}
                </span>
                <span className="text-[11px]" style={{ color: theme.t3 }}>
                  {longMonth(v.month)} · {v.repairer || 'Repairer not stated'}
                </span>
                <span className="ml-auto text-xs tabular-nums" style={{ color: theme.t2 }}>
                  file P {pula(v.file_total)} · ours P {pula(v.computed)}
                </span>
                <span className="text-xs font-bold tabular-nums" style={{ color: theme.wr }}>
                  {n(v.difference) > 0 ? '+' : ''}P {pula(v.difference)}
                </span>
              </li>
            ))}
          </ul>
          {data.variance_rows.length > 8 && (
            <p className="text-[11px] mt-2" style={{ color: theme.t3 }}>
              And {data.variance_rows.length - 8} more.
            </p>
          )}
        </>
      )}
    </section>
  )
}

function UploadCard({ theme, onDone }: { theme: any; onDone: () => void }) {
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<{ message: string; months: string[] } | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  async function send(file: File) {
    setBusy(true); setErr(null); setResult(null)
    try {
      const fd = new FormData()
      fd.append('file', file)
      const headers: Record<string, string> = {}
      try {
        const { acquireApiToken, SSO_API_CALLS_READY } = await import('@/auth/msal')
        if (SSO_API_CALLS_READY) {
          const t = await acquireApiToken()
          if (t) headers['Authorization'] = `Bearer ${t}`
        }
      } catch { /* fall through to token auth */ }
      if (!headers['Authorization']) {
        const t = getToken()
        if (t && t !== SSO_SENTINEL_TOKEN) headers['Authorization'] = `Token ${t}`
      }
      // API_BASE, not a hardcoded '/api/v1/…' — same-origin in prod, but it
      // also honours NEXT_PUBLIC_API_BASE, and a relative POST through the
      // dev proxy loses its trailing slash and dies on Django's APPEND_SLASH.
      const r = await fetch(`${API_BASE}/salvage/parts/upload/`, {
        method: 'POST', body: fd, headers, credentials: 'include',
      })
      const body = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(body.detail || 'That file could not be read.')
      setResult({ message: body.message, months: body.months || [] })
      if (fileRef.current) fileRef.current.value = ''
      onDone()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Upload failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="rounded-xl p-5 relative overflow-hidden"
             style={{ background: `linear-gradient(135deg, ${theme.navy} 0%, #1F1547 100%)`, color: '#FFFFFF' }}>
      <div className="absolute -top-16 -right-16 w-56 h-56 rounded-full blur-2xl pointer-events-none"
           style={{ background: `radial-gradient(circle, ${theme.orange}80, transparent 70%)` }} />
      <div className="relative">
        <h2 className="font-display text-xl font-bold mb-1">Load this month</h2>
        <p className="text-xs opacity-80 max-w-xl mb-4">
          Drop either workbook — the assessment savings report, or the parts summary.
          Omni works out which one it is from the sheets inside. Loading a month
          again replaces it, so a corrected file is safe to send twice.
        </p>

        <div className="flex flex-wrap items-center gap-3">
          <label className="inline-flex items-center gap-2 px-4 py-2 rounded-md text-sm font-semibold cursor-pointer"
                 style={{
                   background: `linear-gradient(135deg, ${theme.orange}, #FF9A2E)`,
                   boxShadow: `0 8px 24px -8px ${theme.orange}8C`,
                   opacity: busy ? 0.6 : 1,
                 }}>
            {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />}
            {busy ? 'Reading…' : 'Choose workbook'}
            <input ref={fileRef} type="file" accept=".xlsx,.xlsm" className="hidden" disabled={busy}
                   onChange={e => { const f = e.target.files?.[0]; if (f) send(f) }} />
          </label>
          <span className="inline-flex items-center gap-1.5 text-[11px] opacity-70">
            <Info className="w-3.5 h-3.5" /> Nothing here posts to the ledger or moves money.
          </span>
        </div>

        {result && (
          <div className="mt-4 rounded-lg px-3 py-2.5 text-xs flex items-start gap-2"
               style={{ background: 'rgba(255,255,255,0.12)' }}>
            <CheckCircle2 className="w-4 h-4 shrink-0 mt-0.5" style={{ color: '#7BE8A9' }} />
            <span>
              {result.message}
              {result.months.length > 0 && (
                <> Months loaded: <strong>{result.months.map(longMonth).join(', ')}</strong>.</>
              )}
            </span>
          </div>
        )}
        {err && (
          <div className="mt-4 rounded-lg px-3 py-2.5 text-xs flex items-start gap-2"
               style={{ background: 'rgba(255,120,120,0.18)' }}>
            <TriangleAlert className="w-4 h-4 shrink-0 mt-0.5" style={{ color: '#FFB4B4' }} />
            <span>{err}</span>
          </div>
        )}

        <Link href="/salvage"
              className="inline-flex items-center gap-1.5 text-xs font-semibold mt-4 opacity-90 hover:opacity-100">
          Veritas salvage yard <ArrowUpRight className="w-3.5 h-3.5" />
        </Link>
      </div>
    </section>
  )
}
