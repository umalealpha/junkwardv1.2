'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { getAssetMovement, getToken } from '@/lib/api'
import type { AssetMovementReport } from '@/lib/api'
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

// Default range: current Botswana fiscal year (Jul → next Jun)
function defaultRange() {
  const today = new Date()
  const m = today.getMonth() // 0-indexed
  const fyStartYear = m >= 6 ? today.getFullYear() : today.getFullYear() - 1
  return {
    from: `${fyStartYear}-07-01`,
    to:   localYmd(today),
  }
}

export default function AssetMovementReportPage() {
  const router = useRouter()
  const init = defaultRange()
  const [fromDate, setFromDate] = useState(init.from)
  const [toDate,   setToDate]   = useState(init.to)
  const [report, setReport] = useState<AssetMovementReport | null>(null)
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await getAssetMovement({ from: fromDate, to: toDate })
      setReport(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load report')
    } finally {
      setLoading(false)
    }
  }, [fromDate, toDate])

  useEffect(() => {
    const token = getToken()
    if (!token) { router.replace('/login'); return }
    load()
  }, [load, router])

  function exportCsv() {
    if (!report) return
    const header = [
      'Category code', 'Category name',
      'Gross — Opening', 'Gross — Additions', 'Gross — Disposals', 'Gross — Closing',
      'Accum. — Opening', 'Accum. — Charge', 'Accum. — Disposals', 'Accum. — Closing',
      'NBV — Opening', 'NBV — Closing',
    ]
    const rows = report.rows.map(r => [
      r.category_code, r.category_name,
      r.gross_cost.opening, r.gross_cost.additions, r.gross_cost.disposals, r.gross_cost.closing,
      r.accumulated_depr.opening, r.accumulated_depr.charge, r.accumulated_depr.disposals, r.accumulated_depr.closing,
      r.nbv.opening, r.nbv.closing,
    ])
    const tot = report.totals
    rows.push([
      'TOTAL', '',
      tot.gross_cost.opening, tot.gross_cost.additions, tot.gross_cost.disposals, tot.gross_cost.closing,
      tot.accumulated_depr.opening, tot.accumulated_depr.charge, tot.accumulated_depr.disposals, tot.accumulated_depr.closing,
      tot.nbv.opening, tot.nbv.closing,
    ])
    const csv = [header, ...rows]
      .map(row => row.map(c => `"${String(c).replace(/"/g, '""')}"`).join(','))
      .join('\n')
    const blob = new Blob([csv], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `asset-movement-${fromDate}_to_${toDate}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar />
      <main className="flex-1 px-6 py-6 space-y-6">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Button variant="ghost" size="sm" onClick={() => router.back()}>
              <ArrowLeft className="h-4 w-4 mr-1" /> Back
            </Button>
            <h1 className="text-2xl font-semibold">Asset Movement Report (IAS 16)</h1>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={load} disabled={loading}>
              <RefreshCw className={`h-4 w-4 mr-1 ${loading ? 'animate-spin' : ''}`} /> Refresh
            </Button>
            <Button variant="outline" size="sm" onClick={exportCsv} disabled={!report}>
              <Download className="h-4 w-4 mr-1" /> CSV
            </Button>
          </div>
        </div>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Period</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-4 items-end">
              <div>
                <label className="block text-xs text-muted-foreground mb-1">From</label>
                <input
                  type="date"
                  value={fromDate}
                  onChange={e => setFromDate(e.target.value)}
                  className="border rounded px-3 py-1.5 text-sm bg-background"
                />
              </div>
              <div>
                <label className="block text-xs text-muted-foreground mb-1">To</label>
                <input
                  type="date"
                  value={toDate}
                  onChange={e => setToDate(e.target.value)}
                  className="border rounded px-3 py-1.5 text-sm bg-background"
                />
              </div>
              <Button size="sm" onClick={load} disabled={loading}>Apply</Button>
            </div>
          </CardContent>
        </Card>

        {error && (
          <Card className="border-red-300 bg-red-50/40 dark:bg-red-950/20">
            <CardContent className="py-3 flex items-center gap-2 text-sm">
              <AlertCircle className="h-4 w-4 text-red-600" /> {error}
            </CardContent>
          </Card>
        )}

        {report && (
          <Card>
            <CardHeader>
              <CardTitle className="text-base">
                Roll-forward by category — {report.from_date} → {report.to_date}
              </CardTitle>
            </CardHeader>
            <CardContent className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b">
                    <th className="text-left py-2 px-2" rowSpan={2}>Category</th>
                    <th className="text-center py-2 px-2 border-l" colSpan={4}>Gross Carrying Amount</th>
                    <th className="text-center py-2 px-2 border-l" colSpan={4}>Accumulated Depreciation</th>
                    <th className="text-center py-2 px-2 border-l" colSpan={2}>Net Book Value</th>
                  </tr>
                  <tr className="border-b text-muted-foreground">
                    <th className="text-right py-1 px-2 border-l">Opening</th>
                    <th className="text-right py-1 px-2">Additions</th>
                    <th className="text-right py-1 px-2">Disposals</th>
                    <th className="text-right py-1 px-2 font-semibold">Closing</th>
                    <th className="text-right py-1 px-2 border-l">Opening</th>
                    <th className="text-right py-1 px-2">Charge</th>
                    <th className="text-right py-1 px-2">Disposals</th>
                    <th className="text-right py-1 px-2 font-semibold">Closing</th>
                    <th className="text-right py-1 px-2 border-l">Opening</th>
                    <th className="text-right py-1 px-2 font-semibold">Closing</th>
                  </tr>
                </thead>
                <tbody>
                  {report.rows.map(r => (
                    <tr key={r.category_code} className="border-b hover:bg-muted/30">
                      <td className="py-1.5 px-2">
                        <div className="font-medium">{r.category_code}</div>
                        <div className="text-muted-foreground text-[10px]">{r.category_name}</div>
                      </td>
                      <td className="text-right tabular-nums px-2 border-l">{fmt(r.gross_cost.opening)}</td>
                      <td className="text-right tabular-nums px-2">{fmt(r.gross_cost.additions)}</td>
                      <td className="text-right tabular-nums px-2">{fmt(r.gross_cost.disposals)}</td>
                      <td className="text-right tabular-nums px-2 font-semibold">{fmt(r.gross_cost.closing)}</td>
                      <td className="text-right tabular-nums px-2 border-l">{fmt(r.accumulated_depr.opening)}</td>
                      <td className="text-right tabular-nums px-2">{fmt(r.accumulated_depr.charge)}</td>
                      <td className="text-right tabular-nums px-2">{fmt(r.accumulated_depr.disposals)}</td>
                      <td className="text-right tabular-nums px-2 font-semibold">{fmt(r.accumulated_depr.closing)}</td>
                      <td className="text-right tabular-nums px-2 border-l">{fmt(r.nbv.opening)}</td>
                      <td className="text-right tabular-nums px-2 font-semibold">{fmt(r.nbv.closing)}</td>
                    </tr>
                  ))}
                  <tr className="border-t-2 bg-muted/40 font-semibold">
                    <td className="py-2 px-2">TOTAL</td>
                    <td className="text-right tabular-nums px-2 border-l">{fmt(report.totals.gross_cost.opening)}</td>
                    <td className="text-right tabular-nums px-2">{fmt(report.totals.gross_cost.additions)}</td>
                    <td className="text-right tabular-nums px-2">{fmt(report.totals.gross_cost.disposals)}</td>
                    <td className="text-right tabular-nums px-2">{fmt(report.totals.gross_cost.closing)}</td>
                    <td className="text-right tabular-nums px-2 border-l">{fmt(report.totals.accumulated_depr.opening)}</td>
                    <td className="text-right tabular-nums px-2">{fmt(report.totals.accumulated_depr.charge)}</td>
                    <td className="text-right tabular-nums px-2">{fmt(report.totals.accumulated_depr.disposals)}</td>
                    <td className="text-right tabular-nums px-2">{fmt(report.totals.accumulated_depr.closing)}</td>
                    <td className="text-right tabular-nums px-2 border-l">{fmt(report.totals.nbv.opening)}</td>
                    <td className="text-right tabular-nums px-2">{fmt(report.totals.nbv.closing)}</td>
                  </tr>
                </tbody>
              </table>
            </CardContent>
          </Card>
        )}
      </main>
    </div>
  )
}
