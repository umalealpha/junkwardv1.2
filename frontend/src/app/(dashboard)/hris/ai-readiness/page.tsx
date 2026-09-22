'use client'

/**
 * /hris/ai-readiness — Workforce AI Readiness.
 *
 * Calls GET /hris/api/talent/ai-readiness/. Buckets every active
 * employee into Ready / Developing / Aware / Not assessed using
 * (talent segment × personal-effectiveness score). Rolls up by
 * department + computes an org-wide composite score.
 */
import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { useHrisAccess } from '@/hooks/useHrisAccess'
import { authedHrisFetch } from '../_shared'

type Tier = 'Ready' | 'Developing' | 'Aware' | 'Not assessed'

interface Employee {
  profile_id: string
  name: string
  position: string
  department: string
  grade: string
  initials: string
  talent_segment: string
  perf_score: number
  pot_score: number
  pe_score: number
  ai_tier: Tier
  box: { l: string; c: string; t: string }
}
interface DeptRow {
  department: string
  headcount: number
  breakdown: Record<Tier, number>
  score: number
}
interface Response {
  as_of: string
  total: number
  org_score: number
  counts: Record<Tier, number>
  departments: DeptRow[]
  employees: Employee[]
}

const TIER_COLOR: Record<Tier, { bg: string; fg: string; bar: string }> = {
  'Ready':         { bg: '#0A9396', fg: '#fff',    bar: '#0A9396' },
  'Developing':    { bg: '#94D2BD', fg: '#0A2240', bar: '#94D2BD' },
  'Aware':         { bg: '#EE9B00', fg: '#0A2240', bar: '#EE9B00' },
  'Not assessed':  { bg: '#778DA9', fg: '#fff',    bar: '#778DA9' },
}

