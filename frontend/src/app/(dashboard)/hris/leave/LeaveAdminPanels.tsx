'use client'

/**
 * LeaveAdminPanels — the HR-only leave-administration surfaces (Unami Butale,
 * HR, 2026-07-27). Mounted on /hris/leave for users who hold
 * `manage_leave_admin`. Kept out of page.tsx so the employee/manager screen
 * stays lean.
 *
 *   • All leave      — every employee's Pending / Approved leave (asks #1, #2)
 *   • Department dashboard — on leave today, off in 14 days, per-dept/type (#3)
 *   • HR verification — dual-approval queue for manager-approved sick leave (#5)
 */
import { useCallback, useEffect, useState } from 'react'
import {
  Users, BarChart3, ShieldCheck, Paperclip, Loader2, Check, Flag,
  CalendarClock, AlertCircle, UserPlus,
} from 'lucide-react'
import { authedHrisFetch, fetchHrisEmployees, type HrisEmployee } from '../_shared'
import type { Theme } from '@/lib/themes'

interface AdminRow {
  id: string
  employee: string
  department: string
  leave_type: string
  leave_code: string
  start_date: string
  end_date: string
  days: number
  day_breakdown?: string
  status: string
  status_label: string
  reason: string
  reason_category?: string
  has_certificate: boolean
  certificate_url: string | null
  hr_review_state: string
  hr_review_label: string
  hr_review_notes: string
  approver: string
  decided_at: string | null
}

interface DeptRollup {
  department: string
  approved_days: number
  on_leave_today: number
  pending: number
  requests: number
}
interface TypeRollup { leave_type: string; approved_days: number }
interface Analytics {
  today: string
  on_leave_today: AdminRow[]
  upcoming_14d: AdminRow[]
  by_department: DeptRollup[]
  by_type: TypeRollup[]
}

// Leave days, never rounded UP (EXCO change request, 2026-08-11) — same rule as
// the balances and report screens. toFixed ROUNDS; Math.floor does not.
const fmtDays = (d: number) => {
  if (Number.isInteger(d)) return d.toFixed(0)
  // Server-floored already — only trim float noise. Math.floor(d*100)/100
  // drops a hundredth on 573 of 9,999 two-decimal values (0.29 → 0.28).
  return String(parseFloat(d.toFixed(2)))
}

