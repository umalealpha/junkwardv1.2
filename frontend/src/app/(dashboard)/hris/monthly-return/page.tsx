'use client'

/**
 * /hris/monthly-return — the Monthly Manager Return (CFO 2026-07-26).
 *
 * Short and sweet, deliberately. The top half is FACTS omni already knows about
 * the manager's team (Time Doctor hours vs expectation, who lost hours without
 * leave or an explanation, tasks, their own hours) — read-only, never typed. The
 * bottom half is a role-specific question set: an IT manager gets IT questions,
 * AML gets KYC, Claims gets SLA. Nobody outside a revenue-facing role is asked
 * about sales.
 *
 * Task lands on the 1st -> manager submits by the 5th -> their manager clears by
 * the 10th. It rolls up into the Development Dialogue at year end.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import {
  AlertTriangle, ArrowUpRight, CheckCircle2, ChevronLeft, Clock, Lock,
  Send, Sparkles, Users as UsersIcon,
} from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { authedHrisFetch } from '../_shared'

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
  'August', 'September', 'October', 'November', 'December']

interface TeamRow {
  name: string; job_title?: string
  required_hours: number; tracked_hours: number; shortfall_hours: number
  hours_pct: number | null
  unexplained_days: number; unanswered_days: number
  leave_days: number; sick_days: number
  tasks_assigned: number; tasks_completed: number; tasks_on_time_pct: number | null
  employee_id?: string
  flag?: RosterFlagMark | null
}
interface RosterFlagMark {
  flag_id: string; kind: string; kind_label: string; note: string; waiting_on: string
}
interface CoReviewedRow {
  profile_id: string; employee_id: string; name: string; job_title: string
  line_manager: string | null; my_share: number; line_share: number
  line_score: number | null; co_score: number | null
  combined: number | null; complete: boolean; waiting_on: string[]
  spread: number | null; disputed: boolean
}
interface AdditionalReviewedRow {
  profile_id: string; employee_id: string; name: string; job_title: string
  line_manager: string | null
  my_additional_score: number | null; my_additional_comment: string
  combined: number | null; complete: boolean
}
interface Question { key: string; kind: string; label: string; required?: boolean; help?: string; comment_key?: string; source?: string }
interface Block { title: string; questions: Question[] }
interface ReturnData {
  id: string; manager: string; period_year: number; period_month: number
  status: string; status_label: string; is_locked: boolean; frozen: boolean
  submit_due: string; clear_due: string
  team: TeamRow[]; own: Partial<TeamRow> & { name?: string }
  headcount: number; headcount_equivalent: number | null; offenders: string[]
  co_reviewed: CoReviewedRow[]
  additional_reviewed: AdditionalReviewedRow[]
  question_spec: { blocks?: Block[]; department_matched?: string; asks_sales?: boolean; asks_sla?: boolean }
  dept_answers: Record<string, unknown>
  missing: string[]
  prior_commitments: string; prior_outcome: string; next_month_commitment: string
  reviewer_verdict: string; reviewer_notes: string; submitted_to: string | null
  [k: string]: unknown
}

const BASE_COLUMN_FIELDS = new Set([
  'leave_action', 'work_finished_on_time', 'work_on_time_comment',
  'dashboard_cleared_on_time', 'overstaffed', 'overstaffed_comment',
  'fy27_aligned', 'fy27_actions', 'innovation', 'sales_target_met',
  'new_sales_amount', 'sla_breaches', 'sla_explanation', 'tasks_comment',
])

export default function MonthlyReturnPage() {
  const router = useRouter()
  const { theme } = useTheme()
  const [data, setData] = useState<ReturnData | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [noTeam, setNoTeam] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [vals, setVals] = useState<Record<string, unknown>>({})
  const [extras, setExtras] = useState<Record<string, unknown>>({})
  const [flagFor, setFlagFor] = useState<TeamRow | null>(null)
  const [flagKind, setFlagKind] = useState('not_mine')
  const [flagNote, setFlagNote] = useState('')
  const [scores, setScores] = useState<Record<string, string>>({})

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await authedHrisFetch('/hris/api/manager-return/')
      const j = await r.json()
      if (j.no_team) { setNoTeam(true); return }
      if (!r.ok) { setMsg({ ok: false, text: j.detail || 'Could not load your return.' }); return }
      setData(j)
      const next: Record<string, unknown> = {}
      BASE_COLUMN_FIELDS.forEach(f => { next[f] = j[f] ?? (f.endsWith('_comment') || f.endsWith('_action') ? '' : null) })
      next.prior_outcome = j.prior_outcome || ''
      next.next_month_commitment = j.next_month_commitment || ''
      setVals(next)
      setExtras({ ...(j.dept_answers || {}) })
    } catch {
      setMsg({ ok: false, text: 'Could not reach omni. Try again in a moment.' })
    } finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

  async function post(submitIt: boolean) {
    if (!data) return
    setSaving(true); setMsg(null)
    try {
      const r = await authedHrisFetch('/hris/api/manager-return/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...vals, dept_answers: extras, submit: submitIt }),
      })
      const j = await r.json()
      if (!r.ok) {
        const errs = j.errors
        const flat = errs && typeof errs === 'object'
          ? Object.values(errs).flat().join(' ') : (j.detail || 'Could not save.')
        setMsg({ ok: false, text: String(flat) })
      } else {
        setData(j)
        setMsg({ ok: true, text: submitIt ? 'Sent to your manager.' : 'Saved.' })
      }
    } catch {
      setMsg({ ok: false, text: 'Could not save — check your connection.' })
    } finally { setSaving(false) }
  }

  async function submitFlag() {
    if (!flagFor?.employee_id) return
    setSaving(true); setMsg(null)
    try {
      const r = await authedHrisFetch('/hris/api/roster-flags/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ employee_id: flagFor.employee_id, kind: flagKind, note: flagNote }),
      })
      const j = await r.json()
      if (!r.ok) {
        const flat = j.errors ? Object.values(j.errors).flat().join(' ') : (j.detail || 'Could not send.')
        setMsg({ ok: false, text: String(flat) })
      } else {
        setMsg({ ok: true, text: `Sent to Unami to decide. ${flagFor.name} stays on your list until she does.` })
        setFlagFor(null); setFlagNote(''); setFlagKind('not_mine')
        load()
      }
    } catch {
      setMsg({ ok: false, text: 'Could not send — check your connection.' })
    } finally { setSaving(false) }
  }

  async function saveScore(row: { profile_id: string; name: string }) {
    const raw = scores[row.profile_id]
    if (raw === undefined || raw === '') return
    setSaving(true); setMsg(null)
    try {
      const r = await authedHrisFetch('/hris/api/co-review/rate/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          profile_id: row.profile_id, score: raw,
          year: data?.period_year, month: data?.period_month,
        }),
      })
      const j = await r.json()
      if (!r.ok) setMsg({ ok: false, text: String(j.detail || 'Could not save the score.') })
      else { setMsg({ ok: true, text: `Score saved for ${row.name}.` }); load() }
    } catch {
      setMsg({ ok: false, text: 'Could not save — check your connection.' })
    } finally { setSaving(false) }
  }

  const blocks = data?.question_spec?.blocks || []
  const readOnly = !!data && (data.frozen || data.is_locked)
  const period = data ? `${MONTHS[data.period_month - 1]} ${data.period_year}` : ''

  const capacity = useMemo(() => {
    if (!data || data.headcount_equivalent == null) return null
    const gap = data.headcount - data.headcount_equivalent
    return { equiv: data.headcount_equivalent, gap, flag: gap >= 1 }
  }, [data])

  if (noTeam) {
    return (
      <div className="flex flex-col flex-1 min-h-0">
        <TopBar title="Monthly Manager Return" breadcrumbs={[{ label: 'HRIS', href: '/hris' }, { label: 'Monthly Return' }]} />
        <main className="flex-1 p-6">
          <div className="rounded-2xl p-8 text-center" style={{ background: theme.card, border: `1px solid ${theme.cardBdr}` }}>
            <UsersIcon className="w-8 h-8 mx-auto mb-3" style={{ color: theme.t2 }} />
            <p style={{ color: theme.text }}>You have no direct reports, so there is no return to file.</p>
            <Link href="/hris" className="inline-block mt-4 text-sm font-semibold" style={{ color: theme.orange }}>Back to HRIS</Link>
          </div>
        </main>
      </div>
    )
  }

  if (loading || !data) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ backgroundColor: theme.bg }}>
        <div className="w-8 h-8 border-2 border-[#F07F00] border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  const card = { background: theme.card, border: `1px solid ${theme.cardBdr}` }
  const inputStyle = {
    background: theme.g100, border: `1px solid ${theme.cardBdr}`, color: theme.text,
  }

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <TopBar title="Monthly Manager Return"
        breadcrumbs={[{ label: 'HRIS', href: '/hris' }, { label: 'Monthly Return' }]} />
      <main className="flex-1 overflow-y-auto p-6 space-y-5 max-w-6xl">

        <div className="flex items-center justify-between gap-4 flex-wrap">
          <Link href="/hris" className="inline-flex items-center gap-1.5 text-sm font-medium" style={{ color: theme.t2 }}>
            <ChevronLeft className="w-4 h-4" /> Back to HRIS
          </Link>
          <div className="flex items-center gap-2 text-xs" style={{ color: theme.t2 }}>
            <Clock className="w-3.5 h-3.5" />
            Due to your manager by {data.submit_due} · cleared by {data.clear_due}
          </div>
        </div>

        {/* header */}
        <div className="rounded-2xl p-5" style={card}>
          <div className="flex items-start justify-between gap-4 flex-wrap">
            <div>
              <h1 className="text-xl font-bold" style={{ color: theme.text }}>{period}</h1>
              <p className="text-sm mt-0.5" style={{ color: theme.t2 }}>
                {data.manager} · {data.headcount} in your team
                {data.submitted_to ? ` · goes to ${data.submitted_to}` : ''}
              </p>
            </div>
            <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold"
              style={{ background: theme.oL, color: theme.orange }}>
              {data.is_locked && <Lock className="w-3 h-3" />}
              {data.status_label}
            </span>
          </div>
          {data.reviewer_notes && (
            <p className="mt-3 text-sm rounded-lg p-3" style={{ background: theme.g100, color: theme.text }}>
              <strong>Your manager:</strong> {data.reviewer_notes}
              {data.reviewer_verdict ? ` (${data.reviewer_verdict.replace('_', ' ')})` : ''}
            </p>
          )}
        </div>

        {/* ---------- FACTS: the team ---------- */}
        <section className="rounded-2xl p-5 space-y-3" style={card}>
          <h2 className="text-sm font-bold uppercase tracking-wider" style={{ color: theme.t2 }}>
            Your team this month — from omni, not typed
          </h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-[11px] uppercase tracking-wider" style={{ color: theme.t2 }}>
                  <th className="py-2 pr-3 font-semibold">Name</th>
                  <th className="py-2 pr-3 font-semibold text-right">Expected</th>
                  <th className="py-2 pr-3 font-semibold text-right">Actual</th>
                  <th className="py-2 pr-3 font-semibold text-right">%</th>
                  <th className="py-2 pr-3 font-semibold text-right">Short</th>
                  <th className="py-2 pr-3 font-semibold text-right">No leave, no reason</th>
                  <th className="py-2 pr-3 font-semibold text-right">Leave</th>
                  <th className="py-2 pr-3 font-semibold text-right">Tasks on time</th>
                  <th className="py-2 pr-3 font-semibold text-right">Not mine?</th>
                </tr>
              </thead>
              <tbody>
                {data.team.length === 0 && (
                  <tr><td colSpan={8} className="py-6 text-center" style={{ color: theme.t2 }}>No team data for this month.</td></tr>
                )}
                {data.team.map(r => {
                  const bad = (r.unexplained_days || 0) + (r.unanswered_days || 0) > 0
                  const pct = r.hours_pct
                  const dot = pct == null ? theme.t2 : pct >= 95 ? '#15803d' : pct >= 80 ? '#F47C20' : '#b91c1c'
                  return (
                    <tr key={r.name} className="border-t" style={{ borderColor: theme.cardBdr }}>
                      <td className="py-2.5 pr-3">
                        <span className="inline-flex items-center gap-2">
                          <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ background: dot }} />
                          <span className="font-medium" style={{ color: theme.text }}>{r.name}</span>
                        </span>
                      </td>
                      <td className="py-2.5 pr-3 text-right tabular-nums" style={{ color: theme.t2 }}>{r.required_hours}h</td>
                      <td className="py-2.5 pr-3 text-right tabular-nums" style={{ color: theme.text }}>{r.tracked_hours}h</td>
                      <td className="py-2.5 pr-3 text-right tabular-nums font-semibold" style={{ color: dot }}>
                        {pct == null ? '—' : `${pct}%`}
                      </td>
                      <td className="py-2.5 pr-3 text-right tabular-nums" style={{ color: theme.t2 }}>
                        {r.shortfall_hours ? `${r.shortfall_hours}h` : '—'}
                      </td>
                      <td className="py-2.5 pr-3 text-right tabular-nums font-semibold"
                        style={{ color: bad ? '#b91c1c' : theme.t2 }}>
                        {bad ? (r.unexplained_days + r.unanswered_days) : '—'}
                      </td>
                      <td className="py-2.5 pr-3 text-right tabular-nums" style={{ color: theme.t2 }}>{r.leave_days || '—'}</td>
                      <td className="py-2.5 pr-3 text-right tabular-nums" style={{ color: theme.t2 }}>
                        {r.tasks_on_time_pct == null ? '—' : `${r.tasks_on_time_pct}%`}
                        <span className="text-[10px] ml-1">({r.tasks_completed}/{r.tasks_assigned})</span>
                      </td>
                      {/* CFO 2026-07-26: one click to say "this person isn't mine"
                          or "they've left". They STAY on the roster; it goes to
                          Unami to decide. */}
                      <td className="py-2.5 pr-3 text-right">
                        {r.flag ? (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold whitespace-nowrap"
                            style={{ background: theme.oL, color: theme.orange }}
                            title={`${r.flag.kind_label}${r.flag.note ? ' — ' + r.flag.note : ''}`}>
                            <Clock className="w-2.5 h-2.5" /> {r.flag.waiting_on}
                          </span>
                        ) : readOnly ? (
                          <span style={{ color: theme.t2 }}>—</span>
                        ) : (
                          <button type="button"
                            onClick={() => setFlagFor(r)}
                            className="px-2 py-0.5 rounded text-[11px] font-semibold"
                            style={{ background: theme.g100, color: theme.t2, border: `1px solid ${theme.cardBdr}` }}
                            title="Not reporting to me / has left">
                            Flag
                          </button>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          {/* capacity signal + own hours */}
          <div className="grid gap-3 sm:grid-cols-2 pt-1">
            <div className="rounded-xl p-3" style={{ background: capacity?.flag ? '#FEF2F2' : theme.g100 }}>
              <p className="text-[11px] uppercase tracking-wider font-semibold" style={{ color: theme.t2 }}>Capacity</p>
              {capacity ? (
                <p className="text-sm mt-1" style={{ color: capacity.flag ? '#b91c1c' : theme.text }}>
                  Your <strong>{data.headcount}</strong> people delivered{' '}
                  <strong>{capacity.equiv}</strong> people worth of hours.
                  {capacity.flag && ' Answer the overstaffing question against this.'}
                </p>
              ) : (
                <p className="text-sm mt-1" style={{ color: theme.t2 }}>
                  Not enough tracked time this month to work this out.
                </p>
              )}
            </div>
            <div className="rounded-xl p-3" style={{ background: theme.g100 }}>
              <p className="text-[11px] uppercase tracking-wider font-semibold" style={{ color: theme.t2 }}>Your own month</p>
              <p className="text-sm mt-1" style={{ color: theme.text }}>
                {data.own?.tracked_hours ?? 0}h of {data.own?.required_hours ?? 0}h
                {data.own?.hours_pct != null ? ` (${data.own.hours_pct}%)` : ''}
                {data.own?.tasks_on_time_pct != null ? ` · tasks ${data.own.tasks_on_time_pct}% on time` : ''}
              </p>
            </div>
          </div>

          {data.offenders && data.offenders.length > 0 && (
            <p className="text-sm rounded-lg p-3 flex items-start gap-2" style={{ background: '#FEF2F2', color: '#b91c1c' }}>
              <AlertTriangle className="w-4 h-4 mt-0.5 flex-shrink-0" />
              <span><strong>Lost hours with no leave and no explanation:</strong> {data.offenders.join(', ')}.
                You must say below what you did about it.</span>
            </p>
          )}
        </section>

        {/* ---------- people I share a rating on ---------- */}
        {data.co_reviewed && data.co_reviewed.length > 0 && (
          <section className="rounded-2xl p-5 space-y-3" style={card}>
            <h2 className="text-sm font-bold uppercase tracking-wider" style={{ color: theme.t2 }}>
              People you rate jointly
            </h2>
            <p className="text-xs" style={{ color: theme.t2 }}>
              These people do not report to you — you work with them, so you and their
              line manager each score them and the two are averaged. Their hours and
              leave stay their line manager&apos;s business.
            </p>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-[11px] uppercase tracking-wider" style={{ color: theme.t2 }}>
                    <th className="py-2 pr-3 font-semibold">Name</th>
                    <th className="py-2 pr-3 font-semibold">Reports to</th>
                    <th className="py-2 pr-3 font-semibold">Your score (0-100)</th>
                    <th className="py-2 pr-3 font-semibold text-right">Combined</th>
                    <th className="py-2 pr-3 font-semibold">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {data.co_reviewed.map(row => (
                    <tr key={row.profile_id} className="border-t" style={{ borderColor: theme.cardBdr }}>
                      <td className="py-2.5 pr-3">
                        <span className="font-medium" style={{ color: theme.text }}>{row.name}</span>
                        <span className="block text-[11px]" style={{ color: theme.t2 }}>{row.job_title}</span>
                      </td>
                      <td className="py-2.5 pr-3" style={{ color: theme.t2 }}>{row.line_manager || '—'}</td>
                      <td className="py-2.5 pr-3">
                        <div className="flex items-center gap-2">
                          <input type="number" min="0" max="100" disabled={readOnly}
                            value={scores[row.profile_id] ?? (row.co_score ?? '')}
                            onChange={e => setScores(s => ({ ...s, [row.profile_id]: e.target.value }))}
                            className="w-20 rounded-lg px-2 py-1.5 text-sm outline-none tabular-nums"
                            style={inputStyle} />
                          {!readOnly && (
                            <button type="button" disabled={saving}
                              onClick={() => saveScore(row)}
                              className="px-2.5 py-1 rounded text-[11px] font-semibold disabled:opacity-60"
                              style={{ background: theme.orange, color: '#fff' }}>
                              Save
                            </button>
                          )}
                          <span className="text-[10px]" style={{ color: theme.t2 }}>
                            {row.my_share}%
                          </span>
                        </div>
                      </td>
                      <td className="py-2.5 pr-3 text-right tabular-nums font-semibold"
                        style={{ color: row.complete ? theme.text : theme.t2 }}>
                        {row.combined == null ? '—' : row.combined}
                      </td>
                      <td className="py-2.5 pr-3 text-[11px]">
                        {row.complete ? (
                          row.disputed ? (
                            <span style={{ color: '#b91c1c' }}>
                              Both in — {row.spread} apart, worth a conversation
                            </span>
                          ) : <span style={{ color: '#15803d' }}>Both in</span>
                        ) : (
                          <span style={{ color: theme.t2 }}>
                            Waiting on {row.waiting_on.join(' and ')}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {data.additional_reviewed && data.additional_reviewed.length > 0 && (
          <section className="rounded-2xl p-5 space-y-3" style={card}>
            <h2 className="text-sm font-bold uppercase tracking-wider" style={{ color: theme.t2 }}>
              People you additionally review
            </h2>
            <p className="text-xs" style={{ color: theme.t2 }}>
              You oversee these people across departments — they report to someone else.
              Your feedback is recorded for them, but it does <b>not</b> change their line
              manager&apos;s score, and you do not approve their leave.
            </p>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-[11px] uppercase tracking-wider" style={{ color: theme.t2 }}>
                    <th className="py-2 pr-3 font-semibold">Name</th>
                    <th className="py-2 pr-3 font-semibold">Reports to</th>
                    <th className="py-2 pr-3 font-semibold">Your feedback (0-100)</th>
                    <th className="py-2 pr-3 font-semibold text-right">Team score</th>
                  </tr>
                </thead>
                <tbody>
                  {data.additional_reviewed.map(row => (
                    <tr key={row.profile_id} className="border-t" style={{ borderColor: theme.cardBdr }}>
                      <td className="py-2.5 pr-3">
                        <span className="font-medium" style={{ color: theme.text }}>{row.name}</span>
                        <span className="block text-[11px]" style={{ color: theme.t2 }}>{row.job_title}</span>
                      </td>
                      <td className="py-2.5 pr-3" style={{ color: theme.t2 }}>{row.line_manager || '—'}</td>
                      <td className="py-2.5 pr-3">
                        <div className="flex items-center gap-2">
                          <input type="number" min="0" max="100" disabled={readOnly}
                            value={scores[row.profile_id] ?? (row.my_additional_score ?? '')}
                            onChange={e => setScores(s => ({ ...s, [row.profile_id]: e.target.value }))}
                            className="w-20 rounded-lg px-2 py-1.5 text-sm outline-none tabular-nums"
                            style={inputStyle} />
                          {!readOnly && (
                            <button type="button" disabled={saving}
                              onClick={() => saveScore(row)}
                              className="px-2.5 py-1 rounded text-[11px] font-semibold disabled:opacity-60"
                              style={{ background: theme.orange, color: '#fff' }}>
                              Save
                            </button>
                          )}
                        </div>
                      </td>
                      <td className="py-2.5 pr-3 text-right tabular-nums"
                        style={{ color: theme.t2 }}>
                        {row.combined == null ? '—' : row.combined}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {/* ---------- flag dialog ---------- */}
        {flagFor && (
          <section className="rounded-2xl p-5 space-y-3" style={{ ...card, borderColor: theme.orange }}>
            <h2 className="text-sm font-bold" style={{ color: theme.text }}>
              {flagFor.name} — what&apos;s wrong?
            </h2>
            <p className="text-xs" style={{ color: theme.t2 }}>
              This goes to Unami to decide. {flagFor.name} stays on your list until she does.
            </p>
            <select value={flagKind} onChange={e => setFlagKind(e.target.value)}
              className="w-full rounded-lg px-3 py-2 text-sm outline-none" style={inputStyle}>
              <option value="not_mine">Not reporting to me</option>
              <option value="resigned">Resigned / left the company</option>
              <option value="long_leave">On long leave — exclude this month</option>
              <option value="wrong_info">Job title or details are wrong</option>
              <option value="other">Something else</option>
            </select>
            <textarea rows={2} value={flagNote} onChange={e => setFlagNote(e.target.value)}
              placeholder={flagKind === 'resigned'
                ? 'When did they leave? (required — HR needs it for final pay)'
                : 'Anything Unami should know'}
              className="w-full rounded-lg px-3 py-2 text-sm outline-none" style={inputStyle} />
            <div className="flex gap-2">
              <button type="button" disabled={saving} onClick={submitFlag}
                className="px-4 py-2 rounded-lg text-sm font-semibold disabled:opacity-60"
                style={{ background: theme.orange, color: '#fff' }}>
                Send to Unami
              </button>
              <button type="button" onClick={() => { setFlagFor(null); setFlagNote('') }}
                className="px-4 py-2 rounded-lg text-sm font-semibold"
                style={{ background: theme.g100, color: theme.text, border: `1px solid ${theme.cardBdr}` }}>
                Cancel
              </button>
            </div>
          </section>
        )}

        {/* ---------- last month's promise ---------- */}
        {data.prior_commitments && (
          <section className="rounded-2xl p-5 space-y-2" style={card}>
            <h2 className="text-sm font-bold uppercase tracking-wider" style={{ color: theme.t2 }}>
              What you said last month
            </h2>
            <p className="text-sm italic rounded-lg p-3" style={{ background: theme.g100, color: theme.text }}>
              “{data.prior_commitments}”
            </p>
            <label className="block text-sm font-medium" style={{ color: theme.text }}>What happened to it?</label>
            <textarea rows={2} disabled={readOnly}
              value={String(vals.prior_outcome ?? '')}
              onChange={e => setVals(v => ({ ...v, prior_outcome: e.target.value }))}
              className="w-full rounded-lg px-3 py-2 text-sm outline-none" style={inputStyle} />
          </section>
        )}

        {/* ---------- QUESTIONS ---------- */}
        {blocks.map(block => (
          <section key={block.title} className="rounded-2xl p-5 space-y-4" style={card}>
            <h2 className="text-sm font-bold uppercase tracking-wider flex items-center gap-2" style={{ color: theme.t2 }}>
              {block.title}
              {block.questions.some(q => q.source === 'aria') && (
                <span className="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded"
                  style={{ background: theme.oL, color: theme.orange }}>
                  <Sparkles className="w-2.5 h-2.5" /> Aria
                </span>
              )}
            </h2>
            {block.questions.map(q => {
              const isColumn = BASE_COLUMN_FIELDS.has(q.key)
              const val = isColumn ? vals[q.key] : extras[q.key]
              const set = (v: unknown) => isColumn
                ? setVals(s => ({ ...s, [q.key]: v }))
                : setExtras(s => ({ ...s, [q.key]: v }))
              return (
                <div key={q.key} className="space-y-1.5">
                  <label className="block text-sm font-medium" style={{ color: theme.text }}>
                    {q.label}{q.required && <span style={{ color: theme.orange }}> *</span>}
                  </label>
                  {q.help && <p className="text-xs" style={{ color: theme.t2 }}>{q.help}</p>}

                  {q.kind === 'bool' ? (
                    <div className="flex gap-2">
                      {[{ l: 'Yes', v: true }, { l: 'No', v: false }].map(o => (
                        <button key={o.l} type="button" disabled={readOnly}
                          onClick={() => set(o.v)}
                          className="px-4 py-1.5 rounded-lg text-sm font-semibold transition-colors disabled:opacity-60"
                          style={val === o.v
                            ? { background: theme.orange, color: '#fff' }
                            : { background: theme.g100, color: theme.t2, border: `1px solid ${theme.cardBdr}` }}>
                          {o.l}
                        </button>
                      ))}
                    </div>
                  ) : q.kind === 'money' ? (
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium" style={{ color: theme.t2 }}>BWP</span>
                      <input type="number" step="0.01" min="0" disabled={readOnly}
                        value={String(val ?? '')}
                        onChange={e => set(e.target.value)}
                        placeholder="0.00"
                        className="w-48 rounded-lg px-3 py-2 text-sm outline-none tabular-nums" style={inputStyle} />
                    </div>
                  ) : q.kind === 'number' ? (
                    <input type="number" min="0" disabled={readOnly}
                      value={String(val ?? '')}
                      onChange={e => set(e.target.value)}
                      className="w-32 rounded-lg px-3 py-2 text-sm outline-none tabular-nums" style={inputStyle} />
                  ) : (
                    <textarea rows={2} disabled={readOnly}
                      value={String(val ?? '')}
                      onChange={e => set(e.target.value)}
                      className="w-full rounded-lg px-3 py-2 text-sm outline-none" style={inputStyle} />
                  )}

                  {q.comment_key && (
                    <textarea rows={2} disabled={readOnly}
                      placeholder="Add a line of detail"
                      value={String(vals[q.comment_key] ?? '')}
                      onChange={e => setVals(s => ({ ...s, [q.comment_key as string]: e.target.value }))}
                      className="w-full rounded-lg px-3 py-2 text-sm outline-none" style={inputStyle} />
                  )}
                </div>
              )
            })}
          </section>
        ))}

        {/* ---------- next month's promise ---------- */}
        <section className="rounded-2xl p-5 space-y-2" style={card}>
          <h2 className="text-sm font-bold uppercase tracking-wider" style={{ color: theme.t2 }}>
            One thing you commit to next month
          </h2>
          <p className="text-xs" style={{ color: theme.t2 }}>
            You will be asked what happened to this, next month.
          </p>
          <textarea rows={2} disabled={readOnly}
            value={String(vals.next_month_commitment ?? '')}
            onChange={e => setVals(v => ({ ...v, next_month_commitment: e.target.value }))}
            className="w-full rounded-lg px-3 py-2 text-sm outline-none" style={inputStyle} />
        </section>

        {/* ---------- actions ---------- */}
        {msg && (
          <p className="text-sm rounded-lg p-3 flex items-start gap-2"
            style={{ background: msg.ok ? '#F0FDF4' : '#FEF2F2', color: msg.ok ? '#15803d' : '#b91c1c' }}>
            {msg.ok ? <CheckCircle2 className="w-4 h-4 mt-0.5 flex-shrink-0" /> : <AlertTriangle className="w-4 h-4 mt-0.5 flex-shrink-0" />}
            {msg.text}
          </p>
        )}

        {!readOnly && data.missing.length > 0 && (
          <div className="rounded-2xl p-4" style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}` }}>
            <p className="text-sm font-semibold mb-1.5" style={{ color: theme.text }}>Still needed before you can send it:</p>
            <ul className="text-sm space-y-0.5" style={{ color: theme.t2 }}>
              {data.missing.map(m => <li key={m}>• {m}</li>)}
            </ul>
          </div>
        )}

        {!readOnly && (
          <div className="flex flex-wrap gap-3 pb-4">
            <button type="button" disabled={saving} onClick={() => post(false)}
              className="px-5 py-2.5 rounded-lg text-sm font-semibold disabled:opacity-60"
              style={{ background: theme.g100, color: theme.text, border: `1px solid ${theme.cardBdr}` }}>
              {saving ? 'Saving…' : 'Save for later'}
            </button>
            <button type="button" disabled={saving || data.missing.length > 0} onClick={() => post(true)}
              className="px-5 py-2.5 rounded-lg text-sm font-semibold inline-flex items-center gap-2 disabled:opacity-50"
              style={{ background: theme.orange, color: '#fff' }}
              title={data.missing.length > 0 ? 'Answer everything above first' : 'Send to your manager'}>
              <Send className="w-4 h-4" /> Send to my manager
            </button>
          </div>
        )}

        {readOnly && (
          <p className="text-sm pb-4 flex items-center gap-2" style={{ color: theme.t2 }}>
            <Lock className="w-4 h-4" />
            {data.is_locked
              ? 'Cleared and locked. It forms part of your Development Dialogue.'
              : 'Sent to your manager — waiting for them to clear it.'}
            <Link href="/hris/my-dialogue" className="inline-flex items-center gap-1 font-semibold" style={{ color: theme.orange }}>
              Development Dialogue <ArrowUpRight className="w-3 h-3" />
            </Link>
          </p>
        )}
      </main>
    </div>
  )
}
