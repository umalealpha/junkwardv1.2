'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { getRelatedPartyReport, getToken } from '@/lib/api'
import type { RelatedPartyReport, RelatedPartyRow, RelatedPartyByContact } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { ArrowLeft, AlertCircle, Download, RefreshCw, Users } from 'lucide-react'
import { localYmd } from '@/lib/utils'

function fmt(v: string | number): string {
  const n = typeof v === 'string' ? parseFloat(v) : v
  if (Number.isNaN(n)) return '—'
  return new Intl.NumberFormat('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n)
}

function firstOfMonth(d: Date): string {
  return localYmd(new Date(d.getFullYear(), d.getMonth(), 1))
}

function lastOfMonth(d: Date): string {
  return localYmd(new Date(d.getFullYear(), d.getMonth() + 1, 0))
}

export default function RelatedPartyReportPage() {
  const router = useRouter()
  const today = new Date()
  const [from, setFrom] = useState(firstOfMonth(today))
  const [to, setTo] = useState(lastOfMonth(today))
  const [report, setReport] = useState<RelatedPartyReport | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [view, setView] = useState<'window' | 'fiscal_year_to_date'>('window')

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setReport(await getRelatedPartyReport(from, to))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load report')
    } finally {
      setLoading(false)
    }
  }, [from, to])

  useEffect(() => {
    const token = getToken()
    if (!token) { router.replace('/login'); return }
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load])

  function exportCsv() {
    if (!report) return
    const bucket = report[view]
    const header = ['Entry #', 'Date', 'Status', 'Account', 'Account Name', 'Contact', 'Relationship', 'Debit (BWP)', 'Credit (BWP)', 'Description', 'JE Description']
    const rows = bucket.rows.map((r: RelatedPartyRow) => [
      r.entry_number, r.entry_date, r.status, r.account_code, r.account_name,
      r.contact_name, r.relationship, r.debit_bwp, r.credit_bwp,
      r.line_description, r.description,
    ])
    const csv = [header, ...rows]
      .map(row => row.map(c => `"${String(c).replace(/"/g, '""')}"`).join(','))
      .join('\n')
    const blob = new Blob([csv], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `related-party-${view}-${from}-to-${to}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  const bucket = report ? report[view] : null

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Related-Party Transactions"
        breadcrumbs={[
          { label: 'Reports', href: '/reports' },
          { label: 'Related-Party Transactions' },
        ]}
        actions={
          <div className="flex gap-2">
            <Button variant="outline" size="sm" leftIcon={<ArrowLeft className="w-3.5 h-3.5" />} onClick={() => router.push('/reports')}>Reports</Button>
            <Button variant="outline" size="sm" leftIcon={<Download className="w-3.5 h-3.5" />} disabled={!report} onClick={exportCsv}>Export CSV</Button>
          </div>
        }
      />

      <div className="flex-1 p-6 space-y-4">
        <Card>
          <CardContent className="p-4 grid grid-cols-1 md:grid-cols-4 gap-3 items-end">
            <label className="block">
              <span className="block text-xs font-medium text-[#374151] mb-1">From</span>
              <input type="date" value={from} onChange={e => setFrom(e.target.value)} className="w-full h-10 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm" />
            </label>
            <label className="block">
              <span className="block text-xs font-medium text-[#374151] mb-1">To</span>
              <input type="date" value={to} onChange={e => setTo(e.target.value)} className="w-full h-10 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm" />
            </label>
            <Button variant="accent" leftIcon={<RefreshCw className="w-3.5 h-3.5" />} onClick={load} disabled={loading}>
              {loading ? 'Loading…' : 'Run report'}
            </Button>
            <div className="flex gap-1 self-end">
              <button
                onClick={() => setView('window')}
                className={`px-3 py-2 rounded-md text-sm font-medium border ${view === 'window' ? 'bg-[#F07F00] text-white border-[#F07F00]' : 'bg-white text-[#374151] border-[#D1D5DB]'}`}
              >
                In window
              </button>
              <button
                onClick={() => setView('fiscal_year_to_date')}
                className={`px-3 py-2 rounded-md text-sm font-medium border ${view === 'fiscal_year_to_date' ? 'bg-[#F07F00] text-white border-[#F07F00]' : 'bg-white text-[#374151] border-[#D1D5DB]'}`}
              >
                Fiscal YTD
              </button>
            </div>
          </CardContent>
        </Card>

        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-[#DC2626]" />
            <p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}

        {report && bucket && (
          <>
            <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
              <Kpi label="Lines (window)" value={String(report.summary.window_count)} />
              <Kpi label="Lines (FY to date)" value={String(report.summary.fiscal_year_count)} />
              <Kpi label={`${view === 'window' ? 'Window' : 'FY YTD'} debit total`} value={`BWP ${fmt(view === 'window' ? report.summary.window_debit_total : report.summary.fy_debit_total)}`} />
              <Kpi label={`${view === 'window' ? 'Window' : 'FY YTD'} credit total`} value={`BWP ${fmt(view === 'window' ? report.summary.window_credit_total : report.summary.fy_credit_total)}`} accent />
            </div>

            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Users className="w-4 h-4" /> By related party — {view === 'window' ? 'window' : 'fiscal year to date'}
                </CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                {bucket.by_contact.length === 0 ? (
                  <p className="px-6 py-8 text-center text-sm text-[#6B7280]">No related-party transactions in this period.</p>
                ) : (
                  <table className="w-full text-sm border-collapse">
                    <thead className="bg-[#F3F4F6] border-b-2 border-[#E5E7EB]">
                      <tr>
                        <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase">Related party</th>
                        <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase">Relationship</th>
                        <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase">Lines</th>
                        <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase">Debit (BWP)</th>
                        <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase">Credit (BWP)</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[#E5E7EB] bg-white">
                      {bucket.by_contact.map((c: RelatedPartyByContact) => (
                        <tr key={c.contact_id || c.contact_name}>
                          <td className="px-4 py-2 text-[#111827] font-medium">{c.contact_name}</td>
                          <td className="px-4 py-2 text-[#374151]">{c.relationship || '—'}</td>
                          <td className="px-4 py-2 text-right tabular-nums">{c.count}</td>
                          <td className="px-4 py-2 text-right tabular-nums">{fmt(c.debit_bwp)}</td>
                          <td className="px-4 py-2 text-right tabular-nums">{fmt(c.credit_bwp)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader><CardTitle>Detail ({bucket.count} lines)</CardTitle></CardHeader>
              <CardContent className="p-0">
                <div className="overflow-x-auto max-h-[60vh]">
                  <table className="w-full text-xs border-collapse">
                    <thead className="bg-[#F3F4F6] border-b-2 border-[#E5E7EB] sticky top-0">
                      <tr>
                        <th className="px-3 py-2 text-left font-semibold text-[#374151]">Entry #</th>
                        <th className="px-3 py-2 text-left font-semibold text-[#374151]">Date</th>
                        <th className="px-3 py-2 text-left font-semibold text-[#374151]">Account</th>
                        <th className="px-3 py-2 text-left font-semibold text-[#374151]">Related party</th>
                        <th className="px-3 py-2 text-left font-semibold text-[#374151]">Relationship</th>
                        <th className="px-3 py-2 text-right font-semibold text-[#374151]">Debit</th>
                        <th className="px-3 py-2 text-right font-semibold text-[#374151]">Credit</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[#E5E7EB] bg-white">
                      {bucket.rows.map((r: RelatedPartyRow, idx: number) => (
                        <tr key={`${r.entry_number}-${idx}`}>
                          <td className="px-3 py-1.5 font-mono">{r.entry_number}</td>
                          <td className="px-3 py-1.5">{r.entry_date}</td>
                          <td className="px-3 py-1.5">{r.account_code} {r.account_name}</td>
                          <td className="px-3 py-1.5">{r.contact_name}</td>
                          <td className="px-3 py-1.5 text-[#6B7280]">{r.relationship || '—'}</td>
                          <td className="px-3 py-1.5 text-right tabular-nums">{fmt(r.debit_bwp)}</td>
                          <td className="px-3 py-1.5 text-right tabular-nums">{fmt(r.credit_bwp)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </CardContent>
            </Card>
          </>
        )}
      </div>
    </div>
  )
}

function Kpi({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <Card>
      <CardContent className="p-4">
        <p className="text-xs font-medium text-[#6B7280] uppercase tracking-wider">{label}</p>
        <p className={`mt-1 text-lg font-semibold ${accent ? 'text-[#F07F00]' : 'text-[#111827]'}`}>{value}</p>
      </CardContent>
    </Card>
  )
}
