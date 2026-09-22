'use client'

/**
 * /hris/incentives — Staff Incentive Approval (CFO directive 2026-07-13).
 *
 * Managers submit a period's incentive list (mirrors the real email
 * requests: name/category + amount). The CFO signs it off (single control,
 * CFO directive 2026-07-15 — the second control is at the bank: Pako loads the
 * payment in FNB and Unami reviews it there). Unami's HR sign-off is optional
 * and recorded if given. Finance (Pako / Kago) then mark it processed into
 * payroll.
 *
 * NOTE: deliberately no useHrisAccess redirect — the makers are line
 * managers who are not HRIS-whitelist members. The backend gates every
 * action; a plain employee just sees the "not available" notice.
 */
import { useCallback, useEffect, useState, type ChangeEvent } from 'react'
import Link from 'next/link'
import { ChevronLeft, Coins, Download, Pencil, Plus, Power, Repeat, Trash2, Upload } from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { authedHrisFetch } from '../_shared'
interface Line {
  id?: string; name: string; basis: string; amount: string
  employee_id?: string
  beyond_normal_duties?: boolean; on_time?: boolean; error_free?: boolean
  needed_manager_fix?: boolean; justification?: string
}
interface EmpOption { id: string; name: string; department: string }
interface Signature { signed: boolean; at: string | null }
interface IncentiveReq {
  id: string
  title: string
  period: string
  department: string
  notes: string
  status: string
  maker_email: string
  is_own: boolean
  created_at: string | null
  total: string
  signatures: { cfo: Signature; hr: Signature }
  rejected: { at: string | null; notes: string } | null
  payroll: { processed: boolean; at: string | null }
  is_recurring?: boolean
  amended?: { at: string; by: string } | null
  lines: Line[]
}
interface Me { can_submit: boolean; can_approve: boolean; approver_slot: string; is_finance: boolean }
interface RecTemplate {
  id: string; name: string; category: string; basis: string; amount: string
  department: string; justification: string; note: string; active: boolean
  created_by_email: string; created_at: string | null
}

const STATUS_CHIP: Record<string, string> = {
  pending: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
  approved: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300',
  rejected: 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300',
}

const fmtP = (v: string | number) =>
  `P${new Intl.NumberFormat('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Number(v) || 0)}`

const emptyLine = (): Line => ({
  name: '', basis: '', amount: '',
  beyond_normal_duties: false, on_time: false, error_free: false,
  needed_manager_fix: false, justification: '',
})

const MIN_JUSTIFICATION_WORDS = 50
const wc = (s: string) => s.trim().split(/\s+/).filter(Boolean).length

