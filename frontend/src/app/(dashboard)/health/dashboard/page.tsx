'use client'

/**
 * /health/dashboard — Alpha Direct Health.
 *
 * Rebuilt 8-Sep-2026 to the Health feature request from the Projects Lead, Health.
 * There is not a single number in this markup: every figure is read live from
 * /health/dashboard/ on load, on Refresh, and whenever the as-of month is
 * changed. Change a bordereaux, a claim run or a register row in Omni and the
 * next load shows it — no code change, no manual edit.
 *
 * Three corrections the request asked for, all of them server-side now:
 *   - the year-to-date premium is the financial year (Jul-Jun), not
 *     inception-to-date; inception-to-date is shown only where it is labelled;
 *   - the headline loss ratio is year-to-date;
 *   - the register and the quote-derived on-cover book disagree, so both are
 *     printed with the difference stated instead of being blended.
 *
 * A null is printed as "—". Nothing here invents a value.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import {
  Loader2, Stethoscope, RefreshCw, FileText, Calculator, FileSpreadsheet,
  ShieldCheck, HeartPulse, ChevronDown, ChevronUp, AlertTriangle, ArrowRight,
} from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import {
  getHealthDashboard, getToken,
  type HealthDashboard, type HealthDashMonth,
} from '@/lib/api'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'

const DASH = '—'

function fmtP(n: number | null | undefined, dp = 0): string {
  if (n === null || n === undefined) return DASH
  return 'P' + Number(n).toLocaleString('en-BW', {
    minimumFractionDigits: dp, maximumFractionDigits: dp,
  })
}
function fmtN(n: number | null | undefined): string {
  if (n === null || n === undefined) return DASH
  return Number(n).toLocaleString('en-BW')
}
function fmtPct(n: number | null | undefined, dp = 1): string {
  if (n === null || n === undefined) return DASH
  return `${Number(n).toFixed(dp)}%`
}
function fmtDate(iso: string | null | undefined): string {
  if (!iso) return DASH
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? DASH
    : d.toLocaleDateString('en-GB', { day: '2-digit', month: 'long', year: 'numeric' })
}
function fmtStamp(iso: string | null | undefined): string {
  if (!iso) return DASH
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? DASH
    : d.toLocaleString('en-GB', {
      day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
    })
}
/** Last day of the month an <input type="month"> gives us. */
function monthEnd(value: string): string {
  const [y, m] = value.split('-').map(Number)
  if (!y || !m) return value
  return new Date(Date.UTC(y, m, 0)).toISOString().slice(0, 10)
}

