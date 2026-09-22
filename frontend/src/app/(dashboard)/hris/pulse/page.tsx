'use client'

/**
 * /hris/pulse — Staff Pulse / Mood Check.
 *
 * Two zones on one page:
 *   (a) every member of staff (self-service tier) submits THIS week's mood
 *       1-5 + an optional comment — anonymous to HR, one submission/week.
 *   (b) HR/exec (HRIS whitelist) see the aggregate dashboard: mood trend
 *       over the last 12 weeks, average by department, participation %.
 *       Aggregates only — the backend never returns a group under 3 people.
 *
 * Backend: hris/pulse_views.py — POST/GET /api/v1/hris/pulse/{submit,me,dashboard}/
 */
import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  Smile, MessageCircle, Users, TrendingUp, CheckCircle2, AlertCircle, Loader2,
} from 'lucide-react'
import type { Theme } from '@/lib/themes'
import { TopBar } from '@/components/layout/TopBar'
import FeatureAcceptBar from '@/components/hris/FeatureAcceptBar'
import { useTheme } from '@/contexts/ThemeContext'
import { useHrisAccess, useHrisSelfService } from '@/hooks/useHrisAccess'
import { authedHrisFetch } from '../_shared'

const MOODS: { score: 1 | 2 | 3 | 4 | 5; emoji: string; label: string }[] = [
  { score: 1, emoji: '😞', label: 'Struggling' },
  { score: 2, emoji: '😕', label: 'Not great' },
  { score: 3, emoji: '😐', label: 'Okay' },
  { score: 4, emoji: '🙂', label: 'Good' },
  { score: 5, emoji: '😄', label: 'Great' },
]

interface MeResponse {
  submitted_this_week: boolean
  score: number | null
}
interface WeekPoint {
  week_start: string
  avg_score: number | null
  responses: number
}
interface DeptRow {
  department: string
  avg_score: number
  responses: number
}
interface DashboardResponse {
  weeks: WeekPoint[]
  by_department: DeptRow[]
  participation: { responded: number; total_active: number; pct: number }
}

function moodColor(score: number | null, theme: Theme): string {
  if (score === null) return theme.t3
  if (score >= 4) return theme.ok
  if (score >= 2.5) return theme.wr
  return theme.er
}

function fmtWeek(iso: string): string {
  const d = new Date(`${iso}T00:00:00`)
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
}

export default function PulsePage() {
  const router = useRouter()
  const { theme } = useTheme()
  const allowed = useHrisAccess()
  // Self-service (every staff member submits their OWN pulse; no whitelist,
  // mirrors /hris/profile's gating — see hris/layout.tsx SELF_SERVICE_ROUTES).
  const selfService = useHrisSelfService()
  const canView = allowed === true || selfService === true
  const accessDenied = allowed === false && selfService === false

  useEffect(() => {
    if (accessDenied) router.replace('/dashboard')
  }, [accessDenied, router])

  if (!canView) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ backgroundColor: theme.bg }}>
        <div className="w-8 h-8 border-2 border-[#F07F00] border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar title="Pulse Check" breadcrumbs={[{ label: 'HRIS', href: '/hris' }, { label: 'Pulse' }]} />
      <main className="flex-1 overflow-y-auto p-6 space-y-6">
        <FeatureAcceptBar featureKey="pulse" />
        <SubmitWidget theme={theme} />
        {allowed === true && <HrDashboard theme={theme} />}
      </main>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────
// Zone (a) — self-service weekly submit widget
// ─────────────────────────────────────────────────────────────────────────

