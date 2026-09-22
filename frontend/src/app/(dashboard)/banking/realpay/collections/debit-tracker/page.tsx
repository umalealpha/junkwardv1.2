'use client'

/**
 * /banking/realpay/collections/debit-tracker — Objective 1.
 *
 * Failed / Error Debit Tracker. Pick a period + status, see each policy's debit
 * outcome (Successful / Failed / Processing / Error / Cancelled), and read the
 * plain-language reason joined from the RealPay Response Code report. Export the
 * filtered view as CSV (audit-logged server-side).
 *
 * Build-spec: "REALPAY REPORT PROMPT" (Data Dept), CFO directive 2026-06-05.
 */

import { useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  getToken, getRealPayDebitTracker, downloadRealPayDebitExport,
} from '@/lib/api'
import type { RealPayDebitTracker } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  ArrowDownLeft, Download, AlertTriangle, Info, Filter, RefreshCw,
} from 'lucide-react'

const STATUS_META: Record<string, { label: string; cls: string }> = {
  SUCCESSFUL:  { label: 'Successful', cls: 'bg-[#D1FAE5] text-[#065F46]' },
  FAILED:      { label: 'Failed',     cls: 'bg-[#FEE2E2] text-[#991B1B]' },
  ERROR:       { label: 'Error',      cls: 'bg-[#FECACA] text-[#7F1D1D]' },
  PROCESSING:  { label: 'Processing', cls: 'bg-[#DBEAFE] text-[#1E40AF]' },
  CANCELLED:   { label: 'Cancelled',  cls: 'bg-[#F3F4F6] text-[#374151]' },
  OTHER:       { label: 'Other',      cls: 'bg-[#FEF3C7] text-[#92400E]' },
}

const STATUS_FILTERS = [
  { value: 'all',          label: 'All statuses' },
  { value: 'failed_error', label: 'Failed + Error' },
  { value: 'failed',       label: 'Failed only' },
  { value: 'error',        label: 'Error only' },
  { value: 'processing',   label: 'Processing' },
  { value: 'cancelled',    label: 'Cancelled' },
  { value: 'success',      label: 'Successful' },
]

