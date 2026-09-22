'use client'

/**
 * /reports/profit-loss — Management Accounts P&L view.
 *
 * CFO directive 2026-05-20: this page MUST show the MA layout
 * (Net Earned Premium → Claims → Acquisition → Gross Profit → Other Income
 * → Expenses → Provisions → EBITDA → Depreciation → EBIT → Finance Cost →
 * PBT → Taxation → PAT), not the raw account-type dump that the legacy
 * `build_profit_loss` returned. Reads from /api/reports/ma-profit-loss/
 * (the canonical builder, driven by reporting/ma_pl_spec.py).
 */

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { downloadReportXlsx, getMAProfitLoss, getToken } from '@/lib/api'
import type { MAProfitLossReport } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { LoadingTable } from '@/components/ui/loading'
import { formatAmount, parseAmount, exportToCsv, getFyStart, today, cn, formatDate } from '@/lib/utils'
import { useNumberFormat } from '@/contexts/NumberFormatContext'
import { useCompany } from '@/contexts/CompanyContext'
import { Download, TrendingUp, TrendingDown, AlertCircle } from 'lucide-react'
import { computeETRStatus } from '@/components/reports/TaxBlock'
import TaxRowSlim from '@/components/reports/TaxRowSlim'
import { FyPresetChips } from '@/components/finance/FyPresetChips'


function SubtotalRow({
  label, amount, bold, colorClass, currency = 'BWP',
}: { label: string; amount: string; bold?: boolean; colorClass?: string; currency?: string }) {
  const { mode } = useNumberFormat()
  const fmt = (a: string | number) => formatAmount(a, currency, mode)
  return (
    <div
      className={cn(
        'flex items-center justify-between py-2 border-t border-[#E5E7EB]',
        bold && 'border-t-2 border-[#0B0B3B] py-3',
      )}
    >
      <span
        className={cn(
          'text-sm uppercase tracking-wider',
          bold ? 'text-[#111827] font-bold' : 'text-[#374151] font-semibold',
        )}
      >
        {label}
      </span>
      <span className={cn('font-mono-nums', bold ? 'text-base font-bold' : 'text-sm font-semibold', colorClass)}>
        {fmt(amount)}
      </span>
    </div>
  )
}

function LineRow({
  label, amount, sign, currency = 'BWP',
}: { label: string; amount: string; sign: 'income' | 'expense'; currency?: string }) {
  const { mode } = useNumberFormat()
  const fmt = (a: string | number) => formatAmount(a, currency, mode)
  // BUG-008 (Oprah 2c, 2026-06-05): expense lines neutral grey, not orange.
  // BUG-007 (Oprah 1a): expenses are deductions → shown in (parentheses).
  const colorClass = sign === 'income' ? 'text-[#059669]' : 'text-[#374151]'
  const display = sign === 'expense'
    ? `(${fmt(Math.abs(parseAmount(amount)))})`
    : fmt(amount)
  return (
    <div className="flex items-center justify-between py-1.5">
      <span className="text-sm text-[#374151]">{label}</span>
      <span className={cn('text-sm font-mono-nums', colorClass)}>{display}</span>
    </div>
  )
}


