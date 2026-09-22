'use client'

/** /app/explain-day · /m/staff/explain-day — explain a short Time Doctor day
 * from your phone, or plan a day off in advance. Drives the SAME endpoints as
 * the desktop /hris/my-brief page (my-brief / justify / plan-absence); every
 * rule (approved-leave evidence, the 1.5h meeting cap, manager review) lives
 * on the server — this screen only mirrors it and shows the server's words.
 * The hours shown are the signed-in person's own: the endpoint scopes to the
 * caller, nobody else's day is ever fetched here. */
import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { ArrowLeft, CalendarDays, CheckCircle2, Clock } from 'lucide-react'
import { sfetch, ApiError, reauthOn401 } from '@/app/(customer)/api'
import { C, serif, sans, card, headerPad, safeBottom } from '@/app/(customer)/ui'
import { useStaffBase } from './useStaffBase'
import { inputStyle, labelStyle, primaryBtn, ghostBtn, RetryBanner, ServerMessage, Toast, errText } from './StaffFormKit'

interface BriefDay { work_date: string; required: string; tracked: string; shortfall: string }
interface Brief { employee: string | null; days: BriefDay[] }
interface JustifyResult { work_date: string; status: string; justified_hours: string; pending_review: boolean }
interface PlanResult { work_date: string; status: string; planned: boolean }
type Reason = 'on_leave' | 'external_meeting' | 'client_visit' | 'other'
interface JustifyBody {
  work_date: string; reason: Reason; justification?: string; meeting_minutes?: number; location?: string
  client_name?: string; client_reason?: string; client_outcome?: string; amount?: number
}
interface PlanBody { work_date: string; reason: Reason; justification?: string; location?: string; client_name?: string; client_reason?: string }

const REASONS: { key: Reason; label: string; hint: string }[] = [
  { key: 'on_leave',         label: 'On leave',         hint: 'Needs leave that is already approved' },
  { key: 'external_meeting', label: 'External meeting', hint: 'Off-site — covers at most 1.5 hours' },
  { key: 'client_visit',     label: 'Client visit',     hint: 'Seeing a client or prospect' },
  { key: 'other',            label: 'Other',            hint: 'Explain in your own words' },
]
const MINUTE_STEPS = [15, 30, 45, 60, 90]
const MIN_WORDS_OTHER = 3

const localISO = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
const plusDays = (n: number) => { const d = new Date(); d.setDate(d.getDate() + n); return localISO(d) }
const niceDate = (iso: string) => {
  const d = new Date(`${iso}T00:00:00`)
  return isNaN(d.getTime()) ? iso : d.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' })
}
const hrs = (s: string) => { const n = Number(s); return isFinite(n) ? `${n.toFixed(2).replace(/\.?0+$/, '')}h` : `${s}h` }
const wordCount = (s: string) => s.trim().split(/\s+/).filter(Boolean).length

const chipStyle = (on: boolean): React.CSSProperties => ({
  minHeight: 44, padding: '10px 14px', borderRadius: 999, fontWeight: 700, fontSize: 13, cursor: 'pointer', fontFamily: sans,
  border: 'none', background: on ? C.navy : '#fff', color: on ? '#fff' : C.ink,
  boxShadow: on ? 'none' : `inset 0 0 0 1px ${C.line}`,
})
const sheetWrap: React.CSSProperties = { position: 'fixed', inset: 0, background: 'rgba(15,28,44,0.55)', display: 'flex', alignItems: 'flex-end', zIndex: 65 }
const sheetBody: React.CSSProperties = { background: C.card, width: '100%', maxHeight: '92vh', overflowY: 'auto', borderRadius: '22px 22px 0 0', padding: `20px 18px calc(24px + ${safeBottom})` }

/** What the server decided, in plain words. `pending_review` beats status. */
function outcomeText(day: BriefDay, r: JustifyResult): { text: string; tone: 'ok' | 'warn' } {
  if (r.pending_review) return { text: 'Sent to your manager to review.', tone: 'ok' }
  if (r.status === 'justified' || r.status === 'met' || r.status === 'not_required') return { text: 'Cleared — nothing more to do for this day.', tone: 'ok' }
  const left = Number(day.shortfall) - Number(r.justified_hours)
  const n = isFinite(left) && left > 0 ? left.toFixed(2).replace(/\.?0+$/, '') : day.shortfall
  return { text: `Still short by ${n} hours. That time counts against your leave unless HR hears otherwise.`, tone: 'warn' }
}

