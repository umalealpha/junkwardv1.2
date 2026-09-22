'use client'

/**
 * /premium-lapse — Premium Lapse Early-Warning (wow feature #6, 2026-07-22).
 * Policies whose most-recent debits are failing, with the monthly premium at
 * risk — so the collections shortfall is seen BEFORE month-end shows it.
 * Read-only. Server-gated to finance / management (CanViewFinancials).
 */
import { useCallback, useEffect, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { getPremiumLapse, type PremiumLapseResponse, type LapseRow } from '@/lib/api'
import { RenewalsDuePanel } from '@/components/graphite/RenewalsDuePanel'
import { Loader2, AlertTriangle } from 'lucide-react'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'
const SERIF = 'Book Antiqua, Palatino, Georgia, serif'

const TIER: Record<string, { label: string; bg: string; fg: string }> = {
  tier1: { label: '1 miss', bg: '#FFF7ED', fg: '#B45309' },
  tier2: { label: '2 misses', bg: '#FEF3C7', fg: '#92400E' },
  tier3: { label: '3+ misses', bg: '#FEF2F2', fg: '#B42318' },
}

function money(s: string): string {
  const n = Number(s)
  if (!isFinite(n)) return s
  return n.toLocaleString('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export default function PremiumLapsePage() {
  const [months, setMonths] = useState(6)
  const [data, setData] = useState<PremiumLapseResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [denied, setDenied] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null); setDenied(false)
    try {
      setData(await getPremiumLapse(months))
    } catch (e: any) {
      if (e?.status === 403) setDenied(true)
      else setError(e?.message || 'Failed to load')
    } finally {
      setLoading(false)
    }
  }, [months])

  useEffect(() => { load() }, [load])

  const rows: LapseRow[] = data?.rows || []

  return (
    <>
      <TopBar title="Premium Lapse Early-Warning" />
      <div className="p-6 max-w-6xl mx-auto">
        <header className="mb-6">
          <h1 style={{ fontFamily: SERIF, color: NAVY }} className="text-2xl font-semibold tracking-tight">
            Who is about to lapse?
          </h1>
          <p className="text-sm text-slate-500 mt-1 max-w-2xl">
            Policies whose recent debits keep failing — and the monthly premium riding on them. See the
            shortfall before the month-end does.
          </p>
        </header>

        <div className="flex items-center gap-2 mb-6">
          {[3, 6, 12].map((m) => {
            const active = m === months
            return (
              <button
                key={m}
                onClick={() => setMonths(m)}
                className="px-3 py-1.5 rounded-full text-sm font-medium transition-colors"
                style={active ? { background: NAVY, color: 'white' } : { background: '#F1F5F9', color: '#475569' }}
              >
                Last {m} months
              </button>
            )
          })}
        </div>

        {loading && (
          <div className="flex items-center gap-2 text-slate-500 py-16 justify-center">
            <Loader2 className="animate-spin" size={18} /> Scanning the debit history…
          </div>
        )}
        {denied && !loading && (
          <div className="rounded-xl border border-amber-200 bg-amber-50 p-6 text-amber-800 text-sm">
            This view is restricted to finance and management.
          </div>
        )}
        {error && !loading && (
          <div className="rounded-xl border border-red-200 bg-red-50 p-6 text-red-700 text-sm">{error}</div>
        )}

        {!loading && !denied && !error && data && (
          <>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
              <div className="rounded-2xl p-5 text-white" style={{ background: NAVY }}>
                <div className="text-3xl font-bold tabular-nums" style={{ color: ORANGE }}>
                  P {money(data.at_risk_monthly_bwp)}
                </div>
                <div className="text-xs mt-1 text-slate-300">monthly premium at risk (2+ misses)</div>
              </div>
              <Stat n={data.at_risk_policies} label="at-risk policies" />
              <Stat n={data.counts.tier2} label="2 consecutive misses" tone="#92400E" />
              <Stat n={data.counts.tier3} label="3+ consecutive misses" tone="#B42318" />
            </div>

            {rows.length === 0 ? (
              <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-6 text-emerald-700 text-sm">
                No policies with failing recent debits in this window.
              </div>
            ) : (
              <div className="rounded-2xl bg-white overflow-hidden" style={{ border: '1px solid #E2E8F0' }}>
                <table className="w-full text-sm">
                  <thead>
                    <tr style={{ background: NAVY }} className="text-white text-left">
                      <th className="px-4 py-3 font-medium">Policy</th>
                      <th className="px-4 py-3 font-medium">Product</th>
                      <th className="px-4 py-3 font-medium">Run</th>
                      <th className="px-4 py-3 font-medium text-right">Monthly at risk</th>
                      <th className="px-4 py-3 font-medium">Last seen</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => {
                      const t = TIER[r.tier] || TIER.tier1
                      return (
                        <tr key={r.policy_number} className="border-t border-slate-100">
                          <td className="px-4 py-2.5 font-mono font-semibold" style={{ color: NAVY }}>{r.policy_number}</td>
                          <td className="px-4 py-2.5 text-slate-600">{r.product_name || '—'}</td>
                          <td className="px-4 py-2.5">
                            <span className="text-xs font-semibold px-2 py-0.5 rounded-full" style={{ background: t.bg, color: t.fg }}>
                              {t.label}
                            </span>
                          </td>
                          <td className="px-4 py-2.5 text-right font-semibold tabular-nums text-slate-800">P {money(r.est_monthly_bwp)}</td>
                          <td className="px-4 py-2.5 text-slate-400">{r.last_seen}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
                {data.at_risk_policies > rows.length && (
                  <div className="px-4 py-2 text-xs text-slate-400 border-t border-slate-100">
                    Showing top {rows.length} of {data.at_risk_policies}; headline totals cover all.
                  </div>
                )}
              </div>
            )}
          </>
        )}

        {/* Failing debits are only half of the leak: a policy that simply
            reaches its expiry date loses the premium just as surely. That side
            arrives nightly from Graphite, so it sits below rather than mixed
            into the lapse table above. */}
        <div className="mt-8">
          <RenewalsDuePanel />
        </div>
      </div>
    </>
  )
}

function Stat({ n, label, tone }: { n: number; label: string; tone?: string }) {
  return (
    <div className="rounded-2xl p-5 bg-white" style={{ border: '1px solid #E2E8F0' }}>
      <div className="text-3xl font-bold tabular-nums" style={{ color: tone || '#0D1B2A' }}>{n}</div>
      <div className="text-xs mt-1 text-slate-500">{label}</div>
    </div>
  )
}