export default function HealthDashboardPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const [d, setD] = useState<HealthDashboard | null>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [asof, setAsof] = useState<string>('')          // '' = today, server decides
  const [showGroups, setShowGroups] = useState(false)
  const [showTable, setShowTable] = useState(false)

  const load = useCallback((month?: string) => {
    setLoading(true); setErr(null)
    getHealthDashboard(month ? monthEnd(month) : undefined)
      .then(setD)
      .catch(() => setErr('Data unavailable — check OMNI connection'))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const card = { background: theme.card, border: `1px solid ${theme.cardBdr}`, borderRadius: 14 }

  const months = d?.monthly ?? []
  const revenueMonths = useMemo(() => months.filter(m => m.gwpIncl), [months])
  const latestKey = revenueMonths.length ? revenueMonths[revenueMonths.length - 1].month : ''
  const maxGwp = Math.max(1, ...revenueMonths.map(m => Number(m.gwpIncl || 0)))
  const lrMonths = useMemo(() => months.filter(m => m.lossRatioPct !== null), [months])
  const maxLr = Math.max(1, ...lrMonths.map(m => Number(m.lossRatioPct || 0)))

  const kpi = d?.kpi
  const objectivePct = kpi?.annualisedIncl && kpi?.objective
    ? (kpi.annualisedIncl / kpi.objective) * 100 : 0
  const livesPct = kpi?.activeLives && kpi?.livesTarget
    ? (kpi.activeLives / kpi.livesTarget) * 100 : 0

  const pipeline: [string, number][] = d ? [
    ['Draft', d.pipeline.draft], ['In review', d.pipeline.inReview],
    ['Approved', d.pipeline.approved], ['Invoiced', d.pipeline.invoiced],
    ['Renewals', d.pipeline.renewals],
  ] : []

  const links = [
    { href: '/health/quick-quote', icon: Calculator, label: 'Quick Quote' },
    { href: '/health/quotes', icon: FileText, label: 'Quotations' },
    { href: '/healthcare/revenue', icon: FileSpreadsheet, label: 'Revenue (Bordereaux)' },
    { href: '/healthcare/claims', icon: HeartPulse, label: 'Claims (AFT)' },
    { href: '/healthcare/treaty', icon: ShieldCheck, label: 'Treaty (IN / OUT)' },
    { href: '/health/service-providers', icon: Stethoscope, label: 'Service Providers' },
  ]

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar title="Health Care" breadcrumbs={[{ label: 'Healthcare', href: '/health/quotes' }, { label: 'Overview' }]} />
      <main className="flex-1 overflow-y-auto p-6 space-y-5">

        {/* ── Masthead ─────────────────────────────────────────── */}
        <header className="flex flex-wrap items-end justify-between gap-3 pb-3"
                style={{ borderBottom: `3px solid ${ORANGE}` }}>
          <div>
            <h1 className="text-2xl font-bold" style={{ color: theme.text }}>Alpha Direct Health</h1>
            <p className="text-sm mt-0.5" style={{ color: theme.t2 }}>
              Health Care — book, revenue &amp; pipeline
            </p>
          </div>
          <div className="text-right text-xs" style={{ color: theme.t2 }}>
            <div className="text-sm font-semibold" style={{ color: theme.text }}>
              As at {fmtDate(d?.asOf)}
            </div>
            <div className="mt-0.5">
              {d ? `${d.fy.label} = ${fmtDate(d.fy.start)} – ${fmtDate(d.fy.end)}` : DASH}
              {' · BWP · VAT '}{d ? `${(d.sources.vatRate * 100).toFixed(0)}%` : DASH}
            </div>
            <div className="mt-1 flex items-center justify-end gap-2">
              <span>Last updated {fmtStamp(d?.lastUpdated)}</span>
              <label htmlFor="asof" className="text-xs font-semibold"
                     style={{ color: theme.text }}>Month end</label>
              <input id="asof" type="month" value={asof || (d?.asOf ?? '').slice(0, 7)}
                     onChange={e => { setAsof(e.target.value); load(e.target.value) }}
                     className="text-xs rounded px-1.5 py-1"
                     style={{ background: theme.card, border: `1px solid ${theme.cardBdr}`, color: theme.text }} />
              <button onClick={() => load(asof)} disabled={loading}
                      aria-label="Refresh"
                      className="inline-flex items-center gap-1.5 text-xs font-semibold rounded px-2 py-1"
                      style={{ border: `1px solid ${theme.cardBdr}`, color: theme.text }}>
                <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} /> Refresh
              </button>
            </div>
          </div>
        </header>

        {loading && !d && (
          <div className="text-sm" style={{ color: theme.t2 }}>
            <Loader2 className="w-4 h-4 inline animate-spin" /> Loading…
          </div>
        )}
        {err && (
          <div className="rounded-lg px-3 py-2 text-sm" role="alert"
               style={{ background: theme.erB, color: theme.er }}>{err}</div>
        )}

        {d && kpi && (
          <>
            {/* ── KPI band ───────────────────────────────────────── */}
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
              <div className="p-4" style={{ background: ORANGE, borderRadius: 14 }}>
                <div className="text-xs font-semibold" style={{ color: NAVY }}>
                  GWP — {d.fy.label} year to date (incl VAT)
                </div>
                <div className="text-3xl font-bold tabular-nums mt-1" style={{ color: NAVY }}>
                  {fmtP(kpi.gwpYtdIncl)}
                </div>
                <div className="text-[11px] mt-1.5" style={{ color: NAVY }}>
                  Annualised run-rate {fmtP(kpi.annualisedIncl)}
                  {kpi.objective ? ` · ${objectivePct.toFixed(1)}% of the ${fmtP(kpi.objective / 1e6, 1)}M objective` : ''}
                </div>
              </div>

              <div className="p-4" style={{ background: NAVY, borderRadius: 14 }}>
                <div className="text-xs font-semibold text-white/80">
                  GWP — {kpi.gwpMonthLabel || DASH} (incl VAT)
                </div>
                <div className="text-3xl font-bold text-white tabular-nums mt-1">
                  {fmtP(kpi.gwpMonthIncl)}
                </div>
                {kpi.gwpMonMoMPct !== null && (
                  <div className="inline-block text-[11px] font-semibold mt-1.5 px-2 py-0.5 rounded"
                       style={{ background: 'rgba(255,255,255,0.16)', color: '#fff' }}>
                    {kpi.gwpMonMoMPct >= 0 ? '▲' : '▼'} {Math.abs(kpi.gwpMonMoMPct).toFixed(1)}% MoM
                  </div>
                )}
              </div>

              <div className="p-4" style={{ background: NAVY, borderRadius: 14 }}>
                <div className="text-xs font-semibold text-white/80">Active lives</div>
                <div className="text-3xl font-bold text-white tabular-nums mt-1">
                  {fmtN(kpi.activeLives)}
                </div>
                <div className="text-[11px] text-white/80 mt-1.5">
                  {kpi.activeLives === null
                    ? 'Register unavailable — showing no figure rather than a guess'
                    : `${fmtN(kpi.policyholders)} policyholders + ${fmtN(kpi.dependants)} dependants`}
                </div>
              </div>

              <div className="p-4" style={{ background: NAVY, borderRadius: 14 }}>
                <div className="text-xs font-semibold text-white/80">
                  Loss ratio — {d.fy.label} YTD
                </div>
                <div className="text-3xl font-bold text-white tabular-nums mt-1">
                  {fmtPct(kpi.lossRatioYtdPct)}
                </div>
                <div className="text-[11px] text-white/80 mt-1.5">
                  {kpi.gwpMonthLabel ? `${kpi.gwpMonthLabel} month ${fmtPct(kpi.lossRatioMonthPct)}` : DASH}
                  {' · inception to date '}{fmtPct(d.itd.lossRatioPct)}
                </div>
              </div>
            </div>

            {/* ── Progress against objective ─────────────────────── */}
            <section className="p-4" style={card} aria-labelledby="obj-h">
              <h2 id="obj-h" className="text-base font-bold mb-3" style={{ color: theme.text }}>
                Progress against {d.fy.label} objective{' '}
                <span className="text-xs font-normal" style={{ color: theme.t2 }}>
                  {fmtP((kpi.objective || 0) / 1e6, 1)}M GWP · {fmtN(kpi.livesTarget)} lives
                </span>
              </h2>
              <Bar label={`Annualised GWP vs ${fmtP((kpi.objective || 0) / 1e6, 1)}M`}
                   pct={objectivePct} valueLabel={fmtP(kpi.annualisedIncl)}
                   right={kpi.annualisedIncl === null ? DASH
                     : `${objectivePct.toFixed(1)}% · gap ${fmtP((kpi.objective || 0) - kpi.annualisedIncl)}`}
                   theme={theme} />
              <div className="h-3" />
              <Bar label={`Active lives vs ${fmtN(kpi.livesTarget)}`}
                   pct={kpi.activeLives === null ? 0 : livesPct}
                   valueLabel={fmtN(kpi.activeLives)}
                   right={kpi.activeLives === null ? 'register unavailable'
                     : `${livesPct.toFixed(1)}% · ${fmtN(kpi.livesTarget - kpi.activeLives)} to go`}
                   theme={theme} />
            </section>

            {/* ── The register / on-cover-book gap ───────────────── */}
            {!d.gap.reconciled && d.gap.registerLives !== null && (
              <div className="flex items-start gap-2 rounded-lg px-3 py-2 text-sm" role="note"
                   style={{ background: theme.wrB, color: theme.wr }}>
                <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
                <div>
                  <b>Lives do not reconcile yet.</b>{' '}
                  The register shows {fmtN(d.gap.registerLives)} active lives; the on-cover book,
                  built from approved and invoiced quotations, shows {fmtN(d.gap.quoteBookLives)}
                  {d.gap.difference !== null ? ` — a difference of ${fmtN(Math.abs(d.gap.difference))}` : ''}.
                  Both are shown; they are never blended. GWP above is the billed book.
                </div>
              </div>
            )}

            {d.gap.registerLives === null && (
              <div className="flex items-start gap-2 rounded-lg px-3 py-2 text-sm" role="note"
                   style={{ background: theme.wrB, color: theme.wr }}>
                <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
                <div>
                  <b>Lives are not showing.</b>{' '}
                  The member register could not be read
                  {d.sources.registerUnavailableReason ? ` (${d.sources.registerUnavailableReason})` : ''}.
                  Rather than print a stale or invented figure, the lives cards read {DASH}.
                  Premium, claims and the quote pipeline above are live and unaffected.
                  For reference, the on-cover book built from approved and invoiced
                  quotations holds {fmtN(d.gap.quoteBookLives)} lives.
                </div>
              </div>
            )}

            {/* ── Revenue ────────────────────────────────────────── */}
            <h2 className="text-base font-bold pt-1" style={{ color: theme.text }}>
              Revenue{' '}
              <span className="text-xs font-normal" style={{ color: theme.t2 }}>
                monthly GWP incl VAT, BWP
              </span>
            </h2>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
              <section className="p-4" style={card} aria-labelledby="gwp-h">
                <h3 id="gwp-h" className="font-bold text-sm mb-4" style={{ color: theme.text }}>
                  Monthly GWP
                </h3>
                {revenueMonths.length === 0 ? (
                  <p className="text-sm" style={{ color: theme.t2 }}>
                    No premium bordereaux loaded for this period.
                  </p>
                ) : (
                  <div className="flex items-end gap-1" style={{ height: 210 }}>
                    {revenueMonths.map(m => (
                      <ColumnBar key={m.month} label={m.label}
                                 value={fmtK(m.gwpIncl)} pct={(Number(m.gwpIncl) / maxGwp) * 100}
                                 colour={m.month === latestKey ? ORANGE : NAVY} theme={theme} />
                    ))}
                  </div>
                )}
              </section>

              <section className="p-4" style={card} aria-labelledby="lr-h">
                <h3 id="lr-h" className="font-bold text-sm mb-4" style={{ color: theme.text }}>
                  Claims &amp; loss ratio
                </h3>
                {lrMonths.length === 0 ? (
                  <p className="text-sm" style={{ color: theme.t2 }}>
                    No claim run has been loaded for this period.
                  </p>
                ) : (
                  <>
                    <div className="flex items-end gap-1" style={{ height: 180 }}>
                      {lrMonths.map(m => (
                        <ColumnBar key={m.month} label={m.label}
                                   value={fmtPct(m.lossRatioPct, 0)}
                                   pct={(Number(m.lossRatioPct) / maxLr) * 100}
                                   colour={Number(m.lossRatioPct) >= 100 ? ORANGE : NAVY}
                                   theme={theme} />
                      ))}
                    </div>
                    <div className="flex gap-4 mt-3 text-[11px]" style={{ color: theme.t2 }}>
                      <Key colour={NAVY} label="under 100%" />
                      <Key colour={ORANGE} label="100% or over (past breakeven)" />
                    </div>
                  </>
                )}
              </section>
            </div>

            {/* ── Cancellations ──────────────────────────────────── */}
            <h2 className="text-base font-bold pt-1" style={{ color: theme.text }}>
              Cancellations{' '}
              <span className="text-xs font-normal" style={{ color: theme.t2 }}>
                {d.cancellations.available
                  ? `${fmtN(d.cancellations.totalLives)} lives on the book`
                  : 'register unavailable'}
              </span>
            </h2>
            <section className="p-4" style={card}>
              {!d.cancellations.reasonsAvailable && (
                <p className="text-sm mb-3 px-3 py-2 rounded"
                   style={{ background: theme.g100, color: theme.text }}>
                  <b style={{ color: theme.text }}>Reason split not available.</b>{' '}
                  {d.cancellations.note}
                </p>
              )}
              {d.cancellations.byGroup.length === 0 ? (
                <p className="text-sm" style={{ color: theme.t2 }}>
                  {d.cancellations.available
                    ? 'No cancelled lives on the register.'
                    : `Data unavailable — ${d.sources.registerUnavailableReason || 'register not reachable'}.`}
                </p>
              ) : (
                <>
                  <button type="button" onClick={() => setShowGroups(v => !v)}
                          aria-expanded={showGroups}
                          className="w-full flex items-center justify-between text-sm font-bold py-1"
                          style={{ color: theme.text }}>
                    <span>Ops detail — cancellations by employer group</span>
                    {showGroups ? <ChevronUp className="w-4 h-4" style={{ color: ORANGE }} />
                      : <ChevronDown className="w-4 h-4" style={{ color: ORANGE }} />}
                  </button>
                  {showGroups && (
                    <div className="overflow-x-auto mt-2">
                      <table className="w-full text-sm tabular-nums">
                        <thead>
                          <tr style={{ background: NAVY, color: '#fff' }}>
                            <th className="text-left px-3 py-2 font-semibold">Employer group</th>
                            <th className="text-right px-3 py-2 font-semibold">Cancelled lives</th>
                            <th className="text-right px-3 py-2 font-semibold">Dated in month</th>
                            <th className="text-right px-3 py-2 font-semibold">Active lives</th>
                            <th className="text-right px-3 py-2 font-semibold">Main reason</th>
                          </tr>
                        </thead>
                        <tbody>
                          {d.cancellations.byGroup.map((g, i) => (
                            <tr key={g.group} style={{ background: i % 2 ? theme.g50 : 'transparent' }}>
                              <td className="px-3 py-2" style={{ color: theme.text }}>{g.group}</td>
                              <td className="px-3 py-2 text-right" style={{ color: theme.text }}>{fmtN(g.lives)}</td>
                              <td className="px-3 py-2 text-right" style={{ color: theme.t2 }}>{fmtN(g.in_month)}</td>
                              <td className="px-3 py-2 text-right" style={{ color: theme.text }}>{fmtN(g.active_lives)}</td>
                              <td className="px-3 py-2 text-right" style={{ color: theme.t2 }}>{g.main_reason || DASH}</td>
                            </tr>
                          ))}
                          <tr style={{ background: theme.g100, fontWeight: 700 }}>
                            <td className="px-3 py-2" style={{ color: theme.text }}>Total</td>
                            <td className="px-3 py-2 text-right" style={{ color: theme.text }}>
                              {fmtN(d.cancellations.byGroup.reduce((s, g) => s + g.lives, 0))}
                            </td>
                            <td className="px-3 py-2 text-right" style={{ color: theme.t2 }}>{DASH}</td>
                            <td className="px-3 py-2 text-right" style={{ color: theme.text }}>
                              {fmtN(d.cancellations.byGroup.reduce((s, g) => s + g.active_lives, 0))}
                            </td>
                            <td className="px-3 py-2 text-right" style={{ color: theme.t2 }}>{DASH}</td>
                          </tr>
                        </tbody>
                      </table>
                    </div>
                  )}
                </>
              )}
            </section>

            {/* ── Quote pipeline ─────────────────────────────────── */}
            <h2 className="text-base font-bold pt-1" style={{ color: theme.text }}>Quote pipeline</h2>
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
              {pipeline.map(([label, n], i) => (
                <div key={label} className="p-4 text-center"
                     style={{ borderRadius: 14, background: i >= 3 ? ORANGE : NAVY }}>
                  <div className="text-xs font-semibold"
                       style={{ color: i >= 3 ? NAVY : 'rgba(255,255,255,0.82)' }}>{label}</div>
                  <div className="text-3xl font-bold tabular-nums mt-1"
                       style={{ color: i >= 3 ? NAVY : '#fff' }}>{fmtN(n)}</div>
                  <div className="text-[11px]"
                       style={{ color: i >= 3 ? NAVY : 'rgba(255,255,255,0.82)' }}>quotes</div>
                </div>
              ))}
            </div>

            {/* ── Full monthly table ─────────────────────────────── */}
            <section className="p-4" style={card}>
              <button type="button" onClick={() => setShowTable(v => !v)}
                      aria-expanded={showTable}
                      className="w-full flex items-center justify-between text-sm font-bold py-1"
                      style={{ color: theme.text }}>
                <span>Ops detail — full monthly table (GWP, claims, loss ratio)</span>
                {showTable ? <ChevronUp className="w-4 h-4" style={{ color: ORANGE }} />
                  : <ChevronDown className="w-4 h-4" style={{ color: ORANGE }} />}
              </button>
              {showTable && (
                <div className="overflow-x-auto mt-2">
                  <table className="w-full text-sm tabular-nums">
                    <thead>
                      <tr style={{ background: NAVY, color: '#fff' }}>
                        <th className="text-left px-3 py-2 font-semibold">Month</th>
                        <th className="text-right px-3 py-2 font-semibold">GWP excl VAT</th>
                        <th className="text-right px-3 py-2 font-semibold">GWP incl VAT</th>
                        <th className="text-right px-3 py-2 font-semibold">Claims</th>
                        <th className="text-right px-3 py-2 font-semibold">Loss ratio</th>
                      </tr>
                    </thead>
                    <tbody>
                      {months.map((m, i) => (
                        <MonthRow key={m.month} m={m} zebra={i % 2 === 1} theme={theme} />
                      ))}
                      {d.fyTotals.map(f => (
                        <tr key={f.fy} style={{ background: theme.g100, fontWeight: 700 }}>
                          <td className="px-3 py-2" style={{ color: theme.text }}>
                            {f.label} {f.isCurrent ? 'YTD' : 'total'}
                          </td>
                          <td className="px-3 py-2 text-right" style={{ color: theme.text }}>{fmtP(f.gwpExcl, 2)}</td>
                          <td className="px-3 py-2 text-right" style={{ color: theme.text }}>{fmtP(f.gwpIncl, 2)}</td>
                          <td className="px-3 py-2 text-right" style={{ color: theme.text }}>{fmtP(f.claims, 2)}</td>
                          <td className="px-3 py-2 text-right" style={{ color: theme.text }}>
                            {f.claims && f.gwpExcl ? fmtPct((f.claims / f.gwpExcl) * 100, 2) : DASH}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            {/* ── All areas ──────────────────────────────────────── */}
            <section className="p-4" style={card}>
              <h2 className="font-bold text-sm mb-3" style={{ color: theme.text }}>Health Care — all areas</h2>
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
                {links.map(({ href, icon: Icon, label }) => (
                  <Link key={href} href={href}
                        className="rounded-xl px-3 py-3 flex items-center gap-3"
                        style={{ background: theme.g100 }}>
                    <Icon className="w-5 h-5 shrink-0" style={{ color: ORANGE }} />
                    <span className="text-sm font-medium truncate" style={{ color: theme.text }}>{label}</span>
                    <ArrowRight className="w-3.5 h-3.5 ml-auto shrink-0" style={{ color: theme.t3 }} />
                  </Link>
                ))}
              </div>
            </section>

            {/* ── Footer ─────────────────────────────────────────── */}
            <footer className="text-[11px] leading-relaxed pt-2 pb-6" style={{ color: theme.t3 }}>
              <div>
                Sources — premium: {d.sources.premium}. Claims: {d.sources.claims}.
                Lives: {d.sources.lives}
                {d.sources.registerGroups !== null ? ` (${d.sources.registerGroups} employer groups)` : ''}.
                Pipeline: {d.sources.pipeline}.
              </div>
              <div className="mt-1">
                Inception to date from {d.itd.firstMonth || DASH}: GWP {fmtP(d.itd.gwpIncl, 2)} incl VAT,
                claims {fmtP(d.itd.claims, 2)}. Shown separately from the {d.fy.label} figures above.
              </div>
              <div className="mt-1">Projections are illustrative, not commitments.</div>
            </footer>
          </>
        )}
      </main>
    </div>
  )
}

/* ── small pieces ─────────────────────────────────────────────── */

function fmtK(n: number | null): string {
  if (n === null) return DASH
  return n >= 1000 ? `${Math.round(n / 1000)}k` : `${Math.round(n)}`
}

function Bar({ label, pct, valueLabel, right, theme }: {
  label: string; pct: number; valueLabel: string; right: string
  theme: { text: string; t2: string; g100: string }
}) {
  const known = pct > 0 && Number.isFinite(pct)
  const width = known ? Math.max(2, Math.min(100, pct)) : 0
  const labelInside = width >= 22          // below that the text is wider than the fill
  return (
    <div>
      <div className="text-xs mb-1" style={{ color: theme.t2 }}>{label}</div>
      <div className="relative h-6 rounded-md" style={{ background: theme.g100 }}>
        <div className="h-full rounded-md flex items-center"
             style={{ width: `${width}%`, background: known ? ORANGE : 'transparent' }}>
          {labelInside && (
            <span className="text-[11px] font-bold text-white tabular-nums whitespace-nowrap px-2">
              {valueLabel}
            </span>
          )}
        </div>
        {!labelInside && (
          <span className="absolute top-0 h-6 flex items-center text-[11px] font-bold tabular-nums whitespace-nowrap"
                style={{ left: `calc(${width}% + 6px)`, color: theme.text }}>{valueLabel}</span>
        )}
        <span className="absolute right-2 top-0 h-6 flex items-center text-[11px] tabular-nums"
              style={{ color: theme.text }}>{right}</span>
      </div>
    </div>
  )
}

function ColumnBar({ label, value, pct, colour, theme }: {
  label: string; value: string; pct: number; colour: string
  theme: { t2: string; text: string }
}) {
  return (
    <div className="flex flex-col items-center justify-end"
         style={{ flex: '1 1 0', minWidth: 0, height: '100%' }}>
      <div className="font-bold tabular-nums mb-1"
           style={{ color: theme.text, fontSize: 10 }}>{value}</div>
      <div style={{
        width: '100%', maxWidth: 42, background: colour, borderRadius: '3px 3px 0 0',
        height: `${Math.max(2, Math.min(100, pct))}%`,
      }} />
      <div className="mt-1 text-center w-full overflow-hidden"
           style={{ color: theme.t2, fontSize: 9, whiteSpace: 'nowrap' }}>{label}</div>
    </div>
  )
}

function Key({ colour, label }: { colour: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span style={{ width: 9, height: 9, background: colour, borderRadius: 2, display: 'inline-block' }} />
      {label}
    </span>
  )
}

function MonthRow({ m, zebra, theme }: {
  m: HealthDashMonth; zebra: boolean
  theme: { text: string; t2: string; g50: string }
}) {
  return (
    <tr style={{ background: zebra ? theme.g50 : 'transparent' }}>
      <td className="px-3 py-2" style={{ color: theme.text }}>{m.label}</td>
      <td className="px-3 py-2 text-right" style={{ color: theme.text }}>{fmtP(m.gwpExcl, 2)}</td>
      <td className="px-3 py-2 text-right" style={{ color: theme.text }}>{fmtP(m.gwpIncl, 2)}</td>
      <td className="px-3 py-2 text-right" style={{ color: m.claims === null ? theme.t2 : theme.text }}>
        {fmtP(m.claims, 2)}
      </td>
      <td className="px-3 py-2 text-right" style={{ color: m.lossRatioPct === null ? theme.t2 : theme.text }}>
        {fmtPct(m.lossRatioPct, 2)}
      </td>
    </tr>
  )
}
