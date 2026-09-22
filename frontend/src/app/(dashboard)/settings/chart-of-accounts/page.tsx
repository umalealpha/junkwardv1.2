'use client'

/**
 * Chart of Accounts (Settings) — CFO / Finance Manager domain.
 *
 * Two sections:
 *   A) Bulk upload — drop a CoA CSV, get Aria's review BEFORE commit.
 *      The CFO can read the AI's flagged rows + summary, then click Commit
 *      to actually write, or Cancel to abandon.
 *   B) Create a single account inline — quick form for adding one GL
 *      account without re-uploading the entire CoA.
 *
 * Gated to CFO / admin via the same /cfo-upload-status/ probe used by the
 * legacy /cfo-upload page. Re-authentication with the override password
 * is required for bulk upload only — single-account create relies on the
 * normal user session.
 */

import { useEffect, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import {
  Upload, ShieldCheck, AlertTriangle, CheckCircle2,
  FileText, LockKeyhole, Download, Sparkles, PlusCircle, Archive,
} from 'lucide-react'
import SmartUpload from '@/components/SmartUpload'
import { apiFetch, getToken } from '@/lib/api'

const BASE_URL = process.env.NEXT_PUBLIC_API_BASE ?? ''
const STATUS_PATH    = '/admin/cfo-upload-status/'
const UPLOAD_URL     = `${BASE_URL}/api/v1/admin/cfo-upload-coa/`
const TEMPLATE_URL   = `${BASE_URL}/api/v1/admin/cfo-upload-coa/template/`
const ACCOUNTS_URL   = `${BASE_URL}/api/v1/accounts/`

type PreviewRow = {
  account_code: string
  account_name: string
  statement_class: string
  fs_line_item: string
  normal_balance: string
  company?: string
}

type AiFlag = {
  code?: string
  field?: string
  issue?: string
  severity?: 'high' | 'medium' | 'low'
}

type AiReview = {
  verdict?: 'clean' | 'warnings' | 'errors' | 'unavailable' | string
  flagged_rows?: AiFlag[]
  missing?: string[]
  summary?: string
  rows_reviewed?: number
  rows_total?: number
  reason?: string
}

type PreviewResult = {
  success?: boolean
  preview?: boolean
  filename?: string
  row_count?: number
  error_count?: number
  error_rows?: Array<{ line: number; code: string; error: string }>
  sample_rows?: PreviewRow[]
  ai_review?: AiReview
  error?: string
}

type CommitResult = {
  success?: boolean
  filename?: string
  created?: number
  updated?: number
  unchanged?: number
  archived?: number
  error_count?: number
  error_rows?: Array<{ line: number; code: string; error: string }>
  error?: string
}

async function buildAuthHeader(): Promise<Record<string, string>> {
  const h: Record<string, string> = {}
  try {
    const { acquireApiToken, SSO_API_CALLS_READY } = await import('@/auth/msal')
    if (SSO_API_CALLS_READY) {
      const bearer = await acquireApiToken()
      if (bearer) h['Authorization'] = `Bearer ${bearer}`
    }
  } catch { /* fall through */ }
  if (!h['Authorization']) {
    const t = getToken()
    if (t) h['Authorization'] = `Token ${t}`
  }
  return h
}

export default function ChartOfAccountsSettings() {
  const router = useRouter()
  const [isCfo, setIsCfo] = useState<boolean | null>(null)
  const [username, setUsername] = useState('')

  useEffect(() => {
    const token = getToken()
    if (!token) { router.replace('/login'); return }
    apiFetch<{ is_cfo: boolean; username: string }>(STATUS_PATH)
      .then(d => {
        setIsCfo(!!d.is_cfo)
        setUsername(d.username || '')
        if (!d.is_cfo) router.replace('/dashboard')
      })
      .catch(() => setIsCfo(false))
  }, [router])

  if (isCfo === null) {
    return <div className="p-10 text-slate-500"><ShieldCheck className="inline mr-2" /> Checking authority…</div>
  }
  if (!isCfo) {
    return (
      <div className="p-10 max-w-xl">
        <AlertTriangle className="w-10 h-10 text-orange-500 mb-4" />
        <h1 className="text-2xl mb-2">CFO / Finance Manager only</h1>
        <p className="text-slate-600">
          Your account ({username}) does not have authority to manage the chart of accounts.
        </p>
        <Link href="/dashboard" className="text-orange-600 hover:underline mt-4 inline-block">Back to dashboard</Link>
      </div>
    )
  }

  return (
    <div className="p-8 max-w-5xl mx-auto space-y-10">
      <div>
        <div className="flex items-center gap-3 mb-2">
          <FileText className="w-7 h-7 text-orange-600" />
          <h1 className="text-3xl">Chart of Accounts</h1>
        </div>
        <p className="text-slate-600">
          Configure the authoritative GL accounts. Bulk upload a CSV, or create
          single accounts inline. The AI reviewer flags mis-mappings before
          you commit a bulk upload.
        </p>
      </div>

      <SmartUpload section="coa" />
      <SmartUpload section="vendors" />
      <SmartUpload section="customers" />
      <SmartUpload section="bank_accounts" />
      <BulkUploadSection />
      <CreateAccountSection />
    </div>
  )
}


// ─── Section A: Bulk upload with Aria preview ─────────────────────────────

function BulkUploadSection() {
  const fileRef = useRef<HTMLInputElement>(null)
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [archiveUnmatched, setArchiveUnmatched] = useState(true)
  const [phase, setPhase] = useState<'idle' | 'previewing' | 'preview-ready' | 'committing' | 'done'>('idle')
  const [preview, setPreview] = useState<PreviewResult | null>(null)
  const [commitResult, setCommitResult] = useState<CommitResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  const resetPreview = () => {
    setPreview(null); setCommitResult(null); setError(null); setPhase('idle')
  }

  const runPreview = async () => {
    if (!selectedFile) { setError('Choose a CSV file first.'); return }
    if (!password)     { setError('Enter the override password.'); return }
    setError(null); setCommitResult(null)
    setPhase('previewing')
    try {
      const authHeader = await buildAuthHeader()
      const fd = new FormData()
      fd.append('file', selectedFile)
      fd.append('override_password', password)
      fd.append('preview', 'true')
      const res = await fetch(UPLOAD_URL, { method: 'POST', headers: authHeader, body: fd })
      const data: PreviewResult = await res.json()
      if (!res.ok) {
        setError(data.error || `HTTP ${res.status}`)
        setPhase('idle')
        return
      }
      setPreview(data)
      setPhase('preview-ready')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Network error')
      setPhase('idle')
    }
  }

  const runCommit = async () => {
    if (!selectedFile) { setError('No file in memory. Re-select and preview first.'); return }
    setError(null)
    setPhase('committing')
    try {
      const authHeader = await buildAuthHeader()
      const fd = new FormData()
      fd.append('file', selectedFile)
      fd.append('override_password', password)
      fd.append('preview', 'false')
      fd.append('archive_unmatched', archiveUnmatched ? 'true' : 'false')
      const res = await fetch(UPLOAD_URL, { method: 'POST', headers: authHeader, body: fd })
      const data: CommitResult = await res.json()
      if (!res.ok) {
        setError(data.error || `HTTP ${res.status}`)
        setPhase('preview-ready')
        return
      }
      setCommitResult(data)
      setPhase('done')
      if (data.success) setPassword('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Network error')
      setPhase('preview-ready')
    }
  }

  const downloadTemplate = async () => {
    const authHeader = await buildAuthHeader()
    const res = await fetch(TEMPLATE_URL, { headers: authHeader })
    if (!res.ok) { alert(`Could not download template (HTTP ${res.status}).`); return }
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = 'coa_template.csv'
    document.body.appendChild(a); a.click(); a.remove()
    URL.revokeObjectURL(url)
  }

  return (
    <section className="border rounded-lg p-6 bg-white space-y-5 shadow-card">
      <div className="flex items-center gap-2">
        <Upload className="w-5 h-5 text-orange-600" />
        <h2 className="text-xl font-semibold">Bulk upload (CSV)</h2>
      </div>
      <p className="text-sm text-slate-600">
        Step 1 — preview with AI. Step 2 — commit to the database. Required
        columns: <code className="bg-slate-100 px-1 rounded">company, account_code, account_name, statement_class, fs_line_item, normal_balance</code>.
      </p>

      <div>
        <button
          type="button"
          onClick={downloadTemplate}
          className="inline-flex items-center gap-2 text-sm text-orange-700 hover:text-orange-800 border border-orange-200 hover:border-orange-300 bg-orange-50 px-3 py-1.5 rounded-md"
        >
          <Download className="w-4 h-4" />
          Download CSV template
        </button>
      </div>

      <div>
        <label className="block mb-2 font-medium">
          <FileText className="inline w-4 h-4 mr-1" />
          CoA CSV file
        </label>
        <input
          ref={fileRef}
          type="file"
          accept=".csv,text/csv,.txt"
          onChange={e => { setSelectedFile(e.target.files?.[0] || null); resetPreview() }}
          className="block w-full text-sm text-slate-600
                     file:mr-4 file:py-2 file:px-4
                     file:rounded-md file:border-0
                     file:bg-orange-50 file:text-orange-700
                     hover:file:bg-orange-100 file:cursor-pointer"
        />
        {selectedFile && (
          <p className="mt-2 text-xs text-slate-500">{selectedFile.name} ({(selectedFile.size / 1024).toFixed(1)} KB)</p>
        )}
      </div>

      <div className="flex items-center gap-2">
        <input
          type="checkbox"
          id="archive-unmatched"
          checked={archiveUnmatched}
          onChange={e => setArchiveUnmatched(e.target.checked)}
        />
        <label htmlFor="archive-unmatched" className="text-sm text-slate-700">
          Archive accounts not in this CSV (preserves JE history, hides from selectors)
        </label>
      </div>

      <div>
        <label className="block mb-2 font-medium">
          <LockKeyhole className="inline w-4 h-4 mr-1" />
          Override password
        </label>
        <div className="flex gap-2">
          <input
            type={showPassword ? 'text' : 'password'}
            value={password}
            onChange={e => setPassword(e.target.value)}
            autoComplete="off"
            placeholder="Override password"
            className="flex-1 border border-slate-300 rounded px-3 py-2"
          />
          <button
            type="button"
            onClick={() => setShowPassword(s => !s)}
            className="px-3 py-2 border border-slate-300 rounded text-sm text-slate-700"
          >
            {showPassword ? 'Hide' : 'Show'}
          </button>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3 pt-1">
        <button
          type="button"
          onClick={runPreview}
          disabled={phase === 'previewing' || phase === 'committing' || !selectedFile || !password}
          className="inline-flex items-center gap-2 px-4 py-2 bg-slate-900 text-white rounded-md font-medium hover:bg-slate-700 disabled:bg-slate-300 disabled:cursor-not-allowed"
        >
          <Sparkles className="w-4 h-4" />
          {phase === 'previewing' ? 'Previewing with AI…' : 'Preview with AI'}
        </button>
        <button
          type="button"
          onClick={runCommit}
          disabled={phase !== 'preview-ready'}
          className="inline-flex items-center gap-2 px-4 py-2 bg-orange-600 text-white rounded-md font-medium hover:bg-orange-700 disabled:bg-slate-300 disabled:cursor-not-allowed"
        >
          <CheckCircle2 className="w-4 h-4" />
          {phase === 'committing' ? 'Committing…' : 'Commit upload'}
        </button>
        {phase !== 'idle' && (
          <button
            type="button"
            onClick={resetPreview}
            className="text-sm text-slate-600 hover:text-slate-900 underline"
          >
            Discard preview
          </button>
        )}
      </div>

      {error && (
        <div className="border border-red-200 bg-red-50 rounded-md p-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {preview && phase !== 'done' && <PreviewPanel preview={preview} />}
      {commitResult && phase === 'done' && <CommitPanel result={commitResult} />}
    </section>
  )
}


function PreviewPanel({ preview }: { preview: PreviewResult }) {
  const ai = preview.ai_review || {}
  const verdict = ai.verdict || 'unavailable'
  const verdictStyle =
    verdict === 'clean'    ? 'bg-green-100 text-green-800' :
    verdict === 'warnings' ? 'bg-amber-100 text-amber-800' :
    verdict === 'errors'   ? 'bg-red-100 text-red-800'   :
                             'bg-slate-100 text-slate-700'

  return (
    <div className="border border-slate-200 rounded-md p-4 bg-slate-50 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="font-semibold text-slate-900">Preview — review before commit</h3>
        <span className={`text-xs font-medium px-2 py-1 rounded ${verdictStyle}`}>
          AI verdict: {verdict}
        </span>
      </div>

      <div className="grid grid-cols-3 gap-4 text-sm">
        <div>
          <div className="text-slate-500 text-xs uppercase tracking-wide">Rows parsed</div>
          <div className="text-2xl font-semibold">{preview.row_count ?? 0}</div>
        </div>
        <div>
          <div className="text-slate-500 text-xs uppercase tracking-wide">Validation errors</div>
          <div className={`text-2xl font-semibold ${(preview.error_count || 0) > 0 ? 'text-red-700' : 'text-slate-700'}`}>
            {preview.error_count ?? 0}
          </div>
        </div>
        <div>
          <div className="text-slate-500 text-xs uppercase tracking-wide">AI flagged</div>
          <div className={`text-2xl font-semibold ${(ai.flagged_rows?.length || 0) > 0 ? 'text-amber-700' : 'text-slate-700'}`}>
            {ai.flagged_rows?.length ?? 0}
          </div>
        </div>
      </div>

      {ai.summary && (
        <div>
          <div className="text-slate-500 text-xs uppercase tracking-wide mb-1">AI summary</div>
          <p className="text-sm text-slate-700">{ai.summary}</p>
        </div>
      )}

      {!!ai.flagged_rows?.length && (
        <details open className="text-sm">
          <summary className="cursor-pointer font-medium text-amber-800">
            AI-flagged rows ({ai.flagged_rows.length})
          </summary>
          <table className="mt-2 w-full text-xs border-collapse">
            <thead>
              <tr className="bg-amber-50">
                <th className="px-2 py-1 text-left">Code</th>
                <th className="px-2 py-1 text-left">Field</th>
                <th className="px-2 py-1 text-left">Issue</th>
                <th className="px-2 py-1 text-left">Severity</th>
              </tr>
            </thead>
            <tbody>
              {ai.flagged_rows.map((f, i) => (
                <tr key={i} className="border-t border-amber-200">
                  <td className="px-2 py-1 font-mono">{f.code}</td>
                  <td className="px-2 py-1">{f.field}</td>
                  <td className="px-2 py-1">{f.issue}</td>
                  <td className="px-2 py-1">{f.severity}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}

      {!!ai.missing?.length && (
        <div>
          <div className="text-slate-500 text-xs uppercase tracking-wide mb-1">AI flagged as MISSING</div>
          <ul className="list-disc pl-5 text-sm text-amber-800">
            {ai.missing.map((m, i) => <li key={i}>{m}</li>)}
          </ul>
        </div>
      )}

      {verdict === 'unavailable' && (
        <div className="text-xs text-slate-500 italic">
          AI reviewer unavailable: {ai.reason || 'no reason given'}. You can still commit — the AI is advisory.
        </div>
      )}

      {!!preview.error_rows?.length && (
        <details className="text-sm">
          <summary className="cursor-pointer text-red-700">
            Validation errors ({preview.error_rows.length})
          </summary>
          <table className="mt-2 w-full text-xs border-collapse">
            <thead>
              <tr className="bg-red-50">
                <th className="px-2 py-1 text-left">Line</th>
                <th className="px-2 py-1 text-left">Code</th>
                <th className="px-2 py-1 text-left">Error</th>
              </tr>
            </thead>
            <tbody>
              {preview.error_rows.map((r, i) => (
                <tr key={i} className="border-t border-red-200">
                  <td className="px-2 py-1">{r.line}</td>
                  <td className="px-2 py-1 font-mono">{r.code}</td>
                  <td className="px-2 py-1 text-red-700">{r.error}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}

      {!!preview.sample_rows?.length && (
        <details className="text-sm">
          <summary className="cursor-pointer text-slate-700">
            Sample rows (first {preview.sample_rows.length})
          </summary>
          <table className="mt-2 w-full text-xs border-collapse">
            <thead>
              <tr className="bg-slate-100">
                <th className="px-2 py-1 text-left">Code</th>
                <th className="px-2 py-1 text-left">Name</th>
                <th className="px-2 py-1 text-left">BS/PNL</th>
                <th className="px-2 py-1 text-left">FS line item</th>
                <th className="px-2 py-1 text-left">D/C</th>
              </tr>
            </thead>
            <tbody>
              {preview.sample_rows.map((r, i) => (
                <tr key={i} className="border-t border-slate-200">
                  <td className="px-2 py-1 font-mono">{r.account_code}</td>
                  <td className="px-2 py-1">{r.account_name}</td>
                  <td className="px-2 py-1">{r.statement_class}</td>
                  <td className="px-2 py-1">{r.fs_line_item}</td>
                  <td className="px-2 py-1">{r.normal_balance}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </div>
  )
}


function CommitPanel({ result }: { result: CommitResult }) {
  return (
    <div className="border border-green-200 bg-green-50 rounded-md p-4 space-y-2">
      <div className="flex items-center gap-2">
        <CheckCircle2 className="w-5 h-5 text-green-700" />
        <span className="font-medium">Committed — {result.filename}</span>
      </div>
      <ul className="text-sm text-slate-700 list-disc pl-5">
        <li>Created: <strong>{result.created ?? 0}</strong></li>
        <li>Updated: <strong>{result.updated ?? 0}</strong></li>
        <li>Unchanged: <strong>{result.unchanged ?? 0}</strong></li>
        <li>Archived (not in CSV): <strong>{result.archived ?? 0}</strong></li>
        {!!result.error_count && (
          <li className="text-red-700">Error rows skipped: {result.error_count}</li>
        )}
      </ul>
      <Link href="/dashboard" className="text-orange-600 hover:underline text-sm">View dashboard →</Link>
    </div>
  )
}


// ─── Section B: Create a single account inline ────────────────────────────────

function CreateAccountSection() {
  const [code, setCode] = useState('')
  const [name, setName] = useState('')
  const [sclass, setSclass] = useState<'BS' | 'PNL'>('BS')
  const [fsLine, setFsLine] = useState('')
  const [dc, setDc] = useState<'D' | 'C'>('D')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const submit = async () => {
    if (!code.trim() || !name.trim() || !fsLine.trim()) {
      setMsg({ ok: false, text: 'Code, name, and FS line item are required.' }); return
    }
    setBusy(true); setMsg(null)
    try {
      // Derive account_type from (sclass, dc) — backend has same table but
      // the AccountSerializer requires account_type explicitly.
      const inferred =
        sclass === 'BS'  && dc === 'D' ? { account_type: 'asset',     sub_type: 'current_asset' } :
        sclass === 'BS'  && dc === 'C' ? { account_type: 'liability', sub_type: 'current_liability' } :
        sclass === 'PNL' && dc === 'D' ? { account_type: 'expense',   sub_type: 'operating_expense' } :
                                         { account_type: 'revenue',   sub_type: 'operating_revenue' }
      const body = {
        code: code.trim(), name: name.trim(),
        statement_class: sclass, fs_line_item: fsLine.trim(),
        normal_balance_dc: dc,
        ...inferred,
        is_active: true,
      }
      const res = await apiFetch<{ id?: string; code?: string; detail?: string }>(
        '/accounts/', { method: 'POST', body: JSON.stringify(body) }
      )
      if (res?.id || res?.code) {
        setMsg({ ok: true, text: `Created account ${res.code}.` })
        setCode(''); setName(''); setFsLine('')
      } else {
        setMsg({ ok: false, text: res?.detail || 'Account create failed.' })
      }
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : 'Network error' })
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="border rounded-lg p-6 bg-white space-y-4 shadow-card">
      <div className="flex items-center gap-2">
        <PlusCircle className="w-5 h-5 text-orange-600" />
        <h2 className="text-xl font-semibold">Add a single account</h2>
      </div>
      <p className="text-sm text-slate-600">
        For ad-hoc additions without re-uploading the whole CoA CSV.
      </p>

      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="block text-sm font-medium mb-1">Account code</label>
          <input
            type="text" value={code}
            onChange={e => setCode(e.target.value)}
            placeholder="e.g. 1310"
            className="w-full border border-slate-300 rounded px-3 py-2 font-mono"
          />
        </div>
        <div>
          <label className="block text-sm font-medium mb-1">Account name</label>
          <input
            type="text" value={name}
            onChange={e => setName(e.target.value)}
            placeholder="e.g. Prepaid Insurance"
            className="w-full border border-slate-300 rounded px-3 py-2"
          />
        </div>
        <div>
          <label className="block text-sm font-medium mb-1">Statement class</label>
          <select
            value={sclass}
            onChange={e => setSclass(e.target.value as 'BS' | 'PNL')}
            className="w-full border border-slate-300 rounded px-3 py-2"
          >
            <option value="BS">Balance Sheet (BS)</option>
            <option value="PNL">Profit & Loss (PNL)</option>
          </select>
        </div>
        <div>
          <label className="block text-sm font-medium mb-1">Normal balance</label>
          <select
            value={dc}
            onChange={e => setDc(e.target.value as 'D' | 'C')}
            className="w-full border border-slate-300 rounded px-3 py-2"
          >
            <option value="D">Debit-natural (assets, expenses)</option>
            <option value="C">Credit-natural (liabilities, equity, revenue)</option>
          </select>
        </div>
        <div className="col-span-2">
          <label className="block text-sm font-medium mb-1">FS line item</label>
          <input
            type="text" value={fsLine}
            onChange={e => setFsLine(e.target.value)}
            placeholder='e.g. "Cash and bank", "Crossover", "Gross written premium"'
            className="w-full border border-slate-300 rounded px-3 py-2"
          />
        </div>
      </div>

      <div>
        <button
          type="button"
          onClick={submit}
          disabled={busy}
          className="inline-flex items-center gap-2 px-4 py-2 bg-orange-600 text-white rounded-md font-medium hover:bg-orange-700 disabled:bg-slate-300"
        >
          <PlusCircle className="w-4 h-4" />
          {busy ? 'Creating…' : 'Create account'}
        </button>
      </div>

      {msg && (
        <div className={`text-sm border rounded-md p-3 ${msg.ok ? 'border-green-200 bg-green-50 text-green-800' : 'border-red-200 bg-red-50 text-red-700'}`}>
          {msg.text}
        </div>
      )}
    </section>
  )
}
