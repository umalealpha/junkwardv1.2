'use client'

/** /app/monthly-feedback · /m/staff/monthly-feedback — a manager gives each direct
 * report their short monthly note from the phone (CFO 4-Sep-2026: "I should be able
 * to give feedback through this"). Mirrors the desktop page
 * frontend/src/app/(dashboard)/hris/monthly-feedback/page.tsx exactly: same two
 * endpoints, same fields, same body — nothing new on the server.
 *   GET  /hris/api/performance/monthly/?year&month   who is due, the facts panel, a draft
 *   POST /hris/api/performance/checkins/             the note (one per person per month)
 * A low rating needs the "did not do well" box (the server requires concerns + evidence).
 * Every figure shown is the server's. */
import { useCallback, useEffect, useState } from 'react'
import { CheckCircle2, ChevronLeft, ChevronRight, Save } from 'lucide-react'
import { ApiError, hfetch, reauthOn401 } from '@/app/(customer)/api'
import { C, serif } from '@/app/(customer)/ui'
import { useStaffBase } from './useStaffBase'
import {
  Card, RetryBanner, ScreenFrame, ServerMessage, Toast, errText, formatServerErrors,
  ghostBtn, inputStyle, labelStyle, primaryBtn, rawHrisFetch,
} from './StaffFormKit'
import { localYmd } from '@/lib/utils'

interface Panel { tracked_hours: number; absent_days: number; leave_days: number; sick_days: number; tasks_completed: number; tasks_on_time: number; tasks_on_time_pct: number | null }
interface Target { target_id: string; metric: string; target_value: number | null; actual_value: number | null; unit: string }
interface Draft { strengths?: string; concerns?: string; support_provided?: string }
interface Emp { profile_id: string; name: string; position: string; panel: Panel; targets: Target[]; feedback_given: boolean; checkin_locked: boolean; draft: Draft | null }
interface Monthly { tier: string; period: { year: number; month: number }; employees: Emp[] }

const RATINGS: [string, string][] = [
  ['EX', 'Exceeds expectations'], ['ME', 'Meets expectations'], ['PA', 'Partially meets'],
  ['BE', 'Below expectations'], ['SB', 'Significantly below'],
]
const LOW = new Set(['BE', 'SB'])
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
const fmt = (v: number | null, unit: string) => v === null || v === undefined ? '' : `${Number.isInteger(v) ? v : v.toFixed(2)}${unit ? ` ${unit}` : ''}`

