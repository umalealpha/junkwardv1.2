'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import {
  getToken,
  getInvestments,
  type Investment,
} from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { TrendingUp, AlertTriangle, RefreshCw, ArrowUpRight, ArrowDownLeft } from 'lucide-react'
import { useCompany } from '@/contexts/CompanyContext'

function fmtMoney(s: string | null | undefined): string {
  if (!s) return '—'
  const n = Number(s)
  return isFinite(n)
    ? n.toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : s
}

const CLASSIFICATION_BADGE: Record<string, { bg: string; fg: string }> = {
  fvtpl:          { bg: '#FEF3C7', fg: '#92400E' },
  fvoci:          { bg: '#EFF6FF', fg: '#1D4ED8' },
  amortised_cost: { bg: '#ECFDF5', fg: '#047857' },
}

export default function InvestmentsPage() {
  const router = useRouter()
  const { selected: selectedCompany } = useCompany()
  // ADIC-OMNI-QA-001 §8: standard is ISO 4217 ("BWP X"), never single-letter "P".
  // Take the entity's reporting currency from the topbar selection.
  const currency = (selectedCompany?.base_currency || 'BWP') as string
  const [items, setItems] = useState<Investment[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [classFilter, setClassFilter] = useState('')

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const res = await getInvestments({ classification: classFilter || undefined })
      setItems(res.results)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load investments')
    } finally {
      setLoading(false)
    }
  }, [classFilter])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    void load()
  }, [router, load])

  // Portfolio summary
  const totalCost = items.reduce((s, i) => s + Number(i.cost || 0), 0)
  const totalFV = items.reduce((s, i) => s + Number(i.current_fair_value || 0), 0)
  const unrealised = totalFV - totalCost

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Investment Portfolio"
        breadcrumbs={[{ label: 'Investments' }]}
        actions={
          <Button
            variant="outline" size="sm"
            leftIcon={<RefreshCw className="w-3.5 h-3.5" />}
            onClick={load} disabled={loading}
          >
            Refresh
          </Button>
        }
      />

      <div className="flex-1 p-6 space-y-4">
        {/* Portfolio summary */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <Card>
            <CardContent className="p-4">
              <p className="text-[10px] uppercase tracking-wider text-[#6B7280]">Positions</p>
              <p className="text-2xl font-bold text-[#0B0B3B]">{items.length}</p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-4">
              <p className="text-[10px] uppercase tracking-wider text-[#6B7280]">Total cost</p>
              <p className="text-2xl font-bold font-mono tabular-nums text-[#374151]">
                {currency} {fmtMoney(String(totalCost))}
              </p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-4">
              <p className="text-[10px] uppercase tracking-wider text-[#6B7280]">Fair value</p>
              <p className="text-2xl font-bold font-mono tabular-nums text-[#0B0B3B]">
                {currency} {fmtMoney(String(totalFV))}
              </p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-4">
              <p className="text-[10px] uppercase tracking-wider text-[#6B7280]">Unrealised P&L</p>
              <p
                className="text-2xl font-bold font-mono tabular-nums flex items-center gap-1"
                style={{ color: unrealised >= 0 ? '#047857' : '#B91C1C' }}
              >
                {unrealised >= 0
                  ? <ArrowUpRight className="w-5 h-5" />
                  : <ArrowDownLeft className="w-5 h-5" />}
                {currency} {fmtMoney(String(Math.abs(unrealised)))}
              </p>
            </CardContent>
          </Card>
        </div>

        {/* Classification filter */}
        <Card>
          <CardContent className="p-4 flex flex-wrap items-center gap-3">
            <span className="text-xs font-medium text-[#374151]">Classification:</span>
            {[
              { value: '',               label: 'All' },
              { value: 'fvtpl',          label: 'FVTPL' },
              { value: 'fvoci',          label: 'FVOCI' },
              { value: 'amortised_cost', label: 'Amortised Cost' },
            ].map((opt) => (
              <button
                key={opt.value}
                onClick={() => setClassFilter(opt.value)}
                className={`text-xs px-2.5 py-1 rounded border transition-colors ${
                  classFilter === opt.value
                    ? 'bg-[#0B0B3B] text-white border-[#0B0B3B]'
                    : 'bg-white text-[#374151] border-[#D1D5DB] hover:bg-[#F9FAFB]'
                }`}
              >
                {opt.label}
              </button>
            ))}
          </CardContent>
        </Card>

        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-[#DC2626]" />
            <p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}

        <Card>
          <CardContent className="p-0">
            {loading && (
              <p className="px-6 py-8 text-center text-sm text-[#6B7280]">Loading…</p>
            )}
            {!loading && items.length === 0 && (
              <div className="px-6 py-12 text-center text-[#6B7280]">
                <TrendingUp className="w-10 h-10 mx-auto text-[#D1D5DB] mb-3" strokeWidth={1.5} />
                <p className="text-sm">No investments registered.</p>
                <p className="text-xs text-[#9CA3AF] mt-2">
                  Add positions in Django admin under <code>/admin/investments/</code>.
                </p>
              </div>
            )}
            {!loading && items.length > 0 && (
              <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="bg-[#F9FAFB] border-b border-[#E5E7EB]">
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Ref</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Name</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Type</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">IFRS 9</th>
                      <th className="text-right px-4 py-2.5 font-semibold text-[#374151]">Cost</th>
                      <th className="text-right px-4 py-2.5 font-semibold text-[#374151]">Fair Value</th>
                      <th className="text-right px-4 py-2.5 font-semibold text-[#374151]">Unreal. P&L</th>
                      <th className="text-left px-4 py-2.5 font-semibold text-[#374151]">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((inv) => {
                      const c = CLASSIFICATION_BADGE[inv.classification] || CLASSIFICATION_BADGE.fvtpl
                      const pl = Number(inv.unrealised_pl || 0)
                      return (
                        <tr key={inv.id} className="border-b border-[#F3F4F6] hover:bg-[#F9FAFB]">
                          <td className="px-4 py-2.5 font-mono text-xs text-[#0B0B3B]">
                            {inv.investment_number}
                          </td>
                          <td className="px-4 py-2.5 text-[#374151]">
                            <div>{inv.name}</div>
                            <div className="text-xs text-[#9CA3AF]">{inv.issuer}</div>
                          </td>
                          <td className="px-4 py-2.5 text-[#374151] text-xs">
                            {inv.instrument_type_display}
                          </td>
                          <td className="px-4 py-2.5">
                            <span
                              className="inline-flex text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded border"
                              style={{ background: c.bg, color: c.fg, borderColor: `${c.fg}30` }}
                            >
                              {inv.classification.toUpperCase()}
                            </span>
                          </td>
                          <td className="px-4 py-2.5 text-right font-mono tabular-nums text-[#374151]">
                            {fmtMoney(inv.cost)}
                          </td>
                          <td className="px-4 py-2.5 text-right font-mono tabular-nums text-[#111827]">
                            {fmtMoney(inv.current_fair_value)}
                          </td>
                          <td
                            className="px-4 py-2.5 text-right font-mono tabular-nums"
                            style={{ color: pl >= 0 ? '#047857' : '#B91C1C' }}
                          >
                            {pl >= 0 ? '+' : ''}{fmtMoney(inv.unrealised_pl)}
                          </td>
                          <td className="px-4 py-2.5 text-xs text-[#374151]">{inv.status_display}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
