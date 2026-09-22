'use client'

/**
 * The large-loss list — for the Claims Register, which pages through every
 * claim in date order and so never puts the biggest exposures in front of
 * anyone.
 *
 * Exposure adds the magnitudes of paid and reserve rather than summing them
 * signed: Graphite sends reserves negative on some cuts, and netting a large
 * reserve against a large payment would rank a serious claim as a small one.
 */
import { useEffect, useState } from 'react'
import { apiFetch } from '@/lib/api'
import { GraphitePanelShell } from './GraphitePanelShell'

interface Row {
  claim_no: string
  claim_type: string
  status: string
  paid: number
  reserve: number
  exposure: number
  repudiated: boolean
}
interface Resp {
  received_at: string | null
  claim_count: number
  total_exposure: number
  repudiated_count: number
  results: Row[]
}

const pula = (n: number) =>
  n.toLocaleString('en-BW', { minimumFractionDigits: 0, maximumFractionDigits: 0 })

export function MajorClaimsPanel() {
  const [data, setData] = useState<Resp | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    apiFetch<Resp>('/graphite-panels/major-claims/')
      .then(d => { if (live) setData(d) })
      .catch(e => { if (live) setError(e?.message || 'Could not load major claims.') })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [])

  const rows = (data?.results || []).slice(0, 15)

  return (
    <GraphitePanelShell
      title="Largest claims"
      subtitle={data
        ? `${data.claim_count} major claims · P ${pula(data.total_exposure)} total exposure (paid + reserve).`
        : 'The biggest exposures on the book.'}
      receivedAt={data?.received_at}
      loading={loading}
      error={error}
      empty={!loading && !error && rows.length === 0}
    >
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-xs text-slate-500">
            <tr>
              <th className="text-left font-medium px-4 py-2">Claim</th>
              <th className="text-left font-medium px-4 py-2">Type</th>
              <th className="text-left font-medium px-4 py-2">Status</th>
              <th className="text-right font-medium px-4 py-2">Paid</th>
              <th className="text-right font-medium px-4 py-2">Reserve</th>
              <th className="text-right font-medium px-4 py-2">Exposure</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={`${r.claim_no}-${i}`} className="border-t border-slate-100">
                <td className="px-4 py-2 font-mono text-xs">{r.claim_no}</td>
                <td className="px-4 py-2 text-slate-600">{r.claim_type}</td>
                <td className="px-4 py-2 text-slate-600 capitalize">
                  {r.status}
                  {r.repudiated && (
                    <span className="ml-2 text-[11px] font-semibold text-[#B42318]">repudiated</span>
                  )}
                </td>
                <td className="px-4 py-2 text-right tabular-nums text-slate-600">P {pula(r.paid)}</td>
                <td className="px-4 py-2 text-right tabular-nums text-slate-600">P {pula(r.reserve)}</td>
                <td className="px-4 py-2 text-right tabular-nums font-semibold" style={{ color: '#0D1B2A' }}>
                  P {pula(r.exposure)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data && data.claim_count > rows.length && (
        <div className="px-4 py-2 text-xs text-slate-400 border-t border-slate-100">
          Showing the top {rows.length} by exposure; the totals above cover all {data.claim_count}.
        </div>
      )}
    </GraphitePanelShell>
  )
}
