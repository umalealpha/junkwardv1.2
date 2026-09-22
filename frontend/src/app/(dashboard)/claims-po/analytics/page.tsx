'use client'

/**
 * /claims-po/analytics — Claims-PO Analytics dashboard.
 *
 * Company-scoped analytics over department=claims purchase orders +
 * ClaimsAssessment turnaround (CFO 2026-07-07). Mirrors the RealPay
 * collections analytics page: recharts + KPI tiles + brand navy/orange.
 *
 * Source: GET /api/v1/reports/claims-po-analytics/ (read-only aggregation).
 */

import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useRouter } from 'next/navigation'
import { getToken } from '@/lib/api'
import { getClaimsPOAnalytics, type ClaimsPOAnalytics } from '@/lib/claimsAnalytics'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  ComposedChart, Bar, Line, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, Legend, PieChart, Pie, Cell, BarChart,
} from 'recharts'
import {
  BarChart3, Info, AlertTriangle, Wrench, MinusCircle, Clock, Send,
} from 'lucide-react'

const NAVY = '#0D1B2A'
const NAVY_2 = '#1D3270'
const ORANGE = '#F47C20'

function money(v: number | null | undefined, dp = 0): string {
  const n = Number(v)
  if (!isFinite(n)) return '—'
  return 'BWP ' + n.toLocaleString(undefined, { minimumFractionDigits: dp, maximumFractionDigits: dp })
}
function compact(v: number): string {
  if (Math.abs(v) >= 1e6) return (v / 1e6).toFixed(1) + 'M'
  if (Math.abs(v) >= 1e3) return (v / 1e3).toFixed(0) + 'k'
  return String(v)
}