export default function ExplainDayScreen() {
  const base = useStaffBase()
  const [tab, setTab] = useState<'explain' | 'plan'>('explain')
  const [days, setDays] = useState<BriefDay[] | null>(null)
  const [loadErr, setLoadErr] = useState<string | null>(null)
  const [active, setActive] = useState<BriefDay | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  const [notice, setNotice] = useState<{ text: string; tone: 'ok' | 'warn' } | null>(null)
  const show = (m: string) => { setToast(m); setTimeout(() => setToast(null), 4000) }

  const load = useCallback(() => {
    setLoadErr(null)
    sfetch<Brief>('/timedoctor/my-brief/')
      .then(b => setDays(b.days || []))
      .catch((e: unknown) => { if (reauthOn401(e)) return; setLoadErr(errText(e, 'Could not load your days.')) })
  }, [])
  useEffect(() => { load() }, [load])

  function onExplained(day: BriefDay, r: JustifyResult) {
    setDays(ds => (ds || []).filter(d => d.work_date !== day.work_date))
    setActive(null)
    setNotice(outcomeText(day, r))
  }

  return (
    <div style={{ background: C.surface, minHeight: '100vh', fontFamily: sans }}>
      <header style={{ display: 'flex', alignItems: 'center', gap: 12, padding: headerPad, background: C.navy, color: '#fff' }}>
        <Link href={base} aria-label="Back" style={{ color: '#fff', display: 'flex', minWidth: 44, minHeight: 44, alignItems: 'center' }}><ArrowLeft size={20} /></Link>
        <h1 style={{ fontFamily: serif, fontWeight: 800, fontSize: 20, margin: 0, color: '#fff' }}>My hours</h1>
      </header>

      <main style={{ padding: '14px 16px 16px', display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div role="tablist" aria-label="My hours" style={{ display: 'flex', gap: 8, padding: 0 }}>
          {([['explain', 'Explain a day'], ['plan', 'Plan a day off']] as const).map(([k, label]) => (
            <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)}
              style={{ flex: 1, minHeight: 44, padding: '10px 4px', borderRadius: 999, border: 'none', fontWeight: 700, fontSize: 13, cursor: 'pointer', fontFamily: sans,
                background: tab === k ? C.navy : '#fff', color: tab === k ? '#fff' : C.inkSoft, boxShadow: tab === k ? 'none' : `inset 0 0 0 1px ${C.line}` }}>
              {label}
            </button>
          ))}
        </div>

        {tab === 'explain' && (
          <>
            <p style={{ color: C.inkSoft, fontSize: 13, margin: 0, lineHeight: 1.5 }}>
              Days in the last 14 where Time Doctor tracked fewer hours than required. Tap a day to say why — an unexplained day counts against your leave.
            </p>
            {notice && <ServerMessage text={notice.text} tone={notice.tone} />}
            {loadErr && <RetryBanner message={loadErr} onRetry={load} />}
            {days === null && !loadErr && <p style={{ color: C.inkSoft, fontSize: 13, textAlign: 'center', margin: '24px 0' }}>Loading…</p>}
            {days && days.length === 0 && (
              <div style={{ ...card, padding: 24, textAlign: 'center' }}>
                <CheckCircle2 size={30} style={{ color: '#047857' }} aria-hidden="true" />
                <p style={{ margin: '8px 0 0', fontWeight: 700, color: C.ink, fontSize: 15 }}>Nothing to explain — your last 14 days are clear.</p>
              </div>
            )}
            {days && days.map(d => (
              <button key={d.work_date} onClick={() => { setNotice(null); setActive(d) }}
                aria-label={`Explain ${niceDate(d.work_date)}: ${hrs(d.tracked)} tracked of ${hrs(d.required)} required, ${hrs(d.shortfall)} short`}
                style={{ ...card, padding: '14px 16px', display: 'flex', alignItems: 'center', gap: 12, textAlign: 'left', cursor: 'pointer', fontFamily: sans, width: '100%', minHeight: 44 }}>
                <div style={{ width: 40, height: 40, borderRadius: 12, background: '#FFF7ED', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                  <CalendarDays size={20} style={{ color: C.orangeDeep }} aria-hidden="true" />
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 800, color: C.ink, fontSize: 15 }}>{niceDate(d.work_date)}</div>
                  <div style={{ color: C.inkSoft, fontSize: 12.5, marginTop: 2 }}>{hrs(d.tracked)} tracked of {hrs(d.required)} required</div>
                </div>
                <span style={{ fontSize: 12, fontWeight: 800, color: '#B91C1C', whiteSpace: 'nowrap' }}>{hrs(d.shortfall)} short</span>
              </button>
            ))}
          </>
        )}

        {tab === 'plan' && <PlanCard onDone={(msg) => { setNotice({ text: msg, tone: 'ok' }); setTab('explain'); show('Saved.') }} />}

        {active && <ExplainSheet day={active} onClose={() => setActive(null)} onDone={onExplained} />}
        <Toast text={toast} />
      </main>
    </div>
  )
}

