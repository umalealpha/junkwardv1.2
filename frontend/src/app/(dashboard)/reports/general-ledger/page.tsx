'use client'

import { useEffect, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { getAccounts, getGeneralLedger, downloadGeneralLedgerExtractAll, fetchGeneralLedgerExtractAllCsv, getToken } from '@/lib/api'
import type { Account, GeneralLedgerReport } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { LoadingTable } from '@/components/ui/loading'
import { formatAmount, formatDate, parseAmount, exportToCsv, getFyStart, today, cn } from '@/lib/utils'
import { useNumberFormat } from '@/contexts/NumberFormatContext'
import { useCompany } from '@/contexts/CompanyContext'
import { Download, AlertCircle, BookOpen, Search, Eraser } from 'lucide-react'
import SmartUpload from '@/components/SmartUpload'

export default function GeneralLedgerPage() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const { mode } = useNumberFormat()
  const { selectedId: companyId, selected: selectedCompany } = useCompany()
  // CFO directive 2026-05-21: entity functional currency
  const currency = (selectedCompany?.base_currency || 'BWP') as string
  const fmt = (amount: string | number, c: string = currency) => formatAmount(amount, c, mode)
  const [accounts, setAccounts] = useState<Account[]>([])
  const [selectedAccount, setSelectedAccount] = useState('')
  const [fromDate, setFromDate] = useState(getFyStart())
  const [toDate, setToDate] = useState(today())
  const [report, setReport] = useState<GeneralLedgerReport | null>(null)
  const [loading, setLoading] = useState(false)
  const [accountsLoading, setAccountsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  // CFO directive 2026-05-19 — GL needs search across both the
  // account picker (600+ codes is unusable as a flat dropdown) and the
  // entry rows after the report loads.
  const [accountQuery, setAccountQuery] = useState('')
  const [entryQuery, setEntryQuery] = useState('')

  useEffect(() => {
    const token = getToken()
    if (!token) {
      router.replace('/login')
      return
    }
    loadAccounts()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // OMNI-QA-006 (Lakshmi QA 2026-06-09): drill-down URL carries ?account=<CODE>
  // (e.g. 100007) but the <select> options are keyed by account.id (UUID), so
  // setting selectedAccount to the raw code never matched an option — selector
  // stayed blank and the ledger never auto-loaded. Resolve the URL param (code
  // OR id) against the loaded account list, set the matching id, auto-Generate.
  const [autoGenDone, setAutoGenDone] = useState(false)
  useEffect(() => {
    if (!searchParams || autoGenDone || accounts.length === 0) return
    const want = (searchParams.get('account') || '').trim()
    if (!want) return
    const match = accounts.find((a) => a.id === want || (a.code || '') === want)
    if (match) {
      setSelectedAccount(match.id)
      setAutoGenDone(true)
      setTimeout(() => { handleGenerate(match.id) }, 0)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams, accounts, autoGenDone])

  // Re-fetch ledger when company picker changes (only if an account is selected)
  useEffect(() => {
    if (selectedAccount) handleGenerate()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [companyId])

  const loadAccounts = async () => {
    setAccountsLoading(true)
    try {
      // CFO directive 2026-05-19: GL must include DEACTIVATED accounts
      // that still carry posted JE lines, otherwise historical balances
      // are invisible. Page size bumped past the Alpha Direct CoA size
      // (~600 codes today + headroom for prefixed subsidiary accounts).
      const res = await getAccounts({ page_size: '2000', ordering: 'code' })
      setAccounts(res.results)
    } catch {
      setError('Failed to load accounts')
    } finally {
      setAccountsLoading(false)
    }
  }

  const handleGenerate = async (overrideAccount?: string) => {
    const acct = typeof overrideAccount === 'string' && overrideAccount ? overrideAccount : selectedAccount
    if (!acct) return
    setLoading(true)
    setError(null)
    try {
      const data = await getGeneralLedger(acct, fromDate, toDate, companyId)
      setReport(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to generate general ledger')
    } finally {
      setLoading(false)
    }
  }

  // ── Export ────────────────────────────────────────────────────────────────
  // CFO directive 2026-05-19: signs and running balance side were
  // unclear on the GL export. Adds explicit "Running Dr (BWP)" and
  // "Running Cr (BWP)" columns derived from the signed running_balance
  // + account natural side. Opening row prepended so the file is
  // self-contained (you can recompute the running balance from the
  // CSV alone). Subtotal of dr/cr in period appended. XLSX export
  // added alongside CSV.

  function gLNaturalSplit(value: string | number, accountType: string): { dr: number; cr: number } {
    const n = typeof value === 'string' ? parseFloat(value) : (value || 0)
    if (!isFinite(n) || n === 0) return { dr: 0, cr: 0 }
    const debitSide = accountType === 'asset' || accountType === 'expense'
    if (debitSide) {
      return n > 0 ? { dr: n, cr: 0 } : { dr: 0, cr: Math.abs(n) }
    }
    return n > 0 ? { dr: 0, cr: n } : { dr: Math.abs(n), cr: 0 }
  }

  function gLBuildRows(): Array<Record<string, any>> {
    if (!report) return []
    const at = report.account.account_type
    const rows: Array<Record<string, any>> = []

    // Opening row
    const openingSplit = gLNaturalSplit(report.opening_balance ?? 0, at)
    rows.push({
      Date: report.from_date,
      'Entry #': '— OPENING —',
      Description: `Opening balance for ${report.account.code} ${report.account.name}`,
      'Journal Type': '',
      'Debit (BWP)': '',
      'Credit (BWP)': '',
      'Running Dr (BWP)': openingSplit.dr ? openingSplit.dr.toFixed(2) : '',
      'Running Cr (BWP)': openingSplit.cr ? openingSplit.cr.toFixed(2) : '',
      'Running Balance (signed)': parseFloat(String(report.opening_balance ?? '0')).toFixed(2),
    })

    let totDr = 0, totCr = 0
    for (const line of report.lines) {
      const dr = parseFloat(line.debit  || '0') || 0
      const cr = parseFloat(line.credit || '0') || 0
      const running = parseFloat(line.running_balance || '0') || 0
      const split = gLNaturalSplit(running, at)
      totDr += dr
      totCr += cr
      rows.push({
        Date: line.date,
        'Entry #': line.entry_number,
        Description: line.description,
        'Journal Type': line.journal_type,
        'Debit (BWP)':  dr ? dr.toFixed(2) : '',
        'Credit (BWP)': cr ? cr.toFixed(2) : '',
        'Running Dr (BWP)': split.dr ? split.dr.toFixed(2) : '',
        'Running Cr (BWP)': split.cr ? split.cr.toFixed(2) : '',
        'Running Balance (signed)': running.toFixed(2),
      })
    }

    // Period totals + closing row
    const closingSigned = report.lines.length
      ? parseFloat(report.lines[report.lines.length - 1].running_balance || '0') || 0
      : parseFloat(String(report.opening_balance ?? '0')) || 0
    const closingSplit = gLNaturalSplit(closingSigned, at)
    rows.push({
      Date: '', 'Entry #': '', Description: 'Period totals',
      'Journal Type': '',
      'Debit (BWP)':  totDr.toFixed(2),
      'Credit (BWP)': totCr.toFixed(2),
      'Running Dr (BWP)': '', 'Running Cr (BWP)': '',
      'Running Balance (signed)': '',
    })
    rows.push({
      Date: report.to_date,
      'Entry #': '— CLOSING —',
      Description: `Closing balance for ${report.account.code} ${report.account.name}`,
      'Journal Type': '',
      'Debit (BWP)':  '', 'Credit (BWP)': '',
      'Running Dr (BWP)': closingSplit.dr ? closingSplit.dr.toFixed(2) : '',
      'Running Cr (BWP)': closingSplit.cr ? closingSplit.cr.toFixed(2) : '',
      'Running Balance (signed)': closingSigned.toFixed(2),
    })

    return rows
  }

  // CFO directive 2026-05-21: download every posted JE line for the
  // period — no account filter. Streams from the backend so a 100k-line
  // ADIC dump doesn't OOM the browser tab.
  // CSV path = pure stream, fast, file opens in Excel natively.
  // XLSX path = pull the CSV blob, convert in-browser via SheetJS.
  const [extractAllBusy, setExtractAllBusy] = useState<false | 'csv' | 'xlsx'>(false)
  const handleExtractAll = async (format: 'csv' | 'xlsx' = 'csv') => {
    setExtractAllBusy(format)
    setError(null)
    try {
      if (format === 'csv') {
        await downloadGeneralLedgerExtractAll(fromDate, toDate, companyId)
      } else {
        // Pull CSV stream + convert to XLSX in-browser.
        const csv = await fetchGeneralLedgerExtractAllCsv(fromDate, toDate, companyId)
        const xlsx = await import('xlsx')
        // SheetJS reads CSV directly.
        const wb = xlsx.read(csv, { type: 'string' })
        // Rename the sheet for clarity.
        const oldName = wb.SheetNames[0]
        wb.Sheets['General Ledger'] = wb.Sheets[oldName]
        delete wb.Sheets[oldName]
        wb.SheetNames = ['General Ledger']
        const coCode = selectedCompany?.code || 'all'
        xlsx.writeFile(wb, `general_ledger_all_${coCode}_${fromDate}_to_${toDate}.xlsx`)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Extract all failed')
    } finally {
      setExtractAllBusy(false)
    }
  }

  const handleExport = async (format: 'csv' | 'xlsx' = 'csv') => {
    if (!report) return
    const rows = gLBuildRows()
    if (rows.length === 0) return
    const acctName = report.account.code + '_' + report.account.name.replace(/\s+/g, '_')
    const filenameBase = `general_ledger_${acctName}_${fromDate}_${toDate}`
    if (format === 'xlsx') {
      const xlsx = await import('xlsx')
      const headers = Object.keys(rows[0])
      const aoa = [headers, ...rows.map(r => headers.map(h => r[h]))]
      const ws = xlsx.utils.aoa_to_sheet(aoa)
      const wb = xlsx.utils.book_new()
      xlsx.utils.book_append_sheet(wb, ws, 'General Ledger')
      xlsx.writeFile(wb, `${filenameBase}.xlsx`)
    } else {
      exportToCsv(rows, filenameBase)
    }
  }

  const inputCls =
    'bg-white border border-[#D1D5DB] rounded-lg px-3 py-2 text-sm text-[#111827] focus:outline-none focus:border-[#F07F00] focus:ring-[3px] focus:ring-[rgba(240,127,0,0.1)] transition-all'

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="General Ledger"
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
        {/* Extract-all banner — CFO directive 2026-05-21.
            Dumps every posted JE line across every account for the
            chosen period. NO account filter required. Streams from
            the backend so 100k+ lines don't OOM the tab. */}
        <Card className="border-[#F07F00] bg-gradient-to-r from-[#FFF7ED] to-white">
          <CardContent className="py-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <div className="text-sm font-semibold text-[#111827]">
                  Extract entire General Ledger
                </div>
                <div className="text-xs text-[#374151] mt-0.5">
                  Every posted JE line, every account, for the selected period
                  ({fromDate} → {toDate}){selectedCompany ? ` · ${selectedCompany.code}` : ''}.
                  No account filter needed.
                </div>
              </div>
              <div className="flex items-center gap-2">
                <Button
                  variant="primary"
                  size="md"
                  leftIcon={<Download className="w-4 h-4" />}
                  onClick={() => handleExtractAll('csv')}
                  loading={extractAllBusy === 'csv'}
                  disabled={!!extractAllBusy}
                  title="Stream every posted JE line as CSV (opens in Excel)."
                >
                  Extract all → CSV
                </Button>
                <Button
                  variant="primary"
                  size="md"
                  leftIcon={<Download className="w-4 h-4" />}
                  onClick={() => handleExtractAll('xlsx')}
                  loading={extractAllBusy === 'xlsx'}
                  disabled={!!extractAllBusy}
                  title="Stream every posted JE line and convert to .xlsx in the browser."
                >
                  Extract all → Excel (XLSX)
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>

        <SmartUpload section="gl" onCommitted={() => handleGenerate()} />
        {/* Controls */}
        <Card>
          <CardContent className="py-4">
            <div className="flex flex-wrap items-end gap-4">
              <div className="min-w-[260px]">
                <label className="block text-xs text-[#374151] font-medium mb-1">
                  Account search
                </label>
                <input
                  type="search"
                  placeholder="Code or name..."
                  value={accountQuery}
                  onChange={(e) => setAccountQuery(e.target.value)}
                  className={cn(inputCls, 'w-full mb-2')}
                  disabled={accountsLoading}
                />
                <select
                  value={selectedAccount}
                  onChange={(e) => setSelectedAccount(e.target.value)}
                  className={cn(inputCls, 'w-full')}
                  disabled={accountsLoading}
                >
                  <option value="">Select an account...</option>
                  {(() => {
                    const q = accountQuery.trim().toLowerCase()
                    const filtered = q
                      ? accounts.filter(
                          (a) =>
                            (a.code || '').toLowerCase().includes(q) ||
                            (a.name || '').toLowerCase().includes(q),
                        )
                      : accounts
                    return filtered.map((acc) => (
                      <option key={acc.id} value={acc.id}>
                        {acc.code} — {acc.name}
                      </option>
                    ))
                  })()}
                </select>
              </div>
              <div>
                <label className="block text-xs text-[#374151] font-medium mb-1">From</label>
                <input
                  type="date"
                  value={fromDate}
                  onChange={(e) => setFromDate(e.target.value)}
                  className={inputCls}
                />
              </div>
              <div>
                <label className="block text-xs text-[#374151] font-medium mb-1">To</label>
                <input
                  type="date"
                  value={toDate}
                  onChange={(e) => setToDate(e.target.value)}
                  className={inputCls}
                />
              </div>
              <Button
                variant="primary"
                size="md"
                onClick={() => handleGenerate()}
                loading={loading}
                disabled={!selectedAccount}
              >
                Generate
              </Button>
              <div className="flex-1 min-w-[220px]">
                <label className="block text-xs text-[#374151] font-medium mb-1">
                  Filter rows
                </label>
                <input
                  type="search"
                  placeholder="Description, entry #, counterparty..."
                  value={entryQuery}
                  onChange={(e) => setEntryQuery(e.target.value)}
                  className={cn(inputCls, 'w-full')}
                />
              </div>
            </div>
          </CardContent>
        </Card>

        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-xl p-3 flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-[#DC2626]" />
            <p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}

        {/* Account summary header */}
        {report && !loading && (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div className="bg-white border border-[#E5E7EB] rounded-xl p-4 text-center">
              <p className="text-xs text-[#6B7280] uppercase tracking-wider font-semibold">Opening Balance</p>
              <p className="text-lg font-bold text-[#0B0B3B] font-mono-nums mt-1">
                {fmt(report.opening_balance)}
              </p>
            </div>
            <div className="bg-white border border-[#E5E7EB] rounded-xl p-4 text-center">
              <p className="text-xs text-[#6B7280] uppercase tracking-wider font-semibold">Period Movement</p>
              <p className={cn(
                'text-lg font-bold font-mono-nums mt-1',
                parseAmount(report.totals.total_debits) >= parseAmount(report.totals.total_credits)
                  ? 'text-[#2563EB]'
                  : 'text-[#CC6C00]'
              )}>
                Dr {fmt(report.totals.total_debits)} / Cr {fmt(report.totals.total_credits)}
              </p>
            </div>
            <div className="bg-white border border-[#E5E7EB] rounded-xl p-4 text-center">
              <p className="text-xs text-[#6B7280] uppercase tracking-wider font-semibold">Closing Balance</p>
              <p className="text-lg font-bold text-[#059669] font-mono-nums mt-1">
                {fmt(report.totals.closing_balance)}
              </p>
            </div>
          </div>
        )}

        {/* Detail Table */}
        <Card>
          <CardContent className="p-0">
            {loading ? (
              <LoadingTable rows={10} cols={7} />
            ) : !report ? (
              <div className="py-16 text-center">
                <BookOpen className="w-8 h-8 text-[#D1D5DB] mx-auto mb-3" />
                <p className="text-[#6B7280] text-sm font-medium">
                  {selectedAccount ? 'Click Generate to view the ledger' : 'Select an account to begin'}
                </p>
              </div>
            ) : report.lines.length === 0 ? (
              <div className="py-16 text-center">
                <Search className="w-8 h-8 text-[#D1D5DB] mx-auto mb-3" />
                <p className="text-[#6B7280] text-sm font-medium">No transactions in this period</p>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm border-collapse">
                  <thead className="bg-[#F3F4F6] border-b-2 border-[#E5E7EB]">
                    <tr>
                      <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Date</th>
                      <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Entry #</th>
                      <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Description</th>
                      <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider hidden md:table-cell">Type</th>
                      <th className="px-4 py-3 text-center text-xs font-semibold text-[#374151] uppercase tracking-wider" title="Related Party (IAS 24)">RP</th>
                      <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider">Debit</th>
                      <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider">Credit</th>
                      <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider">Balance</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#E5E7EB] bg-white">
                    {(() => {
                      const q = entryQuery.trim().toLowerCase()
                      const linesToShow = q
                        ? report.lines.filter((l: any) =>
                            [l.description, l.entry_number, l.contact_name, l.journal_type]
                              .filter(Boolean)
                              .some((v: any) =>
                                String(v).toLowerCase().includes(q),
                              ),
                          )
                        : report.lines
                      return linesToShow.map((line, idx) => {
                      const rp = line.is_related_party_line || line.is_related_party_entry
                      return (
                      <tr key={idx} className={`table-row-alt hover:bg-[#FFF7ED] transition-colors ${rp ? 'bg-[#FFFBEB]' : ''}`}>
                        <td className="px-4 py-2.5 text-[#6B7280] text-xs">{formatDate(line.date)}</td>
                        <td className="px-4 py-2.5 font-mono text-xs text-[#CC6C00] font-medium">
                          <span className="inline-flex items-center gap-1.5">
                            {line.entry_number}
                            {/* CFO directive 2026-05-26 — let makers submit
                                this JE for clearing straight from the GL.
                                Voucher-clearing reads ?number= and pre-picks
                                the JE if it can match exactly. */}
                            <button
                              type="button"
                              onClick={() => router.push(
                                `/banking/voucher-clearing?number=${encodeURIComponent(line.entry_number)}`,
                              )}
                              className="opacity-60 hover:opacity-100 transition"
                              title="Submit this JE for maker-checker clearing"
                            >
                              <Eraser className="w-3 h-3" />
                            </button>
                          </span>
                        </td>
                        <td className="px-4 py-2.5 text-[#374151] max-w-[240px] truncate" title={line.contact_name ? `Counterparty: ${line.contact_name}` : undefined}>
                          {line.description || '—'}
                          {line.contact_name && <span className="ml-1 text-[#9CA3AF] text-xs">· {line.contact_name}</span>}
                        </td>
                        <td className="px-4 py-2.5 text-[#6B7280] text-xs capitalize hidden md:table-cell">
                          {line.journal_type?.replace(/_/g, ' ')}
                        </td>
                        <td className="px-4 py-2.5 text-center">
                          {rp ? (
                            <span className="inline-block px-1.5 py-0.5 rounded text-xs font-bold bg-[#FFFBEB] text-[#92400E] border border-[#FDE68A]"
                                  title={line.is_related_party_line ? 'Line flagged as related-party' : 'Entry-level related-party'}>
                              RP
                            </span>
                          ) : <span className="text-[#D1D5DB]">—</span>}
                        </td>
                        <td className="px-4 py-2.5 text-right font-mono-nums text-[#2563EB]">
                          {parseAmount(line.debit) > 0 ? fmt(line.debit) : ''}
                        </td>
                        <td className="px-4 py-2.5 text-right font-mono-nums text-[#CC6C00]">
                          {parseAmount(line.credit) > 0 ? fmt(line.credit) : ''}
                        </td>
                        <td className="px-4 py-2.5 text-right font-mono-nums font-medium text-[#111827]">
                          {fmt(line.running_balance)}
                        </td>
                      </tr>
                    )})
                    })()}
                  </tbody>
                  <tfoot className="bg-[#F9FAFB] border-t-2 border-[#E5E7EB]">
                    <tr>
                      <td colSpan={5} className="px-4 py-3 text-xs font-bold text-[#6B7280] uppercase">Totals</td>
                      <td className="px-4 py-3 text-right font-bold font-mono-nums text-[#2563EB]">
                        {fmt(report.totals.total_debits)}
                      </td>
                      <td className="px-4 py-3 text-right font-bold font-mono-nums text-[#CC6C00]">
                        {fmt(report.totals.total_credits)}
                      </td>
                      <td className="px-4 py-3 text-right font-bold font-mono-nums text-[#0B0B3B]">
                        {fmt(report.totals.closing_balance)}
                      </td>
                    </tr>
                  </tfoot>
                </table>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
