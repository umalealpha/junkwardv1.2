'use client'

/**
 * /salvage/inventory/import — bulk-import salvage items from CSV / XLSX.
 *
 * CFO directive 2026-05-18: operator can already add items one-by-one
 * via /salvage/inventory/new; this surface handles batch intake (e.g.
 * 50-row import from a panel-beater handover spreadsheet).
 *
 * Flow:
 *   1. Pick file → parse client-side with `xlsx` → render preview table.
 *   2. Validate the preview locally (part_name required; item_code optional
 *      since 17-Sep-2026 — blank means the server generates it; no
 *      client-side duplicates among the codes that ARE supplied).
 *   3. Confirm → POST multipart to /api/v1/salvage-items/import/.
 *   4. Backend re-validates, creates rows in a transaction, and fires
 *      `post_intake_to_gl` per row. All-or-nothing on validation errors.
 *   5. Success summary links back to the inventory list.
 */
import { useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import {
  ArrowLeft, Upload, Download, Info, AlertCircle, CheckCircle2, Loader2,
} from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { getToken, SSO_SENTINEL_TOKEN } from '@/lib/api'

interface PreviewRow {
  row_number:    number
  item_code:     string
  part_name:     string
  claim_number:  string
  condition:     string
  status:        string
  asking_price:  string
  reserve_price: string
  cost_basis:    string
  location:      string
  received_date: string
  notes:         string
  warning?:      string
}

interface ImportResult {
  created:           number
  skipped:           number
  intake_jes_posted: number
  company:           string
  message:           string
}

interface ImportError {
  detail: string
  errors?: { row_number: number; message: string }[]
}

const COLUMN_SYNONYMS: Record<string, string[]> = {
  item_code:     ['item code', 'code', 'sku'],
  part_name:     ['part name', 'part', 'description', 'name'],
  claim_number:  ['claim number', 'claim', 'claim no'],
  condition:     ['condition'],
  status:        ['status'],
  asking_price:  ['asking price', 'asking', 'price'],
  reserve_price: ['reserve price', 'reserve', 'floor'],
  cost_basis:    ['cost basis', 'carrying', 'carrying value', 'cost'],
  location:      ['location', 'yard', 'shelf'],
  received_date: ['received date', 'date', 'intake date'],
  notes:         ['notes', 'comment', 'memo'],
}

function normaliseKey(s: string): string {
  return (s || '').trim().toLowerCase().replace(/[-_]/g, ' ').replace(/\s+/g, ' ')
}

function resolveColumn(header: string): string | null {
  const n = normaliseKey(header)
  for (const [canonical, syns] of Object.entries(COLUMN_SYNONYMS)) {
    if (syns.includes(n)) return canonical
  }
  return null
}

export default function ImportSalvageItemsPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const [file, setFile] = useState<File | null>(null)
  const [rows, setRows] = useState<PreviewRow[]>([])
  const [parseError, setParseError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [parsing, setParsing] = useState(false)
  const [result, setResult] = useState<ImportResult | null>(null)
  const [errResult, setErrResult] = useState<ImportError | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  async function handleFile(f: File | null) {
    setFile(f)
    setRows([])
    setParseError(null)
    setResult(null)
    setErrResult(null)
    if (!f) return
    setParsing(true)
    try {
      const xlsx = await import('xlsx')
      const buf = await f.arrayBuffer()
      const wb  = xlsx.read(buf, { type: 'array', cellDates: true })
      const sheet = wb.Sheets[wb.SheetNames[0]]
      if (!sheet) throw new Error('Workbook has no sheets.')
      const raw = xlsx.utils.sheet_to_json<Record<string, unknown>>(sheet, { defval: '' })
      if (!raw.length) throw new Error('Sheet has no data rows.')

      const headers = Object.keys(raw[0])
      const map: Record<string, string> = {}
      for (const h of headers) {
        const canonical = resolveColumn(h)
        if (canonical) map[h] = canonical
      }
      const canonicalCols = new Set(Object.values(map))
      // item_code is optional since 17-Sep-2026 — a blank cell, or no column
      // at all, means the server draws the next ML-#### on save.
      if (!canonicalCols.has('part_name')) {
        throw new Error(`File must include a "part_name" column. Found: ${headers.join(', ')}`)
      }

      const seen = new Set<string>()
      const out: PreviewRow[] = raw.map((r, i) => {
        const get = (canonical: string): string => {
          for (const [h, c] of Object.entries(map)) {
            if (c === canonical) {
              const v = r[h]
              if (v instanceof Date) return v.toISOString().slice(0, 10)
              return v == null ? '' : String(v).trim()
            }
          }
          return ''
        }
        const code = get('item_code')
        const name = get('part_name')
        let warning: string | undefined
        // A blank code is fine now — it is generated. Only a code the file
        // actually supplied can be a duplicate.
        if (!name) warning = 'part_name is blank'
        else if (code && seen.has(code)) warning = `duplicate item_code "${code}" in file`
        else if (code) seen.add(code)
        return {
          row_number:    i + 2,
          item_code:     code,
          part_name:     name,
          claim_number:  get('claim_number'),
          condition:     (get('condition') || 'fair').toLowerCase(),
          status:        (get('status') || 'available').toLowerCase(),
          asking_price:  get('asking_price') || '0',
          reserve_price: get('reserve_price') || '0',
          cost_basis:    get('cost_basis') || get('reserve_price') || '0',
          location:      get('location'),
          received_date: get('received_date'),
          notes:         get('notes'),
          warning,
        }
      })
      setRows(out)
    } catch (err) {
      setParseError(err instanceof Error ? err.message : String(err))
    } finally {
      setParsing(false)
    }
  }

  async function confirm() {
    if (!file || rows.length === 0) return
    if (rows.some(r => r.warning)) {
      setErrResult({ detail: 'Fix client-side warnings before importing.' })
      return
    }
    setSubmitting(true)
    setErrResult(null)
    try {
      const fd = new FormData()
      fd.append('file', file)
      // Kgosi 87a249f3: no company is sent — the server records all salvage under Veritas Capital.
      fd.append('auto_post', 'true')

      const headers: Record<string, string> = {}
      try {
        const { acquireApiToken, SSO_API_CALLS_READY } = await import('@/auth/msal')
        if (SSO_API_CALLS_READY) {
          const t = await acquireApiToken()
          if (t) headers['Authorization'] = `Bearer ${t}`
        }
      } catch { /* fall through */ }
      if (!headers['Authorization']) {
        const t = getToken()
        if (t && t !== SSO_SENTINEL_TOKEN) headers['Authorization'] = `Token ${t}`
      }

      const r = await fetch('/api/v1/salvage-items/import/', {
        method: 'POST', body: fd, headers, credentials: 'include',
      })
      const data = await r.json().catch(() => ({}))
      if (r.ok) {
        setResult(data as ImportResult)
        setFile(null)
        setRows([])
        if (fileRef.current) fileRef.current.value = ''
      } else {
        setErrResult(data as ImportError)
      }
    } catch (err) {
      setErrResult({ detail: err instanceof Error ? err.message : 'Network error' })
    } finally {
      setSubmitting(false)
    }
  }

  function downloadTemplate() {
    const csv =
      // item_code left blank on purpose — the server fills in the next
      // ML-#### so nobody has to invent codes in a spreadsheet.
      'item_code,part_name,claim_number,condition,status,asking_price,reserve_price,cost_basis,location,received_date,notes\n' +
      ',Front bumper assembly,CLM-2026-0042,fair,available,5000,3500,3500,Yard A Bay 12,2026-05-18,Toyota Hilux 2018\n' +
      ',Engine block,CLM-2026-0042,good,available,15000,11000,11000,Yard A Bay 12,2026-05-18,\n'
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'salvage-import-template.csv'
    a.click()
    URL.revokeObjectURL(url)
  }

  const blockingWarnings = rows.filter(r => r.warning).length

  return (
    <div className="min-h-screen" style={{ background: theme.bg }}>
      <TopBar title="Import Salvage Items" />
      <div className="p-4 lg:p-6 max-w-[1200px] mx-auto space-y-5">
        <Link href="/salvage/inventory"
              className="inline-flex items-center gap-1.5 text-sm font-medium" style={{ color: theme.t2 }}>
          <ArrowLeft className="w-4 h-4" /> Back to inventory
        </Link>

        <div className="rounded-2xl p-4" style={{ background: theme.oL, border: `1px solid ${theme.orange}33` }}>
          <p className="text-xs leading-relaxed" style={{ color: theme.text }}>
            <Info className="w-3.5 h-3.5 inline mr-1" style={{ color: theme.orange }} />
            Each imported row creates a SalvageItem AND posts a balanced intake JE
            (<strong> Dr 1320 Salvage Inventory · Cr 105004 Salvages &amp; Recoveries </strong>at
            <strong> cost_basis</strong>). Validation is strict — if any row has a problem, nothing is
            committed.
            <span className="block mt-1">
              Items will land in company: <strong>Veritas Capital (VCM)</strong> — salvage is recorded there only.
            </span>
          </p>
        </div>

        <div className="rounded-2xl p-5 space-y-4"
             style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
          <div className="flex flex-wrap items-end gap-3 justify-between">
            <div className="flex-1 min-w-[280px]">
              <label className="block text-xs font-medium mb-1" style={{ color: theme.t2 }}>
                CSV or XLSX file
              </label>
              <input ref={fileRef} type="file" accept=".csv,.txt,.xlsx,.xls"
                     onChange={e => handleFile(e.target.files?.[0] || null)}
                     className="w-full text-sm file:mr-3 file:px-3 file:py-1.5 file:rounded-md file:border-0 file:text-xs file:font-semibold file:cursor-pointer"
                     style={{ color: theme.t2 }} />
              {file && (
                <p className="text-xs mt-1.5" style={{ color: theme.t2 }}>
                  {file.name} · {(file.size / 1024).toFixed(0)} KB
                  {parsing && <span className="ml-2"><Loader2 className="w-3 h-3 inline animate-spin" /> parsing…</span>}
                </p>
              )}
            </div>
            <button type="button" onClick={downloadTemplate}
                    className="inline-flex items-center gap-1.5 h-9 px-3 rounded-md text-sm font-semibold"
                    style={{ background: theme.g100, color: theme.text, border: `1px solid ${theme.cardBdr}` }}>
              <Download className="w-3.5 h-3.5" /> Download CSV template
            </button>
          </div>

          {parseError && (
            <div className="rounded-lg p-3 text-sm flex items-start gap-2"
                 style={{ background: theme.erB, color: theme.er, border: `1px solid ${theme.er}40` }}>
              <AlertCircle className="w-4 h-4 mt-0.5 flex-shrink-0" /> {parseError}
            </div>
          )}

          {rows.length > 0 && (
            <>
              <div className="flex items-center justify-between text-sm">
                <span className="font-semibold" style={{ color: theme.text }}>
                  Preview · {rows.length} row{rows.length === 1 ? '' : 's'}
                </span>
                <span className="text-xs"
                      style={{ color: blockingWarnings > 0 ? theme.er : theme.ok }}>
                  {blockingWarnings === 0
                    ? 'No client-side warnings'
                    : `${blockingWarnings} row(s) flagged — fix before import`}
                </span>
              </div>

              <div className="overflow-x-auto rounded-lg border max-h-80"
                   style={{ borderColor: theme.cardBdr }}>
                <table className="w-full text-xs">
                  <thead className="sticky top-0" style={{ background: theme.g100 }}>
                    <tr className="text-left">
                      {['#', 'Code', 'Part', 'Claim', 'Cond', 'Status', 'Asking', 'Reserve', 'Cost basis', 'Date', 'Note']
                        .map(h => (
                          <th key={h} className="px-2 py-1.5 font-semibold" style={{ color: theme.t2 }}>{h}</th>
                        ))}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.slice(0, 200).map(r => (
                      <tr key={r.row_number} className="border-t"
                          style={{
                            borderColor: theme.cardBdr,
                            background: r.warning ? theme.erB : 'transparent',
                          }}>
                        <td className="px-2 py-1 tabular-nums" style={{ color: theme.t2 }}>{r.row_number}</td>
                        <td className="px-2 py-1 font-mono" style={{ color: theme.text }}>{r.item_code || '—'}</td>
                        <td className="px-2 py-1 truncate max-w-[180px]" style={{ color: theme.text }}>{r.part_name}</td>
                        <td className="px-2 py-1 font-mono" style={{ color: theme.t2 }}>{r.claim_number}</td>
                        <td className="px-2 py-1" style={{ color: theme.t2 }}>{r.condition}</td>
                        <td className="px-2 py-1" style={{ color: theme.t2 }}>{r.status}</td>
                        <td className="px-2 py-1 text-right tabular-nums" style={{ color: theme.text }}>{r.asking_price}</td>
                        <td className="px-2 py-1 text-right tabular-nums" style={{ color: theme.text }}>{r.reserve_price}</td>
                        <td className="px-2 py-1 text-right tabular-nums font-semibold" style={{ color: theme.text }}>{r.cost_basis}</td>
                        <td className="px-2 py-1 font-mono text-[10px]" style={{ color: theme.t2 }}>{r.received_date || ''}</td>
                        <td className="px-2 py-1" style={{ color: r.warning ? theme.er : theme.t2 }}>{r.warning || r.notes}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {rows.length > 200 && (
                <p className="text-[11px]" style={{ color: theme.t2 }}>Showing first 200 of {rows.length} rows.</p>
              )}
            </>
          )}

          {errResult && (
            <div className="rounded-lg p-3 text-sm space-y-2"
                 style={{ background: theme.erB, color: theme.er, border: `1px solid ${theme.er}40` }}>
              <div className="flex items-center gap-2 font-semibold">
                <AlertCircle className="w-4 h-4" /> Import rejected — nothing committed
              </div>
              <div className="text-xs" style={{ color: theme.text }}>{errResult.detail}</div>
              {errResult.errors && errResult.errors.length > 0 && (
                <ul className="text-[11px] font-mono space-y-0.5 max-h-40 overflow-y-auto"
                    style={{ color: theme.t2 }}>
                  {errResult.errors.slice(0, 30).map((e, i) =>
                    <li key={i}>Row {e.row_number}: {e.message}</li>
                  )}
                  {errResult.errors.length > 30 && <li>…and {errResult.errors.length - 30} more.</li>}
                </ul>
              )}
            </div>
          )}

          {result && (
            <div className="rounded-lg p-3 text-sm flex items-start gap-2"
                 style={{ background: theme.okB, color: theme.ok, border: `1px solid ${theme.ok}40` }}>
              <CheckCircle2 className="w-4 h-4 mt-0.5 flex-shrink-0" />
              <div>
                <div className="font-semibold">{result.message}</div>
                <div className="text-xs mt-1" style={{ color: theme.text }}>
                  Created {result.created} item{result.created === 1 ? '' : 's'} ·{' '}
                  {result.intake_jes_posted} intake JE{result.intake_jes_posted === 1 ? '' : 's'} posted.
                </div>
                <Link href="/salvage/inventory" className="text-xs font-semibold underline"
                      style={{ color: theme.orange }}>Back to inventory →</Link>
              </div>
            </div>
          )}

          <div className="flex items-center justify-end gap-2 pt-1">
            <Link href="/salvage/inventory"
                  className="px-3 py-1.5 rounded-lg text-sm font-medium"
                  style={{ background: theme.g100, color: theme.text, border: `1px solid ${theme.cardBdr}` }}>
              Cancel
            </Link>
            <button type="button" onClick={confirm}
                    disabled={submitting || rows.length === 0 || blockingWarnings > 0}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-semibold disabled:opacity-50"
                    style={{ background: theme.orange, color: '#fff' }}>
              {submitting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Upload className="w-3.5 h-3.5" />}
              {submitting ? 'Importing…' : `Confirm & import ${rows.length} row${rows.length === 1 ? '' : 's'}`}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
