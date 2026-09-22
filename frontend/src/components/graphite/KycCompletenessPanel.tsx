'use client'

/**
 * KYC completeness by branch — for the Compliance overview, which tracked our
 * own policies and controls but showed nothing about the 803-agent book the
 * regulator would actually ask about.
 *
 * Branch percentages come from the API, which totals policies and completions
 * before dividing. Averaging each agent's own percentage is the tempting
 * version and it is wrong: it gives a one-policy agent the same weight as one
 * carrying four hundred.
 */
import { useEffect, useState } from 'react'
import { apiFetch } from '@/lib/api'
import { GraphitePanelShell } from './GraphitePanelShell'

interface Branch {
  branch: string
  policies: number
  complete: number
  agents: number
  pct_complete: number
}
interface Resp {
  received_at: string | null
  agent_count: number
  total_policies: number
  total_complete: number
  pct_complete: number
  by_branch: Branch[]
}

function tone(pct: number): string {
  if (pct >= 95) return '#15803D'
  if (pct >= 80) return '#B45309'
  return '#B42318'
}

export function KycCompletenessPanel() {
  const [data, setData] = useState<Resp | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    apiFetch<Resp>('/graphite-panels/kyc-completeness/')
      .then(d => { if (live) setData(d) })
      .catch(e => { if (live) setError(e?.message || 'Could not load KYC completeness.') })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [])

  const branches = data?.by_branch || []

  return (
    <GraphitePanelShell
      title="KYC completeness"
      subtitle={data
        ? `${data.total_complete.toLocaleString('en-GB')} of ${data.total_policies.toLocaleString('en-GB')} policies complete across ${data.agent_count} agents.`
        : 'How much of the book has its KYC done.'}
      receivedAt={data?.received_at}
      loading={loading}
      error={error}
      empty={!loading && !error && branches.length === 0}
    >
      <div className="px-4 py-3">
        {data && (
          <div className="mb-4">
            <div className="flex items-baseline gap-2">
              <span className="text-2xl font-semibold tabular-nums"
                    style={{ color: tone(data.pct_complete) }}>
                {data.pct_complete.toFixed(1)}%
              </span>
              <span className="text-xs text-slate-500">complete, whole book</span>
            </div>
          </div>
        )}
        <ul className="space-y-2">
          {branches.map(b => (
            <li key={b.branch} className="flex items-center gap-3">
              <span className="w-40 shrink-0 text-sm text-slate-700 truncate" title={b.branch}>{b.branch}</span>
              <span className="flex-1 h-2 rounded-full bg-slate-100 overflow-hidden">
                <span className="block h-full rounded-full"
                      style={{ width: `${Math.min(100, Math.max(0, b.pct_complete))}%`,
                               background: tone(b.pct_complete) }} />
              </span>
              <span className="w-14 text-right text-sm tabular-nums" style={{ color: tone(b.pct_complete) }}>
                {b.pct_complete.toFixed(0)}%
              </span>
              <span className="w-24 text-right text-xs text-slate-400 tabular-nums">
                {b.complete.toLocaleString('en-GB')}/{b.policies.toLocaleString('en-GB')}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </GraphitePanelShell>
  )
}