export default function AiReadinessPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const allowed = useHrisAccess()

  const [data, setData] = useState<Response | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [tierFilter, setTierFilter] = useState<Tier | null>(null)

  useEffect(() => {
    if (allowed === false) router.replace('/dashboard')
  }, [allowed, router])

  useEffect(() => {
    if (allowed !== true) return
    setLoading(true); setError(null)
    authedHrisFetch('/hris/api/talent/ai-readiness/')
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then((d: Response) => setData(d))
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false))
  }, [allowed])

  const visibleEmps = useMemo(() => {
    if (!data) return []
    if (!tierFilter) return data.employees
    return data.employees.filter(e => e.ai_tier === tierFilter)
  }, [data, tierFilter])

  if (allowed !== true) return <Loader theme={theme} />

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar
        title="AI Readiness"
        breadcrumbs={[{ label: 'HRIS', href: '/hris' }, { label: 'AI Readiness' }]}
      />
      <main className="flex-1 overflow-y-auto p-6 space-y-5">
        {error && (
          <div className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-700">{error}</div>
        )}

        {data && (
          <>
            <section
              className="rounded-xl p-5 flex flex-wrap items-center gap-4"
              style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}
            >
              <div>
                <div className="text-[11px] uppercase tracking-wider" style={{ color: theme.t2 }}>Org AI score</div>
                <div className="text-4xl font-bold mt-1" style={{ color: theme.text }}>{data.org_score.toFixed(1)}</div>
                <div className="text-xs" style={{ color: theme.t2 }}>0 = no readiness, 100 = fully ready</div>
              </div>
              <div className="flex-1 min-w-[16rem]">
                <div className="h-4 rounded-full overflow-hidden flex" style={{ background: theme.cardBdr }}>
                  {(Object.keys(TIER_COLOR) as Tier[]).map(t => {
                    const pct = (data.counts[t] || 0) / Math.max(data.total, 1) * 100
                    if (!pct) return null
                    return (
                      <div
                        key={t}
                        title={`${t}: ${data.counts[t]} (${pct.toFixed(0)}%)`}
                        style={{ width: `${pct}%`, background: TIER_COLOR[t].bar }}
                      />
                    )
                  })}
                </div>
                <div className="mt-2 flex flex-wrap gap-2 text-xs">
                  {(Object.keys(TIER_COLOR) as Tier[]).map(t => (
                    <span key={t} className="inline-flex items-center gap-1.5">
                      <span className="w-2.5 h-2.5 rounded-sm" style={{ background: TIER_COLOR[t].bar }} />
                      <span style={{ color: theme.t2 }}>{t}: <strong style={{ color: theme.text }}>{data.counts[t] || 0}</strong></span>
                    </span>
                  ))}
                </div>
              </div>
            </section>

            <section className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              {(Object.keys(TIER_COLOR) as Tier[]).map(t => {
                const active = tierFilter === t
                return (
                  <button
                    key={t}
                    type="button"
                    onClick={() => setTierFilter(active ? null : t)}
                    className="text-left rounded-xl p-3"
                    style={{
                      background: TIER_COLOR[t].bg,
                      color: TIER_COLOR[t].fg,
                      outline: active ? `3px solid ${theme.text}` : 'none',
                      opacity: tierFilter && !active ? 0.55 : 1,
                    }}
                  >
                    <div className="text-[10px] uppercase tracking-wider opacity-85">{t}</div>
                    <div className="text-2xl font-bold mt-1">{data.counts[t] || 0}</div>
                    <div className="text-[11px] opacity-90">
                      {((data.counts[t] || 0) / Math.max(data.total, 1) * 100).toFixed(0)}% of workforce
                    </div>
                  </button>
                )
              })}
            </section>

            <section
              className="rounded-xl p-4"
              style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}
            >
              <h2 className="font-semibold mb-3" style={{ color: theme.text }}>Department Rollup</h2>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr style={{ color: theme.t2 }}>
                      <th className="text-left py-2 px-2 font-medium">Department</th>
                      <th className="text-right py-2 px-2 font-medium">Headcount</th>
                      <th className="text-right py-2 px-2 font-medium">Ready</th>
                      <th className="text-right py-2 px-2 font-medium">Developing</th>
                      <th className="text-right py-2 px-2 font-medium">Aware</th>
                      <th className="text-right py-2 px-2 font-medium">Score</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.departments.map(d => (
                      <tr key={d.department} className="border-t" style={{ borderColor: theme.cardBdr }}>
                        <td className="py-2 px-2 font-medium" style={{ color: theme.text }}>{d.department}</td>
                        <td className="py-2 px-2 text-right" style={{ color: theme.t2 }}>{d.headcount}</td>
                        <td className="py-2 px-2 text-right">{d.breakdown.Ready || 0}</td>
                        <td className="py-2 px-2 text-right">{d.breakdown.Developing || 0}</td>
                        <td className="py-2 px-2 text-right">{d.breakdown.Aware || 0}</td>
                        <td className="py-2 px-2 text-right font-mono font-semibold" style={{ color: theme.text }}>{d.score.toFixed(1)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section
              className="rounded-xl p-4"
              style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}
            >
              <div className="flex items-center justify-between mb-3">
                <h2 className="font-semibold" style={{ color: theme.text }}>
                  Employees — {visibleEmps.length}
                  {tierFilter ? ` in "${tierFilter}"` : ''}
                </h2>
                {tierFilter && (
                  <button
                    type="button"
                    onClick={() => setTierFilter(null)}
                    className="text-xs underline"
                    style={{ color: theme.t2 }}
                  >Clear filter</button>
                )}
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr style={{ color: theme.t2 }}>
                      <th className="text-left py-2 px-2 font-medium">Employee</th>
                      <th className="text-left py-2 px-2 font-medium">Department</th>
                      <th className="text-left py-2 px-2 font-medium">9-Box</th>
                      <th className="text-left py-2 px-2 font-medium">AI Tier</th>
                      <th className="text-right py-2 px-2 font-medium">PE</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibleEmps.slice(0, 200).map(e => (
                      <tr key={e.profile_id} className="border-t" style={{ borderColor: theme.cardBdr }}>
                        <td className="py-2 px-2">
                          <div className="flex items-center gap-2">
                            <span
                              className="inline-flex w-7 h-7 items-center justify-center rounded-full text-[10px] font-bold"
                              style={{ background: e.box.c, color: e.box.t }}
                            >{e.initials}</span>
                            <div>
                              <div className="font-medium" style={{ color: theme.text }}>{e.name}</div>
                              <div className="text-[11px]" style={{ color: theme.t2 }}>{e.position}</div>
                            </div>
                          </div>
                        </td>
                        <td className="py-2 px-2" style={{ color: theme.t2 }}>{e.department || '—'}</td>
                        <td className="py-2 px-2">
                          <span
                            className="inline-block rounded px-2 py-0.5 text-[11px] font-medium"
                            style={{ background: e.box.c + '22', color: e.box.c }}
                          >{e.box.l}</span>
                        </td>
                        <td className="py-2 px-2">
                          <span
                            className="inline-block rounded px-2 py-0.5 text-[11px] font-medium"
                            style={{ background: TIER_COLOR[e.ai_tier].bg + '22', color: TIER_COLOR[e.ai_tier].bg }}
                          >{e.ai_tier}</span>
                        </td>
                        <td className="py-2 px-2 text-right font-mono" style={{ color: theme.text }}>{e.pe_score.toFixed(2)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        )}

        {loading && !data && (
          <div className="rounded-xl p-6 text-center text-sm" style={{ background: theme.card, color: theme.t2 }}>Loading AI readiness…</div>
        )}
      </main>
    </div>
  )
}

function Loader({ theme }: { theme: { bg: string } }) {
  return (
    <div className="min-h-screen flex items-center justify-center" style={{ backgroundColor: theme.bg }}>
      <div className="w-8 h-8 border-2 border-[#F07F00] border-t-transparent rounded-full animate-spin" />
    </div>
  )
}