export default function ClaimsPOAnalyticsPage() {
  const router = useRouter()
  const [data, setData] = useState<ClaimsPOAnalytics | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      setData(await getClaimsPOAnalytics())
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
  }, [load, router])

  const monthly = useMemo(() => data?.monthly || [], [data])
  const hasData = (data?.totals.po_count ?? 0) > 0

  const splitData = useMemo(() => data ? [
    { name: 'Repairer POs (with excess)', value: data.split.repairer.value, count: data.split.repairer.count },
    { name: 'Parts POs', value: data.split.parts.value, count: data.split.parts.count },
  ].filter(s => s.count > 0) : [], [data])
  const SPLIT_COLORS = [ORANGE, NAVY_2]

  const t = data?.totals

  return (
    <div className="min-h-screen bg-[#F8F9FB]" style={{ fontFamily: '"Book Antiqua", Georgia, serif' }}>
      <TopBar title="Claims PO Analytics" />
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        {/* Header */}
        <div className="flex items-center gap-3 mb-1">
          <div className="w-10 h-10 rounded-lg bg-[#0D1B2A] flex items-center justify-center">
            <BarChart3 className="w-5 h-5 text-[#F47C20]" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-[#0D1B2A]">Claims PO — Analytics</h1>
            <p className="text-sm text-[#6B7280]">Spend · repairers · excess deducted · turnaround · sent status</p>
          </div>
        </div>

        {error && (
          <div className="mt-4 rounded-md bg-[#FEE2E2] text-[#991B1B] px-4 py-3 text-sm flex items-center gap-2">
            <AlertTriangle className="w-4 h-4" /> {error}
          </div>
        )}

        {/* KPI tiles */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mt-5">
          <KPI icon={<BarChart3 className="w-4 h-4" />} label="Claims-PO spend (this month)"
               value={loading ? '…' : money(t?.spend_this_month)}
               sub={loading ? '' : `${money(t?.total_spend)} all time · ${t?.po_count ?? 0} POs`} navy />
          <KPI icon={<MinusCircle className="w-4 h-4" />} label="Excess deducted (total)"
               value={loading ? '…' : money(t?.excess_total)}
               sub={loading ? '' : `avg ${money(t?.excess_avg)} across ${t?.excess_po_count ?? 0} POs`} />
          <KPI icon={<Clock className="w-4 h-4" />} label="Avg turnaround (assessment → PO)"
               value={loading ? '…' : (t?.avg_turnaround_days == null ? '—' : `${t.avg_turnaround_days} days`)}
               sub={loading ? '' : `${t?.turnaround_sample ?? 0} assessment(s) with POs`} />
          <KPI icon={<Send className="w-4 h-4" />} label="POs emailed to supplier"
               value={loading ? '…' : (t?.sent_known ? `${t.sent} / ${(t.sent + t.unsent)}` : 'n/a')}
               sub={loading ? '' : (t?.sent_known ? `${t.unsent} not yet sent` : 'send tracking not live yet')} />
        </div>

        {/* Monthly spend trend */}
        <Card className="mt-5">
          <CardHeader><CardTitle className="text-[#0D1B2A]">Monthly claims-PO spend (last 12 months)</CardTitle></CardHeader>
          <CardContent>
            {loading ? <div className="py-16 text-center text-[#6B7280]">Loading…</div>
              : !hasData ? <div className="py-16 text-center text-[#6B7280]">No claims purchase orders yet.</div>
              : (
                <ResponsiveContainer width="100%" height={320}>
                  <ComposedChart data={monthly} margin={{ top: 10, right: 20, left: 10, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#EEF1F5" />
                    <XAxis dataKey="label" tick={{ fontSize: 12, fill: '#6B7280' }} />
                    <YAxis yAxisId="l" tickFormatter={compact} tick={{ fontSize: 11, fill: '#6B7280' }} />
                    <YAxis yAxisId="r" orientation="right" allowDecimals={false} tick={{ fontSize: 11, fill: '#6B7280' }} />
                    <Tooltip formatter={(v, n) =>
                      n === 'Spend (BWP)' ? money(Number(v), 2) : Number(v).toLocaleString()} />
                    <Legend />
                    <Bar yAxisId="l" dataKey="spend" name="Spend (BWP)" fill={ORANGE} radius={[4, 4, 0, 0]} maxBarSize={64} />
                    <Line yAxisId="r" dataKey="count" name="PO count" stroke={NAVY} strokeWidth={2.5} dot={{ r: 3 }} />
                  </ComposedChart>
                </ResponsiveContainer>
              )}
          </CardContent>
        </Card>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5 mt-5">
          {/* Top suppliers */}
          <Card>
            <CardHeader>
              <CardTitle className="text-[#0D1B2A] text-base flex items-center gap-2">
                <Wrench className="w-4 h-4 text-[#F47C20]" /> Top repairers / suppliers (by total spend)
              </CardTitle>
            </CardHeader>
            <CardContent>
              {loading ? <div className="py-16 text-center text-[#6B7280]">Loading…</div>
                : (data?.top_suppliers.length ?? 0) > 0 ? (
                <ResponsiveContainer width="100%" height={Math.max(220, (data?.top_suppliers.length ?? 0) * 34)}>
                  <BarChart data={data?.top_suppliers} layout="vertical"
                            margin={{ top: 4, right: 24, left: 8, bottom: 4 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#EEF1F5" horizontal={false} />
                    <XAxis type="number" tickFormatter={compact} tick={{ fontSize: 11, fill: '#6B7280' }} />
                    <YAxis type="category" dataKey="supplier" width={150}
                           tick={{ fontSize: 11, fill: '#374151' }} />
                    <Tooltip formatter={(v) => money(Number(v), 2)} />
                    <Bar dataKey="total" name="Spend (BWP)" fill={NAVY_2} radius={[0, 4, 4, 0]} maxBarSize={20} />
                  </BarChart>
                </ResponsiveContainer>
              ) : <div className="py-16 text-center text-[#6B7280]">—</div>}
            </CardContent>
          </Card>

          {/* Repairer vs parts split */}
          <Card>
            <CardHeader><CardTitle className="text-[#0D1B2A] text-base">Repairer vs parts split (by value)</CardTitle></CardHeader>
            <CardContent>
              {loading ? <div className="py-16 text-center text-[#6B7280]">Loading…</div>
                : splitData.length > 0 ? (
                <>
                  <ResponsiveContainer width="100%" height={240}>
                    <PieChart>
                      <Pie data={splitData} dataKey="value" nameKey="name" cx="50%" cy="50%"
                        innerRadius={55} outerRadius={90} paddingAngle={2}
                        label={(p) => (p as { percent?: number }).percent && (p as { percent: number }).percent >= 0.03
                          ? `${((p as { percent: number }).percent * 100).toFixed(0)}%` : ''}
                        labelLine={false}>
                        {splitData.map((s, i) => <Cell key={s.name} fill={SPLIT_COLORS[i % SPLIT_COLORS.length]} />)}
                      </Pie>
                      <Tooltip formatter={(v) => money(Number(v), 2)} />
                      <Legend />
                    </PieChart>
                  </ResponsiveContainer>
                  <div className="flex justify-around text-xs text-[#374151] mt-1">
                    <span>Repairer: <strong>{data?.split.repairer.count ?? 0}</strong> POs · {money(data?.split.repairer.value)}</span>
                    <span>Parts: <strong>{data?.split.parts.count ?? 0}</strong> POs · {money(data?.split.parts.value)}</span>
                  </div>
                </>
              ) : <div className="py-16 text-center text-[#6B7280]">—</div>}
              <p className="text-xs text-[#9CA3AF] mt-2">A claims PO carrying a negative excess line counts as a repairer PO; all others count as parts.</p>
            </CardContent>
          </Card>
        </div>

        {/* Definitions / assumptions */}
        {data?.notes?.length ? (
          <Card className="mt-5 border-[#F47C20]/40">
            <CardHeader>
              <CardTitle className="text-[#0D1B2A] text-base flex items-center gap-2">
                <Info className="w-4 h-4 text-[#F47C20]" /> Definitions &amp; assumptions
              </CardTitle>
            </CardHeader>
            <CardContent>
              <ul className="list-disc pl-5 space-y-1 text-sm text-[#374151]">
                {data.notes.map((n, i) => <li key={i}>{n}</li>)}
              </ul>
            </CardContent>
          </Card>
        ) : null}
      </div>
    </div>
  )
}

function KPI({ icon, label, value, sub, navy }: {
  icon: ReactNode; label: string; value: string; sub?: string; navy?: boolean
}) {
  return (
    <div className={`rounded-lg px-4 py-3 ${navy ? 'bg-[#0D1B2A] text-white' : 'bg-white border border-[#E5E7EB]'}`}>
      <div className={`flex items-center gap-1.5 text-xs font-medium ${navy ? 'text-[#F47C20]' : 'text-[#6B7280]'}`}>
        {icon}{label}
      </div>
      <div className={`text-xl font-bold mt-1 ${navy ? 'text-white' : 'text-[#0D1B2A]'}`}>{value}</div>
      {sub ? <div className={`text-[11px] mt-0.5 ${navy ? 'text-white/60' : 'text-[#9CA3AF]'}`}>{sub}</div> : null}
    </div>
  )
}
