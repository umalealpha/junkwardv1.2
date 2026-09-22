'use client'

/**
 * /hris/ninebox — 9-Box Talent Grid.
 *
 * Calls GET /hris/api/talent/nine-box/. Renders the canonical 3×3
 * performance × potential matrix + a sortable employee table where
 * each row is colour-coded by its box placement.
 *
 * Closes the talent-management gap flagged by Unami against the
 * AlphaDirect_TMS_Orbit reference design (CFO directive 2026-05-26).
 */
import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { useHrisAccess } from '@/hooks/useHrisAccess'
import { authedHrisFetch } from '../_shared'

interface BoxCell {
  key: string
  box_n: number
  label: string
  color: string
  text: string
  action: string
  count: number
  pct: number
  profile_ids: string[]
}
interface BoxEmployee {
  profile_id: string
  name: string
  position: string
  department: string
  company: string
  grade: string
  initials: string
  talent_segment: string
  perf_score: number
  pot_score: number
  values_score: number
  okr_score: number
  review_period: string | null
  box: { key: string; n: number; l: string; c: string; t: string; a: string }
}
interface NineBoxResponse {
  as_of: string
  total: number
  grid: BoxCell[]
  employees: BoxEmployee[]
}

// Canonical visual order — top-left (Box 1, Star) to bottom-right (Box 9, Talent Risk).
const ORDER: string[] = ['H-H', 'H-M', 'H-L', 'M-H', 'M-M', 'M-L', 'L-H', 'L-M', 'L-L']

export default function NineBoxPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const allowed = useHrisAccess()

  const [data, setData] = useState<NineBoxResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [activeCell, setActiveCell] = useState<string | null>(null)

  useEffect(() => {
    if (allowed === false) router.replace('/dashboard')
  }, [allowed, router])

  useEffect(() => {
    if (allowed !== true) return
    setLoading(true); setError(null)
    authedHrisFetch('/hris/api/talent/nine-box/')
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then((d: NineBoxResponse) => setData(d))
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false))
  }, [allowed])

  const cells = useMemo(() => {
    if (!data) return [] as BoxCell[]
    const byKey = new Map(data.grid.map(g => [g.key, g] as const))
    return ORDER.map(k => byKey.get(k)).filter(Boolean) as BoxCell[]
  }, [data])

  const visibleEmps = useMemo(() => {
    if (!data) return []
    if (!activeCell) return data.employees
    return data.employees.filter(e => e.box.key === activeCell)
  }, [data, activeCell])

  if (allowed !== true) return <Loader theme={theme} />

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar
        title="9-Box Talent Grid"
        breadcrumbs={[{ label: 'HRIS', href: '/hris' }, { label: '9-Box' }]}
      />
      <main className="flex-1 overflow-y-auto p-6 space-y-5">
        {error && (
          <div className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-700">{error}</div>
        )}

        <section className="rounded-xl p-4" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
          <h2 className="font-semibold mb-1" style={{ color: theme.text }}>Performance × Potential</h2>
          <p className="text-xs mb-3" style={{ color: theme.t2 }}>
            {data ? `${data.total} active employees mapped. Click any cell to filter the table below.` : '—'}
          </p>

          {loading ? (
            <div className="h-64 flex items-center justify-center text-sm" style={{ color: theme.t2 }}>Loading…</div>
          ) : (
            <div className="grid grid-cols-3 gap-2">
              {cells.map(cell => {
                const active = activeCell === cell.key
                return (
                  <button
                    key={cell.key}
                    type="button"
                    onClick={() => setActiveCell(active ? null : cell.key)}
                    className="text-left rounded-lg p-3 transition-all"
                    style={{
                      background: cell.color,
                      color: cell.text,
                      outline: active ? `3px solid ${theme.text}` : 'none',
                      opacity: activeCell && !active ? 0.55 : 1,
                    }}
                  >
                    <div className="text-[10px] uppercase tracking-wider opacity-80">Box #{cell.box_n}</div>
                    <div className="font-semibold text-base mt-0.5">{cell.label}</div>
                    <div className="text-2xl font-bold mt-1">{cell.count}</div>
                    <div className="text-[11px] opacity-85">{cell.pct}% of workforce</div>
                    <div className="text-[11px] mt-1.5 opacity-90 leading-snug">{cell.action}</div>
                  </button>
                )
              })}
            </div>
          )}
        </section>

        <section className="rounded-xl p-4" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
          <div className="flex items-center justify-between mb-3">
            <h2 className="font-semibold" style={{ color: theme.text }}>
              Workforce — {visibleEmps.length} employee{visibleEmps.length === 1 ? '' : 's'}
              {activeCell ? ` in "${cells.find(c => c.key === activeCell)?.label}"` : ''}
            </h2>
            {activeCell && (
              <button
                type="button"
                onClick={() => setActiveCell(null)}
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
                  <th className="text-left py-2 px-2 font-medium">Box</th>
                  <th className="text-right py-2 px-2 font-medium">Performance</th>
                  <th className="text-right py-2 px-2 font-medium">Potential</th>
                  <th className="text-right py-2 px-2 font-medium">OKR</th>
                </tr>
              </thead>
              <tbody>
                {visibleEmps.map(e => (
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
                    <td className="py-2 px-2 text-right font-mono" style={{ color: theme.text }}>{e.perf_score.toFixed(2)}</td>
                    <td className="py-2 px-2 text-right font-mono" style={{ color: theme.text }}>{e.pot_score.toFixed(2)}</td>
                    <td className="py-2 px-2 text-right font-mono" style={{ color: theme.text }}>{e.okr_score.toFixed(2)}</td>
                  </tr>
                ))}
                {!visibleEmps.length && !loading && (
                  <tr><td colSpan={6} className="py-6 text-center text-sm" style={{ color: theme.t2 }}>No employees in this cell.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
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