/** Bottom sheet — one day, one reason, the fields that reason needs. */
function ExplainSheet({ day, onClose, onDone }: { day: BriefDay; onClose: () => void; onDone: (d: BriefDay, r: JustifyResult) => void }) {
  const [reason, setReason] = useState<Reason | null>(null)
  const [minutes, setMinutes] = useState('')
  const [location, setLocation] = useState('')
  const [why, setWhy] = useState('')
  const [clientName, setClientName] = useState('')
  const [clientReason, setClientReason] = useState('')
  const [clientOutcome, setClientOutcome] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const mins = parseInt(minutes, 10)
  const minsBad = !Number.isFinite(mins) || mins < 1 || mins > 90
  // A new reason starts clean — never send the previous reason's text along.
  const pick = (r: Reason) => { setReason(r); setMinutes(''); setLocation(''); setWhy(''); setClientName(''); setClientReason(''); setClientOutcome(''); setErr(null) }

  // Mirrors the server's 400s so the obvious ones never round-trip; the server stays the judge.
  function precheck(): string | null {
    if (!reason) return 'Pick a reason first.'
    if (reason === 'external_meeting' && minsBad) return 'Meeting length must be 1–90 minutes.'
    if (reason === 'external_meeting' && !why.trim()) return 'Say what the meeting was about.'
    if (reason === 'client_visit' && !clientName.trim()) return 'Enter the client name.'
    if (reason === 'other' && wordCount(why) < MIN_WORDS_OTHER) return 'Write a few words explaining the day.'
    return null
  }

  async function submit() {
    const p = precheck(); if (p) { setErr(p); return }
    if (!reason) return
    const body: JustifyBody = { work_date: day.work_date, reason }
    if (why.trim()) body.justification = why.trim()
    if (location.trim()) body.location = location.trim()
    if (reason === 'external_meeting') body.meeting_minutes = mins
    if (reason === 'client_visit') {
      body.client_name = clientName.trim()
      if (clientReason.trim()) body.client_reason = clientReason.trim()
      if (clientOutcome.trim()) body.client_outcome = clientOutcome.trim()
    }
    setBusy(true); setErr(null)
    try {
      const r = await sfetch<JustifyResult>('/timedoctor/justify/', { method: 'POST', body: JSON.stringify(body) })
      onDone(day, r)
    } catch (e) { if (reauthOn401(e)) return; setErr(errText(e, 'Could not send — try again.')) }
    finally { setBusy(false) }
  }

  return (
    <div style={sheetWrap} onClick={() => !busy && onClose()}>
      <div role="dialog" aria-modal="true" aria-labelledby="explain-title" onClick={e => e.stopPropagation()} style={sheetBody}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <Clock size={18} style={{ color: C.orangeDeep }} aria-hidden="true" />
          <h2 id="explain-title" style={{ fontFamily: serif, fontSize: 18, color: C.ink, margin: 0 }}>Explain {niceDate(day.work_date)}</h2>
        </div>
        <p style={{ color: C.inkSoft, fontSize: 12.5, margin: '6px 0 14px', lineHeight: 1.5 }}>
          Required <b style={{ color: C.ink }}>{hrs(day.required)}</b> · tracked <b style={{ color: C.ink }}>{hrs(day.tracked)}</b> · <b style={{ color: '#B91C1C' }}>{hrs(day.shortfall)} short</b>
        </p>

        <div role="group" aria-label="Reason" style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          {REASONS.map(o => (
            <button key={o.key} aria-pressed={reason === o.key} onClick={() => pick(o.key)} style={chipStyle(reason === o.key)}>{o.label}</button>
          ))}
        </div>
        {reason && <p style={{ color: C.inkSoft, fontSize: 12, margin: '8px 0 0' }}>{REASONS.find(r => r.key === reason)?.hint}</p>}

        {reason === 'external_meeting' && (
          <>
            <span id="mins-label" style={labelStyle}>Meeting length (minutes)</span>
            <div role="group" aria-labelledby="mins-label" style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              {MINUTE_STEPS.map(m => (
                <button key={m} aria-pressed={minutes === String(m)} onClick={() => { setMinutes(String(m)); setErr(null) }} style={{ ...chipStyle(minutes === String(m)), minWidth: 56 }}>{m}</button>
              ))}
            </div>
            <label htmlFor="mins-free" style={labelStyle}>Or type the minutes (1–90)</label>
            <input id="mins-free" type="number" inputMode="numeric" min={1} max={90} value={minutes} onChange={e => { setMinutes(e.target.value); setErr(null) }} style={inputStyle} placeholder="e.g. 50" />
            <label htmlFor="meet-where" style={labelStyle}>Where was it? (optional)</label>
            <input id="meet-where" value={location} onChange={e => setLocation(e.target.value)} style={inputStyle} placeholder="e.g. Botswana Insurance House" />
            <label htmlFor="meet-why" style={labelStyle}>What was the meeting about?</label>
            <textarea id="meet-why" value={why} onChange={e => { setWhy(e.target.value); setErr(null) }} rows={2} style={{ ...inputStyle, resize: 'vertical' }} />
          </>
        )}

        {reason === 'client_visit' && (
          <>
            <label htmlFor="cv-name" style={labelStyle}>Client name</label>
            <input id="cv-name" value={clientName} onChange={e => { setClientName(e.target.value); setErr(null) }} style={inputStyle} placeholder="Who did you see?" />
            <label htmlFor="cv-reason" style={labelStyle}>Why did you see them? (optional)</label>
            <textarea id="cv-reason" value={clientReason} onChange={e => setClientReason(e.target.value)} rows={2} style={{ ...inputStyle, resize: 'vertical' }} />
            <label htmlFor="cv-outcome" style={labelStyle}>What happened afterwards? (optional)</label>
            <textarea id="cv-outcome" value={clientOutcome} onChange={e => setClientOutcome(e.target.value)} rows={2} style={{ ...inputStyle, resize: 'vertical' }} />
            <label htmlFor="cv-where" style={labelStyle}>Where? (optional)</label>
            <input id="cv-where" value={location} onChange={e => setLocation(e.target.value)} style={inputStyle} placeholder="e.g. Gaborone CBD" />
          </>
        )}

        {reason === 'on_leave' && (
          <>
            <p style={{ fontSize: 12.5, color: C.ink, margin: '12px 0 0', background: '#F6F7F9', borderRadius: 10, padding: '9px 11px', lineHeight: 1.5 }}>
              This only clears the day if leave covering it is already approved. If not, apply for leave first.
            </p>
            <label htmlFor="leave-note" style={labelStyle}>Anything to add (optional)</label>
            <textarea id="leave-note" value={why} onChange={e => setWhy(e.target.value)} rows={2} style={{ ...inputStyle, resize: 'vertical' }} />
          </>
        )}

        {reason === 'other' && (
          <>
            <label htmlFor="other-why" style={labelStyle}>What happened? (a few words at least)</label>
            <textarea id="other-why" value={why} onChange={e => { setWhy(e.target.value); setErr(null) }} rows={3} style={{ ...inputStyle, resize: 'vertical' }} placeholder="Explain in your own words" />
            <label htmlFor="other-where" style={labelStyle}>Where were you? (optional)</label>
            <input id="other-where" value={location} onChange={e => setLocation(e.target.value)} style={inputStyle} />
          </>
        )}

        {err && <div style={{ marginTop: 12 }}><ServerMessage text={err} tone="error" /></div>}

        <div style={{ display: 'flex', gap: 10, marginTop: 16 }}>
          <button onClick={onClose} disabled={busy} style={{ ...ghostBtn, flex: 1 }}>Cancel</button>
          <button onClick={submit} disabled={busy || !reason} style={{ ...primaryBtn(busy || !reason), flex: 2, width: 'auto' }}>{busy ? 'Sending…' : 'Send explanation'}</button>
        </div>
      </div>
    </div>
  )
}

