'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { getTrialBalance, getToken } from '@/lib/api'
import type { TrialBalanceReport } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { LoadingTable } from '@/components/ui/loading'
import { formatAmount, parseAmount, exportToCsv, today, cn, getFyStart } from '@/lib/utils'
import { useNumberFormat } from '@/contexts/NumberFormatContext'
import { useCompany } from '@/contexts/CompanyContext'
import { Download, CheckCircle, AlertCircle, Scale } from 'lucide-react'
import { ReportScopeBanner } from '@/components/ReportScopeBanner'
import SmartUpload from '@/components/SmartUpload'

// ─── Trial Balance Report ─────────────────────────────────────────────────────

export default function TrialBalancePage() {
  const router = useRouter()
  const { mode } = useNumberFormat()
  const { selectedId: companyId, selected: selectedCompany } = useCompany()
  // CFO directive 2026-05-21: entity functional currency
  const currency = (selectedCompany?.base_currency || 'BWP') as string
  const fmt = (amount: string | number, c: string = currency) => formatAmount(amount, c, mode)

  // CFO directive 2026-05-21: TB is ALWAYS a period view (opening +
  // movements + closing). Drop the legacy single "As of Date" field
  // in favour of an explicit From / To period. Defaults to current
  // Botswana fiscal year (Jul-Jun).
  const [fromDate, setFromDate] = useState(getFyStart())
  const [toDate,   setToDate]   = useState(today())
  const [report, setReport] = useState<TrialBalanceReport | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // CFO directive 2026-05-19 — TB needs an in-place search box to find
  // accounts by code or name without re-rendering the whole report.
  const [query, setQuery] = useState('')

  useEffect(() => {
    const token = getToken()
    if (!token) {
      router.replace('/login')
      return
    }
    // Auto-load on mount AND whenever the company / period changes.
    handleGenerate()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [companyId, fromDate, toDate])

  const handleGenerate = async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await getTrialBalance({ from: fromDate, to: toDate, company: companyId })
      setReport(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to generate trial balance')
    } finally {
      setLoading(false)
    }
  }

  // ── Quick FY presets ──────────────────────────────────────────────────────
  // Matches the SmartUpload section above so the upload window and the
  // display window stay in sync without typing dates twice.
  function applyPreset(preset: 'FY25' | 'FY26-9M' | 'FY26' | 'YTD') {
    if (preset === 'FY25')   { setFromDate('2024-07-01'); setToDate('2025-06-30'); return }
    if (preset === 'FY26-9M'){ setFromDate('2025-07-01'); setToDate('2026-03-31'); return }
    if (preset === 'FY26')   { setFromDate('2025-07-01'); setToDate('2026-06-30'); return }
    if (preset === 'YTD')    { setFromDate(getFyStart());  setToDate(today());      return }
  }

  // ── Export ────────────────────────────────────────────────────────────────
  // CFO directive 2026-05-19: TB export was losing the Dr/Cr side and
  // skipping per-type subtotals. Rewritten to produce a proper
  // accountant-style TB with separate Closing Dr and Closing Cr
  // columns + Asset / Liability / Equity / Income / Expense subtotal
  // rows + grand totals. CSV remains the default; XLSX uses dynamic
  // import (xlsx already in package.json).

  function naturalSplit(value: string | number, accountType: string): { dr: number; cr: number } {
    const n = typeof value === 'string' ? parseFloat(value) : (value || 0)
    if (!isFinite(n) || n === 0) return { dr: 0, cr: 0 }
    const debitSide = accountType === 'asset' || accountType === 'expense'
    if (debitSide) {
      return n > 0 ? { dr: n, cr: 0 } : { dr: 0, cr: Math.abs(n) }
    }
    return n > 0 ? { dr: 0, cr: n } : { dr: Math.abs(n), cr: 0 }
  }

  const TYPE_LABEL: Record<string, string> = {
    asset:     'Assets',
    liability: 'Liabilities',
    equity:    'Equity',
    income:    'Income',
    revenue:   'Income',
    expense:   'Expenses',
  }
  const TYPE_ORDER = ['asset', 'liability', 'equity', 'income', 'revenue', 'expense']

  function buildRows(): Array<Record<string, any>> {
    if (!report) return []
    // Search filter — substring match against code / name / type / sub_type.
    const q = query.trim().toLowerCase()
    const accountsToShow = q
      ? report.accounts.filter((a: any) =>
          [a.code, a.name, a.account_type, a.sub_type]
            .filter(Boolean)
            .some((v: string) => String(v).toLowerCase().includes(q)),
        )
      : report.accounts
    const byType: Record<string, typeof report.accounts> = {}
    for (const a of accountsToShow) {
      const t = a.account_type || 'other'
      ;(byType[t] = byType[t] || []).push(a)
    }

    const rows: Array<Record<string, any>> = []
    let grandOpen = 0, grandPerDr = 0, grandPerCr = 0, grandCloseDr = 0, grandCloseCr = 0

    const orderedTypes = [
      ...TYPE_ORDER.filter(t => byType[t]),
      ...Object.keys(byType).filter(t => !TYPE_ORDER.includes(t)),
    ]

    for (const t of orderedTypes) {
      const accs = byType[t]
      // Section header
      rows.push({
        Code: `── ${TYPE_LABEL[t] || t.toUpperCase()} ──`,
        'Account Name': '', 'Type': '', 'Sub-type': '',
        'Opening Balance': '', 'Period Debits': '', 'Period Credits': '',
        'Closing Dr (BWP)': '', 'Closing Cr (BWP)': '', 'Closing Balance': '',
      })

      let secOpen = 0, secPerDr = 0, secPerCr = 0, secCloseDr = 0, secCloseCr = 0

      for (const a of accs) {
        const opening   = parseFloat(a.opening_balance) || 0
        const perDr     = parseFloat(a.period_debits)   || 0
        const perCr     = parseFloat(a.period_credits)  || 0
        const closingS  = parseFloat(a.closing_balance) || 0
        const split    = naturalSplit(closingS, a.account_type)

        secOpen   += opening
        secPerDr  += perDr
        secPerCr  += perCr
        secCloseDr += split.dr
        secCloseCr += split.cr

        rows.push({
          Code: a.code,
          'Account Name': a.name,
          Type: a.account_type,
          'Sub-type': a.sub_type,
          'Opening Balance': opening.toFixed(2),
          'Period Debits':   perDr.toFixed(2),
          'Period Credits':  perCr.toFixed(2),
          'Closing Dr (BWP)': split.dr ? split.dr.toFixed(2) : '',
          'Closing Cr (BWP)': split.cr ? split.cr.toFixed(2) : '',
          'Closing Balance': closingS.toFixed(2),
        })
      }

      // Subtotal row
      rows.push({
        Code: '', 'Account Name': `${TYPE_LABEL[t] || t} subtotal`,
        Type: '', 'Sub-type': '',
        'Opening Balance':  secOpen.toFixed(2),
        'Period Debits':    secPerDr.toFixed(2),
        'Period Credits':   secPerCr.toFixed(2),
        'Closing Dr (BWP)': secCloseDr ? secCloseDr.toFixed(2) : '',
        'Closing Cr (BWP)': secCloseCr ? secCloseCr.toFixed(2) : '',
        'Closing Balance':  (secCloseDr - secCloseCr).toFixed(2),
      })

      grandOpen    += secOpen
      grandPerDr   += secPerDr
      grandPerCr   += secPerCr
      grandCloseDr += secCloseDr
      grandCloseCr += secCloseCr
    }

    // Grand totals — accountant TB MUST balance: ΣDr = ΣCr
    rows.push({
      Code: 'TOTAL', 'Account Name': 'GRAND TOTALS',
      Type: '', 'Sub-type': '',
      'Opening Balance':  grandOpen.toFixed(2),
      'Period Debits':    parseFloat(report.totals.total_debits  || '0').toFixed(2),
      'Period Credits':   parseFloat(report.totals.total_credits || '0').toFixed(2),
      'Closing Dr (BWP)': grandCloseDr.toFixed(2),
      'Closing Cr (BWP)': grandCloseCr.toFixed(2),
      'Closing Balance':  (grandCloseDr - grandCloseCr).toFixed(2),
    })

    return rows
  }

  const handleExport = async (format: 'csv' | 'xlsx' = 'csv') => {
    if (!report) return
    const rows = buildRows()
    if (rows.length === 0) return
    const filenameBase = `trial_balance_${fromDate}_to_${toDate}${companyId ? `_${selectedCompany?.code || 'co'}` : ''}`
    if (format === 'xlsx') {
      const xlsx = await import('xlsx')
      const headers = Object.keys(rows[0])
      const aoa = [headers, ...rows.map(r => headers.map(h => r[h]))]
      const ws = xlsx.utils.aoa_to_sheet(aoa)
      const wb = xlsx.utils.book_new()
      xlsx.utils.book_append_sheet(wb, ws, 'Trial Balance')
      xlsx.writeFile(wb, `${filenameBase}.xlsx`)
    } else {
      exportToCsv(rows, filenameBase)
    }
  }

  const isBalanced = report?.totals.balanced

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Trial Balance"
        breadcrumbs={[{ label: 'Reports', href: '/reports' }, { label: 'Trial Balance' }]}
        actions={
          report && (
            <div className="flex items-center gap-2">
              <Button
                variant="secondary"
                size="sm"
                leftIcon={<Download className="w-3.5 h-3.5" />}
                onClick={() => handleExport('csv')}
              >
                Export CSV
              </Button>
              <Button
                variant="secondary"
                size="sm"
                leftIcon={<Download className="w-3.5 h-3.5" />}
                onClick={() => handleExport('xlsx')}
              >
                Export XLSX
              </Button>
            </div>
          )
        }
      />

      <div className="flex-1 p-6 space-y-6">
        <ReportScopeBanner />
        <SmartUpload section="tb" onCommitted={handleGenerate} />

        {/* Controls — period-based per CFO directive 2026-05-21.
            "As of Date" is gone; TB is always a from→to window. */}
        <Card>
          <CardContent className="py-4 space-y-3">
            <div className="flex flex-wrap items-end gap-4">
              <div>
                <label className="block text-xs text-[#374151] font-medium mb-1">From</label>
                <input
                  type="date"
                  value={fromDate}
                  onChange={(e) => setFromDate(e.target.value)}
                  className="bg-white border border-[#D1D5DB] rounded-lg px-3 py-2 text-sm text-[#111827] focus:outline-none focus:border-[#F07F00] focus:ring-[3px] focus:ring-[rgba(240,127,0,0.1)] transition-all"
                />
              </div>
              <div>
                <label className="block text-xs text-[#374151] font-medium mb-1">To</label>
                <input
                  type="date"
                  value={toDate}
                  onChange={(e) => setToDate(e.target.value)}
                  className="bg-white border border-[#D1D5DB] rounded-lg px-3 py-2 text-sm text-[#111827] focus:outline-none focus:border-[#F07F00] focus:ring-[3px] focus:ring-[rgba(240,127,0,0.1)] transition-all"
                />
              </div>

              <div className="flex items-end gap-1">
                <Button variant="ghost" size="sm" onClick={() => applyPreset('FY25')}>FY25</Button>
                <Button variant="ghost" size="sm" onClick={() => applyPreset('FY26-9M')}>FY26 9M</Button>
                <Button variant="ghost" size="sm" onClick={() => applyPreset('FY26')}>FY26</Button>
                <Button variant="ghost" size="sm" onClick={() => applyPreset('YTD')}>YTD</Button>
              </div>

              <div className="flex-1 min-w-[220px]">
                <label className="block text-xs text-[#374151] font-medium mb-1">
                  Search account
                </label>
                <input
                  type="search"
                  placeholder="Code or name (e.g. 210002, Motor)"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  className="w-full bg-white border border-[#D1D5DB] rounded-lg px-3 py-2 text-sm text-[#111827] focus:outline-none focus:border-[#F07F00] focus:ring-[3px] focus:ring-[rgba(240,127,0,0.1)] transition-all"
                />
              </div>

              {report && (
                <div
                  className={cn(
                    'flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium',
                    isBalanced
                      ? 'bg-[#ECFDF5] border border-[#A7F3D0] text-[#059669]'
                      : 'bg-[#FEF2F2] border border-[#FEE2E2] text-[#DC2626]'
                  )}
                >
                  {isBalanced ? (
                    <CheckCircle className="w-4 h-4" />
                  ) : (
                    <AlertCircle className="w-4 h-4" />
                  )}
                  {isBalanced ? 'BALANCED' : 'NOT BALANCED'}
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

        {/* Report Table */}
        <Card>
          {loading ? (
            <CardContent className="p-0">
              <LoadingTable rows={15} cols={8} />
            </CardContent>
          ) : !report ? (
            <CardContent className="py-12 text-center">
              <Scale className="w-8 h-8 text-[#D1D5DB] mx-auto mb-2" />
              <p className="text-[#9CA3AF] text-sm">Select a period — TB reloads automatically</p>
            </CardContent>
          ) : (
            <CardContent className="p-0">
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="bg-[#F3F4F6] border-b-2 border-[#E5E7EB] sticky top-0">
                    <tr>
                      <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider w-24">
                        Code
                      </th>
                      <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">
                        Account
                      </th>
                      <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider hidden md:table-cell">
                        Type
                      </th>
                      <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider">
                        Opening
                      </th>
                      <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider">
                        Debits
                      </th>
                      <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider">
                        Credits
                      </th>
                      <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider">
                        Closing
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#E5E7EB] bg-white">
                    {report.accounts.length === 0 ? (
                      <tr>
                        <td colSpan={7} className="px-4 py-8 text-center text-[#9CA3AF] text-sm">
                          No accounts with activity
                        </td>
                      </tr>
                    ) : (
                      report.accounts.map((acc, idx) => (
                        <tr
                          key={idx}
                          className="table-row-alt hover:bg-[#FFF7ED] transition-colors cursor-pointer"
                          onClick={() => router.push(`/reports/general-ledger?account=${encodeURIComponent(acc.code)}`)}
                          title="Open this account in General Ledger to drill in and submit JEs for clearing"
                        >
                          <td className="px-4 py-2.5 font-mono text-xs text-[#9CA3AF]">{acc.code}</td>
                          <td className="px-4 py-2.5 text-[#111827] font-medium">{acc.name}</td>
                          <td className="px-4 py-2.5 text-[#9CA3AF] text-xs capitalize hidden md:table-cell">
                            {acc.account_type}
                          </td>
                          <td className="px-4 py-2.5 text-right font-mono-nums text-[#6B7280] text-xs">
                            {parseAmount(acc.opening_balance) !== 0
                              ? fmt(acc.opening_balance)
                              : '—'}
                          </td>
                          <td className="px-4 py-2.5 text-right font-mono-nums text-[#059669] text-xs">
                            {parseAmount(acc.period_debits) > 0
                              ? fmt(acc.period_debits)
                              : '—'}
                          </td>
                          <td className="px-4 py-2.5 text-right font-mono-nums text-[#DC2626] text-xs">
                            {parseAmount(acc.period_credits) > 0
                              ? fmt(acc.period_credits)
                              : '—'}
                          </td>
                          <td className="px-4 py-2.5 text-right font-mono-nums text-[#111827] font-medium">
                            {fmt(acc.closing_balance)}
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                  <tfoot className="bg-[#F3F4F6] border-t-2 border-[#E5E7EB]">
                    <tr>
                      <td colSpan={3} className="px-4 py-3 text-xs font-bold text-[#374151] uppercase tracking-wider">
                        TOTALS
                      </td>
                      <td className="px-4 py-3 text-right text-[#6B7280] text-xs">—</td>
                      <td className="px-4 py-3 text-right font-semibold font-mono-nums text-[#059669]">
                        {fmt(report.totals.total_debits)}
                      </td>
                      <td className="px-4 py-3 text-right font-semibold font-mono-nums text-[#DC2626]">
                        {fmt(report.totals.total_credits)}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <span
                          className={cn(
                            'px-2 py-0.5 rounded text-xs font-bold',
                            isBalanced
                              ? 'bg-[#ECFDF5] text-[#059669]'
                              : 'bg-[#FEF2F2] text-[#DC2626]'
                          )}
                        >
                          {isBalanced ? 'BALANCED' : 'UNBALANCED'}
                        </span>
                      </td>
                    </tr>
                  </tfoot>
                </table>
              </div>
            </CardContent>
          )}
        </Card>
      </div>
    </div>
  )
}
