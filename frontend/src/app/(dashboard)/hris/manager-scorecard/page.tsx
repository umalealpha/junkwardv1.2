'use client'

/**
 * /hris/manager-scorecard — Manager Scorecard.
 *
 * Per line-manager accountability: are they running monthly check-ins and
 * Development Dialogues with their team, and is the team shrinking? Calls
 * GET /api/v1/hris/manager-scorecard/ (HR / CFO / exec whitelist only —
 * same gate as the rest of native HRIS). Sorted worst-score-first by the
 * backend; sortable client-side by any column.
 */
import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { ChevronUp, ChevronDown, Gauge, AlertTriangle, Users } from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import FeatureAcceptBar from '@/components/hris/FeatureAcceptBar'
import { useTheme } from '@/contexts/ThemeContext'
import type { Theme } from '@/lib/themes'
import { useHrisAccess } from '@/hooks/useHrisAccess'
import { authedHrisFetch } from '../_shared'

interface ManagerScoreRow {
  manager_id: string
  manager_name: string
  department: string
  reports: number
  checkin_pct: number
  dialogue_pct: number
  attrition: number | null
  score: number
}
interface ManagerScorecardResponse {
  generated_at: string
  month_label: string
  managers: ManagerScoreRow[]
}

type SortKey = 'manager_name' | 'department' | 'reports' | 'checkin_pct' | 'dialogue_pct' | 'attrition' | 'score'
// Sensible per-column default when a header is clicked for the first time —
// biggest/worst first for numbers, A→Z for text. Score defaults to the
// backend's own worst-first ordering.
const DEFAULT_DIR: Record<SortKey, 'asc' | 'desc'> = {
  manager_name: 'asc', department: 'asc', reports: 'desc',
  checkin_pct: 'desc', dialogue_pct: 'desc', attrition: 'desc', score: 'asc',
}

function initials(name: string): string {
  return name.split(' ').map(p => p[0]).filter(Boolean).slice(0, 2).join('').toUpperCase()
}

function scoreTier(score: number, theme: Theme): { bg: string; fg: string; label: string } {
  if (score >= 75) return { bg: theme.okB, fg: theme.ok, label: 'On track' }
  if (score >= 50) return { bg: theme.wrB, fg: theme.wr, label: 'Watch' }
  return { bg: theme.erB, fg: theme.er, label: 'At risk' }
}

// attrition is nullable on the row type; every other sortable column is not
// — special-case it so the shared comparator never has to juggle `null`.
function sortValue(r: ManagerScoreRow, key: SortKey): string | number {
  if (key === 'attrition') return r.attrition ?? 0
  return r[key]
}

