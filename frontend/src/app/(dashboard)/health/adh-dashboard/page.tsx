'use client'

/**
 * /health/adh-dashboard — Automated Health Care Dashboard (Medu request BUG-2770863a).
 *
 * Live from system tables — same data sources as the Monthly Summary screens.
 * Three sections:
 *   1. Provider network status (live from /health/service-providers/)
 *   2. Daily growth trend (from /health/service-providers/trend/)
 *   3. Financial summary — read from /health/dashboard/, the SAME feed the Health
 *      Cover screen publishes, so the two screens can never disagree. Money here
 *      is always EXCLUDING VAT.
 */
import { useCallback, useEffect, useState } from 'react'
import {
  RefreshCw, TrendingUp, Users, HeartPulse, ShieldCheck,
  Activity, Loader2,
} from 'lucide-react'
import {
  ResponsiveContainer, AreaChart, Area, BarChart, Bar,
  XAxis, YAxis, Tooltip, CartesianGrid,
} from 'recharts'
import { TopBar } from '@/components/layout/TopBar'
import { apiFetch, getHealthDashboard, type HealthDashboard } from '@/lib/api'
import { financialFigures } from './figures'

const NAVY = '#1D3270'
const ORANGE = '#F47C20'
const GREEN = '#00b853'
const AMBER = '#f59e0b'
const RED = '#ba1a1a'
const INK = '#191c1d'
const MUTE = '#64748b'
const SURF = '#f8f9fa'
const FONT_H = "Montserrat,-apple-system,'Segoe UI',Roboto,Arial,sans-serif"
const FONT_B = "'Open Sans',-apple-system,'Segoe UI',Roboto,Arial,sans-serif"

interface Counts {
  total: number; afa_registered: number; afa_pending: number; adh_ready: number
  registered_not_qc: number; mismatches: number; pending_applications: number
  by_discipline: Record<string, number>
}
interface Snapshot {
  date: string; total: number; afa_registered: number; afa_pending: number
  adh_ready: number; qc_confirmed: number; mismatches: number; pending_applications: number
}

function fmt(n: number) { return n.toLocaleString('en') }
function fmtP(n: number | string | null, dp = 0) {
  if (n === null || n === undefined) return '—'
  return 'P ' + Number(n || 0).toLocaleString('en-BW', { minimumFractionDigits: dp, maximumFractionDigits: dp })
}
// The snapshot job's schedule (infra/cron/snapshot-provider-counts.cron), so the
// empty state names the next real run instead of promising "this evening" on a day
// whose slot has already gone — which is exactly what it did on 9-Sep-2026.
const SNAPSHOT_UTC_HOUR = 16, SNAPSHOT_UTC_MIN = 15
function nextSnapshotText() {
  const next = new Date()
  next.setUTCHours(SNAPSHOT_UTC_HOUR, SNAPSHOT_UTC_MIN, 0, 0)
  if (next.getTime() <= Date.now()) next.setUTCDate(next.getUTCDate() + 1)
  const today = next.toDateString() === new Date().toDateString()
  return `${today ? 'today' : 'tomorrow'} at ` + next.toLocaleTimeString('en-GB', {
    hour: '2-digit', minute: '2-digit',
  })
}

