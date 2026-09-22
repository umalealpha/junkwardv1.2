'use client'

/**
 * /adoption — Omni Adoption Scoreboard (CFO 2026-07-21).
 * Who is actually USING Omni vs still hiding in Excel. Turns "the system doesn't
 * work" into facts: login recency + real activity (audit trail) per person.
 * Management-only (C-suite / HR / superuser) — server-gated.
 */
import { useCallback, useEffect, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import {
  adoptionScoreboard, adoptionScreens,
  type AdoptionReport, type ScreenUsageReport, type ScreenSurface,
} from '@/lib/api'
import { Loader2, TrendingUp, CircleSlash, Clock, CheckCircle2 } from 'lucide-react'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'
// Surface filter for the screen-usage table (CFO 2026-09-03). '' = all.
const SURFACES: { key: ScreenSurface | ''; label: string }[] = [
  { key: '', label: 'All' },
  { key: 'desktop', label: 'Desktop' },
  { key: 'app', label: 'Phone app' },
  { key: 'm', label: 'Nexus' },
]
const SURFACE_LABEL: Record<ScreenSurface, string> = { desktop: 'Desktop', app: 'Phone app', m: 'Nexus' }
const STATUS: Record<string, { label: string; bg: string; fg: string }> = {
  active: { label: 'Active', bg: '#ECFDF5', fg: '#15803D' },
  dormant: { label: 'Dormant', bg: '#FFF7ED', fg: '#B45309' },
  never: { label: 'Never used', bg: '#FEF2F2', fg: '#B42318' },
}

function ago(iso: string | null): string {
  if (!iso) return 'never'
  const d = new Date(iso), now = new Date()
  const days = Math.floor((now.getTime() - d.getTime()) / 86400000)
  if (days <= 0) return 'today'
  if (days === 1) return 'yesterday'
  if (days < 30) return `${days}d ago`
  if (days < 365) return `${Math.floor(days / 30)}mo ago`
  return `${Math.floor(days / 365)}y ago`
}

export default function AdoptionPage() {
  const { theme } = useTheme()
  const [data, setData] = useState<AdoptionReport | null>(null)
  const [days, setDays] = useState(30)
  const [loading, setLoading] = useState(true)
  const [denied, setDenied] = useState(false)
  const [screens, setScreens] = useState<ScreenUsageReport | null>(null)
  const [surface, setSurface] = useState<ScreenSurface | ''>('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setData(await adoptionScoreboard(days))
      setDenied(false)
    } catch (e) {
      if (/\b403\b/.test(e instanceof Error ? e.message : '')) setDenied(true)
    } finally { setLoading(false) }
  }, [days])
  useEffect(() => { load() }, [load])

  // Screen usage rides the same window; a failure here must not hide the scoreboard.
  useEffect(() => {
    let cancelled = false
    adoptionScreens(days)
      .then(r => { if (!cancelled) setScreens(r) })
      .catch(() => { if (!cancelled) setScreens(null) })
    return () => { cancelled = true }
  }, [days])
  const screenRows = (screens?.rows ?? []).filter(r => !surface || r.surface === surface)

  const card = { background: theme.card, border: `1px solid ${theme.cardBdr}` }
  const s = data?.summary

  const tile = (label: string, value: string | number, color: string, Icon: typeof TrendingUp) => (
    <div className="rounded-2xl p-4 flex items-center gap-3" style={card}>
      <div className="w-9 h-9 rounded-xl flex items-center justify-center" style={{ background: `${color}18` }}>
        <Icon className="w-5 h-5" style={{ color }} />
      </div>
      <div>
        <div className="text-[10px] uppercase tracking-wide" style={{ color: theme.t2 }}>{label}</div>
        <div className="text-xl font-extrabold" style={{ color }}>{value}</div>
      </div>
    </div>
  )

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar title="Adoption Scoreboard" breadcrumbs={[{ label: 'Adoption' }]} />
      <main className="flex-1 overflow-y-auto p-5 space-y-5">
        <div className="rounded-xl px-4 py-3 text-sm" style={{ background: '#FFFBEB', border: '1px solid #FDE08A', color: '#7A5B00' }}>
          Who is actually using Omni — from real logins + activity in the system. Facts to replace “the system doesn’t work”.
        </div>

        {denied ? (
          <p className="text-sm" style={{ color: theme.t2 }}>This scoreboard is management-only.</p>
        ) : loading && !data ? (
          <div className="flex items-center gap-2 text-sm" style={{ color: theme.t2 }}>
            <Loader2 className="w-4 h-4 animate-spin" /> Loading…
          </div>
        ) : s ? (
          <>
            {/* headline tiles */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <div className="rounded-2xl p-4" style={{ background: NAVY }}>
                <div className="text-[10px] uppercase tracking-wide" style={{ color: '#C7D2E0' }}>Adoption</div>
                <div className="text-3xl font-extrabold" style={{ color: '#fff' }}>{s.adoption_pct}%</div>
                <div className="text-[11px]" style={{ color: '#9FB2C8' }}>using Omni in {data!.window_days}d</div>
              </div>
              {tile('Active', s.active, '#15803D', CheckCircle2)}
              {tile('Dormant', s.dormant, '#B45309', Clock)}
              {tile('Never used', s.never, '#B42318', CircleSlash)}
            </div>

            {/* controls */}
            <div className="flex items-center gap-2">
              <span className="text-[11px] uppercase tracking-wide font-semibold" style={{ color: ORANGE }}>Window</span>
              {[14, 30, 60, 90].map(d => (
                <button key={d} onClick={() => setDays(d)}
                  className="px-3 py-1 rounded-lg text-sm font-semibold transition-colors"
                  style={{ background: days === d ? NAVY : theme.g100, color: days === d ? '#fff' : theme.text }}>
                  {d}d
                </button>
              ))}
            </div>

            {/* scoreboard — worst first */}
            <section className="rounded-2xl overflow-hidden" style={card}>
              <div className="px-4 py-3 text-sm font-bold" style={{ color: theme.text, borderBottom: `1px solid ${theme.cardBdr}` }}>
                Everyone ({s.total}) — least active first
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm" style={{ borderCollapse: 'collapse' }}>
                  <thead>
                    <tr style={{ color: theme.t2 }}>
                      {['Name', 'Company', 'Role', 'Last login', 'Last activity', 'Actions', 'Status'].map(h => (
                        <th key={h} className="text-left font-medium px-4 py-2 text-[11px] uppercase tracking-wide">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {data!.rows.map((r, i) => {
                      const st = STATUS[r.status]
                      return (
                        <tr key={r.email || r.name || i} style={{ borderTop: `1px solid ${theme.cardBdr}` }}>
                          <td className="px-4 py-2 font-semibold" style={{ color: theme.text }}>{r.name}</td>
                          <td className="px-4 py-2 text-[12px]" style={{ color: theme.t2 }}>{r.company}</td>
                          <td className="px-4 py-2 text-[12px]" style={{ color: theme.t2 }}>{r.title || '—'}</td>
                          <td className="px-4 py-2 text-[12px]" style={{ color: theme.t2 }}>{ago(r.last_login)}</td>
                          <td className="px-4 py-2 text-[12px]" style={{ color: theme.t2 }}>{ago(r.last_action)}</td>
                          <td className="px-4 py-2 tabular-nums" style={{ color: theme.text }}>{r.actions_window}</td>
                          <td className="px-4 py-2">
                            <span className="text-[11px] px-2 py-0.5 rounded-full font-semibold" style={{ background: st.bg, color: st.fg }}>
                              {st.label}
                            </span>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </section>

            {/* Top screens — which screens staff actually open (CFO 2026-09-03).
                Path families only (ids collapsed to :id); no content. */}
            <section className="rounded-2xl overflow-hidden" style={card}>
              <div className="px-4 py-3 flex flex-wrap items-center gap-3" style={{ borderBottom: `1px solid ${theme.cardBdr}` }}>
                <div className="text-sm font-bold" style={{ color: theme.text }}>
                  Top screens (last {screens?.days ?? days} days)
                </div>
                <div className="flex items-center gap-1 ml-auto">
                  {SURFACES.map(sf => (
                    <button key={sf.key} onClick={() => setSurface(sf.key)}
                      className="px-2.5 py-1 rounded-lg text-[12px] font-semibold transition-colors"
                      style={{ background: surface === sf.key ? NAVY : theme.g100, color: surface === sf.key ? '#fff' : theme.text }}>
                      {sf.label}
                    </button>
                  ))}
                </div>
              </div>
              {screenRows.length === 0 ? (
                <p className="px-4 py-3 text-sm" style={{ color: theme.t2 }}>No screen views recorded yet.</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm" style={{ borderCollapse: 'collapse' }}>
                    <thead>
                      <tr style={{ color: theme.t2 }}>
                        {['Screen', 'Surface', 'People', 'Opens'].map(h => (
                          <th key={h} className="text-left font-medium px-4 py-2 text-[11px] uppercase tracking-wide">{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {screenRows.map(r => (
                        <tr key={`${r.surface}:${r.screen}`} style={{ borderTop: `1px solid ${theme.cardBdr}` }}>
                          <td className="px-4 py-2 font-mono text-[12px]" style={{ color: theme.text }}>{r.screen}</td>
                          <td className="px-4 py-2 text-[12px]" style={{ color: theme.t2 }}>{SURFACE_LABEL[r.surface] ?? r.surface}</td>
                          <td className="px-4 py-2 tabular-nums" style={{ color: theme.text }}>{r.users}</td>
                          <td className="px-4 py-2 tabular-nums" style={{ color: theme.t2 }}>{r.hits}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          </>
        ) : null}
      </main>
    </div>
  )
}