export default function MonthlyFeedbackScreen() {
  const base = useStaffBase()
  const now = new Date()
  const [year, setYear] = useState(now.getFullYear())
  const [month, setMonth] = useState(now.getMonth() + 1)
  const [data, setData] = useState<Monthly | null>(null)
  const [loadErr, setLoadErr] = useState<string | null>(null)
  const [forbidden, setForbidden] = useState<string | null>(null)
  const [open, setOpen] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  const show = (m: string) => { setToast(m); setTimeout(() => setToast(null), 4000) }

  const load = useCallback(() => {
    setLoadErr(null); setData(null)
    hfetch<Monthly>(`/performance/monthly/?year=${year}&month=${month}`)
      .then(d => { setData(d); setOpen(o => o || d.employees.find(e => !e.feedback_given)?.profile_id || null) })
      .catch((e: unknown) => {
        if (reauthOn401(e)) return
        if (e instanceof ApiError && e.status === 403) { setForbidden(errText(e, '') || 'Monthly feedback is for managers with direct reports (and HR).'); return }
        setLoadErr(errText(e, 'Could not load your team.'))
      })
  }, [year, month])
  useEffect(() => { load() }, [load])

  const step = (d: number) => { let m = month + d, y = year; if (m < 1) { m = 12; y-- } if (m > 12) { m = 1; y++ }; setMonth(m); setYear(y); setOpen(null) }
  const isCurrent = year === now.getFullYear() && month === now.getMonth() + 1
  const due = data?.employees.filter(e => !e.feedback_given).length ?? 0

  if (forbidden) return (
    <ScreenFrame title="Monthly feedback" base={base}>
      <ServerMessage tone="warn" text={forbidden} />
    </ScreenFrame>
  )

  return (
    <ScreenFrame title="Monthly feedback" base={base}>
      <p style={{ margin: 0, fontSize: 13, color: C.inkSoft, lineHeight: 1.5 }}>A short note for each of your team: what went well, what did not, where to improve. Judge on the facts shown. Confidential to you, HR and the execs.</p>

      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <button onClick={() => step(-1)} aria-label="Previous month" style={{ ...ghostBtn, minWidth: 44, padding: 0 }}><ChevronLeft size={18} /></button>
        <b style={{ flex: 1, textAlign: 'center', fontFamily: serif, fontSize: 18, color: C.ink }}>{MONTHS[month - 1]} {year}</b>
        <button onClick={() => step(1)} disabled={isCurrent} aria-label="Next month" style={{ ...ghostBtn, minWidth: 44, padding: 0, opacity: isCurrent ? 0.4 : 1 }}><ChevronRight size={18} /></button>
      </div>

      {loadErr && <RetryBanner message={loadErr} onRetry={load} />}
      {data === null && !loadErr && <p style={{ color: C.inkSoft, fontSize: 13, margin: 0 }}>Loading your team…</p>}
      {data && (
        <p style={{ margin: 0, fontSize: 14, fontWeight: 700, color: due ? '#B42318' : '#047857' }}>
          {due === 0 ? 'Everyone has their feedback for this month. Sharp sharp!' : `${due} of ${data.employees.length} still to do`}
        </p>
      )}

      {data?.employees.map(emp => (
        <EmpCard key={emp.profile_id} emp={emp} year={year} month={month} open={open === emp.profile_id}
          onToggle={() => setOpen(o => o === emp.profile_id ? null : emp.profile_id)}
          onSaved={() => {
            setData(d => d ? { ...d, employees: d.employees.map(e => e.profile_id === emp.profile_id ? { ...e, feedback_given: true } : e) } : d)
            show(`Feedback saved for ${emp.name}.`)
            setOpen(data.employees.find(e => !e.feedback_given && e.profile_id !== emp.profile_id)?.profile_id || null)
          }} />
      ))}
      <Toast text={toast} />
    </ScreenFrame>
  )
}

