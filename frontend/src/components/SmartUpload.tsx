'use client'

/**
 * SmartUpload — "drop any file, get clean rows" widget.
 *
 * CFO directive 2026-05-18: every data-import page on omni ships this
 * widget. Drop xlsx / csv / pdf / docx, Aria figures out the column
 * mapping, omni previews the canonical rows, CFO commits.
 *
 * Backend: core/smart_upload/ (POST /api/v1/smart-upload/preview/ + /commit/).
 *
 * Usage:
 *   <SmartUpload section="vendors" />
 *   <SmartUpload section="coa" hideCompanyPicker />
 *
 * Picks up the topbar company from localStorage('alpha_company_id') unless
 * the caller overrides via the company prop. Calls onCommitted with the
 * server's CommitReport so the parent can refresh its list.
 */

import { useEffect, useMemo, useState } from 'react'
import { apiFetch } from '@/lib/api'

type SectionKey =
  | 'coa' | 'tb' | 'gl' | 'vendors' | 'customers'
  | 'ppe' | 'bank_accounts' | 'employees' | 'payroll'

interface Props {
  section: SectionKey
  /** Override the topbar selection. Defaults to localStorage. */
  company?: string
  /** Hide the company picker (useful when the page itself locks the entity). */
  hideCompanyPicker?: boolean
  /** Callback fired after a successful commit. */
  onCommitted?: (report: CommitReport) => void
  /** Optional CSS class for the outer card. */
  className?: string
}

interface PreviewResponse {
  section: string
  section_label: string
  company: string
  source_hint: string
  truncated: boolean
  headers: string[]
  mapping: Record<string, string>
  fields?: Array<{ name: string; label: string; required: boolean; kind: string }>
  missing_required: string[]
  ai_used?: boolean
  preview_rows: Array<Record<string, string>>
  rows: Array<Record<string, string>>
  row_count: number
}

interface CommitReport {
  created: number
  updated: number
  skipped: number
  errors: string[]
  extra?: {
    journal_entry?: string
    lines?: number
    period_locked?: boolean
    locked_periods?: string[]
    period_overrides?: string[]
    period_auto_locked?: string
    [k: string]: unknown
  }
  section?: string
  company?: string
}

interface CompanyRow {
  id: string
  code: string
  name: string
}

