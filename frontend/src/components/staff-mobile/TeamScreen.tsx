'use client'

/** /app/team · /m/staff/team — a manager's team on the phone (CFO 2026-09-03).
 * One glance: who is in, who is on leave, who is short on hours,
 * what is overdue. Tap a person for their tasks (confirm finished work) and to
 * flag a roster problem to HR. Wording rule: the system never names the CFO as
 * enforcer — it says "HR" or "your manager". */
import { useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { AlertTriangle, ArrowLeft, CheckCircle2, ChevronRight, Flag, MessageSquareText, X } from 'lucide-react'
import { sfetch, hfetch, ApiError, reauthOn401 } from '@/app/(customer)/api'
import { C, serif, sans, card, headerPad } from '@/app/(customer)/ui'
import { useStaffBase } from './useStaffBase'
import { RetryBanner, Toast, ghostBtn, errText } from './StaffFormKit'

interface Report {
  employee_id: string; user_id: number | null; name: string; job_title: string
  on_leave_today: boolean; leave_type: string | null; online: boolean
  dark_days_7: number; open_tasks: number; overdue_tasks: number
  short_days_7: number; awaiting_review_7: number
}
interface Glance { as_of: string; day_name: string; is_workday: boolean; reports: Report[]; counts: { in: number; on_leave: number; dark: number; overdue: number; overdue_people: number; awaiting_me: number } }
interface OverviewTask {
  id: string; title: string; status: string; due_at: string | null; due_time: string | null
  assignee: number; assignee_name: string; is_overdue: boolean; completed_at: string | null
}
interface Overview { as_of: string; tasks: OverviewTask[] }
interface MonthlyRow { profile_id: string; name: string; feedback_given: boolean; checkin_locked: boolean }
interface Monthly { tier: string; employees: MonthlyRow[] }

const FLAG_KINDS: { value: string; label: string }[] = [
  { value: 'not_mine', label: 'Not reporting to me' },
  { value: 'resigned', label: 'Resigned / left the company' },
  { value: 'long_leave', label: 'On long leave — exclude this month' },
  { value: 'wrong_info', label: 'Job title or details are wrong' },
  { value: 'other', label: 'Something else' },
]
const STATUS_LABEL: Record<string, string> = {
  pending: 'Not started', in_progress: 'In progress', partial: 'Partly done', blocked: 'Blocked', done: 'Finished',
}
const GREEN = '#047857', AMBER = '#B45309', RED = '#B91C1C', GREY = '#9CA3AF'

const firstInitial = (full: string) => {
  const parts = full.trim().split(/\s+/)
  return parts.length > 1 ? `${parts[0]} ${parts[parts.length - 1][0]}.` : parts[0]
}

export default function TeamScreen() {
  const base = useStaffBase()
  const [glance, setGlance] = useState<Glance | null>(null)
  const [overview, setOverview] = useState<Overview | null>(null)
  const [overviewDenied, setOverviewDenied] = useState(false)
  const [explanations, setExplanations] = useState<number | null>(null)
  const [monthly, setMonthly] = useState<Monthly | null>(null)
  const [loadErr, setLoadErr] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [openFor, setOpenFor] = useState<Report | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  const show = (m: string) => { setToast(m); setTimeout(() => setToast(null), 4000) }

  const load = useCallback(() => {
    setLoadErr('')
    const now = new Date()
    const g = sfetch<Glance>('/team/glance/').then(setGlance)
    const o = sfetch<Overview>('/taskboard/overview/?week=this')
      .then(d => { setOverview(d); setOverviewDenied(false) })
      .catch((e: unknown) => {
        if (reauthOn401(e)) return
        if (e instanceof ApiError && e.status === 403) { setOverviewDenied(true); return }
        throw e
      })
    // Count only — the review itself lives on its own screen.
    sfetch<{ items: unknown[] }>('/timedoctor/justifications/pending/?no_ai=1')
      .then(d => setExplanations(d.items.length)).catch(() => setExplanations(null))
    // May be feature-flagged off (403) — then the section simply does not show.
    hfetch<Monthly>(`/performance/monthly/?year=${now.getFullYear()}&month=${now.getMonth() + 1}`)
      .then(setMonthly).catch(() => setMonthly(null))
    Promise.all([g, o])
      .catch((e: unknown) => { if (!reauthOn401(e)) setLoadErr(errText(e, 'Could not load your team.')) })
      .finally(() => setLoaded(true))
  }, [])
  useEffect(() => { load() }, [load])

  const reports = glance?.reports ?? []
  const noTeam = loaded && !loadErr && reports.length === 0 && overviewDenied
  const counts = glance?.counts ?? { in: 0, on_leave: 0, dark: 0, overdue: 0, overdue_people: 0, awaiting_me: 0 }
  const feedbackDue = useMemo(() => (monthly?.employees ?? []).filter(e => !e.feedback_given), [monthly])
  const shortReports = reports.filter(r => r.short_days_7 > 0).length
  const todayCaption = glance
    ? (glance.is_workday
        ? `${counts.overdue} overdue task${counts.overdue === 1 ? '' : 's'} · ${counts.awaiting_me} explanation${counts.awaiting_me === 1 ? '' : 's'} waiting for you`
        : `${glance.day_name} — nothing expected today`)
    : ''

  const tasksFor = useCallback((r: Report): OverviewTask[] => {
    const all = overview?.tasks ?? []
    return all.filter(t => (r.user_id !== null ? t.assignee === r.user_id : t.assignee_name === r.name))
  }, [overview])

  return (
    // Same frame as ScreenFrame, but the title is a real <h1> — this is a whole
    // tab of its own, not a sub-screen, and axe wants a level-one heading.
    <div style={{ background: C.surface, minHeight: '100vh', fontFamily: sans }}>
      <header style={{ display: 'flex', alignItems: 'center', gap: 12, padding: headerPad, background: C.navy, color: '#fff' }}>
        <Link href={base} aria-label="Back" style={{ color: '#fff', display: 'flex', minWidth: 44, minHeight: 44, alignItems: 'center' }}><ArrowLeft size={20} /></Link>
        <h1 style={{ fontFamily: serif, fontWeight: 800, fontSize: 20, margin: 0 }}>My team</h1>
      </header>
      <main style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 14 }}>
      {loadErr && <RetryBanner message={loadErr} onRetry={load} />}

      {noTeam && (
        <div style={{ ...card, padding: 22, textAlign: 'center' }}>
          <p style={{ margin: 0, color: C.ink, fontWeight: 700, fontSize: 16 }}>This tab is for people with a team.</p>
          <p style={{ margin: '6px 0 0', color: C.inkSoft, fontSize: 13 }}>If you do manage people and they are not showing, ask HR to check who reports to you.</p>
        </div>
      )}

      {!noTeam && (
        <>
          <section aria-label="Team today" style={{ ...card, padding: '14px 12px' }}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: 4 }}>
              <Count n={counts.in} label="online now" color={GREEN} />
              <Count n={counts.on_leave} label="on leave" color={AMBER} />
              <Count n={shortReports} label="short on hours" color={RED} />
              <Count n={counts.overdue_people} label="overdue" color={RED} />
            </div>
            <p style={{ margin: '8px 0 0', color: C.inkSoft, fontSize: 12, textAlign: 'center' }}>{todayCaption}</p>
          </section>

          <Section title="People" hint={reports.length ? `${reports.length} report${reports.length === 1 ? '' : 's'}` : undefined}>
            {!loaded && <Muted>Loading your team…</Muted>}
            {loaded && reports.length === 0 && <Muted>No one reports to you on the roster yet.</Muted>}
            {reports.map((r, i) => (
              <button key={r.employee_id} onClick={() => setOpenFor(r)} className="oa-press"
                aria-label={`${r.name}, ${personStatus(r)}`}
                style={{ display: 'flex', alignItems: 'center', gap: 12, width: '100%', textAlign: 'left', padding: '13px 16px', border: 0, background: 'transparent',
                         borderTop: i ? `1px solid ${C.line}` : 0, cursor: 'pointer', fontFamily: sans, minHeight: 56 }}>
                <span style={{ display: 'grid', placeItems: 'center', width: 40, height: 40, borderRadius: 14, background: 'var(--ao-orange-wash, rgba(240,127,0,0.14))', color: C.head, fontWeight: 800, fontSize: 14, flexShrink: 0 }} aria-hidden>
                  {r.name.trim().split(/\s+/).map(p => p[0]).slice(0, 2).join('')}
                </span>
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span style={{ display: 'block', color: C.ink, fontWeight: 700, fontSize: 15, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.name}</span>
                  <span style={{ display: 'block', color: C.inkSoft, fontSize: 12.5, marginTop: 2 }}>{r.job_title || '—'}</span>
                </span>
                <span style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }} aria-hidden>
                  <Dot color={r.online ? GREEN : GREY} title={r.online ? 'online' : 'not online'} />
                  {r.on_leave_today && <Dot color={AMBER} title="on leave" />}
                  {r.short_days_7 > 0 && <span style={{ background: RED, color: '#fff', fontWeight: 700, borderRadius: 999, padding: '2px 8px', fontSize: 11 }}>{r.short_days_7}d short</span>}
                  {r.awaiting_review_7 > 0 && <span style={{ background: C.orange, color: C.navy, fontWeight: 700, borderRadius: 999, padding: '2px 8px', fontSize: 11 }}>{r.awaiting_review_7} to review</span>}
                  {r.overdue_tasks > 0 && <span style={{ background: RED, color: '#fff', fontWeight: 800, borderRadius: 999, padding: '2px 8px', fontSize: 12 }}>{r.overdue_tasks}</span>}
                  <ChevronRight size={18} color={C.inkSoft} />
                </span>
              </button>
            ))}
          </Section>

          {explanations !== null && (
            <Section title="Explanations to review">
              <Link href={`${base}/review-explanations`} className="oa-press" style={rowLink}>
                <AlertTriangle size={20} color={explanations ? AMBER : C.inkSoft} />
                <span style={{ flex: 1, fontWeight: 600, fontSize: 15, color: C.ink }}>
                  {explanations === 0 ? 'Nothing waiting on you' : `${explanations} explanation${explanations === 1 ? '' : 's'} to decide`}
                </span>
                {explanations > 0 && <Badge n={explanations} />}
                <ChevronRight size={18} color={C.inkSoft} />
              </Link>
            </Section>
          )}

          {monthly && (
            <Section title="Feedback due this month">
              {feedbackDue.length === 0
                ? <Muted>Everyone has their monthly feedback. Sharp sharp!</Muted>
                : (
                  <>
                    <p style={{ margin: 0, padding: '12px 16px 0', color: C.ink, fontSize: 14, lineHeight: 1.5 }}>
                      {feedbackDue.map(e => e.name).join(', ')}
                    </p>
                    <Link href={`${base}/monthly-feedback`} style={{ ...rowLink, color: C.head }}>
                      <MessageSquareText size={20} color={C.head} />
                      <span style={{ flex: 1, fontWeight: 700, fontSize: 15 }}>Give feedback now</span>
                      <ChevronRight size={18} color={C.inkSoft} />
                    </Link>
                  </>
                )}
            </Section>
          )}
        </>
      )}

      {/* Toast lives inside <main> so it stays within a landmark (axe "region");
          it is position:fixed, so DOM placement changes nothing visually. */}
      <Toast text={toast} />
      </main>
      {openFor && (
        <PersonSheet report={openFor} tasks={tasksFor(openFor)} tasksDenied={overviewDenied}
          onClose={() => setOpenFor(null)} onToast={show} onChanged={load} />
      )}
    </div>
  )
}

