'use client'

/**
 * Shared NBFIRA workflow components — Phase 4.
 *
 * <WorkflowBar> — review / approve / reject / reopen / lock / submit /
 * export-xlsx / audit-log buttons, gated on `status`. POSTs to the
 * matching ViewSet @action endpoints.
 *
 * <AuditLogPanel> — slide-out drawer showing immutable audit-log rows.
 *
 * <ScheduleTable> — render lines grouped by section with formula tip on
 * hover. Used by both quarterly + annual pages.
 *
 * <FiledReturnPanel> — attach the return actually FILED with NBFIRA against the
 * period, so it can be compared with what Omni computes from the GL
 * (CFO 2026-08-17).
 */

import { useEffect, useState } from 'react'
import {
  CheckCircle2, XCircle, RotateCcw, Lock, Send, Download,
  History, Loader2, AlertCircle, X, Upload,
} from 'lucide-react'
import { useTheme } from '@/contexts/ThemeContext'
import { authedHrisFetch } from '../../hris/_shared'

export interface ReturnLine {
  id: string
  schedule: string
  section: string
  line_code: string
  label: string
  value: string
  sort_order: number
  source_accounts: string[]
  formula: string
}

export interface ReturnSummary {
  id: string
  type: string
  period_label: string
  period_start: string | null
  period_end: string | null
  status: string
  company: string | null
  created_at: string | null
}

export interface FiledDocument {
  id: string
  original_name: string
  statement: string
  size_bytes: number
  content_type: string
  file_hash_sha256: string
  notes: string
  uploaded_by: string
  uploaded_at: string | null
  parsed: boolean
  parse_note: string
  download_url: string
}
export interface ReturnDetail extends ReturnSummary {
  schedules: string[]
  lines_by_schedule: Record<string, ReturnLine[]>
  filed_documents?: FiledDocument[]
}

interface AuditRow {
  id: string
  action: string
  user: string
  username: string
  comment: string
  timestamp: string
  before: Record<string, unknown>
  after:  Record<string, unknown>
  ip: string
}

const STATUS_FLOW: Record<string, string[]> = {
  draft:     ['review'],
  reopened:  ['review'],
  reviewed:  ['approve', 'reject'],
  approved:  ['lock', 'reopen'],
  rejected:  ['reopen'],
  locked:    ['submit', 'reopen'],
  submitted: [],
}

function fmtNum(s: string): string {
  const n = Number(s)
  if (!isFinite(n)) return s
  return new Intl.NumberFormat('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n)
}

// ─── Workflow bar ─────────────────────────────────────────────────────
export function WorkflowBar({
  ret, onMutate, onOpenAudit,
}: {
  ret: ReturnDetail
  onMutate: (next: ReturnDetail) => void
  onOpenAudit: () => void
}) {
  const { theme } = useTheme()
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr]   = useState<string | null>(null)

  const allowed = STATUS_FLOW[ret.status] || []

  async function post(transition: string, body: Record<string, unknown> = {}) {
    if (busy) return
    let comment = ''
    let filing  = ''
    let ack     = ''
    if (transition === 'reject' || transition === 'reopen') {
      comment = window.prompt(`Comment for ${transition} (required):`) || ''
      if (!comment.trim()) return
    } else if (['review', 'approve', 'lock'].includes(transition)) {
      comment = window.prompt(`Comment for ${transition} (optional):`) || ''
    } else if (transition === 'submit_to_nbfira') {
      filing = window.prompt('NBFIRA filing reference (optional):') || ''
      ack    = window.prompt('NBFIRA acknowledgement reference (optional):') || ''
      comment = window.prompt('Comment (optional):') || ''
    }
    setBusy(transition); setErr(null)
    try {
      const urlSeg = transition.replace(/_/g, '-')
      const r = await authedHrisFetch(`/api/v1/nbfira/returns/${ret.id}/${urlSeg}/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...body, comment, filing_reference: filing, acknowledgement_ref: ack }),
      })
      const data = await r.json()
      if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`)
      onMutate(data)
    } catch (e) {
      setErr(e instanceof Error ? e.message : `${transition} failed`)
    } finally { setBusy(null) }
  }

  async function exportXlsx() {
    setBusy('export'); setErr(null)
    try {
      const r = await authedHrisFetch(`/api/v1/nbfira/returns/${ret.id}/export-xlsx/`)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const blob = await r.blob()
      const cd   = r.headers.get('Content-Disposition') || ''
      const m    = cd.match(/filename="([^"]+)"/)
      const name = m ? m[1] : `ADIC_NBFIRA_${ret.type}_${ret.period_label}.xlsx`
      const url  = URL.createObjectURL(blob)
      const a    = document.createElement('a')
      a.href = url; a.download = name
      document.body.appendChild(a); a.click()
      a.remove(); URL.revokeObjectURL(url)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Export failed')
    } finally { setBusy(null) }
  }

  const btn = (key: string, label: string, Icon: any, bg: string) => (
    <button key={key} type="button" disabled={busy !== null}
            onClick={() => key === 'submit' ? post('submit_to_nbfira') : post(key)}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-opacity disabled:opacity-50"
            style={{ background: bg, color: '#fff' }}>
      {busy === key || (key === 'submit' && busy === 'submit_to_nbfira')
        ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
        : <Icon className="w-3.5 h-3.5" />}
      {label}
    </button>
  )

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2 items-center">
        <span className="text-[11px] uppercase tracking-wider font-semibold"
              style={{ color: theme.t2 }}>Workflow:</span>
        {allowed.includes('review')  && btn('review',  'Mark Reviewed', CheckCircle2, '#3b82f6')}
        {allowed.includes('approve') && btn('approve', 'Approve',       CheckCircle2, '#10b981')}
        {allowed.includes('reject')  && btn('reject',  'Reject',        XCircle,      '#ef4444')}
        {allowed.includes('reopen')  && btn('reopen',  'Reopen',        RotateCcw,    '#6b7280')}
        {allowed.includes('lock')    && btn('lock',    'Lock',          Lock,         theme.navy)}
        {allowed.includes('submit')  && btn('submit',  'Submit to NBFIRA', Send,      theme.orange)}
        <div className="flex-1" />
        <button type="button" onClick={onOpenAudit}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold"
                style={{ background: theme.g100, color: theme.text, border: `1px solid ${theme.cardBdr}` }}>
          <History className="w-3.5 h-3.5" /> Audit log
        </button>
        <button type="button" onClick={exportXlsx} disabled={busy !== null}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-opacity disabled:opacity-50"
                style={{ background: theme.navy, color: '#fff' }}>
          {busy === 'export' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Download className="w-3.5 h-3.5" />}
          Export XLSX
        </button>
      </div>
      {err && (
        <div className="rounded-lg px-3 py-2 text-xs flex items-start gap-2"
             style={{ background: theme.erB, color: theme.er, border: `1px solid ${theme.er}40` }}>
          <AlertCircle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" /> <div>{err}</div>
        </div>
      )}
    </div>
  )
}

