'use client'

/**
 * /hris/command-center — Workforce Command Center.
 *
 * Monthly workforce dashboard for the CFO: Time Doctor tracking, workday
 * shortfalls, excuses, manager SLA, tasks, flight risk, salary at risk,
 * recognition, trends and source-record cross-checks. Deterministic numbers;
 * no AI calls in this release.
 */
import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { ChevronLeft, Info, ShieldAlert, AlertTriangle, CheckCircle2 } from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import FeatureAcceptBar from '@/components/hris/FeatureAcceptBar'
import { useTheme } from '@/contexts/ThemeContext'
import { useHrisAccess } from '@/hooks/useHrisAccess'
import { apiFetch } from '@/lib/api'

interface BandCounts {
  high: number
  med: number
  low: number
}

interface FlightRiskRow {
  name: string
  department: string
  band: 'high' | 'med' | 'low'
  score: number
}

interface CommandCenterResponse {
  month: string
  named: boolean
  window: { first: string; last: string }
  td: {
    days_with_data: number
    latest_as_of: string | null
    total_tracked_hours: number
    avg_productive_pct: number | null
    no_tracking_person_days: number
    stale: boolean
    excluded_days?: string[]
    partial_days?: string[]
  }
  shortfall: {
    person_days_short: number
    hours_short: string
    by_status: Record<string, number>
  }
  excuses: {
    by_reason: Record<string, number>
    most_used: string | null
    repeated_people: number
    unresolved: number
  }
  manager_sla: {
    available: boolean
    reason?: string
    median_hours?: number | null
    waiting_over_48h?: number
  }
  tasks: {
    open_overdue: number
    completed_in_window: number
    by_assignee: Array<{ name: string; overdue_count: number }>
  }
  flight_risk: {
    counts: BandCounts
    results?: FlightRiskRow[]
  }
  salary_at_risk: {
    bwp: string | null
    people: number
    basis: string
    breakdown?: Array<{ name: string; department: string; gross: string | null }>
  }
  recognition: {
    count: number
    top_recipients: Array<{ name: string; count: number }>
  }
  trend: Array<{
    month: string
    tracked_hours: number
    person_days_short: number
    open_overdue_tasks: number
  }>
  cross_check: {
    snapshot_rows: number
    justification_rows: number
    task_rows: number
    employees_in_scope: number
  }
}

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'

const BAND_STYLE = {
  high: { bg: '#C1121F', fg: '#FFFFFF', label: 'High' },
  med:  { bg: '#EE9B00', fg: '#0A2240', label: 'Medium' },
  low:  { bg: '#94D2BD', fg: '#0A2240', label: 'Low' },
} as const

function lastFullMonth(): string {
  const now = new Date()
  const firstOfCurrent = new Date(now.getFullYear(), now.getMonth(), 1)
  const lastFull = new Date(firstOfCurrent.getTime() - 24 * 60 * 60 * 1000)
  return `${lastFull.getFullYear()}-${String(lastFull.getMonth() + 1).padStart(2, '0')}`
}

function currentMonth(): string {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
}

const dateFmt = new Intl.DateTimeFormat('en-BW', { dateStyle: 'medium' })
const numberFmt = new Intl.NumberFormat('en-BW', { maximumFractionDigits: 0 })
const decimalFmt = new Intl.NumberFormat('en-BW', { maximumFractionDigits: 1 })

function formatMonth(value: string): string {
  const [y, m] = value.split('-').map(Number)
  return new Intl.DateTimeFormat('en-BW', { month: 'short', year: 'numeric' }).format(new Date(y, m - 1, 1))
}