export default function ProfitLossPage() {
  const router = useRouter()
  const { mode } = useNumberFormat()
  const { selectedId: companyId, selected: selectedCompany } = useCompany()
  // CFO directive 2026-05-21: entity functional currency drives every label
  const currency = (selectedCompany?.base_currency || 'BWP') as string
  const fmt = (a: string | number) => formatAmount(a, currency, mode)
  const [fromDate, setFromDate] = useState(getFyStart())
  const [toDate, setToDate] = useState(today())
  const [report, setReport] = useState<MAProfitLossReport | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Accept explicit dates so preset chips can fire the fetch synchronously
  // without waiting for React state to flush.
  const fetchReport = useCallback(async (from: string, to: string) => {
    setLoading(true)
    setError(null)
    try {
      const data = await getMAProfitLoss(from, to, companyId ?? undefined)
      setReport(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to generate P&L report')
    } finally {
      setLoading(false)
    }
  }, [companyId])

  const handleGenerate = useCallback(() => fetchReport(fromDate, toDate),
    [fetchReport, fromDate, toDate])

  const applyPreset = useCallback((from: string, to: string) => {
    setFromDate(from); setToDate(to); fetchReport(from, to)
  }, [fetchReport])

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
    // Bottom-line metrics
    const t = report.totals
    rows.push({ Section: 'Below-the-line', Line: 'Gross Profit', Amount: t.gross_profit })
    rows.push({ Section: '', Line: 'EBITDA', Amount: t.ebitda })
    rows.push({ Section: '', Line: 'Depreciation', Amount: t.depreciation })
    rows.push({ Section: '', Line: 'EBIT', Amount: t.ebit })
    rows.push({ Section: '', Line: 'Finance Cost', Amount: t.finance_cost })
    rows.push({ Section: '', Line: 'PBT', Amount: t.pbt })
    rows.push({ Section: '', Line: 'Taxation', Amount: t.taxation })
    rows.push({ Section: '', Line: 'PAT', Amount: t.pat })
    exportToCsv(rows, `ma_profit_loss_${fromDate}_to_${toDate}.csv`)
  }

  const isProfit = report ? parseAmount(report.totals.pat) >= 0 : false

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Profit and Loss Alpha Direct BW"
        breadcrumbs={[{ label: 'Reports' }, { label: 'Profit and Loss Alpha Direct BW' }]}
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
                onClick={() => downloadReportXlsx('profit_loss', {
                  from: fromDate, to: toDate, company: companyId ?? undefined,
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
          <CardContent className="py-4 space-y-3">
            <FyPresetChips activeFrom={fromDate} activeTo={toDate} onApply={applyPreset} />
            <div className="flex flex-wrap items-end gap-4">
              <div>
                <label className="block text-xs text-[#374151] font-medium mb-1">From Date</label>
                <input
                  type="date"
                  value={fromDate}
                  onChange={(e) => setFromDate(e.target.value)}
                  className="bg-white border border-[#D1D5DB] rounded-lg px-3 py-2 text-sm text-[#111827] focus:outline-none focus:border-[#F07F00] focus:ring-[3px] focus:ring-[rgba(240,127,0,0.1)] transition-all"
                />
              </div>
              <div>
                <label className="block text-xs text-[#374151] font-medium mb-1">To Date</label>
                <input
                  type="date"
                  value={toDate}
                  onChange={(e) => setToDate(e.target.value)}
                  className="bg-white border border-[#D1D5DB] rounded-lg px-3 py-2 text-sm text-[#111827] focus:outline-none focus:border-[#F07F00] focus:ring-[3px] focus:ring-[rgba(240,127,0,0.1)] transition-all"
                />
              </div>
              <Button variant="primary" size="md" onClick={handleGenerate} loading={loading}>
                Generate
              </Button>

              {report && (
                <div
                  className={cn(
                    'flex items-center gap-2 px-3 py-2 rounded-lg',
                    isProfit
                      ? 'bg-[#ECFDF5] border border-[#A7F3D0]'
                      : 'bg-[#FEF2F2] border border-[#FEE2E2]',
                  )}
                >
                  {isProfit ? (
                    <TrendingUp className="w-4 h-4 text-[#059669]" />
                  ) : (
                    <TrendingDown className="w-4 h-4 text-[#DC2626]" />
                  )}
                  <span className={cn('text-sm font-bold', isProfit ? 'text-[#059669]' : 'text-[#DC2626]')}>
                    {isProfit ? 'PAT' : 'LOSS'}: {fmt(report.totals.pat)}
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
          <Card><CardContent className="p-0"><LoadingTable rows={20} cols={2} /></CardContent></Card>
        ) : !report ? null : (
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle>Statement of Comprehensive Income</CardTitle>
                <p className="text-xs text-[#9CA3AF]">
                  {formatDate(report.from_date)} to {formatDate(report.to_date)}
                </p>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              {report.sections.map((sec) => (
                <div key={sec.id}>
                  <h4 className="text-sm font-semibold text-[#374151] uppercase tracking-wider mb-1">
                    {sec.label}
                  </h4>
                  <div className="ml-2 space-y-0">
                    {sec.lines.map((ln) => (
                      <LineRow key={ln.id} label={ln.label} amount={ln.amount} sign={ln.sign} currency={currency} />
                    ))}
                  </div>
                  <SubtotalRow label={sec.subtotal_label} amount={sec.subtotal} colorClass="text-[#0B0B3B]" currency={currency} />
                </div>
              ))}

              {/* Below-the-line metrics from the totals dict */}
              <div className="pt-4 space-y-0">
                <SubtotalRow label="Gross Profit" amount={report.totals.gross_profit} bold currency={currency}
                             colorClass={parseAmount(report.totals.gross_profit) >= 0 ? 'text-[#059669]' : 'text-[#DC2626]'} />
                <SubtotalRow label="EBITDA" amount={report.totals.ebitda} bold currency={currency}
                             colorClass={parseAmount(report.totals.ebitda) >= 0 ? 'text-[#059669]' : 'text-[#DC2626]'} />
                <LineRow    label="Depreciation"  amount={report.totals.depreciation} sign="expense" currency={currency} />
                <SubtotalRow label="EBIT" amount={report.totals.ebit} currency={currency}
                             colorClass={parseAmount(report.totals.ebit) >= 0 ? 'text-[#059669]' : 'text-[#DC2626]'} />
                <LineRow    label="Finance Cost"  amount={report.totals.finance_cost} sign="expense" currency={currency} />
                <SubtotalRow label="Profit Before Tax" amount={report.totals.pbt} bold currency={currency}
                             colorClass={parseAmount(report.totals.pbt) >= 0 ? 'text-[#059669]' : 'text-[#DC2626]'} />
                {/* TAX-005: slim Taxation row only; full IAS 12
                    reconciliation moved to /reports/tax-reconciliation. */}
                <TaxRowSlim
                  totalTax={parseAmount(report.totals.taxation)}
                  pbt={parseAmount(report.totals.pbt)}
                  currency={currency}
                />
                <SubtotalRow label="Profit After Tax" amount={report.totals.pat} bold currency={currency}
                             colorClass={parseAmount(report.totals.pat) >= 0 ? 'text-[#059669]' : 'text-[#DC2626]'} />
              </div>

              {/* KPI strip — Gross/Net Loss Ratio + ETR (TAX-003) */}
              <div className="pt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-[#6B7280]">
                <span>Gross Loss Ratio: <strong>{(parseFloat(report.totals.gross_loss_ratio) * 100).toFixed(1)}%</strong></span>
                <span>Net Loss Ratio: <strong>{(parseFloat(report.totals.net_loss_ratio) * 100).toFixed(1)}%</strong></span>
                {(() => {
                  const { label, tone } = computeETRStatus(
                    parseAmount(report.totals.pbt),
                    parseAmount(report.totals.taxation),
                  )
                  const cls =
                    tone === 'normal' ? 'text-[#059669]' :
                    tone === 'low'    ? 'text-[#B45309]' :
                    tone === 'high'   ? 'text-[#DC2626]' :
                                        'text-[#6B7280]'
                  return (
                    <span>
                      Effective Tax Rate: <strong className={cls} title="ETR = Tax ÷ PBT. Expected 15–35% (Botswana CIT 22%).">{label}</strong>
                    </span>
                  )
                })()}
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  )
}
