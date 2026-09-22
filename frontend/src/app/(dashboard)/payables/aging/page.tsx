'use client'

/**
 * /payables/aging — PAY-002 AP Aging (CFO directive 2026-05-27).
 *
 * Per-vendor summary across 4 buckets (0-30 / 31-60 / 61-90 / 90+),
 * grand total, filters (vendor / currency / as-of), Excel + PDF export,
 * 90+ flagged red, and a Balance-Sheet reconciliation check (variance
 * must be zero — red if not).
 *
 * Reuses the existing /reports/ap-aging/ endpoint (getApAging), which now
 * returns a `reconciliation` block. Does not rebuild the report engine —
 * this is the per-vendor summary + reconciliation surface the spec asked
 * for, distinct from the invoice-level /reports/ap-aging page.
 */
import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { getApAging, downloadReportXlsx, getToken, apiFetch } from '@/lib/api'
import type { AgingReport, AgingCustomer } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { LoadingTable } from '@/components/ui/loading'
import { formatAmount, parseAmount, today, cn } from '@/lib/utils'
import { useNumberFormat } from '@/contexts/NumberFormatContext'
import { useCompany } from '@/contexts/CompanyContext'
import { Download, Printer, AlertCircle, CheckCircle2, Clock, Upload } from 'lucide-react'

// Directive buckets. Backend splits 90+ into days_91_120 + over_120 — merge
// both into the single "90+" column here.
const COL_30 = 'current'
const COL_60 = 'days_31_60'
const COL_90 = 'days_61_90'
const COL_90P = ['days_91_120', 'over_120']

function bucketBalance(cust: AgingCustomer, keys: string[]): number {
  return cust.invoices
    .filter((inv) => keys.includes(inv.age_bucket))
    .reduce((s, inv) => s + parseAmount(inv.balance_due), 0)
}

interface VendorRow {
  name: string
  b30: number
  b60: number
  b90: number
  b90p: number
  total: number
}

