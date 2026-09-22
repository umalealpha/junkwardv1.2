'use client'

/**
 * /reports/balance-sheet — Management Accounts Balance Sheet view.
 *
 * CFO directive 2026-05-20: groups by `Account.fs_line_item` per the
 * MA workbook layout (reporting/ma_bs_spec.py). Reads from
 * /api/reports/balance-sheet/?format=ma — the canonical MA-aligned
 * builder. Legacy account-type/sub-type breakdown is still available
 * via ?format=legacy on the same endpoint.
 */

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { downloadReportXlsx, getMaBalanceSheet, getToken } from '@/lib/api'
import type { MaBalanceSheetReport } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { LoadingTable } from '@/components/ui/loading'
import { formatAmount, exportToCsv, today, cn, formatDate } from '@/lib/utils'
import { useNumberFormat } from '@/contexts/NumberFormatContext'
import { useCompany } from '@/contexts/CompanyContext'
import { Download, CheckCircle, AlertCircle } from 'lucide-react'
import { FyPresetChips } from '@/components/finance/FyPresetChips'


function SectionBlock({
  label, side, lines, subtotalLabel, subtotal, currency = 'BWP',
}: {
  label: string
  side: 'asset' | 'liability' | 'equity'
  lines: { label: string; amount: string }[]
  subtotalLabel: string
  subtotal: string
  currency?: string
}) {
  const { mode } = useNumberFormat()
  const fmt = (a: string | number) => formatAmount(a, currency, mode)
  const sideColor =
    side === 'asset' ? 'text-[#059669]' :
    side === 'liability' ? 'text-[#CC6C00]' :
    'text-[#1D4ED8]'
  return (
    <div className="min-w-0">
      <h4 className="text-sm font-semibold text-[#374151] uppercase tracking-wider mb-1">
        {label}
      </h4>
      <div className="ml-2 space-y-0">
        {lines.map((ln) => (
          // BUG-2 fix 2026-06-09: row uses min-w-0 + label truncates so the
          // amount column on the right never gets clipped at the viewport edge.
          <div key={ln.label} className="flex items-center justify-between gap-3 py-1.5 min-w-0">
            <span className="text-sm text-[#374151] min-w-0 truncate" title={ln.label}>{ln.label}</span>
            <span className={cn('text-sm font-mono-nums flex-shrink-0 whitespace-nowrap', sideColor)}>
              {fmt(ln.amount)}
            </span>
          </div>
        ))}
      </div>
      <div className="flex items-center justify-between gap-3 py-2 border-t border-[#E5E7EB] min-w-0">
        <span className="text-sm font-semibold uppercase tracking-wider text-[#111827] min-w-0 truncate" title={subtotalLabel}>
          {subtotalLabel}
        </span>
        <span className={cn('text-sm font-bold font-mono-nums flex-shrink-0 whitespace-nowrap', sideColor)}>
          {fmt(subtotal)}
        </span>
      </div>
    </div>
  )
}


