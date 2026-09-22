'use client'

// Graphite V2 premium-debtors Age Analysis — omni reads the Graphite read
// replica (read-only) and displays it in the SAME look as AR Aging.

import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { getGraphiteAgeAnalysis, getToken } from '@/lib/api'
import type { GraphiteAgeReport } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { LoadingTable } from '@/components/ui/loading'
import {
  formatAmount,
  getBucketBgColor,
  getBucketColor,
  exportToCsv,
  cn,
} from '@/lib/utils'
import { useNumberFormat } from '@/contexts/NumberFormatContext'
import { Download, AlertCircle, Clock } from 'lucide-react'

const BUCKETS = ['0-30', '31-60', '61-90', '120+']
const BUCKET_LABELS: Record<string, string> = {
  '0-30': '0–30 days',
  '31-60': '31–60 days',
  '61-90': '61–90 days',
  '120+': '120+ days',
}

export default function GraphiteAgeAnalysisPage() {
  const router = useRouter()
  const { mode } = useNumberFormat()
  const currency = 'BWP'
  const fmt = (amount: string | number) => formatAmount(amount, currency, mode)

  const [search, setSearch] = useState('')
  const [report, setReport] = useState<GraphiteAgeReport | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [sortBy, setSortBy] = useState('balance_desc')

  // Sort the debtors on the client (CFO 2026-08-30): biggest owing first,
  // oldest debt (90+) first, who has never paid, or by client name.
  const num = (v: string | number | null | undefined) => {
    const n = typeof v === 'number' ? v : parseFloat(String(v ?? '0'))
    return isNaN(n) ? 0 : n
  }
  const sortedRows = useMemo(() => {
    const rows = report ? [...report.rows] : []
    switch (sortBy) {
      case 'oldest_desc':
        return rows.sort((a, b) => (num(b.days_90) + num(b.days_120_plus)) - (num(a.days_90) + num(a.days_120_plus)))
      case 'never_paid':
        // Never paid = nothing received but a balance is owing; biggest first.
        return rows
          .filter((r) => num(r.payment_total) === 0 && num(r.balance_outstanding) > 0)
          .sort((a, b) => num(b.balance_outstanding) - num(a.balance_outstanding))
      case 'client_asc':
        return rows.sort((a, b) => (a.client_name || '').localeCompare(b.client_name || ''))
      case 'balance_desc':
      default:
        return rows.sort((a, b) => num(b.balance_outstanding) - num(a.balance_outstanding))
    }
  }, [report, sortBy])

  useEffect(() => {
    if (!getToken()) {
      router.replace('/login')
      return
    }
    handleGenerate()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleGenerate = async () => {
    setLoading(true)
    setError(null)
    try {
      setReport(await getGraphiteAgeAnalysis({ search: search.trim() }))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load age analysis')
    } finally {
      setLoading(false)
    }
  }

  const handleExport = () => {
    if (!report) return
    exportToCsv(
      report.rows.map((r) => ({
        Policy: r.policy_number,
        Client: r.client_name,
        Product: r.product_name,
        Status: r.policy_status,
        Invoice: r.invoice_total,
        Paid: r.payment_total,
        Balance: r.balance_outstanding,
        '31-60': r.days_30,
        '61-90': r.days_60,
        '91-120': r.days_90,
        '120+': r.days_120_plus,
      })),
      'graphite_age_analysis',
    )
  }

  const bucketVal = (b: string) => report?.summary.buckets[b] ?? 0

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Graphite Age Analysis"
        breadcrumbs={[{ label: 'Debtors', href: '/debtors' }, { label: 'Graphite Age Analysis' }]}
        actions={
          report && (
            <Button
              variant="secondary"
              size="sm"
              leftIcon={<Download className="w-3.5 h-3.5" />}
              onClick={handleExport}
            >
              Export CSV
            </Button>
          )
        }
      />

      <div className="flex-1 p-6 space-y-6">
        {/* Controls */}
        <Card>
          <CardContent className="py-4">
            <div className="flex flex-wrap items-end gap-4">
              <div>
                <label className="block text-xs text-[#374151] font-medium mb-1">
                  Search policy / client
                </label>
                <input
                  type="text"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleGenerate()}
                  placeholder="e.g. COMG… or client name"
                  className="bg-white border border-[#D1D5DB] rounded-lg px-3 py-2 text-sm text-[#111827] focus:outline-none focus:border-[#F07F00] focus:ring-[3px] focus:ring-[rgba(240,127,0,0.1)] transition-all w-64"
                />
              </div>
              <Button variant="primary" size="md" onClick={handleGenerate} loading={loading}>
                Generate
              </Button>
              {report && (
                <div>
                  <label className="block text-xs text-[#374151] font-medium mb-1">Sort by</label>
                  <select
                    value={sortBy}
                    onChange={(e) => setSortBy(e.target.value)}
                    className="bg-white border border-[#D1D5DB] rounded-lg px-3 py-2 text-sm text-[#111827] focus:outline-none focus:border-[#F07F00] focus:ring-[3px] focus:ring-[rgba(240,127,0,0.1)] transition-all"
                  >
                    <option value="balance_desc">Balance — high to low</option>
                    <option value="oldest_desc">Oldest debt (90+ days) first</option>
                    <option value="never_paid">Never paid</option>
                    <option value="client_asc">Client name (A–Z)</option>
                  </select>
                </div>
              )}
              {report && (
                <div className="flex items-center gap-2 px-3 py-2 bg-[#F9FAFB] rounded-lg border border-[#E5E7EB]">
                  <Clock className="w-4 h-4 text-[#9CA3AF]" />
                  <span className="text-sm text-[#374151]">
                    Total Outstanding:{' '}
                    <span className="font-bold text-[#CC6C00] font-mono-nums">
                      {fmt(report.summary.total_balance)}
                    </span>
                    <span className="text-[#9CA3AF] ml-2">({report.summary.policy_count} policies)</span>
                  </span>
                </div>
              )}
            </div>
            <p className="text-[11px] text-[#9CA3AF] mt-2">{report?.source}</p>
          </CardContent>
        </Card>

        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-xl p-3 flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-[#DC2626]" />
            <p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}

        {/* Bucket Summary */}
        {report && !loading && (
          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-4 gap-3">
            {BUCKETS.map((bucket) => (
              <div key={bucket} className={cn('rounded-xl p-3 text-center border', getBucketBgColor(bucket))}>
                <p className="text-xs text-[#6B7280] uppercase tracking-wider">{BUCKET_LABELS[bucket]}</p>
                <p className={cn('text-base font-bold font-mono-nums mt-1', getBucketColor(bucket))}>
                  {fmt(bucketVal(bucket))}
                </p>
              </div>
            ))}
          </div>
        )}

        {/* Detail Table */}
        <Card>
          <CardContent className="p-0">
            {loading ? (
              <LoadingTable rows={10} cols={8} />
            ) : !report ? (
              <div className="py-12 text-center">
                <Clock className="w-8 h-8 text-[#D1D5DB] mx-auto mb-2" />
                <p className="text-[#9CA3AF] text-sm">Click Generate to load age analysis</p>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="bg-[#F3F4F6] border-b-2 border-[#E5E7EB]">
                    <tr>
                      <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Policy</th>
                      <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Client</th>
                      <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider hidden md:table-cell">Product</th>
                      <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider hidden lg:table-cell">0–30</th>
                      <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider hidden lg:table-cell">31–60</th>
                      <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider hidden lg:table-cell">61–90</th>
                      <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider">120+</th>
                      <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider">Balance</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#E5E7EB] bg-white">
                    {sortedRows.length === 0 ? (
                      <tr>
                        <td colSpan={8} className="px-4 py-8 text-center text-[#9CA3AF] text-sm">
                          {sortBy === 'never_paid' ? 'No never-paid debtors found' : 'No outstanding balances found'}
                        </td>
                      </tr>
                    ) : (
                      sortedRows.map((r, idx) => (
                        <tr key={idx} className="table-row-alt hover:bg-[#FFF7ED] transition-colors">
                          <td className="px-4 py-2.5 font-mono text-xs text-[#CC6C00]">{r.policy_number}</td>
                          <td className="px-4 py-2.5 text-[#111827] font-medium max-w-[160px] truncate" title={r.client_name}>{r.client_name}</td>
                          <td className="px-4 py-2.5 text-[#6B7280] text-xs hidden md:table-cell">{r.product_name}</td>
                          <td className="px-4 py-2.5 text-right font-mono-nums text-[#6B7280] text-xs hidden lg:table-cell">{r.days_30 ? fmt(r.days_30) : '—'}</td>
                          <td className="px-4 py-2.5 text-right font-mono-nums text-xs hidden lg:table-cell">{r.days_60 ? fmt(r.days_60) : '—'}</td>
                          <td className="px-4 py-2.5 text-right font-mono-nums text-xs hidden lg:table-cell">{r.days_90 ? fmt(r.days_90) : '—'}</td>
                          <td className="px-4 py-2.5 text-right font-mono-nums text-xs">
                            <span className="text-[#DC2626]">{r.days_120_plus ? fmt(r.days_120_plus) : '—'}</span>
                          </td>
                          <td className="px-4 py-2.5 text-right font-mono-nums font-semibold text-[#CC6C00]">{fmt(r.balance_outstanding)}</td>
                        </tr>
                      ))
                    )}
                  </tbody>
                  {report.rows.length > 0 && (
                    <tfoot className="bg-[#F9FAFB] border-t border-[#E5E7EB]">
                      <tr>
                        <td colSpan={3} className="px-4 py-3 text-xs font-bold text-[#6B7280] uppercase">Total Outstanding</td>
                        <td className="px-4 py-3 text-right font-bold font-mono-nums hidden lg:table-cell">{fmt(bucketVal('0-30'))}</td>
                        <td className="px-4 py-3 text-right font-bold font-mono-nums hidden lg:table-cell">{fmt(bucketVal('31-60'))}</td>
                        <td className="px-4 py-3 text-right font-bold font-mono-nums hidden lg:table-cell">{fmt(bucketVal('61-90'))}</td>
                        <td className="px-4 py-3 text-right font-bold font-mono-nums">{fmt(bucketVal('120+'))}</td>
                        <td className="px-4 py-3 text-right font-bold font-mono-nums text-[#CC6C00]">{fmt(report.summary.total_balance)}</td>
                      </tr>
                    </tfoot>
                  )}
                </table>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