export default function LeaveAdminPanels({
  theme, viewCertificate, certBusy,
}: {
  theme: Theme
  viewCertificate: (id: string) => void
  certBusy: string | null
}) {
  const card = { background: theme.card, border: `1px solid ${theme.cardBdr}` }
  const inp = { background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }

  // ── Apply for someone (HR-initiated, e.g. maternity for a person with no
  //    login). Oprah bug 8c165d53 / CFO 2026-09-05: the backend accepted
  //    on_behalf_employee_id since 3 Sep but HR had no screen for it. Unami is
  //    routed as the approver server-side; the initiator can never approve.
  const [obEmployees, setObEmployees] = useState<HrisEmployee[]>([])
  const [obEid, setObEid] = useState('')
  const [obType, setObType] = useState('maternity')
  const [obStart, setObStart] = useState('')
  const [obEnd, setObEnd] = useState('')
  const [obReason, setObReason] = useState('')
  const [obCert, setObCert] = useState<File | null>(null)
  const [obBusy, setObBusy] = useState(false)
  const [obMsg, setObMsg] = useState<{ ok: boolean; text: string } | null>(null)
  useEffect(() => {
    fetchHrisEmployees().then(r => setObEmployees((r.employees || []).filter(e => e.eid)))
  }, [])
  const obLocked = obType === 'maternity'
  async function applyOnBehalf() {
    if (!obEid || !obStart || (!obLocked && !obEnd)) {
      setObMsg({ ok: false, text: 'Pick the employee, the leave type and the dates.' }); return
    }
    if ((obType === 'sick' || obType === 'maternity') && !obCert) {
      setObMsg({ ok: false, text: 'A medical certificate is required for this leave type.' }); return
    }
    if (['compassionate', 'study', 'special'].includes(obType)) {
      setObMsg({ ok: false, text: `${obType[0].toUpperCase() + obType.slice(1)} leave is `
        + 'discretionary — the employee answers the questions and writes the motivation '
        + 'themselves, then the CFO signs it off. Ask them to apply on their own Leave screen.' })
      return
    }
    setObBusy(true); setObMsg(null)
    try {
      const fd = new FormData()
      fd.set('on_behalf_employee_id', obEid)
      fd.set('type', obType)
      fd.set('start_date', obStart)
      fd.set('end_date', obLocked ? obStart : obEnd)
      fd.set('reason', obReason.trim())
      if (obCert) fd.set('certificate', obCert)
      const r = await authedHrisFetch('/hris/api/leave-requests/', { method: 'POST', body: fd })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) { setObMsg({ ok: false, text: d.detail || `HTTP ${r.status}` }); return }
      setObMsg({ ok: true, text: `Leave applied for ${obEmployees.find(e => e.eid === obEid)?.nm || 'the employee'} and sent to Unami Butale to approve.` })
      setObEid(''); setObStart(''); setObEnd(''); setObReason(''); setObCert(null)
      loadRows()
    } catch (err) {
      setObMsg({ ok: false, text: err instanceof Error ? err.message : 'Network error' })
    } finally { setObBusy(false) }
  }

  // ── All leave (pending / approved) ────────────────────────────────────────
  const [tab, setTab] = useState<'pending' | 'approved'>('pending')
  const [rows, setRows] = useState<AdminRow[]>([])
  const [loadingRows, setLoadingRows] = useState(false)
  const [rowsErr, setRowsErr] = useState(false)
  const [dept, setDept] = useState('')

  const loadRows = useCallback(() => {
    setLoadingRows(true); setRowsErr(false)
    const q = new URLSearchParams({ status: tab })
    if (dept.trim()) q.set('department', dept.trim())
    authedHrisFetch(`/hris/api/leave-admin/all/?${q.toString()}`)
      .then(async r => {
        if (r.ok) { const d = await r.json(); setRows(Array.isArray(d.rows) ? d.rows : []) }
        else setRowsErr(true)
      })
      .catch(() => setRowsErr(true))
      .finally(() => setLoadingRows(false))
  }, [tab, dept])
  useEffect(() => { loadRows() }, [loadRows])

  // ── Department analytics ──────────────────────────────────────────────────
  const [analytics, setAnalytics] = useState<Analytics | null>(null)
  useEffect(() => {
    authedHrisFetch('/hris/api/leave-admin/analytics/')
      .then(async r => { if (r.ok) setAnalytics(await r.json()) })
      .catch(() => { /* analytics is non-critical */ })
  }, [])

  // ── HR verification queue ─────────────────────────────────────────────────
  const [hrQueue, setHrQueue] = useState<AdminRow[]>([])
  const [hrQueueErr, setHrQueueErr] = useState(false)
  const [verifyBusy, setVerifyBusy] = useState<string | null>(null)
  const [flagId, setFlagId] = useState<string | null>(null)
  const [flagNote, setFlagNote] = useState('')
  const [hrMsg, setHrMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const loadHrQueue = useCallback(() => {
    setHrQueueErr(false)
    authedHrisFetch('/hris/api/leave-admin/hr-queue/')
      .then(async r => {
        if (r.ok) { const d = await r.json(); setHrQueue(Array.isArray(d.rows) ? d.rows : []) }
        else setHrQueueErr(true)
      })
      // A fraud-control queue must NOT read "all clear" when the load failed.
      .catch(() => setHrQueueErr(true))
  }, [])
  useEffect(() => { loadHrQueue() }, [loadHrQueue])

  async function hrVerify(id: string, decision: 'verify' | 'flag', notes = '') {
    setVerifyBusy(id); setHrMsg(null)
    try {
      const r = await authedHrisFetch(`/hris/api/leave-admin/${id}/hr-verify/`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ decision, notes }),
      })
      const data = await r.json().catch(() => ({}))
      if (!r.ok) { setHrMsg({ ok: false, text: data.detail || `HTTP ${r.status}` }); return }
      setHrMsg({ ok: true, text: decision === 'verify' ? 'Leave verified.' : 'Leave flagged for follow-up.' })
      setHrQueue(q => q.filter(row => row.id !== id))
      setFlagId(null); setFlagNote('')
    } catch (err) {
      setHrMsg({ ok: false, text: err instanceof Error ? err.message : 'Network error' })
    } finally {
      setVerifyBusy(null)
    }
  }

  const certChip = (row: AdminRow) => row.has_certificate ? (
    <button type="button" onClick={() => viewCertificate(row.id)} disabled={certBusy === row.id}
            className="ml-1 inline-flex items-center gap-0.5 text-[10px] font-semibold underline decoration-dotted disabled:opacity-50"
            style={{ color: theme.ok }}>
      {certBusy === row.id ? <Loader2 className="w-3 h-3 animate-spin" /> : <Paperclip className="w-3 h-3" />} View cert
    </button>
  ) : null

  /** Same readable chip the employee sees on /hris/leave — a manager confirming a
 *  Time Doctor deduction should not be shown a raw code the staff member can't
 *  read either (bug b7e41c7e). */
