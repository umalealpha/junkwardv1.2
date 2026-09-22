'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { getSubrogationSummary, getToken } from '@/lib/api'
import type { SubrogationSummary } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { AlertCircle, ArrowRight, ShieldAlert, UserX, Coins } from 'lucide-react'

function fmt(v: string | number): string {
  const n = typeof v === 'string' ? parseFloat(v) : v
  if (Number.isNaN(n)) return '—'
  return new Intl.NumberFormat('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n)
}

export default function ClaimsRecoveriesDashboard() {
  const router = useRouter()
  const [data, setData] = useState<SubrogationSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    getSubrogationSummary()
      .then(setData)
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false))
  }, [router])

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Claims Recoveries"
        breadcrumbs={[{ label: 'Claims Recoveries' }]}
        actions={
          <Button variant="accent" size="sm" rightIcon={<ArrowRight className="w-3.5 h-3.5" />}
            onClick={() => router.push('/claims/subrogations')}>
            Open subrogations
          </Button>
        }
      />

      <div className="flex-1 p-6 space-y-4 max-w-6xl">
        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-[#DC2626]" /><p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}
        {loading ? (
          <p className="px-2 py-8 text-sm text-[#6B7280]">Loading…</p>
        ) : data && (
          <>
            {/* Headline row */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <Kpi label="Open recoverable" value={`P ${fmt(data.open_recoverable)}`} sub={`${data.open_count} open cases`} />
              <Kpi label="Recovered (year to date)" value={`P ${fmt(data.recovered_ytd)}`} tone="green" sub={`P ${fmt(data.recovered_mtd)} this month`} />
              <Kpi label="Recovery rate" value={`${data.recovery_rate}%`} />
              <Kpi label="Recovered (all time)" value={`P ${fmt(data.recovered_total)}`} tone="green" />
            </div>

            {/* The two piles that move money */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <button onClick={() => router.push('/claims/subrogations')}
                className="text-left bg-white border border-[#FECACA] rounded-lg p-4 hover:shadow-md transition-shadow">
                <div className="flex items-center gap-2 text-[#B91C1C]"><ShieldAlert className="w-4 h-4" />
                  <span className="text-xs font-semibold uppercase tracking-wide">Prescription at risk</span></div>
                <p className="text-2xl font-bold text-[#0B0B3B] mt-2 tabular-nums">P {fmt(data.at_risk.balance)}</p>
                <p className="text-sm text-[#6B7280] mt-0.5">{data.at_risk.count} cases expiring or expired — the 3-year clock</p>
              </button>
              <button onClick={() => router.push('/claims/subrogations')}
                className="text-left bg-white border border-[#FED7AA] rounded-lg p-4 hover:shadow-md transition-shadow">
                <div className="flex items-center gap-2 text-[#C2410C]"><UserX className="w-4 h-4" />
                  <span className="text-xs font-semibold uppercase tracking-wide">Unassigned queue</span></div>
                <p className="text-2xl font-bold text-[#0B0B3B] mt-2 tabular-nums">P {fmt(data.unassigned.balance)}</p>
                <p className="text-sm text-[#6B7280] mt-0.5">{data.unassigned.count} open cases with nobody appointed to collect</p>
              </button>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {/* Ageing */}
              <Card>
                <CardHeader><CardTitle>Ageing (open balance)</CardTitle></CardHeader>
                <CardContent className="p-0">
                  <table className="w-full text-sm">
                    <tbody className="divide-y divide-[#F3F4F6]">
                      {data.ageing.map(a => (
                        <tr key={a.bucket}>
                          <td className="px-4 py-2 text-[#374151]">{a.bucket}</td>
                          <td className="px-4 py-2 text-right text-[#6B7280] tabular-nums">{a.count}</td>
                          <td className="px-4 py-2 text-right tabular-nums font-medium">{fmt(a.balance)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </CardContent>
              </Card>

              {/* Top counterparties — scorecard base */}
              <Card>
                <CardHeader><CardTitle>Top 5 by open balance</CardTitle></CardHeader>
                <CardContent className="p-0">
                  {data.top_counterparties.length === 0 ? (
                    <p className="px-4 py-6 text-sm text-[#6B7280] text-center">No open cases.</p>
                  ) : (
                    <table className="w-full text-sm">
                      <tbody className="divide-y divide-[#F3F4F6]">
                        {data.top_counterparties.map(c => (
                          <tr key={c.name}>
                            <td className="px-4 py-2 text-[#374151]">
                              {c.name === 'Unassigned'
                                ? <span className="text-[#B91C1C] font-medium">Unassigned</span>
                                : c.name}
                            </td>
                            <td className="px-4 py-2 text-right text-[#6B7280] tabular-nums">{c.count}</td>
                            <td className="px-4 py-2 text-right tabular-nums font-medium">{fmt(c.balance)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </CardContent>
              </Card>
            </div>

            <p className="text-xs text-[#9CA3AF] flex items-center gap-1.5">
              <Coins className="w-3.5 h-3.5" /> Figures cover the company you have selected. Click any tile to work the cases.
            </p>
          </>
        )}
      </div>
    </div>
  )
}

function Kpi({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: 'green' }) {
  const color = tone === 'green' ? 'text-[#047857]' : 'text-[#0B0B3B]'
  return (
    <div className="bg-white border border-[#E5E7EB] rounded-lg px-4 py-3">
      <p className="text-xs text-[#6B7280] uppercase tracking-wide">{label}</p>
      <p className={`text-lg font-semibold tabular-nums mt-1 ${color}`}>{value}</p>
      {sub && <p className="text-xs text-[#9CA3AF] mt-0.5">{sub}</p>}
    </div>
  )
}
