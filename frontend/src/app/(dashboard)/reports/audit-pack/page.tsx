'use client'

/**
 * /reports/audit-pack
 *
 * Auditor-ready bundle: TB + P&L + Balance Sheet + Posted JE listing,
 * scoped optionally by company. UI lets the user pick a date range,
 * then download the bundle as PDF (consumes
 * /api/v1/reports/audit-pack/pdf/) or preview the JSON summary.
 */

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  getAuditPackJson, downloadAuditPackPdf, getToken,
} from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { ReportScopeBanner } from '@/components/ReportScopeBanner'
import { useCompany } from '@/contexts/CompanyContext'
import { useNumberFormat } from '@/contexts/NumberFormatContext'
import { formatAmount, parseAmount, getFyStart, today, cn, formatDate } from '@/lib/utils'
import { Download, AlertCircle, FileText, Loader2, CheckCircle2, Calculator, ArrowUpRight } from 'lucide-react'
import Link from 'next/link'

export default function AuditPackPage() {
  const router = useRouter()
  const { mode } = useNumberFormat()
  const fmt = (a: string|number, c='BWP') => formatAmount(a, c, mode)
  const { selectedId: companyId, selected: selectedCompany } = useCompany()

  const [fromDate, setFromDate] = useState(getFyStart())
  const [toDate, setToDate] = useState(today())
  const [summary, setSummary] = useState<any>(null)
  const [loadingSummary, setLoadingSummary] = useState(false)
  const [downloading, setDownloading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  useEffect(() => {
    if (!getToken()) router.replace('/login')
  }, [router])

  const loadSummary = async () => {
    setError(null); setSuccess(null); setLoadingSummary(true)
    try {
      const data = await getAuditPackJson(fromDate, toDate, companyId)
      setSummary(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load audit pack')
    } finally { setLoadingSummary(false) }
  }

  const handleDownload = async () => {
    setError(null); setSuccess(null); setDownloading(true)
    try {
      const filename = await downloadAuditPackPdf(fromDate, toDate, companyId)
      setSuccess(`Downloaded ${filename}`)
      setTimeout(() => setSuccess(null), 5000)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Download failed')
    } finally { setDownloading(false) }
  }

  const tb = summary?.trial_balance
  const pl = summary?.profit_loss

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Audit Pack"
        breadcrumbs={[{ label: 'Reports', href: '/reports' }, { label: 'Audit Pack' }]}
        actions={
          <Button variant="accent" size="sm" leftIcon={<Download className="w-3.5 h-3.5" />} onClick={handleDownload} disabled={downloading}>
            {downloading ? 'Generating…' : 'Download PDF'}
          </Button>
        }
      />

      <div className="flex-1 p-6 max-w-5xl mx-auto w-full space-y-5">
        <ReportScopeBanner />

        <div className="flex items-start gap-3">
          <FileText className="w-5 h-5 mt-0.5" style={{ color: '#CC6C00' }} />
          <div>
            <h1 className="text-[20px] font-bold text-[#0B0B3B]">Audit Pack</h1>
            <p className="text-sm text-[#6B7280] mt-1">
              Auditor-ready bundle: trial balance + profit &amp; loss + balance sheet + every
              posted journal entry in the period, with reviewer sign-off page. Renders as a
              single PDF.
            </p>
          </div>
        </div>

        {success && (
          <div className="bg-[#ECFDF5] border border-[#A7F3D0] rounded-lg p-3 flex items-center gap-2">
            <CheckCircle2 className="w-4 h-4 text-[#059669]" /><p className="text-[#059669] text-sm">{success}</p>
          </div>
        )}
        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-[#DC2626]" /><p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}

        <Card>
          <CardHeader><CardTitle>Period</CardTitle></CardHeader>
          <CardContent>
            <div className="flex flex-wrap items-end gap-4">
              <div>
                <label className="block text-xs text-[#374151] font-medium mb-1">From</label>
                <input type="date" value={fromDate} onChange={(e) => setFromDate(e.target.value)} className="bg-white border border-[#D1D5DB] rounded-lg px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="block text-xs text-[#374151] font-medium mb-1">To</label>
                <input type="date" value={toDate} onChange={(e) => setToDate(e.target.value)} className="bg-white border border-[#D1D5DB] rounded-lg px-3 py-2 text-sm" />
              </div>
              <Button variant="primary" size="md" onClick={loadSummary} loading={loadingSummary} disabled={!fromDate || !toDate}>
                Preview Contents
              </Button>
              <Button variant="accent" size="md" leftIcon={<Download className="w-4 h-4" />} onClick={handleDownload} loading={downloading} disabled={!fromDate || !toDate}>
                Download PDF
              </Button>
            </div>
            <p className="text-[11px] text-[#9CA3AF] mt-3">
              Scope follows the Company picker in the topbar. Currently:
              {' '}<span className="font-medium text-[#374151]">{selectedCompany ? `${selectedCompany.code} — ${selectedCompany.name}` : 'All companies (consolidated)'}</span>.
            </p>
          </CardContent>
        </Card>

        {/* TAX-006: companion pack link */}
        <Card className="border-[#FFD7B5] bg-[#FFF7ED]">
          <CardContent className="py-4 flex items-center justify-between gap-4">
            <div className="flex items-start gap-3">
              <Calculator className="w-5 h-5 text-[#F07F00] flex-shrink-0 mt-0.5" />
              <div>
                <p className="text-sm font-semibold text-[#7C2D12]">
                  Tax Pack — IAS 12 Reconciliation
                </p>
                <p className="text-xs text-[#7C2D12]/80 mt-0.5">
                  Companion to the audit pack. Effective tax rate, statutory-rate
                  reconciliation, current vs deferred tax, withholding tax.
                </p>
              </div>
            </div>
            <Link
              href="/reports/tax-reconciliation"
              className="inline-flex items-center gap-1 text-sm font-semibold text-[#F07F00] hover:underline whitespace-nowrap"
            >
              Open Tax Pack <ArrowUpRight className="w-4 h-4" />
            </Link>
          </CardContent>
        </Card>

        {summary && (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            <SummaryTile label="Period" value={`${formatDate(summary.from_date)} → ${formatDate(summary.to_date)}`} />
            <SummaryTile label="JEs in pack" value={String(summary.journal_entry_count || 0)} />
            <SummaryTile label="TB Debits" value={fmt(tb?.totals?.total_debits || '0')} accent={tb?.totals?.balanced ? '#059669' : '#DC2626'} />
            <SummaryTile label="TB Credits" value={fmt(tb?.totals?.total_credits || '0')} accent={tb?.totals?.balanced ? '#059669' : '#DC2626'} />
            <SummaryTile label="Revenue" value={fmt(pl?.revenue?.total || '0')} accent="#059669" />
            <SummaryTile label="Net Profit" value={fmt(pl?.net_profit || '0')} accent={parseAmount(pl?.net_profit || '0') >= 0 ? '#059669' : '#DC2626'} />
            <SummaryTile label="Cost of Insurance" value={fmt(pl?.cost_of_insurance?.total || '0')} accent="#D97706" />
            <SummaryTile label="Operating Expenses" value={fmt(pl?.operating_expenses?.total || '0')} accent="#D97706" />
          </div>
        )}

        {summary && (
          <Card>
            <CardHeader><CardTitle>Posted Journal Entries (preview — first 25)</CardTitle></CardHeader>
            <CardContent className="p-0">
              {summary.journal_entries?.length === 0 ? (
                <p className="text-sm text-[#9CA3AF] p-6">No posted entries in this period.</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead className="bg-[#F9FAFB] border-b border-[#E5E7EB]">
                      <tr>
                        <th className="px-3 py-2 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Date</th>
                        <th className="px-3 py-2 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Entry #</th>
                        <th className="px-3 py-2 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Type</th>
                        <th className="px-3 py-2 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Description</th>
                        <th className="px-3 py-2 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">RP?</th>
                        <th className="px-3 py-2 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">By</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[#E5E7EB] bg-white">
                      {summary.journal_entries.slice(0, 25).map((je: any) => (
                        <tr key={je.entry_number}>
                          <td className="px-3 py-2 text-[#374151] text-xs">{je.entry_date}</td>
                          <td className="px-3 py-2 font-mono text-xs text-[#CC6C00] font-semibold">{je.entry_number}</td>
                          <td className="px-3 py-2 text-[#6B7280] text-xs">{je.journal_type}</td>
                          <td className="px-3 py-2 text-[#374151] text-xs max-w-[400px] truncate">{je.description}</td>
                          <td className="px-3 py-2 text-xs">{je.is_related_party && <span className="text-[#7C3AED] font-semibold">Yes</span>}</td>
                          <td className="px-3 py-2 text-[#6B7280] text-xs">{je.created_by}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {summary.journal_entries?.length > 25 && (
                <p className="text-[11px] text-[#9CA3AF] p-3 border-t border-[#E5E7EB]">
                  Showing 25 of {summary.journal_entries.length}. The full listing appears in the PDF.
                </p>
              )}
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  )
}

function SummaryTile({ label, value, accent = '#0B0B3B' }: { label: string; value: string; accent?: string }) {
  return (
    <div className="bg-white border border-[#E5E7EB] rounded-xl p-3">
      <p className="text-[10px] font-semibold text-[#6B7280] uppercase tracking-wider">{label}</p>
      <p className="text-base font-bold mt-1 font-mono-nums" style={{ color: accent }}>{value}</p>
    </div>
  )
}
