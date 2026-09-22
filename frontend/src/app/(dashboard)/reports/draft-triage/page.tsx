'use client'

/**
 * /reports/draft-triage — classify aged draft JEs into action buckets.
 *
 * Lifted from ADSA pipeline (triage_drafts.py) on 2026-05-18. Gives
 * the CFO close-loop a clear queue: which drafts to post, which to
 * delete, which need investigation, which to revisit.
 */
import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { AlertCircle, RefreshCw, CheckCircle2, Trash2, AlertTriangle, ClipboardCheck } from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { useCompany } from '@/contexts/CompanyContext'
import { apiFetch, getToken } from '@/lib/api'

type Bucket = 'POST_READY' | 'STALE_DELETE' | 'INVESTIGATE' | 'REVIEW'

interface TriageRow {
  id: string
  entry_number: string
  entry_date: string
  description: string
  source_type: string
  company: string
  total_dr: number
  total_cr: number
  imbalance: number
  line_count: number
  has_contra: boolean
  age_days: number
  bucket: Bucket
  reason: string
}

interface TriageResponse {
  as_of: string
  buckets: Record<Bucket, TriageRow[]>
  totals: Record<Bucket, { count: number; value_at_risk_bwp: number }>
}

const BUCKET_ORDER: Bucket[] = ['POST_READY', 'STALE_DELETE', 'INVESTIGATE', 'REVIEW']

const BUCKET_CONFIG: Record<Bucket, { label: string; icon: any; tint: string }> = {
  POST_READY:   { label: 'Post ready',   icon: CheckCircle2,    tint: '#059669' },
  STALE_DELETE: { label: 'Stale delete', icon: Trash2,          tint: '#dc2626' },
  INVESTIGATE:  { label: 'Investigate',  icon: AlertTriangle,   tint: '#d97706' },
  REVIEW:       { label: 'Review',       icon: ClipboardCheck,  tint: '#2563eb' },
}

