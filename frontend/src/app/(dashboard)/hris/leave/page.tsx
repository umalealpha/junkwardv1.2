'use client'

/**
 * /hris/leave — native Leave surface with Alpha Direct CoS compliance.
 *
 * CFO directive 2026-05-18 (Unami audit closeout): enforce the seven
 * leave types Alpha Direct supports with their exact entitlement and
 * paid-percentage rules, and require a medical certificate upload on
 * Sick Leave (CoS §7.6.1).
 *
 * Balances come from GET  /hris/api/leave-balances/
 * Applications post  POST /hris/api/leave-requests/  (multipart)
 */
import { useEffect, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import {
  ChevronLeft, Calendar as CalendarIcon, Send, Info, AlertCircle,
  Paperclip, FileText, Check, X, Inbox, Loader2, BarChart3, AlertTriangle,
} from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { estimateWorkingDays } from '@/lib/leaveDays'
import { useTheme } from '@/contexts/ThemeContext'
import { useHrisAccess, useHrisCan, useHrisSelfService } from '@/hooks/useHrisAccess'
import { authedHrisFetch } from '../_shared'
import { saveBlob } from '@/lib/api'
import { OverdueBanner, OverdueModal, type OverdueInfo } from '@/components/hris/OverdueWork'
import LeaveAdminPanels from './LeaveAdminPanels'
import { localYmd } from '@/lib/utils'

interface LeaveBalance {
  code: string
  name: string
  days: number
  used: number
  remaining: number
  accrued?: number       // accrued to date (annual = pro-rata by month)
  available?: number     // bookable now (accrued − used)
  accrues?: boolean      // true for annual (CoS §7.5.1 accrual)
  paid_pct: number
  cos: string
  rule: string
}

interface BalancesResponse {
  year: number
  balances: LeaveBalance[]
  has_profile: boolean
}

// Who may review a leave request (CFO 2026-07-15) — genuine people-managers
// only, fetched from /hris/api/leave-managers/.
interface ManagerOption {
  id: number
  name: string
  department: string
}

/** The Time Doctor deduction is raised by Omni itself (the 4pm enforce job), not
 *  applied for by the employee. Staff read an approved row in their own list as
 *  leave a manager granted them, so it is marked explicitly (bug b7e41c7e). */
function isAutoRaised(m: { leave_code?: string; reason?: string }): boolean {
  // Keyed on the enforce job's own 'Auto-applied:' stamp, NOT on the type:
  // td_deduct is ALSO an employee self-service type (CFO 2026-08-25), so a
  // deduction someone applied for themselves must not be labelled auto-raised.
  return (m.leave_code || '').toLowerCase() === 'td_deduct'
    && (m.reason || '').trimStart().startsWith('Auto-applied')
}

/** Short, readable chip. Raw codes ('TD_DEDUCT') meant nothing to staff. */
function typeChip(m: { leave_code?: string; leave_type?: string }): string {
  const code = (m.leave_code || '').toLowerCase()
  if (code === 'td_deduct') return 'Time Doctor (unpaid)'
  return m.leave_code || m.leave_type || ''
}

const LEAVE_TYPE_ORDER = [
  'annual', 'sick', 'maternity', 'paternity',
  'compassionate', 'study', 'special',
  // Time Doctor deduction (CFO 2026-08-25: employee self-service, and it follows
  // the SAME pattern as every other leave type — a card in this grid + the same
  // apply flow, not a special case). Zero entitlement, unpaid; the backend
  // rule (feature_views.DEFAULT_LEAVE_RULES) carries days=0 / paid_pct=0.
  'td_deduct',
] as const
type LeaveCode = typeof LEAVE_TYPE_ORDER[number]

// Day-type per boundary (half-day support). 'full' | 'am' | 'pm'.
type DayType = 'full' | 'am' | 'pm'

interface QueueRow {
  id: string
  employee: string
  department: string
  leave_type: string
  leave_code: string
  start_date: string
  end_date: string
  days: number
  start_day_type?: DayType
  end_day_type?: DayType
  day_breakdown?: string
  reason: string
  reason_category?: string
  has_certificate: boolean
  tasks_in_window?: number
  // No start date on the employee's record, so the leave balance behind this
  // request was counted from 1 January and is a guess, not a figure.
  balance_is_assumed?: boolean
  created_at: string
  // Discretionary leave (CFO 2026-09-10) — the answers and the pattern travel
  // with the row so the manager decides on those, not on the dates alone.
  is_discretionary?: boolean
  policy_answers?: { label: string; value: string }[]
  policy_ack?: boolean
  policy_history?: { count: number; days: number; months: number } | null
}

// ── Discretionary leave (CFO 2026-09-10) ──────────────────────────────────
// Compassionate / study / special are granted at the company's discretion and
// were being used to keep annual-leave days. The question set is defined ONCE
// on the server (hris/discretionary_leave.py) and fetched here, so the form
// and the validation can never drift apart.
interface PolicyQuestion {
  key: string
  label: string
  kind: 'text' | 'longtext' | 'choice' | 'date'
  options: string[]
  min_words: number
  help: string
  depends_on: Record<string, string> | null
}
interface PolicySpec {
  discretionary: boolean
  type?: string
  notice_title?: string
  notice_body?: string
  ack_text?: string
  min_words?: number
  proof_required?: boolean
  proof_label?: string
  annual_available?: number | null
  history?: { count: number; days: number; months: number }
  questions: PolicyQuestion[]
}

// Words the way a person counts them — must match discretionary_leave.word_count.
const wordCount = (s: string) => (s.match(/[A-Za-z0-9']+/g) || []).length

// Fixed reason categories (Unami 2026-07-27) — the employee picks one instead of
// being forced to write a justification. Mirrors LeaveRequest.ReasonCategory.
const REASON_CATEGORIES: { v: string; label: string }[] = [
  { v: 'personal', label: 'Personal / rest' },
  { v: 'family', label: 'Family responsibility' },
  { v: 'medical', label: 'Medical / health' },
  { v: 'bereavement', label: 'Bereavement' },
  { v: 'travel', label: 'Travel' },
  { v: 'religious', label: 'Religious / cultural' },
  { v: 'study', label: 'Study / exams' },
  { v: 'other', label: 'Other' },
  { v: 'undisclosed', label: 'Prefer not to say' },
]

// The caller's own requests (employee-facing status) — bug 3af04928.
interface MyReq {
  id: string
  leave_type: string
  leave_code: string
  start_date: string
  end_date: string
  days: number
  start_day_type?: DayType
  end_day_type?: DayType
  day_breakdown?: string
  status: string
  status_label: string
  reason: string
  decision_notes: string
  approver: string
  decided_at: string | null
  created_at: string
  can_cancel?: boolean
}

// An approver's own past decisions (history) — bug 3af04928.
interface DecisionRow {
  id: string
  employee: string
  department: string
  leave_type: string
  leave_code: string
  start_date: string
  end_date: string
  days: number
  start_day_type?: DayType
  end_day_type?: DayType
  day_breakdown?: string
  status: string
  status_label: string
  decision_notes: string
  decided_at: string | null
}

// Show 0.5 / 2.5 exactly, but whole days without a trailing ".0".
// Leave days, never rounded UP (EXCO change request, 2026-08-11). toFixed
// ROUNDS — it printed a 1.75-day accrual as "1.8", a fraction of a day the
// employee had not earned. Floor to two decimals and trim the trailing zero.
const fmtDays = (d: number) => {
  if (Number.isInteger(d)) return d.toFixed(0)
  // Server-floored already — only trim float noise. Math.floor(d*100)/100
  // drops a hundredth on 573 of 9,999 two-decimal values (0.29 → 0.28).
  return String(parseFloat(d.toFixed(2)))
}

const DAY_TYPE_OPTIONS: { v: DayType; label: string }[] = [
  { v: 'full', label: 'Full day' },
  { v: 'am', label: 'Half day (AM)' },
  { v: 'pm', label: 'Half day (PM)' },
]
// A request carries a half-day if either boundary is not a full day.
const hasHalf = (r: { start_day_type?: DayType; end_day_type?: DayType }) =>
  (r.start_day_type && r.start_day_type !== 'full') ||
  (r.end_day_type && r.end_day_type !== 'full')

export default function HrisLeavePage() {
  const router = useRouter()
  const { theme } = useTheme()
  const allowed = useHrisAccess()
  // Self-service tier (CFO directive 2026-06-16, bug aec2f3ce): any employee
  // may log their OWN leave even when they're not on the HRIS whitelist.
  // `canView` = privileged OR self-service; `denied` only when BOTH resolve false.
  const selfService = useHrisSelfService()
  const canView = allowed === true || selfService === true
  const accessDenied = allowed === false && selfService === false
  const canApprove = useHrisCan('approve_team_leave')
  // HRIS-002: Leave Report is manager/HR-tier only — employees never see the link.
  const canViewReport = useHrisCan('view_team')
  // HR leave administration (Unami 2026-07-27): all-leave oversight, department
  // analytics, and the HR dual-approval verification queue — HR tier only.
  const canAdminLeave = useHrisCan('manage_leave_admin')

  const today = localYmd(new Date())
  const [balances, setBalances] = useState<BalancesResponse | null>(null)
  const [loadingBal, setLoadingBal] = useState(true)
  const [type, setType] = useState<LeaveCode>('annual')
  const [start, setStart] = useState(today)
  const [end, setEnd] = useState(today)
  // Half-day support (Kago Tshutlhedi 2026-07-13). Off by default → existing
  // behaviour unchanged. When on, each boundary picks Full / AM / PM.
  const [halfDay, setHalfDay] = useState(false)
  const [startDayType, setStartDayType] = useState<DayType>('full')
  const [endDayType, setEndDayType] = useState<DayType>('full')
  const [reason, setReason] = useState('')
  // Fixed reason category (Unami 2026-07-27) — replaces the forced free-text why.
  const [reasonCategory, setReasonCategory] = useState('')
  // Choose-your-manager picker (CFO 2026-07-15): restricted to genuine
  // people-managers, pre-selected to the caller's own line manager if listed.
  const [managers, setManagers] = useState<ManagerOption[]>([])
  // Why the list came back empty (e.g. no approver set up for your company) —
  // the picker used to just disappear, leaving no way to route the request.
  const [managersEmptyReason, setManagersEmptyReason] = useState<string>('')
  const [approverId, setApproverId] = useState<string>('')
  const [certificate, setCertificate] = useState<File | null>(null)
  const certInputRef = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState<'idle' | 'queued' | 'error'>('idle')
  const [msg, setMsg] = useState<string | null>(null)
  // Why this application now needs a CEO/CFO signature (CFO 2026-08-07).
  const [overdue, setOverdue] = useState<OverdueInfo | null>(null)

  const [queue, setQueue] = useState<QueueRow[]>([])
  const [loadingQueue, setLoadingQueue] = useState(false)
  const [decideBusy, setDecideBusy] = useState<string | null>(null)
  const [cancelBusy, setCancelBusy] = useState<string | null>(null)
  // Post-leave reversal (Ontlametse Mogomotsi, ref AD/HR/IA/2026/001). The
  // employee says how many days they actually worked; their manager decides.
  const [revFor, setRevFor] = useState<string | null>(null)
  const [revDays, setRevDays] = useState('1')
  const [revReason, setRevReason] = useState('')
  // Proof of the days worked — a Time Doctor screenshot, a diary, an email. The
  // backend has accepted an attachment since the feature shipped; the form never
  // offered the field, so nobody could send one (report 2026-08-11, item 3).
  const [revFile, setRevFile] = useState<File | null>(null)
  const [revBusy, setRevBusy] = useState(false)
  // The manager's side: claims from their own team, with the tracked hours beside
  // each one so the decision is made on evidence, not on the claim alone.
  const [pendingRev, setPendingRev] = useState<any[]>([])
  const [revNotes, setRevNotes] = useState<Record<string, string>>({})
  const [revDecideBusy, setRevDecideBusy] = useState<string | null>(null)
  const [decideMsg, setDecideMsg] = useState<{ ok: boolean; text: string } | null>(null)
  // Reject-with-reason (bug 3af04928): a Reject opens an inline reason box; the
  // note is required and is stored as the decision note on the request.
  const [rejectingId, setRejectingId] = useState<string | null>(null)
  const [rejectNote, setRejectNote] = useState('')
  // Discretionary leave: the question set for the chosen type, the answers, and
  // the acknowledgement that they stay at work until it is approved.
  const [policy, setPolicy] = useState<PolicySpec | null>(null)
  const [policyAnswers, setPolicyAnswers] = useState<Record<string, string>>({})
  const [policyAck, setPolicyAck] = useState(false)
  const [policyErrors, setPolicyErrors] = useState<string[]>([])
  // My own requests (employee status) + my past decisions (approver history).
  const [mine, setMine] = useState<MyReq[]>([])
  const [decisions, setDecisions] = useState<DecisionRow[]>([])

  useEffect(() => {
    if (accessDenied) router.replace('/dashboard')
  }, [accessDenied, router])

  useEffect(() => {
    if (!canView) return
    setLoadingBal(true)
    authedHrisFetch('/hris/api/leave-balances/')
      .then(async r => {
        if (r.ok) setBalances(await r.json())
      })
      .finally(() => setLoadingBal(false))
  }, [canView])

  useEffect(() => {
    if (!canView) return
    authedHrisFetch('/hris/api/leave-managers/')
      .then(async r => {
        if (!r.ok) return
        const d = await r.json()
        setManagers(Array.isArray(d.managers) ? d.managers : [])
        setManagersEmptyReason(typeof d.empty_reason === 'string' ? d.empty_reason : '')
        if (d.default_manager_id) setApproverId(String(d.default_manager_id))
      })
      .catch(() => { /* picker is a nice-to-have — never break the page */ })
  }, [canView])

  // Pull the discretionary question set whenever the leave type changes. A
  // statutory type answers {discretionary:false} and the form stays short.
  useEffect(() => {
    if (!canView) return
    setPolicyErrors([])
    authedHrisFetch(`/hris/api/leave-policy/?type=${encodeURIComponent(type)}`)
      .then(async r => {
        if (!r.ok) { setPolicy(null); return }
        const d: PolicySpec = await r.json()
        setPolicy(d.discretionary ? d : null)
        if (!d.discretionary) { setPolicyAnswers({}); setPolicyAck(false) }
      })
      .catch(() => setPolicy(null))
  }, [type, canView])

  useEffect(() => {
    if (allowed !== true || canApprove !== true) return
    refreshQueue()
  }, [allowed, canApprove])

  function refreshQueue() {
    setLoadingQueue(true)
    authedHrisFetch('/hris/api/leave-requests/queue/')
      .then(async r => {
        if (r.ok) {
          const data = await r.json()
          setQueue(Array.isArray(data.pending) ? data.pending : [])
        }
      })
      .finally(() => setLoadingQueue(false))
  }

  useEffect(() => {
    if (!canView) return
    refreshMine()
  }, [canView])

  useEffect(() => {
    if (allowed !== true || canApprove !== true) return
    refreshDecisions()
  }, [allowed, canApprove])

  function refreshMine() {
    authedHrisFetch('/hris/api/leave-reversals/pending/')
      .then(r => (r.ok ? r.json() : { reversals: [] }))
      .then(d => setPendingRev(d.reversals || []))
      .catch(() => setPendingRev([]))
    authedHrisFetch('/hris/api/leave-requests/mine/')
      .then(async r => { if (r.ok) { const d = await r.json(); setMine(Array.isArray(d.requests) ? d.requests : []) } })
      .catch(() => { /* own-status list is non-critical — never break the page */ })
  }

  function refreshBalances() {
    authedHrisFetch('/hris/api/leave-balances/')
      .then(async r => { if (r.ok) setBalances(await r.json()) })
      .catch(() => { /* balance card is non-critical */ })
  }

  // Employee withdraws their own request (Kago 2026-07-16). Since 2026-08-08
  // this also covers APPROVED leave that has not started yet, so the wording
  // changes: cancelling leave a manager already signed is a different act from
  // withdrawing something still waiting, and they are told the manager is told.
  function refreshPendingReversals() {
    authedHrisFetch('/hris/api/leave-reversals/pending/')
      .then(r => (r.ok ? r.json() : { reversals: [] }))
      .then(d => setPendingRev(d.reversals || []))
      .catch(() => {})
  }

  async function openReversalProof(id: string) {
    // Fetch, do not link. The DRF token is in localStorage, so a plain href
    // reaches the endpoint unauthenticated and comes back 401. saveBlob rather
    // than window.open(blob) — the latter does nothing inside OmniDesktop.
    try {
      const r = await authedHrisFetch(`/hris/api/leave-reversals/${id}/attachment/`)
      if (!r.ok) { setDecideMsg({ ok: false, text: 'Could not open that proof.' }); return }
      saveBlob(await r.blob(), `reversal-proof-${id}`)
    } catch {
      setDecideMsg({ ok: false, text: 'Could not open that proof.' })
    }
  }

  async function decideReversal(id: string, decision: 'approve' | 'decline') {
    const notes = (revNotes[id] || '').trim()
    if (decision === 'decline' && !notes) {
      setMsg('Give a reason — the employee is shown what you write.')
      return
    }
    setRevDecideBusy(id)
    try {
      const r = await authedHrisFetch(`/hris/api/leave-reversals/${id}/decide/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ decision, notes }),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) { setMsg(d.detail || 'Could not save that.'); return }
      setMsg(decision === 'approve'
        ? `Approved. ${fmtDays(d.days)} day(s) are back in their balance.`
        : 'Declined. The employee has been told your reason.')
      refreshPendingReversals()
    } catch {
      setMsg('Could not save that. Please try again.')
    } finally { setRevDecideBusy(null) }
  }

  async function submitReversal(id: string) {
    setRevBusy(true)
    try {
      // Multipart when there is a file, JSON when there is not. Never set
      // Content-Type by hand on FormData — the browser has to add the multipart
      // boundary itself or the backend sees no file at all.
      let r: Response
      if (revFile) {
        const fd = new FormData()
        fd.append('days', revDays)
        fd.append('reason', revReason)
        fd.append('attachment', revFile)
        r = await authedHrisFetch(`/hris/api/leave-requests/${id}/reversals/`,
                                  { method: 'POST', body: fd })
      } else {
        r = await authedHrisFetch(`/hris/api/leave-requests/${id}/reversals/`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ days: revDays, reason: revReason }),
        })
      }
      const d = await r.json().catch(() => ({}))
      if (!r.ok) { setMsg(d.detail || 'Could not send that.'); return }
      setMsg(`Sent to ${d.approver_name || 'your manager'} to approve. `
        + `${fmtDays(d.days)} day(s) come back once they agree.`)
      setRevFor(null); setRevReason(''); setRevDays('1'); setRevFile(null)
      refreshMine()
    } catch {
      setMsg('Could not send that. Please try again.')
    } finally { setRevBusy(false) }
  }

  async function cancelRequest(id: string, wasApproved = false) {
    const msg = wasApproved
      ? 'Cancel this approved leave? The days go back into your balance and your manager is told.'
      : 'Cancel this leave request? This cannot be undone.'
    if (!window.confirm(msg)) return
    setCancelBusy(id); setDecideMsg(null)
    try {
      const r = await authedHrisFetch(`/hris/api/leave-requests/${id}/cancel/`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
      })
      const data = await r.json().catch(() => ({}))
      if (!r.ok) { setDecideMsg({ ok: false, text: data.detail || `HTTP ${r.status}` }); return }
      setDecideMsg({ ok: true, text: data.was_approved
        ? 'Approved leave cancelled. The days are back in your balance and your manager has been told.'
        : 'Leave request cancelled.' })
      refreshMine(); refreshBalances()   // the held days are freed again
    } catch (err) {
      setDecideMsg({ ok: false, text: err instanceof Error ? err.message : 'Network error' })
    } finally {
      setCancelBusy(null)
    }
  }

  function refreshDecisions() {
    authedHrisFetch('/hris/api/leave-requests/decisions/')
      .then(async r => { if (r.ok) { const d = await r.json(); setDecisions(Array.isArray(d.decisions) ? d.decisions : []) } })
      .catch(() => { /* approver history is non-critical */ })
  }

  // View a medical certificate (Unami ask #4). The file endpoint is access-
  // controlled (bearer header), so a plain link 401s — fetch it as a blob and
  // open it in a new tab where the browser renders the PDF / image inline.
  const [certBusy, setCertBusy] = useState<string | null>(null)
  async function viewCertificate(id: string) {
    setCertBusy(id)
    try {
      const r = await authedHrisFetch(`/hris/api/leave-requests/${id}/certificate/`)
      if (!r.ok) {
        setDecideMsg({ ok: false, text: `Could not open certificate (HTTP ${r.status}).` })
        return
      }
      const url = URL.createObjectURL(await r.blob())
      window.open(url, '_blank', 'noopener')
      setTimeout(() => URL.revokeObjectURL(url), 60_000)
    } catch {
      setDecideMsg({ ok: false, text: 'Could not open certificate — network error.' })
    } finally {
      setCertBusy(null)
    }
  }

  async function decide(id: string, decision: 'approve' | 'reject', notes = '') {
    setDecideBusy(id); setDecideMsg(null)
    try {
      const r = await authedHrisFetch(`/hris/api/leave-requests/${id}/decide/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ decision, notes }),
      })
      const data = await r.json().catch(() => ({}))
      if (!r.ok) { setDecideMsg({ ok: false, text: data.detail || `HTTP ${r.status}` }); return }
      setDecideMsg({ ok: true, text: `Request ${decision === 'approve' ? 'approved' : 'rejected'}.` })
      setQueue(q => q.filter(row => row.id !== id))
      setRejectingId(null); setRejectNote('')
      refreshDecisions()   // the decision now shows in the approver's history
    } catch (err) {
      setDecideMsg({ ok: false, text: err instanceof Error ? err.message : 'Network error' })
    } finally {
      setDecideBusy(null)
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setStatus('idle')
    setMsg(null)
    if (type === 'sick' && !certificate) {
      setStatus('error')
      setMsg('Medical certificate is required for Sick Leave (CoS §7.6.1).')
      return
    }
    // Reason is now a fixed category, not a forced justification (Unami
    // 2026-07-27) — an employee need not disclose why. Just pick a category.
    if (!reasonCategory) {
      setStatus('error')
      setMsg('Pick a reason category (you do not have to explain further).')
      return
    }
    // Discretionary leave: catch the obvious gaps here so the person is not
    // bounced by the server for a blank box. The server re-checks all of it —
    // this is courtesy, not the control.
    setPolicyErrors([])
    if (policy) {
      const gaps: string[] = []
      for (const q of policy.questions) {
        if (q.depends_on && !Object.entries(q.depends_on)
          .every(([k, v]) => (policyAnswers[k] || '').trim() === v)) continue
        const val = (policyAnswers[q.key] || '').trim()
        if (!val) { gaps.push(`Answer required: ${q.label}`); continue }
        if (q.min_words && wordCount(val) < q.min_words)
          gaps.push(`"${q.label}" needs at least ${q.min_words} words — you wrote ${wordCount(val)}.`)
      }
      const min = policy.min_words || 50
      if (wordCount(reason) < min)
        gaps.push(`Your motivation must be at least ${min} words. You wrote ${wordCount(reason)}.`)
      if (!policyAck) gaps.push(`You must tick: "${policy.ack_text}"`)
      if (policy.proof_required && !certificate)
        gaps.push(`Attach the supporting document — ${policy.proof_label}.`)
      if (gaps.length) {
        setStatus('error')
        setPolicyErrors(gaps)
        setMsg('This leave is not automatic. Please complete every part below.')
        return
      }
    }
    setBusy(true)
    try {
      const fd = new FormData()
      fd.append('type', type)
      fd.append('start_date', start)
      fd.append('end_date', end)
      // Day types: single-day request carries its choice on the start boundary
      // only (end stays full so 0.5 isn't deducted twice); a range carries a
      // choice on each end.
      const sameDate = start === end
      const effStart: DayType = halfDay ? startDayType : 'full'
      const effEnd: DayType = halfDay ? (sameDate ? 'full' : endDayType) : 'full'
      fd.append('start_day_type', effStart)
      fd.append('end_day_type', effEnd)
      if (reason) fd.append('reason', reason)
      if (reasonCategory) fd.append('reason_category', reasonCategory)
      if (approverId) fd.append('approver_id', approverId)
      if (certificate) fd.append('certificate', certificate)
      if (policy) {
        fd.append('policy_answers', JSON.stringify(policyAnswers))
        fd.append('policy_ack', policyAck ? 'true' : 'false')
      }

      const r = await authedHrisFetch('/hris/api/leave-requests/', {
        method: 'POST', body: fd,
      })
      const data = await r.json().catch(() => ({}))
      if (r.ok) {
        setStatus('queued')
        setMsg(data.message || 'Application submitted.')
        if (data.needs_exec_signoff) setOverdue(data)
        setReason('')
        setReasonCategory('')
        setHalfDay(false)
        setStartDayType('full')
        setEndDayType('full')
        setCertificate(null)
        setPolicyAnswers({})
        setPolicyAck(false)
        setPolicyErrors([])
        if (certInputRef.current) certInputRef.current.value = ''
        // Refresh balances so the used column updates.
        authedHrisFetch('/hris/api/leave-balances/')
          .then(async r2 => { if (r2.ok) setBalances(await r2.json()) })
        refreshMine()   // show the new request in "My requests" straight away
      } else {
        setStatus('error')
        // The discretionary check returns every problem at once so the form is
        // fixed in one pass — show them as a list, not one run-on sentence.
        if (Array.isArray(data.errors) && data.errors.length) {
          setPolicyErrors(data.errors)
          setMsg('This leave is not automatic. Please complete every part below.')
        } else {
          setMsg(data.detail || `HTTP ${r.status}`)
        }
      }
    } catch (err) {
      setStatus('error')
      setMsg(err instanceof Error ? err.message : 'Network error')
    } finally {
      setBusy(false)
    }
  }

  if (!canView) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ backgroundColor: theme.bg }}>
        <div className="w-8 h-8 border-2 border-[#F07F00] border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  const balByCode: Record<string, LeaveBalance> = {}
  for (const b of balances?.balances || []) balByCode[b.code] = b
  const selected = balByCode[type]
  const sameDate = start === end
  // Half days are a working-day concept — maternity is a calendar-day
  // entitlement (CoS §7.8.1), so it never offers the half-day option.
  const canHalfDay = type !== 'maternity'

  // Maternity end date is system-locked (bug f4464440): 98 statutory calendar
  // days, day 1 inclusive = start + 97. The field is read-only; the server
  // ignores any typed end for maternity anyway.
  const isMaternity = type === 'maternity'
  const maternityEnd = (() => {
    if (!isMaternity || !start) return ''
    const d = new Date(start + 'T00:00:00')
    d.setDate(d.getDate() + 97)
    return localYmd(d)
  })()

  // Balance shown at the POINT OF APPLYING (CFO 2026-09-02: employees should see
  // how many days they have, especially Annual and Sick, before they submit —
  // not discover "0 days" only at submit). Reuses the balances already fetched.
  const selAvail = selected ? (selected.available ?? selected.remaining) : 0
  const annualBal = balByCode['annual']
  const sickBal = balByCode['sick']
  // Rough requested-days heads-up (server stays the exact gate — see helper).
  const startHalf = halfDay && startDayType !== 'full'
  const endHalf = halfDay && !sameDate && endDayType !== 'full'
  const reqDays = estimateWorkingDays(start, end, startHalf, endHalf)
  const notEnough = !loadingBal && selected != null && reqDays != null
    && reqDays > 0 && reqDays > selAvail

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar title="Leave" breadcrumbs={[{ label: 'HRIS', href: '/hris' }, { label: 'Leave' }]} />
      {/* Bug 4a0daab5: extra bottom padding so the Submit button clears the
          floating Team Chat / ARIA widgets pinned in the bottom-right corner. */}
      <main className="flex-1 overflow-y-auto p-6 pb-28 space-y-5">
        <div className="flex items-center justify-between">
          <Link href="/hris" className="inline-flex items-center gap-1.5 text-sm font-medium" style={{ color: theme.t2 }}>
            <ChevronLeft className="w-4 h-4" /> Back to HRIS
          </Link>
          {canViewReport && (
            <Link href="/hris/leave/report"
                  title="Team-wide leave balances for managers/HR — not your personal leave (your own balances are shown below)."
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-semibold"
                  style={{ background: theme.orange + '22', color: theme.orange }}>
              <BarChart3 className="w-4 h-4" /> Team Leave Report
            </Link>
          )}
        </div>

        {/* Balances grid — every CoS leave type rendered, even at zero */}
        <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-4 gap-3">
          {LEAVE_TYPE_ORDER.map(code => {
            const b = balByCode[code]
            // Available = accrual-aware bookable amount (falls back to remaining
            // for back-compat if the API hasn't shipped `available` yet).
            const avail = b ? (b.available ?? b.remaining) : 0
            const cap = b ? b.days : 0
            return (
              <button
                key={code}
                type="button"
                onClick={() => setType(code)}
                className="rounded-2xl p-3 text-left transition-all"
                style={{
                  background: type === code ? theme.oL : theme.card,
                  border: `1px solid ${type === code ? theme.orange + '55' : theme.cardBdr}`,
                }}
              >
                <div className="text-[10px] uppercase tracking-wider font-semibold truncate"
                     style={{ color: type === code ? theme.orange : theme.t2 }}>
                  {code}
                </div>
                <div className="mt-1 flex items-baseline gap-1">
                  <span className="text-2xl font-bold tabular-nums" style={{ color: theme.text }}>
                    {loadingBal ? '—' : fmtDays(avail)}
                  </span>
                  <span className="text-xs" style={{ color: theme.t2 }}>/ {cap}d</span>
                </div>
                <div className="text-[10px] mt-1" style={{ color: theme.t2 }}>
                  {/* BUG 494b5676 (Oprah 2026-06-26): "{paid_pct}%" read as the
                      AVAILABILITY % — so Maternity (paid at 70%) looked like only
                      70% available when 98/98 days are free. It is the PAY RATE.
                      Label it "paid at X%" so it can't be misread; the big number
                      above ("98 / 98d") already shows availability. */}
                  {b ? (b.accrues ? `accrued · paid at ${b.paid_pct}% · ${b.cos}` : `available · paid at ${b.paid_pct}% · ${b.cos}`) : '…'}
                </div>
              </button>
            )
          })}
        </div>

        {/* CoS rule for the selected type — surfaced so the user can't
            apply for sick leave without realising they need a cert. */}
        {selected && (
          <div className="rounded-xl p-3 flex items-start gap-2 text-sm"
               style={{ background: theme.inB, border: `1px solid ${theme.inf}33`, color: theme.text }}>
            <Info className="w-4 h-4 flex-shrink-0 mt-0.5" style={{ color: theme.inf }} />
            <div>
              <span style={{ color: theme.t2 }}>Policy</span> · <strong>{selected.cos}</strong> · {selected.rule}
              {' '}<span style={{ color: theme.t2 }}>({selected.days}d/yr, paid at {selected.paid_pct}%.)</span>
            </div>
          </div>
        )}

        {/* My leave requests — employee-facing status + any rejection reason
            (bug 3af04928). */}
        {mine.length > 0 && (
          <div className="rounded-2xl p-5" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
            <h3 className="font-semibold inline-flex items-center gap-1.5 mb-3" style={{ color: theme.text }}>
              <FileText className="w-4 h-4" style={{ color: theme.orange }} /> My leave requests
            </h3>
            <div className="space-y-2">
              {mine.map(m => {
                const stColor = m.status === 'approved' ? theme.ok : m.status === 'refused' ? theme.er : theme.t2
                const stBg    = m.status === 'approved' ? theme.okB : m.status === 'refused' ? theme.erB : theme.g100
                return (
                  <div key={m.id} className="rounded-lg px-3 py-2.5" style={{ background: theme.g100 }}>
                    <div className="flex items-center justify-between gap-2">
                      <div className="text-sm font-medium min-w-0" style={{ color: theme.text }}>
                        <span className="px-1.5 py-0.5 rounded text-[10px] uppercase tracking-wide font-semibold mr-2"
                              style={{ background: theme.orange + '22', color: theme.orange }}>
                          {typeChip(m)}
                        </span>
                        {hasHalf(m) && m.day_breakdown
                          ? m.day_breakdown
                          : `${m.start_date} → ${m.end_date} · ${fmtDays(m.days)}d`}
                      </div>
                      <span className="px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wide flex-shrink-0"
                            style={{ background: stBg, color: stColor }}>
                        {m.status_label}
                      </span>
                    </div>
                    {m.status === 'refused' && m.decision_notes && (
                      <p className="text-xs mt-1.5" style={{ color: theme.er }}>Reason: {m.decision_notes}</p>
                    )}
                    {isAutoRaised(m) && (
                      <p className="text-[11px] mt-1.5 rounded px-2 py-1"
                         style={{ background: theme.orange + '18', color: theme.text }}>
                        Raised automatically by Omni — you did not apply for this. It is an
                        unpaid Time Doctor deduction, not annual leave, and it does not use
                        any of your leave days. If you think it is wrong, reply to your
                        manager or HR.
                      </p>
                    )}
                    {m.status === 'approved' && m.approver && (
                      <p className="text-[11px] mt-1" style={{ color: theme.t2 }}>
                        {isAutoRaised(m)
                          ? `Confirmed by ${m.approver} (they did not apply for it on your behalf)`
                          : `Approved by ${m.approver}`}
                      </p>
                    )}
                    {m.status === 'approved' && (
                      revFor === m.id ? (
                        <div className="mt-2 rounded-lg p-2.5"
                             style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
                          <p className="text-[11px] mb-2" style={{ color: theme.t2 }}>
                            Worked some of this leave? Say how many days and why. Your
                            manager decides, and they will see your tracked hours.
                          </p>
                          <div className="flex items-center gap-2 mb-2">
                            <label className="text-xs" style={{ color: theme.t2 }}>Days worked</label>
                            <input value={revDays} onChange={e => setRevDays(e.target.value)}
                              inputMode="decimal" placeholder="1"
                              className="w-20 text-sm rounded-md px-2 py-1"
                              style={{ background: theme.g100, color: theme.text,
                                       border: `1px solid ${theme.cardBdr}` }} />
                            <span className="text-[11px]" style={{ color: theme.t2 }}>
                              of {fmtDays(m.days)} — half days allowed
                            </span>
                          </div>
                          <textarea value={revReason} onChange={e => setRevReason(e.target.value)}
                            rows={2} placeholder="Why did you work these days?"
                            className="w-full text-sm rounded-md px-2 py-1.5 mb-2"
                            style={{ background: theme.g100, color: theme.text,
                                     border: `1px solid ${theme.cardBdr}` }} />
                          <div className="flex items-center gap-2 mb-2">
                            <label className="text-xs whitespace-nowrap" style={{ color: theme.t2 }}>
                              Proof (optional)
                            </label>
                            <input type="file" onChange={e => setRevFile(e.target.files?.[0] || null)}
                              accept=".png,.jpg,.jpeg,.pdf,.xlsx,.xls,.csv"
                              className="text-[11px] flex-1 min-w-0"
                              style={{ color: theme.t2 }} />
                          </div>
                          {revFile && (
                            <p className="text-[11px] mb-2" style={{ color: theme.t2 }}>
                              Attaching {revFile.name} — a Time Doctor screenshot or your
                              diary helps your manager decide.
                            </p>
                          )}
                          <div className="flex justify-end gap-2">
                            <button type="button" onClick={() => { setRevFor(null); setRevFile(null) }}
                              className="text-xs font-semibold px-2.5 py-1 rounded-md"
                              style={{ background: theme.g100, color: theme.t2,
                                       border: `1px solid ${theme.cardBdr}` }}>
                              Never mind
                            </button>
                            <button type="button" onClick={() => submitReversal(m.id)}
                              disabled={revBusy || !revReason.trim()}
                              className="inline-flex items-center gap-1 text-xs font-semibold px-2.5 py-1 rounded-md disabled:opacity-50"
                              style={{ background: theme.orange, color: '#fff' }}>
                              {revBusy && <Loader2 className="w-3 h-3 animate-spin" />}
                              Send to my manager
                            </button>
                          </div>
                        </div>
                      ) : (
                        <div className="mt-2 flex justify-end">
                          <button type="button" onClick={() => { setRevFor(m.id); setRevDays('1'); setRevReason('') }}
                            className="text-xs font-semibold px-2.5 py-1 rounded-md"
                            style={{ background: theme.g100, color: theme.t2,
                                     border: `1px solid ${theme.cardBdr}` }}>
                            I worked some of this leave
                          </button>
                        </div>
                      )
                    )}
                    {m.can_cancel && (
                      <div className="mt-2 flex justify-end">
                        <button type="button"
                          onClick={() => cancelRequest(m.id, m.status === 'approved')}
                          disabled={cancelBusy === m.id}
                          className="inline-flex items-center gap-1 text-xs font-semibold px-2.5 py-1 rounded-md disabled:opacity-50"
                          style={{ background: theme.g100, color: theme.t2, border: `1px solid ${theme.cardBdr}` }}>
                          {cancelBusy === m.id ? <Loader2 className="w-3 h-3 animate-spin" /> : <X className="w-3 h-3" />}
                          {m.status === 'approved' ? 'Cancel this leave' : 'Cancel request'}
                        </button>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        )}

        {/* Leave reversals from my team (Ontlametse Mogomotsi, AD/HR/IA/2026/001).
            The employee claims the days; the manager decides. Time Doctor hours are
            shown beside the claim so it is judged on evidence, not on the word. */}
        {pendingRev.length > 0 && (
          <div className="rounded-xl p-4 mb-4"
               style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
            <h3 className="text-sm font-semibold mb-1" style={{ color: theme.text }}>
              Leave reversals to approve ({pendingRev.length})
            </h3>
            <p className="text-[11px] mb-3" style={{ color: theme.t2 }}>
              Someone on your team says they worked days they had booked off. Approving
              puts those days back in their balance and shortens the leave record.
            </p>
            <div className="space-y-3">
              {pendingRev.map((rv: any) => (
                <div key={rv.id} className="rounded-lg px-3 py-2.5" style={{ background: theme.g100 }}>
                  <div className="flex items-center justify-between gap-2 flex-wrap">
                    <div className="text-sm font-medium" style={{ color: theme.text }}>
                      {rv.employee} — <strong>{fmtDays(rv.days)} day(s)</strong> back
                    </div>
                    <span className="text-[11px]" style={{ color: theme.t2 }}>
                      of {fmtDays(rv.original_leave?.days_at_request)}d
                      {' '}({rv.original_leave?.start_date} → {rv.original_leave?.end_date})
                    </span>
                  </div>
                  <p className="text-xs mt-1.5" style={{ color: theme.t2 }}>
                    <span style={{ color: theme.text }}>Their reason:</span> {rv.reason}
                  </p>
                  {rv.has_attachment && (
                    <button type="button" onClick={() => openReversalProof(rv.id)}
                      className="mt-1.5 inline-flex items-center gap-1 text-[11px] underline"
                      style={{ color: theme.orange }}>
                      <Paperclip className="w-3 h-3" />
                      Open the proof they attached
                    </button>
                  )}
                  {rv.evidence_note && (
                    <p className="text-[11px] mt-1.5 pl-2"
                       style={{ color: theme.t2, borderLeft: `2px solid ${theme.orange}` }}>
                      {rv.evidence_note}
                    </p>
                  )}
                  {rv.timedoctor?.available && (
                    <div className="mt-1.5 flex flex-wrap gap-1.5">
                      {rv.timedoctor.days.map((d: any) => (
                        <span key={d.date}
                          className="text-[10px] px-1.5 py-0.5 rounded"
                          style={{
                            background: d.looks_worked ? theme.orange + '22' : theme.g100,
                            color: d.looks_worked ? theme.orange : theme.t2,
                            border: `1px solid ${theme.cardBdr}`,
                          }}>
                          {d.date.slice(5)} · {d.tracked_hours}h
                        </span>
                      ))}
                    </div>
                  )}
                  <textarea value={revNotes[rv.id] || ''}
                    onChange={e => setRevNotes({ ...revNotes, [rv.id]: e.target.value })}
                    rows={2} placeholder="Reason — required if you decline"
                    className="w-full text-sm rounded-md px-2 py-1.5 mt-2"
                    style={{ background: theme.card, color: theme.text,
                             border: `1px solid ${theme.cardBdr}` }} />
                  <div className="mt-2 flex justify-end gap-2">
                    <button type="button" onClick={() => decideReversal(rv.id, 'decline')}
                      disabled={revDecideBusy === rv.id}
                      className="text-xs font-semibold px-2.5 py-1 rounded-md disabled:opacity-50"
                      style={{ background: theme.g100, color: theme.er,
                               border: `1px solid ${theme.cardBdr}` }}>
                      Decline
                    </button>
                    <button type="button" onClick={() => decideReversal(rv.id, 'approve')}
                      disabled={revDecideBusy === rv.id}
                      className="inline-flex items-center gap-1 text-xs font-semibold px-2.5 py-1 rounded-md disabled:opacity-50"
                      style={{ background: theme.orange, color: '#fff' }}>
                      {revDecideBusy === rv.id && <Loader2 className="w-3 h-3 animate-spin" />}
                      Approve — give the days back
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* HR leave administration (Unami 2026-07-27) — all-leave oversight,
            department dashboard, and the dual-approval verification queue.
            HR tier only; managers/employees never see it. */}
        {canAdminLeave && (
          <LeaveAdminPanels theme={theme} viewCertificate={viewCertificate} certBusy={certBusy} />
        )}

        {/* Manager approval queue */}
        {canApprove && (
          <div className="rounded-2xl p-5"
               style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-semibold inline-flex items-center gap-1.5" style={{ color: theme.text }}>
                <Inbox className="w-4 h-4" style={{ color: theme.orange }} />
                Approval queue
                {queue.length > 0 && (
                  <span className="ml-1 px-1.5 py-0.5 rounded text-[10px] font-bold"
                        style={{ background: theme.orange, color: '#fff' }}>{queue.length}</span>
                )}
              </h3>
              <button type="button" onClick={refreshQueue} className="text-xs font-semibold"
                      style={{ color: theme.t2 }}>Refresh</button>
            </div>
            {loadingQueue && (
              <div className="py-6 text-center text-sm" style={{ color: theme.t2 }}>
                <Loader2 className="w-4 h-4 inline animate-spin mr-1" /> Loading queue…
              </div>
            )}
            {!loadingQueue && queue.length === 0 && (
              <div className="py-6 text-center text-sm" style={{ color: theme.t2 }}>
                Nothing pending. Inbox zero.
              </div>
            )}
            {!loadingQueue && queue.length > 0 && (
              <div className="space-y-2">
                {queue.map(row => (
                  <div key={row.id} className="rounded-lg px-3 py-3"
                       style={{ background: theme.g100 }}>
                    <div className="flex items-start gap-3">
                      <div className="min-w-0 flex-1">
                        <div className="text-sm font-semibold truncate" style={{ color: theme.text }}>
                          {row.employee}
                          <span className="ml-2 px-1.5 py-0.5 rounded text-[10px] uppercase tracking-wide font-semibold"
                                style={{ background: theme.orange + '22', color: theme.orange }}>
                            {typeChip(row)}
                          </span>
                          {row.has_certificate && (
                            <button type="button" onClick={() => viewCertificate(row.id)}
                                    disabled={certBusy === row.id}
                                    className="ml-1 inline-flex items-center gap-0.5 text-[10px] font-semibold underline decoration-dotted disabled:opacity-50"
                                    style={{ color: theme.ok }}>
                              {certBusy === row.id
                                ? <Loader2 className="w-3 h-3 animate-spin" />
                                : <Paperclip className="w-3 h-3" />} View cert
                            </button>
                          )}
                          {(row.tasks_in_window ?? 0) > 0 && (
                            <span className="ml-1 inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-semibold"
                                  style={{ background: theme.er + '22', color: theme.er }}
                                  title="Open tasks that fall inside the leave dates">
                              <AlertCircle className="w-3 h-3" /> {row.tasks_in_window} task{(row.tasks_in_window ?? 0) > 1 ? 's' : ''} due
                            </span>
                          )}
                          {row.balance_is_assumed && (
                            <span className="ml-1 inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-semibold"
                                  style={{ background: theme.orange + '22', color: theme.orange }}
                                  title="This person has no start date in Omni, so their leave balance is counted from 1 January and is not a real figure">
                              <AlertCircle className="w-3 h-3" /> no start date
                            </span>
                          )}
                        </div>
                        <div className="text-xs mt-0.5" style={{ color: theme.t2 }}>
                          {row.department || '—'} · {hasHalf(row) && row.day_breakdown
                            ? row.day_breakdown
                            : `${row.start_date} → ${row.end_date} · ${fmtDays(row.days)}d`}
                          {row.reason_category && <> · <span style={{ color: theme.text }}>{row.reason_category}</span></>}
                        </div>
                        {row.balance_is_assumed && (
                          <p className="text-xs mt-1" style={{ color: theme.orange }}>
                            No start date is recorded for this person, so Omni counts their
                            leave from 1 January and the balance you see is a guess, not what
                            they have earned. Ask HR to load the start date before approving.
                          </p>
                        )}
                        {row.reason && (
                          <p className="text-xs mt-1 italic" style={{ color: theme.t2 }}>"{row.reason}"</p>
                        )}

                        {/* Discretionary leave (CFO 2026-09-10): decide on the
                            answers and the pattern, not on the dates. The CFO
                            signs after you either way. */}
                        {row.is_discretionary && (
                          <div className="mt-2 rounded-lg p-2.5 text-xs"
                               style={{ background: theme.erB, border: `1px solid ${theme.er}55` }}>
                            <p className="font-semibold mb-1" style={{ color: theme.er }}>
                              Discretionary leave — the CFO signs this off after you.
                              {row.policy_history && row.policy_history.count > 1 && (
                                <> Request #{row.policy_history.count} of this type in{' '}
                                  {row.policy_history.months} months
                                  ({fmtDays(row.policy_history.days)}d total).</>
                              )}
                            </p>
                            {!row.policy_ack && (
                              <p className="mb-1" style={{ color: theme.er }}>
                                They did NOT tick the acknowledgement.
                              </p>
                            )}
                            {(row.policy_answers || []).map((a, i) => (
                              <p key={i} style={{ color: theme.t2 }}>
                                <span style={{ color: theme.text }}>{a.label}</span> — {a.value}
                              </p>
                            ))}
                          </div>
                        )}
                      </div>
                      <div className="flex items-center gap-2 flex-shrink-0">
                        <button type="button" onClick={() => decide(row.id, 'approve')}
                                disabled={decideBusy === row.id}
                                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-semibold transition-opacity disabled:opacity-50"
                                style={{ background: theme.ok, color: '#fff' }}>
                          {decideBusy === row.id ? <Loader2 className="w-3 h-3 animate-spin" /> : <Check className="w-3 h-3" />}
                          Approve
                        </button>
                        <button type="button"
                                onClick={() => { setRejectingId(rejectingId === row.id ? null : row.id); setRejectNote('') }}
                                disabled={decideBusy === row.id}
                                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-semibold transition-opacity disabled:opacity-50"
                                style={{ background: rejectingId === row.id ? theme.er + 'cc' : theme.er, color: '#fff' }}>
                          <X className="w-3 h-3" /> Reject
                        </button>
                      </div>
                    </div>
                    {/* Reject reason — required, stored as the decision note and
                        shown to the employee on their request (bug 3af04928). */}
                    {rejectingId === row.id && (
                      <div className="mt-2.5 rounded-lg p-2.5"
                           style={{ background: theme.card, border: `1px solid ${theme.er}40` }}>
                        <label className="block text-[11px] font-semibold mb-1" style={{ color: theme.t2 }}>
                          Reason for rejection (required — the employee sees this)
                        </label>
                        {/* Quick preset (Unami ask #7): decline based on pending work. */}
                        <button type="button"
                                onClick={() => setRejectNote('Declined for now — you have open work due during these dates. Please re-apply once it is cleared or handed over.')}
                                className="mb-1.5 inline-flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-semibold"
                                style={{ background: theme.g100, color: theme.t2, border: `1px solid ${theme.cardBdr}` }}>
                          <AlertCircle className="w-3 h-3" /> Clashes with pending work
                        </button>
                        <textarea value={rejectNote} onChange={e => setRejectNote(e.target.value)} rows={2} autoFocus
                                  placeholder="e.g. Clashes with month-end close — please re-apply for the following week."
                                  className="w-full px-2.5 py-1.5 rounded-md text-sm outline-none resize-y"
                                  style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }} />
                        <div className="flex items-center justify-end gap-2 mt-2">
                          <button type="button" onClick={() => { setRejectingId(null); setRejectNote('') }}
                                  className="px-2.5 py-1 rounded-md text-xs font-semibold" style={{ color: theme.t2 }}>
                            Cancel
                          </button>
                          <button type="button" onClick={() => decide(row.id, 'reject', rejectNote.trim())}
                                  disabled={decideBusy === row.id || !rejectNote.trim()}
                                  className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-semibold transition-opacity disabled:opacity-50"
                                  style={{ background: theme.er, color: '#fff' }}>
                            {decideBusy === row.id ? <Loader2 className="w-3 h-3 animate-spin" /> : <X className="w-3 h-3" />}
                            Confirm rejection
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
            {decideMsg && (
              <div className="mt-3 rounded-lg px-3 py-2 text-sm flex items-start gap-2"
                   style={{
                     background: decideMsg.ok ? theme.okB : theme.erB,
                     color:      decideMsg.ok ? theme.ok  : theme.er,
                     border:     `1px solid ${decideMsg.ok ? theme.ok : theme.er}40`,
                   }}>
                {decideMsg.ok ? <Check className="w-4 h-4 flex-shrink-0 mt-0.5" /> :
                                <AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5" />}
                <div>{decideMsg.text}</div>
              </div>
            )}
          </div>
        )}

        {/* My past decisions — approver's own history + the reason recorded
            (bug 3af04928). */}
        {canApprove && decisions.length > 0 && (
          <div className="rounded-2xl p-5" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
            <h3 className="font-semibold inline-flex items-center gap-1.5 mb-3" style={{ color: theme.text }}>
              <Check className="w-4 h-4" style={{ color: theme.orange }} /> My past decisions
            </h3>
            <div className="space-y-2 max-h-80 overflow-y-auto">
              {decisions.map(d => {
                const approved = d.status === 'approved'
                return (
                  <div key={d.id} className="rounded-lg px-3 py-2.5" style={{ background: theme.g100 }}>
                    <div className="flex items-center justify-between gap-2">
                      <div className="text-sm min-w-0" style={{ color: theme.text }}>
                        <span className="font-semibold">{d.employee}</span>
                        <span className="ml-2 text-xs" style={{ color: theme.t2 }}>
                          {typeChip(d)} · {hasHalf(d) && d.day_breakdown
                            ? d.day_breakdown
                            : `${d.start_date} → ${d.end_date} · ${fmtDays(d.days)}d`}
                        </span>
                      </div>
                      <span className="px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wide flex-shrink-0"
                            style={{ background: approved ? theme.okB : theme.erB, color: approved ? theme.ok : theme.er }}>
                        {d.status_label}
                      </span>
                    </div>
                    <div className="flex items-center justify-between gap-2 mt-1">
                      {d.decision_notes
                        ? <p className="text-xs italic min-w-0" style={{ color: theme.t2 }}>"{d.decision_notes}"</p>
                        : <span />}
                      {d.decided_at && (
                        <span className="text-[10px] flex-shrink-0" style={{ color: theme.t2 }}>
                          {new Date(d.decided_at).toLocaleDateString('en-GB')}
                        </span>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        )}

        {/* Apply form */}
        <form onSubmit={submit} className="rounded-2xl p-5 space-y-4"
              style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
          <div className="flex items-center gap-2">
            <CalendarIcon className="w-4 h-4" style={{ color: theme.orange }} />
            <h3 className="font-semibold" style={{ color: theme.text }}>Apply for {selected?.name || 'leave'}</h3>
          </div>

          {/* Balance AT THE POINT OF APPLYING (CFO 2026-09-02). The grid at the
              top scrolls out of view by the time someone reaches the dates and
              Submit, so a new employee only learned they had 0 days from the red
              error at submit (Snehal, 2 Sep). Show the selected type's balance
              here, plus Annual + Sick always in view — the two the CFO named. */}
          <div className="rounded-xl p-3.5"
               style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}` }}>
            <div className="flex items-baseline justify-between gap-3 flex-wrap">
              <div>
                <span className="text-xs" style={{ color: theme.t2 }}>
                  {selected?.name || 'This leave'} available now
                </span>
                <div className="flex items-baseline gap-1.5 mt-0.5">
                  <span className="text-2xl font-bold tabular-nums"
                        style={{ color: loadingBal ? theme.t2
                                 : selAvail <= 0 ? theme.er
                                 : notEnough ? theme.er : theme.ok }}>
                    {loadingBal ? '—' : fmtDays(selAvail)}
                  </span>
                  <span className="text-sm" style={{ color: theme.t2 }}>
                    / {selected ? selected.days : 0} days
                  </span>
                </div>
              </div>
              {/* Annual + Sick quick-reference — always visible while applying,
                  whatever type is selected. */}
              <div className="flex items-center gap-2">
                {[['Annual', annualBal], ['Sick', sickBal]].map(([label, b]) => {
                  const bal = b as LeaveBalance | undefined
                  const av = bal ? (bal.available ?? bal.remaining) : 0
                  return (
                    <div key={label as string}
                         className="rounded-lg px-2.5 py-1.5 text-center"
                         style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
                      <div className="text-[10px] uppercase tracking-wide font-semibold"
                           style={{ color: theme.t2 }}>{label as string}</div>
                      <div className="text-sm font-bold tabular-nums"
                           style={{ color: loadingBal ? theme.t2 : av <= 0 ? theme.er : theme.text }}>
                        {loadingBal ? '—' : `${fmtDays(av)}d`}
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
            {/* Why annual can read 0 for a new joiner — plain English, so the
                number isn't mistaken for a mistake (matches CoS §7.5.1). */}
            {/* The monthly-credit explanation is an ANNUAL-leave fact (CoS
                §7.5.1), so gate it on the annual type itself, not the generic
                `accrues` flag — if another accruing type is ever added, this
                wording must not misstate its rule (panel L13, 2026-09-02). */}
            {type === 'annual' && selAvail <= 0 && (
              <p className="text-[11px] mt-2" style={{ color: theme.t2 }}>
                Annual leave is credited at the end of each month, so your balance
                rises when the month closes.
              </p>
            )}
          </div>

          {/* Heads-up BEFORE Submit if the request is bigger than the balance,
              instead of the red error after (🟢 CFO 2026-09-02). The server is
              still the exact gate; this is an early, friendly warning only. */}
          {notEnough && (
            <div className="rounded-xl p-3 flex items-start gap-2 text-sm"
                 style={{ background: theme.erB, border: `1px solid ${theme.er}33`, color: theme.er }}>
              <Info className="w-4 h-4 flex-shrink-0 mt-0.5" />
              <div>
                You're requesting about <strong>{fmtDays(reqDays!)} day{reqDays === 1 ? '' : 's'}</strong>,
                but only <strong>{fmtDays(selAvail)}</strong> of {selected?.name || 'this leave'} {selAvail === 1 ? 'is' : 'are'} available now.
                {type === 'annual'
                  ? ' Annual leave is credited monthly, so waiting until the month closes gives you more.'
                  : ' You can still apply — your manager decides.'}
              </div>
            </div>
          )}

          {/* Say it BEFORE the form is filled in, not after (CFO 2026-08-07).
              Renders nothing when the person has no overdue work. */}
          <OverdueBanner what="leave" />

          {/* Discretionary leave — say it at the TOP, before a single date is
              picked (CFO 2026-09-10). This is the whole point of the change:
              nobody should reach the Submit button thinking this works like
              annual leave. */}
          {policy && (
            <div className="rounded-xl p-4"
                 style={{ background: theme.erB, border: `2px solid ${theme.er}` }}>
              <div className="flex items-start gap-2">
                <AlertTriangle className="w-5 h-5 flex-shrink-0 mt-0.5" style={{ color: theme.er }} />
                <div>
                  <p className="font-bold text-sm mb-1" style={{ color: theme.er }}>
                    {policy.notice_title}
                  </p>
                  <p className="text-[13px] leading-relaxed" style={{ color: theme.text }}>
                    {policy.notice_body}
                  </p>
                  {typeof policy.annual_available === 'number' && (
                    <p className="text-[13px] mt-2 font-semibold" style={{ color: theme.text }}>
                      You currently have {fmtDays(policy.annual_available)} day(s) of annual
                      leave available.
                    </p>
                  )}
                  {policy.history && policy.history.count > 0 && (
                    <p className="text-[13px] mt-1" style={{ color: theme.text }}>
                      Your record shows {policy.history.count} request(s) of this type in the
                      last {policy.history.months} months ({fmtDays(policy.history.days)} day(s)).
                      Your manager and the CFO see this too.
                    </p>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* Everything wrong with the form, in one list, so it is fixed in one
              pass instead of being drip-fed one error at a time. */}
          {policyErrors.length > 0 && (
            <div className="rounded-xl p-3 text-sm"
                 style={{ background: theme.erB, border: `1px solid ${theme.er}`, color: theme.er }}>
              <p className="font-semibold mb-1">Still needed before this can be sent:</p>
              <ul className="list-disc pl-5 space-y-0.5">
                {policyErrors.map((e, i) => <li key={i}>{e}</li>)}
              </ul>
            </div>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium mb-1" style={{ color: theme.t2 }}>Start</label>
              <input type="date" value={start} onChange={e => setStart(e.target.value)}
                     className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                     style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }} />
            </div>
            <div>
              <label className="block text-xs font-medium mb-1" style={{ color: theme.t2 }}>
                End{isMaternity ? ' · locked (98 statutory days)' : ''}
              </label>
              <input type="date" value={isMaternity ? maternityEnd : end}
                     onChange={e => setEnd(e.target.value)}
                     disabled={isMaternity}
                     className="w-full px-3 py-2 rounded-lg text-sm outline-none disabled:opacity-60"
                     style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }} />
              {isMaternity && (
                <p className="text-xs mt-1" style={{ color: theme.t2 }}>
                  Set automatically to 98 calendar days from the start date (ELRA s.222). Not editable.
                </p>
              )}
            </div>
          </div>

          {/* Half-day support (Kago Tshutlhedi 2026-07-13). Off by default; when
              ticked, each boundary picks Full / AM / PM. Same start+end date →
              a single "Day type" dropdown. Hidden for maternity (calendar-day). */}
          {canHalfDay && (
            <div>
              <label className="inline-flex items-center gap-2 text-sm cursor-pointer select-none"
                     style={{ color: theme.text }}>
                <input type="checkbox" checked={halfDay}
                       onChange={e => setHalfDay(e.target.checked)}
                       className="w-4 h-4 accent-[#F07F00]" />
                Include half day(s)
              </label>
              {halfDay && (
                <>
                  <div className={`mt-3 grid grid-cols-1 gap-4 ${sameDate ? '' : 'sm:grid-cols-2'}`}>
                    {sameDate ? (
                      <div>
                        <label className="block text-xs font-medium mb-1" style={{ color: theme.t2 }}>Day type</label>
                        <select value={startDayType} onChange={e => setStartDayType(e.target.value as DayType)}
                                className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                                style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }}>
                          {DAY_TYPE_OPTIONS.map(o => <option key={o.v} value={o.v}>{o.label}</option>)}
                        </select>
                      </div>
                    ) : (
                      <>
                        <div>
                          <label className="block text-xs font-medium mb-1" style={{ color: theme.t2 }}>Start day</label>
                          <select value={startDayType} onChange={e => setStartDayType(e.target.value as DayType)}
                                  className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                                  style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }}>
                            {DAY_TYPE_OPTIONS.map(o => <option key={o.v} value={o.v}>{o.label}</option>)}
                          </select>
                        </div>
                        <div>
                          <label className="block text-xs font-medium mb-1" style={{ color: theme.t2 }}>End day</label>
                          <select value={endDayType} onChange={e => setEndDayType(e.target.value as DayType)}
                                  className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                                  style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }}>
                            {DAY_TYPE_OPTIONS.map(o => <option key={o.v} value={o.v}>{o.label}</option>)}
                          </select>
                        </div>
                      </>
                    )}
                  </div>
                  <p className="text-[11px] mt-2" style={{ color: theme.t2 }}>
                    A half day counts as 0.5 days. Weekends and public holidays are excluded first.
                  </p>
                </>
              )}
            </div>
          )}

          {/* Choose-your-manager (CFO 2026-07-15): who reviews this request —
              restricted to genuine people-managers, pre-selected to the
              caller's own line manager when listed. */}
          {managers.length > 0 && (
            <div>
              <label className="block text-xs font-medium mb-1" style={{ color: theme.t2 }}>Who should review this?</label>
              <select value={approverId} onChange={e => setApproverId(e.target.value)}
                      className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                      style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }}>
                <option value="">Select a manager…</option>
                {managers.map(m => (
                  <option key={m.id} value={m.id}>{m.name}{m.department ? ` — ${m.department}` : ''}</option>
                ))}
              </select>
            </div>
          )}

          {/* No approver available for this employee's company — say so, rather
              than silently hiding the picker (CFO 2026-07-25). */}
          {managers.length === 0 && managersEmptyReason && (
            <div className="rounded-lg px-3 py-2 text-xs"
                 style={{ background: theme.g100, border: '1px solid #F59E0B', color: theme.text }}>
              {managersEmptyReason}
            </div>
          )}

          {/* Reason (Unami 2026-07-27): a fixed category, not a forced "why".
              An employee is not obliged to disclose the reason for their leave,
              so the category is required but the free-text note is optional. */}
          <div>
            <label className="block text-xs font-medium mb-1" style={{ color: theme.t2 }}>
              Reason <span className="font-semibold" style={{ color: theme.er }}>· required</span>
            </label>
            <select value={reasonCategory} onChange={e => setReasonCategory(e.target.value)}
                    className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                    style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }}>
              <option value="">Select a reason…</option>
              {REASON_CATEGORIES.map(o => <option key={o.v} value={o.v}>{o.label}</option>)}
            </select>
            {!policy && (
              <>
                <textarea value={reason} onChange={e => setReason(e.target.value)} rows={2}
                          placeholder="Anything to add? (optional — you do not have to explain why)"
                          className="w-full mt-2 px-3 py-2 rounded-lg text-sm outline-none resize-y"
                          style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text }} />
                <p className="text-[11px] mt-1" style={{ color: theme.t2 }}>
                  You are not required to give details — the category is enough.
                </p>
              </>
            )}
          </div>

          {/* ── The discretionary questions (CFO 2026-09-10) ───────────────
              Rendered from the server's own list, so a question added in
              hris/discretionary_leave.py appears here with no second edit and
              can never be on screen without being checked. */}
          {policy && (
            <div className="rounded-xl p-4 space-y-4"
                 style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}` }}>
              <p className="text-sm font-bold" style={{ color: theme.text }}>
                Answer every question. All of them are compulsory.
              </p>

              {policy.questions.map(q => {
                // A follow-up question stays hidden until its trigger is picked.
                if (q.depends_on && !Object.entries(q.depends_on)
                  .every(([k, v]) => (policyAnswers[k] || '').trim() === v)) return null
                const val = policyAnswers[q.key] || ''
                const set = (v: string) => setPolicyAnswers(a => ({ ...a, [q.key]: v }))
                const short = q.min_words > 0 && wordCount(val) < q.min_words
                const boxStyle = {
                  background: theme.card, border: `1px solid ${theme.cardBdr}`, color: theme.text,
                }
                return (
                  <div key={q.key}>
                    <label className="block text-xs font-semibold mb-1" style={{ color: theme.text }}>
                      {q.label} <span style={{ color: theme.er }}>·  required</span>
                    </label>
                    {q.help && (
                      <p className="text-[11px] mb-1" style={{ color: theme.t2 }}>{q.help}</p>
                    )}
                    {q.kind === 'choice' ? (
                      <select value={val} onChange={e => set(e.target.value)}
                              className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                              style={boxStyle}>
                        <option value="">Select…</option>
                        {q.options.map(o => <option key={o} value={o}>{o}</option>)}
                      </select>
                    ) : q.kind === 'date' ? (
                      <input type="date" value={val} onChange={e => set(e.target.value)}
                             className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                             style={boxStyle} />
                    ) : q.kind === 'longtext' ? (
                      <>
                        <textarea value={val} onChange={e => set(e.target.value)} rows={3}
                                  className="w-full px-3 py-2 rounded-lg text-sm outline-none resize-y"
                                  style={boxStyle} />
                        <p className="text-[11px] mt-1"
                           style={{ color: short ? theme.er : theme.t2 }}>
                          {wordCount(val)} / {q.min_words} words
                        </p>
                      </>
                    ) : (
                      <input type="text" value={val} onChange={e => set(e.target.value)}
                             className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                             style={boxStyle} />
                    )}
                  </div>
                )
              })}

              {/* The 50-word motivation. This IS the `reason` field — the
                  optional note above is hidden for these types so there are
                  never two boxes asking the same thing. */}
              <div>
                <label className="block text-xs font-semibold mb-1" style={{ color: theme.text }}>
                  Your motivation — at least {policy.min_words} words
                  <span style={{ color: theme.er }}> ·  required</span>
                </label>
                <p className="text-[11px] mb-1" style={{ color: theme.t2 }}>
                  In your own words: what has happened, why you need these exact days,
                  and why the work cannot wait or be done another way.
                </p>
                <textarea value={reason} onChange={e => setReason(e.target.value)} rows={6}
                          className="w-full px-3 py-2 rounded-lg text-sm outline-none resize-y"
                          style={{ background: theme.card, border: `1px solid ${theme.cardBdr}`, color: theme.text }} />
                <p className="text-[11px] mt-1"
                   style={{ color: wordCount(reason) < (policy.min_words || 50) ? theme.er : theme.t2 }}>
                  {wordCount(reason)} / {policy.min_words} words
                </p>
              </div>

              {/* The undertaking. Ticking it is what makes an absence before
                  approval unambiguously unauthorised. */}
              <label className="flex items-start gap-2 text-sm cursor-pointer select-none rounded-lg p-3"
                     style={{ background: theme.erB, border: `1px solid ${theme.er}66`, color: theme.text }}>
                <input type="checkbox" checked={policyAck}
                       onChange={e => setPolicyAck(e.target.checked)}
                       className="w-4 h-4 mt-0.5 accent-[#F07F00]" />
                <span>{policy.ack_text}</span>
              </label>
            </div>
          )}

          {/* Medical certificate uploader (sick only — required) */}
          <div>
            <label className="block text-xs font-medium mb-1" style={{ color: theme.t2 }}>
              {policy ? policy.proof_label : 'Medical certificate'}
              {(type === 'sick' || policy?.proof_required) && (
                <span className="font-semibold" style={{ color: theme.er }}> · required</span>
              )}
            </label>
            <input
              ref={certInputRef}
              type="file"
              accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png"
              onChange={e => setCertificate(e.target.files?.[0] || null)}
              className="w-full text-sm file:mr-3 file:px-3 file:py-1.5 file:rounded-md file:border-0 file:text-xs file:font-semibold file:cursor-pointer"
              style={{ color: theme.t2 }}
            />
            {certificate && (
              <p className="text-xs mt-1.5 inline-flex items-center gap-1.5" style={{ color: theme.t2 }}>
                <Paperclip className="w-3 h-3" /> {certificate.name} · {(certificate.size / 1024).toFixed(0)} KB
              </p>
            )}
            {type !== 'sick' && !policy?.proof_required && (
              <p className="text-[11px] mt-1" style={{ color: theme.t2 }}>
                {policy
                  ? 'Attach it now if you already have it. If not, say in the question above what you will provide and by when. PDF / JPG / PNG. Max 5 MB.'
                  : 'Optional for non-sick leave. PDF / JPG / PNG. Max 5 MB.'}
              </p>
            )}
          </div>

          {/* Bug (Oprah Mogomotsi 2026-06-22): the fixed Aria orb (bottom-right,
              ~94px corner footprint) visually overlaps the right edge of the
              right-aligned Submit button, clipping its label to "Submit appli…".
              The `pb-28` on <main> gives vertical clearance; `pr-24` here gives
              the horizontal clearance so the button stays clear of the orb at
              all viewports >= 987px. Same class of fix as RECON-001 / BUG-014. */}
          <div className="flex items-center justify-between gap-4 pr-24">
            <p className="text-xs" style={{ color: theme.t2 }}>
              <Info className="w-3.5 h-3.5 inline mr-1" />
              Manager approval follows the existing OneDesk workflow.
            </p>
            <button type="submit" disabled={busy}
                    className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold transition-opacity disabled:opacity-50"
                    style={{ background: theme.orange, color: '#fff' }}>
              <Send className="w-4 h-4" />
              {busy ? 'Submitting…' : 'Submit application'}
            </button>
          </div>

          {status === 'queued' && msg && (
            <div className="rounded-lg px-3 py-2 text-sm flex items-start gap-2"
                 style={{ background: theme.okB, color: theme.ok, border: `1px solid ${theme.ok}40` }}>
              <FileText className="w-4 h-4 flex-shrink-0 mt-0.5" />
              <div>{msg}</div>
            </div>
          )}
          {status === 'error' && msg && (
            <div className="rounded-lg px-3 py-2 text-sm flex items-start gap-2"
                 style={{ background: theme.erB, color: theme.er, border: `1px solid ${theme.er}40` }}>
              <AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5" />
              <div>{msg}</div>
            </div>
          )}
        </form>
      </main>

      {/* The application WAS accepted — this explains what happens next and
          names the work holding it up (CFO 2026-08-07). */}
      {overdue && <OverdueModal info={overdue} onClose={() => setOverdue(null)} />}
    </div>
  )
}
