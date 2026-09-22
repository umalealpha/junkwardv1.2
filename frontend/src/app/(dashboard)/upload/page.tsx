'use client'

/**
 * /upload — single-drop-zone "Upload All Documents" page.
 *
 * CFO directive 2026-05-19: stop forcing the finance team to navigate to a
 * separate page per section ("Trial Balance" page, "Vendors" page, etc.).
 * One page. Drop any number of files. System sniffs each file's headers
 * and proposes the matching section. User reviews + commits.
 *
 * Backend: POST /api/v1/smart-upload/autodetect/  → returns the section
 * with the best heuristic match for that file's headers, plus the
 * preview rows. Then POST /api/v1/smart-upload/commit/ — same payload
 * shape as the per-section widget.
 */

import { useEffect, useMemo, useState } from 'react'
import { apiFetch } from '@/lib/api'

type SectionKey =
  | 'coa' | 'tb' | 'gl' | 'vendors' | 'customers'
  | 'ppe' | 'bank_accounts' | 'employees' | 'payroll'

interface DetectResponse {
  section: SectionKey | null
  section_label: string
  company: string
  source_hint: string
  truncated: boolean
  headers: string[]
  mapping: Record<string, string>
  missing_required: string[]
  ai_used?: boolean
  preview_rows: Array<Record<string, string>>
  rows: Array<Record<string, string>>
  row_count: number
  autodetect_score: number
  detail?: string
}

interface CommitReport {
  created: number
  updated: number
  skipped: number
  errors: string[]
  extra?: Record<string, unknown>
  section?: string
  company?: string
  detail?: string
  ai_explanation?: {
    explanation: string
    suggested_fix: string
    source: 'deepseek' | 'fallback'
  }
}

interface CompanyRow { id: string; code: string; name: string }

interface FileEntry {
  id: number
  file: File
  status: 'pending' | 'detecting' | 'ready' | 'committing' | 'done' | 'error'
  detect?: DetectResponse
  report?: CommitReport
  error?: string
}

let _id = 1

const SECTION_LABELS: Record<SectionKey, string> = {
  coa: 'Chart of Accounts',
  tb: 'Trial Balance',
  gl: 'General Ledger',
  vendors: 'Vendors / Suppliers',
  customers: 'Customers',
  ppe: 'Fixed Assets (PP&E)',
  bank_accounts: 'Bank Accounts',
  employees: 'Employees',
  payroll: 'Payroll',
}