function personStatus(r: Report): string {
  const bits = [
    r.online ? 'online' : 'not online',
    r.on_leave_today ? 'on leave' : '',
    r.short_days_7 ? `${r.short_days_7} day${r.short_days_7 === 1 ? '' : 's'} short on hours` : '',
    r.awaiting_review_7 ? `${r.awaiting_review_7} explanation${r.awaiting_review_7 === 1 ? '' : 's'} waiting for you` : '',
    r.overdue_tasks ? `${r.overdue_tasks} overdue task${r.overdue_tasks === 1 ? '' : 's'}` : ''
  ].filter(Boolean)
  return bits.join(', ')
}

const rowLink: React.CSSProperties = { display: 'flex', alignItems: 'center', gap: 12, padding: '14px 16px', textDecoration: 'none', color: C.ink, minHeight: 52 }

function Count({ n, label, color }: { n: number; label: string; color: string }) {
  return (
    <div style={{ textAlign: 'center' }}>
      <div style={{ fontFamily: serif, fontSize: 30, fontWeight: 800, lineHeight: 1, color: n ? color : C.inkSoft }}>{n}</div>
      <div style={{ fontSize: 11, color: C.inkSoft, marginTop: 4, letterSpacing: '0.04em', textTransform: 'uppercase', fontWeight: 600 }}>{label}</div>
    </div>
  )
}
function Dot({ color, title }: { color: string; title: string }) {
  return <span title={title} style={{ width: 10, height: 10, borderRadius: 999, background: color, display: 'inline-block' }} />
}
function Badge({ n }: { n: number }) {
  return <span style={{ background: C.orange, color: C.navy, fontWeight: 800, borderRadius: 999, padding: '3px 10px', fontSize: 13 }}>{n}</span>
}
function Muted({ children }: { children: React.ReactNode }) {
  return <p style={{ margin: 0, padding: '14px 16px', color: C.inkSoft, fontSize: 13.5 }}>{children}</p>
}
function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section aria-label={title} style={{ ...card, padding: 0, overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', padding: '14px 16px 6px' }}>
        <h2 style={{ fontFamily: serif, fontSize: 17, fontWeight: 800, color: C.ink, margin: 0 }}>{title}</h2>
        {hint && <span style={{ fontSize: 12, color: C.inkSoft }}>{hint}</span>}
      </div>
      {children}
    </section>
  )
}