function fmtMoney(s: string | null | undefined): string {
  if (s == null) return '—'
  const v = Number(s)
  if (!isFinite(v)) return '—'
  return v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export default function RealPayDebitTrackerPage() {
  const router = useRouter()
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [status, setStatus] = useState('failed_error')
  const [data, setData] = useState<RealPayDebitTracker | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      setData(await getRealPayDebitTracker({ start, end, status, limit: 1000 }))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load')
    } finally {
      setLoading(false)
    }
  }, [start, end, status])

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
  }, [load, router])

  async function doExport() {
    setBusy(true); setError(null)
    try {
      await downloadRealPayDebitExport({ start, end, status })
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Export failed')
    } finally {
      setBusy(false)
    }
  }

  const summary = data?.summary || {}

  return (
    <div className="min-h-screen bg-[#F8F9FB]">
      <TopBar title="Debit Tracker" />
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        {/* Header */}
        <div className="flex items-center gap-3 mb-1">
          <div className="w-10 h-10 rounded-lg bg-[#0D1B2A] flex items-center justify-center">
            <ArrowDownLeft className="w-5 h-5 text-[#F4A623]" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-[#0D1B2A]">Failed / Error Debit Tracker</h1>
            <p className="text-sm text-[#6B7280]">RealPay collections · debit outcomes &amp; reasons by period</p>
          </div>
        </div>

        {/* Filters */}
        <Card className="mt-5">
          <CardContent className="py-4">
            <div className="flex flex-wrap items-end gap-4">
              <div>
                <label className="block text-xs font-medium text-[#6B7280] mb-1">Start (debit date)</label>
                <input type="date" value={start} onChange={e => setStart(e.target.value)} aria-label="Start (debit date)"
                  className="border border-[#D1D5DB] rounded-md px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="block text-xs font-medium text-[#6B7280] mb-1">End (debit date)</label>
                <input type="date" value={end} onChange={e => setEnd(e.target.value)} aria-label="End (debit date)"
                  className="border border-[#D1D5DB] rounded-md px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="block text-xs font-medium text-[#6B7280] mb-1">Status</label>
                <select value={status} onChange={e => setStatus(e.target.value)} aria-label="Status"
                  className="border border-[#D1D5DB] rounded-md px-3 py-2 text-sm bg-white">
                  {STATUS_FILTERS.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
                </select>
              </div>
              <Button onClick={load} disabled={loading}
                className="bg-[#0D1B2A] hover:bg-[#162a40] text-white">
                <Filter className="w-4 h-4 mr-1.5" /> Apply
              </Button>
              <Button onClick={doExport} disabled={busy || !data?.row_count_filtered}
                variant="outline" className="border-[#F4A623] text-[#92400E]">
                {busy ? <RefreshCw className="w-4 h-4 mr-1.5 animate-spin" /> : <Download className="w-4 h-4 mr-1.5" />}
                Export CSV
              </Button>
            </div>
          </CardContent>
        </Card>

        {error && (
          <div className="mt-4 rounded-md bg-[#FEE2E2] text-[#991B1B] px-4 py-3 text-sm flex items-center gap-2">
            <AlertTriangle className="w-4 h-4" /> {error}
          </div>
        )}

        {/* Summary tiles */}
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3 mt-5">
          {['TOTAL', 'SUCCESSFUL', 'FAILED', 'ERROR', 'PROCESSING', 'CANCELLED', 'OTHER'].map(k => {
            const meta = STATUS_META[k] || { label: 'Total', cls: 'bg-[#0D1B2A] text-white' }
            const isTotal = k === 'TOTAL'
            return (
              <div key={k} className={`rounded-lg px-3 py-3 ${isTotal ? 'bg-[#0D1B2A] text-white' : 'bg-white border border-[#E5E7EB]'}`}>
                <div className={`text-xs font-medium ${isTotal ? 'text-[#F4A623]' : 'text-[#6B7280]'}`}>
                  {isTotal ? 'Total rows' : meta.label}
                </div>
                <div className={`text-xl font-bold ${isTotal ? 'text-white' : 'text-[#0D1B2A]'}`}>
                  {(summary[k] ?? 0).toLocaleString()}
                </div>
              </div>
            )
          })}
        </div>
        <p className="mt-2 text-xs text-[#6B7280]">
          Counts are debit <strong>attempts</strong> (one row = one debit attempt), not distinct installments — a re-presented debit appears once per attempt.
        </p>

        {/* Unmatched warning */}
        {data && data.unmatched_reason_count > 0 && (
          <div className="mt-4 rounded-md bg-[#FEF3C7] text-[#92400E] px-4 py-3 text-sm flex items-center gap-2">
            <AlertTriangle className="w-4 h-4" />
            {data.unmatched_reason_count} failed/error row(s) across the full filtered set carry a response code that did not map (flagged in the Reason column, not left blank). Sentinel codes (XX/CA) are excluded.
          </div>
        )}

        {/* Rows */}
        <Card className="mt-5">
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle className="text-[#0D1B2A]">
              Debit outcomes {data ? `(${data.row_count_filtered.toLocaleString()} matching)` : ''}
            </CardTitle>
            {data?.rows_truncated && (
              <span className="text-xs text-[#92400E] bg-[#FEF3C7] rounded px-2 py-1">
                Showing first {data.rows.length.toLocaleString()} — narrow the period or export for the full set
              </span>
            )}
          </CardHeader>
          <CardContent>
            {loading ? (
              <div className="py-10 text-center text-[#6B7280]">Loading…</div>
            ) : !data?.data_present ? (
              <div className="py-10 text-center text-[#6B7280]">
                No RealPay transaction data loaded for this period. Import the Transaction Report first.
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-[#6B7280] border-b border-[#E5E7EB]">
                      <th className="py-2 pr-3 font-medium">Date</th>
                      <th className="py-2 pr-3 font-medium">Client #</th>
                      <th className="py-2 pr-3 font-medium">Contract</th>
                      <th className="py-2 pr-3 font-medium text-right">Total</th>
                      <th className="py-2 pr-3 font-medium text-right">Collected</th>
                      <th className="py-2 pr-3 font-medium">Status</th>
                      <th className="py-2 pr-3 font-medium">Code</th>
                      <th className="py-2 pr-3 font-medium">Reason</th>
                      <th className="py-2 pr-3 font-medium">Bank</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.rows.map((r, i) => {
                      const meta = STATUS_META[r.current_status] || { label: r.current_status, cls: 'bg-[#F3F4F6] text-[#374151]' }
                      return (
                        <tr key={i} className="border-b border-[#F3F4F6]">
                          <td className="py-2 pr-3 whitespace-nowrap">{r.txn_date || '—'}</td>
                          <td className="py-2 pr-3 font-mono text-xs">{r.client_number}</td>
                          <td className="py-2 pr-3 font-mono text-xs">{r.contract_number || '—'}</td>
                          <td className="py-2 pr-3 text-right tabular-nums">{fmtMoney(r.total_amount)}</td>
                          <td className="py-2 pr-3 text-right tabular-nums">{fmtMoney(r.collected_amount)}</td>
                          <td className="py-2 pr-3">
                            <span className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${meta.cls}`}>{meta.label}</span>
                          </td>
                          <td className="py-2 pr-3 font-mono text-xs">{r.result_code || '—'}</td>
                          <td className={`py-2 pr-3 text-xs ${!r.matched && r.reason ? 'text-[#92400E] font-medium' : 'text-[#374151]'}`}>
                            {r.ambiguous && <AlertTriangle className="w-3 h-3 inline mr-1" />}
                            {r.reason || '—'}
                          </td>
                          <td className="py-2 pr-3 text-xs text-[#6B7280]">{r.client_bank || '—'}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Assumptions / open decisions */}
        {data?.assumptions?.length ? (
          <Card className="mt-5 border-[#F4A623]/40">
            <CardHeader>
              <CardTitle className="text-[#0D1B2A] text-base flex items-center gap-2">
                <Info className="w-4 h-4 text-[#F4A623]" /> Assumptions applied (Data Dept to confirm)
              </CardTitle>
            </CardHeader>
            <CardContent>
              <ul className="list-disc pl-5 space-y-1 text-sm text-[#374151]">
                {data.assumptions.map((a, i) => <li key={i}>{a}</li>)}
              </ul>
            </CardContent>
          </Card>
        ) : null}
      </div>
    </div>
  )
}
