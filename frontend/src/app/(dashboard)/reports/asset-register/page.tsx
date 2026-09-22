'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { getAssetRegister, getAssetCategories, getToken } from '@/lib/api'
import type { AssetRegisterReport, AssetCategory } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { ArrowLeft, AlertCircle, Download, RefreshCw } from 'lucide-react'
import { localYmd } from '@/lib/utils'

function fmt(v: string | number): string {
  const n = typeof v === 'string' ? parseFloat(v) : v
  if (Number.isNaN(n)) return '—'
  return new Intl.NumberFormat('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n)
}

export default function AssetRegisterReportPage() {
  const router = useRouter()
  const [asOf, setAsOf] = useState(() => localYmd(new Date()))
  const [category, setCategory] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [report, setReport] = useState<AssetRegisterReport | null>(null)
  const [categories, setCategories] = useState<AssetCategory[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await getAssetRegister({
        as_of: asOf,
        category: category || undefined,
        status: statusFilter || undefined,
      })
      setReport(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load report')
    } finally {
      setLoading(false)
    }
  }, [asOf, category, statusFilter])

  useEffect(() => {
    const token = getToken()
    if (!token) { router.replace('/login'); return }
    getAssetCategories().then(res => setCategories(res.results)).catch(() => {})
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load])

  function exportCsv() {
    if (!report) return
    const header = [
      'Tag', 'External ref', 'Name', 'Category', 'Company',
      'Cost', 'Accum. depr.', 'NBV', 'Salvage', 'Method',
      'Useful life (months)', 'Purchase date', 'In-service date',
      'Last depr.', 'Location', 'Custodian', 'Status',
    ]
    const rows = report.items.map(i => [
      i.tag_number, i.external_ref, i.name, i.category_code, i.company_code,
      i.cost, i.accumulated_depr, i.net_book_value, i.salvage_value, i.method,
      String(i.useful_life_months), i.purchase_date || '', i.in_service_date || '',
      i.last_depr_date || '', i.location, i.custodian, i.status,
    ])
    const csv = [header, ...rows]
      .map(r => r.map(c => `"${String(c).replace(/"/g, '""')}"`).join(','))
      .join('\n')
    const blob = new Blob([csv], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `asset-register-${asOf}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title="Asset Register"
        breadcrumbs={[
          { label: 'Reports', href: '/reports' },
          { label: 'Asset Register' },
        ]}
        actions={
          <div className="flex gap-2">
            <Button variant="outline" size="sm" leftIcon={<ArrowLeft className="w-3.5 h-3.5" />} onClick={() => router.push('/reports')}>
              Reports
            </Button>
            <Button variant="outline" size="sm" leftIcon={<Download className="w-3.5 h-3.5" />} disabled={!report} onClick={exportCsv}>
              Export CSV
            </Button>
          </div>
        }
      />

      <div className="flex-1 p-6 space-y-4">
        <Card>
          <CardContent className="p-4 grid grid-cols-1 md:grid-cols-4 gap-3 items-end">
            <label className="block">
              <span className="block text-xs font-medium text-[#374151] mb-1">As of</span>
              <input type="date" value={asOf} onChange={e => setAsOf(e.target.value)} className="w-full h-10 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm" />
            </label>
            <label className="block">
              <span className="block text-xs font-medium text-[#374151] mb-1">Category</span>
              <select value={category} onChange={e => setCategory(e.target.value)} className="w-full h-10 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm">
                <option value="">All</option>
                {categories.map(c => <option key={c.id} value={c.code}>{c.code} — {c.name}</option>)}
              </select>
            </label>
            <label className="block">
              <span className="block text-xs font-medium text-[#374151] mb-1">Status</span>
              <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)} className="w-full h-10 bg-white border border-[#D1D5DB] rounded-md px-3 text-sm">
                <option value="">All</option>
                <option value="active">Active</option>
                <option value="disposed">Disposed</option>
                <option value="written_off">Written off</option>
                <option value="transferred">Transferred</option>
              </select>
            </label>
            <Button variant="accent" leftIcon={<RefreshCw className="w-3.5 h-3.5" />} onClick={load} disabled={loading}>
              {loading ? 'Loading…' : 'Run report'}
            </Button>
          </CardContent>
        </Card>

        {error && (
          <div className="bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3 flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-[#DC2626]" />
            <p className="text-[#DC2626] text-sm">{error}</p>
          </div>
        )}

        {report && (
          <>
            {/* Totals */}
            <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
              <Kpi label="Assets" value={String(report.count)} />
              <Kpi label="Total cost" value={`BWP ${fmt(report.totals.cost)}`} />
              <Kpi label="Accumulated depreciation" value={`BWP ${fmt(report.totals.accumulated_depr)}`} />
              <Kpi label="Net book value" value={`BWP ${fmt(report.totals.net_book_value)}`} accent />
            </div>

            {/* By category */}
            <Card>
              <CardHeader><CardTitle>By category</CardTitle></CardHeader>
              <CardContent className="p-0">
                <table className="w-full text-sm border-collapse">
                  <thead className="bg-[#F3F4F6] border-b-2 border-[#E5E7EB]">
                    <tr>
                      <th className="px-4 py-3 text-left text-xs font-semibold text-[#374151] uppercase">Category</th>
                      <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase">Count</th>
                      <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase">Cost</th>
                      <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase">Accum. depr.</th>
                      <th className="px-4 py-3 text-right text-xs font-semibold text-[#374151] uppercase">NBV</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#E5E7EB] bg-white">
                    {report.by_category.map(b => (
                      <tr key={b.category_code}>
                        <td className="px-4 py-2 text-[#111827]">{b.category_code} — {b.category_name}</td>
                        <td className="px-4 py-2 text-right tabular-nums">{b.count}</td>
                        <td className="px-4 py-2 text-right tabular-nums">{fmt(b.cost)}</td>
                        <td className="px-4 py-2 text-right tabular-nums">{fmt(b.accumulated_depr)}</td>
                        <td className="px-4 py-2 text-right tabular-nums font-medium">{fmt(b.net_book_value)}</td>
                      </tr>
                    ))}
                    <tr className="bg-[#F9FAFB] font-semibold">
                      <td className="px-4 py-2 text-[#111827]">Total</td>
                      <td className="px-4 py-2 text-right tabular-nums">{report.count}</td>
                      <td className="px-4 py-2 text-right tabular-nums">{fmt(report.totals.cost)}</td>
                      <td className="px-4 py-2 text-right tabular-nums">{fmt(report.totals.accumulated_depr)}</td>
                      <td className="px-4 py-2 text-right tabular-nums text-[#F07F00]">{fmt(report.totals.net_book_value)}</td>
                    </tr>
                  </tbody>
                </table>
              </CardContent>
            </Card>

            {/* Detail */}
            <Card>
              <CardHeader><CardTitle>Detail ({report.count} assets)</CardTitle></CardHeader>
              <CardContent className="p-0">
                <div className="overflow-x-auto max-h-[60vh]">
                  <table className="w-full text-xs border-collapse">
                    <thead className="bg-[#F3F4F6] border-b-2 border-[#E5E7EB] sticky top-0">
                      <tr>
                        <th className="px-3 py-2 text-left font-semibold text-[#374151]">Tag</th>
                        <th className="px-3 py-2 text-left font-semibold text-[#374151]">Name</th>
                        <th className="px-3 py-2 text-left font-semibold text-[#374151] hidden md:table-cell">Category</th>
                        <th className="px-3 py-2 text-right font-semibold text-[#374151]">Cost</th>
                        <th className="px-3 py-2 text-right font-semibold text-[#374151]">Accum.</th>
                        <th className="px-3 py-2 text-right font-semibold text-[#374151]">NBV</th>
                        <th className="px-3 py-2 text-left font-semibold text-[#374151] hidden lg:table-cell">Location</th>
                        <th className="px-3 py-2 text-left font-semibold text-[#374151]">Status</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[#E5E7EB] bg-white">
                      {report.items.map(i => (
                        <tr key={i.id} className="hover:bg-[#F9FAFB] cursor-pointer" onClick={() => router.push(`/assets/${i.id}`)}>
                          <td className="px-3 py-1.5 font-mono">{i.tag_number}</td>
                          <td className="px-3 py-1.5">{i.name}</td>
                          <td className="px-3 py-1.5 hidden md:table-cell">{i.category_code}</td>
                          <td className="px-3 py-1.5 text-right tabular-nums">{fmt(i.cost)}</td>
                          <td className="px-3 py-1.5 text-right tabular-nums text-[#6B7280]">{fmt(i.accumulated_depr)}</td>
                          <td className="px-3 py-1.5 text-right tabular-nums font-medium">{fmt(i.net_book_value)}</td>
                          <td className="px-3 py-1.5 hidden lg:table-cell">{i.location || '—'}</td>
                          <td className="px-3 py-1.5">{i.status}</td>
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
