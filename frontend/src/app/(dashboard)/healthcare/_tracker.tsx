'use client'

/**
 * Shared Healthcare Smart-Upload Tracker component.
 *
 * One page per kind (revenue / claims / treaty) — all three pages
 * import this and pass the kind + (optional) direction toggle.
 *
 * CFO directive 2026-06-05 (Tlamelo Chimidza thread) — three trackers
 * shipped end-to-end this session, no queuing.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Modal, ModalBody, ModalFooter } from '@/components/ui/modal'
import { Button } from '@/components/ui/button'
import { CheckCircle2, AlertTriangle, Upload, FileSpreadsheet, Clock, RefreshCw, Trash2, BarChart3 } from 'lucide-react'
import { apiFetch } from '@/lib/api'

type Kind = 'revenue' | 'claims' | 'treaty'

interface UploadRow {
  id: string
  kind: Kind
  direction: 'na' | 'inbound' | 'outbound'
  file_name: string
  file_size: number
  period_label: string
  total_rows: number
  lives_count: number
  gross_amount: string
  paid_amount: string
  status: 'parsed' | 'failed'
  error_log: string
  uploaded_by: string
  uploaded_at: string
}

interface PostResp {
  id?: string
  status?: string
  detail?: string
  total_rows?: number
  lives_count?: number
  gross_amount?: string
  paid_amount?: string
  period_label?: string
  headers?: string[]
}

const TITLES: Record<Kind, string> = {
  revenue: 'Health Care — Revenue (Premium Bordereaux)',
  claims:  'Health Care — Claims (AFT Weekly Remit)',
  treaty:  'Health Care — Treaty (Bordereaux IN / OUT)',
}

const HELPS: Record<Kind, string> = {
  revenue: 'Upload the GWP Master xlsx (monthly Premium Bordereaux). Omni parses every member row, computes lives count + premium totals, and stores the raw rows for the dashboard.',
  claims:  'Upload the ADI_AFT_PmtRun_YYYYMMDD.xlsx (weekly remit). Omni reads the Claim Lines sheet, totals charged + paid amounts, and ties them to the Product / Group / Practice totals.',
  treaty:  'Upload the monthly Treaty bordereaux. Pick IN (received from broker) or OUT (sent to broker). Both flow into the same 6-sub-register store for monthly cession reconciliation.',
}

function fmt(s: string | number | null | undefined): string {
  const n = Number(s ?? 0)
  if (!isFinite(n)) return String(s ?? '')
  return new Intl.NumberFormat('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n)
}
function kb(n: number): string {
  return n < 1024 ? `${n} B` : n < 1024 * 1024 ? `${(n/1024).toFixed(1)} KB` : `${(n/1024/1024).toFixed(2)} MB`
}

// ── Monthly / YTD summary (Tlamelo 2026-06-10) ────────────────────────────
// One /health/summary/ call returns month aggregates for all three kinds, so
// the Claims tab can compute loss ratios against the Revenue months.

interface SummaryMonth {
  period_year: number | null
  period_month: number | null
  label: string
  uploads: number
  rows: number
  lives: number
  gross: string
  paid: string
}
type SummaryResp = { kinds: Record<Kind, SummaryMonth[]> }

const VAT_RATE = 0.14            // parsed revenue gross is the Incl.-VAT total
const TREATY_QS = 0.9            // Hannover Re 90% quota share

// Financial year runs Jul → Jun; named by the ending year (FY26 = Jul25–Jun26).
function fyOf(y: number, m: number): number { return m >= 7 ? y + 1 : y }

function SummaryPanel({ kind, summary }: { kind: Kind; summary: SummaryResp | null }) {
  const months = summary?.kinds?.[kind] ?? []
  const revenueByKey = useMemo(() => {
    const map = new Map<string, SummaryMonth>()
    for (const r of summary?.kinds?.revenue ?? []) map.set(`${r.period_year}-${r.period_month}`, r)
    return map
  }, [summary])

  const dated = months.filter((x) => x.period_year && x.period_month)
  const latest = dated[dated.length - 1]
  const currentFY = latest ? fyOf(latest.period_year!, latest.period_month!) : null
  const ytdRows = currentFY === null ? [] : dated.filter((x) => fyOf(x.period_year!, x.period_month!) === currentFY)

  const sum = (xs: SummaryMonth[], f: (x: SummaryMonth) => number) => xs.reduce((a, x) => a + f(x), 0)
  const totalCols = (xs: SummaryMonth[]) => ({
    lives: sum(xs, (x) => x.lives),
    gross: sum(xs, (x) => Number(x.gross)),
    paid:  sum(xs, (x) => Number(x.paid)),
  })

  if (months.length === 0) return null

  // Claims arrive as WEEKLY AFT runs — several uploads per month is normal
  // (Tlamelo's target dashboard has a "Number of Runs" column). Only revenue
  // and treaty are one-bordereaux-per-month, where >1 means double-counting.
  const dupBadge = (u: number) => {
    if (kind === 'claims') {
      return u > 0
        ? <span className="ml-1 inline-flex px-1.5 py-0.5 rounded text-[10px] font-semibold bg-[#EFF6FF] text-[#1D4ED8]" title="Weekly AFT payment runs counted in this month.">{u} run{u === 1 ? '' : 's'}</span>
        : null
    }
    return u > 1
      ? <span className="ml-1 inline-flex px-1.5 py-0.5 rounded text-[10px] font-semibold bg-[#FEF3C7] text-[#92400E]" title="More than one upload counted in this month — remove duplicates so totals are not double-counted.">{u} uploads</span>
      : null
  }

  const revExcl = (g: number) => g / (1 + VAT_RATE)

  function cellsFor(x: { lives: number; gross: number; paid: number }, prevGross: number | null, ky: string) {
    if (kind === 'revenue') {
      const excl = revExcl(x.gross)
      const mom = prevGross !== null && prevGross !== 0 ? ((x.gross - prevGross) / prevGross) * 100 : null
      return (
        <>
          <td className="px-3 py-2 text-right text-xs tabular-nums">{x.lives}</td>
          <td className="px-3 py-2 text-right text-xs tabular-nums">{fmt(excl)}</td>
          <td className="px-3 py-2 text-right text-xs tabular-nums">{fmt(x.gross - excl)}</td>
          <td className="px-3 py-2 text-right text-xs tabular-nums font-medium">{fmt(x.gross)}</td>
          <td className={`px-3 py-2 text-right text-xs tabular-nums ${mom === null ? 'text-[#9CA3AF]' : mom >= 0 ? 'text-[#059669]' : 'text-[#DC2626]'}`}>
            {mom === null ? '—' : `${mom >= 0 ? '+' : ''}${mom.toFixed(1)}%`}
          </td>
        </>
      )
    }
    if (kind === 'claims') {
      const rev = revenueByKey.get(ky)
      const revGrossExcl = rev ? revExcl(Number(rev.gross)) : null
      const ratio = revGrossExcl ? (x.paid / revGrossExcl) * 100 : null
      return (
        <>
          <td className="px-3 py-2 text-right text-xs tabular-nums">{x.lives}</td>
          <td className="px-3 py-2 text-right text-xs tabular-nums">{fmt(x.gross)}</td>
          <td className="px-3 py-2 text-right text-xs tabular-nums font-medium">{fmt(x.paid)}</td>
          <td className={`px-3 py-2 text-right text-xs tabular-nums ${ratio === null ? 'text-[#9CA3AF]' : ratio > 100 ? 'text-[#DC2626]' : 'text-[#059669]'}`}>
            {ratio === null ? '—' : `${ratio.toFixed(1)}%`}
          </td>
        </>
      )
    }
    // treaty
    const net = TREATY_QS * (x.gross - x.paid)
    return (
      <>
        <td className="px-3 py-2 text-right text-xs tabular-nums">{x.lives}</td>
        <td className="px-3 py-2 text-right text-xs tabular-nums">{fmt(x.gross)}</td>
        <td className="px-3 py-2 text-right text-xs tabular-nums">{fmt(x.paid)}</td>
        <td className="px-3 py-2 text-right text-xs tabular-nums">{fmt(x.gross * TREATY_QS)}</td>
        <td className="px-3 py-2 text-right text-xs tabular-nums">{fmt(x.paid * TREATY_QS)}</td>
        <td className={`px-3 py-2 text-right text-xs tabular-nums font-medium ${net < 0 ? 'text-[#DC2626]' : ''}`}>{fmt(net)}</td>
      </>
    )
  }

  const HEADERS: Record<Kind, string[]> = {
    revenue: ['Lives', 'GWP Excl. VAT', 'VAT @14%', 'GWP Incl. VAT', 'MoM'],
    claims:  ['Lives', 'Charged (BWP)', 'Paid (BWP)', 'Loss Ratio'],
    treaty:  ['Members', 'Premium (BWP)', 'Claims (BWP)', `${TREATY_QS * 100}% Premium`, `${TREATY_QS * 100}% Claims`, 'RI Net'],
  }
  const FOOTNOTES: Record<Kind, string> = {
    revenue: 'Excl.-VAT and VAT columns are derived at 14% from the parsed Incl.-VAT total. MoM compares Incl.-VAT totals.',
    claims:  'Loss ratio = claims paid ÷ same-month GWP excl. VAT (from the Revenue uploads). Months without a revenue upload show —.',
    treaty:  `${TREATY_QS * 100}% columns and RI Net are derived at the ${TREATY_QS * 100}% quota share from the parsed 100% totals. Brokerage not deducted.`,
  }

  let prevGross: number | null = null
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm flex items-center gap-2">
          <BarChart3 className="w-4 h-4 text-[#F4A623]" /> Monthly summary {currentFY ? `· YTD FY${String(currentFY).slice(-2)}` : ''}
        </CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        <table className="w-full text-sm border-collapse">
          <thead className="bg-[#0D1B2A] text-white">
            <tr>
              <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider">Month</th>
              {HEADERS[kind].map((h) => (
                <th key={h} className="px-3 py-2 text-right text-xs font-semibold uppercase tracking-wider">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-[#E5E7EB] bg-white">
            {months.map((x) => {
              const ky = `${x.period_year}-${x.period_month}`
              const row = (
                <tr key={ky + x.label}>
                  <td className="px-3 py-2 text-xs text-[#111827] font-medium">{x.label}{dupBadge(x.uploads)}</td>
                  {cellsFor({ lives: x.lives, gross: Number(x.gross), paid: Number(x.paid) }, x.period_year ? prevGross : null, ky)}
                </tr>
              )
              if (x.period_year) prevGross = Number(x.gross)
              return row
            })}
            {ytdRows.length > 0 && (
              <tr className="bg-[#FFF7ED] font-semibold">
                <td className="px-3 py-2 text-xs text-[#9A3412]">YTD FY{String(currentFY).slice(-2)} ({ytdRows.length} month{ytdRows.length === 1 ? '' : 's'})</td>
                {cellsFor(totalCols(ytdRows), null, '')}
              </tr>
            )}
            {dated.length > ytdRows.length && (
              <tr className="bg-[#0D1B2A] text-white font-semibold">
                <td className="px-3 py-2 text-xs">Inception to date ({dated.length} months)</td>
                {cellsFor(totalCols(dated), null, '')}
              </tr>
            )}
          </tbody>
        </table>
        <p className="px-4 py-2 text-[11px] text-[#6B7280]">{FOOTNOTES[kind]}</p>
      </CardContent>
    </Card>
  )
}

export function HealthcareTracker({ kind }: { kind: Kind }) {
  const [direction, setDirection] = useState<'inbound' | 'outbound'>('inbound')
  const [rows, setRows] = useState<UploadRow[]>([])
  const [summary, setSummary] = useState<SummaryResp | null>(null)
  const [busy, setBusy] = useState(false)
  const [deleting, setDeleting] = useState<string>('')
  // HC-DEL-01 (CFO 2026-08-15, Manus QC R2): a single window.confirm() sat
  // between one misclick and 9 months of GWP restated (its own tooltip warns
  // that deleting "removes its totals from the dashboard and summary
  // immediately"). Typed confirm: the user has to type the file name before
  // the red Delete arms. Same shape as SEC-DEL-01 on /settings/secrets.
  const [pendingDelete, setPendingDelete] = useState<UploadRow | null>(null)
  const [deleteText, setDeleteText] = useState('')
  const [result, setResult] = useState<PostResp | null>(null)
  const [err, setErr] = useState<string>('')
  const inputRef = useRef<HTMLInputElement | null>(null)

  const refresh = useCallback(async () => {
    try {
      // apiFetch attaches the Bearer/Token auth header (raw fetch didn't —
      // that 401'd every upload for SSO users). It also drops Content-Type
      // for FormData on the POST path below.
      const j = await apiFetch<{ results?: UploadRow[] }>(`/health/uploads/?kind=${kind}`)
      setRows(Array.isArray(j.results) ? j.results : [])
    } catch (e: any) {
      // ignore — list is best-effort
    }
    try {
      const s = await apiFetch<SummaryResp>('/health/summary/')
      setSummary(s)
    } catch (e: any) {
      // ignore — summary is best-effort
    }
  }, [kind])

  useEffect(() => { refresh() }, [refresh])

  function onDelete(u: UploadRow) {
    // Arm the typed-confirm modal — the actual delete fires from confirmDelete().
    setPendingDelete(u)
    setDeleteText('')
  }

  async function confirmDelete() {
    if (!pendingDelete) return
    if (deleteText.trim() !== pendingDelete.file_name) return   // safety; UI also disables
    setDeleting(pendingDelete.id); setErr('')
    const id = pendingDelete.id
    try {
      await apiFetch(`/health/uploads/${id}/`, { method: 'DELETE' })
      setPendingDelete(null)
      setDeleteText('')
      await refresh()
    } catch (e: any) {
      setErr(e?.message || 'Delete failed.')
    } finally {
      setDeleting('')
    }
  }

  async function onFile(f: File) {
    setBusy(true); setErr(''); setResult(null)
    try {
      const fd = new FormData()
      fd.append('kind', kind)
      fd.append('direction', kind === 'treaty' ? direction : 'na')
      fd.append('file', f)
      // apiFetch attaches auth + drops Content-Type for FormData, and throws
      // on non-2xx with the flattened DRF detail (e.g. parse errors).
      const j = await apiFetch<PostResp>('/health/upload/', { method: 'POST', body: fd })
      setResult(j)
      await refresh()
    } catch (e: any) {
      setErr(e?.message || 'Upload failed.')
    } finally {
      setBusy(false)
      if (inputRef.current) inputRef.current.value = ''
    }
  }

  return (
    <div className="flex flex-col min-h-screen">
      <TopBar
        title={TITLES[kind]}
        breadcrumbs={[{ label: 'Health Care' }, { label: kind[0].toUpperCase() + kind.slice(1) }]}
      />
      <div className="flex-1 p-6 space-y-4">

        <Card>
          <CardContent className="py-4">
            <div className="flex items-start gap-3">
              <FileSpreadsheet className="w-5 h-5 text-[#F4A623] mt-0.5" />
              <div className="flex-1">
                <p className="text-sm text-[#0D1B2A] font-medium">{HELPS[kind]}</p>
                <p className="text-xs text-[#6B7280] mt-1">
                  Accepted: <code>.xlsx</code>. Max 25 MB. Omni stores up to the first 5,000 rows for display;
                  totals are computed over the whole file.
                </p>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Upload zone */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm flex items-center gap-2">
              <Upload className="w-4 h-4 text-[#F4A623]" /> Upload bordereaux
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex items-center gap-3 flex-wrap">
              {kind === 'treaty' && (
                <div className="flex items-center gap-2 mr-2">
                  <label className="text-xs font-medium text-[#374151]">Direction:</label>
                  <select
                    value={direction}
                    onChange={(e) => setDirection(e.target.value as any)}
                    className="h-9 px-2 rounded-md text-sm border border-[#D1D5DB] bg-white text-[#1F2937]"
                  >
                    <option value="inbound">Received from broker</option>
                    <option value="outbound">Sent to broker</option>
                  </select>
                </div>
              )}
              <input
                ref={inputRef}
                type="file"
                aria-label="Upload workbook (.xlsx)"
                accept=".xlsx"
                disabled={busy}
                onChange={(e) => {
                  const f = e.target.files?.[0]
                  if (f) onFile(f)
                }}
                className="text-sm"
              />
              <button
                type="button"
                onClick={refresh}
                className="inline-flex items-center gap-1.5 h-9 px-3 rounded-md text-xs font-semibold bg-[#F3F4F6] text-[#0D1B2A] border border-[#E5E7EB]"
              >
                <RefreshCw className="w-3.5 h-3.5" /> Refresh
              </button>
            </div>

            {busy && (
              <p className="text-xs text-[#6B7280] mt-3 flex items-center gap-2">
                <Clock className="w-3.5 h-3.5 animate-pulse" /> Parsing…
              </p>
            )}
            {err && (
              <div className="mt-3 px-3 py-2 rounded-md bg-[#FEF2F2] border border-[#FECACA] text-xs text-[#7F1D1D] flex items-start gap-2">
                <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
                <div><b>Upload rejected.</b> {err}</div>
              </div>
            )}
            {result && (
              <div className="mt-3 px-3 py-2 rounded-md bg-[#ECFDF5] border border-[#A7F3D0] text-xs text-[#065F46] flex items-start gap-2">
                <CheckCircle2 className="w-4 h-4 flex-shrink-0 mt-0.5" />
                <div>
                  <b>Parsed.</b> {result.total_rows ?? 0} row(s) · Lives {result.lives_count ?? 0} ·
                  Gross BWP {fmt(result.gross_amount || '0')} · Paid BWP {fmt(result.paid_amount || '0')}
                  {result.period_label ? <> · Period {result.period_label}</> : null}
                </div>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Monthly + YTD summary (Tlamelo 2026-06-10) */}
        <SummaryPanel kind={kind} summary={summary} />

        {/* Recent uploads */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm flex items-center gap-2">
              <FileSpreadsheet className="w-4 h-4 text-[#F4A623]" /> Recent uploads
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            {rows.length === 0 ? (
              <p className="px-4 py-6 text-sm text-[#6B7280] text-center">No uploads yet. Drop a file above to get started.</p>
            ) : (
              <table className="w-full text-sm border-collapse">
                <thead className="bg-[#0D1B2A] text-white">
                  <tr>
                    <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider">File</th>
                    <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider">Period</th>
                    {kind === 'treaty' && <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider">Direction</th>}
                    <th className="px-3 py-2 text-right text-xs font-semibold uppercase tracking-wider">Rows</th>
                    <th className="px-3 py-2 text-right text-xs font-semibold uppercase tracking-wider">Lives</th>
                    <th className="px-3 py-2 text-right text-xs font-semibold uppercase tracking-wider">Gross (BWP)</th>
                    <th className="px-3 py-2 text-right text-xs font-semibold uppercase tracking-wider">Paid (BWP)</th>
                    <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider">By</th>
                    <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider">When</th>
                    <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider">Status</th>
                    <th className="px-3 py-2 text-right text-xs font-semibold uppercase tracking-wider"></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#E5E7EB] bg-white">
                  {rows.map((u) => (
                    <tr key={u.id}>
                      <td className="px-3 py-2 text-[#111827] text-xs">
                        <div className="font-medium truncate" title={u.file_name} style={{ maxWidth: 280 }}>{u.file_name}</div>
                        <div className="text-[10px] text-[#9CA3AF]">{kb(u.file_size)}</div>
                      </td>
                      <td className="px-3 py-2 text-xs text-[#374151]">{u.period_label || '—'}</td>
                      {kind === 'treaty' && (
                        <td className="px-3 py-2 text-xs text-[#374151]">
                          {u.direction === 'inbound' ? 'IN — Received' : u.direction === 'outbound' ? 'OUT — Sent' : '—'}
                        </td>
                      )}
                      <td className="px-3 py-2 text-right text-xs tabular-nums">{u.total_rows}</td>
                      <td className="px-3 py-2 text-right text-xs tabular-nums">{u.lives_count}</td>
                      <td className="px-3 py-2 text-right text-xs tabular-nums">{fmt(u.gross_amount)}</td>
                      <td className="px-3 py-2 text-right text-xs tabular-nums">{fmt(u.paid_amount)}</td>
                      <td className="px-3 py-2 text-xs text-[#6B7280]">{u.uploaded_by || '—'}</td>
                      <td className="px-3 py-2 text-xs text-[#6B7280]">{new Date(u.uploaded_at).toLocaleString('en-BW')}</td>
                      <td className="px-3 py-2">
                        {u.status === 'parsed'
                          ? <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-medium bg-[#ECFDF5] text-[#059669]"><CheckCircle2 className="w-3 h-3" /> Parsed</span>
                          : <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-medium bg-[#FEF2F2] text-[#DC2626]" title={u.error_log}><AlertTriangle className="w-3 h-3" /> Failed</span>
                        }
                      </td>
                      <td className="px-3 py-2 text-right">
                        <button
                          type="button"
                          onClick={() => onDelete(u)}
                          disabled={deleting === u.id}
                          title="Delete this upload (removes its totals from the dashboard and summary)"
                          className="inline-flex items-center justify-center w-7 h-7 rounded-md text-[#9CA3AF] hover:text-[#DC2626] hover:bg-[#FEF2F2] disabled:opacity-40"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </CardContent>
        </Card>

      </div>

      {/* HC-DEL-01 — typed confirm before wiping an upload out of the totals */}
      <Modal
        open={pendingDelete !== null}
        onOpenChange={(v) => { if (!v && !deleting) { setPendingDelete(null); setDeleteText('') } }}
        title="Delete this upload?"
        description="Its totals leave the dashboard and the monthly summary immediately. There is no undo — re-upload the file to restore it."
        size="sm"
      >
        <ModalBody className="space-y-4">
          <div className="flex items-start gap-3 bg-[#FEF2F2] border border-[#FEE2E2] rounded-lg p-3">
            <AlertTriangle className="w-5 h-5 text-[#DC2626] flex-shrink-0 mt-0.5" />
            <div className="text-sm text-[#7F1D1D]">
              <p className="font-semibold break-all">{pendingDelete?.file_name}</p>
              <p className="mt-1">
                {pendingDelete?.period_label || 'no period'} · Gross BWP {pendingDelete ? fmt(pendingDelete.gross_amount) : ''}
              </p>
              <p className="mt-1">
                Deleting this row is a restatement — the numbers on every dashboard tile
                and the monthly summary change the moment you click.
              </p>
            </div>
          </div>
          <div>
            <label className="block text-xs font-medium text-[#374151] mb-1">
              Type the file name to enable delete:
              <span className="ml-1 font-mono text-[#0D1B2A] break-all">{pendingDelete?.file_name}</span>
            </label>
            <input
              type="text"
              value={deleteText}
              onChange={(e) => setDeleteText(e.target.value)}
              placeholder={pendingDelete?.file_name || ''}
              autoComplete="off"
              className="w-full bg-white border border-[#D1D5DB] rounded-lg px-3 py-2 text-sm text-[#111827] focus:outline-none focus:border-[#DC2626] focus:ring-[3px] focus:ring-[rgba(220,38,38,0.1)] transition-all font-mono"
            />
          </div>
        </ModalBody>
        <ModalFooter>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => { setPendingDelete(null); setDeleteText('') }}
            disabled={deleting !== ''}
          >
            Cancel
          </Button>
          <Button
            variant="danger"
            size="sm"
            loading={deleting !== ''}
            disabled={!pendingDelete || deleteText.trim() !== pendingDelete.file_name}
            onClick={confirmDelete}
          >
            Delete permanently
          </Button>
        </ModalFooter>
      </Modal>
    </div>
  )
}
