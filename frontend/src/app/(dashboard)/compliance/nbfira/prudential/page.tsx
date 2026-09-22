'use client'

/**
 * /compliance/nbfira/prudential — Prudential Limits Monitor (LIVE).
 *
 * Basis: Insurance Industry Regulations 2019 (S.I. 57 of 2019), Regulation 7 →
 * Schedule 1 (GENERAL insurers). Six headline concentration/liquidity measures.
 * Backend GET /api/v1/compliance/prudential-limits/?company=&as_of= computes
 * each ratio from the Investments + Banking + MA P&L modules. Period picker
 * pins the measurement date — defaults to today, falls back to fiscal-
 * year-end when a fiscal year is picked.
 *
 * Some Schedule 1 single-issuer sub-limits are not yet monitored (needs per-
 * instrument asset classification on Investment rows) — see per-row notes.
 */

import { useEffect, useMemo, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { apiFetch } from '@/lib/api'
import { useCompany } from '@/contexts/CompanyContext'
import { cn, localYmd } from '@/lib/utils'
import { AlertTriangle, RefreshCw } from 'lucide-react'
import { PRUDENTIAL_LIMITS } from '../_lib'
import { Button } from '@/components/ui/button'

interface Measurement {
  amount?: string
  counterparty?: string
  cash_near_cash?: string
  bank_balance?: string
  maturing_12m?: string
  projected_claims_12m?: string
  ratio: number | null
  status: 'ok' | 'warning' | 'breach' | 'unknown'
  note?: string
}

interface PrudentialResponse {
  as_of: string
  company: { id: string; code: string; name: string } | null
  total_portfolio: string
  measurements: Record<string, Measurement>
  warnings: string[]
}

function _isoToday(): string {
  return localYmd(new Date())
}

// Period-end shortcuts CFO uses most.
const PERIOD_OPTIONS: { label: string; as_of: string }[] = [
  { label: 'Today',          as_of: _isoToday() },
  { label: 'FY25 (Jun-2025)', as_of: '2025-06-30' },
  { label: 'FY26 9M (Mar-2026)', as_of: '2026-03-31' },
  { label: 'FY26 H1 (Dec-2025)', as_of: '2025-12-31' },
  { label: 'Q1 FY26 (Sep-2025)', as_of: '2025-09-30' },
]

export default function PrudentialLimitsPage() {
  const { selectedId: companyId } = useCompany()
  const [asOf, setAsOf] = useState<string>(_isoToday())
  const [data, setData] = useState<PrudentialResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const reload = useMemo(() => async () => {
    setLoading(true); setError(null)
    try {
      const q = new URLSearchParams()
      if (companyId) q.set('company', companyId)
      if (asOf)      q.set('as_of', asOf)
      const r = await apiFetch<PrudentialResponse>(
        `/compliance/prudential-limits/?${q.toString()}`,
      )
      setData(r)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load measurements')
    } finally {
      setLoading(false)
    }
  }, [companyId, asOf])

  useEffect(() => { reload() }, [reload])

  const rows = PRUDENTIAL_LIMITS.map((l) => {
    const m = data?.measurements?.[l.id]
    return { ...l, m }
  })

  const statusBadge: Record<string, string> = {
    ok:      'bg-[#ECFDF5] text-[#047857] border-[#A7F3D0]',
    warning: 'bg-[#FFFBEB] text-[#92400E] border-[#FDE68A]',
    breach:  'bg-[#FEF2F2] text-[#B91C1C] border-[#FECACA]',
    unknown: 'bg-[#F3F4F6] text-[#374151] border-[#D1D5DB]',
  }
  const statusLabel: Record<string, string> = {
    ok: 'Within limit', warning: 'Near ceiling',
    breach: 'BREACH', unknown: 'No measurement',
  }

  const fmt = (n: number | null | undefined) =>
    n == null ? '—' : (n * 100).toFixed(1) + '%'
  const fmtMoney = (s?: string) => {
    if (!s) return '—'
    const v = Number(s)
    if (!isFinite(v)) return '—'
    return 'BWP ' + v.toLocaleString(undefined, { maximumFractionDigits: 0 })
  }

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Prudential Limits Monitor"
        subtitle="Insurance Industry Regulations 2019 (S.I. 57 of 2019), Reg 7 → Schedule 1 · general insurers"
        breadcrumbs={[{ label: 'Compliance' }, { label: 'NBFIRA' }, { label: 'Prudential Limits' }]}
        actions={
          <Button variant="secondary" size="sm" onClick={reload} loading={loading}>
            <RefreshCw className={`w-4 h-4 mr-1 ${loading ? 'animate-spin' : ''}`} /> Refresh
          </Button>
        }
      />
      <div className="flex-1 p-6 space-y-4">
        {/* Period selector */}
        <Card>
          <CardContent className="py-3 flex flex-wrap items-center gap-3">
            <span className="text-xs uppercase tracking-wider text-[#6B7280]">
              Measurement date
            </span>
            <input
              type="date" value={asOf}
              onChange={(e) => setAsOf(e.target.value)}
              className="border rounded px-3 py-1 text-sm bg-background"
            />
            {PERIOD_OPTIONS.map((p) => (
              <button
                key={p.as_of}
                onClick={() => setAsOf(p.as_of)}
                className={cn(
                  'px-2.5 py-1 text-xs rounded border transition',
                  asOf === p.as_of
                    ? 'bg-[#F4A623] text-white border-[#F4A623]'
                    : 'bg-white text-[#374151] border-[#D1D5DB] hover:bg-[#F9FAFB]',
                )}
              >
                {p.label}
              </button>
            ))}
            {data && (
              <span className="ml-auto text-xs text-[#6B7280]">
                Total portfolio: <strong className="font-mono-nums">
                  {fmtMoney(data.total_portfolio)}
                </strong>
              </span>
            )}
          </CardContent>
        </Card>

        {error && (
          <Card className="border-red-300 bg-red-50/40">
            <CardContent className="py-3 text-sm text-red-700">{error}</CardContent>
          </Card>
        )}

        <Card>
          <CardContent className="p-0">
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase text-[#6B7280] border-b border-[#E5E7EB]">
                <tr>
                  <th className="py-3 px-4">Limit</th>
                  <th className="py-3 px-4">Statutory ceiling</th>
                  <th className="py-3 px-4 text-right">Current</th>
                  <th className="py-3 px-4 text-right">Amount</th>
                  <th className="py-3 px-4">Status</th>
                  <th className="py-3 px-4">Source</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const status = r.m?.status ?? 'unknown'
                  const amount = r.id === 'liquidity'
                    ? r.m?.cash_near_cash
                    : r.m?.amount
                  return (
                    <tr key={r.id} className="border-b border-[#F3F4F6]">
                      <td className="py-3 px-4 font-semibold text-[#0D1B2A]">
                        {r.label}
                        {r.m?.counterparty && (
                          <span className="ml-2 text-xs font-normal text-[#9CA3AF]">
                            (top: {r.m.counterparty})
                          </span>
                        )}
                      </td>
                      <td className="py-3 px-4 text-[#374151]">
                        {r.rule}
                        {r.note && (
                          <div className="mt-1 text-[10px] leading-snug text-[#9CA3AF] max-w-[22rem]">
                            {r.note}
                          </div>
                        )}
                      </td>
                      <td className="py-3 px-4 text-right font-mono-nums">
                        {fmt(r.m?.ratio)}
                      </td>
                      <td className="py-3 px-4 text-right font-mono-nums text-xs text-[#6B7280]">
                        {fmtMoney(amount)}
                      </td>
                      <td className="py-3 px-4">
                        <span className={cn('px-2 py-0.5 rounded-full text-xs border', statusBadge[status])}>
                          {statusLabel[status]}
                        </span>
                        {r.m?.note && (
                          <div className="mt-1 text-[10px] text-[#9CA3AF]">{r.m.note}</div>
                        )}
                      </td>
                      <td className="py-3 px-4 text-xs text-[#9CA3AF]">{r.source}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </CardContent>
        </Card>

        {/* Liquidity detail */}
        {data?.measurements?.liquidity && (
          <Card>
            <CardContent className="py-3 text-xs text-[#6B7280] grid grid-cols-1 sm:grid-cols-4 gap-3">
              <div>Bank balance: <strong className="font-mono-nums text-[#0D1B2A]">
                {fmtMoney(data.measurements.liquidity.bank_balance)}
              </strong></div>
              <div>Investments ≤12 mo: <strong className="font-mono-nums text-[#0D1B2A]">
                {fmtMoney(data.measurements.liquidity.maturing_12m)}
              </strong></div>
              <div>Cash + near-cash: <strong className="font-mono-nums text-[#0D1B2A]">
                {fmtMoney(data.measurements.liquidity.cash_near_cash)}
              </strong></div>
              <div>Projected 12-mo claims: <strong className="font-mono-nums text-[#0D1B2A]">
                {fmtMoney(data.measurements.liquidity.projected_claims_12m)}
              </strong></div>
            </CardContent>
          </Card>
        )}

        {data?.warnings?.length ? (
          <div className="text-xs text-[#92400E] bg-[#FFFBEB] border border-[#FDE68A] rounded p-2 space-y-1">
            {data.warnings.map((w, i) => (
              <div key={i} className="flex items-start gap-1">
                <AlertTriangle className="w-3.5 h-3.5 mt-0.5" />{w}
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  )
}
