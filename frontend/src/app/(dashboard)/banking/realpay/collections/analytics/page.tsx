'use client'

/**
 * /banking/realpay/collections/analytics — RealPay Collections Analytics.
 *
 * Monthly collections trend, paying-client counts, success rate, status mix,
 * by-merchant / by-bank / by-grouping, and an FY rollup. Source = the RealPay
 * Transaction Report rows pulled from the portal (CFO directive 2026-06-05).
 *
 * Coverage = months loaded into Omni so far (currently Feb–Jun 2026). Earlier
 * months ARE retained in the RealPay portal Archive (Archive → Transactions
 * Report (Archived)) and can be pulled in to extend this. Charges are not in
 * the transaction report.
 */

import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useRouter } from 'next/navigation'
import { getToken, getRealPayCollectionsAnalytics } from '@/lib/api'
import type { RealPayAnalytics } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  ComposedChart, Bar, Line, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, Legend, PieChart, Pie, Cell, BarChart,
} from 'recharts'
import { ArrowDownLeft, Info, AlertTriangle, TrendingUp, Users, CheckCircle2, Layers } from 'lucide-react'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'
const STATUS_COLORS: Record<string, string> = {
  SUCCESSFUL: '#10B981', FAILED: '#EF4444', ERROR: '#B91C1C',
  PROCESSING: '#3B82F6', CANCELLED: '#9CA3AF', RETRY: '#F59E0B',
  DISPUTED: '#7C3AED', HOLD: '#6B7280',
}
const GROUP_COLORS: Record<string, string> = {
  INSTANT: ORANGE, CORPORATE: NAVY, PERSONAL: '#2E6FB7', OTHER: '#9CA3AF',
}

function money(s: string | number | null | undefined, dp = 0): string {
  const v = Number(s)
  if (!isFinite(v)) return '—'
  return 'BWP ' + v.toLocaleString(undefined, { minimumFractionDigits: dp, maximumFractionDigits: dp })
}
function compact(v: number): string {
  if (v >= 1e6) return (v / 1e6).toFixed(1) + 'M'
  if (v >= 1e3) return (v / 1e3).toFixed(0) + 'k'
  return String(v)
}

