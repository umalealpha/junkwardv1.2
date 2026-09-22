'use client'

/**
 * /hris/succession — Succession Planning.
 *
 * Calls GET /hris/api/talent/succession/. One row per critical role
 * (incumbent), with the top three internal successor candidates ranked
 * by readiness × performance × potential. Roles surface sorted by risk
 * (critical → high → medium → low) so the gaps that need attention
 * sit at the top of the page.
 *
 * Redesign 2026-08-30 (CFO): a proper hero, click-to-filter risk pills,
 * quick link into the 9-Box Grid, avatar initials on incumbents +
 * successors, ready-now count per role, empty-slot treatment.
 */
import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { useHrisAccess } from '@/hooks/useHrisAccess'
import { authedHrisFetch } from '../_shared'

interface Person {
  profile_id: string
  name: string
  position: string
  department: string
  grade: string
  initials: string
  talent_segment: string
  perf_score: number
  pot_score: number
  box: { l: string; c: string; t: string }
}
interface Successor extends Person {
  readiness: string
  readiness_score: number
  months_to_ready: number
  named?: boolean
}
interface RoleRow {
  role: string
  department: string
  company: string
  risk: 'critical' | 'high' | 'medium' | 'low'
  depth: number
  ready_now: number
  incumbent: Person
  successors: Successor[]
}
interface SuccessionResponse {
  as_of: string
  critical_roles: number
  can_edit?: boolean
  roles: RoleRow[]
}
interface PickOption { profile_id: string; name: string; position: string; department: string }

// Alpha Direct brand palette + risk semantics.
const NAVY   = '#1D3270'
const ORANGE = '#F07F00'
const TEAL   = '#0A9396'

type Risk = RoleRow['risk']
const RISK_META: Record<Risk, { bg: string; fg: string; short: string; full: string }> = {
  critical: { bg: '#C1121F', fg: '#fff',    short: 'CRITICAL',  full: 'No successor'   },
  high:     { bg: '#EE9B00', fg: '#0A2240', short: 'HIGH',      full: 'Thin bench'     },
  medium:   { bg: '#E9D8A6', fg: '#0A2240', short: 'MEDIUM',    full: 'Developing'     },
  low:      { bg: '#94D2BD', fg: '#0A2240', short: 'LOW',       full: 'Covered'        },
}
const RISK_ORDER: Risk[] = ['critical', 'high', 'medium', 'low']

