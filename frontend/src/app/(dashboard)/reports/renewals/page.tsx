'use client'

// Renewal report (Workstream B / B1) — policies renewing in a chosen month,
// read live from Graphite. Pick a month (+ optionally Domestic/Commercial),
// Generate, then download Excel or CSV. Month-only: everyone renewing in that
// month appears, any year (Finance rule). First-year policies are flagged.

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { getRenewalReport, downloadRenewalReport, getToken } from '@/lib/api'
import type { RenewalReport } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { LoadingTable } from '@/components/ui/loading'
import { Download, AlertCircle, RefreshCw } from 'lucide-react'

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

export default function RenewalReportPage() {
  const router = useRouter()
  const [month, setMonth] = useState(new Date().getMonth() + 1)
  const [section, setSection] = useState('')            // '', 'domestic', 'commercial'
  const [report, setReport] = useState<RenewalReport | null>(null)
  const [loading, setLoading] = useState(false)
  const [downloading, setDownloading] = useState<'xlsx' | 'csv' | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!getToken()) {
      router.replace('/login')
      return
    }
    void generate()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const generate = async () => {
    setLoading(true)
    setError(null)
    try {
      setReport(await getRenewalReport({ month, section: section || undefined }))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load the renewal report')
    } finally {
      setLoading(false)
    }
  }

  const download = async (fmt: 'xlsx' | 'csv') => {
    setDownloading(fmt)
    setError(null)
    try {
      await downloadRenewalReport({ month, section: section || undefined }, fmt)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Download failed')
    } finally {
      setDownloading(null)
    }
  }

  const hasRows = !!report && report.rows.length > 0

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Renewal Report"
        breadcrumbs={[{ label: 'Reports', href: '/reports' }, { label: 'Renewal Report' }]}
      />
      <main className="flex-1 p-4 md:p-6 space-y-4">
        <Card>
          <CardContent className="p-4 flex flex-wrap items-end gap-3">
            <div>
              <label className="block text-xs font-semibold text-[#6B7280] mb-1">Renewal month</label>
              <select value={month} onChange={(e) => setMonth(Number(e.target.value))}
                      className="border border-[#E5E7EB] rounded-md px-3 py-2 text-sm bg-white">
                {MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs font-semibold text-[#6B7280] mb-1">Line of business</label>
              <select value={section} onChange={(e) => setSection(e.target.value)}
                      className="border border-[#E5E7EB] rounded-md px-3 py-2 text-sm bg-white">
                <option value="">Domestic &amp; Commercial</option>
                <option value="domestic">Domestic</option>
                <option value="commercial">Commercial</option>
              </select>
            </div>
            <Button onClick={() => void generate()} disabled={loading} size="sm">
              <RefreshCw className={`w-4 h-4 mr-1 ${loading ? 'animate-spin' : ''}`} /> Generate
            </Button>
            <div className="flex-1" />
            <Button variant="secondary" size="sm" disabled={!hasRows || downloading !== null}
                    onClick={() => void download('xlsx')}>
              <Download className="w-4 h-4 mr-1" /> {downloading === 'xlsx' ? 'Preparing…' : 'Excel'}
            </Button>
            <Button variant="secondary" size="sm" disabled={!hasRows || downloading !== null}
                    onClick={() => void download('csv')}>
              <Download className="w-4 h-4 mr-1" /> {downloading === 'csv' ? 'Preparing…' : 'CSV'}
            </Button>
          </CardContent>
        </Card>

        {error && (
          <div className="flex items-center gap-2 rounded-lg bg-[#FEE2E2] text-[#991B1B] px-3 py-2 text-sm">
            <AlertCircle className="w-4 h-4 flex-shrink-0" /> {error}
          </div>
        )}

        {report && !loading && (
          <div className="text-sm text-[#6B7280]">
            <span className="font-semibold text-[#111827]">{report.count}</span>{' '}
            {report.count === 1 ? 'policy' : 'policies'} renewing in {report.month_label} — {report.section}
            {report.meta?.[1] && <div className="text-xs mt-0.5">{report.meta[1]}</div>}
          </div>
        )}

        <Card>
          <CardContent className="p-0 overflow-x-auto">
            {loading ? (
              <LoadingTable />
            ) : hasRows ? (
              <table className="w-full text-xs">
                <thead className="bg-[#F9FAFB] text-[#6B7280]">
                  <tr>
                    {report!.columns.map((c) => (
                      <th key={c} className="text-left font-semibold px-3 py-2 whitespace-nowrap">{c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {report!.rows.map((r, i) => (
                    <tr key={i} className="border-t border-[#F3F4F6]">
                      {report!.columns.map((c) => (
                        <td key={c} className="px-3 py-1.5 whitespace-nowrap">{r[c]}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : report ? (
              <div className="p-6 text-center text-sm text-[#6B7280]">
                No policies renew in {report.month_label}
                {report.section !== 'Domestic & Commercial' ? ` for ${report.section}` : ''}.
              </div>
            ) : (
              <div className="p-6 text-center text-sm text-[#6B7280]">Pick a month and Generate.</div>
            )}
          </CardContent>
        </Card>
      </main>
    </div>
  )
}