export default function UploadAllPage() {
  const [items, setItems] = useState<FileEntry[]>([])
  const [companies, setCompanies] = useState<CompanyRow[]>([])
  const [company, setCompany] = useState<string>('')
  const [busy, setBusy] = useState(false)
  // TB-clear date range (CFO directive 2026-05-27 — no more clear-all).
  const [tbFrom, setTbFrom] = useState<string>('')
  const [tbTo, setTbTo] = useState<string>('')

  // ── Resolve default company from topbar ───────────────────────────────
  useEffect(() => {
    if (typeof window !== 'undefined') {
      const id = localStorage.getItem('alpha_company_id') || ''
      if (id) setCompany(id)
    }
    apiFetch<CompanyRow[]>('/companies/?page_size=100')
      .then((rows) => {
        const list: CompanyRow[] = Array.isArray(rows)
          ? rows
          : (rows as unknown as { results?: CompanyRow[] }).results || []
        setCompanies(list)
      })
      .catch(() => {/* silent */})
  }, [])

  const selectedCompanyCode = useMemo(() => {
    if (!company) return ''
    const row = companies.find((c) => c.id === company || c.code === company)
    return row?.code || company.toUpperCase()
  }, [companies, company])

  // ── Add files ─────────────────────────────────────────────────────────
  function addFiles(files: FileList | null | undefined) {
    if (!files) return
    const entries: FileEntry[] = []
    for (const f of Array.from(files)) {
      entries.push({ id: _id++, file: f, status: 'pending' })
    }
    setItems((prev) => [...prev, ...entries])
    // Auto-detect each on add
    entries.forEach((e) => void detectOne(e.id, e.file))
  }

  async function detectOne(id: number, file: File) {
    setItems((prev) => prev.map((x) => x.id === id ? { ...x, status: 'detecting' } : x))
    try {
      const fd = new FormData()
      fd.append('file', file)
      if (selectedCompanyCode) fd.append('company', selectedCompanyCode)
      const resp = await apiFetch<DetectResponse>(
        '/smart-upload/autodetect/',
        { method: 'POST', body: fd },
      )
      setItems((prev) => prev.map((x) =>
        x.id === id ? { ...x, status: 'ready', detect: resp } : x
      ))
    } catch (e: unknown) {
      setItems((prev) => prev.map((x) =>
        x.id === id
          ? { ...x, status: 'error', error: toMessage(e) || 'Detect failed.' }
          : x
      ))
    }
  }

  async function commitOne(id: number, opts?: { mode?: 'create' | 'replace' }) {
    const item = items.find((x) => x.id === id)
    if (!item || !item.detect || !item.detect.section) return
    setItems((prev) => prev.map((x) => x.id === id ? { ...x, status: 'committing' } : x))
    // External-audit follow-up 2026-05-19: client-generated idempotency
    // key — protects against double-click / browser retry.
    const idempotencyKey =
      (typeof crypto !== 'undefined' && 'randomUUID' in crypto)
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(36).slice(2)}`
    try {
      const resp = await apiFetch<CommitReport>(
        '/smart-upload/commit/',
        {
          method: 'POST',
          headers: { 'Idempotency-Key': idempotencyKey },
          body: JSON.stringify({
            section: item.detect.section,
            company: selectedCompanyCode,
            rows:    item.detect.rows,
            mode:    opts?.mode || 'create',
          }),
        },
      )
      setItems((prev) => prev.map((x) =>
        x.id === id ? { ...x, status: 'done', report: resp } : x
      ))
    } catch (e: unknown) {
      const errStatus = (e as { status?: number }).status
      const errBody = (e as { body?: Record<string, unknown> }).body

      // CFO directive 2026-05-20 (post-3-times-bug): server returns 409
      // when a smart_upload_tb JE already exists for (company, entry_date).
      // Ask the user whether to overwrite via mode=replace.
      if (
        errStatus === 409
        && errBody
        && (errBody as { duplicate_exists?: boolean }).duplicate_exists
      ) {
        const existing = (errBody as { existing?: { entry_number: string; lines: number; created_at: string; created_by: string | null } }).existing
        const period = (errBody as { period_end?: string }).period_end || ''
        const detail = `A trial balance already exists for ${selectedCompanyCode} ${period} (JE ${existing?.entry_number}, ${existing?.lines} lines, posted ${existing?.created_at?.slice(0,16) || '?'} by ${existing?.created_by || '?'}).\n\nReplace it with the new upload?`
        if (typeof window !== 'undefined' && window.confirm(detail)) {
          // Re-POST with mode=replace, fresh idempotency key.
          await commitOne(id, { mode: 'replace' })
          return
        }
        // User cancelled — surface as an error row with the server detail
        setItems((prev) => prev.map((x) =>
          x.id === id
            ? { ...x, status: 'error',
                error: (errBody as { detail?: string }).detail || 'TB already exists; upload cancelled.' }
            : x
        ))
        return
      }

      // CFO directive 2026-05-20: apiFetch now attaches the parsed
      // response body to the thrown error (.body). For smart-upload's
      // 400 path that includes `ai_explanation` from Aria — surface
      // it so the operator sees what to fix, not a one-liner.
      setItems((prev) => prev.map((x) =>
        x.id === id
          ? errBody && (Array.isArray((errBody as { errors?: unknown }).errors) || (errBody as { ai_explanation?: unknown }).ai_explanation)
            ? { ...x, status: 'done', report: errBody as unknown as CommitReport }
            : { ...x, status: 'error', error: toMessage(e) || 'Commit failed.' }
          : x
      ))
    }
  }

  async function clearTBsForCompany() {
    if (!selectedCompanyCode) return
    // Date range is mandatory — clearing all TB uploads at once is disabled.
    if (!tbFrom && !tbTo) {
      window.alert('Pick a date range (From and/or To) before clearing. Clearing all trial balances at once is disabled.')
      return
    }
    if (tbFrom && tbTo && tbFrom > tbTo) {
      window.alert('“From” date is after “To” date.')
      return
    }
    const scope =
      tbFrom && tbTo ? `between ${tbFrom} and ${tbTo}`
      : tbFrom ? `on/after ${tbFrom}`
      : `on/before ${tbTo}`
    const yes = typeof window !== 'undefined' && window.confirm(
      `Delete smart-upload trial balances for ${selectedCompanyCode} ${scope}?\n\n` +
      `This wipes ONLY smart-upload TB JEs whose entry date falls in that range. ` +
      `GL postings, Odoo migration data, and other source types are untouched. ` +
      `Cannot be undone.`
    )
    if (!yes) return
    try {
      setBusy(true)
      const resp = await apiFetch<{
        deleted_count: number
        deleted_lines: number
        entry_numbers: string[]
        company: string
        date_from: string | null
        date_to: string | null
      }>('/smart-upload/tb/clear/', {
        method: 'POST',
        body: JSON.stringify({
          company: selectedCompanyCode,
          date_from: tbFrom || undefined,
          date_to: tbTo || undefined,
        }),
      })
      window.alert(
        `Cleared ${resp.deleted_count} TB JE${resp.deleted_count === 1 ? '' : 's'} ` +
        `(${resp.deleted_lines} line${resp.deleted_lines === 1 ? '' : 's'}) for ${resp.company} ${scope}.`
      )
    } catch (e: unknown) {
      window.alert(`Clear failed: ${toMessage(e)}`)
    } finally {
      setBusy(false)
    }
  }

  async function commitAll() {
    if (!selectedCompanyCode) {
      alert('Pick a company first.')
      return
    }
    setBusy(true)
    for (const item of items) {
      if (item.status === 'ready' && item.detect?.section) {
        await commitOne(item.id)
      }
    }
    setBusy(false)
  }

  function removeOne(id: number) {
    setItems((prev) => prev.filter((x) => x.id !== id))
  }

  function changeSection(id: number, newSection: SectionKey) {
    setItems((prev) => prev.map((x) =>
      x.id === id && x.detect
        ? { ...x, detect: { ...x.detect, section: newSection,
            section_label: SECTION_LABELS[newSection] } }
        : x
    ))
  }

  const ready = items.filter((x) => x.status === 'ready').length
  const done  = items.filter((x) => x.status === 'done').length

  return (
    <div className="upl-page">
      <div className="upl-card">
        <header>
          <span className="upl-pill">Upload</span>
          <h1>Upload all documents</h1>
          <p>
            Drag in any combination of xlsx / csv / pdf / docx files. The
            system reads each file's headers and proposes the right section
            automatically. Review, then commit one or all.
          </p>
        </header>

        <div className="upl-controls">
          <label>
            <span>Company</span>
            <select
              value={company}
              onChange={(e) => setCompany(e.target.value)}
              disabled={busy}
            >
              <option value="">— pick entity —</option>
              {companies.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.code} — {c.name}
                </option>
              ))}
            </select>
          </label>

          <button
            type="button"
            className="upl-btn upl-btn-primary"
            onClick={commitAll}
            disabled={busy || ready === 0 || !selectedCompanyCode}
            title={!selectedCompanyCode ? 'Pick a company first' : ''}
          >
            {busy ? 'Committing…' : `Commit ${ready} ready file${ready === 1 ? '' : 's'}`}
          </button>

          <label>
            <span>Clear TB from</span>
            <input
              type="date"
              value={tbFrom}
              onChange={(e) => setTbFrom(e.target.value)}
              disabled={busy}
            />
          </label>
          <label>
            <span>to</span>
            <input
              type="date"
              value={tbTo}
              onChange={(e) => setTbTo(e.target.value)}
              disabled={busy}
            />
          </label>

          <button
            type="button"
            className="upl-btn upl-btn-danger"
            onClick={clearTBsForCompany}
            disabled={busy || !selectedCompanyCode || (!tbFrom && !tbTo)}
            title={
              !selectedCompanyCode
                ? 'Pick a company first'
                : (!tbFrom && !tbTo)
                  ? 'Pick a date range — clearing all at once is disabled'
                  : `Delete smart-upload TB for ${selectedCompanyCode} in the chosen date range. Other GL data is untouched.`
            }
          >
            Clear TB uploads (date range)
          </button>
        </div>

        <div
          className="upl-drop"
          onDragOver={(e) => { e.preventDefault(); e.stopPropagation() }}
          onDrop={(e) => {
            e.preventDefault(); e.stopPropagation()
            addFiles(e.dataTransfer.files)
          }}
        >
          <input
            type="file"
            multiple
            accept=".xlsx,.xlsm,.xls,.csv,.tsv,.txt,.pdf,.docx"
            onChange={(e) => addFiles(e.target.files)}
            id="upl-input"
            style={{ display: 'none' }}
          />
          <label htmlFor="upl-input" className="upl-drop-label">
            <strong>Drag files here</strong>
            <span>or click to browse</span>
            <small>Multiple files OK. We auto-detect what each one is.</small>
          </label>
        </div>

        {items.length > 0 && (
          <table className="upl-list">
            <thead>
              <tr>
                <th>File</th>
                <th>Detected section</th>
                <th>Rows</th>
                <th>Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {items.map((x) => (
                <tr key={x.id}>
                  <td><code>{x.file.name}</code><div className="upl-size">{(x.file.size / 1024).toFixed(1)} KB</div></td>
                  <td>
                    {x.detect?.section ? (
                      <select
                        value={x.detect.section}
                        onChange={(e) => changeSection(x.id, e.target.value as SectionKey)}
                        disabled={x.status === 'committing' || x.status === 'done'}
                      >
                        {(Object.keys(SECTION_LABELS) as SectionKey[]).map((k) => (
                          <option key={k} value={k}>{SECTION_LABELS[k]}</option>
                        ))}
                      </select>
                    ) : x.status === 'detecting' ? (
                      <span className="upl-muted">detecting…</span>
                    ) : (
                      <span className="upl-muted">—</span>
                    )}
                  </td>
                  <td>{x.detect?.row_count ?? '—'}</td>
                  <td>
                    {x.status === 'error' && (
                      <span className="upl-error">{x.error}</span>
                    )}
                    {x.status === 'done' && x.report && (
                      (() => {
                        const errs = x.report.errors || []
                        const written = (x.report.created || 0) + (x.report.updated || 0)
                        if (written === 0 && errs.length > 0) {
                          const ai = x.report.ai_explanation
                          return (
                            <div className="upl-error-block">
                              <span className="upl-error">✗ Upload failed — 0 rows written.</span>
                              {ai && (
                                <div className="upl-ai-card">
                                  <div className="upl-ai-tag">
                                    {ai.source === 'deepseek' ? '🤖 Aria' : '💡 Hint'}
                                  </div>
                                  <p className="upl-ai-text">{ai.explanation}</p>
                                  {ai.suggested_fix && (
                                    <p className="upl-ai-fix"><strong>Fix:</strong> {ai.suggested_fix}</p>
                                  )}
                                </div>
                              )}
                              <details className="upl-raw-details">
                                <summary>Raw error{errs.length === 1 ? '' : 's'} ({errs.length})</summary>
                                <ul className="upl-error-list">
                                  {errs.slice(0, 10).map((m, i) => <li key={i}>{m}</li>)}
                                  {errs.length > 10 && <li>… and {errs.length - 10} more</li>}
                                </ul>
                              </details>
                            </div>
                          )
                        }
                        if (errs.length > 0) {
                          return (
                            <div className="upl-warn-block">
                              <span className="upl-warn">
                                ⚠ {x.report.created} created, {x.report.updated} updated, {errs.length} row(s) skipped
                              </span>
                              <ul className="upl-error-list">
                                {errs.slice(0, 5).map((m, i) => <li key={i}>{m}</li>)}
                                {errs.length > 5 && <li>… and {errs.length - 5} more</li>}
                              </ul>
                            </div>
                          )
                        }
                        return (
                          <span className="upl-ok">
                            ✓ {x.report.created} created, {x.report.updated} updated
                          </span>
                        )
                      })()
                    )}
                    {x.status === 'ready' && (
                      <span className="upl-ready">ready — {x.detect?.missing_required?.length ? 'missing: ' + x.detect.missing_required.join(', ') : 'mapped ok'}</span>
                    )}
                    {x.status === 'committing' && (
                      <span className="upl-muted">posting…</span>
                    )}
                    {x.status === 'pending' && (
                      <span className="upl-muted">queued</span>
                    )}
                  </td>
                  <td>
                    {x.status === 'ready' && (
                      <button
                        type="button"
                        className="upl-btn upl-btn-small"
                        onClick={() => commitOne(x.id)}
                        disabled={!selectedCompanyCode}
                        title={
                          !selectedCompanyCode
                            ? 'Pick a company in the top-bar before committing.'
                            : (x.detect?.missing_required?.length ?? 0) > 0
                              ? `Missing column(s): ${x.detect?.missing_required?.join(', ')}. Click to try — backend will validate and explain.`
                              : 'Commit this upload to the ledger.'
                        }
                      >Commit</button>
                    )}
                    <button
                      type="button"
                      className="upl-btn upl-btn-small upl-btn-ghost"
                      onClick={() => removeOne(x.id)}
                    >Remove</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {items.length > 0 && (
          <p className="upl-summary">
            {items.length} file{items.length === 1 ? '' : 's'} · {ready} ready · {done} done
          </p>
        )}
      </div>

      <style jsx>{`
        .upl-page { padding: 24px; max-width: 1100px; margin: 0 auto; }
        .upl-card {
          background: #fff; border: 1px solid #E1E5EB; border-radius: 14px;
          padding: 24px 28px; box-shadow: 0 1px 3px rgba(13,27,42,0.04);
        }
        header { margin-bottom: 18px; }
        .upl-pill {
          display: inline-block; background: #0D1B2A; color: #F4A623;
          padding: 3px 12px; border-radius: 999px; font-size: 11px;
          letter-spacing: 0.05em; text-transform: uppercase; margin-bottom: 6px;
        }
        header h1 { margin: 6px 0 4px; font-size: 22px; color: #0D1B2A; }
        header p { color: #6B7480; font-size: 14px; margin: 0; max-width: 720px; }

        .upl-controls {
          display: flex; gap: 14px; align-items: end; margin-bottom: 14px;
          flex-wrap: wrap;
        }
        .upl-controls label { display: flex; flex-direction: column; gap: 4px; }
        .upl-controls label span {
          font-size: 11px; color: #6B7480; text-transform: uppercase;
          letter-spacing: 0.06em;
        }
        .upl-controls select {
          padding: 9px 12px; border: 1px solid #D6DAE2; border-radius: 8px;
          font-size: 14px; min-width: 240px;
        }

        .upl-btn {
          padding: 10px 18px; border-radius: 8px; border: 1px solid #D6DAE2;
          background: #fff; font-size: 14px; cursor: pointer;
        }
        .upl-btn[disabled] { opacity: 0.5; cursor: not-allowed; }
        .upl-btn-primary {
          background: #F4A623; border-color: #F4A623; color: #0D1B2A;
          font-weight: 600;
        }
        .upl-btn-primary:hover:not([disabled]) { background: #E89B16; }
        .upl-btn-danger {
          background: #fff; border-color: #DC2626; color: #B91C1C;
          font-weight: 600;
        }
        .upl-btn-danger:hover:not([disabled]) {
          background: #FEF2F2; border-color: #B91C1C; color: #991B1B;
        }
        .upl-btn-small { padding: 6px 10px; font-size: 13px; margin-right: 6px; }
        .upl-btn-ghost { background: transparent; }

        .upl-drop {
          border: 2px dashed #D6DAE2; border-radius: 12px;
          padding: 32px 20px; text-align: center; cursor: pointer;
          transition: border-color 0.12s ease;
          background: #FAFBFC;
        }
        .upl-drop:hover { border-color: #F4A623; }
        .upl-drop-label {
          display: flex; flex-direction: column; gap: 4px; cursor: pointer;
        }
        .upl-drop-label strong { font-size: 16px; color: #0D1B2A; }
        .upl-drop-label span { color: #6B7480; font-size: 13px; }
        .upl-drop-label small { color: #9CA3AF; font-size: 12px; margin-top: 6px; }

        .upl-list {
          width: 100%; border-collapse: collapse; margin-top: 18px; font-size: 13px;
        }
        .upl-list th {
          text-align: left; background: #F5F7FB; padding: 8px 10px;
          color: #0D1B2A; font-weight: 600; font-size: 12px;
          text-transform: uppercase; letter-spacing: 0.04em;
        }
        .upl-list td { padding: 8px 10px; border-top: 1px solid #ECEEF2; vertical-align: top; }
        .upl-list code { font-family: ui-monospace, SFMono-Regular, monospace; font-size: 13px; }
        .upl-size { color: #9CA3AF; font-size: 11px; margin-top: 2px; }
        .upl-muted { color: #9CA3AF; }
        .upl-ready { color: #1F5132; font-weight: 500; }
        .upl-ok    { color: #1F5132; }
        .upl-error { color: #8E1F12; font-weight: 600; }
        .upl-warn  { color: #8A5A00; font-weight: 600; }
        .upl-error-block, .upl-warn-block {
          display: block; padding: 8px 10px; border-radius: 6px;
          font-size: 13px; line-height: 1.4;
        }
        .upl-error-block { background: #FDECEA; border: 1px solid #F5C2C0; }
        .upl-warn-block  { background: #FFF7DC; border: 1px solid #F0DCA0; }
        .upl-error-list  {
          margin: 6px 0 0; padding-left: 18px; color: #6B7480; font-size: 12px;
        }
        .upl-error-list li { margin: 2px 0; word-break: break-word; }
        .upl-ai-card {
          background: #fff; border: 1px solid #E1E5EB; border-radius: 8px;
          padding: 10px 12px; margin: 8px 0 4px;
        }
        .upl-ai-tag {
          display: inline-block; font-size: 10.5px; letter-spacing: 0.04em;
          color: #0D1B2A; background: #F4A623; padding: 1px 8px;
          border-radius: 999px; margin-bottom: 4px; font-weight: 600;
          text-transform: uppercase;
        }
        .upl-ai-text { color: #0D1B2A; margin: 4px 0; font-size: 13px; }
        .upl-ai-fix  { color: #1F5132; margin: 4px 0 0; font-size: 13px; }
        .upl-raw-details {
          margin-top: 6px; color: #6B7480; font-size: 12px;
        }
        .upl-raw-details summary {
          cursor: pointer; user-select: none; padding: 2px 0;
        }
        .upl-summary { margin-top: 12px; color: #6B7480; font-size: 13px; }
      `}</style>
    </div>
  )
}

function toMessage(e: unknown): string {
  if (e instanceof Error) return e.message
  if (typeof e === 'string') return e
  try { return JSON.stringify(e) } catch { return '' }
}
