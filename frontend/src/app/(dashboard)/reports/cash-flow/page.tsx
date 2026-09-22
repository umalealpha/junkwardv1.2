'use client'

/**
 * /reports/cash-flow — Indirect-method cash flow statement.
 *
 * CFO directive 2026-05-20. Reads from /api/reports/cash-flow/.
 * Starts from PAT, walks working capital deltas, splits into
 * Operating / Investing / Financing.
 */

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { apiFetch, downloadReportXlsx, getToken } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { LoadingTable } from '@/components/ui/loading'
import { formatAmount, parseAmount, exportToCsv, getFyStart, today, cn, formatDate } from '@/lib/utils'
import { useNumberFormat } from '@/contexts/NumberFormatContext'
import { useCompany } from '@/contexts/CompanyContext'
import { Download, AlertCircle } from 'lucide-react'

interface CashFlowReport {
  from_date: string
  to_date: string
  sections: {
    id: string
    label: string
    lines: { label: string; amount: string; subtotal?: boolean }[]
    subtotal_label: string
    subtotal: string
  }[]
  totals: {
    net_change_in_cash: string
    opening_cash: string
    closing_cash: string
    closing_cash_from_bs: string
    reconciliation_difference: string
  }
}

function _qs(o: Record<string, string | undefined>): string {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(o)) if (v) p.set(k, v)
  const s = p.toString()
  return s ? `?${s}` : ''
}

async function getCashFlow(from: string, to: string, company?: string | null) {
  return apiFetch<CashFlowReport>(`/reports/cash-flow/${_qs({ from, to, company: company ?? undefined })}`)
}

