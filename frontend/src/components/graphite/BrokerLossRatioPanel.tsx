'use client'

/**
 * Broker loss ratios — for /underwriting, which showed the document generator
 * and its register and nothing at all about which intermediaries make money.
 *
 * The 70% flag is the CFO's standing underwriting rule, and the threshold is
 * served by the API rather than hardcoded here, so the screen cannot drift
 * away from the rule.
 */
import { useEffect, useState } from 'react'
import { apiFetch } from '@/lib/api'
import { GraphitePanelShell } from './GraphitePanelShell'

interface Row {
  broker: string
  premium_fy: number
  payment: number
  reserve: number
  claim_count: number
  incurred_lr_pct: number
  reserve_lr_pct: number
  flagged: boolean
}
interface Resp {
  received_at: string | null
  threshold_pct: number
  broker_count: number
  flagged_count: number
  total_premium_fy: number
  results: Row[]
  // False when the Graphite replica could not be read. The panel then shows
  // NOTHING rather than the pushed figures, which escalate the wrong brokers —
  // so this state has to be loud, or "0 of 0 brokers over 70%" reads like good
  // news when it means the control is off.
  computed_live: boolean
  source: string
}

const pula = (n: number) =>
  n.toLocaleString('en-BW', { minimumFractionDigits: 0, maximumFractionDigits: 0 })

export function BrokerLossRatioPanel() {
  const [data, setData] = useState<Resp | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    apiFetch<Resp>('/graphite-panels/broker-loss-ratios/')
      .then(d => { if (live) setData(d) })
      .catch(e => { if (live) setError(e?.message || 'Could not load broker loss ratios.') })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [])

  const rows = data?.results || []
  const unavailable = data != null && data.computed_live === false

  return (
    <GraphitePanelShell
      title="Broker loss ratios"
      subtitle={unavailable
        ? 'The 70% check could not run.'
        : data
          ? `${data.flagged_count} of ${data.broker_count} brokers incurring over ${data.threshold_pct}% of premium. `
            + 'Incurred is paid plus the open reserve.'
          : 'Which intermediaries make us money.'}
      receivedAt={data?.received_at}
      // Omni computes this panel from the Graphite replica on request, so there
      // is no push timestamp to show — and the shell's default would read
      // "not received yet" next to live figures.
      freshness={data ? (data.computed_live ? 'Live from Graphite · just now' : 'Not available') : undefined}
      loading={loading}
      // A failed read is an error state, not an empty one: the rule did not run.
      error={error || (unavailable ? data!.source : null)}
      empty={!loading && !error && !unavailable && rows.length === 0}
    >
      <div className="max-h-80 overflow-y-auto">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-slate-50 text-xs text-slate-500">
            <tr>
              <th className="text-left font-medium px-4 py-2">Broker</th>
              <th className="text-right font-medium px-4 py-2">Premium (FY)</th>
              <th className="text-right font-medium px-4 py-2">Claims</th>
              <th className="text-right font-medium px-4 py-2">On reserve</th>
              <th className="text-right font-medium px-4 py-2">Incurred</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={`${r.broker}-${i}`} className="border-t border-slate-100">
                <td className="px-4 py-2">{r.broker}</td>
                <td className="px-4 py-2 text-right tabular-nums text-slate-600">P {pula(r.premium_fy)}</td>
                <td className="px-4 py-2 text-right tabular-nums text-slate-600">{r.claim_count}</td>
                <td className="px-4 py-2 text-right tabular-nums text-slate-500">
                  {r.reserve_lr_pct.toFixed(1)}%
                </td>
                <td className="px-4 py-2 text-right tabular-nums font-semibold"
                    style={{ color: r.flagged ? '#B42318' : '#15803D' }}>
                  {r.incurred_lr_pct.toFixed(1)}%
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </GraphitePanelShell>
  )
}
