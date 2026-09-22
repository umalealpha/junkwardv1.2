'use client'

/**
 * /banking/realpay/collections/dashboard — Objective 2.
 *
 * Collections Dashboard. Actual amount collected for a chosen period, split by
 * product grouping derived from the client-number prefix:
 *   MIS                       -> INSTANT
 *   COM / COMG                -> Corporate lines
 *   DOM / DOMG                -> Personal lines
 *   anything else             -> Other (unmapped)  [so totals always reconcile]
 *
 * Build-spec: "REALPAY REPORT PROMPT" (Data Dept), CFO directive 2026-06-05.
 */

import { useCallback, useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { getToken, getRealPayCollectionsDashboard, downloadRealPayCollectionsExport, uploadRealPayBilling } from '@/lib/api'
import type { RealPayCollectionsDashboard } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { ArrowDownLeft, Filter, Info, AlertTriangle, Download, CheckCircle2, RefreshCw, Upload } from 'lucide-react'

const GROUP_COLOR: Record<string, string> = {
  INSTANT:   '#F4A623',
  CORPORATE: '#0D1B2A',
  PERSONAL:  '#2E6FB7',
  OTHER:     '#9CA3AF',
}

function fmtMoney(s: string | null | undefined): string {
  if (s == null) return 'BWP 0.00'
  const v = Number(s)
  if (!isFinite(v)) return '—'
  return 'BWP ' + v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

// Default the screen to the current calendar month so it opens fast on a
// meaningful "this month so far" figure instead of scanning all history.
// Local date (Botswana UTC+2) — never toISOString(), which can roll back a day.
function _currentMonthRange(): { start: string; end: string } {
  const pad = (n: number) => String(n).padStart(2, '0')
  const fmt = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
  const now = new Date()
  return { start: fmt(new Date(now.getFullYear(), now.getMonth(), 1)), end: fmt(now) }
}

export default function RealPayCollectionsDashboardPage() {
  const router = useRouter()
  const _dflt = _currentMonthRange()
  const [start, setStart] = useState(_dflt.start)
  const [end, setEnd] = useState(_dflt.end)
  const [data, setData] = useState<RealPayCollectionsDashboard | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadMsg, setUploadMsg] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      setData(await getRealPayCollectionsDashboard({ start, end }))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load')
    } finally {
      setLoading(false)
    }
  }, [start, end])

  async function doExport() {
    setBusy(true); setError(null)
    try {
      await downloadRealPayCollectionsExport({ start, end })
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Export failed')
    } finally {
      setBusy(false)
    }
  }

  // Bokani Makosha, bug 6a48367f: the Client Billing Period export is the only
  // source that carries each debit's tracking start date, so it is the only way
  // a month's figure switches from the Graphite estimate to the billing basis.
  // The upload route has been live since 16-Sep with no button to reach it.
  async function doBillingUpload(file: File) {
    setUploading(true); setError(null); setUploadMsg(null)
    try {
      const r = await uploadRealPayBilling(file)
      const read = r.file && 'rows_read' in r.file ? (r.file as { rows_read?: number }).rows_read : r.rows_read
      setUploadMsg(`${file.name}: ${Number(read ?? 0).toLocaleString()} rows read`
        + (r.created != null ? ` · ${r.created.toLocaleString()} new` : '')
        + (r.updated != null ? ` · ${r.updated.toLocaleString()} updated` : '')
        + '. Uploading the same file again corrects rows, it never doubles them.')
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Upload failed')
    } finally {
      setUploading(false)
    }
  }

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    load()
  }, [load, router])

  return (
    <div className="min-h-screen bg-[#F8F9FB]">
      <TopBar title="Collections Dashboard" />
      <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        {/* Header */}
        <div className="flex items-center gap-3 mb-1">
          <div className="w-10 h-10 rounded-lg bg-[#0D1B2A] flex items-center justify-center">
            <ArrowDownLeft className="w-5 h-5 text-[#F4A623]" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-[#0D1B2A]">Collections Dashboard</h1>
            <p className="text-sm text-[#6B7280]">RealPay · amount collected by product grouping</p>
          </div>
        </div>

        {/* Filters */}
        <Card className="mt-5">
          <CardContent className="py-4">
            <div className="flex flex-wrap items-end gap-4">
              <div>
                <label className="block text-xs font-medium text-[#6B7280] mb-1">Start</label>
                <input type="date" value={start} onChange={e => setStart(e.target.value)}
                  className="border border-[#D1D5DB] rounded-md px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="block text-xs font-medium text-[#6B7280] mb-1">End</label>
                <input type="date" value={end} onChange={e => setEnd(e.target.value)}
                  className="border border-[#D1D5DB] rounded-md px-3 py-2 text-sm" />
              </div>
              <Button onClick={load} disabled={loading}
                className="bg-[#0D1B2A] hover:bg-[#162a40] text-white">
                <Filter className="w-4 h-4 mr-1.5" /> Apply
              </Button>
              <Button onClick={doExport} disabled={busy || !data?.data_present}
                variant="outline" className="border-[#F4A623] text-[#92400E]">
                {busy ? <RefreshCw className="w-4 h-4 mr-1.5 animate-spin" /> : <Download className="w-4 h-4 mr-1.5" />}
                Export CSV
              </Button>
              <label className={`inline-flex items-center rounded-md border border-[#0D1B2A] px-3 py-2 text-sm font-medium text-[#0D1B2A] cursor-pointer hover:bg-[#F3F4F6] focus-within:ring-2 focus-within:ring-[#F4A623] ${uploading ? 'opacity-60 pointer-events-none' : ''}`}>
                {uploading ? <RefreshCw className="w-4 h-4 mr-1.5 animate-spin" /> : <Upload className="w-4 h-4 mr-1.5" />}
                Upload Client Billing report
                <input type="file" accept=".xlsx,.xls,.csv" className="sr-only"
                  aria-label="Upload a RealPay Client Billing Period export"
                  onChange={e => { const f = e.target.files?.[0]; e.target.value = ''; if (f) doBillingUpload(f) }} />
              </label>
            </div>
            <p className="mt-3 text-xs text-[#6B7280]">
              For a full month, upload that month&apos;s Client Billing Period export <b>and</b> the next
              month&apos;s: debits tracked this month that clear next month are only in the later report.
              Omni files every row under its tracking start date.
            </p>
            {uploadMsg && (
              <p className="mt-2 text-xs text-[#065F46] flex items-center gap-1">
                <CheckCircle2 className="w-3.5 h-3.5" /> {uploadMsg}
              </p>
            )}
          </CardContent>
        </Card>

        {error && (
          <div className="mt-4 rounded-md bg-[#FEE2E2] text-[#991B1B] px-4 py-3 text-sm flex items-center gap-2">
            <AlertTriangle className="w-4 h-4" /> {error}
          </div>
        )}

        {/* Total */}
        <div className="mt-5 rounded-xl bg-[#0D1B2A] text-white px-6 py-5">
          <div className="flex items-start justify-between">
            <div>
              <div className="text-sm text-[#F4A623] font-medium">Total Collected</div>
              <div className="text-3xl font-bold mt-1">
                {loading ? '…' : fmtMoney(data?.total_collected)}
              </div>
              <div className="text-xs text-[#9CA3AF] mt-1">
                {data ? `${data.total_collected_lines.toLocaleString()} collected lines · ${data.total_attempts.toLocaleString()} attempts` : ''}
              </div>
            </div>
            {data && data.live ? (
              <span className="text-xs px-2.5 py-1 rounded-full flex items-center gap-1 bg-[#065F46] text-[#D1FAE5]"
                title={data.as_of ? `Live from Graphite · as of ${data.as_of}` : 'Live from Graphite'}>
                <CheckCircle2 className="w-3.5 h-3.5" />
                Live from Graphite{data.as_of ? ` · ${data.as_of}` : ''}
              </span>
            ) : data && data.source === 'client-billing' ? (
              /* The billing basis overrides the Graphite estimate, so the screen
                 has to say so — and show the gap it was built to expose. */
              <div className="flex flex-col items-end gap-1">
                <span className="text-xs px-2.5 py-1 rounded-full flex items-center gap-1
                                 bg-[#065F46] text-[#D1FAE5]"
                  title={data.source_label}>
                  <CheckCircle2 className="w-3.5 h-3.5" />
                  {data.source_label ?? 'RealPay Client Billing report'}
                </span>
                {data.cross_check?.difference && (
                  <span className="text-xs text-[#9CA3AF]">
                    Graphite says {data.cross_check.graphite_total ?? '—'} · difference{' '}
                    {data.cross_check.difference}
                  </span>
                )}
                {!!data.legacy_undated_billing_rows && (
                  <span className="text-xs flex items-center gap-1 text-[#FCD34D]"
                    title={data.legacy_undated_note ?? undefined}>
                    <AlertTriangle className="w-3.5 h-3.5" />
                    {data.legacy_undated_billing_rows} older row(s) not in this total
                  </span>
                )}
              </div>
            ) : data && data.reconciled !== null && (
              <span className={`text-xs px-2.5 py-1 rounded-full flex items-center gap-1 ${data.reconciled ? 'bg-[#065F46] text-[#D1FAE5]' : 'bg-[#7F1D1D] text-[#FECACA]'}`}>
                {data.reconciled ? <CheckCircle2 className="w-3.5 h-3.5" /> : <AlertTriangle className="w-3.5 h-3.5" />}
                {data.reconciled ? 'Reconciled to import' : `Variance ${data.variance}`}
              </span>
            )}
          </div>
        </div>

        {/* Group cards + bar */}
        {loading ? (
          <div className="py-10 text-center text-[#6B7280]">Loading…</div>
        ) : !data?.data_present ? (
          <div className="py-10 text-center text-[#6B7280]">
            No RealPay collections in Graphite for this period.
          </div>
        ) : (
          <>
            {(data.groups_with_no_result?.length ?? 0) > 0 && (
              <div className="mt-5 rounded-lg px-4 py-3 text-sm flex items-start gap-2"
                   style={{ background: '#FEF3C7', color: '#92400E' }}>
                <AlertTriangle className="w-4 h-4 mt-0.5 flex-shrink-0" />
                <span>
                  <b>{data.groups_with_no_result!.join(' and ')}</b> show no collection result for this
                  period. {(data.awaiting_result_total || 0).toLocaleString()} debits were raised and
                  nothing came back, so these are <b>not</b> zeros — the result feed has not reported.
                  Do not read them as no business collected.
                </span>
              </div>
            )}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mt-5">
              {data.groups.map(g => (
                <Card key={g.key}>
                  <CardContent className="py-5">
                    <div className="flex items-center gap-2">
                      <span className="w-3 h-3 rounded-full" style={{ background: GROUP_COLOR[g.key] || '#9CA3AF' }} />
                      <div className="text-sm font-medium text-[#374151]">{g.label}</div>
                    </div>
                    {/* 🔴 A zero and a blank are NOT the same fact. When debits
                        were raised and no result ever came back, printing
                        "BWP 0.00" tells Finance the book collected nothing —
                        which is what this screen did for Corporate and Personal
                        all through the feed outage that began in June 2026. */}
                    {g.no_result_received ? (
                      <>
                        <div className="text-2xl font-bold mt-2" style={{ color: '#92400E' }}>
                          No result received
                        </div>
                        <div className="text-xs mt-1" style={{ color: '#92400E' }}>
                          {(g.awaiting_result || 0).toLocaleString()} debit{(g.awaiting_result || 0) === 1 ? '' : 's'} raised,
                          {' '}none came back — this is not a zero
                        </div>
                      </>
                    ) : (
                      <>
                        <div className="text-2xl font-bold text-[#0D1B2A] mt-2">{fmtMoney(g.collected)}</div>
                        <div className="text-xs text-[#6B7280] mt-1">
                          {g.collected_lines.toLocaleString()} collected · {g.attempts.toLocaleString()} attempts · {g.pct.toFixed(1)}%
                        </div>
                      </>
                    )}
                  </CardContent>
                </Card>
              ))}
            </div>

            {/* Stacked share bar */}
            <Card className="mt-5">
              <CardHeader><CardTitle className="text-[#0D1B2A] text-base">Share of collections</CardTitle></CardHeader>
              <CardContent>
                <div className="w-full h-6 rounded-full overflow-hidden flex">
                  {data.groups.map(g => (
                    <div key={g.key} style={{ width: `${g.pct}%`, background: GROUP_COLOR[g.key] || '#9CA3AF' }}
                      title={`${g.label}: ${g.pct.toFixed(1)}%`} />
                  ))}
                </div>
                <div className="flex flex-wrap gap-4 mt-3 text-xs text-[#374151]">
                  {data.groups.map(g => (
                    <span key={g.key} className="flex items-center gap-1.5">
                      <span className="w-2.5 h-2.5 rounded-full" style={{ background: GROUP_COLOR[g.key] || '#9CA3AF' }} />
                      {g.label} — {g.pct.toFixed(1)}%
                    </span>
                  ))}
                </div>
              </CardContent>
            </Card>

            {/* Unmapped prefixes transparency */}
            {data.unmapped_prefixes?.length ? (
              <div className="mt-4 rounded-md bg-[#FEF3C7] text-[#92400E] px-4 py-3 text-sm">
                <div className="flex items-center gap-2 font-medium">
                  <AlertTriangle className="w-4 h-4" /> Unmapped client-number prefixes (in &quot;Other&quot;)
                </div>
                <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1">
                  {data.unmapped_prefixes.map(u => (
                    <span key={u.prefix} className="font-mono text-xs">{u.prefix}: {u.count.toLocaleString()}</span>
                  ))}
                </div>
              </div>
            ) : null}
          </>
        )}

        {/* Assumptions */}
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
