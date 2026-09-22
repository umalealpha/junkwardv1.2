'use client'

/**
 * /hris/leave/report — manager/HR leave balance report (HRIS-002).
 *
 * Requested by Oprah Mogomotsi 2026-06-10: one row per employee × leave
 * type with Opening / Accrued / Taken / Encashed / Closing, period + type +
 * department + employee filters, per-employee subtotals, grand total,
 * CSV export. Visible to `view_team` capability only (mgr / HR / admin
 * tiers) — employees keep their personal leave view.
 *
 * Data: GET /hris/api/leave-report/ (company-scoped via authedHrisFetch).
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import {
  ChevronLeft, Download, Filter, Loader2, RefreshCw, Search, Users,
  CalendarRange, Sigma, UploadCloud, FileSpreadsheet, CheckCircle2,
  AlertTriangle, ChevronDown, ChevronRight,
} from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { useHrisAccess, useHrisCan } from '@/hooks/useHrisAccess'
import { authedHrisFetch } from '../../_shared'

const LEAVE_TYPE_ORDER = [
  'annual', 'sick', 'maternity', 'paternity',
  'compassionate', 'study', 'special',
] as const

interface ReportRow {
  employee_id: string
  employee_name: string
  department: string
  leave_type: string
  leave_type_code: string
  opening_balance: number
  accrued: number | null
  accrued_from?: string | null
  accrued_from_known?: boolean
  accrued_note?: string | null
  entitlement_check?: string | null
  taken: number
  encashed: number
  closing_balance: number
}

interface ReportResponse {
  date_from: string
  date_to: string
  leave_year: number
  clamped: boolean
  count: number
  rows: ReportRow[]
  summary: {
    headcount_active: number
    total_days_taken: number
    avg_days_per_employee: number
  }
}

// Leave days, never rounded UP (EXCO change request, 2026-08-11).
//
// This is the FOURTH place the same figure was derived. The backend now floors
// every leave day to two decimals, and `toFixed(1)` here quietly rounded it
// straight back up on the way to the screen — 1.75 printed as "1.8" — so the
// server fix alone would have changed nothing on the page HR actually reads.
// toFixed ROUNDS; Math.floor is what "does not round up" means. Trailing zeros
// are trimmed so a clean 1.5 stays "1.5" and only real fractions grow a second
// decimal.
function fmt(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—'
  if (Number.isInteger(n)) return n.toFixed(0)
  // The server has ALREADY floored this. Re-deriving it here with
  // Math.floor(n * 100) / 100 is wrong on 573 of the 9,999 two-decimal
  // values under 100 — n*100 lands a hair under the integer and a
  // hundredth of a day is dropped, so 0.29 printed as "0.28" and the
  // screen disagreed with the API and the CSV. Only TRIM float noise.
  return String(parseFloat(n.toFixed(2)))
}

interface UploadResult {
  created?: number
  updated?: number
  errors?: { row: number; error: string }[]
  message?: string
  detail?: string
}

/** A single bulk-upload tile (file picker → POST → result), reused for both
 *  opening balances and approvers. The Chrome-MCP sandbox can't drive a file
 *  picker, so the e2e harness (setInputFiles) exercises this server-side. */
