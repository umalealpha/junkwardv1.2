'use client'

/**
 * CFO Upload page — 3 tabs:
 *   1. Chart of Accounts (CFO format)
 *   2. General Ledger    (CFO format, balanced JE)
 *   3. Trial Balance     (Odoo format, legacy)
 *
 * All three require CFO authority + the OMNI_FINANCIAL_LOCK_OVERRIDE password.
 * Each tab: download template → pick CSV → submit → see result.
 */

import { useEffect, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import {
  Upload, ShieldCheck, AlertTriangle, CheckCircle2,
  FileText, LockKeyhole, Download, BookOpen, FileSpreadsheet,
} from 'lucide-react'
import { apiFetch, getToken } from '@/lib/api'
import { localYmd } from '@/lib/utils'

const BASE_URL = process.env.NEXT_PUBLIC_API_BASE ?? ''
const STATUS_PATH = '/admin/cfo-upload-status/'

type TabKey = 'gl' | 'tb' | 'coa'

type TabSpec = {
  key: TabKey
  label: string
  icon: typeof BookOpen
  uploadUrl: string
  templateUrl?: string
  description: string
  columnsHint: string
  extraFields?: 'gl' | null
}

// Note: the Chart of Accounts tab moved to /settings/chart-of-accounts on
// 2026-05-17 per CFO directive — that's CFO/FM domain (configuration) and
// includes a Aria AI preview step before commit.
const TABS: TabSpec[] = [
  {
    key: 'gl',
    label: 'General Ledger',
    icon: BookOpen,
    uploadUrl: `${BASE_URL}/api/v1/admin/cfo-upload-gl/`,
    templateUrl: `${BASE_URL}/api/v1/admin/cfo-upload-gl/template/`,
    description:
      'Upload GL balances. One balanced JournalEntry is created and posted at the as-of date. Missing accounts are auto-created using the row’s statement_class.',
    columnsHint: 'company, account_code, account_name, amount, dc (D/C), statement_class (BS/PNL), fs_line_item',
    extraFields: 'gl',
  },
  {
    key: 'tb',
    label: 'Trial Balance',
    icon: FileSpreadsheet,
    uploadUrl: `${BASE_URL}/api/v1/admin/cfo-upload-tb/`,
    description:
      'Legacy Odoo TB format (alpha_direct_full_tb_complete.csv). Runs import_tb_csv --commit server-side. Idempotent.',
    columnsHint: 'Period, Row Type, Account Code, Account Name, End Balance Debit (BWP), End Balance Credit (BWP), Period Activity Debit, Period Activity Credit',
    extraFields: null,
  },
]

type UploadResult = Record<string, unknown> & {
  success?: boolean
  error?: string
  filename?: string
  created?: number
  updated?: number
  archived?: number
  unchanged?: number
  error_count?: number
  error_rows?: Array<{ line: number; code: string; error: string }>
  journal_entry_number?: string
  line_count?: number
  accounts_auto_created?: number
  totals?: { debit?: string; credit?: string; rounding_diff?: string }
  as_of_date?: string
  company?: string
  log_tail?: string
  summary?: Record<string, string>
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

export default function CfoUploadPage() {
  const router = useRouter()
  const [isCfo, setIsCfo] = useState<boolean | null>(null)
  const [username, setUsername] = useState<string>('')
  const [activeTab, setActiveTab] = useState<TabKey>('coa')

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
    return (
      <div className="p-10 text-slate-500">
        <ShieldCheck className="inline mr-2" /> Checking authority...
      </div>
    )
  }

  if (!isCfo) {
    return (
      <div className="p-10 max-w-xl">
        <AlertTriangle className="w-10 h-10 text-orange-500 mb-4" />
        <h1 className="text-2xl mb-2">Finance team only</h1>
        <p className="text-slate-600">
          Your account ({username}) does not have authority to use the CFO upload tools.
        </p>
        <Link href="/dashboard" className="text-orange-600 hover:underline mt-4 inline-block">
          Back to dashboard
        </Link>
      </div>
    )
  }

  return (
    <div className="p-8 max-w-4xl mx-auto">
      <div className="flex items-center gap-3 mb-2">
        <Upload className="w-7 h-7 text-orange-600" />
        <h1 className="text-3xl">CFO Bulk Upload</h1>
      </div>
      <p className="text-slate-600 mb-4">
        Upload authoritative accounting data. Each tab handles a different file
        format. Restricted to CFO / Finance Manager / Financial Controller — override password required on every commit.
      </p>

      <div className="border border-orange-200 bg-orange-50 rounded-md px-4 py-3 mb-6 text-sm text-orange-900">
        <strong>Chart of Accounts moved.</strong> CoA configuration lives in
        <Link href="/settings/chart-of-accounts" className="underline ml-1 font-medium">Settings → Chart of Accounts</Link>
        — it has an AI preview step before commit and lets you add single
        accounts inline.
      </div>

      {/* Tab strip */}
      <div className="flex border-b border-slate-200 mb-6">
        {TABS.map(t => {
          const Icon = t.icon
          const active = activeTab === t.key
          return (
            <button
              key={t.key}
              type="button"
              onClick={() => setActiveTab(t.key)}
              className={`flex items-center gap-2 px-5 py-3 -mb-px text-sm font-medium border-b-2 transition-colors ${
                active
                  ? 'border-orange-600 text-orange-600'
                  : 'border-transparent text-slate-600 hover:text-slate-900'
              }`}
            >
              <Icon className="w-4 h-4" />
              {t.label}
            </button>
          )
        })}
      </div>

      {TABS.filter(t => t.key === activeTab).map(t => (
        <UploadPanel key={t.key} spec={t} />
      ))}
    </div>
  )
}


function UploadPanel({ spec }: { spec: TabSpec }) {
  const fileRef = useRef<HTMLInputElement>(null)
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [asOfDate, setAsOfDate] = useState(() => {
    // default: last day of previous month
    const t = new Date()
    const first = new Date(t.getFullYear(), t.getMonth(), 1)
    first.setDate(0)
    return localYmd(first)
  })
  const [defaultCompany, setDefaultCompany] = useState('ADIC')
  const [archiveUnmatched, setArchiveUnmatched] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [result, setResult] = useState<UploadResult | null>(null)

  const onUpload = async () => {
    if (!selectedFile) {
      setResult({ success: false, error: 'Choose a CSV file first.' }); return
    }
    if (!password) {
      setResult({ success: false, error: 'Enter the override password before uploading.' }); return
    }
    setSubmitting(true)
    setResult(null)
    try {
      const authHeader = await buildAuthHeader()
      const fd = new FormData()
      fd.append('file', selectedFile)
      fd.append('override_password', password)
      if (spec.key === 'gl') {
        fd.append('as_of_date', asOfDate)
        fd.append('company', defaultCompany)
      }
      if (spec.key === 'coa') {
        fd.append('archive_unmatched', archiveUnmatched ? 'true' : 'false')
      }
      const res = await fetch(spec.uploadUrl, {
        method: 'POST',
        headers: authHeader,
        body: fd,
      })
      const data = await res.json()
      if (!res.ok) {
        setResult({ success: false, ...data, error: data.error || data.detail || `HTTP ${res.status}` })
      } else {
        setResult(data)
        if (data.success) setPassword('')
      }
    } catch (err) {
      setResult({ success: false, error: err instanceof Error ? err.message : 'Network error' })
    } finally {
      setSubmitting(false)
    }
  }

  const downloadTemplate = async () => {
    if (!spec.templateUrl) return
    const authHeader = await buildAuthHeader()
    const res = await fetch(spec.templateUrl, { headers: authHeader })
    if (!res.ok) {
      alert(`Could not download template (HTTP ${res.status}).`)
      return
    }
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${spec.key}_template.csv`
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="border rounded-lg p-6 bg-white space-y-6 shadow-card">
      <div>
        <p className="text-slate-700">{spec.description}</p>
        <p className="text-xs text-slate-500 mt-2">
          <strong>Required columns:</strong> {spec.columnsHint}
        </p>
      </div>

      {spec.templateUrl && (
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
      )}

      {/* File picker */}
      <div>
        <label className="block mb-2 font-medium">
          <FileText className="inline w-4 h-4 mr-1" />
          CSV file
        </label>
        <input
          ref={fileRef}
          type="file"
          accept=".csv,text/csv,.txt"
          onChange={e => { setSelectedFile(e.target.files?.[0] || null); setResult(null) }}
          className="block w-full text-sm text-slate-600
                     file:mr-4 file:py-2 file:px-4
                     file:rounded-md file:border-0
                     file:bg-orange-50 file:text-orange-700
                     hover:file:bg-orange-100 file:cursor-pointer"
        />
        {selectedFile && (
          <p className="mt-2 text-xs text-slate-500">
            {selectedFile.name} ({(selectedFile.size / 1024).toFixed(1)} KB)
          </p>
        )}
      </div>

      {/* GL extra fields */}
      {spec.extraFields === 'gl' && (
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block mb-1 text-sm font-medium">As-of date</label>
            <input
              type="date"
              value={asOfDate}
              onChange={e => setAsOfDate(e.target.value)}
              className="w-full border border-slate-300 rounded px-3 py-2"
            />
            <p className="text-xs text-slate-500 mt-1">Date stamped on the JE.</p>
          </div>
          <div>
            <label className="block mb-1 text-sm font-medium">Default company</label>
            <input
              type="text"
              value={defaultCompany}
              onChange={e => setDefaultCompany(e.target.value.toUpperCase())}
              placeholder="ADIC"
              className="w-full border border-slate-300 rounded px-3 py-2 uppercase"
            />
            <p className="text-xs text-slate-500 mt-1">Used when a row’s company column is blank.</p>
          </div>
        </div>
      )}

      {/* CoA extra fields */}
      {spec.key === 'coa' && (
        <div className="flex items-center gap-2">
          <input
            type="checkbox"
            id="archive-unmatched"
            checked={archiveUnmatched}
            onChange={e => setArchiveUnmatched(e.target.checked)}
            className="rounded"
          />
          <label htmlFor="archive-unmatched" className="text-sm text-slate-700">
            Archive accounts not in this CSV (preserves JE history, hides from selectors)
          </label>
        </div>
      )}

      {/* Override password */}
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

      {/* Submit */}
      <div className="pt-2">
        <button
          type="button"
          onClick={onUpload}
          disabled={submitting || !selectedFile || !password}
          className="px-5 py-2.5 bg-orange-600 text-white rounded-md font-medium
                     hover:bg-orange-700 disabled:bg-slate-300 disabled:cursor-not-allowed"
        >
          {submitting ? 'Uploading…' : 'Upload and import'}
        </button>
      </div>

      {/* Result */}
      {result && <ResultPanel result={result} tabKey={spec.key} />}
    </div>
  )
}


function ResultPanel({ result, tabKey }: { result: UploadResult; tabKey: TabKey }) {
  const ok = !!result.success
  return (
    <div className={`mt-2 border rounded-lg p-5 ${ok ? 'border-green-200 bg-green-50' : 'border-red-200 bg-red-50'}`}>
      <div className="flex items-center gap-2 mb-2">
        {ok
          ? <CheckCircle2 className="w-5 h-5 text-green-600" />
          : <AlertTriangle className="w-5 h-5 text-red-600" />}
        <span className="font-medium">{ok ? 'Import successful' : 'Import failed'}</span>
      </div>

      {result.error && <p className="text-red-700 mb-2">{result.error}</p>}

      {/* CoA-shape result */}
      {tabKey === 'coa' && ok && (
        <ul className="text-sm text-slate-700 list-disc pl-5">
          <li>Created: <strong>{result.created ?? 0}</strong></li>
          <li>Updated: <strong>{result.updated ?? 0}</strong></li>
          <li>Unchanged: <strong>{result.unchanged ?? 0}</strong></li>
          <li>Archived (not in CSV): <strong>{result.archived ?? 0}</strong></li>
          {!!result.error_count && (
            <li className="text-red-700">Error rows skipped: {result.error_count}</li>
          )}
        </ul>
      )}

      {/* GL-shape result */}
      {tabKey === 'gl' && ok && (
        <ul className="text-sm text-slate-700 list-disc pl-5">
          <li>Journal entry: <strong>{result.journal_entry_number}</strong></li>
          <li>Lines posted: <strong>{result.line_count}</strong></li>
          <li>As-of date: <strong>{result.as_of_date}</strong></li>
          <li>Company: <strong>{result.company}</strong></li>
          <li>Total Dr: <strong>{result.totals?.debit}</strong></li>
          <li>Total Cr: <strong>{result.totals?.credit}</strong></li>
          {!!result.accounts_auto_created && (
            <li>Accounts auto-created: <strong>{result.accounts_auto_created}</strong></li>
          )}
        </ul>
      )}

      {/* TB-shape result */}
      {tabKey === 'tb' && ok && result.summary && (
        <div className="text-sm text-slate-700">
          <p className="font-medium mb-1">Bank balances after import (ADIC):</p>
          <ul className="list-disc pl-5">
            {Object.entries(result.summary).map(([k, v]) => (
              <li key={k}><code>{k}</code>: {v}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Error rows table */}
      {!!result.error_rows?.length && (
        <details className="mt-3">
          <summary className="cursor-pointer text-sm text-red-700">
            Show first {result.error_rows.length} error row(s)
          </summary>
          <table className="mt-2 text-xs w-full border-collapse">
            <thead>
              <tr className="text-left bg-red-100">
                <th className="px-2 py-1">Line</th>
                <th className="px-2 py-1">Code</th>
                <th className="px-2 py-1">Error</th>
              </tr>
            </thead>
            <tbody>
              {result.error_rows.map((r, i) => (
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

      {result.log_tail && (
        <details className="mt-4">
          <summary className="cursor-pointer text-sm text-slate-600">
            Command output (last 30 lines)
          </summary>
          <pre className="mt-2 text-xs bg-slate-900 text-slate-100 rounded p-3 overflow-x-auto">
            {result.log_tail}
          </pre>
        </details>
      )}

      {ok && (
        <Link href="/dashboard" className="inline-block mt-4 text-orange-600 hover:underline">
          View dashboard →
        </Link>
      )}
    </div>
  )
}