function SubmitWidget({ theme }: { theme: Theme }) {
  const [me, setMe] = useState<MeResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [notFound, setNotFound] = useState(false)
  const [selected, setSelected] = useState<number | null>(null)
  const [comment, setComment] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)

  useEffect(() => {
    setLoading(true)
    authedHrisFetch('/api/v1/hris/pulse/me/')
      .then(async r => {
        if (!r.ok) return
        const data: MeResponse = await r.json()
        setMe(data)
        if (data.score) setSelected(data.score)
      })
      .finally(() => setLoading(false))
  }, [])

  async function submit() {
    if (!selected) return
    setSaving(true); setError(null)
    try {
      const r = await authedHrisFetch('/api/v1/hris/pulse/submit/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ score: selected, comment }),
      })
      if (r.status === 404) { setNotFound(true); return }
      const data = await r.json().catch(() => ({}))
      if (!r.ok) { setError(data.detail || `HTTP ${r.status}`); return }
      setMe({ submitted_this_week: true, score: selected })
      setEditing(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Network error')
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <section className="rounded-2xl p-6" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
        <p className="text-sm flex items-center gap-2" style={{ color: theme.t2 }}>
          <Loader2 className="w-4 h-4 animate-spin" /> Loading your pulse check…
        </p>
      </section>
    )
  }

  if (notFound) {
    return (
      <section className="rounded-2xl p-6 flex items-start gap-3"
                style={{ background: theme.wrB, border: `1px solid ${theme.wr}40` }}>
        <AlertCircle className="w-5 h-5 flex-shrink-0" style={{ color: theme.wr }} />
        <div className="text-sm" style={{ color: theme.text }}>
          We couldn&apos;t find an HR profile linked to your account, so we can&apos;t record your check-in yet.
          Email <strong>hr@alphadirect.co.bw</strong> to get linked up.
        </div>
      </section>
    )
  }

  if (me?.submitted_this_week && !editing) {
    const mood = MOODS.find(m => m.score === me.score)
    return (
      <section className="rounded-2xl p-6 flex flex-wrap items-center justify-between gap-4"
                style={{ background: `linear-gradient(135deg, ${theme.navy}, ${theme.navy}e6)`, color: '#fff' }}>
        <div className="flex items-center gap-4">
          <div className="text-4xl">{mood?.emoji ?? '✓'}</div>
          <div>
            <div className="font-display text-lg font-bold">Thanks for checking in this week</div>
            <p className="text-sm opacity-80 mt-0.5">
              Your answer is anonymous — HR only ever sees company-wide trends, never who said what.
            </p>
          </div>
        </div>
        <button onClick={() => setEditing(true)}
                className="text-xs font-semibold px-3 py-2 rounded-lg flex-shrink-0"
                style={{ background: 'rgba(255,255,255,0.12)', color: '#fff' }}>
          Change answer
        </button>
      </section>
    )
  }

  return (
    <section className="rounded-2xl p-6" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
      <div className="flex items-center gap-2 mb-1">
        <Smile className="w-5 h-5" style={{ color: theme.orange }} />
        <h2 className="font-display text-lg font-bold" style={{ color: theme.text }}>How are you feeling this week?</h2>
      </div>
      <p className="text-sm mb-5" style={{ color: theme.t2 }}>
        Anonymous — HR sees company-wide trends only, never your individual answer.
      </p>

      <div className="grid grid-cols-5 gap-2 sm:gap-3 mb-5">
        {MOODS.map(m => {
          const active = selected === m.score
          return (
            <button key={m.score} type="button" onClick={() => setSelected(m.score)}
                    className="flex flex-col items-center gap-1.5 rounded-xl py-4 transition-all"
                    style={{
                      background: active ? theme.orange : theme.g100,
                      border: `2px solid ${active ? theme.orange : theme.cardBdr}`,
                      transform: active ? 'scale(1.05)' : 'scale(1)',
                    }}>
              <span className="text-2xl sm:text-3xl">{m.emoji}</span>
              <span className="text-[10px] sm:text-[11px] font-semibold text-center leading-tight"
                    style={{ color: active ? '#fff' : theme.t2 }}>{m.label}</span>
            </button>
          )
        })}
      </div>

      <label className="block text-xs font-semibold uppercase tracking-wider mb-1.5" style={{ color: theme.t2 }}>
        Anything you want HR to know? (optional)
      </label>
      <textarea
        value={comment} onChange={e => setComment(e.target.value)} rows={2} maxLength={1000}
        placeholder="Stays anonymous — only shows up in aggregate, never linked to your name."
        className="w-full px-3 py-2 rounded-lg text-sm outline-none resize-none"
        style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }}
      />

      {error && (
        <div className="mt-3 text-xs flex items-center gap-1.5" style={{ color: theme.er }}>
          <AlertCircle className="w-3.5 h-3.5" /> {error}
        </div>
      )}

      <div className="flex items-center justify-end gap-2 mt-4">
        {editing && (
          <button type="button" onClick={() => setEditing(false)} className="text-sm px-4 py-2 rounded-lg font-medium"
                  style={{ color: theme.t2 }}>Cancel</button>
        )}
        <button type="button" onClick={submit} disabled={!selected || saving}
                className="inline-flex items-center gap-2 px-5 py-2 rounded-lg text-sm font-semibold disabled:opacity-50"
                style={{ background: theme.orange, color: '#fff' }}>
          {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <CheckCircle2 className="w-4 h-4" />}
          {saving ? 'Saving…' : 'Submit check-in'}
        </button>
      </div>
    </section>
  )
}