const LABELS: Record<SectionKey, string> = {
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

export default function SmartUpload({
  section,
  company: companyProp,
  hideCompanyPicker,
  onCommitted,
  className = '',
}: Props) {
  const [file, setFile] = useState<File | null>(null)
  const [companies, setCompanies] = useState<CompanyRow[]>([])
  const [company, setCompany] = useState<string>('')
  const [busy, setBusy] = useState(false)
  const [preview, setPreview] = useState<PreviewResponse | null>(null)
  const [report, setReport] = useState<CommitReport | null>(null)
  const [error, setError] = useState<string>('')
  // CFO directive 2026-06-03 — Aria-translated rejection popup. When
  // the backend returns friendly_message, render a modal instead of a
  // toast so the missing-accounts list + suggested fix are unmissable.
  const [friendlyError, setFriendlyError] = useState<null | {
    title: string
    message: string
    fix: string
    missing_accounts: Array<{ code: string; name?: string; debit?: string; credit?: string }>
    missing_dr?: string
    missing_cr?: string
    raw_detail: string
    raw_errors: string[]
    source: string
  }>(null)
  // CFO directive 2026-05-20: period-bound sections (TB / GL / Payroll)
  // need explicit period_start + period_end on the upload form so users
  // don't rely on the file row values. Defaults below = current FY
  // (Alpha Direct FY = 1 Jul → 30 Jun).
  const isPeriodSection = section === 'tb' || section === 'gl' || section === 'payroll'
  // Payroll runs MONTHLY, so it must not inherit the fiscal-year default
  // (bug report Pako Kago 2026-07-29): a whole-FY range labelled "FY27" put
  // all twelve months of the year into ONE payroll period, and every month
  // after the first then looked like a duplicate payslip. Payroll defaults to
  // the current month, labelled YYYY-MM; TB and GL keep the FY default.
  const _defaultPeriod = (() => {
    const now = new Date()
    const y = now.getFullYear()
    const m = now.getMonth()          // 0-based
    if (section === 'payroll') {
      const two = (n: number) => String(n).padStart(2, '0')
      const lastDay = new Date(y, m + 1, 0).getDate()
      return {
        start: `${y}-${two(m + 1)}-01`,
        end:   `${y}-${two(m + 1)}-${two(lastDay)}`,
        label: `${y}-${two(m + 1)}`,
      }
    }
    const fyStartYear = m + 1 >= 7 ? y : y - 1
    return {
      start: `${fyStartYear}-07-01`,
      end:   `${fyStartYear + 1}-06-30`,
      label: `FY${(fyStartYear + 1).toString().slice(-2)}`,
    }
  })()
  const [periodStart, setPeriodStart] = useState<string>(_defaultPeriod.start)
  const [periodEnd,   setPeriodEnd]   = useState<string>(_defaultPeriod.end)
  const [periodLabel, setPeriodLabel] = useState<string>(_defaultPeriod.label)
  // Manual column-mapping overrides — {raw_header: canonical_field}. '' means
  // "ignore this column". Sent to /preview/ so the server re-slices the rows.
  const [overrides, setOverrides] = useState<Record<string, string>>({})

  // Quick period presets. Payroll gets MONTHS, not fiscal years — an FY preset
  // on a payroll upload re-creates the very bug this screen was fixed for
  // (one FY-wide period swallowing all twelve months). TB and GL keep FY.
  const PERIOD_PRESETS = (() => {
    if (section !== 'payroll') {
      return [
        { label: 'FY25',    start: '2024-07-01', end: '2025-06-30', periodLabel: 'FY25' },
        { label: 'FY26 9M', start: '2025-07-01', end: '2026-03-31', periodLabel: 'FY26 9M' },
        { label: 'FY26',    start: '2025-07-01', end: '2026-06-30', periodLabel: 'FY26' },
      ]
    }
    const two = (n: number) => String(n).padStart(2, '0')
    const now = new Date()
    // This month and the two before it — the realistic payroll upload window.
    return [0, 1, 2].map((back) => {
      const d = new Date(now.getFullYear(), now.getMonth() - back, 1)
      const y = d.getFullYear()
      const m = d.getMonth() + 1
      const lastDay = new Date(y, m, 0).getDate()
      return {
        label: d.toLocaleString('en-GB', { month: 'short', year: 'numeric' }),
        start: `${y}-${two(m)}-01`,
        end:   `${y}-${two(m)}-${two(lastDay)}`,
        periodLabel: `${y}-${two(m)}`,
      }
    })
  })()

  // ── Resolve default company from prop / topbar ─────────────────────────
  useEffect(() => {
    if (companyProp) {
      setCompany(companyProp)
      return
    }
    if (typeof window !== 'undefined') {
      const id = localStorage.getItem('alpha_company_id') || ''
      if (id) {
        setCompany(id)
      }
    }
  }, [companyProp])

  // ── Load company list for the picker ───────────────────────────────────
  useEffect(() => {
    if (hideCompanyPicker) return
    apiFetch<CompanyRow[]>('/companies/?page_size=100')
      .then((rows) => {
        // some envs return {results:[…]}, others a bare list
        const list: CompanyRow[] = Array.isArray(rows)
          ? rows
          : (rows as unknown as { results?: CompanyRow[] }).results || []
        setCompanies(list)
      })
      .catch(() => {/* widget still works without a picker */})
  }, [hideCompanyPicker])

  const selectedCompanyCode = useMemo(() => {
    // The dropdown stores either a UUID (from list) or a code.
    if (!company) return ''
    const row = companies.find((c) => c.id === company || c.code === company)
    return row?.code || company.toUpperCase()
  }, [companies, company])

  // ── Preview ────────────────────────────────────────────────────────────
  async function handlePreview(overrideMap: Record<string, string> | null = null) {
    setError('')
    setReport(null)
    if (!file) {
      setError('Pick a file first.')
      return
    }
    if (!hideCompanyPicker && !selectedCompanyCode) {
      setError('Pick a company first.')
      return
    }

    setBusy(true)
    try {
      const fd = new FormData()
      fd.append('file', file)
      fd.append('section', section)
      if (selectedCompanyCode) {
        fd.append('company', selectedCompanyCode)
      }
      // Period-bound sections: forward period range so backend can
      // override the file's row values uniformly.
      if (isPeriodSection) {
        fd.append('period_start', periodStart)
        fd.append('period_end',   periodEnd)
        fd.append('period_label', periodLabel)
      }
      // Manual column mapping the user set in the Column Mapping table.
      const effectiveOverrides = overrideMap ?? overrides
      if (Object.keys(effectiveOverrides).length) {
        fd.append('mapping_override', JSON.stringify(effectiveOverrides))
      }

      // We need a fetch that posts FormData; reuse apiFetch (it handles auth).
      const resp = await apiFetch<PreviewResponse>(
        '/smart-upload/preview/',
        { method: 'POST', body: fd },
      )
      setPreview(resp)
    } catch (e: unknown) {
      setError(toMessage(e) || 'Preview failed.')
    } finally {
      setBusy(false)
    }
  }

  // ── Manual column mapping ──────────────────────────────────────────────
  // The dropdown records an override and re-runs the preview, so the rows the
  // Commit button will post are always the rows shown in the table.
  const fieldOptions = preview?.fields ?? []
  const unmappedCount = preview
    ? preview.headers.filter((h) => !preview.mapping[h]).length
    : 0

  function setColumnMapping(header: string, fieldName: string) {
    const next = { ...overrides, [header]: fieldName }
    setOverrides(next)
    void handlePreview(next)
  }

  // ── Commit ─────────────────────────────────────────────────────────────
  async function handleCommit() {
    if (!preview) return
    if (preview.missing_required?.length) {
      setError(
        `Cannot commit — these required columns are not mapped: ` +
        `${preview.missing_required.join(', ')}. Set them in the Column ` +
        `mapping table, or rename them in your file and re-upload.`,
      )
      return
    }
    setError('')
    setBusy(true)
    // External-audit follow-up 2026-05-19: client-generated idempotency
    // key so a double-click / browser retry can't post twice. The
    // backend honours external_ref-based de-dup already; this is an
    // additional belt for the user.
    const idempotencyKey =
      (typeof crypto !== 'undefined' && 'randomUUID' in crypto)
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(36).slice(2)}`
    await postCommit({ idempotencyKey, force_unlock: false })
  }

  // CFO directive 2026-05-20: when amending a TB whose period is
  // already locked, surface the "Unlock + amend" confirm modal before
  // calling the backend with force_unlock=true.
  async function postCommit({ idempotencyKey, force_unlock }:
                            { idempotencyKey: string; force_unlock: boolean }) {
    try {
      const resp = await apiFetch<CommitReport>(
        '/smart-upload/commit/',
        {
          method: 'POST',
          headers: { 'Idempotency-Key': idempotencyKey },
          body: JSON.stringify({
            section,
            company: selectedCompanyCode,
            rows: preview!.rows,
            ...(isPeriodSection ? {
              period_start: periodStart,
              period_end:   periodEnd,
              period_label: periodLabel,
            } : {}),
            ...(force_unlock ? { force_unlock: true } : {}),
          }),
        },
      )
      setReport(resp)
      onCommitted?.(resp)
    } catch (e: unknown) {
      // Prefer the backend's Aria-translated friendly_message when
      // present (commit_tb populates this on rejection along with the
      // missing_accounts list). Falls back to the raw error toast for
      // anything else (network, 500, etc.).
      const body: any = (e as any)?.body
      if (body && (body.friendly_message || body.missing_accounts)) {
        setFriendlyError({
          title:            String(body.friendly_title || "I couldn't load this file"),
          message:          String(body.friendly_message || body.detail || ''),
          fix:              String(body.friendly_fix || ''),
          missing_accounts: Array.isArray(body.missing_accounts) ? body.missing_accounts : [],
          missing_dr:       body.missing_dr,
          missing_cr:       body.missing_cr,
          raw_detail:       String(body.detail || ''),
          raw_errors:       Array.isArray(body.errors) ? body.errors.map(String) : [],
          source:           String(body.friendly_source || 'fallback'),
        })
      } else {
        setError(toMessage(e) || 'Commit failed.')
      }
    } finally {
      setBusy(false)
    }
  }

  function reset() {
    setFile(null); setPreview(null); setReport(null); setError('')
    setFriendlyError(null); setOverrides({})
  }

  const label = LABELS[section]

  return (
    <div className={`smart-upload ${className}`}>
      {friendlyError && (
        <div className="su-modal-backdrop" onClick={() => setFriendlyError(null)}>
          <div className="su-modal" onClick={(ev) => ev.stopPropagation()} role="dialog" aria-modal="true">
            <div className="su-modal-head">
              <span className="su-modal-icon" aria-hidden>⚠️</span>
              <h3>{friendlyError.title}</h3>
              <button type="button" className="su-modal-close" onClick={() => setFriendlyError(null)} aria-label="Close">×</button>
            </div>
            <div className="su-modal-body">
              <p className="su-friendly-msg">{friendlyError.message}</p>
              {friendlyError.fix && (
                <div className="su-friendly-fix">
                  <strong>What to do next:</strong>
                  <p>{friendlyError.fix}</p>
                </div>
              )}
              {friendlyError.missing_accounts.length > 0 && (
                <div className="su-missing">
                  <strong>{friendlyError.missing_accounts.length} account code(s) not in the CoA</strong>
                  {(friendlyError.missing_dr || friendlyError.missing_cr) && (
                    <p className="su-missing-sub">
                      Unposted: Dr {friendlyError.missing_dr || '0'} / Cr {friendlyError.missing_cr || '0'}
                    </p>
                  )}
                  <ul>
                    {friendlyError.missing_accounts.slice(0, 20).map((m, i) => (
                      <li key={`${m.code}-${i}`}>
                        <code>{m.code}</code> {m.name ? ` — ${m.name}` : ''}{' '}
                        {(m.debit && m.debit !== '0') ? <span className="su-dr">Dr {m.debit}</span> : null}
                        {(m.credit && m.credit !== '0') ? <span className="su-cr">Cr {m.credit}</span> : null}
                      </li>
                    ))}
                    {friendlyError.missing_accounts.length > 20 && (
                      <li>... and {friendlyError.missing_accounts.length - 20} more</li>
                    )}
                  </ul>
                </div>
              )}
              <details className="su-raw-detail">
                <summary>Technical details</summary>
                <p><b>Detail:</b> {friendlyError.raw_detail || '—'}</p>
                {friendlyError.raw_errors.length > 0 && (
                  <ul>{friendlyError.raw_errors.map((e, i) => <li key={i}>{e}</li>)}</ul>
                )}
                <p className="su-source">Explanation source: {friendlyError.source}</p>
              </details>
            </div>
            <div className="su-modal-foot">
              <button type="button" className="su-btn" onClick={() => setFriendlyError(null)}>Close</button>
            </div>
          </div>
        </div>
      )}
      <div className="su-card">
        <div className="su-header">
          <div className="su-title">
            <span className="su-pill">Smart Upload</span>
            {/* h2 — first heading after the page's sr-only h1 (axe heading-order) */}
            <h2>Upload {label}</h2>
            <a
              className="su-template"
              href={`/api/v1/smart-upload/template/?section=${section}`}
              target="_blank"
              rel="noopener noreferrer"
              title="Download a blank CSV template with the expected columns"
            >
              Download template
            </a>
          </div>
          <p className="su-sub">
            Drop an xlsx, csv, pdf or docx. Aria maps the columns per
            company; you review the preview before anything hits the books.
          </p>
        </div>

        {isPeriodSection && (
          <div className="su-row" style={{ marginBottom: 8 }}>
            <div className="su-field">
              <span>{section === 'payroll' ? 'Quick month' : 'Quick FY preset'}</span>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {PERIOD_PRESETS.map((p) => (
                  <button
                    key={p.label}
                    type="button"
                    className="su-btn su-btn-small"
                    onClick={() => {
                      setPeriodStart(p.start)
                      setPeriodEnd(p.end)
                      setPeriodLabel(p.periodLabel)
                    }}
                    disabled={busy}
                  >{p.label}</button>
                ))}
              </div>
            </div>
          </div>
        )}

        <div className="su-row">
          {!hideCompanyPicker && (
            <label className="su-field">
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
          )}

          {isPeriodSection && (
            <>
              <label className="su-field">
                <span>Period start</span>
                <input
                  type="date"
                  value={periodStart}
                  onChange={(e) => setPeriodStart(e.target.value)}
                  disabled={busy}
                />
              </label>
              <label className="su-field">
                <span>Period end</span>
                <input
                  type="date"
                  value={periodEnd}
                  onChange={(e) => setPeriodEnd(e.target.value)}
                  disabled={busy}
                />
              </label>
              <label className="su-field">
                <span>Period label</span>
                <input
                  type="text"
                  value={periodLabel}
                  onChange={(e) => setPeriodLabel(e.target.value)}
                  placeholder="FY25"
                  disabled={busy}
                />
              </label>
            </>
          )}

          <label className="su-field grow">
            <span>File</span>
            <input
              type="file"
              accept=".xlsx,.xlsm,.xls,.csv,.tsv,.txt,.pdf,.docx"
              onChange={(e) => setFile(e.target.files?.[0] || null)}
              disabled={busy}
            />
          </label>

          <div className="su-actions">
            {!preview && (
              <button
                type="button"
                className="su-btn su-btn-primary"
                onClick={() => void handlePreview()}
                disabled={busy || !file}
              >
                {busy ? 'Analysing…' : 'Analyse with AI'}
              </button>
            )}
            {preview && (
              <>
                <button
                  type="button"
                  className="su-btn su-btn-primary"
                  onClick={handleCommit}
                  disabled={busy || preview.missing_required.length > 0}
                >
                  {busy ? 'Committing…' : `Commit ${preview.row_count} rows`}
                </button>
                <button
                  type="button"
                  className="su-btn"
                  onClick={reset}
                  disabled={busy}
                >
                  Start over
                </button>
              </>
            )}
          </div>
        </div>

        {error && <div className="su-error">{error}</div>}

        {preview && (
          <div className="su-preview">
            <div className="su-meta">
              <span><b>Source:</b> {preview.source_hint || 'n/a'}</span>
              <span><b>Rows:</b> {preview.row_count}{preview.truncated ? ' (truncated)' : ''}</span>
              <span><b>Mapped:</b> {Object.keys(preview.mapping).length} / {preview.headers.length} columns</span>
              {preview.ai_used && (
                <span className="su-ai-tag" title="Aria refined the heuristic mapping">
                  AI-assisted
                </span>
              )}
            </div>

            {preview.missing_required.length > 0 && (
              <div className="su-warn">
                <b>Commit is blocked.</b> These required columns did not map:{' '}
                <b>{preview.missing_required.join(', ')}</b>. Set them in the
                Column mapping table below, or rename them in your file and
                re-upload. Committing without them would post rows with no
                money on them.
              </div>
            )}

            <details open={unmappedCount > 0}>
              <summary>
                Column mapping{' '}
                {unmappedCount > 0
                  ? `— ${unmappedCount} column${unmappedCount === 1 ? '' : 's'} still unmapped`
                  : '— all columns mapped'}
              </summary>
              <p className="su-hint">
                Pick a field for any column the matcher got wrong or left
                blank. Choose <i>Ignore this column</i> to leave it out.
              </p>
              <table className="su-mapping">
                <thead>
                  <tr><th>Raw header</th><th>Canonical field</th></tr>
                </thead>
                <tbody>
                  {preview.headers.map((h) => {
                    const current = preview.mapping[h] || ''
                    return (
                      <tr key={h} className={current ? undefined : 'su-row-unmapped'}>
                        <td>{h}</td>
                        <td>
                          {fieldOptions.length > 0 ? (
                            <select
                              className="su-select"
                              aria-label={`Map column ${h}`}
                              value={current}
                              disabled={busy}
                              onChange={(e) => setColumnMapping(h, e.target.value)}
                            >
                              <option value="">— Ignore this column —</option>
                              {fieldOptions.map((f) => (
                                <option key={f.name} value={f.name}>
                                  {f.label} ({f.name}){f.required ? ' *' : ''}
                                </option>
                              ))}
                            </select>
                          ) : (
                            current || <i>— unmapped —</i>
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </details>

            <table className="su-table">
              <thead>
                <tr>
                  {Object.values(preview.mapping).map((f) => (
                    <th key={f}>{f}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {preview.preview_rows.map((r, i) => (
                  <tr key={i}>
                    {Object.values(preview.mapping).map((f) => (
                      <td key={f}>{String(r[f] ?? '')}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            {preview.row_count > preview.preview_rows.length && (
              <div className="su-foot">
                Showing first {preview.preview_rows.length} of {preview.row_count} rows.
              </div>
            )}
          </div>
        )}

        {report && (
          <div className="su-result">
            {report.extra?.period_locked ? (
              <div className="su-warn">
                <div>
                  <b>Period {report.extra.locked_periods?.join(', ') || 'is'} locked.</b>
                  &nbsp;TB amendments require an explicit unlock + confirmation.
                </div>
                <div style={{ marginTop: 10, display: 'flex', gap: 8 }}>
                  <button
                    type="button"
                    className="su-btn su-btn-primary"
                    onClick={() => {
                      const ok = window.confirm(
                        `Unlock ${report.extra?.locked_periods?.join(', ') || 'the period'} and post the TB amendment?\n\n` +
                        'This is recorded on the FiscalPeriod audit trail. ' +
                        'The period will be re-locked automatically after the post.'
                      )
                      if (!ok) return
                      const k =
                        (typeof crypto !== 'undefined' && 'randomUUID' in crypto)
                          ? crypto.randomUUID()
                          : `${Date.now()}-${Math.random().toString(36).slice(2)}`
                      setReport(null)
                      setBusy(true)
                      void postCommit({ idempotencyKey: k, force_unlock: true })
                    }}
                    disabled={busy}
                  >Unlock &amp; amend</button>
                  <button
                    type="button"
                    className="su-btn"
                    onClick={() => setReport(null)}
                    disabled={busy}
                  >Cancel</button>
                </div>
              </div>
            ) : (
              <>
                <div className="su-result-line">
                  <b>Done.</b> created <b>{report.created}</b>,
                  updated <b>{report.updated}</b>,
                  skipped <b>{report.skipped}</b>.
                  {report.extra?.period_auto_locked && (
                    <>&nbsp;Period <b>{report.extra.period_auto_locked}</b> auto-locked.</>
                  )}
                  {report.extra?.period_overrides?.length && (
                    <>&nbsp;Unlocked: <b>{report.extra.period_overrides.join(', ')}</b>.</>
                  )}
                </div>
                {!!report.errors?.length && (
                  <details>
                    <summary>{report.errors.length} warnings/errors</summary>
                    <ul>{report.errors.map((e, i) => <li key={i}>{e}</li>)}</ul>
                  </details>
                )}
              </>
            )}
          </div>
        )}
      </div>

      <style jsx>{`
        .smart-upload { margin: 0 0 24px; }
        .su-card {
          background: #fff;
          border: 1px solid #E1E5EB;
          border-radius: 12px;
          padding: 18px 20px;
          box-shadow: 0 1px 2px rgba(13,27,42,0.04);
        }
        .su-pill {
          display: inline-block;
          background: #0D1B2A;
          color: #F4A623;
          padding: 2px 10px;
          border-radius: 999px;
          font-size: 11px;
          letter-spacing: 0.04em;
          text-transform: uppercase;
          margin-right: 10px;
          vertical-align: middle;
        }
        .su-title h2 {
          display: inline-block;
          margin: 0;
          font-size: 17px;
          color: #0D1B2A;
          vertical-align: middle;
        }
        .su-template {
          margin-left: 12px;
          font-size: 12px;
          color: #0D1B2A;
          text-decoration: underline;
          text-underline-offset: 2px;
          vertical-align: middle;
        }
        .su-template:hover { color: #F4A623; }
        .su-ai-tag {
          background: #0D1B2A;
          color: #F4A623;
          padding: 2px 8px;
          border-radius: 999px;
          font-size: 11px;
          font-weight: 600;
          letter-spacing: 0.04em;
          text-transform: uppercase;
        }
        .su-sub {
          color: #6B7480;
          font-size: 13px;
          margin: 6px 0 14px;
        }
        .su-row {
          display: flex;
          flex-wrap: wrap;
          gap: 12px;
          align-items: end;
        }
        .su-field { display: flex; flex-direction: column; gap: 4px; }
        .su-field.grow { flex: 1 1 240px; }
        .su-field span {
          font-size: 11px; color: #6B7480; text-transform: uppercase;
          letter-spacing: 0.06em;
        }
        .su-field select, .su-field input[type="file"] {
          padding: 8px 10px;
          border: 1px solid #D6DAE2;
          border-radius: 8px;
          font-size: 14px;
          background: #fff;
        }
        .su-actions { display: flex; gap: 8px; }
        .su-btn {
          padding: 9px 16px;
          border-radius: 8px;
          border: 1px solid #D6DAE2;
          background: #fff;
          font-size: 14px;
          cursor: pointer;
        }
        .su-btn[disabled] { opacity: 0.55; cursor: not-allowed; }
        .su-btn-primary {
          background: #F4A623;
          border-color: #F4A623;
          color: #0D1B2A;
          font-weight: 600;
        }
        .su-btn-primary:hover:not([disabled]) { background: #E89B16; }
        .su-btn-small { padding: 5px 10px; font-size: 12px; }
        .su-error {
          margin-top: 12px;
          padding: 10px 12px;
          background: #FDECEA;
          border: 1px solid #F5C2BE;
          color: #8E1F12;
          border-radius: 8px;
          font-size: 13px;
        }
        /* Aria-translated rejection modal (CFO directive 2026-06-03) */
        .su-modal-backdrop {
          position: fixed; inset: 0; background: rgba(13, 27, 42, 0.62);
          display: flex; align-items: center; justify-content: center;
          z-index: 9999; padding: 16px;
        }
        .su-modal {
          background: #fff; border-radius: 14px; max-width: 640px;
          width: 100%; box-shadow: 0 24px 64px rgba(0,0,0,0.35);
          overflow: hidden; display: flex; flex-direction: column;
        }
        .su-modal-head {
          display: flex; align-items: center; gap: 12px;
          padding: 16px 18px; background: #FFF7ED;
          border-bottom: 1px solid #FED7AA;
        }
        .su-modal-icon { font-size: 22px; }
        .su-modal-head h3 {
          margin: 0; flex: 1; font-size: 16px;
          color: #7C2D12; font-weight: 600;
        }
        .su-modal-close {
          background: transparent; border: none; font-size: 22px;
          color: #92400E; cursor: pointer; line-height: 1;
        }
        .su-modal-body { padding: 18px; max-height: 60vh; overflow: auto; }
        .su-friendly-msg { font-size: 14px; color: #1F2937; margin: 0 0 12px; }
        .su-friendly-fix {
          background: #ECFDF5; border: 1px solid #A7F3D0;
          border-radius: 8px; padding: 10px 12px; margin: 10px 0;
          color: #065F46; font-size: 13px;
        }
        .su-friendly-fix p { margin: 4px 0 0; }
        .su-missing {
          margin-top: 14px; padding: 12px;
          background: #FEF2F2; border: 1px solid #FECACA;
          border-radius: 8px; color: #7F1D1D;
        }
        .su-missing-sub { margin: 4px 0 8px; font-size: 12px; color: #B91C1C; }
        .su-missing ul { margin: 8px 0 0; padding-left: 20px; font-size: 13px; }
        .su-missing li { margin: 3px 0; }
        .su-missing code {
          background: #fff; padding: 1px 6px; border-radius: 4px;
          border: 1px solid #FECACA; font-family: ui-monospace, monospace;
        }
        .su-dr { color: #B91C1C; margin-left: 6px; }
        .su-cr { color: #047857; margin-left: 6px; }
        .su-raw-detail {
          margin-top: 14px; font-size: 12px; color: #6B7280;
        }
        .su-raw-detail summary { cursor: pointer; user-select: none; }
        .su-raw-detail ul { margin: 6px 0; padding-left: 20px; }
        .su-source { font-style: italic; }
        .su-modal-foot {
          padding: 12px 18px; border-top: 1px solid #E5E7EB;
          display: flex; justify-content: flex-end;
        }
        .su-warn {
          margin-top: 12px;
          padding: 10px 12px;
          background: #FFF7E6;
          border: 1px solid #F5D584;
          color: #6B4F00;
          border-radius: 8px;
          font-size: 13px;
        }
        .su-preview { margin-top: 16px; }
        .su-meta {
          display: flex; gap: 18px; flex-wrap: wrap;
          font-size: 13px; color: #6B7480; margin-bottom: 10px;
        }
        .su-meta b { color: #0D1B2A; }
        .su-mapping, .su-table {
          width: 100%; border-collapse: collapse; margin-top: 8px;
          font-size: 13px;
        }
        .su-mapping th, .su-table th {
          text-align: left; background: #F5F7FB; padding: 6px 8px;
          font-weight: 600; color: #0D1B2A;
        }
        .su-mapping td, .su-table td {
          padding: 6px 8px; border-top: 1px solid #ECEEF2;
          color: #2A3B4D;
        }
        .su-table { display: block; overflow-x: auto; max-height: 360px; }
        .su-hint {
          margin: 8px 0 0; font-size: 12px; color: #5A6B7D;
        }
        .su-select {
          width: 100%; max-width: 320px; padding: 4px 6px; font-size: 13px;
          border: 1px solid #D3D9E2; border-radius: 4px; background: #FFF;
          color: #0D1B2A;
        }
        .su-select:focus-visible {
          outline: 2px solid #F4A623; outline-offset: 1px; border-color: #F4A623;
        }
        /* An unmapped column is money that will not post — flag it in the row,
           not only in the banner above. */
        .su-row-unmapped td { background: #FFF7E8; }
        .su-row-unmapped .su-select { border-color: #F4A623; }
        .su-foot {
          font-size: 12px; color: #6B7480; margin-top: 6px;
        }
        .su-result {
          margin-top: 14px;
          padding: 12px 14px;
          background: #ECF8F0;
          border: 1px solid #B6E0C2;
          color: #1F5132;
          border-radius: 8px;
          font-size: 14px;
        }
        .su-result-line b { color: #0D1B2A; }
        details { margin-top: 8px; }
        summary {
          cursor: pointer; font-size: 13px; color: #0D1B2A;
        }
      `}</style>
    </div>
  )
}

function toMessage(e: unknown): string {
  if (e instanceof Error) return e.message
  if (typeof e === 'string') return e
  try { return JSON.stringify(e) } catch { return '' }
}

// Re-exports so callers can write `import SmartUpload from '@/components/SmartUpload'`.
export { SmartUpload }