export default function ADHDashboardPage() {
  const [counts, setCounts] = useState<Counts | null>(null)
  const [trend, setTrend] = useState<Snapshot[]>([])
  const [dash, setDash] = useState<HealthDashboard | null>(null)
  const [loading, setLoading] = useState(true)
  const [lastUpdated, setLastUpdated] = useState('')
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [provData, trendData, dashData] = await Promise.all([
        apiFetch<{ counts: Counts }>('/health/service-providers/?active_only=1'),
        apiFetch<{ snapshots: Snapshot[] }>('/health/service-providers/trend/?days=90'),
        getHealthDashboard(),
      ])
      setCounts(provData.counts)
      setTrend(trendData.snapshots)
      setDash(dashData)
      setLastUpdated(new Date().toLocaleString('en-GB', {
        day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
      }))
    } catch (e) { setError(String(e)) } finally { setLoading(false) }
  }, [])
  useEffect(() => { load() }, [load])

  const kpi = dash?.kpi
  const { ytdGwp, ytdClaims, lossRatio, latestGwp } = financialFigures(dash)

  const trendChart = trend.map(s => ({
    date: s.date.slice(5),
    ready: s.adh_ready,
    total: s.total,
    registered: s.afa_registered,
  }))

  const revChart = (dash?.monthly ?? []).map(m => ({
    label: m.label,
    gwp: m.gwpExcl ?? 0,
    claims: m.claims ?? 0,
  }))

  const pct = (n: number, d: number) => d ? Math.round(100 * n / d) : 0


  return (
    <div style={{ fontFamily: FONT_B, background: SURF, minHeight: '100vh' }}>
      <TopBar title="Health — Live Dashboard" />
      <div style={{ maxWidth: 1200, margin: '0 auto', padding: '24px 16px' }}>
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
          <div>
            <h1 style={{ fontFamily: FONT_H, fontSize: 22, fontWeight: 700, color: NAVY, margin: 0 }}>
              <HeartPulse size={22} style={{ verticalAlign: 'text-bottom', marginRight: 8 }} />
              Alpha Direct Health — Live Dashboard
            </h1>
            <p style={{ color: MUTE, fontSize: 13, margin: '4px 0 0' }}>
              Automated from system data · Last refreshed: {lastUpdated || '—'}
            </p>
          </div>
          <button onClick={load} disabled={loading} style={{
            background: NAVY, color: '#fff', border: 'none', borderRadius: 8,
            padding: '8px 16px', cursor: 'pointer', fontSize: 13, fontWeight: 600,
            display: 'flex', alignItems: 'center', gap: 6, fontFamily: FONT_H,
          }}>
            {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
            Refresh
          </button>
        </div>

        {error && (
          <div style={{ background: '#fef2f2', border: `1px solid ${RED}`, borderRadius: 8,
            padding: '12px 16px', marginBottom: 16, color: RED, fontSize: 13 }}>
            Failed to load dashboard data. Please try refreshing.
          </div>
        )}

        {loading && !counts ? (
          <div style={{ textAlign: 'center', padding: 60, color: MUTE }}>
            <Loader2 size={32} className="animate-spin" style={{ marginBottom: 12 }} />
            <div>Loading dashboard…</div>
          </div>
        ) : error ? null : (
          <>
            {/* === SECTION 1: Provider Network Status === */}
            <SectionHeader icon={<Users size={16} />} title="Provider Network" />

            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 20 }}>
              <StatTile label="Total Providers" value={fmt(counts?.total ?? 0)} color={NAVY} />
              <StatTile label="ADH-Ready" value={fmt(counts?.adh_ready ?? 0)} color={GREEN}
                sub={`${pct(counts?.adh_ready ?? 0, counts?.total ?? 0)}% of network`} />
              <StatTile label="AFA Registered" value={fmt(counts?.afa_registered ?? 0)} color={NAVY} />
              <StatTile label="AFA Pending" value={fmt(counts?.afa_pending ?? 0)} color={AMBER} />
              <StatTile label="Needs Chasing" value={fmt(counts?.mismatches ?? 0)} color={RED}
                alert={!!counts?.mismatches} />
              <StatTile label="New Applications" value={fmt(counts?.pending_applications ?? 0)} color={ORANGE} />
            </div>

            {/* Traffic-light bar */}
            {counts && counts.total > 0 && (() => {
              const inProgress = Math.max(0, counts.afa_registered + counts.afa_pending - counts.adh_ready)
              const notStarted = Math.max(0, counts.total - counts.adh_ready - inProgress)
              return (
              // Labels sit UNDER the bar, never inside a segment: a zero or
              // near-zero segment has no width to hold its own text and the
              // label spills past the end of the bar (seen at 1120 and 1280).
              <div style={{ marginBottom: 24 }}>
                <div style={{ display: 'flex', borderRadius: 8, overflow: 'hidden', height: 20, background: '#e5e7eb' }}>
                  {counts.adh_ready > 0 && (
                    <div style={{ width: `${pct(counts.adh_ready, counts.total)}%`, background: GREEN }} />
                  )}
                  {inProgress > 0 && (
                    <div style={{ width: `${pct(inProgress, counts.total)}%`, background: AMBER }} />
                  )}
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16, marginTop: 8, fontSize: 12, color: INK }}>
                  {([
                    [GREEN, counts.adh_ready, 'ready'],
                    [AMBER, inProgress, 'in progress'],
                    ['#9ca3af', notStarted, 'not started'],
                  ] as [string, number, string][]).map(([colour, n, word]) => (
                    <span key={word} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <span style={{ width: 10, height: 10, borderRadius: 2, background: colour, flexShrink: 0 }} />
                      <strong>{fmt(n)}</strong> {word}
                    </span>
                  ))}
                </div>
              </div>
              )
            })()}

            {/* Discipline breakdown */}
            {counts?.by_discipline && Object.keys(counts.by_discipline).length > 0 && (
              <div style={{ background: '#fff', borderRadius: 12, border: '1px solid #e5e7eb', padding: 20, marginBottom: 24 }}>
                <div style={{ fontSize: 13, fontWeight: 700, color: NAVY, marginBottom: 12, fontFamily: FONT_H }}>
                  Coverage by Discipline
                </div>
                {(() => {
                  const entries = Object.entries(counts.by_discipline).slice(0, 8)
                  const max = Math.max(...entries.map(([, v]) => v))
                  return entries.map(([name, count]) => (
                    <div key={name} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                      <div style={{ width: 100, fontSize: 12, color: INK, textAlign: 'right' }}>{name.charAt(0) + name.slice(1).toLowerCase()}</div>
                      <div style={{ flex: 1, background: '#f3f4f6', borderRadius: 4, height: 12 }}>
                        <div style={{ width: `${(count / max) * 100}%`, background: NAVY, height: 12, borderRadius: 4 }} />
                      </div>
                      <div style={{ width: 36, fontSize: 12, color: MUTE, textAlign: 'right' }}>{count}</div>
                    </div>
                  ))
                })()}
              </div>
            )}

            {/* === SECTION 2: Daily Growth Trend === */}
            <SectionHeader icon={<TrendingUp size={16} />} title="Daily Growth Trend" />

            {trendChart.length > 1 ? (
              <div style={{ background: '#fff', borderRadius: 12, border: '1px solid #e5e7eb', padding: 20, marginBottom: 24 }}>
                <div style={{ fontSize: 12, color: MUTE, marginBottom: 8 }}>
                  Provider counts over the last {trend.length} days
                </div>
                <ResponsiveContainer width="100%" height={260}>
                  <AreaChart data={trendChart}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                    <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                    <YAxis tick={{ fontSize: 11 }} />
                    <Tooltip contentStyle={{ fontSize: 12 }} />
                    <Area type="monotone" dataKey="total" stroke="#94a3b8" fill="#f1f5f9" name="Total" />
                    <Area type="monotone" dataKey="registered" stroke={NAVY} fill={NAVY + '20'} name="AFA Registered" />
                    <Area type="monotone" dataKey="ready" stroke={GREEN} fill={GREEN + '30'} name="ADH-Ready" />
                  </AreaChart>
                </ResponsiveContainer>
                {trend.length >= 2 && (() => {
                  const first = trend[0]
                  const last = trend[trend.length - 1]
                  const delta = last.adh_ready - first.adh_ready
                  return (
                    <div style={{ marginTop: 12, fontSize: 13, color: delta >= 0 ? GREEN : RED }}>
                      <strong>{delta >= 0 ? '+' : ''}{delta}</strong> ADH-ready providers since {first.date}
                    </div>
                  )
                })()}
              </div>
            ) : (
              <div style={{ background: '#fff', borderRadius: 12, border: '1px solid #e5e7eb', padding: 32,
                marginBottom: 24, textAlign: 'center', color: MUTE, fontSize: 13 }}>
                <Activity size={24} style={{ marginBottom: 8 }} />
                <div>
                  {trend.length === 0
                    ? 'No daily counts recorded yet — the chart needs two days.'
                    : `Day ${trend.length} of 2 recorded — the chart needs one more.`}
                </div>
                <div style={{ fontSize: 12, marginTop: 4 }}>Next count: {nextSnapshotText()}.</div>
              </div>
            )}

            {/* === SECTION 3: Financial Summary === */}
            <SectionHeader icon={<ShieldCheck size={16} />} title="Financial Summary" />

            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 20 }}>
              <StatTile label={`${dash?.fy?.label ?? 'YTD'} GWP`} value={fmtP(ytdGwp)} color={NAVY}
                sub="excl. VAT" />
              <StatTile label={`${kpi?.gwpMonthLabel ?? 'Latest month'} GWP`} value={fmtP(latestGwp)}
                color={ORANGE} sub="excl. VAT" />
              <StatTile label="Claims Paid" value={fmtP(ytdClaims)} color={RED}
                sub={dash?.fy?.label ?? undefined} />
              <StatTile label="Loss Ratio" value={lossRatio !== null ? `${lossRatio.toFixed(2)}%` : '—'}
                color={lossRatio !== null && lossRatio > 70 ? RED : GREEN}
                sub="claims ÷ GWP excl. VAT"
                alert={lossRatio !== null && lossRatio > 70} />
            </div>

            {revChart.length > 0 && (
              <div style={{ background: '#fff', borderRadius: 12, border: '1px solid #e5e7eb', padding: 20, marginBottom: 24 }}>
                <div style={{ fontSize: 13, fontWeight: 700, color: NAVY, marginBottom: 12, fontFamily: FONT_H }}>
                  Monthly GWP (excl. VAT) vs Claims Paid
                </div>
                <ResponsiveContainer width="100%" height={260}>
                  <BarChart data={revChart}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
                    <XAxis dataKey="label" tick={{ fontSize: 11 }} />
                    <YAxis tick={{ fontSize: 11 }} tickFormatter={v => `P${(v / 1000).toFixed(0)}k`} />
                    <Tooltip formatter={(v) => fmtP(Number(v))} contentStyle={{ fontSize: 12 }} />
                    <Bar dataKey="gwp" name="GWP (excl. VAT)" fill={NAVY} radius={[4, 4, 0, 0]} />
                    <Bar dataKey="claims" name="Claims Paid" fill={RED} radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

function SectionHeader({ icon, title }: { icon: React.ReactNode; title: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14, marginTop: 8 }}>
      <span style={{ color: ORANGE }}>{icon}</span>
      <h2 style={{ fontFamily: FONT_H, fontSize: 15, fontWeight: 700, color: NAVY, margin: 0,
        textTransform: 'uppercase', letterSpacing: '0.04em' }}>
        {title}
      </h2>
      <div style={{ flex: 1, height: 2, background: ORANGE, borderRadius: 1, marginLeft: 8 }} />
    </div>
  )
}

function StatTile({ label, value, color, sub, alert }: {
  label: string; value: string; color: string; sub?: string; alert?: boolean
}) {
  return (
    <div style={{
      background: '#fff', borderRadius: 12, padding: '16px 14px', textAlign: 'center',
      border: alert ? `2px solid ${RED}` : '1px solid #e5e7eb', flex: 1, minWidth: 140,
    }}>
      <div style={{ fontSize: 26, fontWeight: 700, color, fontFamily: FONT_H }}>{value}</div>
      <div style={{ fontSize: 11, color: MUTE, marginTop: 2 }}>{label}</div>
      {sub && <div style={{ fontSize: 10, color: MUTE, marginTop: 2 }}>{sub}</div>}
    </div>
  )
}