// ─────────────────────────────────────────────────────────────────────────
// Zone (b) — HR / exec aggregate dashboard (HRIS whitelist only)
// ─────────────────────────────────────────────────────────────────────────

function HrDashboard({ theme }: { theme: Theme }) {
  const [data, setData] = useState<DashboardResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true); setError(null)
    authedHrisFetch('/api/v1/hris/pulse/dashboard/')
      .then(r => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d: DashboardResponse) => setData(d))
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false))
  }, [])

  const weeks = data?.weeks ?? []
  const byDepartment = data?.by_department ?? []
  const hasAnyData = weeks.some(w => w.responses > 0)
  const latest = weeks[weeks.length - 1]
  const prev = weeks[weeks.length - 2]
  const trendDelta = (latest?.avg_score != null && prev?.avg_score != null)
    ? Math.round((latest.avg_score - prev.avg_score) * 10) / 10
    : null

  return (
    <section className="space-y-5">
      <div className="flex items-center gap-2">
        <div className="h-px flex-1" style={{ background: theme.cardBdr }} />
        <span className="text-[11px] uppercase tracking-widest font-semibold" style={{ color: theme.t3 }}>
          HR view — company aggregates
        </span>
        <div className="h-px flex-1" style={{ background: theme.cardBdr }} />
      </div>

      {error && (
        <div className="rounded-md border p-3 text-sm" style={{ borderColor: theme.er, background: theme.erB, color: theme.er }}>{error}</div>
      )}

      {loading && (
        <div className="rounded-xl p-6 text-center text-sm" style={{ background: theme.card, color: theme.t2 }}>
          Loading company pulse…
        </div>
      )}

      {!loading && data && !hasAnyData && (
        <div className="rounded-xl p-8 text-center" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
          <Smile className="w-8 h-8 mx-auto mb-2" style={{ color: theme.t3 }} />
          <p className="text-sm font-medium" style={{ color: theme.text }}>No check-ins yet</p>
          <p className="text-xs mt-1" style={{ color: theme.t2 }}>
            Once staff start submitting their weekly pulse, the company trend, department averages
            and participation rate will appear here.
          </p>
        </div>
      )}

      {!loading && data && hasAnyData && (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <StatTile theme={theme} icon={Smile} label="This week's mood"
                       value={latest?.avg_score != null ? `${latest.avg_score.toFixed(1)} / 5` : '—'}
                       sub={trendDelta == null
                         ? 'No prior week to compare'
                         : trendDelta === 0
                           ? 'Flat vs last week'
                           : `${trendDelta > 0 ? '▲' : '▼'} ${Math.abs(trendDelta).toFixed(1)} vs last week`}
                       accent={moodColor(latest?.avg_score ?? null, theme)} />
            <StatTile theme={theme} icon={Users} label="Participation this week"
                       value={`${data.participation.pct.toFixed(1)}%`}
                       sub={`${data.participation.responded} of ${data.participation.total_active} staff`}
                       accent={theme.teal} />
            <StatTile theme={theme} icon={MessageCircle} label="Departments reporting"
                       value={String(byDepartment.length)}
                       sub="≥3 responses in the last 12 weeks"
                       accent={theme.orange} />
          </div>

          <div className="rounded-2xl p-5" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
            <div className="flex items-center gap-2 mb-4">
              <TrendingUp className="w-4 h-4" style={{ color: theme.orange }} />
              <h3 className="font-semibold text-sm" style={{ color: theme.text }}>Company mood — last 12 weeks</h3>
            </div>
            <div className="flex items-end gap-1.5 sm:gap-2" style={{ height: 140 }}>
              {weeks.map(w => {
                const pct = w.avg_score != null ? Math.max(6, ((w.avg_score - 1) / 4) * 100) : 0
                return (
                  <div key={w.week_start} className="flex-1 flex flex-col items-center justify-end h-full">
                    <div className="text-[10px] font-semibold mb-1" style={{ color: theme.t2 }}>
                      {w.avg_score != null ? w.avg_score.toFixed(1) : (w.responses > 0 ? '···' : '')}
                    </div>
                    <div
                      className="w-full rounded-t-md transition-all"
                      style={{
                        height: `${pct}%`,
                        minHeight: 4,
                        background: w.avg_score != null ? moodColor(w.avg_score, theme) : theme.cardBdr,
                      }}
                      title={w.avg_score != null
                        ? `${fmtWeek(w.week_start)} — avg ${w.avg_score.toFixed(1)}/5 (${w.responses} responses)`
                        : `${fmtWeek(w.week_start)} — too few responses to show (${w.responses})`}
                    />
                    <div className="text-[9px] mt-1.5 whitespace-nowrap" style={{ color: theme.t3 }}>
                      {fmtWeek(w.week_start)}
                    </div>
                  </div>
                )
              })}
            </div>
          </div>

          <div className="rounded-2xl p-5" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
            <div className="flex items-center gap-2 mb-4">
              <Users className="w-4 h-4" style={{ color: theme.orange }} />
              <h3 className="font-semibold text-sm" style={{ color: theme.text }}>Average mood by department</h3>
            </div>
            {byDepartment.length === 0 ? (
              <p className="text-sm" style={{ color: theme.t2 }}>
                No department has reached 3 responses in the last 12 weeks yet — averages stay hidden
                until then to keep answers anonymous.
              </p>
            ) : (
              <div className="space-y-3">
                {byDepartment.map(d => (
                  <div key={d.department} className="flex items-center gap-3">
                    <div className="w-32 sm:w-40 text-xs font-medium truncate flex-shrink-0" style={{ color: theme.text }}>
                      {d.department}
                    </div>
                    <div className="flex-1 h-2.5 rounded-full overflow-hidden" style={{ background: theme.g100 }}>
                      <div className="h-full rounded-full"
                           style={{ width: `${Math.max(4, ((d.avg_score - 1) / 4) * 100)}%`, background: moodColor(d.avg_score, theme) }} />
                    </div>
                    <div className="w-16 text-right text-xs font-mono flex-shrink-0" style={{ color: theme.t2 }}>
                      {d.avg_score.toFixed(1)}/5
                    </div>
                    <div className="w-20 text-right text-[11px] flex-shrink-0" style={{ color: theme.t3 }}>
                      {d.responses} resp.
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </section>
  )
}

function StatTile({ theme, icon: Icon, label, value, sub, accent }: {
  theme: Theme
  icon: any
  label: string
  value: string
  sub: string
  accent: string
}) {
  return (
    <div className="rounded-2xl p-4" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
      <div className="flex items-center gap-2 mb-2">
        <div className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0" style={{ background: `${accent}22` }}>
          <Icon className="w-4 h-4" style={{ color: accent }} />
        </div>
        <span className="text-[11px] uppercase tracking-wider font-semibold" style={{ color: theme.t2 }}>{label}</span>
      </div>
      <div className="text-2xl font-bold" style={{ color: theme.text }}>{value}</div>
      <div className="text-[11px] mt-0.5" style={{ color: theme.t3 }}>{sub}</div>
    </div>
  )
}