function leaveTypeChip(row: { leave_code?: string; leave_type?: string }): string {
  if ((row.leave_code || '').toLowerCase() === 'td_deduct') return 'Time Doctor (unpaid)'
  return row.leave_code || row.leave_type || ''
}

const codeChip = (row: AdminRow) => (
    <span className="ml-2 px-1.5 py-0.5 rounded text-[10px] uppercase tracking-wide font-semibold"
          style={{ background: theme.orange + '22', color: theme.orange }}>
      {leaveTypeChip(row)}
    </span>
  )

  const whenLine = (row: AdminRow) =>
    row.day_breakdown && row.day_breakdown.length ? row.day_breakdown
      : `${row.start_date} → ${row.end_date} · ${fmtDays(row.days)}d`

  return (
    <div className="space-y-5">
      {/* ── Apply for someone (HR on behalf) ── */}
      <div className="rounded-2xl p-5" style={card}>
        <h3 className="font-semibold inline-flex items-center gap-1.5 mb-1" style={{ color: theme.text }}>
          <UserPlus className="w-4 h-4" style={{ color: theme.orange }} /> Apply for someone
        </h3>
        <p className="text-xs mb-3" style={{ color: theme.t2 }}>
          For an employee who cannot apply themselves (no Omni login yet, or already on maternity leave).
          The request goes to Unami Butale to approve — you cannot approve what you apply for.
          Maternity is locked to 98 days from the start date.
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div className="sm:col-span-2">
            <label className="block text-xs mb-1" style={{ color: theme.t2 }}>Employee</label>
            <select value={obEid} onChange={e => setObEid(e.target.value)} className="w-full rounded-lg px-3 py-2 text-sm outline-none" style={inp}>
              <option value="">— select employee —</option>
              {obEmployees.map(e => <option key={e.eid} value={e.eid}>{e.nm} · {e.company} · {e.ps}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-xs mb-1" style={{ color: theme.t2 }}>Leave type</label>
            <select value={obType} onChange={e => setObType(e.target.value)} className="w-full rounded-lg px-3 py-2 text-sm outline-none" style={inp}>
              {[['maternity', 'Maternity (98 days, 70% pay)'], ['sick', 'Sick'], ['annual', 'Annual'], ['paternity', 'Paternity'],
                ['compassionate', 'Compassionate'], ['study', 'Study'], ['special', 'Special']].map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
            {/* Discretionary leave (CFO 2026-09-10): the questions, the 50-word
                motivation and the undertaking are the employee's own answers —
                HR cannot write them, and this panel has no fields for them. Say
                so here rather than letting the server bounce it with a list of
                missing answers HR cannot fill in. */}
            {['compassionate', 'study', 'special'].includes(obType) && (
              <p className="text-[11px] mt-1.5" style={{ color: theme.er }}>
                {obType[0].toUpperCase() + obType.slice(1)} leave is discretionary. The
                employee answers the questions and writes the motivation themselves on
                their own Leave screen, and the CFO signs it off. It cannot be applied
                for on their behalf here.
              </p>
            )}
          </div>
          <div>
            <label className="block text-xs mb-1" style={{ color: theme.t2 }}>Start date</label>
            <input type="date" value={obStart} onChange={e => setObStart(e.target.value)} className="w-full rounded-lg px-3 py-2 text-sm outline-none" style={inp} />
          </div>
          {!obLocked && (
            <div>
              <label className="block text-xs mb-1" style={{ color: theme.t2 }}>End date</label>
              <input type="date" value={obEnd} onChange={e => setObEnd(e.target.value)} className="w-full rounded-lg px-3 py-2 text-sm outline-none" style={inp} />
            </div>
          )}
          <div>
            <label className="block text-xs mb-1" style={{ color: theme.t2 }}>Medical certificate {(obType === 'sick' || obType === 'maternity') ? '(required)' : '(optional)'}</label>
            <input type="file" accept=".pdf,.jpg,.jpeg,.png" onChange={e => setObCert(e.target.files?.[0] || null)}
              className="w-full text-xs" style={{ color: theme.t2 }} />
          </div>
          <div className="sm:col-span-2">
            <label className="block text-xs mb-1" style={{ color: theme.t2 }}>Note</label>
            <input value={obReason} onChange={e => setObReason(e.target.value)} placeholder="e.g. maternity leave, applied by HR on her behalf"
              className="w-full rounded-lg px-3 py-2 text-sm outline-none" style={inp} />
          </div>
        </div>
        <button type="button" onClick={applyOnBehalf} disabled={obBusy}
          className="mt-3 inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold disabled:opacity-50"
          style={{ background: theme.orange, color: '#fff' }}>
          {obBusy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />} Apply on their behalf
        </button>
        {obMsg && (
          <div className="mt-3 rounded-lg px-3 py-2 text-sm" style={{
            background: obMsg.ok ? theme.okB : theme.erB, color: obMsg.ok ? theme.ok : theme.er,
            border: `1px solid ${(obMsg.ok ? theme.ok : theme.er)}40`,
          }}>{obMsg.text}</div>
        )}
      </div>

      {/* ── HR verification queue (dual approval) ── */}
      <div className="rounded-2xl p-5" style={card}>
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold inline-flex items-center gap-1.5" style={{ color: theme.text }}>
            <ShieldCheck className="w-4 h-4" style={{ color: theme.orange }} />
            HR verification
            {hrQueue.length > 0 && (
              <span className="ml-1 px-1.5 py-0.5 rounded text-[10px] font-bold"
                    style={{ background: theme.orange, color: '#fff' }}>{hrQueue.length}</span>
            )}
          </h3>
          <button type="button" onClick={loadHrQueue} className="text-xs font-semibold" style={{ color: theme.t2 }}>Refresh</button>
        </div>
        <p className="text-xs mb-3" style={{ color: theme.t2 }}>
          Manager-approved sick leave awaiting HR checks — authenticate the dates and the certificate before the record is final.
        </p>
        {hrQueueErr && (
          <div className="py-4 text-center text-sm inline-flex items-center justify-center gap-1.5 w-full"
               style={{ color: theme.er }}>
            <AlertCircle className="w-4 h-4" /> Could not load the verification queue — please refresh.
          </div>
        )}
        {!hrQueueErr && hrQueue.length === 0 && (
          <div className="py-4 text-center text-sm" style={{ color: theme.t2 }}>Nothing to verify. All clear.</div>
        )}
        <div className="space-y-2">
          {hrQueue.map(row => (
            <div key={row.id} className="rounded-lg px-3 py-3" style={{ background: theme.g100 }}>
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="text-sm font-semibold" style={{ color: theme.text }}>
                    {row.employee}{codeChip(row)}{certChip(row)}
                  </div>
                  <div className="text-xs mt-0.5" style={{ color: theme.t2 }}>
                    {row.department || '—'} · {whenLine(row)}
                    {row.approver && <> · approved by {row.approver}</>}
                  </div>
                </div>
                <div className="flex items-center gap-2 flex-shrink-0">
                  <button type="button" onClick={() => hrVerify(row.id, 'verify')} disabled={verifyBusy === row.id}
                          className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-semibold disabled:opacity-50"
                          style={{ background: theme.ok, color: '#fff' }}>
                    {verifyBusy === row.id ? <Loader2 className="w-3 h-3 animate-spin" /> : <Check className="w-3 h-3" />} Verify
                  </button>
                  <button type="button" onClick={() => { setFlagId(flagId === row.id ? null : row.id); setFlagNote('') }}
                          disabled={verifyBusy === row.id}
                          className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-semibold disabled:opacity-50"
                          style={{ background: theme.er, color: '#fff' }}>
                    <Flag className="w-3 h-3" /> Flag
                  </button>
                </div>
              </div>
              {flagId === row.id && (
                <div className="mt-2.5 rounded-lg p-2.5" style={{ background: theme.card, border: `1px solid ${theme.er}40` }}>
                  <label className="block text-[11px] font-semibold mb-1" style={{ color: theme.t2 }}>
                    What is the concern? (required — recorded on the request)
                  </label>
                  <textarea value={flagNote} onChange={e => setFlagNote(e.target.value)} rows={2} autoFocus
                            placeholder="e.g. Certificate date does not match the leave dates — verifying with the clinic."
                            className="w-full px-2.5 py-1.5 rounded-md text-sm outline-none resize-y"
                            style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }} />
                  <div className="flex items-center justify-end gap-2 mt-2">
                    <button type="button" onClick={() => { setFlagId(null); setFlagNote('') }}
                            className="px-2.5 py-1 rounded-md text-xs font-semibold" style={{ color: theme.t2 }}>Cancel</button>
                    <button type="button" onClick={() => hrVerify(row.id, 'flag', flagNote.trim())}
                            disabled={verifyBusy === row.id || !flagNote.trim()}
                            className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-semibold disabled:opacity-50"
                            style={{ background: theme.er, color: '#fff' }}>
                      <Flag className="w-3 h-3" /> Confirm flag
                    </button>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
        {hrMsg && (
          <div className="mt-3 rounded-lg px-3 py-2 text-sm flex items-start gap-2"
               style={{ background: hrMsg.ok ? theme.okB : theme.erB, color: hrMsg.ok ? theme.ok : theme.er,
                        border: `1px solid ${hrMsg.ok ? theme.ok : theme.er}40` }}>
            {hrMsg.ok ? <Check className="w-4 h-4 mt-0.5" /> : <AlertCircle className="w-4 h-4 mt-0.5" />}
            <div>{hrMsg.text}</div>
          </div>
        )}
      </div>

      {/* ── Department dashboard ── */}
      {analytics && (
        <div className="rounded-2xl p-5" style={card}>
          <h3 className="font-semibold inline-flex items-center gap-1.5 mb-3" style={{ color: theme.text }}>
            <BarChart3 className="w-4 h-4" style={{ color: theme.orange }} /> Department dashboard
          </h3>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {/* On leave today */}
            <div>
              <div className="text-xs font-semibold uppercase tracking-wide mb-2" style={{ color: theme.t2 }}>
                On leave today · {analytics.on_leave_today.length}
              </div>
              {analytics.on_leave_today.length === 0
                ? <p className="text-sm" style={{ color: theme.t2 }}>Nobody is on leave today.</p>
                : <div className="space-y-1.5">
                    {analytics.on_leave_today.map(r => (
                      <div key={r.id} className="text-sm flex items-center justify-between gap-2">
                        <span style={{ color: theme.text }}>{r.employee}</span>
                        <span className="text-xs" style={{ color: theme.t2 }}>{r.department || '—'} · until {r.end_date}</span>
                      </div>
                    ))}
                  </div>}
            </div>
            {/* Upcoming 2 weeks */}
            <div>
              <div className="text-xs font-semibold uppercase tracking-wide mb-2 inline-flex items-center gap-1" style={{ color: theme.t2 }}>
                <CalendarClock className="w-3.5 h-3.5" /> Going on leave (next 2 weeks) · {analytics.upcoming_14d.length}
              </div>
              {analytics.upcoming_14d.length === 0
                ? <p className="text-sm" style={{ color: theme.t2 }}>No leave starts in the next 2 weeks.</p>
                : <div className="space-y-1.5">
                    {analytics.upcoming_14d.map(r => (
                      <div key={r.id} className="text-sm flex items-center justify-between gap-2">
                        <span style={{ color: theme.text }}>{r.employee}</span>
                        <span className="text-xs" style={{ color: theme.t2 }}>{r.department || '—'} · from {r.start_date}</span>
                      </div>
                    ))}
                  </div>}
            </div>
          </div>
          {/* Per-department table */}
          {analytics.by_department.length > 0 && (
            <div className="mt-4 overflow-x-auto">
              <table className="w-full text-sm border-collapse">
                <thead>
                  <tr style={{ color: theme.t2 }}>
                    <th className="text-left font-semibold py-1.5">Department</th>
                    <th className="text-right font-semibold py-1.5">On leave today</th>
                    <th className="text-right font-semibold py-1.5">Pending</th>
                    <th className="text-right font-semibold py-1.5">Approved days (YTD)</th>
                  </tr>
                </thead>
                <tbody>
                  {analytics.by_department.map(d => (
                    <tr key={d.department} style={{ borderTop: `1px solid ${theme.cardBdr}` }}>
                      <td className="py-1.5" style={{ color: theme.text }}>{d.department}</td>
                      <td className="py-1.5 text-right tabular-nums" style={{ color: theme.text }}>{d.on_leave_today}</td>
                      <td className="py-1.5 text-right tabular-nums" style={{ color: d.pending > 0 ? theme.orange : theme.t2 }}>{d.pending}</td>
                      <td className="py-1.5 text-right tabular-nums" style={{ color: theme.text }}>{fmtDays(d.approved_days)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* ── All leave (pending / approved) ── */}
      <div className="rounded-2xl p-5" style={card}>
        <div className="flex items-center justify-between mb-3 gap-2 flex-wrap">
          <h3 className="font-semibold inline-flex items-center gap-1.5" style={{ color: theme.text }}>
            <Users className="w-4 h-4" style={{ color: theme.orange }} /> All leave
          </h3>
          <div className="flex items-center gap-2">
            {(['pending', 'approved'] as const).map(t => (
              <button key={t} type="button" onClick={() => setTab(t)}
                      className="px-3 py-1 rounded-lg text-xs font-semibold capitalize"
                      style={tab === t
                        ? { background: theme.orange, color: '#fff' }
                        : { background: theme.g100, color: theme.t2, border: `1px solid ${theme.cardBdr}` }}>
                {t}
              </button>
            ))}
            <input value={dept} onChange={e => setDept(e.target.value)} placeholder="Filter department…"
                   className="px-2.5 py-1 rounded-lg text-xs outline-none w-40"
                   style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }} />
          </div>
        </div>
        {loadingRows && (
          <div className="py-6 text-center text-sm" style={{ color: theme.t2 }}>
            <Loader2 className="w-4 h-4 inline animate-spin mr-1" /> Loading…
          </div>
        )}
        {!loadingRows && rowsErr && (
          <div className="py-6 text-center text-sm inline-flex items-center justify-center gap-1.5 w-full"
               style={{ color: theme.er }}>
            <AlertCircle className="w-4 h-4" /> Could not load leave — please refresh.
          </div>
        )}
        {!loadingRows && !rowsErr && rows.length === 0 && (
          <div className="py-6 text-center text-sm" style={{ color: theme.t2 }}>No {tab} leave{dept ? ` in ${dept}` : ''}.</div>
        )}
        {!loadingRows && !rowsErr && rows.length > 0 && (
          <div className="space-y-2 max-h-[32rem] overflow-y-auto">
            {rows.map(row => (
              <div key={row.id} className="rounded-lg px-3 py-2.5" style={{ background: theme.g100 }}>
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="text-sm font-semibold" style={{ color: theme.text }}>
                      {row.employee}{codeChip(row)}{certChip(row)}
                      {row.hr_review_state === 'flagged' && (
                        <span className="ml-1 inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-semibold"
                              style={{ background: theme.er + '22', color: theme.er }}>
                          <Flag className="w-3 h-3" /> flagged
                        </span>
                      )}
                      {row.hr_review_state === 'verified' && (
                        <span className="ml-1 inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-semibold"
                              style={{ background: theme.ok + '22', color: theme.ok }}>
                          <ShieldCheck className="w-3 h-3" /> verified
                        </span>
                      )}
                    </div>
                    <div className="text-xs mt-0.5" style={{ color: theme.t2 }}>
                      {row.department || '—'} · {whenLine(row)}
                      {row.reason_category && <> · <span style={{ color: theme.text }}>{row.reason_category}</span></>}
                      {row.approver && <> · by {row.approver}</>}
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