/** Bottom sheet for one report: their tasks (Confirm on finished work) + Flag. */
function PersonSheet({ report, tasks, tasksDenied, onClose, onToast, onChanged }: {
  report: Report; tasks: OverviewTask[]; tasksDenied: boolean
  onClose: () => void; onToast: (m: string) => void; onChanged: () => void
}) {
  const [confirmed, setConfirmed] = useState<Set<string>>(new Set())
  const [busyId, setBusyId] = useState<string | null>(null)
  const [flagging, setFlagging] = useState(false)
  const [kind, setKind] = useState('')
  const [note, setNote] = useState('')
  const [flagBusy, setFlagBusy] = useState(false)
  const [flagSent, setFlagSent] = useState(false)

  async function confirm(t: OverviewTask) {
    setBusyId(t.id)
    try {
      await sfetch(`/taskboard/tasks/${t.id}/feedback/`, {
        method: 'POST', body: JSON.stringify({ body: 'Confirmed as done from the phone.', status: 'done' }) })
      setConfirmed(s => new Set(s).add(t.id))
      onToast('Confirmed — it counts for them now. ✅')
      onChanged()
    } catch (e) { if (!reauthOn401(e)) onToast(errText(e, 'Could not confirm that task.')) }
    finally { setBusyId(null) }
  }

  async function sendFlag() {
    if (!kind) { onToast('Choose a reason first.'); return }
    setFlagBusy(true)
    try {
      await hfetch('/roster-flags/', { method: 'POST', body: JSON.stringify({ employee_id: report.employee_id, kind, note: note.trim() }) })
      setFlagSent(true); setFlagging(false)
    } catch (e) { if (!reauthOn401(e)) onToast(errText(e, 'Could not send the flag.')) }
    finally { setFlagBusy(false) }
  }

  const open = tasks.filter(t => t.status !== 'done' && t.status !== 'cancelled')
  const finished = tasks.filter(t => t.status === 'done')

  return (
    <div role="dialog" aria-modal="true" aria-label={report.name} onClick={onClose}
      style={{ position: 'fixed', inset: 0, background: 'rgba(15,28,44,0.55)', display: 'flex', alignItems: 'flex-end', zIndex: 60 }}>
      <div onClick={e => e.stopPropagation()}
        style={{ background: C.card, width: '100%', maxWidth: 480, margin: '0 auto', maxHeight: '88vh', overflowY: 'auto', borderRadius: '22px 22px 0 0', padding: '20px 18px 30px', fontFamily: sans }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 10 }}>
          <div>
            <h2 style={{ fontFamily: serif, fontSize: 20, color: C.ink, margin: 0, lineHeight: 1.2 }}>{report.name}</h2>
            <p style={{ margin: '3px 0 0', color: C.inkSoft, fontSize: 13 }}>{report.job_title || '—'} · {personStatus(report)}</p>
          </div>
          <button onClick={onClose} aria-label="Close" style={{ background: 'none', border: 'none', color: C.inkSoft, cursor: 'pointer', minWidth: 44, minHeight: 44, display: 'grid', placeItems: 'center' }}><X size={20} /></button>
        </div>

        {report.on_leave_today && <Note tone="amber">On {report.leave_type?.toLowerCase() || 'leave'} today.</Note>}
        {report.short_days_7 > 0 && (
          <Note tone="red">Short on hours {report.short_days_7} day{report.short_days_7 === 1 ? '' : 's'} this week.</Note>)}

        <h3 style={h3}>Tasks this week</h3>
        {tasksDenied && <p style={muted}>Their task list is on the task dashboard on your computer.</p>}
        {!tasksDenied && tasks.length === 0 && <p style={muted}>No tasks from you this week.</p>}
        {open.map(t => <TaskRow key={t.id} t={t} />)}
        {finished.length > 0 && <h3 style={{ ...h3, marginTop: 14 }}>Finished — confirm</h3>}
        {finished.map(t => (
          <TaskRow key={t.id} t={t} action={
            confirmed.has(t.id)
              ? <span style={{ color: GREEN, fontWeight: 800, fontSize: 13, display: 'flex', alignItems: 'center', gap: 4 }}><CheckCircle2 size={15} /> Confirmed</span>
              : <button onClick={() => confirm(t)} disabled={busyId === t.id} className="oa-press"
                  style={{ minHeight: 40, padding: '0 14px', borderRadius: 999, border: 0, background: GREEN, color: '#fff', fontWeight: 800, fontSize: 13, cursor: 'pointer', fontFamily: sans, opacity: busyId === t.id ? 0.6 : 1 }}>
                  {busyId === t.id ? '…' : 'Confirm'}
                </button>} />
        ))}

        <div style={{ marginTop: 18, borderTop: `1px solid ${C.line}`, paddingTop: 14 }}>
          {flagSent
            ? <Note tone="ok">Sent to HR — {firstInitial(report.name)} stays on your list until HR decides.</Note>
            : !flagging
              ? <button onClick={() => setFlagging(true)} style={{ ...ghostBtn, display: 'flex', alignItems: 'center', gap: 8 }}><Flag size={15} /> Flag a roster problem</button>
              : (
                <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
                  <legend style={{ fontWeight: 700, fontSize: 14, color: C.ink, marginBottom: 8 }}>What is wrong?</legend>
                  <div style={{ display: 'grid', gap: 6 }}>
                    {FLAG_KINDS.map(k => (
                      <label key={k.value} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 12px', borderRadius: 12, border: `1px solid ${kind === k.value ? C.orange : C.line}`, background: kind === k.value ? '#FFF7ED' : '#fff', fontSize: 14, color: C.ink, minHeight: 44 }}>
                        <input type="radio" name="flag-kind" value={k.value} checked={kind === k.value} onChange={() => setKind(k.value)} />
                        {k.label}
                      </label>
                    ))}
                  </div>
                  <label htmlFor="flag-note" style={{ display: 'block', fontSize: 12, fontWeight: 700, color: C.inkSoft, margin: '10px 0 6px' }}>Note for HR (optional)</label>
                  <textarea id="flag-note" value={note} onChange={e => setNote(e.target.value)} rows={2}
                    style={{ width: '100%', boxSizing: 'border-box', padding: '12px 14px', border: 'none', background: '#F0F2F5', borderRadius: 12, fontSize: 15, color: C.ink, fontFamily: sans, resize: 'vertical' }} />
                  <p style={{ ...muted, padding: '8px 0 0' }}>They stay on your list — HR decides, the roster does not change until then.</p>
                  <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
                    <button onClick={() => setFlagging(false)} style={ghostBtn}>Cancel</button>
                    <button onClick={sendFlag} disabled={flagBusy} className="oa-press"
                      style={{ flex: 1, minHeight: 44, borderRadius: 999, border: 0, background: C.orange, color: C.navy, fontWeight: 800, fontSize: 14, cursor: 'pointer', fontFamily: sans, opacity: flagBusy ? 0.6 : 1 }}>
                      {flagBusy ? 'Sending…' : 'Send to HR'}
                    </button>
                  </div>
                </fieldset>
              )}
        </div>
      </div>
    </div>
  )
}

