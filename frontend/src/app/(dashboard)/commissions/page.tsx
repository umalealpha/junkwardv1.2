'use client'

/**
 * /commissions — monthly agent commission submission (CFO 2026-07-15).
 * One online form replacing emailed Excel workbooks. Everyone can enter + submit
 * their own month; group owners (Bokani / Tlamelo) + Finance get a review queue
 * and a payout export. Standalone — no GL / payment coupling (mirrors Agent Portal).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { apiFetchBinary, getToken } from '@/lib/api'
import {
  commissionAccess, commissionGroups, commissionSubmissions, commissionAgents,
  commissionCreateSubmission, commissionUpdateSubmission, commissionSubmit,
  commissionReview, commissionBulkReview, commissionAiCheck, commissionFinalApprove, commissionMarkProcessed, commissionUpload, commissionPayoutExportPath, commissionStatementPath,
  commissionSummary, commissionEmailStatements,
  type CommissionAccess, type CommissionGroup, type CommissionSubmission, type CommissionLine, type CommissionSummaryRow,
} from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { CommissionLoader } from '@/components/commissions/CommissionLoader'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  Coins, Plus, Trash2, CheckCircle2, XCircle, Download, ClipboardList, Clock,
  ChevronDown, ChevronRight, Upload,
} from 'lucide-react'

const TXN_TYPES = [
  ['new_business', 'New business'], ['renewal', 'Renewal'],
  ['endorsement', 'Endorsement'], ['previous_month', 'Previous month'],
] as const

const BADGE: Record<string, string> = {
  draft: 'bg-muted text-foreground', submitted: 'bg-blue-100 text-blue-700',
  under_review: 'bg-blue-100 text-blue-700', approved: 'bg-emerald-100 text-emerald-700',
  rejected: 'bg-red-100 text-red-700', paid: 'bg-emerald-100 text-emerald-700',
}

const money = (v: string | number) =>
  'BWP ' + Number(v || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const thisMonth = () => {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}
// Whole days a submission has been sitting at its CURRENT review stage (mirror of
// commissions/stuck.py _LANDED_AT), for the "waiting N days" chip. null if unknown.
const LANDED_AT: Record<string, keyof CommissionSubmission> = {
  submitted: 'submitted_at', second_review: 'first_reviewed_at', final_review: 'second_reviewed_at',
}
const daysWaiting = (s: CommissionSubmission): number | null => {
  const stamp = LANDED_AT[s.status] ? (s[LANDED_AT[s.status]] as string | null) : null
  if (!stamp) return null
  const d = Math.floor((Date.now() - new Date(stamp).getTime()) / 86400000)
  return Number.isFinite(d) && d >= 0 ? d : null
}
// Parse a user-typed money string ('1,200.00' / ' 500 ') → number; NaN if invalid.
const parseNum = (s: string | number | null | undefined): number => {
  if (s == null || s === '') return 0
  return Number(String(s).replace(/[,\s]/g, ''))
}
const cents = (s: string | number | null | undefined) => Math.round(parseNum(s) * 100)
// Clean a typed money string for the API ('1,200.00' → '1200.00'); invalid → '0'.
const num = (s: string) => { const n = parseNum(s); return Number.isFinite(n) ? String(n) : '0' }

type EditLine = CommissionLine & { _uid: string }
const uid = () =>
  (typeof crypto !== 'undefined' && crypto.randomUUID ? crypto.randomUUID() : String(Math.random()))
const blankLine = (): EditLine => ({
  _uid: uid(),
  policy_number: '', client_name: '', transaction_type: 'new_business',
  amount_collected: '', annualised_premium: '', commission_rate: '',
  collection_date: null, is_policy_closed: false, commission_amount: '',
})

// Alpha Direct Finance brand
const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'
const SERIF = '"Book Antiqua", "Palatino Linotype", Palatino, Georgia, serif'
// Which approval stage acts on each awaiting-review status (mirror of access.py).
const STATUS_STAGE: Record<string, string> = {
  submitted: 'stage1', second_review: 'stage2', final_review: 'final',
}

// Amendment trail — every edit a staff member makes to their lines, with the
// Aria (DeepSeek) plain-English note. Read-only; visible to the agent + reviewers.
function AmendHistory({ sub }: { sub: CommissionSubmission }) {
  const items = sub.amendments || []
  if (!items.length) return null
  return (
    <div className="mt-1 text-[11px] rounded-md border" style={{ borderColor: '#e2e8f0' }}>
      <div className="px-2 py-1 font-medium flex items-center gap-1" style={{ color: NAVY }}>
        <ClipboardList className="h-3 w-3" style={{ color: ORANGE }} />Amendment trail ({items.length})
      </div>
      {items.map(a => (
        <div key={a.id} className="px-2 py-1 border-t" style={{ borderColor: '#f1f5f9' }}>
          <div className="text-muted-foreground">
            {a.actor_name}
            {a.created_at ? ' · ' + new Date(a.created_at).toLocaleString() : ''}
            {' · gross '}{money(a.old_gross)} → {money(a.new_gross)}
          </div>
          {a.note && <div style={{ color: NAVY }}>{a.note}</div>}
        </div>
      ))}
    </div>
  )
}

// At-a-glance sanity checks for a reviewer — green "checks out" or amber list of
// things to look at before approving. Server-computed (commissions/review_flags).
function ReviewFlags({ sub }: { sub: CommissionSubmission }) {
  const rf = sub.review_flags
  // 'unknown' (checks not run / errored) shows nothing — never a false "clean".
  if (!rf || (rf.level !== 'clean' && rf.level !== 'check')) return null
  if (rf.level === 'clean')
    return (
      <div className="flex items-center gap-1 text-[11px]" style={{ color: '#1a9d54' }}>
        <CheckCircle2 className="h-3 w-3" />Checks out
      </div>
    )
  return (
    <div className="text-[11px] rounded-md px-2 py-1"
         style={{ background: 'rgba(244,166,35,0.10)', border: '1px solid rgba(244,166,35,0.35)', color: '#92400e' }}>
      <div className="font-medium flex items-center gap-1" style={{ color: NAVY }}>
        <ClipboardList className="h-3 w-3" style={{ color: ORANGE }} />
        {rf.items.length} thing{rf.items.length === 1 ? '' : 's'} to check
      </div>
      <ul className="list-disc ml-4 mt-0.5">
        {rf.items.map((it, i) => <li key={i}>{it}</li>)}
      </ul>
    </div>
  )
}

// The 3-stage approval chain, shown as a branded stepper.
function StageStepper({ sub }: { sub: CommissionSubmission }) {
  const steps = [
    { label: 'Submitted', at: sub.submitted_at, who: '' },
    { label: '1st review', at: sub.first_reviewed_at, who: sub.first_reviewed_by_name },
    { label: '2nd review', at: sub.second_reviewed_at, who: sub.second_reviewed_by_name },
    { label: 'Final', at: sub.final_at, who: sub.final_by_name },
    { label: 'Payroll', at: sub.paid_at, who: sub.paid_by_name },
  ]
  return (
    <div className="flex items-center gap-1.5 flex-wrap">
      {steps.map((s, i) => (
        <div key={i} className="flex items-center gap-1.5">
          <span title={s.who || undefined}
            className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full"
            style={s.at ? { background: NAVY, color: '#fff' }
              : { color: '#94a3b8', border: '1px solid #cbd5e1' }}>
            {s.at && <CheckCircle2 className="h-3 w-3" style={{ color: ORANGE }} />}{s.label}
          </span>
          {i < steps.length - 1 && <span className="w-3 h-px" style={{ background: '#cbd5e1' }} />}
        </div>
      ))}
      {sub.status === 'rejected' &&
        <span className="text-[11px] px-2 py-0.5 rounded-full bg-red-100 text-red-700">Sent back</span>}
    </div>
  )
}

export default function CommissionsPage() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const deepAgent = searchParams.get('agent') || ''
  const [access, setAccess] = useState<CommissionAccess | null>(null)
  const [groups, setGroups] = useState<CommissionGroup[]>([])
  // Deep-link ?agent=<id> (CFO "view any agent") opens straight into the reviewer view.
  const [tab, setTab] = useState<'mine' | 'review'>(deepAgent ? 'review' : 'mine')
  const [msg, setMsg] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  // ── my commission ────────────────────────────────────────────────────────
  const [period, setPeriod] = useState(thisMonth())
  const [mine, setMine] = useState<CommissionSubmission[]>([])
  const [lines, setLines] = useState<EditLine[]>([blankLine()])
  const current = useMemo(
    () => mine.find(s => s.period_label === period) || null, [mine, period])
  const myGroup = useMemo(
    () => groups.find(g => g.key === access?.group) || null, [groups, access])
  // Effective rate for THIS agent (0 if they work through a company / payroll) —
  // comes from the access endpoint, not the flat group rate.
  const rate = Number(access?.withholding_rate ?? myGroup?.withholding_rate ?? 0)
  const editable = !current || current.status === 'draft' || current.status === 'rejected'
  // Default the agent's own view to READ-ONLY (review their sheet), and only open
  // the inputs when they choose to amend a figure (CFO 2026-07-17). New agent with
  // no submission yet starts in amend mode so they can enter their first lines.
  const [amendMode, setAmendMode] = useState(false)

  // Integer-cents math so the live preview matches the server's ROUND_HALF_UP
  // to the thebe (float math drifted a cent). `valid` gates submit on real numbers.
  const totals = useMemo(() => {
    const grossC = lines.reduce((s, l) => s + cents(l.commission_amount), 0)
    const withC = Math.round(grossC * rate)
    const valid = lines.every(l => Number.isFinite(parseNum(l.commission_amount))
      && Number.isFinite(parseNum(l.amount_collected)) && Number.isFinite(parseNum(l.commission_rate)))
    return { gross: grossC / 100, withholding: withC / 100, net: (grossC - withC) / 100, valid }
  }, [lines, rate])

  const loadMine = useCallback(async () => {
    // mine=1 forces own-scope even for a manager, so this tab never loads
    // another agent's submission.
    const rows = await commissionSubmissions({ mine: true }).catch(() => [])
    setMine(rows)
  }, [])

  // ── review queue (managers) ────────────────────────────────────────────────
  const [rvGroup, setRvGroup] = useState('')
  const [rvPeriod, setRvPeriod] = useState(thisMonth())
  // rvAll=false → only what's waiting for THIS reviewer's stage (the queue).
  // rvAll=true  → EVERY commission for the filter, so the CFO/reviewers can open
  // and inspect each individual submission (CFO 2026-07-17). API returns all for
  // a reviewer when queue isn't set.
  const [rvAll, setRvAll] = useState(false)
  const [rvAgent, setRvAgent] = useState(deepAgent)
  const [agentList, setAgentList] = useState<{ id: string; name: string; code: string }[]>([])
  const [queue, setQueue] = useState<CommissionSubmission[]>([])
  // CFO 2026-08-21: separate months in the review queue too. Default shows the
  // picked month only; anything pending in OTHER months is surfaced as a count
  // with one-click reveal, so nothing is silently hidden (the #650 trap from
  // 2026-08-13). loadQueue still fetches every pending month, so the count and
  // the "show all" view are exact.
  const [queueSpanAllMonths, setQueueSpanAllMonths] = useState(false)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const toggleExpand = (id: string) =>
    setExpanded(s => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n })
  const loadQueue = useCallback(async () => {
    if (!access?.is_reviewer && !access?.is_payroll) return
    // queue=1 → only submissions sitting at a stage THIS reviewer can act on.
    // Focusing one agent (or "show all") drops the queue filter so DRAFTS show too.
    const inQueue = !(rvAll || !!rvAgent)
    const rows = await commissionSubmissions({
      group: rvGroup || undefined,
      // In the queue we always fetch EVERY pending month (period undefined) and
      // split by month on the client — so the picked-month view is exact AND we
      // can count what is waiting in other months. Filtering server-side by month
      // once hid a reviewer's prior-month items and read as "removed" (#650,
      // 2026-08-13); surfacing the other-month count instead keeps months
      // separate (CFO 2026-08-21) without ever hiding pending work.
      period: inQueue ? undefined : (rvPeriod || undefined),
      agent: rvAgent || undefined,
      queue: inQueue }).catch(() => [])
    setQueue(rows)
  }, [access, rvGroup, rvPeriod, rvAll, rvAgent])

  // Queue view = "Waiting for me" (not All-commissions, not a focused agent).
  const inQueueView = !(rvAll || !!rvAgent)
  // Default: show the picked month only. "Show all months" spans them (the old
  // behaviour), reachable in one click from the other-months banner.
  const monthScoped = inQueueView && !queueSpanAllMonths
  const shownQueue = monthScoped ? queue.filter(s => s.period_label === rvPeriod) : queue
  const otherMonthsPending = monthScoped
    ? queue.filter(s => s.period_label !== rvPeriod).length : 0

  useEffect(() => {
    // Not signed in → sign-in screen (standard dashboard guard). Without this,
    // an emailed /commissions link opened outside a session rendered an empty
    // form + a misleading "not linked to an agent" note (Dimpho, 2026-07-18).
    if (!getToken()) { router.replace('/login'); return }
    (async () => {
      let acc: CommissionAccess | null = null
      try {
        acc = await commissionAccess()
      } catch (e) {
        // A 401 here = the session/token wasn't valid yet — e.g. the /commissions
        // link opened from an email before SSO finished acquiring a Bearer (the
        // getToken() guard above passes for SSO users, who hold a sentinel token).
        // Send them to sign in. Do NOT fall through to the misleading "not linked
        // to an agent" note — that is only correct for a real 200 with agent=null.
        // (Bharath / Dimpho, 2026-07-18: account IS linked; the page just loaded
        // unauthenticated and mislabelled it.)
        const m = e instanceof Error ? e.message : ''
        if (/\b401\b/.test(m) || /credentials were not provided/i.test(m)) {
          router.replace('/login'); return
        }
      }
      const grp = await commissionGroups().catch(() => [])
      setAccess(acc); setGroups(grp)
      if (acc && !acc.agent && (acc.is_reviewer || acc.is_payroll)) setTab('review')
      // CFO "view any agent" (2026-07-21): load the agent directory for the picker,
      // and if the page was deep-linked with ?agent=<id>, open straight into the
      // reviewer view focused on that agent (their statements incl any draft).
      if (acc && (acc.is_reviewer || acc.is_payroll)) {
        commissionAgents().then(setAgentList).catch(() => {})
        if (deepAgent) setTab('review')
      }
    })()
    loadMine()
  }, [loadMine, router])
  useEffect(() => { loadQueue() }, [loadQueue])

  // Load the selected month's lines to edit. Keyed on `period` too, so switching
  // months always resets the editor (never carries one month's lines into another).
  useEffect(() => {
    if (current) setLines(current.lines.length ? current.lines.map(l => ({ ...l, _uid: uid() })) : [blankLine()])
    else setLines([blankLine()])
    // Existing submission → open in read-only review (they approve); brand-new
    // agent with nothing yet → open in amend mode so they can enter their lines.
    setAmendMode(!current)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current, period])

  const setLine = (i: number, patch: Partial<CommissionLine>) =>
    setLines(ls => ls.map((l, j) => (j === i ? { ...l, ...patch } : l)))
  const addLine = () => setLines(ls => [...ls, blankLine()])
  const removeLine = (i: number) => setLines(ls => (ls.length > 1 ? ls.filter((_, j) => j !== i) : ls))

  // Keep any line the user actually touched; build explicit fields (no _uid),
  // cleaning the numbers so '1,200.00' etc. reach the API as valid decimals.
  const payload = (): CommissionLine[] => lines
    .filter(l => l.policy_number || l.client_name || parseNum(l.commission_amount) !== 0
      || parseNum(l.amount_collected) !== 0)
    .map(l => ({
      policy_number: l.policy_number, client_name: l.client_name,
      transaction_type: l.transaction_type, frequency: l.frequency || '',
      amount_collected: num(l.amount_collected), annualised_premium: num(l.annualised_premium),
      commission_rate: num(l.commission_rate), amount_applicable: num(l.amount_applicable || ''),
      collection_date: l.collection_date || null, is_policy_closed: l.is_policy_closed,
      commission_amount: num(l.commission_amount),
    }))

  async function saveDraft(): Promise<string | null> {
    if (!access?.agent) { setMsg('Your account is not linked to an agent — ask Finance to add you.'); return null }
    const lns = payload()
    // The server binds the agent — the client never sends it.
    if (current) { await commissionUpdateSubmission(current.id, { lines: lns }); return current.id }
    const created = await commissionCreateSubmission({ period_label: period, lines: lns })
    return created.id
  }

  async function onSave() {
    setBusy(true); setMsg(null)
    try { const id = await saveDraft(); if (id) setMsg('Saved as draft ✓') }
    catch (e) { setMsg(e instanceof Error ? e.message.replace(/^HTTP \d+: /, '') : 'Save failed') }
    // Always refresh so a created draft is picked up even if a later step failed
    // (otherwise a retry re-creates and hits the one-per-month 400).
    finally { await loadMine(); setBusy(false) }
  }
  async function onSubmit() {
    setBusy(true); setMsg(null)
    try {
      const id = await saveDraft()
      if (id) { await commissionSubmit(id); setMsg('Submitted for review ✓') }
    } catch (e) { setMsg(e instanceof Error ? e.message.replace(/^HTTP \d+: /, '') : 'Submit failed') }
    finally { await loadMine(); setBusy(false) }
  }

  async function review(id: string, approve: boolean, presetNote?: string) {
    // presetNote comes from the one-tap reject chips; fall back to a prompt only
    // when nothing was passed (keeps the old keyboard path working).
    const note = approve ? '' : (presetNote ?? (window.prompt('Reason (sent back to the agent):') || ''))
    if (!approve && !note) return
    setBusy(true); setMsg(null)
    try { await commissionReview(id, approve, note); setRejectFor(null); setRejectText(''); await loadQueue() }
    catch (e) { setMsg(e instanceof Error ? e.message.replace(/^HTTP \d+: /, '') : 'Failed') }
    finally { setBusy(false) }
  }
  // One-tap reject reasons (CFO 2026-08-22) — the agent instantly knows what to fix.
  const [rejectFor, setRejectFor] = useState<string | null>(null)
  const [rejectText, setRejectText] = useState('')
  const REJECT_REASONS = [
    'Rate looks wrong', 'Policy not closed', 'Duplicate line',
    "Amount doesn't match the statement", 'Missing detail',
  ]
  // Aria's on-demand second opinion per row (read-only; never changes data).
  const [ariaNotes, setAriaNotes] = useState<Record<string, { note: string; source: string }>>({})
  const [ariaBusy, setAriaBusy] = useState<string | null>(null)
  async function askAria(id: string) {
    setAriaBusy(id)
    try { const r = await commissionAiCheck(id); setAriaNotes(n => ({ ...n, [id]: r })) }
    catch { setAriaNotes(n => ({ ...n, [id]: { note: 'Aria could not check this one right now.', source: 'checks' } })) }
    finally { setAriaBusy(null) }
  }
  // Approve every clean item waiting at my stage, in one click.
  async function bulkApproveClean() {
    const ids = shownQueue.filter(s => canAct(s) && s.review_flags?.level === 'clean').map(s => s.id)
    if (!ids.length) return
    setBusy(true); setMsg(null)
    try {
      const r = await commissionBulkReview(ids)
      setMsg(`Approved ${r.approved.length} that checked out`
        + (r.skipped.length ? ` · ${r.skipped.length} left for you to look at` : '') + ' ✓')
      await loadQueue()
    } catch (e) { setMsg(e instanceof Error ? e.message.replace(/^HTTP \d+: /, '') : 'Failed') }
    finally { setBusy(false) }
  }
  async function downloadStatement(id: string) {
    if (busy) return
    setBusy(true); setMsg(null)
    try {
      const res = await apiFetchBinary(commissionStatementPath(id))
      if (!res.ok) {
        let detail = `Download failed (${res.status})`
        try { const j = await res.json(); if (j?.detail) detail = j.detail } catch { /* not json */ }
        setMsg(detail); return
      }
      // Name the file from the server's Content-Disposition so the extension
      // matches the content — the CSV fallback was saved as .xlsx and would not
      // open in Excel ("does not work", Bokani Makosha 2026-08-13).
      const cd = res.headers.get('Content-Disposition') || ''
      const m = cd.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i)
      const fname = m && m[1] ? decodeURIComponent(m[1]) : `commission_statement_${id}.csv`
      const url = URL.createObjectURL(await res.blob())
      const a = document.createElement('a')
      a.href = url; a.download = fname
      document.body.appendChild(a); a.click(); a.remove()
      setTimeout(() => URL.revokeObjectURL(url), 30000)
    } catch (e) { setMsg(e instanceof Error ? e.message.replace(/^HTTP \d+: /, '') : 'Download failed') }
    finally { setBusy(false) }
  }

  async function downloadPayout() {
    if (busy) return
    if (!rvGroup || !rvPeriod) { setMsg('Pick a group and month to export.'); return }
    setBusy(true)
    try {
      const res = await apiFetchBinary(commissionPayoutExportPath(rvGroup, rvPeriod))
      if (!res.ok) {
        // Never save an error body as the bank file — surface the real error.
        let detail = `Export failed (${res.status})`
        try { const j = await res.json(); if (j?.detail) detail = j.detail } catch { /* not json */ }
        setMsg(detail); return
      }
      const held = res.headers.get('X-Payout-Held')
      const url = URL.createObjectURL(await res.blob())
      const a = document.createElement('a')
      a.href = url; a.download = `commission_payout_${rvGroup}_${rvPeriod}.csv`
      document.body.appendChild(a); a.click(); a.remove()
      setTimeout(() => URL.revokeObjectURL(url), 30000)
      setMsg(`Payout file downloaded ✓${held && held !== '0'
        ? ` — note: ${held} approved agent(s) held (no bank details)` : ''}`)
    } catch (e) { setMsg(e instanceof Error ? e.message.replace(/^HTTP \d+: /, '') : 'Export failed') }
    finally { setBusy(false) }
  }
  // Month-close: email each approved agent their statement (preview → confirm → send).
  async function emailStatements() {
    if (!rvGroup || !rvPeriod) { setMsg('Pick a group and month first.'); return }
    setBusy(true); setMsg(null)
    try {
      const p = await commissionEmailStatements(rvGroup, rvPeriod, true)   // dry-run
      if (!p.count) { setMsg(`No approved agents to email for ${rvPeriod}.`); return }
      const noEmail = p.no_email?.length ? ` (${p.no_email.length} have no email and will be skipped)` : ''
      if (!window.confirm(`Email ${p.count} agent(s) their ${rvPeriod} statement?${noEmail}`)) return
      const r = await commissionEmailStatements(rvGroup, rvPeriod, false)  // send
      setMsg(`Emailed ${r.emailed ?? 0} agent(s) their statement ✓`
        + (r.failed?.length ? ` · ${r.failed.length} failed` : '')
        + (r.no_email?.length ? ` · ${r.no_email.length} had no email` : ''))
    } catch (e) { setMsg(e instanceof Error ? e.message.replace(/^HTTP \d+: /, '') : 'Failed') }
    finally { setBusy(false) }
  }
  // Payout PREVIEW — what will actually be paid this group+month, before exporting.
  const [payoutPreview, setPayoutPreview] = useState<CommissionSummaryRow[] | null>(null)
  async function previewPayout() {
    if (!rvGroup || !rvPeriod) { setMsg('Pick a group and month to preview.'); return }
    setBusy(true); setMsg(null)
    try {
      const rows = await commissionSummary({ group: rvGroup, period: rvPeriod })
      // only what would be paid: approved (and already-paid, for the record)
      setPayoutPreview(rows.filter(r => r.status === 'approved' || r.status === 'paid'))
    } catch (e) { setMsg(e instanceof Error ? e.message.replace(/^HTTP \d+: /, '') : 'Preview failed') }
    finally { setBusy(false) }
  }

  // A reviewer may act on a row only when it sits at a stage they own.
  const canAct = (s: CommissionSubmission) => {
    const stage = STATUS_STAGE[s.status]
    return !!stage && !!access?.stages?.includes(stage)
  }
  // CFO direct approve (CFO 2026-08-18): the final approver may approve now from
  // an EARLIER stage (before 1st/2nd review), without waiting for them. At
  // final_review the normal Approve already applies, so don't double up there.
  const canFinalNow = (s: CommissionSubmission) =>
    !!access?.stages?.includes('final') &&
    (s.status === 'submitted' || s.status === 'second_review')
  async function finalNow(id: string, approve: boolean) {
    const note = approve ? '' : (window.prompt('Reason (sent back to the agent):') || '')
    if (!approve && !note) return
    setBusy(true); setMsg(null)
    try { await commissionFinalApprove(id, approve, note); await loadQueue() }
    catch (e) { setMsg(e instanceof Error ? e.message.replace(/^HTTP \d+: /, '') : 'Failed') }
    finally { setBusy(false) }
  }
  // Payroll marks an approved submission processed (→ notifies Bokani/Tlamelo).
  const canProcess = (s: CommissionSubmission) => !!access?.is_payroll && s.status === 'approved'

  async function processIt(id: string) {
    setBusy(true); setMsg(null)
    try {
      await commissionMarkProcessed(id)
      setMsg('Marked processed — Bokani & Tlamelo notified (cc Pako & Kago).')
    } catch (e) { setMsg(e instanceof Error ? e.message.replace(/^HTTP \d+: /, '') : 'Failed') }
    finally { await loadQueue(); setBusy(false) }
  }

  // My commission: upload my OWN sheet → server parses it → lines populate (no retyping).
  const uploadRef = useRef<HTMLInputElement>(null)
  async function onUpload(file: File, forPeriod: string) {
    setBusy(true); setMsg(null)
    try {
      // own=1 → always bind to the signed-in user's OWN agent (per-policy),
      // even if they are also a reviewer (Bokani Makosha, bug f3a02295).
      const r = await commissionUpload(file, forPeriod, { own: true })
      setMsg(`Loaded ${r.lines} line(s) for ${r.agent} ✓`)
    } catch (e) { setMsg(e instanceof Error ? e.message.replace(/^HTTP \d+: /, '') : 'Upload failed') }
    finally { await loadMine(); await loadQueue(); setBusy(false) }
  }

  const cell = 'px-2 py-1.5 text-sm border rounded-md bg-background w-full'

  return (
    <div>
      <TopBar />
      <div className="p-6 max-w-6xl mx-auto space-y-6">
        <div className="rounded-xl px-6 py-5" style={{ background: NAVY }}>
          <div className="flex items-center gap-3">
            <div className="rounded-lg p-2" style={{ background: 'rgba(244,166,35,0.15)' }}>
              <Coins className="h-6 w-6" style={{ color: ORANGE }} />
            </div>
            <div>
              <h1 className="text-2xl font-semibold" style={{ fontFamily: SERIF, color: '#fff' }}>Commissions</h1>
              <p className="text-[11px] mt-0.5 font-medium" style={{ color: ORANGE, letterSpacing: '0.09em' }}>
                ALPHA DIRECT · MONTHLY AGENT COMMISSION
              </p>
            </div>
          </div>
          <p className="text-sm mt-3" style={{ color: 'rgba(255,255,255,0.75)' }}>
            Enter your monthly commission here instead of emailing a spreadsheet. Your total, any withholding
            that applies, and your net are worked out for you. It then goes through review before payment.
          </p>
        </div>
        {msg && <div role="status" aria-live="polite" className="text-sm rounded-md bg-muted/60 p-3">{msg}</div>}

        {(access?.is_reviewer || access?.is_payroll) && (
          <div className="flex gap-2">
            <Button size="sm" variant={tab === 'mine' ? 'primary' : 'outline'} onClick={() => setTab('mine')}>My commission</Button>
            <Button size="sm" variant={tab === 'review' ? 'primary' : 'outline'} onClick={() => setTab('review')}>
              <ClipboardList className="h-4 w-4 mr-1" /> {access?.is_payroll && !access?.is_reviewer ? 'Payroll' : 'Review'}
            </Button>
          </div>
        )}

        {/* ── My commission ─────────────────────────────────────────────── */}
        {tab === 'mine' && (
          <>
            <Card><CardContent className="p-5 space-y-4">
              <div className="flex flex-wrap items-center gap-3">
                <label htmlFor="comm-month" className="text-sm font-medium">Month</label>
                <input id="comm-month" type="month" value={period} onChange={e => setPeriod(e.target.value)}
                       className="px-3 py-1.5 text-sm border rounded-md bg-background" />
                {myGroup && <span className="text-xs text-muted-foreground">
                  {myGroup.name} · withholding {(rate * 100).toFixed(0)}% · paid {myGroup.pays_via === 'payroll' ? 'via payroll' : 'to your bank'}
                </span>}
                {current && <span className={'text-xs px-2 py-0.5 rounded ' + (BADGE[current.status] || '')}>{current.status_label || current.status.replace('_', ' ')}</span>}
              </div>
              {current && <StageStepper sub={current} />}
              {current?.status === 'rejected' && current.review_note &&
                <div className="text-xs text-red-600">↩ Sent back: {current.review_note}</div>}

              <div className="overflow-x-auto">
                <table className="w-full border-separate border-spacing-y-1">
                  <thead>
                    <tr className="text-left text-xs text-muted-foreground">
                      <th scope="col" className="px-2">Policy #</th><th scope="col" className="px-2">Client</th><th scope="col" className="px-2">Type</th>
                      <th scope="col" className="px-2">Collected</th><th scope="col" className="px-2">Annualised</th><th scope="col" className="px-2">Rate %</th>
                      <th scope="col" className="px-2">Collected on</th><th scope="col" className="px-2">Closed?</th>
                      <th scope="col" className="px-2">Commission</th><th scope="col" className="px-2"><span className="sr-only">Actions</span></th>
                    </tr>
                  </thead>
                  <tbody>
                    {lines.map((l, i) => {
                      const badNum = (v: string) => !Number.isFinite(parseNum(v))
                      const nCell = (v: string) => cell + ' text-right' + (badNum(v) ? ' border-red-500' : '')
                      return (
                      <tr key={l._uid}>
                        <td><input aria-label={`Policy number, line ${i + 1}`} className={cell} value={l.policy_number} disabled={!editable || !amendMode}
                                   onChange={e => setLine(i, { policy_number: e.target.value })} /></td>
                        <td><input aria-label={`Client, line ${i + 1}`} className={cell} value={l.client_name} disabled={!editable || !amendMode}
                                   onChange={e => setLine(i, { client_name: e.target.value })} /></td>
                        <td><select aria-label={`Type, line ${i + 1}`} className={cell} value={l.transaction_type} disabled={!editable || !amendMode}
                                    onChange={e => setLine(i, { transaction_type: e.target.value })}>
                          {TXN_TYPES.map(([v, lbl]) => <option key={v} value={v}>{lbl}</option>)}
                        </select></td>
                        <td><input aria-label={`Amount collected, line ${i + 1}`} className={nCell(l.amount_collected)} inputMode="decimal" value={l.amount_collected} disabled={!editable || !amendMode}
                                   onChange={e => setLine(i, { amount_collected: e.target.value })} /></td>
                        <td><input aria-label={`Annualised premium, line ${i + 1}`} className={cell + ' text-right'} inputMode="decimal" value={l.annualised_premium} disabled={!editable || !amendMode}
                                   onChange={e => setLine(i, { annualised_premium: e.target.value })} /></td>
                        <td><input aria-label={`Commission rate percent, line ${i + 1}`} className={nCell(l.commission_rate)} inputMode="decimal" value={l.commission_rate} disabled={!editable || !amendMode}
                                   onChange={e => setLine(i, { commission_rate: e.target.value })} /></td>
                        <td><input aria-label={`Collection date, line ${i + 1}`} className={cell} type="date" value={l.collection_date || ''} disabled={!editable || !amendMode}
                                   onChange={e => setLine(i, { collection_date: e.target.value })} /></td>
                        <td className="text-center"><input aria-label={`Policy closed, line ${i + 1}`} type="checkbox" checked={l.is_policy_closed} disabled={!editable || !amendMode}
                                   onChange={e => setLine(i, { is_policy_closed: e.target.checked })} /></td>
                        <td><input aria-label={`Commission amount, line ${i + 1}`} className={nCell(l.commission_amount) + ' font-medium'} inputMode="decimal" value={l.commission_amount} disabled={!editable || !amendMode}
                                   onChange={e => setLine(i, { commission_amount: e.target.value })} /></td>
                        <td>{editable && amendMode && <button aria-label={`Remove line ${i + 1}`} onClick={() => removeLine(i)} title="Remove line"><Trash2 className="h-4 w-4 text-muted-foreground" /></button>}</td>
                      </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
              {editable && amendMode && (
                <div className="flex flex-wrap gap-2">
                  <Button size="sm" variant="outline" onClick={addLine}><Plus className="h-4 w-4 mr-1" />Add line</Button>
                  {/* Upload also lives on the main action row above, which is
                      where an agent sent a sheet back actually looks. Keeping it
                      here too means someone already amending a line can swap the
                      whole workbook without leaving amend mode. One `uploadRef`
                      serves both: only one of the two rows is ever mounted. */}
                  {access?.agent && (
                    <>
                      <input ref={uploadRef} type="file" accept=".xlsx,.xlsm,.xlsb,.xls,.ods" hidden
                             onChange={e => { const f = e.target.files?.[0]; if (f) onUpload(f, period); e.target.value = '' }} />
                      <Button size="sm" variant="outline" disabled={busy} onClick={() => uploadRef.current?.click()}>
                        <Upload className="h-4 w-4 mr-1" />Upload my sheet (Excel)
                      </Button>
                    </>
                  )}
                </div>
              )}

              <div className="flex flex-wrap items-center justify-end gap-x-6 gap-y-2 border-t pt-3 text-sm">
                <span>Gross <b>{money(totals.gross)}</b></span>
                <span className="text-muted-foreground">Withholding ({(rate * 100).toFixed(0)}%) <b>−{money(totals.withholding)}</b></span>
                <span>Net payable <b className="text-[#F4A623]">{money(totals.net)}</b></span>
              </div>

              {access?.agent ? (
                editable ? (amendMode ? (
                  <div className="flex flex-col items-end gap-1">
                    {!totals.valid && <span className="text-xs text-red-600">Some number cells aren&apos;t valid — check the highlighted fields.</span>}
                    <div className="flex justify-end gap-2">
                      <Button size="sm" variant="outline" disabled={busy}
                              onClick={() => { setAmendMode(false); setLines(current && current.lines.length ? current.lines.map(l => ({ ...l, _uid: uid() })) : [blankLine()]) }}>Cancel</Button>
                      <Button size="sm" disabled={busy || !totals.valid} onClick={async () => { await onSave(); setAmendMode(false) }}>Save changes</Button>
                    </div>
                  </div>
                ) : (
                  <div className="flex flex-col items-end gap-2">
                    {/* Bokani Makosha, bug e77ee875 (2026-09-18): "no single Agent
                        can upload a sheet ... she cannot see the upload button but
                        me personal i can be able to upload". The server had already
                        been opened for a rejected sheet (bug 019378fc), but the only
                        Upload control lived INSIDE amend mode — so an agent had to
                        press "Amend a line", which reads as editing one row by hand,
                        before the button existed. Nobody sent back a rejected sheet
                        looks for it there. It is now offered where the rejection is
                        read, and said out loud when a sheet has come back. */}
                    {current?.status === 'rejected' && (
                      <span className="text-[11px] text-amber-700">
                        This sheet was sent back, so it is open again — upload the corrected
                        one below, or amend the lines by hand.
                      </span>
                    )}
                    <div className="flex items-center gap-3">
                      <input ref={uploadRef} type="file" accept=".xlsx,.xlsm,.xlsb,.xls,.ods" hidden
                             onChange={e => { const f = e.target.files?.[0]; if (f) onUpload(f, period); e.target.value = '' }} />
                      <Button size="sm" variant="outline" disabled={busy}
                              onClick={() => uploadRef.current?.click()}>
                        <Upload className="h-4 w-4 mr-1" />Upload my sheet (Excel)
                      </Button>
                      <Button size="sm" variant="outline" disabled={busy} onClick={() => setAmendMode(true)}>Amend a line</Button>
                      <button disabled={busy || !totals.valid || totals.gross <= 0} onClick={onSubmit}
                              className="inline-flex items-center gap-2 rounded-lg px-6 py-3 text-base font-semibold text-white disabled:opacity-50"
                              style={{ background: '#1a9d54', fontFamily: SERIF }}>
                        <CheckCircle2 className="h-5 w-5" />I approve commissions
                      </button>
                    </div>
                    <span className="text-[11px] text-muted-foreground">Approving sends it to Bokani &amp; Tlamelo for review. Disagree with a figure? Press &ldquo;Amend a line&rdquo; first — every change is logged.</span>
                  </div>
                )) : null
              ) : (
                <p className="text-xs text-amber-600">Your account isn&apos;t linked to an agent yet — ask Finance to add you before submitting.</p>
              )}
              {!editable && current && <p className="text-xs text-muted-foreground text-right">This month is {current.status.replace('_', ' ')} — it can&apos;t be edited.</p>}
            </CardContent></Card>

            <Card><CardContent className="p-5">
              <div className="font-medium mb-2">My submissions</div>
              {mine.length === 0 ? <p className="text-sm text-muted-foreground">Nothing submitted yet.</p> :
                mine.map(s => (
                  <div key={s.id} className="py-2 border-b last:border-0 space-y-1 text-sm">
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                      <span className="font-medium w-20">{s.period_label}</span>
                      <span className="flex-1 text-muted-foreground">{s.lines.length} line(s) · gross {money(s.gross_commission)} · net {money(s.net_payable)}</span>
                      <span className={'text-xs px-2 py-0.5 rounded ' + (BADGE[s.status] || '')}>{s.status_label || s.status.replace('_', ' ')}</span>
                    </div>
                    <StageStepper sub={s} />
                    {s.status === 'rejected' && s.review_note && <span className="text-xs text-red-600">↩ {s.review_note}</span>}
                    <AmendHistory sub={s} />
                  </div>
                ))}
            </CardContent></Card>
          </>
        )}

        {/* ── Review queue (managers) ────────────────────────────────────── */}
        {tab === 'review' && (access?.is_reviewer || access?.is_payroll) && (
          <>
          {/* Load agent commission sheets — drop file → check what we read → confirm */}
          {access?.is_reviewer && (
            <CommissionLoader groups={groups} period={rvPeriod} onPeriodChange={setRvPeriod}
                              onLoaded={async (m) => { setMsg(m); await loadMine(); await loadQueue() }} />
          )}
          <Card><CardContent className="p-5 space-y-3">
            <div className="flex flex-wrap items-center gap-3">
              <select aria-label="Filter by group" value={rvGroup} onChange={e => { setRvGroup(e.target.value); setPayoutPreview(null) }} className="px-3 py-1.5 text-sm border rounded-md bg-background">
                <option value="">All groups</option>
                {groups.map(g => <option key={g.key} value={g.key}>{g.name}</option>)}
              </select>
              {agentList.length > 0 && (
                <select aria-label="View a specific agent" value={rvAgent} onChange={e => setRvAgent(e.target.value)}
                        className="px-3 py-1.5 text-sm border rounded-md bg-background" title="See one agent's statements — including drafts">
                  <option value="">All agents</option>
                  {agentList.map(a => <option key={a.id} value={a.id}>{a.name}{a.code ? ` (${a.code})` : ''}</option>)}
                </select>
              )}
              <input aria-label="Filter by month" type="month" value={rvPeriod} onChange={e => { setRvPeriod(e.target.value); setPayoutPreview(null) }}
                     title="Show this month's items. Anything waiting in other months is still flagged below."
                     className="px-3 py-1.5 text-sm border rounded-md bg-background" />
              <div className="flex gap-1">
                <Button size="sm" variant={!rvAll ? 'primary' : 'outline'} onClick={() => setRvAll(false)}>Waiting for me</Button>
                <Button size="sm" variant={rvAll ? 'primary' : 'outline'} onClick={() => setRvAll(true)}>All commissions</Button>
              </div>
              {access?.can_export && (
                <>
                  <Button size="sm" variant="outline" disabled={busy || !rvGroup} onClick={previewPayout}
                          title={rvGroup ? 'See what will be paid before exporting' : 'Pick a group first'}>
                    <ClipboardList className="h-4 w-4 mr-1" />Preview payout
                  </Button>
                  <Button size="sm" variant="outline" disabled={busy || !rvGroup} onClick={downloadPayout}
                          title={rvGroup ? 'Export the approved payout file' : 'Pick a group first'}>
                    <Download className="h-4 w-4 mr-1" />Export payout
                  </Button>
                  <Button size="sm" variant="outline" disabled={busy || !rvGroup} onClick={emailStatements}
                          title={rvGroup ? 'Email each approved agent their own statement (asks first)' : 'Pick a group first'}>
                    <Upload className="h-4 w-4 mr-1" />Email statements
                  </Button>
                </>
              )}
            </div>
            {payoutPreview && (
              <div className="rounded-md border p-3 space-y-2" style={{ borderColor: '#e2e8f0' }}>
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium" style={{ color: NAVY }}>
                    Payout preview — {payoutPreview.length} agent(s) approved or paid for {rvPeriod}
                  </span>
                  <button className="text-xs underline text-muted-foreground" onClick={() => setPayoutPreview(null)}>Hide</button>
                </div>
                {payoutPreview.length === 0 ? <p className="text-xs text-muted-foreground">Nothing approved yet for this group and month.</p> : (
                  <div className="overflow-x-auto"><table className="w-full text-xs">
                    <thead><tr className="text-left text-muted-foreground">
                      <th className="px-2 py-1">Agent</th><th className="px-2 py-1 text-right">Gross</th>
                      <th className="px-2 py-1 text-right">Withholding</th><th className="px-2 py-1 text-right">Net payable</th>
                    </tr></thead>
                    <tbody>
                      {payoutPreview.map(r => (
                        <tr key={r.id} className="border-t">
                          <td className="px-2 py-1">{r.agent}</td>
                          <td className="px-2 py-1 text-right tabular-nums">{money(r.gross)}</td>
                          <td className="px-2 py-1 text-right tabular-nums">−{money(r.withholding)}</td>
                          <td className="px-2 py-1 text-right tabular-nums font-medium">{money(r.net)}</td>
                        </tr>
                      ))}
                      <tr className="border-t font-medium" style={{ background: 'rgba(13,27,42,0.04)' }}>
                        <td className="px-2 py-1">Total</td>
                        <td className="px-2 py-1 text-right tabular-nums">{money(payoutPreview.reduce((s, r) => s + Number(r.gross || 0), 0))}</td>
                        <td className="px-2 py-1 text-right tabular-nums">−{money(payoutPreview.reduce((s, r) => s + Number(r.withholding || 0), 0))}</td>
                        <td className="px-2 py-1 text-right tabular-nums" style={{ color: ORANGE }}>{money(payoutPreview.reduce((s, r) => s + Number(r.net || 0), 0))}</td>
                      </tr>
                    </tbody>
                  </table></div>
                )}
              </div>
            )}
            {/* Reviewer summary + one-click "approve the clean ones" (CFO 2026-08-22). */}
            {access?.is_reviewer && inQueueView && shownQueue.some(s => canAct(s)) && (() => {
              const mine = shownQueue.filter(s => canAct(s))
              const clean = mine.filter(s => s.review_flags?.level === 'clean')
              const flagged = mine.filter(s => s.review_flags?.level === 'check')
              return (
                <div className="flex flex-wrap items-center gap-3 rounded-md px-3 py-2"
                     style={{ background: 'rgba(13,27,42,0.04)' }}>
                  <span className="text-sm">
                    <b>{mine.length}</b> waiting for you
                    {flagged.length > 0 && <> · <b style={{ color: '#92400e' }}>{flagged.length}</b> to look at</>}
                    {clean.length > 0 && <> · <b style={{ color: '#1a9d54' }}>{clean.length}</b> check out</>}
                  </span>
                  {clean.length > 0 && (
                    <Button size="sm" disabled={busy} onClick={bulkApproveClean}
                            title="Approve every item that passed all the checks">
                      <CheckCircle2 className="h-4 w-4 mr-1" />Approve {clean.length} that check out
                    </Button>
                  )}
                </div>
              )
            })()}
            <div className="text-xs text-muted-foreground flex items-center gap-1">
              <Clock className="h-3 w-3" />
              {rvAll
                ? 'Every commission for this filter — click a name to open the full sheet.'
                : (queueSpanAllMonths
                    ? 'Showing everything waiting for you, across all months.'
                    : `Showing ${rvPeriod} only — anything waiting in other months is flagged below.`)}
            </div>
            {inQueueView && !queueSpanAllMonths && otherMonthsPending > 0 && (
              <div className="text-xs flex items-center gap-2 rounded-md px-2.5 py-1.5"
                   style={{ background: 'rgba(244,166,35,0.10)', border: '1px solid rgba(244,166,35,0.35)' }}>
                <span>⚠️ {otherMonthsPending} more waiting for you in other months.</span>
                <button className="underline font-medium" onClick={() => setQueueSpanAllMonths(true)}>Show all months</button>
              </div>
            )}
            {inQueueView && queueSpanAllMonths && (
              <button className="text-xs underline text-muted-foreground w-fit"
                      onClick={() => setQueueSpanAllMonths(false)}>Back to {rvPeriod} only</button>
            )}
            {shownQueue.length === 0 ? <p className="text-sm text-muted-foreground">{rvAll ? 'No commissions for this filter.' : (monthScoped ? `Nothing is waiting for your review in ${rvPeriod} right now — anything already actioned by another reviewer clears from here. Your Approve / Reject buttons appear on each item the moment it reaches your stage. Use “All commissions” to see every sheet and download it.` : 'Nothing is waiting for your review right now — items appear here (with Approve / Reject) the moment they reach your stage. Use “All commissions” to see everything.')}</p> :
              shownQueue.map(s => (
                <div key={s.id} className="py-2.5 border-b last:border-0 space-y-1.5">
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                    <button onClick={() => toggleExpand(s.id)} aria-expanded={expanded.has(s.id)}
                            className="flex items-center gap-1 font-medium flex-1 min-w-[160px] text-left hover:underline">
                      {expanded.has(s.id) ? <ChevronDown className="h-4 w-4 shrink-0" /> : <ChevronRight className="h-4 w-4 shrink-0" />}
                      {s.agent_name} — {s.group_name}
                    </button>
                    <span className="text-xs text-muted-foreground">{s.period_label} · {s.lines.length} line(s)</span>
                    {(() => { const d = daysWaiting(s); return d !== null && d >= 1 ? (
                      <span className="text-[11px] px-1.5 py-0.5 rounded-full"
                            style={d >= 5 ? { background: 'rgba(220,38,38,0.10)', color: '#b91c1c' } : { background: 'rgba(244,166,35,0.12)', color: '#92400e' }}
                            title="How long it has been waiting at your stage">
                        <Clock className="h-3 w-3 inline mr-0.5" />waiting {d}d
                      </span>) : null })()}
                    <span className="text-sm">gross {money(s.gross_commission)} · net <b style={{ color: ORANGE }}>{money(s.net_payable)}</b></span>
                    {/* A reviewer may pull ANY statement to check it (the backend
                        gates the endpoint on is_reviewer too) — not only rows at
                        their own stage. Gating this on canAct hid the Download
                        button on every row a reviewer wasn't actively approving
                        (Bokani, 2026-08-13). Approve/Reject stay stage-gated below. */}
                    {access?.is_reviewer && (s.has_statement_file || (s.lines && s.lines.length > 0)) && (
                      <Button size="sm" variant="outline" disabled={busy} onClick={() => downloadStatement(s.id)}>
                        <Download className="h-4 w-4 mr-1" />Statement
                      </Button>
                    )}
                    {canAct(s) && (
                      <>
                        <Button size="sm" disabled={busy} onClick={() => review(s.id, true)}><CheckCircle2 className="h-4 w-4 mr-1" />Approve</Button>
                        <Button size="sm" variant="outline" disabled={busy} onClick={() => setRejectFor(rejectFor === s.id ? null : s.id)}><XCircle className="h-4 w-4 mr-1" />Reject</Button>
                      </>
                    )}
                    {canFinalNow(s) && (
                      <>
                        <Button size="sm" disabled={busy} onClick={() => finalNow(s.id, true)} title="Approve now without waiting for the 1st & 2nd reviews"><CheckCircle2 className="h-4 w-4 mr-1" />Approve now (CFO)</Button>
                        <Button size="sm" variant="outline" disabled={busy} onClick={() => finalNow(s.id, false)}><XCircle className="h-4 w-4 mr-1" />Send back</Button>
                      </>
                    )}
                    {canProcess(s) && (
                      <Button size="sm" disabled={busy} onClick={() => processIt(s.id)}><CheckCircle2 className="h-4 w-4 mr-1" />Mark payroll processed</Button>
                    )}
                  </div>
                  {rejectFor === s.id && (
                    <div className="rounded-md p-2 space-y-2" style={{ background: 'rgba(220,38,38,0.05)', border: '1px solid rgba(220,38,38,0.25)' }}>
                      <div className="text-[11px] font-medium" style={{ color: '#b91c1c' }}>Send back to {s.agent_name} — pick a reason:</div>
                      <div className="flex flex-wrap gap-1.5">
                        {REJECT_REASONS.map(r => (
                          <Button key={r} size="sm" variant="outline" disabled={busy}
                                  onClick={() => review(s.id, false, r)}>{r}</Button>
                        ))}
                      </div>
                      <div className="flex items-center gap-2">
                        <input value={rejectText} onChange={e => setRejectText(e.target.value)}
                               placeholder="Or type another reason" aria-label="Other reason"
                               className="flex-1 px-2 py-1 text-sm border rounded-md bg-background" />
                        <Button size="sm" disabled={busy || !rejectText.trim()} onClick={() => review(s.id, false, rejectText.trim())}>Send back</Button>
                        <Button size="sm" variant="outline" disabled={busy} onClick={() => { setRejectFor(null); setRejectText('') }}>Cancel</Button>
                      </div>
                    </div>
                  )}
                  <StageStepper sub={s} />
                  <ReviewFlags sub={s} />
                  {access?.is_reviewer && (
                    <div className="flex items-start gap-2">
                      <Button size="sm" variant="outline" disabled={ariaBusy === s.id}
                              onClick={() => askAria(s.id)} title="Aria's quick read — advice only, changes nothing">
                        <ClipboardList className="h-4 w-4 mr-1" style={{ color: ORANGE }} />
                        {ariaBusy === s.id ? 'Asking Aria…' : 'Ask Aria'}
                      </Button>
                      {ariaNotes[s.id] && (
                        <div className="text-[11px] rounded-md px-2 py-1 flex-1"
                             style={{ background: 'rgba(13,27,42,0.04)', border: '1px solid #e2e8f0', color: NAVY }}>
                          <b style={{ color: ORANGE }}>Aria:</b> {ariaNotes[s.id].note}
                        </div>
                      )}
                    </div>
                  )}
                  <AmendHistory sub={s} />
                  {expanded.has(s.id) && (
                    <div className="mt-1 border rounded-md max-h-80 overflow-auto">
                      {s.lines.length === 0 ? <p className="text-xs text-muted-foreground p-2">No lines.</p> : (
                        <table className="w-full text-xs">
                          <thead className="sticky top-0 bg-muted"><tr className="text-left text-muted-foreground">
                            <th className="px-2 py-1">Policy #</th><th className="px-2 py-1">Client</th><th className="px-2 py-1">Type</th>
                            <th className="px-2 py-1 text-right">Collected</th><th className="px-2 py-1 text-right">Rate %</th><th className="px-2 py-1 text-right">Commission</th>
                          </tr></thead>
                          <tbody>
                            {s.lines.map((l, i) => (
                              <tr key={l.id ?? i} className="border-t">
                                <td className="px-2 py-1">{l.policy_number}</td>
                                <td className="px-2 py-1">{l.client_name}</td>
                                <td className="px-2 py-1">{l.transaction_type?.replace('_', ' ')}</td>
                                <td className="px-2 py-1 text-right tabular-nums">{money(l.amount_collected)}</td>
                                <td className="px-2 py-1 text-right tabular-nums">{l.commission_rate}</td>
                                <td className="px-2 py-1 text-right tabular-nums font-medium">{money(l.commission_amount)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      )}
                    </div>
                  )}
                </div>
              ))}
          </CardContent></Card>
          </>
        )}
      </div>
    </div>
  )
}