export default function SuccessionPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const allowed = useHrisAccess()

  const [data, setData] = useState<SuccessionResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [editRole, setEditRole] = useState<RoleRow | null>(null)
  const [filter, setFilter] = useState<Risk | null>(null)

  const load = () => {
    setLoading(true); setError(null)
    return authedHrisFetch('/hris/api/talent/succession/')
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then((d: SuccessionResponse) => setData(d))
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    if (allowed === false) router.replace('/dashboard')
  }, [allowed, router])

  useEffect(() => {
    if (allowed !== true) return
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allowed])

  const summary = useMemo(() => {
    if (!data) return null
    const c: Record<Risk, number> = { critical: 0, high: 0, medium: 0, low: 0 }
    data.roles.forEach(r => { c[r.risk]++ })
    return c
  }, [data])

  // Bench depth across all roles = total successors named/auto — a useful
  // headline number next to "critical roles".
  const benchDepth = useMemo(() => {
    if (!data) return 0
    return data.roles.reduce((n, r) => n + r.successors.length, 0)
  }, [data])
  const readyNowTotal = useMemo(() => {
    if (!data) return 0
    return data.roles.reduce((n, r) => n + (r.ready_now || 0), 0)
  }, [data])

  const visibleRoles = useMemo(() => {
    if (!data) return []
    return filter ? data.roles.filter(r => r.risk === filter) : data.roles
  }, [data, filter])

  if (allowed !== true) return <Loader theme={theme} />

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar
        title="Succession Planning"
        breadcrumbs={[{ label: 'HRIS', href: '/hris' }, { label: 'Succession' }]}
        actions={
          <button
            onClick={() => router.push('/hris/ninebox')}
            className="text-[12px] font-semibold px-3 py-1.5 rounded-lg"
            style={{ background: NAVY, color: '#fff' }}
          >Open 9-Box Grid →</button>
        }
      />
      <main className="flex-1 overflow-y-auto p-6 space-y-5">
        {error && (
          <div className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-700">{error}</div>
        )}

        {/* Hero — three big numbers + the "as of" date, cleanly typeset. */}
        {data && (
          <section
            className="rounded-2xl p-5 md:p-6"
            style={{ background: NAVY, color: '#fff' }}
          >
            <div className="flex items-start justify-between flex-wrap gap-4">
              <div>
                <div className="text-[11px] uppercase tracking-[0.2em] opacity-70">Bench view</div>
                <h2 className="text-2xl md:text-3xl font-semibold mt-1">
                  {data.roles.length} critical role{data.roles.length === 1 ? '' : 's'} watched
                </h2>
                <p className="text-[13px] opacity-80 mt-1">
                  {data.as_of ? `As at ${new Date(data.as_of).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })}` : ''}
                  {' · '}Ranked by risk. Named picks lead the ranking; auto fills the rest.
                </p>
              </div>
              <div className="flex gap-6">
                <Stat label="Bench depth" value={benchDepth} accent="#fff" />
                <Stat label="Ready now"   value={readyNowTotal} accent={ORANGE} />
                <Stat label="Critical"    value={summary?.critical ?? 0} accent="#F7B2B2" />
              </div>
            </div>
          </section>
        )}

        {/* Risk filter — click-to-filter pills. Order: critical → low. */}
        {summary && (
          <section className="flex items-center gap-2 flex-wrap">
            <span className="text-[11px] uppercase tracking-wider" style={{ color: theme.t2 }}>Filter:</span>
            <FilterPill
              active={filter === null}
              onClick={() => setFilter(null)}
              label={`All (${data?.roles.length ?? 0})`}
              bg={theme.card}
              activeBg={NAVY}
              activeFg="#fff"
              fg={theme.text}
              border={theme.cardBdr}
            />
            {RISK_ORDER.map(r => (
              <FilterPill
                key={r}
                active={filter === r}
                onClick={() => setFilter(filter === r ? null : r)}
                label={`${RISK_META[r].short} (${summary[r]})`}
                bg={theme.card}
                activeBg={RISK_META[r].bg}
                activeFg={RISK_META[r].fg}
                fg={theme.text}
                border={theme.cardBdr}
              />
            ))}
            {filter && (
              <span className="text-[12px]" style={{ color: theme.t2 }}>
                {RISK_META[filter].full}
              </span>
            )}
          </section>
        )}

        <section className="space-y-3">
          {loading && (
            <div className="rounded-xl p-6 text-center text-sm" style={{ background: theme.card, color: theme.t2 }}>Loading critical roles…</div>
          )}
          {!loading && data && !data.roles.length && (
            <div className="rounded-xl p-6 text-center text-sm" style={{ background: theme.card, color: theme.t2 }}>
              No critical roles identified yet. Tag job titles with manager / director / head-of keywords to populate this view.
            </div>
          )}
          {!loading && data && data.roles.length > 0 && visibleRoles.length === 0 && filter && (
            <div className="rounded-xl p-6 text-center text-sm" style={{ background: theme.card, color: theme.t2 }}>
              No {RISK_META[filter].short.toLowerCase()} roles.{' '}
              <button onClick={() => setFilter(null)} className="underline" style={{ color: ORANGE }}>Show all</button>
            </div>
          )}

          {visibleRoles.map(r => (
            <RoleCard
              key={r.role + r.incumbent.profile_id}
              row={r}
              canEdit={!!data?.can_edit}
              onEdit={() => setEditRole(r)}
              theme={theme}
            />
          ))}
        </section>
      </main>

      {editRole && (
        <NomineeEditor
          role={editRole}
          onClose={() => setEditRole(null)}
          onSaved={() => { setEditRole(null); load() }}
        />
      )}
    </div>
  )
}

// ── little presentation pieces ─────────────────────────────────────────────

function Stat({ label, value, accent }: { label: string; value: number; accent: string }) {
  return (
    <div className="text-right">
      <div className="text-[10px] uppercase tracking-[0.15em] opacity-70">{label}</div>
      <div className="text-3xl font-semibold font-mono-nums leading-tight" style={{ color: accent }}>{value}</div>
    </div>
  )
}

function FilterPill({ active, onClick, label, bg, activeBg, fg, activeFg, border }:
  { active: boolean; onClick: () => void; label: string; bg: string; activeBg: string; fg: string; activeFg: string; border: string }) {
  return (
    <button
      onClick={onClick}
      className="text-[11px] font-semibold px-2.5 py-1 rounded-full transition-colors"
      style={{
        background: active ? activeBg : bg,
        color: active ? activeFg : fg,
        border: `1px solid ${active ? activeBg : border}`,
      }}
    >{label}</button>
  )
}

function Avatar({ initials, size = 36, bg = '#1D3270' }: { initials: string; size?: number; bg?: string }) {
  return (
    <div
      className="rounded-full flex items-center justify-center font-semibold shrink-0"
      style={{ width: size, height: size, background: bg, color: '#fff', fontSize: size * 0.4 }}
    >{initials || '—'}</div>
  )
}

