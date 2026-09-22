'use client'

/**
 * /hris/idp — Individual Development Plan.
 *
 * Calls GET /hris/api/talent/idp/ to pull the current user's IDP. The
 * payload computes competency gaps from the latest performance review,
 * recommends three learning interventions per gap, and lays out a
 * 4-quarter roadmap.
 */
import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { useHrisAccess } from '@/hooks/useHrisAccess'
import { authedHrisFetch } from '../_shared'

interface Gap {
  code: string
  score: number
  recommendations: string[]
}
interface RoadmapItem {
  quarter: string
  starts: string
  theme: string
}
interface IdpResponse {
  profile_id: string
  name: string
  position: string
  department: string
  grade: string
  initials: string
  talent_segment: string
  perf_score: number
  pot_score: number
  values_score: number
  okr_score: number
  box: { l: string; c: string; t: string; a: string }
  gaps: Gap[]
  stretch: string
  roadmap: RoadmapItem[]
  review_period: string | null
}

const COMP_NAMES: Record<string, string> = {
  LD: 'Leadership',
  BU: 'Business Acumen',
  RE: 'Reinsurance / Technical',
  PE: 'Personal Effectiveness',
  DI: 'Diversity & Inclusion',
}

export default function IdpPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const allowed = useHrisAccess()

  const [data, setData] = useState<IdpResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notAvail, setNotAvail] = useState(false)

  useEffect(() => {
    if (allowed === false) router.replace('/dashboard')
  }, [allowed, router])

  useEffect(() => {
    if (allowed !== true) return
    setLoading(true); setError(null); setNotAvail(false)
    authedHrisFetch('/hris/api/talent/idp/')
      .then(async r => {
        // 404 = the signed-in user has no HRIS/employee profile (or no IDP yet).
        // Show a friendly empty-state, not a raw "HTTP 404" error.
        if (r.status === 404) { setNotAvail(true); return null }
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then((d: IdpResponse | null) => { if (d) setData(d) })
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false))
  }, [allowed])

  if (allowed !== true) return <Loader theme={theme} />

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar
        title="Individual Development Plan"
        breadcrumbs={[{ label: 'HRIS', href: '/hris' }, { label: 'IDP' }]}
      />
      <main className="flex-1 overflow-y-auto p-6 space-y-5">
        {error && (
          <div className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-700">{error}</div>
        )}
        {loading && (
          <div className="rounded-xl p-6 text-center text-sm" style={{ background: theme.card, color: theme.t2 }}>Loading your IDP…</div>
        )}

        {notAvail && !loading && (
          <div className="rounded-xl p-8 text-center" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
            <div className="text-base font-semibold" style={{ color: theme.text }}>No development plan yet</div>
            <div className="text-sm mt-2 max-w-md mx-auto" style={{ color: theme.t2 }}>
              An Individual Development Plan is created for staff with an employee/HRIS profile.
              If you should have one, ask HR to link your profile and set up your IDP.
            </div>
          </div>
        )}

        {data && (
          <>
            <section
              className="rounded-xl p-5 flex flex-wrap items-center gap-4"
              style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}
            >
              <span
                className="inline-flex w-14 h-14 items-center justify-center rounded-full text-base font-bold"
                style={{ background: data.box.c, color: data.box.t }}
              >{data.initials}</span>
              <div className="flex-1 min-w-[12rem]">
                <div className="text-[11px] uppercase tracking-wider" style={{ color: theme.t2 }}>
                  {data.review_period ? `Latest review: ${data.review_period}` : 'No review on file'}
                </div>
                <h2 className="text-lg font-semibold" style={{ color: theme.text }}>{data.name}</h2>
                <div className="text-sm" style={{ color: theme.t2 }}>{data.position} — {data.department} — {data.grade || '—'}</div>
              </div>
              <div
                className="rounded-lg px-3 py-2"
                style={{ background: data.box.c, color: data.box.t }}
              >
                <div className="text-[10px] uppercase tracking-wider opacity-90">9-Box</div>
                <div className="font-semibold">{data.box.l}</div>
              </div>
            </section>

            <section className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <Kpi theme={theme} label="Performance" value={data.perf_score.toFixed(2)} suffix="/ 4" />
              <Kpi theme={theme} label="Potential"   value={data.pot_score.toFixed(2)}  suffix="/ 4" />
              <Kpi theme={theme} label="Values"      value={data.values_score.toFixed(2)} suffix="/ 5" />
              <Kpi theme={theme} label="OKR"         value={data.okr_score.toFixed(2)}  suffix="/ 5" />
            </section>

            <section
              className="rounded-xl p-4"
              style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}
            >
              <h3 className="font-semibold mb-2" style={{ color: theme.text }}>Stretch Assignment</h3>
              <p className="text-sm" style={{ color: theme.t2 }}>{data.stretch}</p>
            </section>

            <section>
              <h3 className="font-semibold mb-2" style={{ color: theme.text }}>Development Gaps</h3>
              {data.gaps.length === 0 ? (
                <div
                  className="rounded-xl p-4 text-sm"
                  style={{ background: theme.card, border: `1px solid ${theme.cardBdr}`, color: theme.t2 }}
                >
                  No competency gaps on the latest review — focus on stretch + visibility this cycle.
                </div>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  {data.gaps.map(g => (
                    <div
                      key={g.code}
                      className="rounded-xl p-4"
                      style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}
                    >
                      <div className="flex items-center justify-between mb-2">
                        <div>
                          <div className="text-[11px] uppercase" style={{ color: theme.t2 }}>{g.code}</div>
                          <div className="font-semibold" style={{ color: theme.text }}>{COMP_NAMES[g.code] || g.code}</div>
                        </div>
                        <span
                          className="text-sm font-mono px-2 py-1 rounded"
                          style={{ background: '#FEF2F2', color: '#B91C1C' }}
                        >{g.score.toFixed(2)} / 4</span>
                      </div>
                      <ul className="text-sm space-y-1.5" style={{ color: theme.text }}>
                        {g.recommendations.map((rec, i) => (
                          <li key={i} className="flex items-start gap-2">
                            <span style={{ color: '#F07F00' }}>▸</span>
                            <span>{rec}</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  ))}
                </div>
              )}
            </section>

            <section
              className="rounded-xl p-4"
              style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}
            >
              <h3 className="font-semibold mb-3" style={{ color: theme.text }}>12-month Roadmap</h3>
              <div className="grid grid-cols-1 sm:grid-cols-4 gap-2">
                {data.roadmap.map(q => (
                  <div
                    key={q.quarter}
                    className="rounded-lg p-3"
                    style={{ background: theme.bg, border: `1px solid ${theme.cardBdr}` }}
                  >
                    <div className="text-[11px] uppercase tracking-wider" style={{ color: theme.t3 }}>{q.quarter} — starts {q.starts}</div>
                    <div className="font-medium mt-1" style={{ color: theme.text }}>{q.theme}</div>
                  </div>
                ))}
              </div>
            </section>
          </>
        )}
      </main>
    </div>
  )
}

function Kpi({
  theme, label, value, suffix,
}: { theme: { card: string; cardBdr: string; text: string; t2: string }; label: string; value: string; suffix: string }) {
  return (
    <div
      className="rounded-xl p-3"
      style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}
    >
      <div className="text-[11px] uppercase tracking-wider" style={{ color: theme.t2 }}>{label}</div>
      <div className="mt-1">
        <span className="text-2xl font-bold font-mono" style={{ color: theme.text }}>{value}</span>
        <span className="ml-1 text-sm" style={{ color: theme.t2 }}>{suffix}</span>
      </div>
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