export default function ApAgingSummaryPage() {
  const router = useRouter()
  const { mode } = useNumberFormat()
  const { selectedId: companyId, selected: selectedCompany } = useCompany()
  const currency = (selectedCompany?.base_currency || 'BWP') as string
  const fmt = (a: string | number) => formatAmount(a, currency, mode)

  const [asOf, setAsOf] = useState(today())
  const [vendorFilter, setVendorFilter] = useState('')
  const [currencyFilter, setCurrencyFilter] = useState('')
  const [report, setReport] = useState<AgingReport | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Uploaded age-analysis snapshot (CFO 2026-07-13) — Finance uploads the
  // maintained schedule (e.g. as at 30 June) so the report shows real AP even
  // before opening bills are loaded.
  interface SnapLine { vendor: string; b_0_30: string; b_31_60: string; b_61_90: string; b_90_plus: string; total: string }
  interface Snap { found: boolean; snapshot?: { as_of: string; vendor_count: number; grand_total: string; uploaded_by: string; source_filename: string; lines: SnapLine[] } }
  const [snap, setSnap] = useState<Snap | null>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadMsg, setUploadMsg] = useState<string | null>(null)

  const fetchSnapshot = async (d: string) => {
    try {
      const q = new URLSearchParams({ as_of: d }); if (companyId) q.set('company', String(companyId))
      setSnap(await apiFetch<Snap>(`/reports/ap-aging/snapshot/?${q.toString()}`))
    } catch { setSnap(null) }
  }

  async function onUploadFile(file: File) {
    setUploading(true); setUploadMsg(null); setError(null)
    try {
      const form = new FormData(); form.append('file', file); form.append('as_of', asOf)
      if (companyId) form.append('company', String(companyId))
      const r = await apiFetch<{ vendor_count: number; grand_total: string }>(
        '/reports/ap-aging/upload/', { method: 'POST', body: form })
      setUploadMsg(`Loaded ${r.vendor_count} vendors — total ${fmt(r.grand_total)} as at ${asOf}.`)
      await fetchSnapshot(asOf)
    } catch (e) { setError(e instanceof Error ? e.message : 'Upload failed') }
    finally { setUploading(false) }
  }

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    void generate()
    void fetchSnapshot(asOf)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [companyId])

  async function generate() {
    setLoading(true); setError(null)
    try {
      setReport(await getApAging(asOf, companyId))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load AP aging')
    } finally {
      setLoading(false)
    }
    void fetchSnapshot(asOf)
  }

  // Distinct currencies present (for the currency filter dropdown).
  const currencies = useMemo(() => {
    if (!report) return [] as string[]
    const s = new Set<string>()
    report.customers.forEach((c) => c.invoices.forEach((i) => {
      const cc = (i as { currency?: string }).currency
      if (cc) s.add(cc)
    }))
    return Array.from(s).sort()
  }, [report])

  const rows: VendorRow[] = useMemo(() => {
    if (!report) return []
    let custs = report.customers
    if (vendorFilter.trim()) {
      const q = vendorFilter.trim().toLowerCase()
      custs = custs.filter((c) => c.contact_name.toLowerCase().includes(q))
    }
    const filterInvCurrency = (c: AgingCustomer): AgingCustomer => {
      if (!currencyFilter) return c
      return { ...c, invoices: c.invoices.filter(
        (i) => (i as { currency?: string }).currency === currencyFilter) }
    }
    return custs
      .map(filterInvCurrency)
      .map((c) => {
        const b30 = bucketBalance(c, [COL_30])
        const b60 = bucketBalance(c, [COL_60])
        const b90 = bucketBalance(c, [COL_90])
        const b90p = bucketBalance(c, COL_90P)
        return { name: c.contact_name, b30, b60, b90, b90p, total: b30 + b60 + b90 + b90p }
      })
      .filter((r) => r.total !== 0)
      .sort((a, b) => b.total - a.total)
  }, [report, vendorFilter, currencyFilter])

  const grand = useMemo(() => rows.reduce(
    (g, r) => ({
      b30: g.b30 + r.b30, b60: g.b60 + r.b60, b90: g.b90 + r.b90,
      b90p: g.b90p + r.b90p, total: g.total + r.total,
    }),
    { b30: 0, b60: 0, b90: 0, b90p: 0, total: 0 },
  ), [rows])

  const recon = report?.reconciliation

  function exportCsv() {
    const header = ['Vendor', '0-30', '31-60', '61-90', '90+', 'Total Outstanding']
    const lines = rows.map((r) =>
      [r.name, r.b30, r.b60, r.b90, r.b90p, r.total].join(','))
    lines.push(['GRAND TOTAL', grand.b30, grand.b60, grand.b90, grand.b90p, grand.total].join(','))
    const blob = new Blob([[header.join(','), ...lines].join('\n')], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a'); a.href = url
    a.download = `ap_aging_summary_${asOf}.csv`; a.click(); URL.revokeObjectURL(url)
  }

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="AP Aging"
        breadcrumbs={[{ label: 'Payables', href: '/bills' }, { label: 'AP Aging' }]}
        actions={report && (
          <div className="flex items-center gap-2 print:hidden">
            <Button variant="secondary" size="sm" leftIcon={<Download className="w-3.5 h-3.5" />} onClick={exportCsv}>CSV</Button>
            <Button variant="secondary" size="sm" leftIcon={<Download className="w-3.5 h-3.5" />}
              onClick={() => downloadReportXlsx('ap_aging', { as_of: asOf, company: companyId ?? undefined })}>Excel</Button>
            <Button variant="secondary" size="sm" leftIcon={<Printer className="w-3.5 h-3.5" />} onClick={() => window.print()}>PDF</Button>
          </div>
        )}
      />

      <div className="flex-1 p-6 space-y-6">
        {/* Filters */}
        <Card className="print:hidden">
          <CardContent className="py-4">
            <div className="flex flex-wrap items-end gap-4">
              <div>
                <label className="block text-xs text-[#374151] font-medium mb-1">As of</label>
                <input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)}
                  className="bg-white border border-[#D1D5DB] rounded-lg px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="block text-xs text-[#374151] font-medium mb-1">Vendor</label>
                <input type="text" placeholder="Filter…" value={vendorFilter}
                  onChange={(e) => setVendorFilter(e.target.value)}
                  className="bg-white border border-[#D1D5DB] rounded-lg px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="block text-xs text-[#374151] font-medium mb-1">Currency</label>
                <select value={currencyFilter} onChange={(e) => setCurrencyFilter(e.target.value)}
                  className="bg-white border border-[#D1D5DB] rounded-lg px-3 py-2 text-sm">
                  <option value="">All</option>
                  {currencies.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              </div>
              <Button variant="primary" size="md" onClick={generate} loading={loading}>Generate</Button>
              <label className="inline-flex items-center gap-1.5 text-sm px-3 py-2 rounded-lg border border-[#0D1B2A]/25 bg-white text-[#0D1B2A] cursor-pointer hover:bg-[#F9FAFB] font-medium">
                <Upload className="w-3.5 h-3.5" />
                {uploading ? 'Uploading…' : 'Upload age analysis'}
                <input type="file" accept=".xlsx,.xlsm,.csv" className="hidden" disabled={uploading}
                  onChange={(e) => { const f = e.target.files?.[0]; if (f) void onUploadFile(f); e.currentTarget.value = '' }} />
              </label>
            </div>
            <p className="mt-2 text-xs text-[#6B7280]">
              Upload the maintained AP age analysis (Excel/CSV: Vendor + 0-30, 31-60, 61-90, 90+ columns, or Vendor + Total) for the selected “As of” date.
            </p>
          </CardContent>
        </Card>

        {uploadMsg && (
          <div className="bg-[#ECFDF5] border border-[#A7F3D0] rounded-xl p-3 flex items-center gap-2 print:hidden">
            <CheckCircle2 className="w-4 h-4 text-[#059669]" />
            <p className="text-[#065F46] text-sm">{uploadMsg}</p>
          </div>
        )}

        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-xl p-3 flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-[#DC2626]" />
            <p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}

        {/* Uploaded age-analysis snapshot (CFO 2026-07-13) */}
        {snap?.found && snap.snapshot && (
          <Card className="overflow-hidden">
            <CardContent className="p-0">
              <div className="flex items-center justify-between px-5 pt-4 pb-3 flex-wrap gap-2">
                <div>
                  <h3 className="text-[11px] font-bold uppercase tracking-[0.14em] text-[#0D1B2A]">
                    Uploaded age analysis · as at {snap.snapshot.as_of}
                  </h3>
                  <p className="text-[11px] text-[#6B7280] mt-0.5">
                    {snap.snapshot.vendor_count} vendors
                    {snap.snapshot.uploaded_by ? ` · uploaded by ${snap.snapshot.uploaded_by}` : ''}
                    {snap.snapshot.source_filename ? ` · ${snap.snapshot.source_filename}` : ''}
                  </p>
                </div>
                <span className="text-[11px] px-2.5 py-1 rounded-full bg-[#FDEFD7] text-[#9A640A] font-semibold">Uploaded schedule</span>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm" style={{ fontVariantNumeric: 'tabular-nums' }}>
                  <thead>
                    <tr className="bg-[#0D1B2A] text-white/90 text-[10.5px] uppercase tracking-[0.10em]">
                      <th className="text-left font-semibold px-5 py-3">Vendor</th>
                      <th className="text-right font-semibold px-4 py-3">0–30</th>
                      <th className="text-right font-semibold px-4 py-3">31–60</th>
                      <th className="text-right font-semibold px-4 py-3">61–90</th>
                      <th className="text-right font-semibold px-4 py-3">90+</th>
                      <th className="text-right font-semibold px-5 py-3">Total</th>
                    </tr>
                  </thead>
                  <tbody>
                    {snap.snapshot.lines.map((ln, i) => (
                      <tr key={i} className={`border-b border-[#F1F2F4] ${i % 2 ? 'bg-[#FAFBFC]' : ''}`}>
                        <td className="px-5 py-2.5 text-[#111827]">{ln.vendor}</td>
                        <td className="px-4 py-2.5 text-right text-[#6B7280]">{fmt(ln.b_0_30)}</td>
                        <td className="px-4 py-2.5 text-right text-[#6B7280]">{fmt(ln.b_31_60)}</td>
                        <td className="px-4 py-2.5 text-right text-[#6B7280]">{fmt(ln.b_61_90)}</td>
                        <td className="px-4 py-2.5 text-right text-[#6B7280]">{fmt(ln.b_90_plus)}</td>
                        <td className="px-5 py-2.5 text-right font-semibold text-[#111827]">{fmt(ln.total)}</td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot>
                    <tr className="border-t-2 border-[#0D1B2A]/15 bg-[#F7F8FB] font-bold text-[#0D1B2A]">
                      <td className="px-5 py-3">Grand total</td>
                      <td colSpan={4}></td>
                      <td className="px-5 py-3 text-right">{fmt(snap.snapshot.grand_total)}</td>
                    </tr>
                  </tfoot>
                </table>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Per-vendor summary (computed from posted bills) */}
        <Card>
          <CardContent className="p-0">
            {loading ? <LoadingTable rows={8} cols={6} /> : !report ? (
              <div className="py-12 text-center text-[#9CA3AF] text-sm">
                <Clock className="w-8 h-8 text-[#D1D5DB] mx-auto mb-2" />Select a date and Generate
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="bg-[#F3F4F6] border-b-2 border-[#E5E7EB]">
                    <tr>
                      <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase tracking-wider">Vendor</th>
                      <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider">0–30</th>
                      <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider">31–60</th>
                      <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider">61–90</th>
                      <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider">90+</th>
                      <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase tracking-wider">Total Outstanding</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#E5E7EB] bg-white">
                    {rows.length === 0 ? (
                      <tr><td colSpan={6} className="px-4 py-8 text-center text-[#9CA3AF] text-sm">No outstanding payables as of {asOf}</td></tr>
                    ) : rows.map((r) => {
                      const flagged = r.b90p > 0
                      return (
                        <tr key={r.name} className={cn('hover:bg-[#FFF7ED] transition-colors', flagged && 'bg-[#FEF2F2]')}>
                          <td className={cn('px-4 py-2.5 font-medium', flagged ? 'text-[#B91C1C]' : 'text-[#111827]')}>
                            {flagged && <span className="mr-1.5">⚠</span>}{r.name}
                          </td>
                          <td className="px-4 py-2.5 text-right font-mono-nums text-[#374151]">{r.b30 ? fmt(r.b30) : '—'}</td>
                          <td className="px-4 py-2.5 text-right font-mono-nums text-[#374151]">{r.b60 ? fmt(r.b60) : '—'}</td>
                          <td className="px-4 py-2.5 text-right font-mono-nums text-[#374151]">{r.b90 ? fmt(r.b90) : '—'}</td>
                          <td className={cn('px-4 py-2.5 text-right font-mono-nums font-semibold', flagged ? 'text-[#DC2626]' : 'text-[#9CA3AF]')}>{r.b90p ? fmt(r.b90p) : '—'}</td>
                          <td className="px-4 py-2.5 text-right font-mono-nums font-semibold text-[#111827]">{fmt(r.total)}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                  {rows.length > 0 && (
                    <tfoot className="bg-[#F9FAFB] border-t-2 border-[#E5E7EB]">
                      <tr className="font-bold">
                        <td className="px-4 py-3 text-xs uppercase text-[#6B7280]">Grand Total</td>
                        <td className="px-4 py-3 text-right font-mono-nums">{fmt(grand.b30)}</td>
                        <td className="px-4 py-3 text-right font-mono-nums">{fmt(grand.b60)}</td>
                        <td className="px-4 py-3 text-right font-mono-nums">{fmt(grand.b90)}</td>
                        <td className="px-4 py-3 text-right font-mono-nums text-[#DC2626]">{fmt(grand.b90p)}</td>
                        <td className="px-4 py-3 text-right font-mono-nums">{fmt(grand.total)}</td>
                      </tr>
                    </tfoot>
                  )}
                </table>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Balance-Sheet reconciliation */}
        {recon && (
          <Card>
            <CardContent className="py-4">
              <div className="flex items-start gap-3">
                {recon.reconciled
                  ? <CheckCircle2 className="w-5 h-5 text-[#047857] mt-0.5" />
                  : <AlertCircle className="w-5 h-5 text-[#DC2626] mt-0.5" />}
                <div className="flex-1">
                  <div className="text-sm font-semibold text-[#111827] mb-1">Balance-Sheet reconciliation</div>
                  <div className={cn('text-sm font-mono-nums', recon.reconciled ? 'text-[#374151]' : 'text-[#B91C1C]')}>
                    AP Aging total {fmt(recon.aging_total)}
                    {'  —  '}Balance Sheet Accounts Payable {fmt(recon.bs_ap_balance)}
                    {'  —  '}Variance{' '}
                    <span className={cn('font-bold', recon.reconciled ? 'text-[#047857]' : 'text-[#DC2626]')}>
                      {fmt(recon.variance)}
                    </span>
                  </div>
                  {!recon.reconciled && (
                    <div className="text-xs text-[#B91C1C] mt-1">
                      Variance is non-zero — the aging schedule does not tie to the GL AP control
                      (accounts {recon.ap_accounts.join(', ')}). Investigate before relying on this report.
                    </div>
                  )}
                </div>
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  )
}