export default function CashFlowPage() {
  const router = useRouter()
  const { mode } = useNumberFormat()
  const { selectedId: companyId, selected: selectedCompany } = useCompany()
  // CFO directive 2026-05-21: entity functional currency
  const currency = (selectedCompany?.base_currency || 'BWP') as string
  const fmt = (a: string | number) => formatAmount(a, currency, mode)
  const [fromDate, setFromDate] = useState(getFyStart())
  const [toDate, setToDate] = useState(today())
  const [report, setReport] = useState<CashFlowReport | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleGenerate = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const data = await getCashFlow(fromDate, toDate, companyId ?? undefined)
      setReport(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to generate Cash Flow')
    } finally {
      setLoading(false)
    }
  }, [fromDate, toDate, companyId])

  useEffect(() => {
    const token = getToken()
    if (!token) { router.replace('/login'); return }
    handleGenerate()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [companyId])

  const handleExport = () => {
    if (!report) return
    const rows: Record<string, string>[] = []
    for (const sec of report.sections) {
      rows.push({ Section: sec.label, Line: '', Amount: '' })
      for (const ln of sec.lines) {
        rows.push({ Section: '', Line: ln.label, Amount: ln.amount })
      }
      rows.push({ Section: '', Line: sec.subtotal_label, Amount: sec.subtotal })
    }
    rows.push({ Section: 'Reconciliation', Line: 'Net change in cash', Amount: report.totals.net_change_in_cash })
    rows.push({ Section: '', Line: 'Opening cash', Amount: report.totals.opening_cash })
    rows.push({ Section: '', Line: 'Closing cash', Amount: report.totals.closing_cash })
    rows.push({ Section: '', Line: 'Reconciliation diff', Amount: report.totals.reconciliation_difference })
    exportToCsv(rows, `cash_flow_${fromDate}_to_${toDate}.csv`)
  }

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Cash Flow Statement"
        breadcrumbs={[{ label: 'Reports' }, { label: 'Cash Flow' }]}
        actions={
          report && (
            <div className="flex items-center gap-2">
              <Button variant="secondary" size="sm"
                      leftIcon={<Download className="w-3.5 h-3.5" />}
                      onClick={handleExport}>Export CSV</Button>
              <Button variant="secondary" size="sm"
                      leftIcon={<Download className="w-3.5 h-3.5" />}
                      onClick={() => downloadReportXlsx('cash_flow', {
                        from: fromDate, to: toDate, company: companyId ?? undefined,
                      })}>Export Excel</Button>
            </div>
          )
        }
      />
      <div className="flex-1 p-6 space-y-6">
        <Card>
          <CardContent className="py-4">
            <div className="flex flex-wrap items-end gap-4">
              <div>
                <label className="block text-xs text-[#374151] font-medium mb-1">From Date</label>
                <input type="date" value={fromDate} onChange={(e) => setFromDate(e.target.value)}
                       className="bg-white border border-[#D1D5DB] rounded-lg px-3 py-2 text-sm text-[#111827] focus:outline-none focus:border-[#F07F00] focus:ring-[3px] focus:ring-[rgba(240,127,0,0.1)] transition-all" />
              </div>
              <div>
                <label className="block text-xs text-[#374151] font-medium mb-1">To Date</label>
                <input type="date" value={toDate} onChange={(e) => setToDate(e.target.value)}
                       className="bg-white border border-[#D1D5DB] rounded-lg px-3 py-2 text-sm text-[#111827] focus:outline-none focus:border-[#F07F00] focus:ring-[3px] focus:ring-[rgba(240,127,0,0.1)] transition-all" />
              </div>
              <Button variant="primary" size="md" onClick={handleGenerate} loading={loading}>Generate</Button>

              {report && (
                <div className={cn(
                  'flex items-center gap-2 px-3 py-2 rounded-lg',
                  parseAmount(report.totals.net_change_in_cash) >= 0
                    ? 'bg-[#ECFDF5] border border-[#A7F3D0]'
                    : 'bg-[#FEF2F2] border border-[#FEE2E2]',
                )}>
                  <span className={cn('text-sm font-bold',
                    parseAmount(report.totals.net_change_in_cash) >= 0 ? 'text-[#059669]' : 'text-[#DC2626]')}>
                    Net Δ Cash: {fmt(report.totals.net_change_in_cash)}
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

        {loading ? (
          <Card><CardContent className="p-0"><LoadingTable rows={25} cols={2} /></CardContent></Card>
        ) : !report ? null : (
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle>Cash Flow Statement</CardTitle>
                <p className="text-xs text-[#9CA3AF]">
                  {formatDate(report.from_date)} to {formatDate(report.to_date)}
                </p>
              </div>
            </CardHeader>
            <CardContent className="space-y-6">
              {report.sections.map((sec) => (
                <div key={sec.id}>
                  <h4 className="text-sm font-semibold text-[#374151] uppercase tracking-wider mb-1">
                    {sec.label}
                  </h4>
                  <div className="ml-2 space-y-0">
                    {sec.lines.map((ln, i) => (
                      <div key={i} className={cn(
                        'flex items-center justify-between py-1.5',
                        ln.subtotal && 'border-t border-[#E5E7EB] mt-1 pt-2 font-semibold',
                      )}>
                        <span className="text-sm text-[#374151]">{ln.label}</span>
                        <span className={cn('text-sm font-mono-nums',
                          parseAmount(ln.amount) >= 0 ? 'text-[#059669]' : 'text-[#CC6C00]')}>
                          {fmt(ln.amount)}
                        </span>
                      </div>
                    ))}
                  </div>
                  <div className="flex items-center justify-between py-2 border-t-2 border-[#0B0B3B] mt-1">
                    <span className="text-sm font-bold uppercase tracking-wider text-[#111827]">{sec.subtotal_label}</span>
                    <span className={cn('text-base font-bold font-mono-nums',
                      parseAmount(sec.subtotal) >= 0 ? 'text-[#059669]' : 'text-[#CC6C00]')}>
                      {fmt(sec.subtotal)}
                    </span>
                  </div>
                </div>
              ))}

              <div className="space-y-1 pt-4 border-t-2 border-[#0B0B3B]">
                <div className="flex items-center justify-between py-1.5">
                  <span className="text-sm font-semibold uppercase tracking-wider text-[#111827]">Net change in cash</span>
                  <span className={cn('text-base font-bold font-mono-nums',
                    parseAmount(report.totals.net_change_in_cash) >= 0 ? 'text-[#059669]' : 'text-[#CC6C00]')}>
                    {fmt(report.totals.net_change_in_cash)}
                  </span>
                </div>
                <div className="flex items-center justify-between py-1.5">
                  <span className="text-sm text-[#374151]">Opening cash</span>
                  <span className="text-sm font-mono-nums">{fmt(report.totals.opening_cash)}</span>
                </div>
                <div className="flex items-center justify-between py-1.5">
                  <span className="text-sm text-[#374151]">Closing cash</span>
                  <span className="text-sm font-mono-nums">{fmt(report.totals.closing_cash)}</span>
                </div>
                {parseAmount(report.totals.reconciliation_difference) !== 0 && (
                  <div className="flex items-center justify-between py-1.5 text-[#CC6C00]">
                    <span className="text-xs">Reconciliation diff (BS Δ vs CF)</span>
                    <span className="text-xs font-mono-nums">{fmt(report.totals.reconciliation_difference)}</span>
                  </div>
                )}
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  )
}