/** Plan a day off (today … +60). External meeting is not offered here by
 * design — the server rejects it: a whole planned day would bypass the 1.5h cap. */
function PlanCard({ onDone }: { onDone: (msg: string) => void }) {
  const today = localISO(new Date())
  const [workDate, setWorkDate] = useState(plusDays(1))
  const [reason, setReason] = useState<Reason | null>(null)
  const [why, setWhy] = useState('')
  const [location, setLocation] = useState('')
  const [clientName, setClientName] = useState('')
  const [clientReason, setClientReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const pick = (r: Reason) => { setReason(r); setWhy(''); setLocation(''); setClientName(''); setClientReason(''); setErr(null) }

  function precheck(): string | null {
    if (!workDate || workDate < today) return 'Pick today or an upcoming day.'
    if (workDate > plusDays(60)) return 'You can plan up to 60 days ahead.'
    if (!reason) return 'Pick a reason first.'
    if (reason === 'client_visit' && !clientName.trim()) return 'Enter the client name.'
    if (reason === 'other' && !why.trim()) return 'Add a short reason.'
    return null
  }

  async function submit() {
    const p = precheck(); if (p) { setErr(p); return }
    if (!reason) return
    const body: PlanBody = { work_date: workDate, reason }
    if (why.trim()) body.justification = why.trim()
    if (location.trim()) body.location = location.trim()
    if (reason === 'client_visit') { body.client_name = clientName.trim(); if (clientReason.trim()) body.client_reason = clientReason.trim() }
    setBusy(true); setErr(null)
    try {
      const r = await sfetch<PlanResult>('/timedoctor/plan-absence/', { method: 'POST', body: JSON.stringify(body) })
      onDone(r.status === 'justified'
        ? `${niceDate(r.work_date)} is covered by your approved leave.`
        : `Noted — you'll be out on ${niceDate(r.work_date)}. Your manager sees the reason and you won't be flagged for low hours that day.`)
    } catch (e) {
      if (reauthOn401(e)) return
      setErr(e instanceof ApiError ? e.message : errText(e, 'Could not save — try again.'))
    } finally { setBusy(false) }
  }

  return (
    <div style={{ ...card, padding: 18 }}>
      <p style={{ color: C.inkSoft, fontSize: 13, margin: '0 0 6px', lineHeight: 1.5 }}>Out of office soon? Say so now and that day won&apos;t be flagged for low hours.</p>
      <label htmlFor="plan-date" style={labelStyle}>Which day?</label>
      <input id="plan-date" type="date" value={workDate} min={today} max={plusDays(60)} onChange={e => { setWorkDate(e.target.value); setErr(null) }} style={inputStyle} />
      <span id="plan-reason-label" style={labelStyle}>Why will you be out?</span>
      <div role="group" aria-labelledby="plan-reason-label" style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
        {REASONS.filter(o => o.key !== 'external_meeting').map(o => (
          <button key={o.key} aria-pressed={reason === o.key} onClick={() => pick(o.key)} style={chipStyle(reason === o.key)}>{o.label}</button>
        ))}
      </div>
      {reason === 'on_leave' && (
        <p style={{ fontSize: 12.5, color: C.ink, margin: '12px 0 0', background: '#F6F7F9', borderRadius: 10, padding: '9px 11px', lineHeight: 1.5 }}>
          Checked against your approved leave. If it isn&apos;t approved yet, apply for leave first.
        </p>
      )}
      {reason === 'client_visit' && (
        <>
          <label htmlFor="plan-client" style={labelStyle}>Client name</label>
          <input id="plan-client" value={clientName} onChange={e => { setClientName(e.target.value); setErr(null) }} style={inputStyle} placeholder="Who are you seeing?" />
          <label htmlFor="plan-client-why" style={labelStyle}>Why? (optional)</label>
          <input id="plan-client-why" value={clientReason} onChange={e => setClientReason(e.target.value)} style={inputStyle} />
        </>
      )}
      {reason && (
        <>
          <label htmlFor="plan-note" style={labelStyle}>{reason === 'other' ? 'Reason' : 'Anything to add (optional)'}</label>
          <textarea id="plan-note" value={why} onChange={e => { setWhy(e.target.value); setErr(null) }} rows={2} style={{ ...inputStyle, resize: 'vertical' }}
            placeholder={reason === 'other' ? 'e.g. Baker Tilly golf day' : ''} />
          <label htmlFor="plan-where" style={labelStyle}>Where? (optional)</label>
          <input id="plan-where" value={location} onChange={e => setLocation(e.target.value)} style={inputStyle} />
        </>
      )}
      {err && <div style={{ marginTop: 12 }}><ServerMessage text={err} tone="error" /></div>}
      <button onClick={submit} disabled={busy || !reason} style={{ ...primaryBtn(busy || !reason), marginTop: 16 }}>{busy ? 'Saving…' : 'Save the plan'}</button>
    </div>
  )
}