function fmt(n: number): string {
  return new Intl.NumberFormat('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n || 0)
}

export default function DraftTriagePage() {
  const router = useRouter()
  const { theme } = useTheme()
  const { selectedId } = useCompany()
  const [data, setData] = useState<TriageResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [active, setActive] = useState<Bucket>('POST_READY')

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId])

  async function load() {
    setLoading(true); setError(null)
    try {
      const qs = new URLSearchParams()
      if (selectedId) qs.set('company', selectedId)
      const r = await apiFetch<TriageResponse>(`/journal-entries/triage/${qs.toString() ? '?' + qs.toString() : ''}`)
      setData(r)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load triage')
    } finally {
      setLoading(false)
    }
  }

  const rows = data?.buckets[active] || []
  const totalAtRisk = useMemo(() => {
    if (!data) return 0
    return BUCKET_ORDER.reduce((s, b) => s + (data.totals[b]?.value_at_risk_bwp || 0), 0)
  }, [data])

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar title="Draft Triage" breadcrumbs={[{ label: 'Reporting' }, { label: 'Draft Triage' }]} />
      <div className="flex-1 p-6 space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-bold" style={{ color: theme.navy }}>Draft Journal Entry Triage</h1>
            <p className="text-xs mt-0.5" style={{ color: theme.t2 }}>
              Every draft JE classified into one of four action buckets so the close-team has a clear queue.
              Total value at risk in drafts: <strong>BWP {fmt(totalAtRisk)}</strong>.
            </p>
          </div>
          <button type="button" onClick={load} disabled={loading}
                  className="inline-flex items-center gap-1.5 h-9 px-3 rounded-md text-sm font-semibold disabled:opacity-50"
                  style={{ background: theme.orange, color: '#fff' }}>
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} /> Refresh
          </button>
        </div>

        {error && (
          <div className="rounded-lg p-3 flex items-center gap-2"
               style={{ background: theme.erB, border: `1px solid ${theme.er}30`, color: theme.er }}>
            <AlertCircle className="w-4 h-4" /> {error}
          </div>
        )}

        {data && (
          <>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              {BUCKET_ORDER.map(b => {
                const cfg = BUCKET_CONFIG[b]
                const t = data.totals[b]
                const Icon = cfg.icon
                return (
                  <button key={b} type="button" onClick={() => setActive(b)}
                          className="rounded-2xl p-4 text-left transition-all"
                          style={{
                            background: active === b ? cfg.tint + '15' : theme.card,
                            border: `1px solid ${active === b ? cfg.tint + '55' : theme.cardBdr}`,
                          }}>
                    <div className="flex items-center justify-between">
                      <span className="text-[11px] uppercase tracking-wider font-semibold" style={{ color: cfg.tint }}>
                        {cfg.label}
                      </span>
                      <Icon className="w-4 h-4" style={{ color: cfg.tint }} />
                    </div>
                    <div className="text-2xl font-bold tabular-nums mt-1" style={{ color: theme.text }}>
                      {t?.count ?? 0}
                    </div>
                    <div className="text-xs mt-0.5" style={{ color: theme.t2 }}>
                      BWP {fmt(t?.value_at_risk_bwp || 0)}
                    </div>
                  </button>
                )
              })}
            </div>

            <div className="rounded-2xl overflow-hidden"
                 style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead style={{ background: theme.g100, borderBottom: `2px solid ${theme.cardBdr}` }}>
                    <tr>
                      <th className="px-3 py-2 text-left text-[11px] uppercase tracking-wider font-semibold" style={{ color: theme.t2 }}>Entry #</th>
                      <th className="px-3 py-2 text-left text-[11px] uppercase tracking-wider font-semibold" style={{ color: theme.t2 }}>Date</th>
                      <th className="px-3 py-2 text-left text-[11px] uppercase tracking-wider font-semibold" style={{ color: theme.t2 }}>Description</th>
                      <th className="px-3 py-2 text-left text-[11px] uppercase tracking-wider font-semibold" style={{ color: theme.t2 }}>Source</th>
                      <th className="px-3 py-2 text-left text-[11px] uppercase tracking-wider font-semibold" style={{ color: theme.t2 }}>Co.</th>
                      <th className="px-3 py-2 text-right text-[11px] uppercase tracking-wider font-semibold" style={{ color: theme.t2 }}>Dr</th>
                      <th className="px-3 py-2 text-right text-[11px] uppercase tracking-wider font-semibold" style={{ color: theme.t2 }}>Cr</th>
                      <th className="px-3 py-2 text-right text-[11px] uppercase tracking-wider font-semibold" style={{ color: theme.t2 }}>Δ</th>
                      <th className="px-3 py-2 text-right text-[11px] uppercase tracking-wider font-semibold" style={{ color: theme.t2 }}>Age</th>
                      <th className="px-3 py-2 text-left text-[11px] uppercase tracking-wider font-semibold" style={{ color: theme.t2 }}>Why</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.length === 0 && (
                      <tr><td colSpan={10} className="px-3 py-8 text-center text-sm" style={{ color: theme.t2 }}>
                        Nothing in this bucket.
                      </td></tr>
                    )}
                    {rows.map(r => (
                      <tr key={r.id} style={{ borderBottom: `1px solid ${theme.cardBdr}` }}>
                        <td className="px-3 py-2 font-mono text-xs">
                          <Link href={`/journal-entries/${r.id}`} style={{ color: theme.orange }}>
                            {r.entry_number || r.id.slice(0, 8)}
                          </Link>
                        </td>
                        <td className="px-3 py-2 text-xs" style={{ color: theme.t2 }}>{r.entry_date}</td>
                        <td className="px-3 py-2 truncate max-w-[300px]" style={{ color: theme.text }}>{r.description || '—'}</td>
                        <td className="px-3 py-2 text-xs" style={{ color: theme.t2 }}>{r.source_type}</td>
                        <td className="px-3 py-2 text-xs" style={{ color: theme.t2 }}>{r.company}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{fmt(r.total_dr)}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{fmt(r.total_cr)}</td>
                        <td className="px-3 py-2 text-right tabular-nums"
                            style={{ color: Math.abs(r.imbalance) > 0.01 ? theme.er : theme.t2 }}>
                          {fmt(r.imbalance)}
                        </td>
                        <td className="px-3 py-2 text-right tabular-nums text-xs"
                            style={{ color: r.age_days > 60 ? theme.er : theme.t2 }}>
                          {r.age_days}d
                        </td>
                        <td className="px-3 py-2 text-xs" style={{ color: theme.t2 }}>{r.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
