'use client'

import { useEffect, useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import {
  getReconDashboard,
  runReconciliation,
  getToken,
} from '@/lib/api'
import type { ReconDashboard, ReconLine, ReconAgeing } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { LoadingTable } from '@/components/ui/loading'
import { formatAmount, cn } from '@/lib/utils'
import { useNumberFormat } from '@/contexts/NumberFormatContext'
import { useCompany } from '@/contexts/CompanyContext'
import { AlertTriangle, Play, RefreshCw, CheckCircle2, XCircle, MinusCircle } from 'lucide-react'

const STATUS_META: Record<string, { label: string; cls: string; Icon: typeof CheckCircle2 }> = {
  matched: { label: 'Matched', cls: 'bg-emerald-50 text-emerald-700 border-emerald-200', Icon: CheckCircle2 },
  breach: { label: 'Breach', cls: 'bg-red-50 text-red-700 border-red-200', Icon: XCircle },
  no_source: { label: 'No source', cls: 'bg-gray-100 text-gray-500 border-gray-200', Icon: MinusCircle },
  no_omni_side: { label: 'No GL side', cls: 'bg-gray-100 text-gray-500 border-gray-200', Icon: MinusCircle },
}

function StatusChip({ status }: { status: string }) {
  const m = STATUS_META[status] ?? STATUS_META.no_source
  const { Icon } = m
  return (
    <span className={cn('inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium', m.cls)}>
      <Icon className="h-3.5 w-3.5" />
      {m.label}
    </span>
  )
}

function BUCKET_LABEL(b: string): string {
  const map: Record<string, string> = {
    '0-30': 'Current (0–30)', '31-60': '31–60 days',
    '61-90': '61–90 days', '90+': '90+ days', total: 'Total',
  }
  return map[b] ?? b
}

export default function ReconciliationPage() {
  const router = useRouter()
  const { mode } = useNumberFormat()
  const { selected: selectedCompany } = useCompany()
  const currency = (selectedCompany?.base_currency || 'BWP') as string

  const [data, setData] = useState<ReconDashboard | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [running, setRunning] = useState(false)

  // Run form
  const [periodLabel, setPeriodLabel] = useState('')
  const [periodEnd, setPeriodEnd] = useState('')
  const [periodStart, setPeriodStart] = useState('')
  const [tolerance, setTolerance] = useState('5')

  const fmt = (v: string | number | null, unit = 'bwp') => {
    if (v === null || v === undefined || v === '') return '—'
    if (unit === 'count') return Number(v).toLocaleString()
    return formatAmount(v, currency, mode)
  }

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const d = await getReconDashboard()
      setData(d)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load reconciliation')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!getToken()) {
      router.replace('/login')
      return
    }
    load()
  }, [load, router])

  async function handleRun() {
    if (!periodLabel.trim() || !periodEnd) {
      setError('Period label and period-end date are required to run a reconciliation.')
      return
    }
    setRunning(true)
    setError(null)
    try {
      await runReconciliation({
        period_label: periodLabel.trim(),
        period_end: periodEnd,
        period_start: periodStart || undefined,
        tolerance_pct: Number(tolerance) || 5,
      })
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Reconciliation run failed')
    } finally {
      setRunning(false)
    }
  }

  const rows: ReconLine[] = data?.rows ?? []
  const ageing: ReconAgeing[] = data?.ageing ?? []
  const breaches = rows.filter((r) => r.status === 'breach').length

  return (
    <div className="min-h-screen bg-gray-50">
      <TopBar />
      <main className="mx-auto max-w-[1440px] px-6 py-6">
        {/* Header */}
        <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold text-[#0B0B3B]">Source-to-Ledger Reconciliation</h1>
            <p className="mt-1 text-sm text-gray-500">
              ADIC (incl. Instant) — source figures vs posted GL, per metric. Tolerance-banded.
            </p>
          </div>
          <div className="flex items-center gap-2">
            {data?.run && (
              <span className="text-sm text-gray-500">
                Last run: <span className="font-medium text-gray-700">{data.run.period_label}</span>
                {' · '}{new Date(data.run.run_at).toLocaleString()}
                {' · '}
                <span className={cn('font-medium', data.run.status === 'completed' ? 'text-emerald-600' : 'text-amber-600')}>
                  {data.run.status}
                </span>
              </span>
            )}
            <Button variant="outline" onClick={load} disabled={loading}>
              <RefreshCw className={cn('mr-1 h-4 w-4', loading && 'animate-spin')} /> Refresh
            </Button>
          </div>
        </div>

        {error && (
          <div className="mb-4 flex items-start gap-2 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {/* Scope note */}
        {data?.scope_note && (
          <div className="mb-6 flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{data.scope_note}</span>
          </div>
        )}

        {/* Run controls */}
        <Card className="mb-6">
          <CardContent className="flex flex-wrap items-end gap-4 py-4">
            <div className="flex flex-col">
              <label className="mb-1 text-xs font-semibold uppercase text-gray-500">Period label</label>
              <input value={periodLabel} onChange={(e) => setPeriodLabel(e.target.value)}
                placeholder="e.g. FY26-Q4 or 2026-06"
                className="h-10 w-44 rounded-md border border-gray-300 px-3 text-sm" />
            </div>
            <div className="flex flex-col">
              <label className="mb-1 text-xs font-semibold uppercase text-gray-500">Period start</label>
              <input type="date" value={periodStart} onChange={(e) => setPeriodStart(e.target.value)}
                className="h-10 rounded-md border border-gray-300 px-3 text-sm" />
            </div>
            <div className="flex flex-col">
              <label className="mb-1 text-xs font-semibold uppercase text-gray-500">Period end *</label>
              <input type="date" value={periodEnd} onChange={(e) => setPeriodEnd(e.target.value)}
                className="h-10 rounded-md border border-gray-300 px-3 text-sm" />
            </div>
            <div className="flex flex-col">
              <label className="mb-1 text-xs font-semibold uppercase text-gray-500">Tolerance %</label>
              <input type="number" step="0.5" value={tolerance} onChange={(e) => setTolerance(e.target.value)}
                className="h-10 w-24 rounded-md border border-gray-300 px-3 text-sm" />
            </div>
            <Button onClick={handleRun} disabled={running}
              className="bg-[#F07F00] text-white hover:bg-[#d97200]">
              <Play className={cn('mr-1 h-4 w-4', running && 'animate-pulse')} />
              {running ? 'Running…' : 'Run reconciliation'}
            </Button>
          </CardContent>
        </Card>

        {loading ? (
          <LoadingTable />
        ) : !data?.run ? (
          <Card>
            <CardContent className="py-10 text-center text-gray-500">
              {data?.hint || 'No reconciliation run yet. Set a period above and run one.'}
            </CardContent>
          </Card>
        ) : (
          <>
            {/* Bridge table */}
            <Card className="mb-6">
              <CardContent className="p-0">
                <div className="flex items-center justify-between border-b px-5 py-3">
                  <h2 className="text-lg font-semibold text-[#0B0B3B]">Metric bridge</h2>
                  {breaches > 0 && (
                    <span className="text-sm font-medium text-red-600">{breaches} breach{breaches > 1 ? 'es' : ''} &gt; tolerance</span>
                  )}
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="bg-[#F3F4F6] text-left text-xs font-semibold uppercase text-gray-600">
                        <th className="px-5 py-2.5">Metric</th>
                        <th className="px-5 py-2.5 text-right">Source total</th>
                        <th className="px-5 py-2.5">Source</th>
                        <th className="px-5 py-2.5 text-right">Posted in Omni (GL)</th>
                        <th className="px-5 py-2.5 text-right">Variance</th>
                        <th className="px-5 py-2.5 text-right">Var %</th>
                        <th className="px-5 py-2.5">Status</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100">
                      {rows.map((r) => (
                        <tr key={r.id} className="hover:bg-[#FFF7ED]">
                          <td className="px-5 py-3 font-medium text-gray-800">{r.label}</td>
                          <td className="px-5 py-3 text-right tabular-nums">{fmt(r.source_total, r.unit)}</td>
                          <td className="px-5 py-3 text-xs text-gray-500">{r.source_system || '—'}</td>
                          <td className="px-5 py-3 text-right tabular-nums">{fmt(r.omni_posted, r.unit)}</td>
                          <td className={cn('px-5 py-3 text-right tabular-nums',
                            r.status === 'breach' && 'font-semibold text-red-600')}>
                            {fmt(r.variance, r.unit)}
                          </td>
                          <td className="px-5 py-3 text-right tabular-nums">
                            {r.variance_pct === null ? '—' : `${Number(r.variance_pct).toFixed(2)}%`}
                          </td>
                          <td className="px-5 py-3"><StatusChip status={r.status} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </CardContent>
            </Card>

            {/* Ageing tie-out */}
            <Card>
              <CardContent className="p-0">
                <div className="border-b px-5 py-3">
                  <h2 className="text-lg font-semibold text-[#0B0B3B]">Premium-debtor ageing tie-out</h2>
                  <p className="text-xs text-gray-500">Graphite (Dom-Com replica) vs Omni GL AR-aging. Portal cross-check: Phase 2.</p>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="bg-[#F3F4F6] text-left text-xs font-semibold uppercase text-gray-600">
                        <th className="px-5 py-2.5">Bucket</th>
                        <th className="px-5 py-2.5 text-right">Graphite</th>
                        <th className="px-5 py-2.5 text-right">Omni GL</th>
                        <th className="px-5 py-2.5 text-right">Portal</th>
                        <th className="px-5 py-2.5 text-right">Variance (GL − Graphite)</th>
                        <th className="px-5 py-2.5 text-right">Var %</th>
                        <th className="px-5 py-2.5">Status</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100">
                      {ageing.map((a) => (
                        <tr key={a.id} className={cn('hover:bg-[#FFF7ED]', a.bucket === 'total' && 'font-semibold bg-gray-50')}>
                          <td className="px-5 py-3 text-gray-800">{BUCKET_LABEL(a.bucket)}</td>
                          <td className="px-5 py-3 text-right tabular-nums">{fmt(a.graphite_total)}</td>
                          <td className="px-5 py-3 text-right tabular-nums">{fmt(a.omni_total)}</td>
                          <td className="px-5 py-3 text-right tabular-nums text-gray-400">{fmt(a.portal_total)}</td>
                          <td className={cn('px-5 py-3 text-right tabular-nums',
                            a.status === 'breach' && 'font-semibold text-red-600')}>
                            {fmt(a.variance_graphite_vs_omni)}
                          </td>
                          <td className="px-5 py-3 text-right tabular-nums">
                            {a.variance_pct === null ? '—' : `${Number(a.variance_pct).toFixed(2)}%`}
                          </td>
                          <td className="px-5 py-3"><StatusChip status={a.status} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </CardContent>
            </Card>
          </>
        )}
      </main>
    </div>
  )
}
