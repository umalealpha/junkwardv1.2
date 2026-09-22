'use client'

/**
 * /hris/flight-risk — Flight-Risk Radar.
 *
 * Calls GET /api/v1/hris/flight-risk/. Computes an INDICATIVE early-warning
 * score for every active employee from signals already on record (leave,
 * check-ins, tenure, recognition) so HR can start a supportive conversation
 * before someone hands in notice. Never a verdict on any one person — the
 * disclaimer banner below stays on screen at all times, not tucked away.
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

type Band = 'high' | 'med' | 'low'

interface FlightRiskRow {
  profile_id: string
  name: string
  department: string
  job_title: string
  score: number
  band: Band
  reasons: string[]
}

interface FlightRiskResponse {
  generated_at: string
  count: number
  high: number
  results: FlightRiskRow[]
}

// Same risk-colour language as /hris/succession — fixed hex regardless of
// dashboard theme, because a risk badge should mean the same thing whether
// the CFO is in Light, Fun or Heavenly mode.
const BAND_STYLE: Record<Band, { bg: string; fg: string; label: string }> = {
  high: { bg: '#C1121F', fg: '#FFFFFF', label: 'High' },
  med:  { bg: '#EE9B00', fg: '#0A2240', label: 'Medium' },
  low:  { bg: '#94D2BD', fg: '#0A2240', label: 'Low' },
}

// Brand navy/orange, hardcoded for the same reason — the disclaimer banner
// must look identical in every theme, not shift with the dashboard skin.
const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'

export default function FlightRiskPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const allowed = useHrisAccess()

  const [data, setData] = useState<FlightRiskResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [dept, setDept] = useState('All')
  const [bandFilter, setBandFilter] = useState<Band | 'All'>('All')

  useEffect(() => {
    if (allowed === false) router.replace('/dashboard')
  }, [allowed, router])

  useEffect(() => {
    if (allowed !== true) return
    setLoading(true); setError(null)
    apiFetch<FlightRiskResponse>('/hris/flight-risk/')
      .then(d => setData(d))
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false))
  }, [allowed])

  const departments = useMemo(() => {
    const s = new Set<string>()
    data?.results.forEach(r => { if (r.department) s.add(r.department) })
    return ['All', ...Array.from(s).sort()]
  }, [data])

  const summary = useMemo(() => {
    const c: Record<Band, number> = { high: 0, med: 0, low: 0 }
    data?.results.forEach(r => { c[r.band]++ })
    return c
  }, [data])

  const filtered = useMemo(() => {
    if (!data) return []
    return data.results.filter(r => {
      if (dept !== 'All' && r.department !== dept) return false
      if (bandFilter !== 'All' && r.band !== bandFilter) return false
      return true
    })
  }, [data, dept, bandFilter])

  if (allowed !== true) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ backgroundColor: theme.bg }}>
        <div className="w-8 h-8 border-2 border-[#F07F00] border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar
        title="Flight-Risk Radar"
        breadcrumbs={[{ label: 'HRIS', href: '/hris' }, { label: 'Flight-Risk Radar' }]}
      />
      <main className="flex-1 overflow-y-auto p-6 space-y-5">
        <Link href="/hris" className="inline-flex items-center gap-1.5 text-sm font-medium" style={{ color: theme.t2 }}>
          <ChevronLeft className="w-4 h-4" /> Back to HRIS
        </Link>

        {/* Disclaimer — deliberately the first thing anyone sees on this page */}
        <div className="rounded-2xl p-4 flex items-start gap-3" style={{ background: NAVY }}>
          <Info className="w-5 h-5 flex-shrink-0 mt-0.5" style={{ color: ORANGE }} />
          <p className="text-sm leading-relaxed" style={{ color: '#FFFFFF' }}>
            <span className="font-semibold">Indicative early-warning signals</span> — a prompt for a
            supportive conversation, not a decision about anyone. Scores come from existing HR
            records (leave, check-ins, tenure, recognition) and can be wrong or incomplete.
          </p>
        </div>

        <FeatureAcceptBar featureKey="flight_risk" />

        {error && (
          <div className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-700">{error}</div>
        )}

        {/* Summary tiles */}
        <section className="grid grid-cols-3 gap-3">
          <div className="rounded-xl p-4" style={{ background: BAND_STYLE.high.bg, color: BAND_STYLE.high.fg }}>
            <div className="flex items-center justify-between">
              <span className="text-[11px] uppercase tracking-wider opacity-85">High risk</span>
              <ShieldAlert className="w-4 h-4 opacity-85" />
            </div>
            <div className="text-3xl font-bold mt-1.5">{loading ? '—' : summary.high}</div>
          </div>
          <div className="rounded-xl p-4" style={{ background: BAND_STYLE.med.bg, color: BAND_STYLE.med.fg }}>
            <div className="flex items-center justify-between">
              <span className="text-[11px] uppercase tracking-wider opacity-85">Medium risk</span>
              <AlertTriangle className="w-4 h-4 opacity-85" />
            </div>
            <div className="text-3xl font-bold mt-1.5">{loading ? '—' : summary.med}</div>
          </div>
          <div className="rounded-xl p-4" style={{ background: BAND_STYLE.low.bg, color: BAND_STYLE.low.fg }}>
            <div className="flex items-center justify-between">
              <span className="text-[11px] uppercase tracking-wider opacity-85">Low risk</span>
              <CheckCircle2 className="w-4 h-4 opacity-85" />
            </div>
            <div className="text-3xl font-bold mt-1.5">{loading ? '—' : summary.low}</div>
          </div>
        </section>

        <div className="rounded-2xl p-4 space-y-4" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
          <div className="flex flex-col sm:flex-row gap-3 sm:items-center sm:justify-between">
            <div className="flex flex-wrap items-center gap-2">
              {(['All', 'high', 'med', 'low'] as const).map(b => (
                <button
                  key={b}
                  onClick={() => setBandFilter(b)}
                  className="px-3 py-1.5 rounded-full text-xs font-semibold transition-colors"
                  style={bandFilter === b
                    ? { background: NAVY, color: '#FFFFFF' }
                    : { background: theme.g100, color: theme.t2 }}
                >
                  {b === 'All' ? 'All bands' : BAND_STYLE[b].label}
                </button>
              ))}
            </div>
            <select
              value={dept}
              onChange={e => setDept(e.target.value)}
              className="px-3 py-2.5 rounded-lg text-sm outline-none"
              style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }}
            >
              {departments.map(d => <option key={d} value={d}>{d}</option>)}
            </select>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-[11px] uppercase tracking-wider" style={{ color: theme.t2 }}>
                  <th className="py-2.5 pr-4 font-semibold">Name</th>
                  <th className="py-2.5 pr-4 font-semibold">Department</th>
                  <th className="py-2.5 pr-4 font-semibold">Job title</th>
                  <th className="py-2.5 pr-4 font-semibold">Signals</th>
                  <th className="py-2.5 pr-4 font-semibold text-right">Score</th>
                  <th className="py-2.5 pr-4 font-semibold text-right">Band</th>
                </tr>
              </thead>
              <tbody>
                {loading && [0, 1, 2, 3, 4].map(i => (
                  <tr key={i} className="border-t" style={{ borderColor: theme.cardBdr }}>
                    {[0, 1, 2, 3, 4, 5].map(j => (
                      <td key={j} className="py-3 pr-4">
                        <div className="h-3 w-20 rounded animate-pulse" style={{ background: theme.g100 }} />
                      </td>
                    ))}
                  </tr>
                ))}
                {!loading && filtered.length === 0 && (
                  <tr>
                    <td colSpan={6} className="py-10 text-center" style={{ color: theme.t2 }}>
                      {data && data.results.length === 0
                        ? 'No signals yet — nothing to flag. Check back once leave, check-in and recognition data builds up.'
                        : 'No one matches these filters.'}
                    </td>
                  </tr>
                )}
                {!loading && filtered.map(r => (
                  <tr key={r.profile_id} className="border-t hover:bg-black/[0.02] transition-colors" style={{ borderColor: theme.cardBdr }}>
                    <td className="py-3 pr-4 font-medium" style={{ color: theme.text }}>{r.name}</td>
                    <td className="py-3 pr-4" style={{ color: theme.t2 }}>{r.department || '—'}</td>
                    <td className="py-3 pr-4" style={{ color: theme.t2 }}>{r.job_title || '—'}</td>
                    <td className="py-3 pr-4">
                      <div className="flex flex-wrap gap-1 max-w-md">
                        {r.reasons.length === 0 && <span style={{ color: theme.t3 }}>—</span>}
                        {r.reasons.map((reason, i) => (
                          <span key={i} className="px-2 py-0.5 rounded text-[10.5px] font-medium"
                                style={{ background: theme.g100, color: theme.t2 }}>
                            {reason}
                          </span>
                        ))}
                      </div>
                    </td>
                    <td className="py-3 pr-4 text-right font-mono" style={{ color: theme.text }}>{r.score}</td>
                    <td className="py-3 pr-4 text-right">
                      <span className="inline-flex px-2 py-0.5 rounded text-[11px] font-semibold uppercase"
                            style={{ background: BAND_STYLE[r.band].bg, color: BAND_STYLE[r.band].fg }}>
                        {BAND_STYLE[r.band].label}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {data && (
          <p className="text-xs" style={{ color: theme.t2 }}>
            Generated {new Date(data.generated_at).toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' })}
            {' '}· {filtered.length} of {data.results.length} shown
          </p>
        )}
      </main>
    </div>
  )
}