export default function ManagerScorecardPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const allowed = useHrisAccess()

  const [data, setData] = useState<ManagerScorecardResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [sortKey, setSortKey] = useState<SortKey>('score')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc')

  useEffect(() => {
    if (allowed === false) router.replace('/dashboard')
  }, [allowed, router])

  useEffect(() => {
    if (allowed !== true) return
    setLoading(true); setError(null)
    authedHrisFetch('/api/v1/hris/manager-scorecard/')
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then((d: ManagerScorecardResponse) => setData(d))
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false))
  }, [allowed])

  function toggleSort(key: SortKey) {
    if (key === sortKey) setSortDir(d => (d === 'asc' ? 'desc' : 'asc'))
    else { setSortKey(key); setSortDir(DEFAULT_DIR[key]) }
  }

  const rows = data?.managers ?? []

  const sorted = useMemo(() => {
    // attrition is all-null or all-numeric together (it's a single dataset-
    // wide derivability check on the backend), so sortValue's `?? 0` never
    // mixes real zeros with unknowns in a misleading way.
    return [...rows].sort((a, b) => {
      const av = sortValue(a, sortKey), bv = sortValue(b, sortKey)
      const cmp = typeof av === 'string' && typeof bv === 'string'
        ? av.localeCompare(bv)
        : (av as number) - (bv as number)
      return sortDir === 'asc' ? cmp : -cmp
    })
  }, [rows, sortKey, sortDir])

  const summary = useMemo(() => {
    if (!rows.length) return null
    const avg = (f: (r: ManagerScoreRow) => number) =>
      rows.reduce((s, r) => s + f(r), 0) / rows.length
    return {
      managers: rows.length,
      avgCheckin: avg(r => r.checkin_pct),
      avgDialogue: avg(r => r.dialogue_pct),
      atRisk: rows.filter(r => r.score < 50).length,
    }
  }, [rows])

  if (allowed !== true) return <Loader theme={theme} />

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar
        title="Manager Scorecard"
        breadcrumbs={[{ label: 'HRIS', href: '/hris' }, { label: 'Manager Scorecard' }]}
      />
      <main className="flex-1 overflow-y-auto p-6 space-y-5">
        <FeatureAcceptBar featureKey="manager_scorecard" />
        {error && (
          <div className="rounded-md p-3 text-sm" style={{ background: theme.erB, border: `1px solid ${theme.er}`, color: theme.er }}>
            {error}
          </div>
        )}

        <div className="rounded-xl px-4 py-3 flex items-start gap-2 text-sm" style={{ background: theme.inB, border: `1px solid ${theme.inf}55`, color: theme.text }}>
          <Gauge className="w-4 h-4 shrink-0 mt-0.5" style={{ color: theme.inf }} />
          <span>
            Holds line managers accountable for their own team, not just their staff: monthly check-ins and Development
            Dialogues completed, and reports lost in the last 12 months. Check-ins measured for{' '}
            <b>{data?.month_label || 'this month'}</b>. Sorted worst-first — the managers who need help show up top.
          </span>
        </div>

        {summary && (
          <section className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <Tile label="Managers tracked" value={summary.managers} theme={theme} icon={<Users className="w-3.5 h-3.5" />} />
            <Tile label="Avg check-in %" value={`${summary.avgCheckin.toFixed(0)}%`} theme={theme} accent={theme.orange} />
            <Tile label="Avg dialogue %" value={`${summary.avgDialogue.toFixed(0)}%`} theme={theme} accent={theme.teal} />
            <Tile label="Needs attention" value={summary.atRisk} theme={theme} accent={theme.er}
                  icon={summary.atRisk > 0 ? <AlertTriangle className="w-3.5 h-3.5" /> : undefined} />
          </section>
        )}

        <section className="rounded-2xl p-4" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-[11px] uppercase tracking-wider" style={{ color: theme.t2 }}>
                  <Th label="Manager" k="manager_name" sortKey={sortKey} sortDir={sortDir} onSort={toggleSort} />
                  <Th label="Department" k="department" sortKey={sortKey} sortDir={sortDir} onSort={toggleSort} />
                  <Th label="Reports" k="reports" align="right" sortKey={sortKey} sortDir={sortDir} onSort={toggleSort} />
                  <Th label="Check-in %" k="checkin_pct" sortKey={sortKey} sortDir={sortDir} onSort={toggleSort} />
                  <Th label="Dialogue %" k="dialogue_pct" sortKey={sortKey} sortDir={sortDir} onSort={toggleSort} />
                  <Th label="Attrition (12mo)" k="attrition" align="right" sortKey={sortKey} sortDir={sortDir} onSort={toggleSort} />
                  <Th label="Score" k="score" align="right" sortKey={sortKey} sortDir={sortDir} onSort={toggleSort} />
                </tr>
              </thead>
              <tbody>
                {loading && [0, 1, 2, 3, 4].map(i => (
                  <tr key={i} className="border-t" style={{ borderColor: theme.cardBdr }}>
                    {[0, 1, 2, 3, 4, 5, 6].map(j => (
                      <td key={j} className="py-3 pr-4">
                        <div className="h-3 w-20 rounded animate-pulse" style={{ background: theme.g100 }} />
                      </td>
                    ))}
                  </tr>
                ))}

                {!loading && sorted.length === 0 && (
                  <tr>
                    <td colSpan={7} className="py-12 text-center">
                      <Users className="w-8 h-8 mx-auto mb-2" style={{ color: theme.t3 }} />
                      <div className="text-sm font-medium" style={{ color: theme.text }}>No managers with direct reports yet.</div>
                      <div className="text-[12px] mt-1" style={{ color: theme.t2 }}>
                        Once HR assigns a manager on someone's HRIS profile, they will appear here.
                      </div>
                    </td>
                  </tr>
                )}

                {!loading && sorted.map(r => {
                  const tier = scoreTier(r.score, theme)
                  return (
                    <tr key={r.manager_id} className="border-t hover:bg-black/[0.02] transition-colors" style={{ borderColor: theme.cardBdr }}>
                      <td className="py-3 pr-4">
                        <div className="flex items-center gap-2.5">
                          <div className="w-7 h-7 rounded-full flex items-center justify-center text-[10px] font-bold flex-shrink-0"
                               style={{ background: theme.oL, color: theme.orange }}>
                            {initials(r.manager_name) || '—'}
                          </div>
                          <Link href={`/hris/monthly-feedback?manager=${r.manager_id}`}
                                className="font-medium hover:underline" style={{ color: theme.text }}
                                title="Open this manager's monthly feedback">
                            {r.manager_name}
                          </Link>
                        </div>
                      </td>
                      <td className="py-3 pr-4" style={{ color: theme.t2 }}>{r.department || '—'}</td>
                      <td className="py-3 pr-4 text-right tabular-nums" style={{ color: theme.text }}>{r.reports}</td>
                      <td className="py-3 pr-4"><PctBar pct={r.checkin_pct} theme={theme} color={theme.orange} /></td>
                      <td className="py-3 pr-4"><PctBar pct={r.dialogue_pct} theme={theme} color={theme.teal} /></td>
                      <td className="py-3 pr-4 text-right tabular-nums">
                        {r.attrition === null ? (
                          <span style={{ color: theme.t3 }} title="Not derivable — no termination dates on file yet">—</span>
                        ) : r.attrition > 0 ? (
                          <span className="inline-flex items-center gap-1 font-semibold" style={{ color: theme.er }}>
                            <AlertTriangle className="w-3 h-3" /> {r.attrition}
                          </span>
                        ) : (
                          <span style={{ color: theme.t2 }}>0</span>
                        )}
                      </td>
                      <td className="py-3 pr-4 text-right">
                        <span className="inline-flex items-center justify-end gap-1 min-w-[84px] text-[11px] px-2 py-0.5 rounded-full font-semibold"
                              style={{ background: tier.bg, color: tier.fg }}>
                          {r.score.toFixed(0)} · {tier.label}
                        </span>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </section>
      </main>
    </div>
  )
}

/* ── small building blocks ─────────────────────────────────────────────── */

function Th({ label, k, align, sortKey, sortDir, onSort }: {
  label: string; k: SortKey; align?: 'right'
  sortKey: SortKey; sortDir: 'asc' | 'desc'; onSort: (k: SortKey) => void
}) {
  const active = sortKey === k
  return (
    <th
      className={`py-2.5 pr-4 font-semibold cursor-pointer select-none ${align === 'right' ? 'text-right' : ''}`}
      onClick={() => onSort(k)}
    >
      <span className="inline-flex items-center gap-1">
        {label}
        {active
          ? (sortDir === 'asc' ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />)
          : <ChevronDown className="w-3 h-3 opacity-30" />}
      </span>
    </th>
  )
}

function PctBar({ pct, theme, color }: { pct: number; theme: Theme; color: string }) {
  const clamped = Math.min(100, Math.max(0, pct))
  return (
    <div className="flex items-center gap-2 min-w-[120px]">
      <div className="flex-1 h-1.5 rounded-full overflow-hidden" style={{ background: theme.cardBdr }}>
        <div className="h-full rounded-full transition-all duration-500" style={{ width: `${clamped}%`, background: color }} />
      </div>
      <span className="text-[12px] tabular-nums w-10 text-right" style={{ color: theme.text }}>{pct.toFixed(0)}%</span>
    </div>
  )
}

function Tile({ label, value, theme, accent, icon }: {
  label: string; value: string | number; theme: Theme; accent?: string; icon?: React.ReactNode
}) {
  return (
    <div className="rounded-2xl p-4" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
      <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wider font-semibold" style={{ color: theme.t2 }}>
        {icon}{label}
      </div>
      <div className="text-2xl font-bold mt-1" style={{ color: accent || theme.text }}>{value}</div>
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