function UploadTile(props: {
  title: string
  blurb: string
  endpoint: string
  columns: string[]
  templateName: string
  statusLine?: string      // persistent last-upload status — survives refresh (bug ecfc3f5a)
  theme: any
}) {
  const { title, blurb, endpoint, columns, templateName, statusLine, theme } = props
  const inputRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<UploadResult | null>(null)
  const [err, setErr] = useState<string | null>(null)

  function downloadTemplate() {
    const csv = columns.join(',') + '\n'
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = templateName; a.click()
    URL.revokeObjectURL(url)
  }

  async function submit() {
    if (!file) return
    setBusy(true); setResult(null); setErr(null)
    try {
      const fd = new FormData()
      fd.append('file', file)
      const r = await authedHrisFetch(endpoint, { method: 'POST', body: fd })
      const body: UploadResult = await r.json().catch(() => ({}))
      if (!r.ok) { setErr(body.detail || `Upload failed (HTTP ${r.status})`); return }
      setResult(body)
      setFile(null)
      if (inputRef.current) inputRef.current.value = ''
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Network error')
    } finally {
      setBusy(false)
    }
  }

  const cardStyle = { background: theme.card, border: `1px solid ${theme.cardBdr}` }
  return (
    <div className="rounded-2xl p-4 space-y-3" style={cardStyle}>
      <div className="flex items-start gap-2">
        <FileSpreadsheet className="w-4 h-4 mt-0.5 shrink-0" style={{ color: theme.orange }} />
        <div>
          <h4 className="font-semibold text-sm" style={{ color: theme.text }}>{title}</h4>
          <p className="text-xs mt-0.5" style={{ color: theme.t2 }}>{blurb}</p>
        </div>
      </div>
      <div className="text-[11px] rounded-lg px-2.5 py-1.5" style={{ background: theme.g100, color: theme.t2 }}>
        Columns: <span className="font-mono">{columns.join(' · ')}</span>
      </div>
      {/* Persistent status from saved data — stays after a page refresh, unlike
          the transient "Done" banner below (bug ecfc3f5a). */}
      {statusLine && (
        <div className="flex items-center gap-1.5 text-[11px] rounded-lg px-2.5 py-1.5"
             style={{ background: theme.okB, color: theme.t2, border: `1px solid ${theme.cardBdr}` }}>
          <CheckCircle2 className="w-3.5 h-3.5 shrink-0" style={{ color: '#16a34a' }} /> {statusLine}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" onClick={downloadTemplate}
                className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-semibold"
                style={{ background: theme.g100, color: theme.text, border: `1px solid ${theme.cardBdr}` }}>
          <Download className="w-3.5 h-3.5" /> Template
        </button>
        {/* Bug 64dc4dcf: the bare native file input read as faint/unclear and the
            orange button sat disabled until a file was picked, so clicking "Upload"
            did nothing. Hide the native input; the orange button now OPENS the
            picker, then becomes the submit once a file is chosen. */}
        <input ref={inputRef} type="file" accept=".xlsx,.xlsm,.csv" className="hidden"
               onChange={e => { setFile(e.target.files?.[0] || null); setResult(null); setErr(null) }} />
        {file && (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-mono max-w-[180px] truncate"
                title={file.name}
                style={{ background: theme.g100, color: theme.text, border: `1px solid ${theme.cardBdr}` }}>
            <FileSpreadsheet className="w-3.5 h-3.5 shrink-0" style={{ color: theme.orange }} />
            <span className="truncate">{file.name}</span>
          </span>
        )}
        <button type="button" onClick={() => (file ? submit() : inputRef.current?.click())} disabled={busy}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold disabled:opacity-40"
                style={{ background: theme.orange, color: '#fff' }}>
          {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <UploadCloud className="w-3.5 h-3.5" />}
          {busy ? 'Uploading…' : file ? 'Upload' : 'Choose file…'}
        </button>
        {file && !busy && (
          <button type="button"
                  onClick={() => { setFile(null); if (inputRef.current) inputRef.current.value = ''; setResult(null); setErr(null) }}
                  className="text-xs px-2 py-1.5 rounded-lg" style={{ color: theme.t2 }}>
            Clear
          </button>
        )}
      </div>
      {err && (
        <div className="flex items-start gap-1.5 text-xs rounded-lg px-2.5 py-1.5"
             style={{ background: theme.erB, color: theme.er, border: `1px solid ${theme.er}40` }}>
          <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" /> {err}
        </div>
      )}
      {result && (
        <div className="space-y-1.5">
          <div className="flex items-center gap-1.5 text-xs font-semibold" style={{ color: '#16a34a' }}>
            <CheckCircle2 className="w-3.5 h-3.5" /> {result.message || 'Done.'}
          </div>
          {!!result.errors?.length && (
            <details className="text-[11px]" style={{ color: theme.t2 }}>
              <summary className="cursor-pointer">{result.errors.length} row(s) skipped — view</summary>
              <ul className="mt-1 space-y-0.5 max-h-40 overflow-y-auto">
                {result.errors.map((e, i) => (
                  <li key={i}>Row {e.row}: {e.error}</li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </div>
  )
}

export default function HrisLeaveReportPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const allowed = useHrisAccess()
  const canView = useHrisCan('view_team')
  const canManage = useHrisCan('manage_leave_admin')   // HR-tier bulk uploads

  // Persistent bulk-upload status so the tiles show state after a refresh,
  // not just a transient toast (bug ecfc3f5a).
  const [uploadStatus, setUploadStatus] = useState<{
    opening_balances?: { last_uploaded_at: string | null; by: string; employees_covered: number; rows: number }
    approvers?: { assigned: number; total: number }
  } | null>(null)

  useEffect(() => {
    if (!canManage) return
    authedHrisFetch('/hris/api/leave-uploads/status/')
      .then(async r => { if (r.ok) setUploadStatus(await r.json()) })
      .catch(() => { /* status line is non-critical */ })
  }, [canManage])

  const yearStart = `${new Date().getFullYear()}-01-01`
  const yearEnd = `${new Date().getFullYear()}-12-31`
  const [dateFrom, setDateFrom] = useState(yearStart)
  const [dateTo, setDateTo] = useState(yearEnd)
  const [types, setTypes] = useState<string[]>([])         // empty = All
  const [department, setDepartment] = useState('')          // '' = All
  const [employeeQ, setEmployeeQ] = useState('')            // client-side search
  const [data, setData] = useState<ReportResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showUpload, setShowUpload] = useState(false)

  useEffect(() => {
    if (allowed === false || canView === false) router.replace('/hris')
  }, [allowed, canView, router])

  function load() {
    setLoading(true); setError(null)
    const params = new URLSearchParams({ date_from: dateFrom, date_to: dateTo })
    if (types.length) params.set('leave_type', types.join(','))
    if (department) params.set('department', department)
    authedHrisFetch(`/hris/api/leave-report/?${params.toString()}`)
      .then(async r => {
        const body = await r.json().catch(() => ({}))
        if (!r.ok) { setError(body.detail || `HTTP ${r.status}`); return }
        setData(body as ReportResponse)
      })
      .catch(err => setError(err instanceof Error ? err.message : 'Network error'))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    if (allowed === true && canView === true) load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allowed, canView])

  // Department options come from the data itself (already role/company scoped).
  const departments = useMemo(() => {
    const s = new Set<string>()
    for (const r of data?.rows || []) if (r.department) s.add(r.department)
    return Array.from(s).sort()
  }, [data])

  const visibleRows = useMemo(() => {
    let rows = data?.rows || []
    // Leave-type filter applied CLIENT-SIDE so toggling a type button instantly
    // filters the table, subtotals and grand total — previously the type only
    // fed the server query and took effect on the next "Run Report", so clicking
    // a type appeared to do nothing (bug: filter buttons don't filter the report).
    if (types.length) {
      rows = rows.filter(r => types.includes(r.leave_type_code))
    }
    if (employeeQ.trim()) {
      const q = employeeQ.trim().toLowerCase()
      rows = rows.filter(r => r.employee_name.toLowerCase().includes(q))
    }
    return rows
  }, [data, employeeQ, types])

  // Group rows by employee for subtotal rendering.
  const grouped = useMemo(() => {
    const order: string[] = []
    const byEmp: Record<string, ReportRow[]> = {}
    for (const r of visibleRows) {
      if (!byEmp[r.employee_id]) { byEmp[r.employee_id] = []; order.push(r.employee_id) }
      byEmp[r.employee_id].push(r)
    }
    return order.map(id => ({ id, rows: byEmp[id] }))
  }, [visibleRows])

  const grand = useMemo(() => {
    let opening = 0, accrued = 0, taken = 0, encashed = 0, closing = 0
    for (const r of visibleRows) {
      opening += r.opening_balance
      accrued += r.accrued ?? 0
      taken += r.taken
      encashed += r.encashed ?? 0
      closing += r.closing_balance
    }
    return { opening, accrued, taken, encashed, closing }
  }, [visibleRows])

  function toggleType(code: string) {
    setTypes(t => t.includes(code) ? t.filter(c => c !== code) : [...t, code])
  }

  function downloadCsv() {
    if (!visibleRows.length || !data) return
    const rows: (string | number)[][] = [
      ['Employee', 'Department', 'Leave Type', 'Opening Balance', 'Accrued', 'Taken', 'Encashed', 'Closing Balance'],
      ...visibleRows.map(r => [
        r.employee_name, r.department, r.leave_type,
        r.opening_balance, r.accrued === null ? '—' : r.accrued,
        r.taken, r.encashed ?? 0, r.closing_balance,
      ]),
      [],
      ['GRAND TOTAL', '', '', fmt(grand.opening), fmt(grand.accrued), fmt(grand.taken), fmt(grand.encashed), fmt(grand.closing)],
    ]
    const csv = rows
      .map(r => r.map(c => {
        const s = String(c ?? '')
        return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
      }).join(','))
      .join('\n') + '\n'
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `Leave_Report_${data.date_from}_to_${data.date_to}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  if (allowed !== true || canView !== true) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ backgroundColor: theme.bg }}>
        <div className="w-8 h-8 border-2 border-[#F07F00] border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  const cardStyle = { background: theme.card, border: `1px solid ${theme.cardBdr}` }
  const inputStyle = { background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }
  const cell = 'px-3 py-2 text-sm tabular-nums'

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar title="Leave Report"
              breadcrumbs={[{ label: 'HRIS', href: '/hris' }, { label: 'Leave', href: '/hris/leave' }, { label: 'Report' }]} />
      <main className="flex-1 overflow-y-auto p-6 space-y-5">
        <Link href="/hris/leave" className="inline-flex items-center gap-1.5 text-sm font-medium" style={{ color: theme.t2 }}>
          <ChevronLeft className="w-4 h-4" /> Back to Leave
        </Link>

        {/* Filters */}
        <div className="rounded-2xl p-4 space-y-3" style={cardStyle}>
          <div className="flex items-center gap-2">
            <Filter className="w-4 h-4" style={{ color: theme.orange }} />
            <h3 className="font-semibold text-sm" style={{ color: theme.text }}>Filters</h3>
            {data?.clamped && (
              <span className="text-[11px]" style={{ color: theme.t2 }}>
                (period clamped to leave year {data.leave_year})
              </span>
            )}
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            <div>
              <label className="block text-xs font-medium mb-1" style={{ color: theme.t2 }}>From</label>
              <input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)}
                     className="w-full px-3 py-2 rounded-lg text-sm outline-none" style={inputStyle} />
            </div>
            <div>
              <label className="block text-xs font-medium mb-1" style={{ color: theme.t2 }}>To</label>
              <input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)}
                     className="w-full px-3 py-2 rounded-lg text-sm outline-none" style={inputStyle} />
            </div>
            <div>
              <label className="block text-xs font-medium mb-1" style={{ color: theme.t2 }}>Department</label>
              <select value={department} onChange={e => setDepartment(e.target.value)}
                      className="w-full px-3 py-2 rounded-lg text-sm outline-none" style={inputStyle}>
                <option value="">All departments</option>
                {departments.map(d => <option key={d} value={d}>{d}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium mb-1" style={{ color: theme.t2 }}>Employee</label>
              <div className="relative">
                <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2" style={{ color: theme.t2 }} />
                <input value={employeeQ} onChange={e => setEmployeeQ(e.target.value)} placeholder="Search name…"
                       className="w-full pl-8 pr-3 py-2 rounded-lg text-sm outline-none" style={inputStyle} />
              </div>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            <button type="button" onClick={() => setTypes([])}
                    className="px-2.5 py-1 rounded-md text-xs font-semibold"
                    style={{
                      background: types.length === 0 ? theme.orange : theme.g100,
                      color: types.length === 0 ? '#fff' : theme.t2,
                    }}>All</button>
            {LEAVE_TYPE_ORDER.map(code => (
              <button key={code} type="button" onClick={() => toggleType(code)}
                      className="px-2.5 py-1 rounded-md text-xs font-semibold capitalize"
                      style={{
                        background: types.includes(code) ? theme.orange : theme.g100,
                        color: types.includes(code) ? '#fff' : theme.t2,
                      }}>{code}</button>
            ))}
            <div className="flex-1" />
            <button type="button" onClick={load} disabled={loading}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold disabled:opacity-50"
                    style={{ background: theme.orange, color: '#fff' }}>
              {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
              Run report
            </button>
            <button type="button" onClick={downloadCsv} disabled={!visibleRows.length}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold disabled:opacity-40"
                    style={{ background: theme.g100, color: theme.text, border: `1px solid ${theme.cardBdr}` }}>
              <Download className="w-3.5 h-3.5" /> Export CSV
            </button>
          </div>
        </div>

        {/* HR-tier bulk uploads (bugs 9c0aa7d3 + 2cdd6333) */}
        {canManage && (
          <div className="rounded-2xl overflow-hidden" style={cardStyle}>
            <button type="button" onClick={() => setShowUpload(s => !s)}
                    className="w-full flex items-center gap-2 px-4 py-3 text-left">
              {showUpload ? <ChevronDown className="w-4 h-4" style={{ color: theme.orange }} />
                          : <ChevronRight className="w-4 h-4" style={{ color: theme.orange }} />}
              <UploadCloud className="w-4 h-4" style={{ color: theme.orange }} />
              <span className="font-semibold text-sm" style={{ color: theme.text }}>
                HR Tools — Bulk leave upload
              </span>
              <span className="text-[11px]" style={{ color: theme.t2 }}>
                set opening balances as at a date · assign leave approvers
              </span>
            </button>
            {showUpload && (
              <div className="px-4 pb-4 grid grid-cols-1 lg:grid-cols-2 gap-3">
                <UploadTile
                  theme={theme}
                  title="Leave opening balances (as at a date)"
                  blurb="Set each employee's correct entitlement / opening balance / accrued days as at a cut-off date. From that date the system tracks leave taken automatically. Existing balances stay unchanged for anyone not in the file."
                  endpoint="/hris/api/leave-opening-balances/upload/"
                  columns={['employee', 'leave_type', 'as_at_date', 'entitlement', 'opening_balance', 'accrued']}
                  templateName="leave_opening_balances_template.csv"
                  statusLine={uploadStatus?.opening_balances?.last_uploaded_at
                    ? `Last uploaded ${new Date(uploadStatus.opening_balances.last_uploaded_at).toLocaleDateString('en-GB')}`
                      + `${uploadStatus.opening_balances.by ? ` by ${uploadStatus.opening_balances.by}` : ''}`
                      + ` · ${uploadStatus.opening_balances.employees_covered} staff on file`
                    : undefined}
                />
                <UploadTile
                  theme={theme}
                  title="Leave approvers (reports-to)"
                  blurb="Set who approves each employee's leave. The approver receives the leave requests in their inbox."
                  endpoint="/hris/api/leave-approvers/upload/"
                  columns={['employee', 'approver']}
                  templateName="leave_approvers_template.csv"
                  statusLine={uploadStatus?.approvers && uploadStatus.approvers.total
                    ? `${uploadStatus.approvers.assigned} of ${uploadStatus.approvers.total} staff have an approver assigned`
                    : undefined}
                />
              </div>
            )}
          </div>
        )}

        {error && (
          <div className="rounded-lg px-3 py-2 text-sm" style={{ background: theme.erB, color: theme.er, border: `1px solid ${theme.er}40` }}>
            {error}
          </div>
        )}

        {/* Summary cards */}
        {data && (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            {[
              { icon: Users, label: 'Employees with leave activity', value: fmt(data.summary.headcount_active) },
              { icon: CalendarRange, label: 'Total days taken in period', value: fmt(data.summary.total_days_taken) },
              { icon: Sigma, label: 'Average days per employee', value: fmt(data.summary.avg_days_per_employee) },
            ].map(({ icon: Icon, label, value }) => (
              <div key={label} className="rounded-2xl p-4" style={cardStyle}>
                <div className="flex items-center gap-2 text-xs" style={{ color: theme.t2 }}>
                  <Icon className="w-3.5 h-3.5" style={{ color: theme.orange }} /> {label}
                </div>
                <div className="mt-1 text-2xl font-bold tabular-nums" style={{ color: theme.text }}>{value}</div>
              </div>
            ))}
          </div>
        )}

        {/* Report table */}
        <div className="rounded-2xl overflow-hidden" style={cardStyle}>
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead>
                <tr style={{ borderBottom: `1px solid ${theme.cardBdr}` }}>
                  {['Employee', 'Department', 'Leave Type', 'Opening', 'Accrued', 'Taken', 'Encashed', 'Closing'].map((h, i) => (
                    <th key={h} className={`px-3 py-2.5 text-[11px] uppercase tracking-wider font-semibold ${i >= 3 ? 'text-right' : ''}`}
                        style={{ color: theme.t2 }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {loading && (
                  <tr><td colSpan={8} className="px-3 py-8 text-center text-sm" style={{ color: theme.t2 }}>
                    <Loader2 className="w-4 h-4 inline animate-spin mr-1" /> Building report…
                  </td></tr>
                )}
                {!loading && grouped.length === 0 && (
                  <tr><td colSpan={8} className="px-3 py-8 text-center text-sm" style={{ color: theme.t2 }}>
                    No rows for the selected filters.
                  </td></tr>
                )}
                {!loading && grouped.map(g => {
                  const sub = g.rows.reduce(
                    (acc, r) => ({
                      opening: acc.opening + r.opening_balance,
                      accrued: acc.accrued + (r.accrued ?? 0),
                      taken: acc.taken + r.taken,
                      encashed: acc.encashed + (r.encashed ?? 0),
                      closing: acc.closing + r.closing_balance,
                    }),
                    { opening: 0, accrued: 0, taken: 0, encashed: 0, closing: 0 })
                  return (
                    <>
                      {g.rows.map((r, i) => (
                        <tr key={`${g.id}-${r.leave_type_code}`} style={{ borderTop: `1px solid ${theme.cardBdr}55` }}>
                          <td className={cell} style={{ color: theme.text }}>{i === 0 ? r.employee_name : ''}</td>
                          <td className={cell} style={{ color: theme.t2 }}>{i === 0 ? (r.department || '—') : ''}</td>
                          <td className={cell}>
                            <span className="px-1.5 py-0.5 rounded text-[10px] uppercase tracking-wide font-semibold"
                                  style={{ background: theme.orange + '22', color: theme.orange }}>
                              {r.leave_type_code}
                            </span>
                          </td>
                          <td className={`${cell} text-right`} style={{ color: theme.text }}>{fmt(r.opening_balance)}</td>
                          <td className={`${cell} text-right`} style={{ color: theme.text }}>
                            {fmt(r.accrued)}
                            {/* Bug b013d4ec: 3.5 and 14.0 read as a discrepancy when they are
                                simply 2 months and 8 months of accrual. Each row now says the
                                date it counts from, so the column can be compared at all. */}
                            {r.accrued_note && (
                              <div className="text-[10px] font-normal" style={{ color: theme.t2 }}>
                                {r.accrued_note}
                              </div>
                            )}
                            {/* The uploaded entitlement on this row is not a
                                Conditions of Service figure, so the monthly rate
                                derived from it cannot be relied on. Said on the
                                row, in orange, because a silently wrong rate is
                                what sent this back for correction twice. */}
                            {r.entitlement_check && (
                              <div className="text-[10px] font-semibold" style={{ color: theme.orange }}>
                                ⚠ {r.entitlement_check}
                              </div>
                            )}
                            {r.accrued_from && (
                              <div className="text-[10px] font-normal" style={{ color: theme.t2 }}>
                                since {new Date(r.accrued_from).toLocaleDateString('en-GB',
                                  { day: '2-digit', month: 'short' })}
                                {r.accrued_from_known === false && (
                                  <span title="No joining date on record — this is the period default, not this employee's actual start.">
                                    {' '}(assumed)
                                  </span>
                                )}
                              </div>
                            )}
                          </td>
                          <td className={`${cell} text-right`} style={{ color: r.taken > 0 ? theme.orange : theme.t2 }}>{fmt(r.taken)}</td>
                          {/* Days cashed out. They leave the balance when the application
                              is raised and stay out through payment — only a rejection puts
                              them back. Bug 0329c5a0: this report ignored them
                              entirely, over-stating five employees by 53 days. */}
                          <td className={`${cell} text-right`}
                              style={{ color: (r.encashed ?? 0) > 0 ? theme.orange : theme.t2 }}>{fmt(r.encashed ?? 0)}</td>
                          <td className={`${cell} text-right font-semibold`}
                              style={{ color: r.closing_balance < 0 ? theme.er : theme.text }}>{fmt(r.closing_balance)}</td>
                        </tr>
                      ))}
                      <tr key={`${g.id}-subtotal`} style={{ background: theme.g100 }}>
                        <td className={cell} colSpan={3}>
                          <span className="text-xs font-semibold" style={{ color: theme.t2 }}>
                            Subtotal — {g.rows[0].employee_name}
                          </span>
                        </td>
                        <td className={`${cell} text-right text-xs font-semibold`} style={{ color: theme.t2 }}>{fmt(sub.opening)}</td>
                        <td className={`${cell} text-right text-xs font-semibold`} style={{ color: theme.t2 }}>{fmt(sub.accrued)}</td>
                        <td className={`${cell} text-right text-xs font-semibold`} style={{ color: theme.t2 }}>{fmt(sub.taken)}</td>
                        <td className={`${cell} text-right text-xs font-semibold`} style={{ color: theme.t2 }}>{fmt(sub.encashed)}</td>
                        <td className={`${cell} text-right text-xs font-semibold`} style={{ color: theme.t2 }}>{fmt(sub.closing)}</td>
                      </tr>
                    </>
                  )
                })}
                {!loading && grouped.length > 0 && (
                  <tr style={{ borderTop: `2px solid ${theme.orange}`, background: theme.oL }}>
                    <td className={cell} colSpan={3}>
                      <span className="text-xs font-bold uppercase tracking-wide" style={{ color: theme.orange }}>Grand total</span>
                    </td>
                    <td className={`${cell} text-right font-bold`} style={{ color: theme.text }}>{fmt(grand.opening)}</td>
                    <td className={`${cell} text-right font-bold`} style={{ color: theme.text }}>{fmt(grand.accrued)}</td>
                    <td className={`${cell} text-right font-bold`} style={{ color: theme.text }}>{fmt(grand.taken)}</td>
                    <td className={`${cell} text-right font-bold`} style={{ color: theme.text }}>{fmt(grand.encashed)}</td>
                    <td className={`${cell} text-right font-bold`} style={{ color: theme.text }}>{fmt(grand.closing)}</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        <p className="text-[11px]" style={{ color: theme.t2 }}>
          Opening + Accrued − Taken − Encashed = Closing · Taken counts approved requests only · Encashed is leave cashed out — held from application through payment, released only on rejection ·
          Annual accrues monthly per CoS §7.5.1; flat-entitlement types show “—” under Accrued · Accrued is counted from the date shown beneath it — an uploaded opening balance restarts it from that date, so two people can hold different accruals for the same tenure ·
          Leave year {data?.leave_year ?? new Date().getFullYear()}.
        </p>
      </main>
    </div>
  )
}
