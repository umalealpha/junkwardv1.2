'use client'

/**
 * Renewals coming up — for /premium-lapse, which until now answered only
 * "whose debits are failing?" and said nothing about whose cover simply runs
 * out. Both are ways to lose the premium; only one of them was on the screen.
 */
import { useEffect, useState } from 'react'
import { apiFetch } from '@/lib/api'
import { GraphitePanelShell } from './GraphitePanelShell'

interface Row {
  policy_no?: string
  gfs_ref?: string
  product_line?: string
  expiry_date?: string
  days_to_expiry?: number
}
interface Resp {
  received_at: string | null
  window_days: number
  feed_window_days: number
  due_count: number
  expired_count: number
  total_in_feed: number
  by_product_line: { product_line: string; count: number }[]
  results: Row[]
}

// Graphite's trigger list only ever carries 0-30 days, so these narrow it —
// they never promise a horizon the feed does not have.
const WINDOWS = [7, 14, 30]

export function RenewalsDuePanel() {
  const [days, setDays] = useState(30)
  const [data, setData] = useState<Resp | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    setLoading(true); setError(null)
    apiFetch<Resp>(`/graphite-panels/renewals-due/?days=${days}`)
      .then(d => { if (live) setData(d) })
      .catch(e => { if (live) setError(e?.message || 'Could not load renewals.') })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [days])

  const rows = data?.results || []

  return (
    <GraphitePanelShell
      title="Renewals coming up"
      subtitle="Policies reaching their expiry date — the other way premium walks out. Graphite triggers 30 days out."
      receivedAt={data?.received_at}
      loading={loading}
      error={error}
      empty={!loading && !error && rows.length === 0}
      emptyText="No policies fall inside this window."
    >
      <div className="px-4 pt-3 flex items-center gap-2">
        {WINDOWS.map(w => (
          <button
            key={w}
            onClick={() => setDays(w)}
            className="px-2.5 py-1 rounded-full text-xs font-medium transition-colors"
            style={w === days
              ? { background: '#0D1B2A', color: 'white' }
              : { background: '#F1F5F9', color: '#475569' }}
          >
            Next {w} days
          </button>
        ))}
        {data && (
          <span className="ml-auto text-xs text-slate-500">
            {data.due_count.toLocaleString('en-GB')} due
            {data.expired_count > 0 && (
              <> · <strong className="text-[#B42318]">{data.expired_count.toLocaleString('en-GB')} already past expiry</strong></>
            )}
            {' '}of {data.total_in_feed.toLocaleString('en-GB')} in the feed
          </span>
        )}
      </div>

      <div className="max-h-80 overflow-y-auto mt-3">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-slate-50 text-xs text-slate-500">
            <tr>
              <th className="text-left font-medium px-4 py-2">Policy</th>
              <th className="text-left font-medium px-4 py-2">Product line</th>
              <th className="text-left font-medium px-4 py-2">Expiry</th>
              <th className="text-right font-medium px-4 py-2">Days</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const d = Number(r.days_to_expiry ?? 0)
              return (
                <tr key={`${r.policy_no || r.gfs_ref || i}-${i}`} className="border-t border-slate-100">
                  <td className="px-4 py-2 font-mono text-xs">{r.policy_no || r.gfs_ref || '—'}</td>
                  <td className="px-4 py-2 text-slate-600">{r.product_line || '—'}</td>
                  <td className="px-4 py-2 text-slate-600">{r.expiry_date || '—'}</td>
                  <td className="px-4 py-2 text-right tabular-nums"
                      style={d < 0 ? { color: '#B42318', fontWeight: 600 } : undefined}>
                    {d < 0 ? `${Math.abs(d)} overdue` : d}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </GraphitePanelShell>
  )
}
