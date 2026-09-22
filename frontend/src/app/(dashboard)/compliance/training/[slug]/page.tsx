'use client'

/**
 * Compliance dashboard — who's done, who hasn't.
 *
 * Reads /api/v1/training/modules/<slug>/roster/. Compliance/HRIS/staff only —
 * the API returns 403 otherwise.
 */
import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import {
  ArrowLeft, AlertCircle, CheckCircle2, XCircle, RotateCcw, Award, Search, Download,
} from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { apiFetch } from '@/lib/api'

interface Row {
  user_id: number; username: string; email: string; name: string
  status: 'passed' | 'attempted' | 'not_started'
  best_score: number | null
  attempts: number
  certificate_number: string | null
  completed_at: string | null
}
interface Resp {
  module: { slug: string; title: string; open_from: string; open_until: string;
            pass_mark_pct: number; is_published: boolean }
  total: number; done: number; not_started: number; attempted: number
  rows: Row[]
}

export default function ComplianceTrainingPage() {
  const { theme } = useTheme()
  const params = useParams<{ slug: string }>()
  const slug = params?.slug
  const [data, setData]     = useState<Resp | null>(null)
  const [error, setError]   = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [q, setQ]           = useState('')
  const [status, setStatus] = useState<'all' | 'passed' | 'attempted' | 'not_started'>('all')

  useEffect(() => {
    if (!slug) return
    setLoading(true)
    apiFetch<Resp>(`/training/modules/${slug}/roster/`)
      .then(setData)
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false))
  }, [slug])

  const rows = useMemo(() => {
    if (!data) return []
    const t = q.trim().toLowerCase()
    return data.rows.filter(r => {
      if (status !== 'all' && r.status !== status) return false
      if (!t) return true
      return r.name.toLowerCase().includes(t)
          || r.username.toLowerCase().includes(t)
          || r.email.toLowerCase().includes(t)
    })
  }, [data, q, status])

  function exportCsv() {
    if (!data) return
    const head = ['Name', 'Username', 'Email', 'Status', 'Best score',
                  'Attempts', 'Certificate no', 'Completed at']
    const body = data.rows.map(r => [
      r.name, r.username, r.email, r.status,
      r.best_score ?? '', r.attempts, r.certificate_number ?? '',
      r.completed_at ?? '',
    ])
    const csv = [head, ...body].map(row =>
      row.map(v => `"${String(v).replaceAll('"', '""')}"`).join(',')
    ).join('\n')
    const blob = new Blob([csv], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = `${data.module.slug}-roster.csv`
    a.click(); URL.revokeObjectURL(url)
  }

  return (
    <div className="min-h-screen" style={{ background: theme.bg }}>
      <TopBar title="Compliance · Training" />
      <div className="p-4 lg:p-6 max-w-[1200px] mx-auto space-y-5">
        <Link href="/compliance"
              className="inline-flex items-center gap-1.5 text-xs font-semibold"
              style={{ color: theme.orange }}>
          <ArrowLeft className="w-3.5 h-3.5" /> Back to Compliance
        </Link>

        {loading && (
          <div className="rounded-xl p-6 text-sm"
               style={{ background: theme.card, border: `1px solid ${theme.cardBdr}`, color: theme.t3 }}>
            Loading…
          </div>
        )}

        {error && (
          <div className="rounded-md p-3 flex items-start gap-2"
               style={{ background: theme.erB, border: `1px solid ${theme.er}30` }}>
            <AlertCircle className="w-4 h-4 mt-0.5" style={{ color: theme.er }} />
            <p className="text-sm" style={{ color: theme.er }}>{error}</p>
          </div>
        )}

        {data && (
          <>
            <div className="rounded-2xl p-6"
                 style={{ background: theme.card, border: `1px solid ${theme.cardBdr}`, boxShadow: theme.cardSh }}>
              <h1 className="font-display-tight text-3xl font-bold" style={{ color: theme.navy }}>
                {data.module.title}
              </h1>
              <p className="text-xs mt-1" style={{ color: theme.t2 }}>
                Open {new Date(data.module.open_from).toLocaleDateString('en-BW')} –
                &nbsp;{new Date(data.module.open_until).toLocaleDateString('en-BW')} ·
                Pass mark {data.module.pass_mark_pct}% ·
                {data.module.is_published ? ' Published' : ' Draft (not visible to staff)'}
              </p>

              <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mt-5">
                <Stat label="People to train" value={data.total.toLocaleString('en-BW')} theme={theme} />
                <Stat label="Passed"       value={data.done.toLocaleString('en-BW')}       theme={theme} accent="#059669" />
                <Stat label="In progress"  value={data.attempted.toLocaleString('en-BW')}  theme={theme} accent="#D97706" />
                <Stat label="Not started"  value={data.not_started.toLocaleString('en-BW')} theme={theme} accent="#DC2626" />
              </div>
              <div className="mt-4 h-2 rounded-full overflow-hidden"
                   style={{ background: theme.g100 }}>
                <div className="h-full" style={{
                  width: `${data.total ? (data.done * 100 / data.total) : 0}%`,
                  background: '#059669',
                }} />
              </div>
              <p className="text-xs mt-2" style={{ color: theme.t2 }}>
                {data.total ? Math.round(data.done * 100 / data.total) : 0}% complete.
              </p>
            </div>

            <div className="rounded-xl p-4 flex items-center gap-3 flex-wrap"
                 style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
              <div className="flex items-center gap-2 flex-1 min-w-[220px]">
                <Search className="w-4 h-4" style={{ color: theme.t3 }} />
                <input value={q} onChange={e => setQ(e.target.value)}
                       placeholder="Search name, username or email"
                       className="flex-1 text-sm px-2 py-1.5 rounded"
                       style={{ background: '#fff', border: `1px solid ${theme.cardBdr}`, color: theme.text }} />
              </div>
              <select value={status} onChange={e => setStatus(e.target.value as typeof status)}
                      className="text-sm px-2 py-1.5 rounded"
                      style={{ background: '#fff', border: `1px solid ${theme.cardBdr}`, color: theme.text }}>
                <option value="all">All ({data.total.toLocaleString('en-BW')})</option>
                <option value="passed">Passed ({data.done.toLocaleString('en-BW')})</option>
                <option value="attempted">In progress ({data.attempted.toLocaleString('en-BW')})</option>
                <option value="not_started">Not started ({data.not_started.toLocaleString('en-BW')})</option>
              </select>
              <button onClick={exportCsv}
                      className="inline-flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-md"
                      style={{ background: theme.navy, color: '#fff' }}>
                <Download className="w-3.5 h-3.5" /> CSV
              </button>
            </div>

            <div className="rounded-xl overflow-hidden"
                 style={{ background: theme.card, border: `1px solid ${theme.cardBdr}`, boxShadow: theme.cardSh }}>
              <table className="min-w-full text-sm">
                <thead style={{ background: theme.g100 }}>
                  <tr>
                    <Th theme={theme}>Person</Th>
                    <Th theme={theme}>Status</Th>
                    <Th theme={theme} right>Best score</Th>
                    <Th theme={theme} right>Attempts</Th>
                    <Th theme={theme}>Certificate</Th>
                    <Th theme={theme}>Completed</Th>
                  </tr>
                </thead>
                <tbody className="divide-y" style={{ borderColor: theme.cardBdr }}>
                  {rows.map(r => (
                    <tr key={r.user_id}>
                      <td className="px-4 py-2">
                        <div style={{ color: theme.text }}>{r.name}</div>
                        <div className="text-[11px]" style={{ color: theme.t3 }}>{r.email}</div>
                      </td>
                      <td className="px-4 py-2"><StatusPill theme={theme} status={r.status} /></td>
                      <td className="px-4 py-2 text-right tabular-nums" style={{ color: theme.text }}>
                        {r.best_score ?? '—'}{r.best_score != null ? '%' : ''}
                      </td>
                      <td className="px-4 py-2 text-right tabular-nums" style={{ color: theme.text }}>
                        {r.attempts}
                      </td>
                      <td className="px-4 py-2 font-mono text-[11px]" style={{ color: theme.t2 }}>
                        {r.certificate_number || '—'}
                      </td>
                      <td className="px-4 py-2 text-[11px]" style={{ color: theme.t3 }}>
                        {r.completed_at ? new Date(r.completed_at).toLocaleString('en-BW') : '—'}
                      </td>
                    </tr>
                  ))}
                  {rows.length === 0 && (
                    <tr><td colSpan={6} className="px-4 py-6 text-center text-xs"
                            style={{ color: theme.t3 }}>No rows match.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

function Stat({ theme, label, value, accent }: { theme: any; label: string; value: string; accent?: string }) {
  return (
    <div className="rounded-lg p-3"
         style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}` }}>
      <div className="text-[10px] uppercase tracking-wider font-semibold"
           style={{ color: theme.t3 }}>{label}</div>
      <div className="font-display-tight text-2xl font-bold tabular-nums mt-1"
           style={{ color: accent || theme.navy }}>{value}</div>
    </div>
  )
}
function Th({ theme, children, right }: { theme: any; children: React.ReactNode; right?: boolean }) {
  return (
    <th className={`px-4 py-2 text-[10px] uppercase tracking-wider font-semibold ${right ? 'text-right' : 'text-left'}`}
        style={{ color: theme.t2 }}>{children}</th>
  )
}
function StatusPill({ theme, status }: { theme: any; status: Row['status'] }) {
  const map: Record<Row['status'], { bg: string; fg: string; label: string; icon: React.ComponentType<{className?:string}> }> = {
    passed:      { bg: '#ECFDF5', fg: '#059669', label: 'Passed',     icon: CheckCircle2 },
    attempted:   { bg: '#FFFBEB', fg: '#D97706', label: 'In progress', icon: RotateCcw },
    not_started: { bg: '#FEF2F2', fg: '#DC2626', label: 'Not started', icon: XCircle },
  }
  const c = map[status]; const I = c.icon
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase tracking-wider"
          style={{ background: c.bg, color: c.fg }}>
      <I className="w-3 h-3" /> {c.label}
    </span>
  )
}