export default function IncentivesPage() {
  const [me, setMe] = useState<Me | null>(null)
  const [requests, setRequests] = useState<IncentiveReq[]>([])
  const [loading, setLoading] = useState(true)
  const [blocked, setBlocked] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')
  const [showForm, setShowForm] = useState(false)

  // form state
  const [title, setTitle] = useState('')
  const [period, setPeriod] = useState('')
  const [department, setDepartment] = useState('')
  const [notes, setNotes] = useState('')
  const [lines, setLines] = useState<Line[]>([emptyLine()])
  const [formErr, setFormErr] = useState('')
  // Manager declaration (CFO 2026-08-18) — must be ticked to submit.
  const [managerAttested, setManagerAttested] = useState(false)
  // People the manager can pick, so each line links to a real employee and the
  // discipline gate can check THAT person's overdue tasks (CFO 2026-08-18).
  const [employees, setEmployees] = useState<EmpOption[]>([])
  // Who (if anyone) was held out for overdue tasks on the last submit.
  const [heldNotice, setHeldNotice] = useState('')
  // Upload-a-list (CFO / Bharath 2026-08-24): parse a spreadsheet into the rows.
  const [uploadErr, setUploadErr] = useState('')
  const [uploadMsg, setUploadMsg] = useState('')

  // recurring templates
  const [templates, setTemplates] = useState<RecTemplate[]>([])
  const [recCanManage, setRecCanManage] = useState(false)
  const [curPeriod, setCurPeriod] = useState('')
  const [showRec, setShowRec] = useState(false)
  const [rec, setRec] = useState({ name: '', category: '', amount: '', basis: '', department: '', justification: '', note: '' })
  const [recErr, setRecErr] = useState('')
  const [recMsg, setRecMsg] = useState('')

  // amend state (keyed by request id -> { line id -> amount string })
  const [amendId, setAmendId] = useState('')
  const [amendAmounts, setAmendAmounts] = useState<Record<string, string>>({})
  const [amendReason, setAmendReason] = useState('')
  const [amendErr, setAmendErr] = useState('')

  const load = useCallback(() => {
    authedHrisFetch('/hris/api/incentives/')
      .then(async r => {
        if (r.status === 403) { setBlocked(true); return null }
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then(d => {
        if (!d) return
        setMe(d.me)
        setRequests(d.requests || [])
        setError('')
        // Load the pickable employee list once we know the viewer can submit.
        if (d.me?.can_submit && employees.length === 0) {
          authedHrisFetch('/hris/api/incentives/employees/')
            .then(r => (r.ok ? r.json() : { employees: [] }))
            .then(e => setEmployees(e.employees || []))
            .catch(() => {})
        }
      })
      .catch(e => setError(String(e?.message || e)))
      .finally(() => setLoading(false))
  }, [])

  const loadRec = useCallback(() => {
    authedHrisFetch('/hris/api/incentives/recurring/')
      .then(async r => (r.ok ? r.json() : null))
      .then(d => {
        if (!d) return
        setTemplates(d.templates || [])
        setRecCanManage(!!d.can_manage)
        setCurPeriod(d.current_period || '')
      })
      .catch(() => { /* recurring is a bonus panel — never break the page */ })
  }, [])

  useEffect(() => { load(); loadRec() }, [load, loadRec])

  const setLine = (i: number, patch: Partial<Line>) =>
    setLines(ls => ls.map((l, j) => (j === i ? { ...l, ...patch } : l)))

  const formTotal = lines.reduce((s, l) => s + (Number(String(l.amount).replace(/,/g, '')) || 0), 0)

  const lineQualifies = (l: Line) =>
    !!l.name.trim() &&
    (Number(String(l.amount).replace(/,/g, '')) || 0) > 0 &&
    !!l.beyond_normal_duties && !!l.on_time && !!l.error_free &&
    !l.needed_manager_fix && wc(l.justification || '') >= MIN_JUSTIFICATION_WORDS
  const namedLines = lines.filter(l => l.name.trim())
  const allQualify = namedLines.length > 0 && namedLines.every(lineQualifies)

  // Why the Submit button is still greyed out, in plain words. It used to be
  // silently disabled with only a hover tooltip, so a manager who had filled in
  // a name and an amount clicked a dead button and concluded the system would
  // not let them submit (Unami / Sechele 2026-07-30).
  const blockers = (): string[] => {
    const out: string[] = []
    if (!title.trim()) out.push('Give the request a title.')
    if (!period.trim()) out.push('Pick the month the incentive belongs to.')
    if (namedLines.length === 0) out.push('Add at least one person and their amount.')
    namedLines.forEach(l => {
      const who = l.name.trim()
      if ((Number(String(l.amount).replace(/,/g, '')) || 0) <= 0)
        out.push(`${who}: enter an amount greater than zero.`)
      if (!l.beyond_normal_duties)
        out.push(`${who}: answer Yes to “beyond their normal day-to-day duties”.`)
      if (!l.on_time) out.push(`${who}: answer Yes to “delivered on time”.`)
      if (!l.error_free) out.push(`${who}: answer Yes to “delivered error-free”.`)
      if (l.needed_manager_fix)
        out.push(`${who}: you answered that you had to fix the work — that does not qualify for an incentive.`)
      const words = wc(l.justification || '')
      if (words < MIN_JUSTIFICATION_WORDS)
        out.push(`${who}: the reason needs at least ${MIN_JUSTIFICATION_WORDS} words (you have ${words}).`)
    })
    if (!managerAttested)
      out.push('Tick the declaration that you have checked this employee’s attendance and leave.')
    return out
  }
  const canSubmit = allQualify && managerAttested
  const outstanding = canSubmit && title.trim() && period.trim() ? [] : blockers()

  const submit = async () => {
    setFormErr('')
    setBusy('submit')
    try {
      const r = await authedHrisFetch('/hris/api/incentives/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title, period, department, notes,
          manager_attested: managerAttested,
          lines: lines.filter(l => l.name.trim()),
        }),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) { setFormErr(d.detail || `HTTP ${r.status}`); return }
      // Some people can be held out for overdue tasks while the rest go through.
      setHeldNotice(d.held_notice || '')
      setTitle(''); setPeriod(''); setDepartment(''); setNotes('')
      setLines([emptyLine()]); setManagerAttested(false); setShowForm(false)
      setUploadErr(''); setUploadMsg('')
      load()
    } finally {
      setBusy('')
    }
  }

  // ---- Upload a list (parse a spreadsheet into the form) -------------------
  // The upload is a TYPING SHORTCUT only — it fills the same rows a manager
  // types by hand. Every qualifying question + the 50-word justification + the
  // declaration + the server gate still apply before anything can be submitted.
  const applyUploaded = (rows: Partial<Line>[]) => {
    const mapped: Line[] = rows.map(r => {
      const name = String(r.name || '').trim()
      const match = employees.find(x => x.name === name)
      return {
        name,
        amount: String(r.amount ?? ''),
        basis: String(r.basis || ''),
        employee_id: match?.id || '',
        beyond_normal_duties: !!r.beyond_normal_duties,
        on_time: !!r.on_time,
        error_free: !!r.error_free,
        needed_manager_fix: !!r.needed_manager_fix,
        justification: String(r.justification || ''),
      }
    })
    // Replace a blank starter form; otherwise append to what's already there.
    setLines(prev => {
      const hasContent = prev.some(l => l.name.trim() || String(l.amount).trim())
      return hasContent ? [...prev, ...mapped] : mapped
    })
  }

  const onUploadFile = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    e.target.value = ''   // let the same file be re-picked after a fix
    if (!file) return
    setUploadErr(''); setUploadMsg(''); setBusy('upload')
    try {
      const fd = new FormData()
      fd.append('file', file)
      const r = await authedHrisFetch('/hris/api/incentives/parse-upload/', { method: 'POST', body: fd })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) { setUploadErr(d.detail || `Upload failed (HTTP ${r.status})`); return }
      applyUploaded(d.lines || [])
      const n = d.count ?? (d.lines?.length ?? 0)
      setUploadMsg(`Loaded ${n} ${n === 1 ? 'person' : 'people'} from the file. Check each row, fill anything missing, tick the declaration, then Submit.`)
    } finally {
      setBusy('')
    }
  }

  const downloadTemplate = () => {
    const header = ['Name', 'Amount', 'Basis', 'Beyond normal duties (Yes/No)', 'On time (Yes/No)', 'Error free (Yes/No)', 'Needed manager fix (Yes/No)', 'Justification (50+ words)']
    const example = ['Full name (must match the staff list)', '1200', 'e.g. Motor claims incentive', 'Yes', 'Yes', 'Yes', 'No', 'In at least 50 words, say exactly what this person did that went beyond their normal day-to-day duties — this is what the CFO and HR sign off.']
    const csv = [header, example]
      .map(row => row.map(c => `"${String(c).replace(/"/g, '""')}"`).join(','))
      .join('\r\n')
    const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = 'incentive-list-template.csv'
    document.body.appendChild(a); a.click(); document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }

  const action = async (id: string, path: string, body: object = {}) => {
    setBusy(id + path)
    try {
      const r = await authedHrisFetch(`/hris/api/incentives/${id}/${path}/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        alert(d.detail || `Action failed (HTTP ${r.status})`)
      }
      load()
    } finally {
      setBusy('')
    }
  }

  const rejectWithReason = (id: string) => {
    const notesIn = window.prompt('Reason for rejection (optional):') ?? null
    if (notesIn === null) return
    action(id, 'reject', { notes: notesIn })
  }

  // ---- Amend a still-pending request (correct a fat-fingered amount) -------
  const openAmend = (r: IncentiveReq) => {
    setAmendErr('')
    setAmendReason('')
    setAmendAmounts(Object.fromEntries(r.lines.map(l => [l.id || '', l.amount])))
    setAmendId(id => (id === r.id ? '' : r.id))
  }

  const saveAmend = async (r: IncentiveReq) => {
    setAmendErr('')
    setBusy('amend' + r.id)
    try {
      const body: Record<string, unknown> = { reason: amendReason }
      if (r.lines.length === 1) {
        body.amount = amendAmounts[r.lines[0].id || '']
      } else {
        body.lines = r.lines.map(l => ({ id: l.id, amount: amendAmounts[l.id || ''] }))
      }
      const resp = await authedHrisFetch(`/hris/api/incentives/${r.id}/amend/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      const d = await resp.json().catch(() => ({}))
      if (!resp.ok) { setAmendErr(d.detail || `HTTP ${resp.status}`); return }
      setAmendId('')
      load()
    } finally {
      setBusy('')
    }
  }

  // ---- Recurring incentive templates ---------------------------------------
  const createTemplate = async () => {
    setRecErr(''); setRecMsg('')
    setBusy('rec-create')
    try {
      const resp = await authedHrisFetch('/hris/api/incentives/recurring/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(rec),
      })
      const d = await resp.json().catch(() => ({}))
      if (!resp.ok) { setRecErr(d.detail || `HTTP ${resp.status}`); return }
      setRec({ name: '', category: '', amount: '', basis: '', department: '', justification: '', note: '' })
      loadRec()
    } finally {
      setBusy('')
    }
  }

  const setTemplateActive = async (id: string, active: boolean) => {
    setBusy('rec-' + id)
    try {
      await authedHrisFetch(`/hris/api/incentives/recurring/${id}/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ active }),
      })
      loadRec()
    } finally {
      setBusy('')
    }
  }

  const generateRecurring = async () => {
    setRecErr(''); setRecMsg('')
    setBusy('rec-generate')
    try {
      const resp = await authedHrisFetch('/hris/api/incentives/recurring/generate/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      })
      const d = await resp.json().catch(() => ({}))
      if (!resp.ok) { setRecErr(d.detail || `HTTP ${resp.status}`); return }
      setRecMsg(`${d.period}: ${d.created_count} created, ${d.skipped_count} already existed.`)
      load()
    } finally {
      setBusy('')
    }
  }

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-950">
      <TopBar />
      <main className="mx-auto max-w-5xl px-4 py-6">
        {heldNotice && (
          <div className="mb-3 flex items-start justify-between gap-3 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/50 dark:text-amber-200">
            <span>{heldNotice}</span>
            <button type="button" onClick={() => setHeldNotice('')} className="shrink-0 font-semibold">×</button>
          </div>
        )}
        <div className="mb-2 flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <Link
              href="/hris"
              className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-gray-800 dark:hover:text-gray-200"
            >
              <ChevronLeft className="h-4 w-4" /> HRIS
            </Link>
            <h1 className="flex items-center gap-2 text-xl font-semibold text-gray-900 dark:text-gray-100">
              <Coins className="h-5 w-5 text-amber-500" /> Staff Incentives
            </h1>
          </div>
          <div className="flex items-center gap-2">
            {(recCanManage || templates.length > 0) && (
              <button
                type="button"
                onClick={() => setShowRec(s => !s)}
                className="inline-flex items-center gap-1 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm font-medium text-amber-800 hover:bg-amber-100 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200"
              >
                <Repeat className="h-4 w-4" /> Recurring
              </button>
            )}
            {me?.can_submit && (
              <button
                type="button"
                onClick={() => setShowForm(s => !s)}
                className="inline-flex items-center gap-1 rounded-lg bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-700"
              >
                <Plus className="h-4 w-4" /> New request
              </button>
            )}
          </div>
        </div>
        <p className="mb-6 text-sm text-gray-500 dark:text-gray-400">
          Managers request; the CFO signs off; Finance considers it in payroll.
          The second control is at the bank — Pako loads the payment in FNB and
          Unami reviews it there. Every step is recorded.
        </p>

        {showRec && (
          <div className="mb-6 rounded-xl border border-amber-200 bg-white p-5 shadow-sm dark:border-amber-900 dark:bg-gray-900">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="flex items-center gap-2 text-base font-semibold text-gray-900 dark:text-gray-100">
                <Repeat className="h-4 w-4 text-amber-500" /> Recurring incentives
              </h2>
              {recCanManage && (
                <button
                  type="button"
                  disabled={busy === 'rec-generate'}
                  onClick={generateRecurring}
                  className="rounded-lg bg-amber-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-amber-700 disabled:opacity-50"
                >
                  Generate {curPeriod ? `${curPeriod} ` : "this month's "}incentives
                </button>
              )}
            </div>
            <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
              Set a staff member&apos;s monthly incentive once. &ldquo;Generate&rdquo; creates this
              period&apos;s requests from the active templates below (once per template — safe to
              re-run), then review and approve them normally.
            </p>
            {recMsg && (
              <div className="mt-3 rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300">
                {recMsg}
              </div>
            )}

            {templates.length > 0 && (
              <div className="mt-4 overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs uppercase text-gray-400">
                      <th className="py-1.5 pr-3 font-medium">Name / category</th>
                      <th className="py-1.5 pr-3 font-medium">Basis</th>
                      <th className="py-1.5 pr-3 text-right font-medium">Amount</th>
                      <th className="py-1.5 font-medium">Status</th>
                      {recCanManage && <th className="py-1.5" />}
                    </tr>
                  </thead>
                  <tbody>
                    {templates.map(t => (
                      <tr key={t.id} className="border-t border-gray-100 dark:border-gray-800">
                        <td className="py-1.5 pr-3 text-gray-800 dark:text-gray-200">
                          {t.name}
                          {t.category && <span className="ml-1 text-xs text-gray-400">· {t.category}</span>}
                        </td>
                        <td className="py-1.5 pr-3 text-gray-500">{t.basis || '—'}</td>
                        <td className="py-1.5 pr-3 text-right font-medium text-gray-900 dark:text-gray-100">{fmtP(t.amount)}</td>
                        <td className="py-1.5">
                          <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${t.active ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300' : 'bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400'}`}>
                            {t.active ? 'Active' : 'Inactive'}
                          </span>
                        </td>
                        {recCanManage && (
                          <td className="py-1.5 text-right">
                            <button
                              type="button"
                              disabled={busy === 'rec-' + t.id}
                              onClick={() => setTemplateActive(t.id, !t.active)}
                              className="inline-flex items-center gap-1 rounded-lg border border-gray-200 px-2 py-1 text-xs text-gray-600 hover:bg-gray-50 disabled:opacity-50 dark:border-gray-700 dark:text-gray-300"
                              title={t.active ? 'Deactivate' : 'Reactivate'}
                            >
                              <Power className="h-3.5 w-3.5" />
                              {t.active ? 'Deactivate' : 'Reactivate'}
                            </button>
                          </td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {recCanManage && (
              <div className="mt-4 rounded-lg border border-gray-200 p-3 dark:border-gray-700">
                <div className="grid gap-2 sm:grid-cols-3">
                  <input value={rec.name} onChange={e => setRec(s => ({ ...s, name: e.target.value }))}
                    placeholder="Person or category"
                    className="rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-950 dark:text-gray-100 sm:col-span-2" />
                  <input value={rec.amount} onChange={e => setRec(s => ({ ...s, amount: e.target.value }))}
                    placeholder="Amount (BWP)" inputMode="decimal"
                    className="rounded-lg border border-gray-200 px-3 py-2 text-right text-sm dark:border-gray-700 dark:bg-gray-950 dark:text-gray-100" />
                  <input value={rec.category} onChange={e => setRec(s => ({ ...s, category: e.target.value }))}
                    placeholder="Category (optional)"
                    className="rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-950 dark:text-gray-100" />
                  <input value={rec.basis} onChange={e => setRec(s => ({ ...s, basis: e.target.value }))}
                    placeholder="Basis (optional)"
                    className="rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-950 dark:text-gray-100" />
                  <input value={rec.department} onChange={e => setRec(s => ({ ...s, department: e.target.value }))}
                    placeholder="Department (optional)"
                    className="rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-950 dark:text-gray-100" />
                </div>
                <textarea value={rec.justification} onChange={e => setRec(s => ({ ...s, justification: e.target.value }))}
                  rows={2} placeholder="Standing reason (optional — carried onto each generated request)"
                  className="mt-2 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-950 dark:text-gray-100" />
                {recErr && (
                  <div className="mt-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950 dark:text-red-300">{recErr}</div>
                )}
                <button
                  type="button"
                  disabled={busy === 'rec-create'}
                  onClick={createTemplate}
                  className="mt-3 inline-flex items-center gap-1 rounded-lg bg-amber-600 px-3 py-2 text-sm font-medium text-white hover:bg-amber-700 disabled:opacity-50"
                >
                  <Plus className="h-4 w-4" /> Add recurring incentive
                </button>
              </div>
            )}
          </div>
        )}

        {loading && (
          <div className="rounded-xl border border-gray-200 bg-white p-6 text-sm text-gray-500 dark:border-gray-800 dark:bg-gray-900">
            Loading…
          </div>
        )}
        {blocked && (
          <div className="rounded-xl border border-amber-200 bg-amber-50 p-6 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200">
            Incentive requests are available to managers, approvers and
            Finance. Your account does not have access.
          </div>
        )}
        {!loading && !blocked && error && (
          <div className="rounded-xl border border-red-200 bg-red-50 p-6 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
            Could not load incentive requests ({error}).
          </div>
        )}

        {showForm && me?.can_submit && (
          <div className="mb-6 rounded-xl border border-blue-200 bg-white p-5 shadow-sm dark:border-blue-900 dark:bg-gray-900">
            <h2 className="mb-3 text-base font-semibold text-gray-900 dark:text-gray-100">
              New incentive request
            </h2>
            {/* Pickable employees — choosing one links the line to a real person
                so the discipline gate can check their overdue tasks. Free text is
                still allowed for a category line (no person to check). */}
            <datalist id="incentive-employee-options">
              {employees.map(e => (
                <option key={e.id} value={e.name}>{e.department}</option>
              ))}
            </datalist>
            <div className="grid gap-3 sm:grid-cols-3">
              <input
                value={title}
                onChange={e => setTitle(e.target.value)}
                placeholder="Title, e.g. Motor claims incentives"
                className="rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-950 dark:text-gray-100 sm:col-span-2"
              />
              <input
                type="month"
                value={period}
                onChange={e => setPeriod(e.target.value)}
                className="rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-950 dark:text-gray-100"
              />
              <input
                value={department}
                onChange={e => setDepartment(e.target.value)}
                placeholder="Department (optional)"
                className="rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-950 dark:text-gray-100"
              />
              <input
                value={notes}
                onChange={e => setNotes(e.target.value)}
                placeholder="Notes (optional)"
                className="rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-950 dark:text-gray-100 sm:col-span-2"
              />
            </div>

            <div className="mt-4 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/50 dark:text-amber-200">
              <strong>Incentives are earned, not automatic.</strong> They are only for
              work <strong>beyond</strong> an employee’s normal day-to-day duties.
              For each person you must confirm it went beyond their role, was on time
              and error-free, did not need you to fix it, and say why. Routine work
              does not qualify.
            </div>

            {/* Upload a list — a typing shortcut for a long list (CFO / Bharath
                2026-08-24). It fills the rows below; you still review each one,
                tick the declaration, and Submit runs the same checks. */}
            <div className="mt-4 flex flex-wrap items-center gap-3 rounded-lg border border-gray-200 bg-gray-50 px-4 py-3 dark:border-gray-700 dark:bg-gray-950/40">
              <label className="inline-flex cursor-pointer items-center gap-1 rounded-lg border border-blue-300 bg-white px-3 py-2 text-sm font-medium text-blue-700 hover:bg-blue-50 dark:border-blue-800 dark:bg-gray-900 dark:text-blue-300 dark:hover:bg-gray-800">
                <Upload className="h-4 w-4" />
                {busy === 'upload' ? 'Reading…' : 'Upload a list'}
                <input
                  type="file"
                  accept=".xlsx,.xlsm,.xlsb,.xls,.ods,.csv"
                  onChange={onUploadFile}
                  disabled={busy === 'upload'}
                  className="hidden"
                />
              </label>
              <button
                type="button"
                onClick={downloadTemplate}
                className="inline-flex items-center gap-1 text-sm font-medium text-gray-600 hover:text-gray-900 dark:text-gray-300 dark:hover:text-gray-100"
              >
                <Download className="h-4 w-4" /> Download template
              </button>
              <span className="text-xs text-gray-500 dark:text-gray-400">
                Upload a spreadsheet of people + amounts instead of typing each row. Excel or CSV.
              </span>
            </div>
            {uploadErr && (
              <div className="mt-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950 dark:text-red-300">
                {uploadErr}
              </div>
            )}
            {uploadMsg && (
              <div className="mt-2 rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300">
                {uploadMsg}
              </div>
            )}

            <div className="mt-4 space-y-4">
              {lines.map((l, i) => {
                const words = wc(l.justification || '')
                const questions: { key: keyof Line; q: string; good: boolean }[] = [
                  { key: 'beyond_normal_duties', q: 'Beyond their normal day-to-day job? (routine work earns no incentive)', good: true },
                  { key: 'on_time', q: 'Delivered on time?', good: true },
                  { key: 'error_free', q: 'Delivered error-free?', good: true },
                  { key: 'needed_manager_fix', q: 'Did you (the manager) spend a lot of time fixing it?', good: false },
                ]
                return (
                <div key={i} className="rounded-lg border border-gray-200 p-3 dark:border-gray-700">
                  <div className="flex flex-wrap items-center gap-2">
                    <input
                      value={l.name}
                      list="incentive-employee-options"
                      onChange={e => {
                        const v = e.target.value
                        const match = employees.find(x => x.name === v)
                        setLine(i, { name: v, employee_id: match?.id || '' })
                      }}
                      placeholder="Pick the person (or type a category)"
                      className="min-w-[220px] flex-1 rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-950 dark:text-gray-100"
                    />
                    <input
                      value={l.amount}
                      onChange={e => setLine(i, { amount: e.target.value })}
                      placeholder="Amount (BWP)"
                      inputMode="decimal"
                      className="w-36 rounded-lg border border-gray-200 px-3 py-2 text-right text-sm dark:border-gray-700 dark:bg-gray-950 dark:text-gray-100"
                    />
                    <button
                      type="button"
                      onClick={() => setLines(ls => ls.length > 1 ? ls.filter((_, j) => j !== i) : ls)}
                      className="rounded-lg border border-gray-200 p-2 text-gray-400 hover:text-red-500 dark:border-gray-700"
                      title="Remove line"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>

                  <div className="mt-3 grid gap-2 sm:grid-cols-2">
                    {questions.map(({ key, q, good }) => {
                      const val = !!l[key]
                      // "good" answer is green: Yes for the first three, No for the fix question
                      const answeredWell = good ? val : !val
                      return (
                        <div key={String(key)} className="flex items-center justify-between gap-2 rounded-md bg-gray-50 px-2.5 py-1.5 dark:bg-gray-950/60">
                          <span className="text-xs text-gray-700 dark:text-gray-300">{q}</span>
                          <div className="flex gap-1">
                            <button type="button" onClick={() => setLine(i, { [key]: true } as Partial<Line>)}
                              className={`rounded px-2 py-0.5 text-xs font-medium ${val ? (good ? 'bg-emerald-600 text-white' : 'bg-red-600 text-white') : 'bg-gray-200 text-gray-600 dark:bg-gray-800 dark:text-gray-400'}`}>Yes</button>
                            <button type="button" onClick={() => setLine(i, { [key]: false } as Partial<Line>)}
                              className={`rounded px-2 py-0.5 text-xs font-medium ${!val ? (good ? 'bg-gray-200 text-gray-600 dark:bg-gray-800 dark:text-gray-400' : 'bg-emerald-600 text-white') : 'bg-gray-200 text-gray-600 dark:bg-gray-800 dark:text-gray-400'}`}>No</button>
                          </div>
                          <span className={`w-3 text-center text-xs ${answeredWell ? 'text-emerald-600' : 'text-gray-300 dark:text-gray-600'}`}>{answeredWell ? '✓' : ''}</span>
                        </div>
                      )
                    })}
                  </div>

                  <textarea
                    value={l.justification || ''}
                    onChange={e => setLine(i, { justification: e.target.value })}
                    rows={2}
                    placeholder="Why did this go over and above their normal day-to-day job? Be specific about what they over-performed on (at least 50 words — this is what the CFO and HR sign off)"
                    className="mt-2 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-950 dark:text-gray-100"
                  />
                  <div className={`mt-1 text-xs ${words >= MIN_JUSTIFICATION_WORDS ? 'text-emerald-600' : 'text-gray-400'}`}>
                    {words}/{MIN_JUSTIFICATION_WORDS} words{words >= MIN_JUSTIFICATION_WORDS ? ' ✓' : ''}
                  </div>
                </div>
              )})}
            </div>
            <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
              <button
                type="button"
                onClick={() => setLines(ls => [...ls, emptyLine()])}
                className="inline-flex items-center gap-1 text-sm font-medium text-blue-600 hover:text-blue-700"
              >
                <Plus className="h-4 w-4" /> Add line
              </button>
              <div className="text-sm font-semibold text-gray-800 dark:text-gray-200">
                Total: {fmtP(formTotal)}
              </div>
            </div>
            {formErr && (
              <div className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950 dark:text-red-300">
                {formErr}
              </div>
            )}
            {outstanding.length > 0 && (
              <div className="mt-3 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/50 dark:text-amber-200">
                <div className="font-semibold">
                  Still to do before you can submit:
                </div>
                <ul className="mt-1 list-disc space-y-0.5 pl-5">
                  {outstanding.map((b, i) => <li key={i}>{b}</li>)}
                </ul>
              </div>
            )}
            <label className="mt-4 flex items-start gap-2 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2.5 text-sm text-gray-700 dark:border-gray-700 dark:bg-gray-950/60 dark:text-gray-300">
              <input
                type="checkbox"
                checked={managerAttested}
                onChange={e => setManagerAttested(e.target.checked)}
                className="mt-0.5 h-4 w-4 shrink-0"
              />
              <span>I have checked this employee’s <strong>attendance and leave</strong>,
              their baseline is clean, and I would be comfortable if <strong>all
              staff saw why they are paid</strong>.</span>
            </label>
            <div className="mt-4 flex gap-2">
              <button
                type="button"
                disabled={busy === 'submit' || !canSubmit}
                onClick={submit}
                title={canSubmit ? '' : 'Every person must pass the qualifying questions and you must tick the declaration.'}
                className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
              >
                Submit for approval
              </button>
              <button
                type="button"
                onClick={() => setShowForm(false)}
                className="rounded-lg border border-gray-200 px-4 py-2 text-sm text-gray-600 dark:border-gray-700 dark:text-gray-300"
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        {!loading && !blocked && !error && requests.length === 0 && (
          <div className="rounded-xl border border-gray-200 bg-white p-6 text-sm text-gray-500 dark:border-gray-800 dark:bg-gray-900">
            {me?.can_approve || me?.is_finance
              ? 'No incentive requests yet.'
              : /* A maker sees only their OWN requests, so an empty page here
                   means "you have not raised one", not "the system lost them" —
                   spell that out (Unami / Sechele 2026-07-30). */
                'You have not raised an incentive request yet. Click “New request” '
                + 'above to raise one. You only see your own requests here — the '
                + 'CFO and HR see them all.'}
          </div>
        )}

        <div className="space-y-5">
          {requests.map(r => {
            const canAmend =
              !r.payroll.processed && r.status !== 'rejected' &&
              (r.is_own || !!me?.can_approve)
            return (
            <div
              key={r.id}
              className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm dark:border-gray-800 dark:bg-gray-900"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <div className="text-base font-semibold text-gray-900 dark:text-gray-100">
                    {r.title}
                    <span className="ml-2 text-sm font-normal text-gray-500">
                      {r.period}{r.department ? ` · ${r.department}` : ''}
                    </span>
                  </div>
                  <div className="mt-0.5 text-xs text-gray-500">
                    Requested by {r.maker_email || 'unknown'}
                    {r.created_at ? ` on ${r.created_at.slice(0, 10)}` : ''}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  {r.is_recurring && (
                    <span className="inline-flex items-center gap-1 rounded-full bg-amber-100 px-2.5 py-1 text-xs font-medium text-amber-700 dark:bg-amber-900/40 dark:text-amber-300">
                      <Repeat className="h-3 w-3" /> Recurring
                    </span>
                  )}
                  {r.amended && (
                    <span className="rounded-full bg-purple-100 px-2.5 py-1 text-xs font-medium text-purple-700 dark:bg-purple-900/40 dark:text-purple-300"
                      title={`Amended ${r.amended.at.slice(0, 10)}${r.amended.by ? ` by ${r.amended.by}` : ''}`}>
                      Amended
                    </span>
                  )}
                  <span className={`rounded-full px-3 py-1 text-xs font-medium ${STATUS_CHIP[r.status] || STATUS_CHIP.pending}`}>
                    {r.status.charAt(0).toUpperCase() + r.status.slice(1)}
                  </span>
                  {r.payroll.processed && (
                    <span className="rounded-full bg-blue-100 px-3 py-1 text-xs font-medium text-blue-700 dark:bg-blue-900/40 dark:text-blue-300">
                      In payroll
                    </span>
                  )}
                </div>
              </div>

              <div className="mt-3 overflow-x-auto">
                <table className="w-full text-sm">
                  <tbody>
                    {r.lines.map(l => (
                      <tr key={l.id} className="border-t border-gray-100 align-top dark:border-gray-800">
                        <td className="py-1.5 pr-3 text-gray-800 dark:text-gray-200">{l.name}</td>
                        <td className="py-1.5 pr-3 text-gray-500">
                          <div>{l.justification || l.basis}</div>
                          <div className="mt-1 flex flex-wrap gap-1 text-[10px] font-medium">
                            {l.beyond_normal_duties && <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300">beyond duties</span>}
                            {l.on_time && <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300">on time</span>}
                            {l.error_free && <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300">error-free</span>}
                            {l.needed_manager_fix && <span className="rounded bg-red-100 px-1.5 py-0.5 text-red-700 dark:bg-red-900/40 dark:text-red-300">needed manager fix</span>}
                          </div>
                        </td>
                        <td className="py-1.5 text-right font-medium text-gray-900 dark:text-gray-100">
                          {fmtP(l.amount)}
                        </td>
                      </tr>
                    ))}
                    <tr className="border-t border-gray-200 dark:border-gray-700">
                      <td colSpan={2} className="py-1.5 pr-3 font-semibold text-gray-900 dark:text-gray-100">
                        Total
                      </td>
                      <td className="py-1.5 text-right font-bold text-gray-900 dark:text-gray-100">
                        {fmtP(r.total)}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>

              <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
                <span className={`rounded-full px-2.5 py-1 font-medium ${r.signatures.cfo.signed ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300' : 'bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400'}`}>
                  CFO {r.signatures.cfo.signed ? '✓ signed' : '— awaiting'}
                </span>
                {/* CFO 2026-07-15: single CFO sign-off approves; HR shown only
                    if optionally signed. Second control is at FNB below. */}
                {r.signatures.hr.signed && (
                  <span className="rounded-full px-2.5 py-1 font-medium bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300">
                    HR ✓ signed
                  </span>
                )}
                <span className="rounded-full px-2.5 py-1 font-medium bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300">
                  2nd control at FNB · Pako loads · Unami reviews
                </span>
                {r.rejected?.notes && (
                  <span className="text-red-600 dark:text-red-400">
                    Rejected: {r.rejected.notes}
                  </span>
                )}
              </div>

              {(me?.can_approve || me?.is_finance || canAmend) && (
                <div className="mt-4 flex flex-wrap gap-2">
                  {me?.can_approve && r.status === 'pending' && !r.is_own && (
                    <>
                      <button
                        type="button"
                        disabled={!!busy}
                        onClick={() => action(r.id, 'approve')}
                        className="rounded-lg bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
                      >
                        Approve{me.approver_slot ? ` (${me.approver_slot.toUpperCase()} signature)` : ''}
                      </button>
                      <button
                        type="button"
                        disabled={!!busy}
                        onClick={() => rejectWithReason(r.id)}
                        className="rounded-lg border border-red-200 px-3 py-1.5 text-sm font-medium text-red-600 hover:bg-red-50 dark:border-red-900 dark:hover:bg-red-950"
                      >
                        Reject
                      </button>
                    </>
                  )}
                  {me?.is_finance && r.status === 'approved' && !r.payroll.processed && (
                    <button
                      type="button"
                      disabled={!!busy}
                      onClick={() => action(r.id, 'mark-processed')}
                      className="rounded-lg bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
                    >
                      Mark processed in payroll
                    </button>
                  )}
                  {canAmend && (
                    <button
                      type="button"
                      disabled={!!busy}
                      onClick={() => openAmend(r)}
                      className="inline-flex items-center gap-1 rounded-lg border border-purple-200 px-3 py-1.5 text-sm font-medium text-purple-700 hover:bg-purple-50 disabled:opacity-50 dark:border-purple-900 dark:text-purple-300 dark:hover:bg-purple-950"
                    >
                      <Pencil className="h-4 w-4" /> {amendId === r.id ? 'Close' : 'Amend'}
                    </button>
                  )}
                </div>
              )}

              {amendId === r.id && (
                <div className="mt-3 rounded-lg border border-purple-200 bg-purple-50/60 p-4 dark:border-purple-900 dark:bg-purple-950/30">
                  <div className="mb-2 text-sm font-semibold text-gray-900 dark:text-gray-100">
                    Correct the amount{r.signatures.cfo.signed ? ' — changing it clears the CFO sign-off and re-opens approval.' : ''}
                  </div>
                  <div className="space-y-2">
                    {r.lines.map(l => (
                      <div key={l.id} className="flex items-center justify-between gap-3">
                        <span className="text-sm text-gray-700 dark:text-gray-300">{l.name}</span>
                        <input
                          value={amendAmounts[l.id || ''] ?? ''}
                          onChange={e => setAmendAmounts(a => ({ ...a, [l.id || '']: e.target.value }))}
                          inputMode="decimal"
                          className="w-40 rounded-lg border border-gray-200 px-3 py-1.5 text-right text-sm dark:border-gray-700 dark:bg-gray-950 dark:text-gray-100"
                        />
                      </div>
                    ))}
                  </div>
                  <input
                    value={amendReason}
                    onChange={e => setAmendReason(e.target.value)}
                    placeholder="Reason for the correction (recorded in the audit log)"
                    className="mt-2 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-950 dark:text-gray-100"
                  />
                  {amendErr && (
                    <div className="mt-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950 dark:text-red-300">{amendErr}</div>
                  )}
                  <div className="mt-3 flex gap-2">
                    <button
                      type="button"
                      disabled={busy === 'amend' + r.id}
                      onClick={() => saveAmend(r)}
                      className="rounded-lg bg-purple-600 px-4 py-2 text-sm font-medium text-white hover:bg-purple-700 disabled:opacity-50"
                    >
                      Save correction
                    </button>
                    <button
                      type="button"
                      onClick={() => setAmendId('')}
                      className="rounded-lg border border-gray-200 px-4 py-2 text-sm text-gray-600 dark:border-gray-700 dark:text-gray-300"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </div>
            )
          })}
        </div>
      </main>
    </div>
  )
}
