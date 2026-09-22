'use client'

/**
 * /reports/ma-trace — TB → BS / P&L line traceability.
 *
 * Lifted from ADSA pipeline (build_monthly_pack.py) on 2026-05-18.
 * Surfaces every GL account's closing balance + the bucket the report
 * engine slotted it into. Anything in `Unmapped` is a production
 * incident: a rand of balance the MA workbook is silently missing.
 */
import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { AlertCircle, CheckCircle2, RefreshCw } from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { useCompany } from '@/contexts/CompanyContext'
import { apiFetch, getToken } from '@/lib/api'
import { localYmd } from '@/lib/utils'

interface TraceRow {
  code: string
  name: string
  balance_bwp: number
  bucket: string
}

interface TraceResponse {
  as_of: string
  rows: TraceRow[]
  totals: Record<string, number>
  unmapped: { code: string; name: string; balance_bwp: number }[]
  integrity: { balanced: boolean; total_dr: number; total_cr: number }
}

function fmt(n: number): string {
  return new Intl.NumberFormat('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n || 0)
}

export default function MaTracePage() {
  const router = useRouter()
  const { theme } = useTheme()
  const { selectedId } = useCompany()
  const today = localYmd(new Date())
  const [asOf, setAsOf] = useState(today)
  const [data, setData] = useState<TraceResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [asOf, selectedId])

  async function load() {
    setLoading(true); setError(null)
    try {
      const qs = new URLSearchParams({ as_of: asOf })
      if (selectedId) qs.set('company', selectedId)
      const r = await apiFetch<TraceResponse>(`/reports/ma-trace/?${qs.toString()}`)
      setData(r)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load trace')
    } finally {
      setLoading(false)
    }
  }

  const unmappedTotal = data?.unmapped.reduce((s, r) => s + r.balance_bwp, 0) || 0

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar title="TB → BS Trace" breadcrumbs={[{ label: 'Reporting' }, { label: 'TB Trace' }]} />
      <div className="flex-1 p-6 space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-bold" style={{ color: theme.navy }}>Trial Balance → Report Trace</h1>
            <p className="text-xs mt-0.5" style={{ color: theme.t2 }}>
              Every GL account's closing balance + the BS / P&amp;L bucket the report engine slotted it into.
              Anything tagged <strong>UNMAPPED</strong> is a production incident.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <label className="text-xs" style={{ color: theme.t2 }}>As of</label>
            <input type="date" value={asOf} onChange={e => setAsOf(e.target.value)}
                   className="h-9 px-2 rounded-md text-sm"
                   style={{ background: theme.card, border: `1px solid ${theme.cardBdr}`, color: theme.text }} />
            <button type="button" onClick={load} disabled={loading}
                    className="inline-flex items-center gap-1.5 h-9 px-3 rounded-md text-sm font-semibold disabled:opacity-50"
                    style={{ background: theme.orange, color: '#fff' }}>
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} /> Refresh
            </button>
          </div>
        </div>

        {error && (
          <div className="rounded-lg p-3 flex items-center gap-2"
               style={{ background: theme.erB, border: `1px solid ${theme.er}30`, color: theme.er }}>
            <AlertCircle className="w-4 h-4" /> {error}
          </div>
        )}

        {data && (
          <>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <KpiCard label="Integrity (Dr − Cr)" value={fmt(data.integrity.total_dr - data.integrity.total_cr)}
                       ok={data.integrity.balanced} theme={theme} />
              <KpiCard label="Unmapped accounts" value={String(data.unmapped.length)}
                       ok={data.unmapped.length === 0} theme={theme} />
              <KpiCard label="Value at risk (Unmapped Σ)" value={`BWP ${fmt(unmappedTotal)}`}
                       ok={Math.abs(unmappedTotal) < 1} theme={theme} />
            </div>

            {data.unmapped.length > 0 && (
              <div className="rounded-2xl p-4"
                   style={{ background: theme.erB, border: `1px solid ${theme.er}40` }}>
                <h3 className="font-semibold flex items-center gap-2" style={{ color: theme.er }}>
                  <AlertCircle className="w-4 h-4" /> Unmapped accounts ({data.unmapped.length})
                </h3>
                <p className="text-xs mt-1" style={{ color: theme.er }}>
                  Each row below carries a balance the BS / P&amp;L is silently dropping. Fix the GL prefix or
                  add an explicit mapping in <code>reporting/ma_trace.py</code>.
                </p>
                <table className="w-full text-sm mt-3">
                  <thead>
                    <tr style={{ color: theme.er }}>
                      <th className="text-left text-[11px] uppercase tracking-wider pb-2">Code</th>
                      <th className="text-left text-[11px] uppercase tracking-wider pb-2">Name</th>
                      <th className="text-right text-[11px] uppercase tracking-wider pb-2">Balance (BWP)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.unmapped.map(r => (
                      <tr key={r.code} style={{ borderTop: `1px solid ${theme.er}30` }}>
                        <td className="py-1.5 font-mono" style={{ color: theme.text }}>{r.code}</td>
                        <td className="py-1.5" style={{ color: theme.text }}>{r.name}</td>
                        <td className="py-1.5 text-right tabular-nums" style={{ color: theme.text }}>{fmt(r.balance_bwp)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <div className="rounded-2xl p-4"
                 style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
              <div className="flex items-center justify-between mb-2">
                <h3 className="font-semibold" style={{ color: theme.text }}>Per-bucket totals</h3>
                <span className="text-xs" style={{ color: theme.t2 }}>{data.rows.length} rows</span>
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                {Object.entries(data.totals).map(([k, v]) => (
                  <div key={k} className="rounded-lg p-3"
                       style={{
                         background: k === 'UNMAPPED' ? theme.erB : theme.g100,
                         border: `1px solid ${k === 'UNMAPPED' ? theme.er + '40' : theme.cardBdr}`,
                       }}>
                    <div className="text-[10px] uppercase tracking-wider font-semibold"
                         style={{ color: k === 'UNMAPPED' ? theme.er : theme.t2 }}>
                      {k.replaceAll('_', ' ')}
                    </div>
                    <div className="text-sm font-bold tabular-nums mt-1" style={{ color: theme.text }}>
                      {fmt(v)}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

function KpiCard({ label, value, ok, theme }: { label: string; value: string; ok: boolean; theme: any }) {
  return (
    <div className="rounded-2xl p-5"
         style={{ background: theme.card, border: `1px solid ${ok ? theme.ok + '40' : theme.er + '40'}` }}>
      <div className="flex items-center justify-between">
        <span className="text-[11px] uppercase tracking-wider font-semibold" style={{ color: theme.t2 }}>{label}</span>
        {ok ? <CheckCircle2 className="w-4 h-4" style={{ color: theme.ok }} /> :
              <AlertCircle  className="w-4 h-4" style={{ color: theme.er }} />}
      </div>
      <div className="text-2xl font-bold tabular-nums mt-1" style={{ color: ok ? theme.text : theme.er }}>
        {value}
      </div>
    </div>
  )
}