// ─── Audit log drawer ─────────────────────────────────────────────────
export function AuditLogPanel({
  returnId, open, onClose,
}: {
  returnId: string | null
  open: boolean
  onClose: () => void
}) {
  const { theme } = useTheme()
  const [rows, setRows]       = useState<AuditRow[]>([])
  const [loading, setLoading] = useState(false)
  const [err, setErr]         = useState<string | null>(null)

  useEffect(() => {
    if (!open || !returnId) return
    let cancelled = false
    setLoading(true); setErr(null)
    authedHrisFetch(`/api/v1/nbfira/returns/${returnId}/audit-log/`)
      .then(async r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        const data = await r.json()
        if (!cancelled) setRows(data.results || [])
      })
      .catch(e => !cancelled && setErr(e instanceof Error ? e.message : 'Load failed'))
      .finally(() => !cancelled && setLoading(false))
    return () => { cancelled = true }
  }, [open, returnId])

  if (!open) return null

  const tagColor = (a: string): string => {
    if (['create', 'generate'].includes(a)) return '#3b82f6'
    if (a === 'review')   return '#8b5cf6'
    if (a === 'approve')  return '#10b981'
    if (a === 'lock')     return theme.navy
    if (a === 'submit')   return theme.orange
    if (a === 'reject')   return '#ef4444'
    if (a === 'reopen')   return '#6b7280'
    if (a === 'export')   return '#0ea5e9'
    return theme.t2
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end" style={{ background: '#0008' }}
         onClick={onClose}>
      <div className="w-full max-w-xl h-full overflow-y-auto"
           style={{ background: theme.bg, borderLeft: `1px solid ${theme.cardBdr}` }}
           onClick={e => e.stopPropagation()}>
        <div className="sticky top-0 z-10 flex items-center justify-between px-5 py-3 border-b"
             style={{ background: theme.bg, borderColor: theme.cardBdr }}>
          <h3 className="font-semibold" style={{ color: theme.text }}>Audit log</h3>
          <button onClick={onClose} className="p-1 rounded hover:opacity-70"
                  style={{ color: theme.t2 }}>
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="p-5 space-y-3">
          {loading && (
            <div className="py-4 text-center text-sm" style={{ color: theme.t2 }}>
              <Loader2 className="w-4 h-4 inline animate-spin mr-1" /> loading…
            </div>
          )}
          {err && (
            <div className="rounded-lg px-3 py-2 text-sm flex items-start gap-2"
                 style={{ background: theme.erB, color: theme.er, border: `1px solid ${theme.er}40` }}>
              <AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5" /> <div>{err}</div>
            </div>
          )}
          {!loading && rows.length === 0 && (
            <p className="text-sm" style={{ color: theme.t2 }}>No audit entries yet.</p>
          )}
          {rows.map(r => (
            <div key={r.id} className="rounded-lg p-3"
                 style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
              <div className="flex items-center gap-2 mb-1">
                <span className="px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider"
                      style={{ background: tagColor(r.action) + '22', color: tagColor(r.action) }}>
                  {r.action}
                </span>
                <span className="text-xs" style={{ color: theme.text }}>{r.user || r.username}</span>
                <div className="flex-1" />
                <span className="text-[11px]" style={{ color: theme.t3 }}>
                  {new Date(r.timestamp).toLocaleString()}
                </span>
              </div>
              {r.comment && (
                <p className="text-xs mt-1" style={{ color: theme.t2 }}>{r.comment}</p>
              )}
              {(Object.keys(r.before).length > 0 || Object.keys(r.after).length > 0) && (
                <pre className="text-[10px] mt-1 p-2 rounded font-mono overflow-x-auto"
                     style={{ background: theme.g100, color: theme.t3 }}>
{Object.keys(r.before).length ? `before: ${JSON.stringify(r.before)}\n` : ''}{Object.keys(r.after).length ? `after:  ${JSON.stringify(r.after)}` : ''}
                </pre>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

// ─── Schedule table ────────────────────────────────────────────────────
export function ScheduleTable({
  lines, editable, onEdit,
}: {
  lines: ReturnLine[]
  editable: boolean
  onEdit?: (line: ReturnLine, newValue: string) => void
}) {
  const { theme } = useTheme()
  const grouped: Record<string, ReturnLine[]> = {}
  for (const ln of lines) {
    const k = ln.section || 'main'
    ;(grouped[k] ||= []).push(ln)
  }

  if (lines.length === 0) {
    return <p className="text-sm py-4" style={{ color: theme.t2 }}>No lines in this schedule.</p>
  }

  return (
    <>
      {Object.entries(grouped).map(([section, ls]) => (
        <div key={section} className="mb-5">
          {section !== 'main' && (
            <h4 className="text-[11px] font-semibold uppercase tracking-wider mb-2"
                style={{ color: theme.t3 }}>
              {section.replace(/_/g, ' ')}
            </h4>
          )}
          <table className="w-full text-sm">
            <thead>
              <tr style={{ borderBottom: `2px solid ${theme.cardBdr}` }}>
                <th className="text-left py-1.5 pr-3 text-xs font-semibold uppercase tracking-wider" style={{ color: theme.t2 }}>Line</th>
                <th className="text-left py-1.5 pr-3 text-xs font-semibold uppercase tracking-wider" style={{ color: theme.t2 }}>Label</th>
                <th className="text-right py-1.5 pr-3 text-xs font-semibold uppercase tracking-wider" style={{ color: theme.t2 }}>Value (P'000)</th>
              </tr>
            </thead>
            <tbody>
              {ls.map(l => (
                <tr key={l.id} title={l.formula || ''} className="border-b" style={{ borderColor: theme.cardBdr }}>
                  <td className="py-1.5 pr-3 font-mono text-xs" style={{ color: theme.t3 }}>{l.line_code}</td>
                  <td className="py-1.5 pr-3" style={{ color: theme.text }}>{l.label}</td>
                  <td className="py-1.5 pr-3 text-right font-mono tabular-nums" style={{ color: theme.text }}>
                    {editable
                      ? <input defaultValue={l.value}
                               onBlur={e => {
                                 const v = e.currentTarget.value.trim()
                                 if (v && v !== l.value && onEdit) onEdit(l, v)
                               }}
                               className="w-32 text-right px-1.5 py-0.5 rounded font-mono outline-none"
                               style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }} />
                      : fmtNum(l.value)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </>
  )
}

/* ------------------------------------------------------------------------
 * FiledReturnPanel — attach the return that was actually FILED with NBFIRA
 * (CFO 2026-08-17).
 *
 * Omni GENERATES its schedules from the GL. This panel holds the real filed
 * workbook against the same period so the two can be compared. Attaching is
 * allowed at every status, including locked and submitted — you normally only
 * have the filed copy AFTER submitting, so the workflow state must never block
 * it.
 *
 * Files are stored, hashed and audited. Since 2026-08-18 the A.1 tab can also
 * be READ — "Compare A.1" puts the filed figure beside Omni's own, line by
 * line. That is the only schedule whose layout has been checked against real
 * filed workbooks; the rest are stored as evidence only, because guessing a
 * regulator's cell positions produces confidently wrong comparisons.
 * ---------------------------------------------------------------------- */
const STATEMENT_CHOICES = ['', 'IS', 'A', 'A.1', 'B', 'C']

interface ReconcileRow {
  line_code:  string
  label:      string
  filed:      string | null
  omni:       string | null
  difference: string | null
  agrees:     boolean
}

interface Reconciliation {
  ok:       boolean
  error?:   string
  as_at?:   string
  units?:   string
  note?:    string
  rows:     ReconcileRow[]
  ratios?:  Record<string, string>
  summary?: { filed_pct: string | null; omni_pct: string | null; difference: string | null }
  method_warning?: string
}

function fmtSize(bytes: number): string {
  if (!bytes) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function FiledReturnPanel({ ret, onMutate }: {
  ret: ReturnDetail
  onMutate: () => void
}) {
  const { theme } = useTheme()
  const [file, setFile] = useState<File | null>(null)
  const [statement, setStatement] = useState('')
  const [notes, setNotes] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [cmp, setCmp] = useState<Reconciliation | null>(null)
  const [cmpDoc, setCmpDoc] = useState<string | null>(null)
  const docs = ret.filed_documents || []

  const upload = async () => {
    if (!file) return
    setBusy('upload'); setErr(null)
    try {
      const body = new FormData()
      body.append('file', file)
      if (statement) body.append('statement', statement)
      if (notes.trim()) body.append('notes', notes.trim())
      const r = await authedHrisFetch(
        `/api/v1/nbfira/returns/${ret.id}/upload-filed/`,
        { method: 'POST', body },   // no Content-Type: the browser sets the boundary
      )
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        setErr(d.detail || `Upload failed (HTTP ${r.status})`)
        return
      }
      setFile(null); setStatement(''); setNotes('')
      onMutate()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Upload failed')
    } finally {
      // Reset on SUCCESS as well as error, or the button stays dead after the
      // first use until a refresh (checklist L16).
      setBusy(null)
    }
  }

  // The API authenticates by Authorization HEADER, so a bare <a href> sends no
  // credentials and 401s in production — while an APITestCase download test
  // stays green because the test client is authenticated. Fetch it with the
  // session's header and hand the blob to the browser instead.
  // (memory reference_pdf_download_saveblob; Fable 5 caught this 2026-08-17.)
  const download = async (d: FiledDocument) => {
    setBusy(`dl-${d.id}`); setErr(null)
    try {
      const r = await authedHrisFetch(d.download_url)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const blob = await r.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = d.original_name || 'filed-return'
      document.body.appendChild(a); a.click()
      a.remove(); URL.revokeObjectURL(url)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not download that file')
    } finally {
      setBusy(null)
    }
  }

  // Read the filed A.1 tab and show it beside Omni's. Read-only — it never
  // writes a figure into the return.
  const compare = async (d: FiledDocument) => {
    if (cmpDoc === d.id) { setCmpDoc(null); setCmp(null); return }
    setBusy(`cmp-${d.id}`); setErr(null)
    try {
      const r = await authedHrisFetch(
        `/api/v1/nbfira/returns/${ret.id}/filed/${d.id}/reconcile/`)
      const data = await r.json()
      if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`)
      setCmp(data); setCmpDoc(d.id)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not read that file')
    } finally {
      setBusy(null)
    }
  }

  const remove = async (docId: string, name: string) => {
    if (!window.confirm(`Remove "${name}" from this period?`)) return
    setBusy(docId); setErr(null)
    try {
      const r = await authedHrisFetch(
        `/api/v1/nbfira/returns/${ret.id}/filed/${docId}/`, { method: 'DELETE' })
      if (!r.ok && r.status !== 204) {
        const d = await r.json().catch(() => ({}))
        setErr(d.detail || `Could not remove it (HTTP ${r.status})`)
        return
      }
      onMutate()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not remove that file')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="rounded-xl p-5"
         style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h3 className="text-sm font-semibold" style={{ color: theme.text }}>
            Filed return — the actual NBFIRA workbook
          </h3>
          <p className="text-[11px] mt-1 max-w-xl" style={{ color: theme.t2 }}>
            Attach the return you filed for <strong>{ret.period_label}</strong>. The
            figures above are what Omni works out from the general ledger; this is
            what was actually submitted, kept against the same period so the two
            can be compared.
          </p>
        </div>
        <span className="text-[11px] px-2 py-1 rounded" style={{ background: theme.g100, color: theme.t2 }}>
          {docs.length} file{docs.length === 1 ? '' : 's'} attached
        </span>
      </div>

      <div className="flex flex-wrap items-end gap-3 mt-4">
        <div>
          <label className="block text-xs font-medium mb-1" style={{ color: theme.t2 }}>
            Return file
          </label>
          <input type="file"
                 accept=".xlsx,.xlsm,.xlsb,.xls,.csv,.pdf"
                 onChange={e => { setFile(e.target.files?.[0] || null); setErr(null) }}
                 className="text-sm max-w-[280px]"
                 style={{ color: theme.text }} />
        </div>
        <div>
          <label className="block text-xs font-medium mb-1" style={{ color: theme.t2 }}>
            Which statement?
          </label>
          <select value={statement} onChange={e => setStatement(e.target.value)}
                  className="px-3 py-2 rounded-lg text-sm outline-none"
                  style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }}>
            {STATEMENT_CHOICES.map(c => (
              <option key={c || 'all'} value={c}>{c || 'Whole workbook'}</option>
            ))}
          </select>
        </div>
        <div className="flex-1 min-w-[200px]">
          <label className="block text-xs font-medium mb-1" style={{ color: theme.t2 }}>
            Note (optional)
          </label>
          <input value={notes} onChange={e => setNotes(e.target.value)}
                 placeholder="e.g. as filed 15 Aug, signed copy"
                 className="px-3 py-2 rounded-lg text-sm outline-none w-full"
                 style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }} />
        </div>
        <button type="button" onClick={upload} disabled={!file || busy !== null}
                className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold transition-opacity disabled:opacity-50"
                style={{ background: theme.orange, color: '#fff' }}>
          {busy === 'upload' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />}
          {busy === 'upload' ? 'Attaching…' : 'Attach return'}
        </button>
      </div>
      <p className="text-[11px] mt-2" style={{ color: theme.t2 }}>
        Excel, CSV or PDF, up to 25 MB. Attach the whole workbook, or one file per
        statement. Every attachment is recorded in the audit log.
      </p>

      {err && (
        <div className="rounded-lg px-3 py-2 text-sm flex items-start gap-2 mt-3"
             style={{ background: theme.erB, color: theme.er, border: `1px solid ${theme.er}40` }}>
          <AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5" /> <div>{err}</div>
        </div>
      )}

      {docs.length > 0 && (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-sm" style={{ color: theme.text }}>
            <thead>
              <tr style={{ color: theme.t2 }}>
                <th className="text-left font-medium py-2 pr-3">File</th>
                <th className="text-left font-medium py-2 pr-3">Statement</th>
                <th className="text-right font-medium py-2 pr-3">Size</th>
                <th className="text-left font-medium py-2 pr-3">Attached by</th>
                <th className="text-left font-medium py-2 pr-3">When</th>
                <th className="py-2" />
              </tr>
            </thead>
            <tbody>
              {docs.map(d => (
                <tr key={d.id} style={{ borderTop: `1px solid ${theme.cardBdr}` }}>
                  <td className="py-2 pr-3">
                    <button type="button" onClick={() => download(d)}
                            disabled={busy !== null}
                            className="underline disabled:opacity-50 text-left"
                            style={{ color: theme.orange }}>
                      {busy === `dl-${d.id}` ? 'Downloading…' : d.original_name}
                    </button>
                    {d.notes && (
                      <div className="text-[11px]" style={{ color: theme.t2 }}>{d.notes}</div>
                    )}
                  </td>
                  <td className="py-2 pr-3">{d.statement || 'Whole workbook'}</td>
                  <td className="py-2 pr-3 text-right font-mono">{fmtSize(d.size_bytes)}</td>
                  <td className="py-2 pr-3">{d.uploaded_by || '—'}</td>
                  <td className="py-2 pr-3">{d.uploaded_at ? d.uploaded_at.slice(0, 16).replace('T', ' ') : '—'}</td>
                  <td className="py-2 text-right whitespace-nowrap">
                    <button type="button" onClick={() => compare(d)}
                            disabled={busy !== null}
                            className="text-xs underline disabled:opacity-50 mr-3"
                            style={{ color: theme.orange }}>
                      {busy === `cmp-${d.id}` ? 'Reading…'
                        : cmpDoc === d.id ? 'Hide comparison' : 'Compare A.1'}
                    </button>
                    <button type="button" onClick={() => remove(d.id, d.original_name)}
                            disabled={busy !== null}
                            className="text-xs underline disabled:opacity-50"
                            style={{ color: theme.er }}>
                      {busy === d.id ? 'Removing…' : 'Remove'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {cmp && (
        <div className="mt-5 pt-4" style={{ borderTop: `1px solid ${theme.cardBdr}` }}>
          <h4 className="text-sm font-semibold" style={{ color: theme.text }}>
            Statement A.1 — filed return vs Omni
          </h4>

          {!cmp.ok ? (
            <div className="rounded-lg px-3 py-2 text-sm flex items-start gap-2 mt-2"
                 style={{ background: theme.erB, color: theme.er, border: `1px solid ${theme.er}40` }}>
              <AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5" />
              <div>{cmp.error}</div>
            </div>
          ) : (
            <>
              {/* Stated before any figure: a variance below is not evidence
                  that Omni misread the ledger — the two are not using the
                  same formula. */}
              {cmp.method_warning && (
                <div className="rounded-lg px-3 py-2 text-[11px] flex items-start gap-2 mt-2"
                     style={{ background: theme.g100, color: theme.text,
                              border: `1px solid ${theme.orange}` }}>
                  <AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5"
                               style={{ color: theme.orange }} />
                  <div>{cmp.method_warning}</div>
                </div>
              )}

              <p className="text-[11px] mt-2" style={{ color: theme.t2 }}>
                {cmp.as_at ? `${cmp.as_at} · ` : ''}figures in {cmp.units || "P'000"}
                {cmp.note ? ` · ${cmp.note}` : ''}
                {cmp.ratios && Object.keys(cmp.ratios).length > 0 && (
                  <> · filed g-factors: {Object.entries(cmp.ratios)
                    .map(([k, v]) => `${k === 'G_INSURANCE' ? 'insurance' : 'market'} ${v}`)
                    .join(', ')}</>
                )}
              </p>

              {cmp.summary?.filed_pct && (
                <div className="flex flex-wrap gap-4 mt-3 text-sm">
                  <div>
                    <div className="text-[11px]" style={{ color: theme.t2 }}>Prescribed Capital Target — filed</div>
                    <div className="font-mono font-semibold" style={{ color: theme.text }}>
                      {fmtNum(cmp.summary.filed_pct)}
                    </div>
                  </div>
                  <div>
                    <div className="text-[11px]" style={{ color: theme.t2 }}>— as Omni computes it</div>
                    <div className="font-mono font-semibold" style={{ color: theme.text }}>
                      {cmp.summary.omni_pct ? fmtNum(cmp.summary.omni_pct) : '—'}
                    </div>
                  </div>
                  <div>
                    <div className="text-[11px]" style={{ color: theme.t2 }}>Difference</div>
                    <div className="font-mono font-semibold" style={{ color: theme.er }}>
                      {cmp.summary.difference ? fmtNum(cmp.summary.difference) : '—'}
                    </div>
                  </div>
                </div>
              )}

              <div className="mt-3 overflow-x-auto">
                <table className="w-full text-sm" style={{ color: theme.text }}>
                  <thead>
                    <tr style={{ color: theme.t2 }}>
                      <th className="text-left font-medium py-2 pr-3">Line</th>
                      <th className="text-right font-medium py-2 pr-3">Filed return</th>
                      <th className="text-right font-medium py-2 pr-3">Omni</th>
                      <th className="text-right font-medium py-2 pr-3">Difference</th>
                    </tr>
                  </thead>
                  <tbody>
                    {cmp.rows.map(row => (
                      <tr key={row.line_code} style={{ borderTop: `1px solid ${theme.cardBdr}` }}>
                        <td className="py-2 pr-3">{row.label}</td>
                        <td className="py-2 pr-3 text-right font-mono">
                          {row.filed !== null ? fmtNum(row.filed)
                            : <span style={{ color: theme.t2 }}>not in file</span>}
                        </td>
                        <td className="py-2 pr-3 text-right font-mono">
                          {row.omni !== null ? fmtNum(row.omni)
                            : <span style={{ color: theme.t2 }}>Omni has no such line</span>}
                        </td>
                        <td className="py-2 pr-3 text-right font-mono"
                            style={{ color: row.difference === null ? theme.t2
                              : row.agrees ? theme.t2 : theme.er }}>
                          {row.difference !== null ? fmtNum(row.difference) : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------------
 * A1InputsPanel — the three A.1 figures the general ledger cannot supply.
 *
 * Omni computes Statement A.1 the way the filed NBFIRA workbook does, but the
 * workbook is driven by assumptions: premium expected per class over the NEXT
 * twelve months, the treaty event retention, and how assets are split. Nobody
 * can derive those from the ledger, so until they are entered the Prescribed
 * Capital Target correctly sits on the statutory minimum.
 *
 * "Load from the filed workbook" reads those figures off the return already
 * attached to this period, so nothing is retyped. It only FILLS the form —
 * saving is a person's act, under their name, with a stated source.
 * ---------------------------------------------------------------------- */
const A1_CLASSES: [string, string][] = [
  ['property', 'Property'], ['transportation', 'Transportation'],
  ['motor', 'Motor'], ['accident', 'Accident'], ['health', 'Health'],
  ['guarantee', 'Guarantee'], ['liability', 'Liability'],
  ['engineering', 'Engineering'], ['miscellaneous', 'Miscellaneous'],
]
const A1_BUCKETS: [string, string][] = [
  ['cash', 'Cash or near cash'],
  ['fixed_interest_1yr', 'Fixed interest — 1 year'],
  ['fixed_interest_2yr', 'Fixed interest — 2 years'],
  ['fixed_interest_5yr', 'Fixed interest — 5 years'],
  ['fixed_interest_7yr', 'Fixed interest — 7 years'],
  ['fixed_interest_10yr', 'Fixed interest — 10 years'],
  ['property', 'Property'], ['listed_equities', 'Listed equities'],
  ['other_assets', 'Other assets'], ['unlisted_equities', 'Unlisted equities'],
]

type NumMap = Record<string, string>

interface A1Saved {
  anwp: NumMap; mer_total: string; net_assets: NumMap; alloc_mrctr: NumMap
  source_note: string; effective_from: string | null
  entered_by: string; entered_at: string | null
}

export function A1InputsPanel({ ret, onMutate }: {
  ret: ReturnDetail
  onMutate: () => void
}) {
  const { theme } = useTheme()
  const [anwp, setAnwp] = useState<NumMap>({})
  const [assets, setAssets] = useState<NumMap>({})
  const [alloc, setAlloc] = useState<NumMap>({})
  const [mer, setMer] = useState('')
  const [note, setNote] = useState('')
  const [effective, setEffective] = useState('')
  const [saved, setSaved] = useState<A1Saved | null>(null)
  const [suggestFrom, setSuggestFrom] = useState<string | null>(null)
  const [suggestion, setSuggestion] = useState<A1Saved | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [ok, setOk] = useState<string | null>(null)

  const locked = ['locked', 'submitted'].includes(ret.status)

  useEffect(() => {
    let live = true
    ;(async () => {
      try {
        const r = await authedHrisFetch(`/api/v1/nbfira/returns/${ret.id}/a1-inputs/`)
        if (!r.ok) return
        const d = await r.json()
        if (!live) return
        if (d.saved) {
          setSaved(d.saved)
          setAnwp(d.saved.anwp || {}); setAssets(d.saved.net_assets || {})
          setAlloc(d.saved.alloc_mrctr || {}); setMer(d.saved.mer_total || '')
          setNote(d.saved.source_note || '')
          setEffective(d.saved.effective_from || '')
        }
        if (d.suggested) {
          setSuggestion(d.suggested); setSuggestFrom(d.suggested.from_document)
        }
      } catch { /* the panel is additive — a failed read must not break the page */ }
    })()
    return () => { live = false }
  }, [ret.id])

  const applySuggestion = () => {
    if (!suggestion) return
    setAnwp(suggestion.anwp || {}); setAssets(suggestion.net_assets || {})
    setAlloc(suggestion.alloc_mrctr || {}); setMer(suggestion.mer_total || '')
    if (!note.trim() && suggestFrom) setNote(`As per the filed workbook ${suggestFrom}.`)
    setOk('Filled from the filed workbook — check the figures, then save.')
    setErr(null)
  }

  const save = async () => {
    if (busy) return
    setBusy('save'); setErr(null); setOk(null)
    try {
      const r = await authedHrisFetch(`/api/v1/nbfira/returns/${ret.id}/a1-inputs/`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          anwp, net_assets: assets, alloc_mrctr: alloc,
          mer_total: mer || '0', source_note: note,
          effective_from: effective || null,
        }),
      })
      const d = await r.json()
      if (!r.ok) { setErr(d.detail || `Could not save (HTTP ${r.status})`); return }
      setSaved(d.saved)
      setOk(d.detail || 'Saved.')
      onMutate()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not save')
    } finally { setBusy(null) }
  }

  const cell = (map: NumMap, set: (m: NumMap) => void, key: string) => (
    <input
      value={map[key] ?? ''}
      onChange={e => set({ ...map, [key]: e.target.value })}
      disabled={locked}
      inputMode="decimal"
      placeholder="0.00"
      className="px-2 py-1 rounded text-sm text-right font-mono w-full outline-none disabled:opacity-50"
      style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }}
    />
  )

  return (
    <div className="rounded-xl p-5 mt-4"
         style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h3 className="text-sm font-semibold" style={{ color: theme.text }}>
            A.1 assumptions — the figures the ledger cannot give us
          </h3>
          <p className="text-[11px] mt-1 max-w-2xl" style={{ color: theme.t2 }}>
            The Prescribed Capital Target is built from premium you <strong>expect
            over the next twelve months</strong>, the treaty event retention, and how
            the assets are split. None of those can be read off the general ledger.
            Until they are entered the target stays at the statutory P5,000k minimum.
            All figures in P&apos;000.
          </p>
        </div>
        {suggestFrom && !locked && (
          <button type="button" onClick={applySuggestion}
                  className="px-3 py-2 rounded-lg text-xs font-semibold whitespace-nowrap"
                  style={{ background: theme.g100, color: theme.text,
                           border: `1px solid ${theme.orange}` }}>
            Load from the filed workbook
          </button>
        )}
      </div>

      {saved && (
        <p className="text-[11px] mt-2" style={{ color: theme.t2 }}>
          Last entered by <strong>{saved.entered_by || '—'}</strong>
          {saved.entered_at ? ` on ${saved.entered_at.slice(0, 16).replace('T', ' ')}` : ''}
          {saved.source_note ? ` · ${saved.source_note}` : ''}
        </p>
      )}

      <div className="grid gap-5 mt-4" style={{ gridTemplateColumns: 'repeat(auto-fit,minmax(300px,1fr))' }}>
        <div>
          <h4 className="text-xs font-semibold mb-2" style={{ color: theme.text }}>
            Assumed net written premium, next 12 months
          </h4>
          <table className="w-full text-sm" style={{ color: theme.text }}>
            <tbody>
              {A1_CLASSES.map(([key, label]) => (
                <tr key={key}>
                  <td className="py-1 pr-2">{label}</td>
                  <td className="py-1 w-[130px]">{cell(anwp, setAnwp, key)}</td>
                </tr>
              ))}
              <tr>
                <td className="py-1 pr-2 font-semibold">Maximum event retention</td>
                <td className="py-1">
                  <input value={mer} onChange={e => setMer(e.target.value)}
                         disabled={locked} inputMode="decimal" placeholder="0.00"
                         className="px-2 py-1 rounded text-sm text-right font-mono w-full outline-none disabled:opacity-50"
                         style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }} />
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div>
          <h4 className="text-xs font-semibold mb-2" style={{ color: theme.text }}>
            Assets by bucket
          </h4>
          <table className="w-full text-sm" style={{ color: theme.text }}>
            <thead>
              <tr style={{ color: theme.t2 }}>
                <th className="text-left font-medium py-1 pr-2 text-[11px]">Bucket</th>
                <th className="text-right font-medium py-1 pr-2 text-[11px]">Net total</th>
                <th className="text-right font-medium py-1 text-[11px]">To market risk</th>
              </tr>
            </thead>
            <tbody>
              {A1_BUCKETS.map(([key, label]) => (
                <tr key={key}>
                  <td className="py-1 pr-2">{label}</td>
                  <td className="py-1 pr-2 w-[110px]">{cell(assets, setAssets, key)}</td>
                  <td className="py-1 w-[110px]">{cell(alloc, setAlloc, key)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="flex flex-wrap items-end gap-3 mt-4">
        <div className="flex-1 min-w-[260px]">
          <label className="block text-xs font-medium mb-1" style={{ color: theme.t2 }}>
            Where do these come from? (required)
          </label>
          <input value={note} onChange={e => setNote(e.target.value)} disabled={locked}
                 placeholder="e.g. as per the filed Q4 June 2026 workbook"
                 className="px-3 py-2 rounded-lg text-sm outline-none w-full disabled:opacity-50"
                 style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }} />
        </div>
        <div>
          <label className="block text-xs font-medium mb-1" style={{ color: theme.t2 }}>
            Effective from
          </label>
          <input type="date" value={effective} onChange={e => setEffective(e.target.value)}
                 disabled={locked}
                 className="px-3 py-2 rounded-lg text-sm outline-none disabled:opacity-50"
                 style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }} />
        </div>
        <button type="button" onClick={save} disabled={locked || busy !== null}
                className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold disabled:opacity-50"
                style={{ background: theme.orange, color: '#fff' }}>
          {busy === 'save' ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
          {busy === 'save' ? 'Saving…' : 'Save and recompute A.1'}
        </button>
      </div>

      {locked && (
        <p className="text-[11px] mt-2" style={{ color: theme.t2 }}>
          This return is {ret.status}. Reopen it before changing the assumptions behind it.
        </p>
      )}
      {err && (
        <div className="rounded-lg px-3 py-2 text-sm flex items-start gap-2 mt-3"
             style={{ background: theme.erB, color: theme.er, border: `1px solid ${theme.er}40` }}>
          <AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5" /> <div>{err}</div>
        </div>
      )}
      {ok && (
        <div className="rounded-lg px-3 py-2 text-sm flex items-start gap-2 mt-3"
             style={{ background: theme.okB, color: theme.ok, border: `1px solid ${theme.ok}40` }}>
          <CheckCircle2 className="w-4 h-4 flex-shrink-0 mt-0.5" /> <div>{ok}</div>
        </div>
      )}
    </div>
  )
}