function RoleCard({ row: r, canEdit, onEdit, theme }:
  { row: RoleRow; canEdit: boolean; onEdit: () => void; theme: { card: string; cardBdr: string; text: string; t2: string; t3: string; bg: string } }) {
  const risk = RISK_META[r.risk]
  return (
    <div
      className="rounded-xl p-4"
      style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}
    >
      <div className="flex items-start justify-between gap-3 mb-3">
        <div className="flex items-start gap-3 min-w-0">
          <Avatar initials={r.incumbent.initials} bg={risk.bg} />
          <div className="min-w-0">
            <div className="text-[11px] uppercase tracking-wider" style={{ color: theme.t2 }}>{r.department || '—'}</div>
            <h3 className="text-lg font-semibold truncate" style={{ color: theme.text }}>{r.role}</h3>
            <div className="text-sm mt-0.5" style={{ color: theme.t2 }}>
              Incumbent: <span className="font-medium" style={{ color: theme.text }}>{r.incumbent.name}</span>
              {' '}— {r.incumbent.grade || '—'}
            </div>
          </div>
        </div>
        <div className="flex flex-col items-end gap-1.5 shrink-0">
          <span
            className="text-[11px] font-semibold px-2 py-1 rounded uppercase"
            style={{ background: risk.bg, color: risk.fg }}
          >{risk.short}</span>
          <span className="text-[11px]" style={{ color: theme.t2 }}>
            {r.ready_now} ready now · {r.successors.length} on bench
          </span>
          {canEdit && (
            <button
              onClick={onEdit}
              className="text-[11px] font-semibold px-2 py-1 rounded border"
              style={{ borderColor: theme.cardBdr, color: theme.text }}
            >Name successors</button>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
        {[0, 1, 2].map(slot => {
          const s = r.successors[slot]
          if (!s) return (
            <div
              key={slot}
              className="rounded-lg p-3 text-center text-xs border-2 border-dashed"
              style={{ borderColor: theme.cardBdr, color: theme.t3 }}
            >
              Slot #{slot + 1} — open
              {canEdit && (
                <div className="mt-1">
                  <button onClick={onEdit} className="text-[11px] underline" style={{ color: ORANGE }}>Name one</button>
                </div>
              )}
            </div>
          )
          const barColor = s.readiness_score >= 75 ? TEAL : s.readiness_score >= 50 ? '#EE9B00' : '#C1121F'
          return (
            <div
              key={s.profile_id}
              className="rounded-lg p-3"
              style={{ background: theme.bg, border: `1px solid ${theme.cardBdr}` }}
            >
              <div className="flex items-center justify-between">
                <div className="text-[10px] uppercase tracking-wider" style={{ color: theme.t3 }}>Slot #{slot + 1}</div>
                {s.named && (
                  <span className="text-[9px] font-semibold px-1.5 py-0.5 rounded" style={{ background: TEAL, color: '#fff' }}>NAMED</span>
                )}
              </div>
              <div className="flex items-center gap-2 mt-1">
                <Avatar initials={s.initials} size={28} bg={s.box.c} />
                <div className="min-w-0">
                  <div className="font-semibold truncate" style={{ color: theme.text }}>{s.name}</div>
                  <div className="text-[11px] truncate" style={{ color: theme.t2 }}>{s.position} — {s.grade}</div>
                </div>
              </div>
              <div className="mt-2 flex items-center gap-2 flex-wrap">
                <span
                  className="text-[11px] px-2 py-0.5 rounded font-medium"
                  style={{ background: s.box.c + '22', color: s.box.c }}
                >{s.box.l}</span>
                <span className="text-[11px]" style={{ color: theme.t2 }}>{s.readiness}</span>
              </div>
              <div className="mt-2 h-1.5 rounded-full overflow-hidden" style={{ background: theme.cardBdr }}>
                <div
                  className="h-full"
                  style={{ width: `${Math.min(100, Math.max(0, s.readiness_score))}%`, background: barColor }}
                />
              </div>
              <div className="text-[11px] mt-1 text-right font-mono" style={{ color: theme.t2 }}>{s.readiness_score.toFixed(0)} / 100</div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function NomineeEditor({ role, onClose, onSaved }: { role: RoleRow; onClose: () => void; onSaved: () => void }) {
  const { theme } = useTheme()
  const [options, setOptions] = useState<PickOption[]>([])
  const [picks, setPicks] = useState<PickOption[]>([])
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const [loadErr, setLoadErr] = useState<string | null>(null)

  useEffect(() => {
    authedHrisFetch(`/hris/api/talent/succession/${role.incumbent.profile_id}/nominees/`)
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then((d: { named: PickOption[]; options: PickOption[] }) => {
        setOptions(d.options || [])
        setPicks((d.named || []).slice(0, 3))
      })
      .catch(e => setLoadErr(e instanceof Error ? e.message : 'Failed to load'))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const pickedIds = new Set(picks.map(p => p.profile_id))
  const filtered = options
    .filter(o => !pickedIds.has(o.profile_id))
    .filter(o => !q || `${o.name} ${o.position} ${o.department}`.toLowerCase().includes(q.toLowerCase()))
    .slice(0, 40)

  const save = () => {
    setBusy(true)
    authedHrisFetch(`/hris/api/talent/succession/${role.incumbent.profile_id}/nominees/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ nominee_ids: picks.map(p => p.profile_id) }),
    })
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then(() => onSaved())
      .catch(() => setBusy(false))
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.45)' }} onClick={onClose}>
      <div className="w-full max-w-lg rounded-xl p-5 max-h-[85vh] overflow-y-auto" style={{ background: theme.card }} onClick={e => e.stopPropagation()}>
        <div className="flex items-start justify-between mb-1">
          <h3 className="text-base font-semibold" style={{ color: theme.text }}>Name successors — {role.role}</h3>
          <button onClick={onClose} className="text-sm" style={{ color: theme.t2 }}>✕</button>
        </div>
        <p className="text-[12px] mb-3" style={{ color: theme.t2 }}>
          Incumbent: {role.incumbent.name}. Pick up to 3, in order. Named picks lead; the system fills any empty slots.
        </p>
        {loadErr && <div className="text-sm text-red-600 mb-2">{loadErr}</div>}

        <div className="mb-3 space-y-1.5">
          {picks.length === 0 && <div className="text-[12px]" style={{ color: theme.t3 }}>No one named yet — the system picks automatically.</div>}
          {picks.map((p, i) => (
            <div key={p.profile_id} className="flex items-center gap-2 rounded-lg px-2.5 py-1.5" style={{ background: theme.bg, border: `1px solid ${theme.cardBdr}` }}>
              <span className="text-[11px] font-bold w-5" style={{ color: theme.t3 }}>#{i + 1}</span>
              <div className="flex-1">
                <div className="text-sm font-medium" style={{ color: theme.text }}>{p.name}</div>
                <div className="text-[11px]" style={{ color: theme.t2 }}>{p.position || '—'}{p.department ? ` · ${p.department}` : ''}</div>
              </div>
              {i > 0 && <button onClick={() => setPicks(x => { const y = [...x]; [y[i - 1], y[i]] = [y[i], y[i - 1]]; return y })} className="text-xs px-1" style={{ color: theme.t2 }}>↑</button>}
              <button onClick={() => setPicks(x => x.filter(y => y.profile_id !== p.profile_id))} className="text-xs px-1" style={{ color: '#C1121F' }}>remove</button>
            </div>
          ))}
        </div>

        {picks.length < 3 && (
          <>
            <input
              value={q}
              onChange={e => setQ(e.target.value)}
              placeholder="Search people to add…"
              className="w-full rounded-lg px-3 py-2 text-sm mb-2"
              style={{ background: theme.bg, border: `1px solid ${theme.cardBdr}`, color: theme.text }}
            />
            <div className="space-y-1 max-h-56 overflow-y-auto">
              {filtered.map(o => (
                <button
                  key={o.profile_id}
                  onClick={() => setPicks(x => x.length < 3 ? [...x, o] : x)}
                  className="w-full text-left rounded-lg px-2.5 py-1.5 hover:opacity-80"
                  style={{ background: theme.bg, border: `1px solid ${theme.cardBdr}` }}
                >
                  <div className="text-sm" style={{ color: theme.text }}>{o.name}</div>
                  <div className="text-[11px]" style={{ color: theme.t2 }}>{o.position || '—'}{o.department ? ` · ${o.department}` : ''}</div>
                </button>
              ))}
            </div>
          </>
        )}

        <div className="flex justify-end gap-2 mt-4">
          <button onClick={onClose} className="text-sm px-3 py-1.5 rounded-lg border" style={{ borderColor: theme.cardBdr, color: theme.text }}>Cancel</button>
          <button onClick={save} disabled={busy} className="text-sm px-3 py-1.5 rounded-lg font-semibold" style={{ background: ORANGE, color: '#fff', opacity: busy ? 0.6 : 1 }}>
            {busy ? 'Saving…' : 'Save'}
          </button>
        </div>
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