export default function RealPayAnalyticsPage() {
  const router = useRouter()
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [data, setData] = useState<RealPayAnalytics | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      setData(await getRealPayCollectionsAnalytics({ start, end }))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load')
    } finally {
      setLoading(false)
    }
  }, [start, end])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
  }, [load, router])

  const chartData = useMemo(() => (data?.monthly || []).map(m => ({
    label: m.partial ? `${m.label} *` : m.label,
    collected: Number(m.collected),
    paying_clients: m.paying_clients,
    success_rate: m.success_rate,
    partial: m.partial,
  })), [data])
  const hasPartial = useMemo(() => (data?.monthly || []).some(m => m.partial), [data])

  const statusData = useMemo(() => Object.entries(data?.by_status || {})
    .map(([name, value]) => ({ name, value })), [data])

  return (
    <div className="min-h-screen bg-[#F8F9FB]" style={{ fontFamily: '"Book Antiqua", Georgia, serif' }}>
      <TopBar title="Collections Analytics" />
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        {/* Header */}
        <div className="flex items-center gap-3 mb-1">
          <div className="w-10 h-10 rounded-lg bg-[#0D1B2A] flex items-center justify-center">
            <TrendingUp className="w-5 h-5 text-[#F4A623]" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-[#0D1B2A]">RealPay Collections Analytics</h1>
            <p className="text-sm text-[#6B7280]">Monthly collections · paying clients · success rate · breakdowns</p>
          </div>
        </div>

        {/* Coverage banner */}
        {data && (
          <div className="mt-4 rounded-md bg-[#EFF6FF] text-[#1E40AF] px-4 py-3 text-sm flex items-start gap-2">
            <Info className="w-4 h-4 mt-0.5 shrink-0" />
            <span>
              Coverage: <strong>{data.coverage.min} → {data.coverage.max}</strong> ({data.coverage.months.length} months). {data.coverage.note}
            </span>
          </div>
        )}
        {error && (
          <div className="mt-4 rounded-md bg-[#FEE2E2] text-[#991B1B] px-4 py-3 text-sm flex items-center gap-2">
            <AlertTriangle className="w-4 h-4" /> {error}
          </div>
        )}

        {/* KPI tiles */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mt-5">
          <KPI icon={<ArrowDownLeft className="w-4 h-4" />} label="Total Collected (period)" value={loading ? '…' : money(data?.totals.collected)} navy />
          <KPI icon={<Users className="w-4 h-4" />} label="Distinct paying clients (period)" value={loading ? '…' : (data?.totals.paying_clients ?? 0).toLocaleString()} />
          <KPI icon={<CheckCircle2 className="w-4 h-4" />} label="Success rate (settled)" value={loading ? '…' : `${data?.totals.success_rate ?? 0}%`} />
          <KPI icon={<Layers className="w-4 h-4" />} label="Debit attempts" value={loading ? '…' : (data?.totals.attempts ?? 0).toLocaleString()} />
        </div>

        {/* Monthly trend — collected bars + paying-clients line */}
        <Card className="mt-5">
          <CardHeader><CardTitle className="text-[#0D1B2A]">Monthly collections &amp; paying clients</CardTitle></CardHeader>
          <CardContent>
            {loading ? <div className="py-16 text-center text-[#6B7280]">Loading…</div>
              : !data?.data_present ? <div className="py-16 text-center text-[#6B7280]">No transaction data loaded.</div>
              : (
                <ResponsiveContainer width="100%" height={340}>
                  <ComposedChart data={chartData} margin={{ top: 10, right: 20, left: 10, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#EEF1F5" />
                    <XAxis dataKey="label" tick={{ fontSize: 12, fill: '#6B7280' }} />
                    <YAxis yAxisId="l" tickFormatter={compact} tick={{ fontSize: 11, fill: '#6B7280' }} />
                    <YAxis yAxisId="r" orientation="right" tickFormatter={compact} tick={{ fontSize: 11, fill: '#6B7280' }} />
                    <Tooltip formatter={(v: any, n: any) => n === 'Collected (BWP)' ? money(v, 2) : Number(v).toLocaleString()} />
                    <Legend />
                    <Bar yAxisId="l" dataKey="collected" name="Collected (BWP)" fill={ORANGE} radius={[4, 4, 0, 0]} maxBarSize={64} />
                    <Line yAxisId="r" dataKey="paying_clients" name="Paying clients" stroke={NAVY} strokeWidth={2.5} dot={{ r: 3 }} />
                  </ComposedChart>
                </ResponsiveContainer>
              )}
            {hasPartial && (
              <p className="text-xs text-[#92400E] mt-2">* latest month is partial (incomplete debit cycle) — not comparable to full months.</p>
            )}
          </CardContent>
        </Card>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5 mt-5">
          {/* Success rate per month */}
          <Card>
            <CardHeader><CardTitle className="text-[#0D1B2A] text-base">Success rate by month</CardTitle></CardHeader>
            <CardContent>
              {loading ? <div className="py-16 text-center text-[#6B7280]">Loading…</div>
                : data?.data_present ? (
                <ResponsiveContainer width="100%" height={240}>
                  <BarChart data={chartData}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#EEF1F5" />
                    <XAxis dataKey="label" tick={{ fontSize: 11, fill: '#6B7280' }} />
                    <YAxis domain={[0, 100]} tickFormatter={(v) => v + '%'} tick={{ fontSize: 11, fill: '#6B7280' }} />
                    <Tooltip formatter={(v: any) => `${v}%`} />
                    <Bar dataKey="success_rate" name="Success %" fill={NAVY} radius={[4, 4, 0, 0]} maxBarSize={48} />
                  </BarChart>
                </ResponsiveContainer>
              ) : <div className="py-16 text-center text-[#6B7280]">—</div>}
              <p className="text-xs text-[#9CA3AF] mt-1">Settled basis: SUCCESSFUL ÷ (SUCCESSFUL+FAILED+ERROR); excludes in-flight/cancelled.</p>
            </CardContent>
          </Card>

          {/* Status mix donut */}
          <Card>
            <CardHeader><CardTitle className="text-[#0D1B2A] text-base">Debit outcome mix</CardTitle></CardHeader>
            <CardContent>
              {loading ? <div className="py-16 text-center text-[#6B7280]">Loading…</div>
                : data?.data_present ? (
                <ResponsiveContainer width="100%" height={240}>
                  <PieChart>
                    <Pie data={statusData} dataKey="value" nameKey="name" cx="50%" cy="50%"
                      innerRadius={55} outerRadius={90} paddingAngle={2}
                      label={(p: any) => p.percent >= 0.03 ? `${(p.percent * 100).toFixed(0)}%` : ''}
                      labelLine={false}>
                      {statusData.map((s) => <Cell key={s.name} fill={STATUS_COLORS[s.name] || '#9CA3AF'} />)}
                    </Pie>
                    <Tooltip formatter={(v: any) => Number(v).toLocaleString()} />
                    <Legend />
                  </PieChart>
                </ResponsiveContainer>
              ) : <div className="py-16 text-center text-[#6B7280]">—</div>}
            </CardContent>
          </Card>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5 mt-5">
          {/* By merchant */}
          <Card>
            <CardHeader><CardTitle className="text-[#0D1B2A] text-base">Collections by entity</CardTitle></CardHeader>
            <CardContent>
              <table className="w-full text-sm">
                <thead><tr className="text-left text-[#6B7280] border-b border-[#E5E7EB]">
                  <th className="py-2 pr-3 font-medium">Entity</th>
                  <th className="py-2 pr-3 font-medium text-right">Collected</th>
                  <th className="py-2 pr-3 font-medium text-right">Paying</th>
                  <th className="py-2 pr-3 font-medium text-right">Avg / payer</th>
                </tr></thead>
                <tbody>
                  {(data?.by_merchant || []).map((m) => (
                    <tr key={m.merchant} className="border-b border-[#F3F4F6]">
                      <td className="py-2 pr-3">{m.merchant}</td>
                      <td className="py-2 pr-3 text-right tabular-nums">{money(m.collected, 2)}</td>
                      <td className="py-2 pr-3 text-right tabular-nums">{m.paying_clients.toLocaleString()}</td>
                      <td className="py-2 pr-3 text-right tabular-nums">{m.paying_clients ? money(Number(m.collected) / m.paying_clients, 0) : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="text-xs text-[#6B7280] mt-1">Paying = distinct clients within each entity; clients billed under more than one entity are counted in each, so the column does not sum to the period total.</p>
              {/* Grouping bars */}
              <div className="mt-4 space-y-2">
                {(data?.by_grouping || []).map((g) => {
                  const total = (data?.by_grouping || []).reduce((a, x) => a + Number(x.collected), 0) || 1
                  const pct = Number(g.collected) / total * 100
                  return (
                    <div key={g.key}>
                      <div className="flex justify-between text-xs text-[#374151]">
                        <span>{g.label}</span><span className="tabular-nums">{money(g.collected)} · {pct.toFixed(1)}%</span>
                      </div>
                      <div className="h-2 rounded-full bg-[#F3F4F6] mt-0.5">
                        <div className="h-2 rounded-full" style={{ width: `${pct}%`, background: GROUP_COLORS[g.key] || '#9CA3AF' }} />
                      </div>
                    </div>
                  )
                })}
              </div>
            </CardContent>
          </Card>

          {/* By bank */}
          <Card>
            <CardHeader><CardTitle className="text-[#0D1B2A] text-base">Collections by client bank (top 12)</CardTitle></CardHeader>
            <CardContent>
              <table className="w-full text-sm">
                <thead><tr className="text-left text-[#6B7280] border-b border-[#E5E7EB]">
                  <th className="py-2 pr-3 font-medium">Bank</th>
                  <th className="py-2 pr-3 font-medium text-right">Collected</th>
                  <th className="py-2 pr-3 font-medium text-right">Attempts</th>
                </tr></thead>
                <tbody>
                  {(data?.by_bank || []).map((b) => (
                    <tr key={b.bank} className="border-b border-[#F3F4F6]">
                      <td className="py-2 pr-3">{b.bank}</td>
                      <td className="py-2 pr-3 text-right tabular-nums">{money(b.collected, 2)}</td>
                      <td className="py-2 pr-3 text-right tabular-nums">{b.attempts.toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </CardContent>
          </Card>
        </div>

        {/* FY summary + charges note */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5 mt-5">
          <Card>
            <CardHeader><CardTitle className="text-[#0D1B2A] text-base">Fiscal-year rollup (Jul–Jun)</CardTitle></CardHeader>
            <CardContent>
              {(data?.fy_summary || []).map((f) => (
                <div key={f.fy} className="flex items-center justify-between py-2 border-b border-[#F3F4F6]">
                  <span className="font-medium text-[#0D1B2A]">{f.fy}</span>
                  <span className="text-sm text-[#374151] tabular-nums">{money(f.collected)} · {f.months} mo · {f.attempts.toLocaleString()} attempts</span>
                </div>
              ))}
              <p className="text-xs text-[#6B7280] mt-2">
                FY25 has no RealPay data in the portal retention window.
                {data?.coverage.partial_latest_month && ` Includes a partial latest month (${data.coverage.partial_latest_month}).`}
              </p>
            </CardContent>
          </Card>
          {data && !data.charges.available && (
            <div className="rounded-md bg-[#FEF3C7] text-[#92400E] px-4 py-3 text-sm flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
              <span><strong>Charges not shown.</strong> {data.charges.note}</span>
            </div>
          )}
        </div>

        {/* Definitions / assumptions */}
        {data?.notes?.length ? (
          <Card className="mt-5 border-[#F4A623]/40">
            <CardHeader>
              <CardTitle className="text-[#0D1B2A] text-base flex items-center gap-2">
                <Info className="w-4 h-4 text-[#F4A623]" /> Definitions &amp; assumptions
              </CardTitle>
            </CardHeader>
            <CardContent>
              <ul className="list-disc pl-5 space-y-1 text-sm text-[#374151]">
                {data.notes.map((n, i) => <li key={i}>{n}</li>)}
              </ul>
              {data.undated_rows > 0 && (
                <p className="text-xs text-[#92400E] mt-2">{data.undated_rows.toLocaleString()} row(s) have no date and are excluded from the monthly/FY views.</p>
              )}
            </CardContent>
          </Card>
        ) : null}
      </div>
    </div>
  )
}

function KPI({ icon, label, value, navy }: { icon: ReactNode; label: string; value: string; navy?: boolean }) {
  return (
    <div className={`rounded-lg px-4 py-3 ${navy ? 'bg-[#0D1B2A] text-white' : 'bg-white border border-[#E5E7EB]'}`}>
      <div className={`flex items-center gap-1.5 text-xs font-medium ${navy ? 'text-[#F4A623]' : 'text-[#6B7280]'}`}>
        {icon}{label}
      </div>
      <div className={`text-xl font-bold mt-1 ${navy ? 'text-white' : 'text-[#0D1B2A]'}`}>{value}</div>
    </div>
  )
}
