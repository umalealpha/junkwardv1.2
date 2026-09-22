'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { downloadReportXlsx, getApAging, getToken } from '@/lib/api'
import type { AgingReport, AgingCustomer } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { LoadingTable } from '@/components/ui/loading'
import {
  formatAmount,
  formatDate,
  parseAmount,
  getBucketBgColor,
  getBucketColor,
  exportToCsv,
  today,
  cn,
} from '@/lib/utils'
import { useNumberFormat } from '@/contexts/NumberFormatContext'
import { useCompany } from '@/contexts/CompanyContext'
import { Download, AlertCircle, Clock } from 'lucide-react'

// ─── Aging bucket summary ─────────────────────────────────────────────────────

const BUCKETS = ['current', '31-60', '61-90', '91-120', 'over_120']
const BUCKET_LABELS: Record<string, string> = {
  current: 'Current',
  '31-60': '31–60 days',
  '61-90': '61–90 days',
  '91-120': '91–120 days',
  over_120: '120+ days',
}

function getBucketTotal(customers: AgingCustomer[], bucket: string): number {
  return customers.reduce(
    (sum, c) =>
      sum +
      c.invoices
        .filter((inv) => inv.age_bucket === bucket)
        .reduce((s, inv) => s + parseAmount(inv.balance_due), 0),
    0
  )
}

// ─── AP Aging Report ──────────────────────────────────────────────────────────