export default function WorkforceCommandCenterPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const allowed = useHrisAccess()

  const [data, setData] = useState<CommandCenterResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [month, setMonth] = useState(lastFullMonth)

  useEffect(() => {
    if (allowed === false) router.replace('/dashboard')
  }, [allowed, router])

  useEffect(() => {
    if (allowed !== true) return
    setLoading(true)
    setError(null)
    apiFetch<CommandCenterResponse>(`/hris/command-center/?month=${month}`)
      .then(d => setData(d))
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false))
  }, [allowed, month])

  const maxTrend = useMemo(() => {
    if (!data) return { hours: 1, short: 1, overdue: 1 }
    return {
      hours: Math.max(...data.trend.map(t => t.tracked_hours), 1),
      short: Math.max(...data.trend.map(t => t.person_days_short), 1),
      overdue: Math.max(...data.trend.map(t => t.open_overdue_tasks), 1),
    }
  }, [data])

  if (allowed !== true) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ backgroundColor: theme.bg }}>
        <div className="w-8 h-8 border-2 border-[#F07F00] border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  const td = data?.td
  const shortfall = data?.shortfall
  const excuses = data?.excuses
  const managerSla = data?.manager_sla
  const tasks = data?.tasks
  const flight = data?.flight_risk
  const salary = data?.salary_at_risk
  const recognition = data?.recognition

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar
        title="Workforce Command Center"
        breadcrumbs={[{ label: 'HRIS', href: '/hris' }, { label: 'Workforce Command Center' }]}
      />
      <main className="flex-1 overflow-y-auto p-6 space-y-5">
        <Link href="/hris" className="inline-flex items-center gap-1.5 text-sm font-medium" style={{ color: theme.t2 }}>
          <ChevronLeft className="w-4 h-4" /> Back to HRIS
        </Link>

        <FeatureAcceptBar featureKey="workforce_command_center" />

        {error && (
          <div className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-700">{error}</div>
        )}

        {td?.stale && (
          <div className="rounded-2xl p-4 flex items-start gap-3" style={{ background: NAVY }}>
            <Info className="w-5 h-5 flex-shrink-0 mt-0.5" style={{ color: ORANGE }} />
            <p className="text-sm leading-relaxed" style={{ color: '#FFFFFF' }}>
              Time Doctor data up to {td.latest_as_of ? new Date(td.latest_as_of).toLocaleDateString('en-BW') : 'no data'}
              {' '}· the Time Doctor API has been down since 17-Sep.
            </p>
          </div>
        )}
        {td && ((td.excluded_days?.length ?? 0) > 0 || (td.partial_days?.length ?? 0) > 0) && (
          <p className="text-xs text-gray-600 dark:text-gray-400">
            {(td.excluded_days?.length ?? 0) > 0 && <>Left out (impossible totals, e.g. a bulk load): {td.excluded_days!.join(', ')}. </>}
            {(td.partial_days?.length ?? 0) > 0 && <>Only partly loaded, so hours read low: {td.partial_days!.join(', ')}.</>}
          </p>
        )}

        <div className="flex flex-col sm:flex-row sm:items-end gap-3">
          <label className="text-xs font-semibold uppercase tracking-wider flex flex-col gap-1" style={{ color: theme.t2 }}>
            Month
            <input
              type="month"
              value={month}
              onChange={e => setMonth(e.target.value)}
              className="px-3 py-2.5 rounded-lg text-sm outline-none"
              style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }}
            />
          </label>
          <button
            onClick={() => setMonth(currentMonth())}
            className="px-4 py-2.5 rounded-lg text-sm font-semibold"
            style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.t2 }}
          >
            This month so far
          </button>
        </div>

        {/* Tiles */}
        <section className="grid grid-cols-3 gap-3">
          <div className="rounded-xl p-4" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
            <div className="flex items-center justify-between">
              <span className="text-[11px] uppercase tracking-wider" style={{ color: theme.t2 }}>Tracked hours</span>
            </div>
            <div className="text-2xl font-bold mt-1.5" style={{ color: theme.text }}>
              {loading ? '—' : decimalFmt.format(td?.total_tracked_hours ?? 0)}
            </div>
            <div className="text-xs" style={{ color: theme.t2 }}>{td?.days_with_data ?? 0} days with data</div>
          </div>

          <div className="rounded-xl p-4" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
            <div className="flex items-center justify-between">
              <span className="text-[11px] uppercase tracking-wider" style={{ color: theme.t2 }}>Avg productive %</span>
            </div>
            <div className="text-2xl font-bold mt-1.5" style={{ color: theme.text }}>
              {loading ? '—' : td?.avg_productive_pct != null ? `${decimalFmt.format(td.avg_productive_pct)}%` : '—'}
            </div>
          </div>

          <div className="rounded-xl p-4" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
            <div className="flex items-center justify-between">
              <span className="text-[11px] uppercase tracking-wider" style={{ color: theme.t2 }}>No-tracking person-days</span>
            </div>
            <div className="text-2xl font-bold mt-1.5" style={{ color: theme.text }}>
              {loading ? '—' : numberFmt.format(td?.no_tracking_person_days ?? 0)}
            </div>
          </div>

          <div className="rounded-xl p-4" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
            <div className="flex items-center justify-between">
              <span className="text-[11px] uppercase tracking-wider" style={{ color: theme.t2 }}>Person-days short</span>
            </div>
            <div className="text-2xl font-bold mt-1.5" style={{ color: theme.text }}>
              {loading ? '—' : numberFmt.format(shortfall?.person_days_short ?? 0)}
            </div>
            <div className="text-xs" style={{ color: theme.t2 }}>{shortfall?.hours_short ?? '0'} hours short</div>
          </div>

          <div className="rounded-xl p-4" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
            <div className="flex items-center justify-between">
              <span className="text-[11px] uppercase tracking-wider" style={{ color: theme.t2 }}>Late starts</span>
            </div>
            <div className="text-sm mt-1.5 leading-relaxed" style={{ color: theme.text }}>
              Not available — start times are not stored; needs the Time Doctor worklog
            </div>
          </div>

          <div className="rounded-xl p-4" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
            <div className="flex items-center justify-between">
              <span className="text-[11px] uppercase tracking-wider" style={{ color: theme.t2 }}>Unresolved excuses</span>
            </div>
            <div className="text-2xl font-bold mt-1.5" style={{ color: theme.text }}>
              {loading ? '—' : numberFmt.format(excuses?.unresolved ?? 0)}
            </div>
          </div>

          <div className="rounded-xl p-4" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
            <div className="flex items-center justify-between">
              <span className="text-[11px] uppercase tracking-wider" style={{ color: theme.t2 }}>Open overdue tasks</span>
            </div>
            <div className="text-2xl font-bold mt-1.5" style={{ color: theme.text }}>
              {loading ? '—' : numberFmt.format(tasks?.open_overdue ?? 0)}
            </div>
          </div>

          <div className="rounded-xl p-4" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
            <div className="flex items-center justify-between">
              <span className="text-[11px] uppercase tracking-wider" style={{ color: theme.t2 }}>High flight risk</span>
              <ShieldAlert className="w-4 h-4" style={{ color: BAND_STYLE.high.bg }} />
            </div>
            <div className="text-2xl font-bold mt-1.5" style={{ color: theme.text }}>
              {loading ? '—' : numberFmt.format(flight?.counts.high ?? 0)}
            </div>
          </div>

          <div className="rounded-xl p-4" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
            <div className="flex items-center justify-between">
              <span className="text-[11px] uppercase tracking-wider" style={{ color: theme.t2 }}>Salary at risk</span>
            </div>
            <div className="text-xl font-bold mt-1.5" style={{ color: theme.text }}>
              {loading ? '—' : salary?.bwp ? `BWP ${numberFmt.format(Number(salary.bwp))}` : '—'}
            </div>
            <div className="text-xs mt-0.5" style={{ color: theme.t2 }}>{salary?.basis ?? '—'}</div>
          </div>
        </section>

        {/* Excuses */}
        <section className="rounded-2xl p-4 space-y-3" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
          <h2 className="font-semibold" style={{ color: theme.text }}>Excuses</h2>
          <div className="flex flex-wrap gap-2 text-sm" style={{ color: theme.t2 }}>
            <span>Most used: <strong style={{ color: theme.text }}>{excuses?.most_used ?? '—'}</strong></span>
            <span>· Repeat people (≥3): <strong style={{ color: theme.text }}>{excuses?.repeated_people ?? 0}</strong></span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <caption className="sr-only">Excuses by reason</caption>
              <thead>
                <tr className="text-left text-[11px] uppercase tracking-wider" style={{ color: theme.t2 }}>
                  <th className="py-2.5 pr-4 font-semibold">Reason</th>
                  <th className="py-2.5 pr-4 font-semibold text-right">Count</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(excuses?.by_reason ?? {}).map(([reason, count]) => (
                  <tr key={reason} className="border-t" style={{ borderColor: theme.cardBdr }}>
                    <td className="py-2.5 pr-4" style={{ color: theme.text }}>{reason}</td>
                    <td className="py-2.5 pr-4 text-right font-mono" style={{ color: theme.text }}>{count}</td>
                  </tr>
                ))}
                {!loading && Object.keys(excuses?.by_reason ?? {}).length === 0 && (
                  <tr>
                    <td colSpan={2} className="py-4 text-center" style={{ color: theme.t2 }}>No excuse rows in this month.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>

        {/* Manager SLA */}
        <section className="rounded-2xl p-4 space-y-2" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
          <h2 className="font-semibold" style={{ color: theme.text }}>Manager action SLA</h2>
          {!managerSla?.available ? (
            <p style={{ color: theme.t2 }}>{managerSla?.reason ?? '—'}</p>
          ) : (
            <div className="flex flex-wrap gap-4 text-sm" style={{ color: theme.t2 }}>
              <span>Median hours to decision: <strong style={{ color: theme.text }}>{managerSla.median_hours != null ? decimalFmt.format(managerSla.median_hours) : '—'}</strong></span>
              <span>Waiting over 48h: <strong style={{ color: theme.text }}>{managerSla.waiting_over_48h ?? 0}</strong></span>
            </div>
          )}
        </section>

        {/* Tasks */}
        <section className="rounded-2xl p-4 space-y-2" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
          <h2 className="font-semibold" style={{ color: theme.text }}>Tasks</h2>
          <p className="text-sm" style={{ color: theme.t2 }}>
            Completed in window: <strong style={{ color: theme.text }}>{tasks?.completed_in_window ?? 0}</strong>
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <caption className="sr-only">Top assignees by overdue count</caption>
              <thead>
                <tr className="text-left text-[11px] uppercase tracking-wider" style={{ color: theme.t2 }}>
                  <th className="py-2.5 pr-4 font-semibold">Assignee</th>
                  <th className="py-2.5 pr-4 font-semibold text-right">Overdue</th>
                </tr>
              </thead>
              <tbody>
                {loading && [0,1,2].map(i => (
                  <tr key={i} className="border-t" style={{ borderColor: theme.cardBdr }}>
                    <td className="py-3 pr-4"><div className="h-3 w-24 rounded animate-pulse" style={{ background: theme.g100 }} /></td>
                    <td className="py-3 pr-4"><div className="h-3 w-8 rounded animate-pulse ml-auto" style={{ background: theme.g100 }} /></td>
                  </tr>
                ))}
                {!loading && tasks?.by_assignee.map(row => (
                  <tr key={row.name} className="border-t" style={{ borderColor: theme.cardBdr }}>
                    <td className="py-2.5 pr-4" style={{ color: theme.text }}>{row.name}</td>
                    <td className="py-2.5 pr-4 text-right font-mono" style={{ color: theme.text }}>{row.overdue_count}</td>
                  </tr>
                ))}
                {!loading && tasks?.by_assignee.length === 0 && (
                  <tr>
                    <td colSpan={2} className="py-4 text-center" style={{ color: theme.t2 }}>No open overdue tasks.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>

        {/* Flight risk register */}
        <section className="rounded-2xl p-4 space-y-3" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
          <h2 className="font-semibold" style={{ color: theme.text }}>Flight-risk register</h2>
          <div className="flex gap-3 text-sm" style={{ color: theme.t2 }}>
            <span style={{ color: BAND_STYLE.high.fg, background: BAND_STYLE.high.bg, padding: '2px 8px', borderRadius: 999 }}>High {flight?.counts.high ?? 0}</span>
            <span style={{ color: BAND_STYLE.med.fg, background: BAND_STYLE.med.bg, padding: '2px 8px', borderRadius: 999 }}>Medium {flight?.counts.med ?? 0}</span>
            <span style={{ color: BAND_STYLE.low.fg, background: BAND_STYLE.low.bg, padding: '2px 8px', borderRadius: 999 }}>Low {flight?.counts.low ?? 0}</span>
          </div>
          {data?.named && flight?.results && (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <caption className="sr-only">Flight-risk named detail</caption>
                <thead>
                  <tr className="text-left text-[11px] uppercase tracking-wider" style={{ color: theme.t2 }}>
                    <th className="py-2.5 pr-4 font-semibold">Name</th>
                    <th className="py-2.5 pr-4 font-semibold">Department</th>
                    <th className="py-2.5 pr-4 font-semibold text-right">Score</th>
                    <th className="py-2.5 pr-4 font-semibold text-right">Band</th>
                  </tr>
                </thead>
                <tbody>
                  {flight.results.map(r => (
                    <tr key={r.name} className="border-t" style={{ borderColor: theme.cardBdr }}>
                      <td className="py-2.5 pr-4" style={{ color: theme.text }}>{r.name}</td>
                      <td className="py-2.5 pr-4" style={{ color: theme.t2 }}>{r.department || '—'}</td>
                      <td className="py-2.5 pr-4 text-right font-mono" style={{ color: theme.text }}>{r.score}</td>
                      <td className="py-2.5 pr-4 text-right">
                        <span className="inline-flex px-2 py-0.5 rounded text-[11px] font-semibold uppercase" style={{ background: BAND_STYLE[r.band].bg, color: BAND_STYLE[r.band].fg }}>
                          {BAND_STYLE[r.band].label}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {/* Recognition */}
        <section className="rounded-2xl p-4 space-y-2" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
          <h2 className="font-semibold" style={{ color: theme.text }}>Recognition</h2>
          <p className="text-sm" style={{ color: theme.t2 }}>Total kudos this month: <strong style={{ color: theme.text }}>{recognition?.count ?? 0}</strong></p>
          <div className="flex flex-wrap gap-2">
            {recognition?.top_recipients.map(r => (
              <span key={r.name} className="px-2 py-1 rounded text-xs font-medium" style={{ background: theme.g100, color: theme.t2 }}>
                {r.name} · {r.count}
              </span>
            ))}
            {!loading && recognition?.top_recipients.length === 0 && (
              <span style={{ color: theme.t2 }}>No recognition in this month.</span>
            )}
          </div>
        </section>

        {/* Trend */}
        <section className="rounded-2xl p-4 space-y-3" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
          <h2 className="font-semibold" style={{ color: theme.text }}>4-month trend</h2>
          <table className="w-full text-sm">
            <caption className="sr-only">Trend for tracked hours, person-days short, open overdue tasks</caption>
            <thead>
              <tr className="text-left text-[11px] uppercase tracking-wider" style={{ color: theme.t2 }}>
                <th className="py-2 pr-4 font-semibold">Month</th>
                <th className="py-2 pr-4 font-semibold w-1/4">Tracked hours</th>
                <th className="py-2 pr-4 font-semibold w-1/4">Short days</th>
                <th className="py-2 pr-4 font-semibold w-1/4">Overdue</th>
              </tr>
            </thead>
            <tbody>
              {data?.trend.map(item => (
                <tr key={item.month} className="border-t" style={{ borderColor: theme.cardBdr }}>
                  <td className="py-2.5 pr-4 font-medium" style={{ color: theme.text }}>{formatMonth(item.month)}</td>
                  <td className="py-2.5 pr-4">
                    <div className="h-2 rounded" style={{ background: theme.g100 }}>
                      <div className="h-2 rounded" style={{ width: `${(item.tracked_hours / maxTrend.hours) * 100}%`, background: NAVY }} />
                    </div>
                    <span className="text-xs" style={{ color: theme.t2 }}>{decimalFmt.format(item.tracked_hours)}</span>
                  </td>
                  <td className="py-2.5 pr-4">
                    <div className="h-2 rounded" style={{ background: theme.g100 }}>
                      <div className="h-2 rounded" style={{ width: `${(item.person_days_short / maxTrend.short) * 100}%`, background: ORANGE }} />
                    </div>
                    <span className="text-xs" style={{ color: theme.t2 }}>{numberFmt.format(item.person_days_short)}</span>
                  </td>
                  <td className="py-2.5 pr-4">
                    <div className="h-2 rounded" style={{ background: theme.g100 }}>
                      <div className="h-2 rounded" style={{ width: `${(item.open_overdue_tasks / maxTrend.overdue) * 100}%`, background: '#C1121F' }} />
                    </div>
                    <span className="text-xs" style={{ color: theme.t2 }}>{numberFmt.format(item.open_overdue_tasks)}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        {/* Salary at risk named breakdown */}
        {data?.named && salary?.breakdown && (
          <section className="rounded-2xl p-4 space-y-2" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
            <h2 className="font-semibold" style={{ color: theme.text }}>Salary at risk — named breakdown</h2>
            <table className="w-full text-sm">
              <caption className="sr-only">Salary at risk per high-band employee</caption>
              <thead>
                <tr className="text-left text-[11px] uppercase tracking-wider" style={{ color: theme.t2 }}>
                  <th className="py-2.5 pr-4 font-semibold">Name</th>
                  <th className="py-2.5 pr-4 font-semibold">Department</th>
                  <th className="py-2.5 pr-4 font-semibold text-right">Latest gross</th>
                </tr>
              </thead>
              <tbody>
                {salary.breakdown.map(row => (
                  <tr key={row.name} className="border-t" style={{ borderColor: theme.cardBdr }}>
                    <td className="py-2.5 pr-4" style={{ color: theme.text }}>{row.name}</td>
                    <td className="py-2.5 pr-4" style={{ color: theme.t2 }}>{row.department || '—'}</td>
                    <td className="py-2.5 pr-4 text-right font-mono" style={{ color: theme.text }}>
                      {row.gross ? `BWP ${numberFmt.format(Number(row.gross))}` : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )}

        {/* Cross-check footer */}
        <footer className="rounded-2xl p-4 text-xs" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}`, color: theme.t2 }}>
          <strong style={{ color: theme.text }}>Cross-check totals:</strong>{' '}
          {data?.cross_check.snapshot_rows ?? 0} Time Doctor rows ·{' '}
          {data?.cross_check.justification_rows ?? 0} workday justifications ·{' '}
          {data?.cross_check.task_rows ?? 0} tasks ·{' '}
          {data?.cross_check.employees_in_scope ?? 0} employees in scope
        </footer>
      </main>
    </div>
  )
}