function EmpCard({ emp, year, month, open, onToggle, onSaved }: { emp: Emp; year: number; month: number; open: boolean; onToggle: () => void; onSaved: () => void }) {
  const [rating, setRating] = useState('ME')
  const [well, setWell] = useState(emp.draft?.strengths || '')
  const [notwell, setNotwell] = useState(emp.draft?.concerns || '')
  const [improve, setImprove] = useState(emp.draft?.support_provided || '')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const isLow = LOW.has(rating)
  const canSave = !emp.feedback_given && (isLow ? !!notwell.trim() : !!(well.trim() || notwell.trim() || improve.trim()))
  const p = emp.panel
  const chips: [string, string, string?][] = [
    ['Tracked', `${p.tracked_hours.toFixed(1)}h`],
    ['Absence', `${p.absent_days}d`, p.absent_days > 0 ? '#B42318' : undefined],
    ['Leave', `${p.leave_days}d`],
    ['Sick', `${p.sick_days}d`, p.sick_days > 0 ? '#B45309' : undefined],
    ['Tasks on time', p.tasks_completed ? `${p.tasks_on_time}/${p.tasks_completed} (${p.tasks_on_time_pct}%)` : '—',
      p.tasks_on_time_pct !== null && p.tasks_on_time_pct < 60 ? '#B42318' : undefined],
  ]

  async function save() {
    if (!canSave || busy) return
    setBusy(true); setErr(null)
    try {
      // Same body as the desktop page — the server is the single rule-keeper.
      const objectives = emp.targets.map(t => ({ description: t.metric, target: fmt(t.target_value, t.unit), result: t.actual_value === null ? '' : fmt(t.actual_value, t.unit) }))
      const r = await rawHrisFetch('/performance/checkins/', {
        method: 'POST',
        body: JSON.stringify({
          profile: emp.profile_id, period_month: month, period_year: year,
          conversation_date: localYmd(new Date()),
          overall_rating: rating, objectives,
          strengths: well, concerns: notwell, support_provided: improve,
          evidence: isLow ? notwell : '',
        }),
      })
      if (!r.ok) { setErr(formatServerErrors(r.body, r.status)); return }
      onSaved()
    } catch (e) { if (!reauthOn401(e)) setErr(errText(e, 'Could not save.')) }
    finally { setBusy(false) }
  }

  return (
    <Card style={{ padding: 14 }}>
      <button onClick={onToggle} style={{ width: '100%', minHeight: 44, textAlign: 'left', background: 'none', border: 'none', padding: 0, cursor: 'pointer' }} aria-expanded={open}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <b style={{ color: C.ink, fontSize: 15.5, display: 'block' }}>{emp.name}</b>
            <span style={{ color: C.inkSoft, fontSize: 12.5 }}>{emp.position || '—'}</span>
          </div>
          {emp.feedback_given
            ? <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, color: '#047857', fontSize: 12.5, fontWeight: 800 }}><CheckCircle2 size={16} /> Given</span>
            : <span style={{ color: '#B42318', fontSize: 12.5, fontWeight: 800 }}>Due</span>}
        </div>
      </button>

      {open && (
        <div style={{ marginTop: 10, paddingTop: 10, borderTop: `1px solid ${C.line}` }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {chips.map(([k, v, col]) => (
              <span key={k} style={{ fontSize: 12, background: '#F0F2F5', borderRadius: 999, padding: '4px 10px', color: col || C.ink }}><span style={{ color: C.inkSoft }}>{k} </span><b>{v}</b></span>
            ))}
          </div>
          {emp.targets.length > 0 && (
            <ul style={{ margin: '10px 0 0', padding: '0 0 0 16px', fontSize: 12.5, color: C.inkSoft, lineHeight: 1.6 }}>
              {emp.targets.map(t => <li key={t.target_id}><b style={{ color: C.ink }}>{t.metric}</b>: target {fmt(t.target_value, t.unit) || '—'}{t.actual_value !== null ? `, actual ${fmt(t.actual_value, t.unit)}` : ''}</li>)}
            </ul>
          )}

          {emp.feedback_given ? (
            <p style={{ margin: '12px 0 0', fontSize: 13, color: C.inkSoft }}>This month&apos;s feedback is already given{emp.checkin_locked ? ' and signed' : ''}. Change it on the computer if you must.</p>
          ) : (
            <>
              <label style={labelStyle} htmlFor={`r-${emp.profile_id}`}>Overall rating</label>
              <select id={`r-${emp.profile_id}`} value={rating} onChange={e => setRating(e.target.value)} style={inputStyle}>
                {RATINGS.map(([c, l]) => <option key={c} value={c}>{l}</option>)}
              </select>
              <label style={labelStyle} htmlFor={`w-${emp.profile_id}`}>What they did well</label>
              <textarea id={`w-${emp.profile_id}`} value={well} onChange={e => setWell(e.target.value)} rows={3} style={{ ...inputStyle, resize: 'vertical' }} />
              <label style={labelStyle} htmlFor={`n-${emp.profile_id}`}>What they did not do well{isLow ? ' (required for a low rating)' : ''}</label>
              <textarea id={`n-${emp.profile_id}`} value={notwell} onChange={e => setNotwell(e.target.value)} rows={3} style={{ ...inputStyle, resize: 'vertical', ...(isLow && !notwell.trim() ? { boxShadow: `inset 0 0 0 2px ${C.orange}` } : {}) }} />
              <label style={labelStyle} htmlFor={`i-${emp.profile_id}`}>Where to improve</label>
              <textarea id={`i-${emp.profile_id}`} value={improve} onChange={e => setImprove(e.target.value)} rows={3} style={{ ...inputStyle, resize: 'vertical' }} />
              {err && <div style={{ marginTop: 8 }}><ServerMessage tone="error" text={err} /></div>}
              <button onClick={save} disabled={!canSave || busy} style={{ ...primaryBtn(busy || !canSave), marginTop: 12 }}>
                <Save size={18} /> {busy ? 'Saving…' : `Save feedback for ${emp.name.split(' ')[0]}`}
              </button>
            </>
          )}
        </div>
      )}
    </Card>
  )
}