export default function BalanceSheetPage() {
  const router = useRouter()
  const { mode } = useNumberFormat()
  const { selectedId: companyId, selected: selectedCompany } = useCompany()
  // CFO directive 2026-05-21: entity functional currency
  const currency = (selectedCompany?.base_currency || 'BWP') as string
  const fmt = (a: string | number) => formatAmount(a, currency, mode)
  const [asOf, setAsOf] = useState(today())
  const [report, setReport] = useState<MaBalanceSheetReport | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Accept explicit as_of so preset chips can fire synchronously
  // without waiting for setAsOf state flush.
  const fetchReport = useCallback(async (when: string) => {
    setLoading(true)
    setError(null)
    try {
      const data = await getMaBalanceSheet(when, companyId ?? undefined)
      setReport(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to generate Balance Sheet')
    } finally {
      setLoading(false)
    }
  }, [companyId])

  const handleGenerate = useCallback(() => fetchReport(asOf),
    [fetchReport, asOf])

  const applyPreset = useCallback((_from: string, to: string) => {
    setAsOf(to); fetchReport(to)
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
    const t = report.totals
    rows.push({ Section: 'Totals', Line: 'Total Assets', Amount: t.total_assets })
    rows.push({ Section: '', Line: 'Total Liabilities', Amount: t.total_liabilities })
    rows.push({ Section: '', Line: 'Total Equity', Amount: t.total_equity })
    rows.push({ Section: '', Line: 'Liabilities + Equity', Amount: t.liabilities_and_equity })
    exportToCsv(rows, `ma_balance_sheet_${asOf}.csv`)
  }

  const assets = report?.sections.filter((s) => s.side === 'asset') || []
  const liabilities = report?.sections.filter((s) => s.side === 'liability') || []
  const equity = report?.sections.filter((s) => s.side === 'equity') || []

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Balance Sheet (Management Accounts)"
        breadcrumbs={[{ label: 'Reports' }, { label: 'Balance Sheet' }]}
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
                onClick={() => downloadReportXlsx('ma_balance_sheet', {
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
        <Card>
          <CardContent className="py-4 space-y-3">
            <FyPresetChips activeTo={asOf} mode="as-of" onApply={applyPreset} />
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
                <div
                  className={cn(
                    'flex items-center gap-2 px-3 py-2 rounded-lg',
                    report.totals.balanced
                      ? 'bg-[#ECFDF5] border border-[#A7F3D0]'
                      : 'bg-[#FEF2F2] border border-[#FEE2E2]',
                  )}
                >
                  {report.totals.balanced ? (
                    <CheckCircle className="w-4 h-4 text-[#059669]" />
                  ) : (
                    <AlertCircle className="w-4 h-4 text-[#DC2626]" />
                  )}
                  <span className={cn('text-sm font-bold', report.totals.balanced ? 'text-[#059669]' : 'text-[#DC2626]')}>
                    {report.totals.balanced ? 'BALANCED' : 'OUT OF BALANCE'}: A {fmt(report.totals.total_assets)} | L+E {fmt(report.totals.liabilities_and_equity)}
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
              {/* BUG-1 fix 2026-06-09: date was clipping to a single digit
                  ("As at 0") because the flex header had no whitespace-nowrap
                  protection. Same overflow class as BUG-2. */}
              <div className="flex items-center justify-between gap-3 min-w-0">
                <CardTitle className="min-w-0 truncate">Statement of Financial Position</CardTitle>
                <p className="text-xs text-[#9CA3AF] flex-shrink-0 whitespace-nowrap">As at {formatDate(report.as_of)}</p>
              </div>
            </CardHeader>
            <CardContent className="space-y-6">
              <div>
                <h3 className="text-base font-bold text-[#111827] uppercase tracking-wider border-b-2 border-[#0B0B3B] pb-1 mb-3">
                  Assets
                </h3>
                {assets.map((sec) => (
                  <div key={sec.id} className="mb-4">
                    <SectionBlock
                      label={sec.label} side={sec.side}
                      lines={sec.lines}
                      subtotalLabel={sec.subtotal_label}
                      subtotal={sec.subtotal}
                      currency={currency}
                    />
                  </div>
                ))}
                <div className="flex items-center justify-between gap-3 border-t-2 border-[#059669] pt-2 min-w-0">
                  <span className="text-base font-bold uppercase tracking-wider text-[#059669] min-w-0 truncate">Total Assets</span>
                  <span className="text-lg font-bold font-mono-nums text-[#059669] flex-shrink-0 whitespace-nowrap">
                    {fmt(report.totals.total_assets)}
                  </span>
                </div>
              </div>

              <div>
                <h3 className="text-base font-bold text-[#111827] uppercase tracking-wider border-b-2 border-[#0B0B3B] pb-1 mb-3">
                  Liabilities
                </h3>
                {liabilities.map((sec) => (
                  <div key={sec.id} className="mb-4">
                    <SectionBlock
                      label={sec.label} side={sec.side}
                      lines={sec.lines}
                      subtotalLabel={sec.subtotal_label}
                      subtotal={sec.subtotal}
                      currency={currency}
                    />
                  </div>
                ))}
                <div className="flex items-center justify-between gap-3 border-t-2 border-[#CC6C00] pt-2 min-w-0">
                  <span className="text-base font-bold uppercase tracking-wider text-[#CC6C00] min-w-0 truncate">Total Liabilities</span>
                  <span className="text-lg font-bold font-mono-nums text-[#CC6C00] flex-shrink-0 whitespace-nowrap">
                    {fmt(report.totals.total_liabilities)}
                  </span>
                </div>
              </div>

              <div>
                <h3 className="text-base font-bold text-[#111827] uppercase tracking-wider border-b-2 border-[#0B0B3B] pb-1 mb-3">
                  Equity
                </h3>
                {equity.map((sec) => (
                  <div key={sec.id} className="mb-4">
                    <SectionBlock
                      label={sec.label} side={sec.side}
                      lines={sec.lines}
                      subtotalLabel={sec.subtotal_label}
                      subtotal={sec.subtotal}
                      currency={currency}
                    />
                  </div>
                ))}
                {/* OMNI-QA-020 (Lakshmi QA 2026-06-09): duplicate "TOTAL EQUITY"
                    lines. When equity is a single section its own subtotal_label
                    is already "Total Equity", so this grand-total row duplicated
                    it. Render the grand total ONLY when it isn't already shown by
                    a lone section subtotal. */}
                {!(equity.length === 1 &&
                   (equity[0].subtotal_label || '').trim().toLowerCase() === 'total equity') && (
                  <div className="flex items-center justify-between gap-3 border-t-2 border-[#1D4ED8] pt-2 min-w-0">
                    <span className="text-base font-bold uppercase tracking-wider text-[#1D4ED8] min-w-0 truncate">Total Equity</span>
                    <span className="text-lg font-bold font-mono-nums text-[#1D4ED8] flex-shrink-0 whitespace-nowrap">
                      {fmt(report.totals.total_equity)}
                    </span>
                  </div>
                )}
              </div>

              <div className="flex items-center justify-between gap-3 border-t-2 border-[#0B0B3B] pt-3 min-w-0">
                <span className="text-base font-bold uppercase tracking-wider text-[#111827] min-w-0 truncate">
                  Liabilities + Equity
                </span>
                <span className="text-lg font-bold font-mono-nums text-[#111827] flex-shrink-0 whitespace-nowrap">
                  {fmt(report.totals.liabilities_and_equity)}
                </span>
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  )
}