export default function ApAgingPage() {
  const router = useRouter()
  const { mode } = useNumberFormat()
  const { selectedId: companyId, selected: selectedCompany } = useCompany()
  // CFO directive 2026-05-21: entity functional currency
  const currency = (selectedCompany?.base_currency || 'BWP') as string
  const fmt = (amount: string | number, c: string = currency) => formatAmount(amount, c, mode)
  const [asOf, setAsOf] = useState(today())
  const [report, setReport] = useState<AgingReport | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const token = getToken()
    if (!token) {
      router.replace('/login')
      return
    }
    handleGenerate()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [companyId])

  const handleGenerate = async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await getApAging(asOf, companyId)
      setReport(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to generate AP aging report')
    } finally {
      setLoading(false)
    }
  }

  const handleExport = () => {
    if (!report) return
    const rows: Record<string, any>[] = []
    report.customers.forEach((vendor) => {
      vendor.invoices.forEach((inv) => {
        rows.push({
          Vendor: vendor.contact_name,
          'Bill #': inv.invoice_number,
          'Due Date': inv.due_date,
          Total: inv.total_amount,
          Paid: inv.amount_paid,
          'Balance Due': inv.balance_due,
          Bucket: inv.age_bucket,
          'Days Past Due': inv.days_past_due,
        })
      })
    })
    exportToCsv(rows, `ap_aging_${asOf}`)
  }

  const allInvoices = report
    ? report.customers.flatMap((c) =>
        c.invoices.map((inv) => ({ ...inv, contact_name: c.contact_name }))
      )
    : []

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="AP Aging Report"
        breadcrumbs={[{ label: 'Reports', href: '/reports' }, { label: 'AP Aging' }]}
        actions={
          report && (
            <div className="flex items-center gap-2">
              <Button
                variant="secondary"
                size="sm"
                leftIcon={<Download className="w-3.5 h-3.5" />}
                onClick={handleExport}
              >
                Export CSV
              </Button>
              <Button
                variant="secondary"
                size="sm"
                leftIcon={<Download className="w-3.5 h-3.5" />}
                onClick={() => downloadReportXlsx('ap_aging', {
                  as_of: asOf, company: companyId ?? undefined,
                })}
              >
                Export Excel
              </Button>
            </div>
          )
        }
      />

      <div className="flex-1 p-6 space-y-6">
        {/* Controls */}
        <Card>
          <CardContent className="py-4">
            <div className="flex flex-wrap items-end gap-4">
              <div>
                <label className="block text-xs text-[#374151] font-medium mb-1">As of Date</label>
                <input
                  type="date"
                  value={asOf}
                  onChange={(e) => setAsOf(e.target.value)}
                  className="bg-white border border-[#D1D5DB] rounded-lg px-3 py-2 text-sm text-[#111827] focus:outline-none focus:border-[#F07F00] focus:ring-[3px] focus:ring-[rgba(240,127,0,0.1)] transition-all"
                />
              </div>
              <Button variant="primary" size="md" onClick={handleGenerate} loading={loading}>
                Generate
              </Button>
              {report && (
                <div className="flex items-center gap-2 px-3 py-2 bg-[#F9FAFB] rounded-lg border border-[#E5E7EB]">
                  <Clock className="w-4 h-4 text-[#9CA3AF]" />
                  <span className="text-sm text-[#374151]">
                    Total Outstanding:{' '}
                    <span className="font-bold text-[#DC2626] font-mono-nums">
                      {fmt(report.totals.total_outstanding)}
                    </span>
                  </span>
                </div>
              )}
            </div>
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
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
            {BUCKETS.map((bucket) => {
              const total = getBucketTotal(report.customers, bucket)
              const colorClass = getBucketColor(bucket)
              const bgClass = getBucketBgColor(bucket)
              return (
                <div
                  key={bucket}
                  className={cn('rounded-xl p-3 text-center border', bgClass)}
                >
                  <p className="text-xs text-[#6B7280] uppercase tracking-wider">
                    {BUCKET_LABELS[bucket]}
                  </p>
                  <p className={cn('text-base font-bold font-mono-nums mt-1', colorClass)}>
                    {fmt(total)}
                  </p>
                </div>
              )
            })}
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
                <p className="text-[#9CA3AF] text-sm">Select a date and click Generate</p>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="bg-[#F3F4F6] border-b-2 border-[#E5E7EB]">
                    <tr>
                      <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Vendor</th>
                      <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Bill #</th>
                      <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider hidden md:table-cell">Due Date</th>
                      <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider hidden lg:table-cell">Total</th>
                      <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider hidden lg:table-cell">Paid</th>
                      <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider">Balance</th>
                      <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Bucket</th>
                      <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider hidden md:table-cell">Days Overdue</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#E5E7EB] bg-white">
                    {allInvoices.length === 0 ? (
                      <tr>
                        <td colSpan={8} className="px-4 py-8 text-center text-[#9CA3AF] text-sm">
                          No outstanding payables as of {asOf}
                        </td>
                      </tr>
                    ) : (
                      allInvoices.map((inv, idx) => (
                        <tr key={idx} className="table-row-alt hover:bg-[#FFF7ED] transition-colors">
                          <td className="px-4 py-2.5 text-[#111827] font-medium max-w-[140px] truncate">
                            {inv.contact_name}
                          </td>
                          <td className="px-4 py-2.5 font-mono text-xs text-[#CC6C00]">
                            {inv.invoice_number}
                          </td>
                          <td className="px-4 py-2.5 text-[#6B7280] text-xs hidden md:table-cell">
                            {formatDate(inv.due_date)}
                          </td>
                          <td className="px-4 py-2.5 text-right font-mono-nums text-[#6B7280] text-xs hidden lg:table-cell">
                            {fmt(inv.total_amount)}
                          </td>
                          <td className="px-4 py-2.5 text-right font-mono-nums text-[#059669] text-xs hidden lg:table-cell">
                            {parseAmount(inv.amount_paid) > 0 ? fmt(inv.amount_paid) : '—'}
                          </td>
                          <td className="px-4 py-2.5 text-right font-mono-nums font-semibold">
                            <span className={getBucketColor(inv.age_bucket)}>
                              {fmt(inv.balance_due)}
                            </span>
                          </td>
                          <td className="px-4 py-2.5">
                            <span
                              className={cn(
                                'inline-flex items-center px-2 py-0.5 text-xs font-medium rounded-full',
                                getBucketBgColor(inv.age_bucket)
                              )}
                            >
                              {BUCKET_LABELS[inv.age_bucket] || inv.age_bucket}
                            </span>
                          </td>
                          <td className="px-4 py-2.5 text-right hidden md:table-cell">
                            <span className={cn('text-xs font-medium', getBucketColor(inv.age_bucket))}>
                              {inv.days_past_due > 0 ? `${inv.days_past_due}d` : 'Current'}
                            </span>
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                  {allInvoices.length > 0 && (
                    <tfoot className="bg-[#F9FAFB] border-t border-[#E5E7EB]">
                      <tr>
                        <td colSpan={5} className="px-4 py-3 text-xs font-bold text-[#6B7280] uppercase">
                          Total Outstanding
                        </td>
                        <td className="px-4 py-3 text-right font-bold font-mono-nums text-[#DC2626]">
                          {fmt(report.totals.total_outstanding)}
                        </td>
                        <td colSpan={2} />
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