const h3: React.CSSProperties = { fontSize: 12, letterSpacing: '0.08em', textTransform: 'uppercase', color: C.inkSoft, fontWeight: 700, margin: '16px 0 6px' }
const muted: React.CSSProperties = { margin: 0, color: C.inkSoft, fontSize: 13.5 }

function TaskRow({ t, action }: { t: OverviewTask; action?: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 0', borderBottom: `1px solid ${C.line}` }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ color: C.ink, fontWeight: 600, fontSize: 14.5, lineHeight: 1.3 }}>{t.title}</div>
        <div style={{ color: t.is_overdue ? RED : C.inkSoft, fontSize: 12.5, marginTop: 2, fontWeight: t.is_overdue ? 700 : 400 }}>
          {t.is_overdue ? 'OVERDUE · ' : ''}{STATUS_LABEL[t.status] || t.status}{t.due_at ? ` · due ${t.due_at}${t.due_time ? ` ${t.due_time.slice(0, 5)}` : ''}` : ''}
        </div>
      </div>
      {action}
    </div>
  )
}

function Note({ tone, children }: { tone: 'amber' | 'red' | 'ok'; children: React.ReactNode }) {
  const bg = tone === 'ok' ? '#ECFDF5' : tone === 'red' ? '#FEF2F2' : '#FFFBEB'
  const fg = tone === 'ok' ? '#065F46' : tone === 'red' ? '#991B1B' : '#92400E'
  return <p role="status" style={{ background: bg, color: fg, borderRadius: 12, padding: '10px 12px', fontSize: 13, lineHeight: 1.5, margin: '12px 0 0' }}>{children}</p>
}
