'use client'

/**
 * /hris/disciplinary — Disciplinary cases (confidential HR record).
 *
 * A manager (or HR) raises a case against an employee describing the
 * allegation and a proposed sanction. The case runs a short chain:
 *   raise → HR review → (CFO sign-off if suspension / dismissal) → issued.
 *
 * The allegation must be at least 50 words so the record stands on its own
 * — a live counter gates the raise button. Only HR / the CFO can act on a
 * case; the backend is the real gate, this page only surfaces the buttons.
 * Suspension and dismissal recommendations additionally need CFO sign-off.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { ShieldAlert, ChevronLeft, Check, Clock, Lock, Paperclip, FileText, X } from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { authedHrisFetch } from '../_shared'
import { localYmd } from '@/lib/utils'

const NAVY = '#1D3270'
const ORANGE = '#F47C20'
const MIN_WORDS = 50

interface Subject { id: number | string; name: string; department: string }
interface Me { can_raise: boolean; can_view_all: boolean; is_hr: boolean; is_cfo: boolean }
interface Attachment {
  id: string
  filename: string
  content_type: string
  size: number
  uploaded_by: string | null
  uploaded_at: string | null
  url: string
}
interface Case {
  id: string
  subject: string
  category: string
  category_label: string
  incident_date: string
  allegation: string
  proposed_action: string
  status: 'pending_hr' | 'pending_response' | 'pending_cfo' | 'issued' | 'rejected'
  status_label: string
  needs_cfo: boolean
  raised_by: string
  hr_reviewed_at: string | null
  cfo_approved_at: string | null
  issued_at: string | null
  rejected_stage: string | null
  override_reason?: string | null
  was_overridden?: boolean
  can_cfo_override?: boolean
  decision_notes: string | null
  created_at: string | null
  can_act: boolean
  can_attach: boolean
  attachments: Attachment[]
  // Natural justice (CFO 2026-08-11): the invitation to respond + the answer.
  inquiry_issued_at: string | null
  inquiry_sent_to: string
  response_deadline: string | null
  employee_response: string
  employee_responded_at: string | null
  deadline_passed: boolean
  natural_justice_satisfied: boolean
  can_issue_inquiry: boolean
  subject_email: string
  response_self_submitted: boolean
  response_recorded_by: string | null
  response_after_decision: boolean
  issued_without_being_heard: boolean
  unheard_issue_reason: string
  unheard_issued_by: string | null
}

// Sanction ladder — value maps 1:1 to the backend `category` field.
const CATEGORIES: { value: string; label: string }[] = [
  { value: 'verbal', label: 'Verbal warning' },
  { value: 'written', label: 'Written warning' },
  { value: 'final', label: 'Final written warning' },
  { value: 'suspension', label: 'Suspension' },
  { value: 'dismissal', label: 'Dismissal recommendation' },
]

const STATUS_CHIP: Record<string, string> = {
  pending_hr: 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300',
  pending_response: 'bg-sky-100 text-sky-800 dark:bg-sky-900/40 dark:text-sky-300',
  pending_cfo: 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300',
  issued: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300',
  rejected: 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300',
}

// Live word count — same rule the raise gate uses (CFO: 50-word minimum).
const countWords = (s: string) => s.trim().split(/\s+/).filter(Boolean).length
const fmtDate = (s?: string | null) => (s ? s.slice(0, 10) : '—')
const fmtSize = (n: number) => {
  if (!n) return '0 B'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}
// Evidence types the backend accepts (kept in sync with ALLOWED_EVIDENCE_EXTS).
const EVIDENCE_ACCEPT = '.pdf,.doc,.docx,.png,.jpg,.jpeg,.gif,.webp,.eml,.msg,.txt'

export default function DisciplinaryPage() {
  const [me, setMe] = useState<Me | null>(null)
  const [cases, setCases] = useState<Case[]>([])
  const [subjects, setSubjects] = useState<Subject[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')

  // raise form
  const [subjectId, setSubjectId] = useState('')
  const [category, setCategory] = useState('')
  const [incidentDate, setIncidentDate] = useState('')
  const [allegation, setAllegation] = useState('')
  const [proposedAction, setProposedAction] = useState('')
  const [formErr, setFormErr] = useState('')

  const load = useCallback(() => {
    authedHrisFetch('/hris/api/disciplinary/')
      .then(async r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then(d => {
        setMe(d.me); setCases(d.cases || []); setSubjects(d.subjects || []); setError('')
      })
      .catch(e => setError(String(e?.message || e)))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  const words = countWords(allegation)
  const canSubmit = words >= MIN_WORDS && !!subjectId && !!category && !!incidentDate && busy !== 'raise'

  const raise = async () => {
    setFormErr(''); setBusy('raise')
    try {
      const r = await authedHrisFetch('/hris/api/disciplinary/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          subject_employee_id: subjectId,
          category,
          incident_date: incidentDate,
          allegation,
          proposed_action: proposedAction,
        }),
      })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) { setFormErr(d.detail || `HTTP ${r.status}`); return }
      setSubjectId(''); setCategory(''); setIncidentDate(''); setAllegation(''); setProposedAction(''); load()
    } finally { setBusy('') }
  }

  const action = async (id: string, path: string, body: object = {}) => {
    setBusy(id + path)
    try {
      const r = await authedHrisFetch(`/hris/api/disciplinary/${id}/${path}/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        alert(d.detail || `Action failed (HTTP ${r.status})`)
      }
      load()
    } finally { setBusy('') }
  }

  const rejectWithReason = (id: string) => {
    const notesIn = window.prompt('Reason for rejection:') ?? null
    if (notesIn === null) return
    action(id, 'reject', { notes: notesIn })
  }

  // Natural justice (CFO 2026-08-11): serving the inquiry asks the employee for
  // their side, so confirm the deadline before the letter goes out.
  const issueInquiry = (c: Case) => {
    const suggested = localYmd(new Date(Date.now() + 5 * 86400_000))
    const deadline = window.prompt(
      `Ask ${c.subject} for their side of this.\n\n` +
      `They must answer by (YYYY-MM-DD, at least 2 days out):`,
      suggested) ?? null
    if (deadline === null) return
    // Emailing is the DEFAULT because an invitation nobody sees is not a hearing.
    // Raising it in omni only is offered for when the employee has already been
    // spoken to elsewhere (CFO instruction 2026-08-12).
    const sendEmail = window.confirm(
      `Email the letter to ${c.subject_email || '(no email on their record)'}?\n\n` +
      `OK = email it, and show it in omni\n` +
      `Cancel = show it in omni ONLY, send nothing`)
    action(c.id, 'issue-inquiry', { deadline: deadline.trim(), send_email: sendEmail })
  }

  // HR captures a reply that came in outside omni (emailed, or a signed letter),
  // so it stops living in a mailbox. Recorded as HR's transcription, not as the
  // employee's own submission — the record shows the difference.
  const recordResponse = (c: Case) => {
    const text = window.prompt(
      `Record the response ${c.subject} gave outside omni (emailed, or a letter handed in).\n\n` +
      `It goes on the case marked as recorded by you, not as their own submission.\n\n` +
      `Their words:`) ?? null
    if (text === null || !text.trim()) return
    const received = window.prompt(
      'Date it was received (YYYY-MM-DD). Leave as-is for today:',
      localYmd(new Date())) ?? null
    if (received === null) return
    action(c.id, 'record-response', { response: text, received_on: received.trim() })
  }

  // Things I can act on float to the top; everything else drops below.
  // A case still needing its inquiry served is actionable too — that IS the work.
  const actionable = cases.filter(c => c.can_act || c.can_issue_inquiry)
  const rest = cases.filter(c => !(c.can_act || c.can_issue_inquiry))

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-950">
      <TopBar />
      <main className="mx-auto max-w-5xl px-4 py-6">
        <div className="mb-1 flex items-center gap-3">
          <Link href="/hris" className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-gray-800 dark:hover:text-gray-200">
            <ChevronLeft className="h-4 w-4" /> HRIS
          </Link>
          <h1 className="flex items-center gap-2 text-xl font-semibold" style={{ color: NAVY }}>
            <ShieldAlert className="h-5 w-5" style={{ color: ORANGE }} /> Disciplinary
          </h1>
        </div>
        <p className="mb-3 text-sm text-gray-500 dark:text-gray-400">
          Raise and track disciplinary cases. Every case is reviewed by HR before it&apos;s
          issued; suspension and dismissal go through a further internal review first.
        </p>
        <div className="mb-6 inline-flex items-center gap-2 rounded-lg border border-gray-200 bg-gray-50 px-3 py-1.5 text-xs text-gray-500 dark:border-gray-800 dark:bg-gray-900 dark:text-gray-400">
          <Lock className="h-3.5 w-3.5" /> Confidential HR record — visible only to authorised HR and the CFO.
        </div>

        {loading && (
          <div className="rounded-xl border border-gray-200 bg-white p-6 text-sm text-gray-500 dark:border-gray-800 dark:bg-gray-900">Loading…</div>
        )}
        {!loading && error && (
          <div className="rounded-xl border border-red-200 bg-red-50 p-6 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
            Could not load disciplinary cases ({error}).
          </div>
        )}

        {/* ── Raise a case (managers / HR) ───────────────────────────────── */}
        {!loading && me?.can_raise && (
          <div className="mb-8 overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm dark:border-gray-800 dark:bg-gray-900">
            <div className="px-5 py-3 text-white" style={{ background: NAVY }}>
              <h2 className="flex items-center gap-2 text-base font-semibold">
                <ShieldAlert className="h-4 w-4" /> Raise a disciplinary case
              </h2>
            </div>
            <div className="p-5">
              <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
                <label className="text-sm">
                  <span className="mb-1 block text-xs font-medium text-gray-500">Employee</span>
                  <select
                    value={subjectId} onChange={e => setSubjectId(e.target.value)}
                    className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-[#F47C20] focus:outline-none dark:border-gray-700 dark:bg-gray-950 dark:text-gray-100"
                  >
                    <option value="">Select employee…</option>
                    {subjects.map(s => (
                      <option key={s.id} value={String(s.id)}>
                        {s.name}{s.department ? ` · ${s.department}` : ''}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="text-sm">
                  <span className="mb-1 block text-xs font-medium text-gray-500">Category</span>
                  <select
                    value={category} onChange={e => setCategory(e.target.value)}
                    className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-[#F47C20] focus:outline-none dark:border-gray-700 dark:bg-gray-950 dark:text-gray-100"
                  >
                    <option value="">Select category…</option>
                    {CATEGORIES.map(c => (
                      <option key={c.value} value={c.value}>{c.label}</option>
                    ))}
                  </select>
                </label>
                <label className="text-sm">
                  <span className="mb-1 block text-xs font-medium text-gray-500">Incident date</span>
                  <input
                    type="date" value={incidentDate} onChange={e => setIncidentDate(e.target.value)}
                    className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-[#F47C20] focus:outline-none dark:border-gray-700 dark:bg-gray-950 dark:text-gray-100"
                  />
                </label>
              </div>

              <label className="block text-sm">
                <span className="mb-1 block text-xs font-medium text-gray-500">Allegation (min. 50 words)</span>
                <textarea
                  value={allegation} onChange={e => setAllegation(e.target.value)} rows={5}
                  placeholder="Set out what happened, when, who was involved, and the rule or policy breached."
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-[#F47C20] focus:outline-none dark:border-gray-700 dark:bg-gray-950 dark:text-gray-100"
                />
              </label>
              <div className={`mt-1 text-xs font-medium ${words >= MIN_WORDS ? 'text-emerald-600 dark:text-emerald-400' : 'text-gray-400'}`}>
                {words} / {MIN_WORDS} words
              </div>

              <label className="mt-3 block text-sm">
                <span className="mb-1 block text-xs font-medium text-gray-500">Proposed action (optional)</span>
                <textarea
                  value={proposedAction} onChange={e => setProposedAction(e.target.value)} rows={2}
                  placeholder="e.g. Written warning with a 6-month review."
                  className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-[#F47C20] focus:outline-none dark:border-gray-700 dark:bg-gray-950 dark:text-gray-100"
                />
              </label>

              <div className="mt-4 flex flex-wrap items-center gap-3">
                <button
                  type="button" disabled={!canSubmit} onClick={raise}
                  className="rounded-lg px-5 py-2 text-sm font-semibold text-white disabled:opacity-50"
                  style={{ background: ORANGE }}
                >
                  Raise case
                </button>
                <p className="text-xs text-gray-500">Suspension and dismissal go through a further internal review before they&apos;re issued.</p>
              </div>
              {formErr && (
                <div className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950 dark:text-red-300">{formErr}</div>
              )}
            </div>
          </div>
        )}

        {/* ── Cases waiting on me ─────────────────────────────────────────── */}
        {actionable.length > 0 && (
          <Section title="Waiting on you">
            {actionable.map(c => <Card key={c.id} c={c} busy={busy} onAdvance={action} onReject={rejectWithReason} onChanged={load} onIssueInquiry={issueInquiry} onRecordResponse={recordResponse} />)}
          </Section>
        )}

        {/* ── Everything else (empty state when there are no cases at all) ── */}
        {(rest.length > 0 || cases.length === 0) && (
          <Section title={actionable.length > 0 ? 'Other cases' : 'Cases'}>
            {cases.length === 0 && !loading && (
              <div className="rounded-xl border border-gray-200 bg-white p-5 text-sm text-gray-500 dark:border-gray-800 dark:bg-gray-900">
                No disciplinary cases yet.
              </div>
            )}
            {rest.map(c => <Card key={c.id} c={c} busy={busy} onAdvance={action} onReject={rejectWithReason} onChanged={load} onIssueInquiry={issueInquiry} onRecordResponse={recordResponse} />)}
          </Section>
        )}
      </main>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mb-8">
      <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-gray-500">{title}</h3>
      <div className="space-y-4">{children}</div>
    </section>
  )
}

function StepTrack({ c }: { c: Case }) {
  const rejected = c.status === 'rejected'
  const steps: { key: string; label: string; done: boolean; current: boolean }[] = [
    // Natural justice: the employee is heard BEFORE anything is decided.
    { key: 'inquiry', label: 'Employee asked', done: !!c.inquiry_issued_at,
      current: !rejected && !c.inquiry_issued_at },
    { key: 'response', label: c.employee_responded_at ? 'Employee answered'
        : c.deadline_passed ? 'No answer given' : 'Awaiting answer',
      done: !!c.employee_responded_at || (!!c.inquiry_issued_at && c.deadline_passed),
      current: !rejected && c.status === 'pending_response' && !c.deadline_passed },
    { key: 'hr', label: 'HR review', done: !!c.hr_reviewed_at,
      current: !rejected && c.status === 'pending_hr' && c.natural_justice_satisfied },
    ...(c.needs_cfo
      ? [{ key: 'cfo', label: 'Final review', done: !!c.cfo_approved_at, current: !rejected && c.status === 'pending_cfo' }]
      : []),
    { key: 'issued', label: 'Issued', done: !!c.issued_at, current: false },
  ]
  return (
    <div className="mt-3 flex flex-wrap items-center gap-1.5 text-xs">
      {steps.map((s, i) => (
        <span key={s.key} className="flex items-center gap-1.5">
          <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 font-medium ${
            s.done ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300'
            : s.current ? 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300'
            : 'bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400'}`}>
            {s.done ? <Check className="h-3 w-3" /> : s.current ? <Clock className="h-3 w-3" /> : null}
            {s.label}{s.done ? ' ✓' : ''}
          </span>
          {i < steps.length - 1 && <span className="text-gray-300">→</span>}
        </span>
      ))}
      {rejected && (
        <>
          <span className="text-gray-300">→</span>
          <span className="rounded-full bg-red-100 px-2.5 py-1 font-medium text-red-700 dark:bg-red-900/40 dark:text-red-300">Rejected</span>
        </>
      )}
    </div>
  )
}

function Card({ c, busy, onAdvance, onReject, onChanged, onIssueInquiry, onRecordResponse }: {
  c: Case; busy: string
  onAdvance: (id: string, path: string) => void
  onReject: (id: string) => void
  onChanged: () => void
  onIssueInquiry: (c: Case) => void
  onRecordResponse: (c: Case) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const long = c.allegation.length > 220
  const shown = expanded || !long ? c.allegation : `${c.allegation.slice(0, 220).trimEnd()}…`
  const advance = c.status === 'pending_hr'
    ? { path: 'hr-review', label: 'HR review →' }
    : c.status === 'pending_cfo'
      ? { path: 'cfo-signoff', label: 'Final review →' }
      : null

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm dark:border-gray-800 dark:bg-gray-900">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="text-base font-semibold text-gray-900 dark:text-gray-100">
            {c.subject}
            <span className="ml-2 text-sm font-normal text-gray-500">{c.category_label}</span>
          </div>
          <div className="mt-0.5 text-xs text-gray-500">
            Incident {fmtDate(c.incident_date)} · raised by {c.raised_by}
            {c.created_at ? ` · logged ${fmtDate(c.created_at)}` : ''}
            {c.needs_cfo ? ' · needs final review' : ''}
          </div>
        </div>
        <span className={`rounded-full px-3 py-1 text-xs font-medium ${STATUS_CHIP[c.status] || STATUS_CHIP.pending_hr}`}>
          {c.status_label}
        </span>
      </div>

      <p className="mt-3 whitespace-pre-line text-sm text-gray-700 dark:text-gray-300">{shown}</p>
      {long && (
        <button type="button" onClick={() => setExpanded(e => !e)}
          className="mt-1 text-xs font-medium hover:underline" style={{ color: NAVY }}>
          {expanded ? 'Show less' : 'Show more'}
        </button>
      )}

      {c.proposed_action && (
        <div className="mt-2 text-xs text-gray-500">Proposed action: {c.proposed_action}</div>
      )}
      {c.decision_notes && c.status !== 'rejected' && (
        <div className="mt-1 text-xs text-gray-500">Decision notes: {c.decision_notes}</div>
      )}
      {c.status === 'rejected' && (
        <div className="mt-1 text-xs text-red-600 dark:text-red-400">
          Rejected{c.rejected_stage ? ` at ${c.rejected_stage}` : ''}{c.decision_notes ? `: ${c.decision_notes}` : ''}
        </div>
      )}
      {/* Overturned: both sides stay on the record, the rejection is not hidden. */}
      {c.was_overridden && (
        <div className="mt-2 rounded-lg border border-amber-200 bg-amber-50 p-2 text-xs dark:border-amber-900 dark:bg-amber-950/40">
          <div className="font-semibold text-amber-800 dark:text-amber-300">
            Rejected at {c.rejected_stage || 'HR'}, then overturned by the CFO
          </div>
          {c.decision_notes && (
            <div className="mt-1 text-amber-900/80 dark:text-amber-200/80">
              Rejection reason: {c.decision_notes}
            </div>
          )}
          {c.override_reason && (
            <div className="mt-1 text-amber-900/80 dark:text-amber-200/80">
              Reason for overturning: {c.override_reason}
            </div>
          )}
        </div>
      )}

      {/* The permanent stamp: this outcome was recorded without the employee's
          side. Red, not amber, and never collapsed — it is the first thing a
          reader of the file should see (CFO decision 2026-08-12). */}
      {c.issued_without_being_heard && (
        <div className="mt-3 rounded-lg border-l-4 border-red-500 bg-red-50 px-3 py-2 text-xs dark:bg-red-950/40">
          <div className="font-semibold text-red-800 dark:text-red-300">
            Issued without the employee being heard
          </div>
          {c.unheard_issue_reason && (
            <div className="mt-1 text-red-800/80 dark:text-red-300/80">
              Reason given{c.unheard_issued_by ? ` by ${c.unheard_issued_by}` : ''}: {c.unheard_issue_reason}
            </div>
          )}
        </div>
      )}

      {/* ── The employee's side of it (natural justice, CFO 2026-08-11) ────── */}
      {c.employee_responded_at ? (
        <div className="mt-3 rounded-lg border-l-4 border-sky-400 bg-sky-50 px-3 py-2 dark:bg-sky-950/40">
          <div className="text-xs font-semibold text-sky-800 dark:text-sky-300">
            {c.subject}&apos;s response · {fmtDate(c.employee_responded_at)}
            {!c.response_self_submitted && c.response_recorded_by && (
              <span className="ml-1 font-normal opacity-80">
                (received outside omni, recorded by {c.response_recorded_by})
              </span>
            )}
            {c.response_after_decision && (
              <span className="ml-1 font-normal text-amber-700 dark:text-amber-400">
                — arrived after the outcome was recorded
              </span>
            )}
          </div>
          <p className="mt-1 whitespace-pre-line text-sm text-gray-700 dark:text-gray-300">{c.employee_response}</p>
        </div>
      ) : c.status === 'pending_response' ? (
        <div className={`mt-3 rounded-lg px-3 py-2 text-xs ${c.deadline_passed
          ? 'bg-amber-50 text-amber-800 dark:bg-amber-950/40 dark:text-amber-300'
          : 'bg-sky-50 text-sky-800 dark:bg-sky-950/40 dark:text-sky-300'}`}>
          {c.deadline_passed
            ? `Asked on ${fmtDate(c.inquiry_issued_at)}; no response by the ${fmtDate(c.response_deadline)} deadline. You may review the case.`
            : `Asked on ${fmtDate(c.inquiry_issued_at)} (${c.inquiry_sent_to}). Response due ${fmtDate(c.response_deadline)} — no outcome can be recorded before then.`}
        </div>
      ) : !c.inquiry_issued_at && (c.status === 'pending_hr') ? (
        <div className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-950/40 dark:text-amber-300">
          The employee has not been asked for their side yet. A warning issued without that on
          record can be challenged, so review is blocked until the inquiry is served.
        </div>
      ) : null}

      <StepTrack c={c} />

      {c.can_cfo_override && <OverturnPanel c={c} busy={busy} onChanged={onChanged} />}

      <div className="mt-4 flex flex-wrap gap-2">
        {c.can_issue_inquiry && (
          <button type="button" disabled={!!busy} onClick={() => onIssueInquiry(c)}
            className="rounded-lg px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-50"
            style={{ background: ORANGE }}>
            Ask the employee to respond →
          </button>
        )}
        {(c.status === 'pending_response' || c.status === 'issued')
          && !c.employee_responded_at && c.can_attach && (
          <button type="button" disabled={!!busy} onClick={() => onRecordResponse(c)}
            className="rounded-lg border px-3 py-1.5 text-sm font-medium disabled:opacity-50"
            style={{ borderColor: NAVY, color: NAVY }}>
            {c.status === 'issued'
              ? 'File a reply that arrived after the decision'
              : 'Record a reply received outside omni'}
          </button>
        )}
        {c.can_act && advance && (
          <button type="button" disabled={!!busy} onClick={() => onAdvance(c.id, advance.path)}
            className="rounded-lg bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50">
            {advance.label}
          </button>
        )}
        {(c.can_act || c.can_issue_inquiry) && (
          <button type="button" disabled={!!busy} onClick={() => onReject(c.id)}
            className="rounded-lg border border-red-200 px-3 py-1.5 text-sm font-medium text-red-600 hover:bg-red-50 dark:border-red-900 dark:hover:bg-red-950">
            Reject
          </button>
        )}
      </div>

      <Evidence c={c} canAct={c.can_attach} onChanged={onChanged} />
    </div>
  )
}

// Overturning a rejection (CFO 2026-08-12). Only the CFO sees this, and only on
// a rejected case. The reason is compulsory and typed into a real field — never a
// window.prompt, which cannot be styled, cannot be cancelled cleanly, and gives
// no room to write the substantive justification this decision needs.
function OverturnPanel({ c, busy, onChanged }: { c: Case; busy: string | null; onChanged: () => void }) {
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState('')
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const words = reason.trim() ? reason.trim().split(/\s+/).length : 0
  const enough = words >= 10

  // Natural justice (CFO 2026-08-12): if the employee was never asked, issuing
  // needs a SECOND, separate reason, and it is stamped on the file for good. The
  // tick starts off, so forcing is always a deliberate act.
  const unheard = !c.natural_justice_satisfied
  const [force, setForce] = useState(false)
  const [forceReason, setForceReason] = useState('')
  const forceWords = forceReason.trim() ? forceReason.trim().split(/\s+/).length : 0
  const forceOk = !unheard || (force && forceWords >= 10)

  const submit = async () => {
    setSaving(true); setErr(null)
    try {
      const res = await authedHrisFetch(`/hris/api/disciplinary/${c.id}/cfo-override/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          notes: reason.trim(),
          ...(unheard && force ? { issue_unheard_reason: forceReason.trim() } : {}),
        }),
      })
      if (!res.ok) {
        const j = await res.json().catch(() => ({}))
        throw new Error(j.detail || `Could not overturn (${res.status}).`)
      }
      setOpen(false); setReason(''); setForce(false); setForceReason(''); onChanged()
    } catch (e: any) {
      setErr(e?.message || 'Could not overturn the rejection.')
    } finally {
      setSaving(false)
    }
  }

  if (!open) {
    return (
      <div className="mt-4">
        <button type="button" disabled={!!busy} onClick={() => setOpen(true)}
          className="rounded-lg border border-amber-300 px-3 py-1.5 text-sm font-medium text-amber-700 hover:bg-amber-50 disabled:opacity-50 dark:border-amber-800 dark:text-amber-300 dark:hover:bg-amber-950">
          Overturn the rejection and issue
        </button>
      </div>
    )
  }

  return (
    <div className="mt-4 rounded-lg border border-amber-300 bg-amber-50/60 p-3 dark:border-amber-800 dark:bg-amber-950/30">
      <div className="text-sm font-semibold text-amber-900 dark:text-amber-200">
        Overturn this rejection and issue the case
      </div>
      <p className="mt-1 text-xs text-amber-900/80 dark:text-amber-200/80">
        The rejection stays on the record. Your reason is added alongside it and is the
        justification if this is ever challenged, so please be specific.
      </p>
      <textarea
        value={reason} onChange={e => setReason(e.target.value)} rows={4}
        placeholder="Why the rejection is being overturned…"
        className="mt-2 w-full rounded-lg border border-amber-300 bg-white p-2 text-sm dark:border-amber-800 dark:bg-gray-900"
      />
      <div className="mt-1 text-xs text-amber-900/70 dark:text-amber-200/70">
        {words} word{words === 1 ? '' : 's'}{enough ? '' : ' — at least 10 needed'}
      </div>
      {unheard && (
        <div className="mt-3 rounded-lg border border-red-300 bg-red-50 p-3 dark:border-red-800 dark:bg-red-950/40">
          <div className="text-xs font-semibold text-red-800 dark:text-red-300">
            {c.subject} has not been asked for their side of this.
          </div>
          <p className="mt-1 text-xs text-red-800/80 dark:text-red-300/80">
            Issuing now is the most challengeable thing that can go on a disciplinary file.
            The safer route is to close this and use &ldquo;Ask the employee to respond&rdquo; first.
          </p>
          <label className="mt-2 flex items-start gap-2 text-xs text-red-800 dark:text-red-300">
            <input type="checkbox" checked={force} onChange={e => setForce(e.target.checked)}
              className="mt-0.5" />
            <span>Issue anyway, without hearing them. This is stamped permanently on the file.</span>
          </label>
          {force && (
            <>
              <textarea
                value={forceReason} onChange={e => setForceReason(e.target.value)} rows={3}
                placeholder="Why this cannot wait for the employee's response…"
                className="mt-2 w-full rounded-lg border border-red-300 bg-white p-2 text-sm dark:border-red-800 dark:bg-gray-900"
              />
              <div className="mt-1 text-xs text-red-800/70 dark:text-red-300/70">
                {forceWords} word{forceWords === 1 ? '' : 's'}{forceWords >= 10 ? '' : ' — at least 10 needed'}
              </div>
            </>
          )}
        </div>
      )}
      {err && <div className="mt-2 text-xs text-red-600 dark:text-red-400">{err}</div>}
      <div className="mt-2 flex gap-2">
        <button type="button" disabled={!enough || !forceOk || saving} onClick={submit}
          className="rounded-lg bg-amber-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-amber-700 disabled:opacity-50">
          {saving ? 'Issuing…' : 'Overturn and issue'}
        </button>
        <button type="button" disabled={saving} onClick={() => { setOpen(false); setErr(null) }}
          className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-600 hover:bg-gray-50 disabled:opacity-50 dark:border-gray-700 dark:text-gray-300 dark:hover:bg-gray-800">
          Cancel
        </button>
      </div>
    </div>
  )
}

// Supporting evidence — email exports, WhatsApp screenshots, PDFs, images.
// Uploads go multipart (FormData); we deliberately do NOT set Content-Type so
// the browser adds the multipart boundary. Downloads fetch WITH the auth header
// and save the blob (the file endpoint is access-controlled — not a public URL).
function Evidence({ c, canAct, onChanged }: {
  c: Case; canAct: boolean; onChanged: () => void
}) {
  const [uploading, setUploading] = useState(false)
  const [busyId, setBusyId] = useState('')
  const [err, setErr] = useState('')
  const inputRef = useRef<HTMLInputElement | null>(null)
  const atts = c.attachments || []

  const upload = async (file: File) => {
    setErr(''); setUploading(true)
    try {
      const fd = new FormData()
      fd.append('file', file)
      const r = await authedHrisFetch(`/hris/api/disciplinary/${c.id}/attach/`, { method: 'POST', body: fd })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        setErr(d.detail || `Upload failed (HTTP ${r.status})`)
        return
      }
      onChanged()
    } finally {
      setUploading(false)
      if (inputRef.current) inputRef.current.value = ''
    }
  }

  const download = async (a: Attachment) => {
    setErr(''); setBusyId(a.id)
    try {
      const r = await authedHrisFetch(a.url)
      if (!r.ok) { setErr(`Download failed (HTTP ${r.status})`); return }
      const blob = await r.blob()
      const objUrl = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = objUrl
      link.download = a.filename || 'evidence'
      document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(objUrl)
    } finally { setBusyId('') }
  }

  const remove = async (a: Attachment) => {
    if (!window.confirm(`Remove "${a.filename}"?`)) return
    setErr(''); setBusyId(a.id)
    try {
      const r = await authedHrisFetch(`/hris/api/disciplinary/attachment/${a.id}/delete/`, { method: 'POST' })
      if (!r.ok && r.status !== 204) {
        const d = await r.json().catch(() => ({}))
        setErr(d.detail || `Delete failed (HTTP ${r.status})`)
        return
      }
      onChanged()
    } finally { setBusyId('') }
  }

  return (
    <div className="mt-4 rounded-lg border border-gray-200 bg-gray-50 p-3 dark:border-gray-800 dark:bg-gray-900/40">
      <div className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-gray-500">
        <Paperclip className="h-3.5 w-3.5" /> Evidence{atts.length ? ` (${atts.length})` : ''}
      </div>

      {atts.length === 0 && <p className="text-xs text-gray-400">No evidence attached yet.</p>}

      {atts.length > 0 && (
        <ul className="space-y-1.5">
          {atts.map(a => (
            <li key={a.id} className="flex items-center justify-between gap-2 rounded-md bg-white px-3 py-1.5 text-sm shadow-sm dark:bg-gray-950">
              <button type="button" disabled={busyId === a.id} onClick={() => download(a)}
                className="flex min-w-0 items-center gap-2 text-left hover:underline disabled:opacity-50" style={{ color: NAVY }}>
                <FileText className="h-4 w-4 shrink-0" style={{ color: ORANGE }} />
                <span className="truncate">{a.filename}</span>
                <span className="shrink-0 text-xs font-normal text-gray-400">{fmtSize(a.size)}</span>
              </button>
              {canAct && (
                <button type="button" disabled={busyId === a.id} onClick={() => remove(a)}
                  aria-label={`Remove ${a.filename}`}
                  className="shrink-0 rounded p-1 text-gray-400 hover:bg-red-50 hover:text-red-600 disabled:opacity-50 dark:hover:bg-red-950">
                  <X className="h-4 w-4" />
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      {canAct && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <input ref={inputRef} type="file" className="hidden" accept={EVIDENCE_ACCEPT}
            onChange={e => { const f = e.target.files?.[0]; if (f) upload(f) }} />
          <button type="button" disabled={uploading} onClick={() => inputRef.current?.click()}
            className="inline-flex items-center gap-1.5 rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-100 disabled:opacity-50 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-800">
            <Paperclip className="h-3.5 w-3.5" /> {uploading ? 'Uploading…' : 'Attach evidence'}
          </button>
          <span className="text-xs text-gray-400">PDF, Word, images, email (.eml/.msg), txt · max 25 MB</span>
        </div>
      )}

      {err && <div className="mt-2 text-xs text-red-600 dark:text-red-400">{err}</div>}
    </div>
  )
}
