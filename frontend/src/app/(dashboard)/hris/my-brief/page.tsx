'use client'

/**
 * /hris/my-brief — the employee's own Daily Brief + the shortfall
 * justification pop-up (CFO 2026-07-14). When an employee is under the
 * required hours for a day, this asks them why: on leave, external meeting
 * (max 1.5h), client visit (client + potential premium), or other.
 * Alpha Direct house style — navy #0D1B2A + orange #F4A623.
 */
import { useEffect, useState } from 'react'
import { getMyBrief, submitJustification, planAbsence } from '@/lib/api'
import type { MyBrief, MyBriefDay } from '@/lib/api'
import { TopBar } from '@/components/layout/TopBar'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Clock, CalendarDays, Plane, Users, Briefcase, MessageSquare, CheckCircle2, X, CalendarPlus } from 'lucide-react'

const NAVY = '#0D1B2A'
const ORANGE = '#F4A623'
const SERIF = 'Georgia, "Book Antiqua", serif'

type Reason = 'on_leave' | 'external_meeting' | 'client_visit' | 'other'
const OPTIONS: { key: Reason; label: string; hint: string; icon: React.ReactNode }[] = [
  { key: 'on_leave',         label: 'I was on leave',      hint: 'Annual, sick or other approved leave', icon: <Plane className="w-5 h-5" /> },
  { key: 'external_meeting', label: 'External meeting',    hint: 'Off-site meeting — max 1.5 hours',      icon: <Users className="w-5 h-5" /> },
  { key: 'client_visit',     label: 'Client visit',        hint: 'Seeing a client / prospect',            icon: <Briefcase className="w-5 h-5" /> },
  { key: 'other',            label: 'Other reason',        hint: 'Explain in your own words',             icon: <MessageSquare className="w-5 h-5" /> },
]

function JustificationModal({ day, onClose, onDone }: { day: MyBriefDay; onClose: () => void; onDone: (d: string, status: string) => void }) {
  const [reason, setReason] = useState<Reason | null>(null)
  const [justification, setJustification] = useState('')
  const [minutes, setMinutes] = useState('')
  const [location, setLocation] = useState('')
  const [clientName, setClientName] = useState('')
  const [clientReason, setClientReason] = useState('')
  const [clientOutcome, setClientOutcome] = useState('')
  const [amount, setAmount] = useState('')
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const mins = parseInt(minutes, 10)
  const meetingBad = reason === 'external_meeting' && (!Number.isFinite(mins) || mins < 1 || mins > 90)

  function validate(): string | null {
    if (!reason) return 'Please pick a reason.'
    if (reason === 'external_meeting' && meetingBad) return 'Meeting length must be 1–90 minutes (max 1.5h).'
    if (reason === 'client_visit' && !clientName.trim()) return 'Please enter the client name.'
    if ((reason === 'other' || reason === 'external_meeting') && !justification.trim()) return 'Please add a short explanation.'
    return null
  }

  async function submit() {
    const v = validate()
    if (v) { setErr(v); return }
    setSaving(true); setErr(null)
    try {
      const resp = await submitJustification({
        work_date: day.work_date, reason: reason as Reason, justification: justification.trim(),
        meeting_minutes: reason === 'external_meeting' ? mins : undefined,
        location: location.trim() || undefined,
        client_name: reason === 'client_visit' ? clientName.trim() : undefined,
        client_reason: reason === 'client_visit' ? clientReason.trim() : undefined,
        client_outcome: reason === 'client_visit' ? clientOutcome.trim() : undefined,
        amount: reason === 'client_visit' && amount.trim() ? Number(amount) : undefined,
      })
      onDone(day.work_date, resp.status)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not submit — please try again.')
    } finally { setSaving(false) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
         style={{ background: 'rgba(13,27,42,0.55)', backdropFilter: 'blur(2px)' }}>
      <div className="w-full max-w-lg rounded-2xl overflow-hidden shadow-2xl bg-white max-h-[92vh] flex flex-col">
        {/* Header */}
        <div className="px-6 py-4 flex items-start gap-3" style={{ background: NAVY }}>
          <div className="w-9 h-9 rounded-lg flex items-center justify-center shrink-0" style={{ background: 'rgba(244,166,35,0.15)' }}>
            <Clock className="w-5 h-5" style={{ color: ORANGE }} />
          </div>
          <div className="flex-1">
            <div className="text-white font-bold text-lg" style={{ fontFamily: SERIF }}>Explain your hours</div>
            <div className="text-[#AEB6C2] text-xs mt-0.5 flex items-center gap-1">
              <CalendarDays className="w-3.5 h-3.5" /> {day.work_date}
            </div>
          </div>
          <button onClick={onClose} className="text-[#AEB6C2] hover:text-white transition-colors" aria-label="Close"><X className="w-5 h-5" /></button>
        </div>

        {/* Shortfall banner */}
        <div className="px-6 py-3 text-sm" style={{ background: '#FFF7E8', borderBottom: '1px solid #F3E4C4', color: NAVY }}>
          Company policy is <b>{day.required}h</b> on this day. You logged <b>{day.tracked}h</b> —
          <b> {day.shortfall}h</b> short. Please tell us why.
        </div>

        {/* Body */}
        <div className="px-6 py-5 overflow-y-auto">
          <div className="grid grid-cols-2 gap-3">
            {OPTIONS.map(o => {
              const on = reason === o.key
              return (
                <button key={o.key} onClick={() => { setReason(o.key); setErr(null) }}
                  className="text-left rounded-xl border p-3 transition-all"
                  style={{ borderColor: on ? ORANGE : '#E5E7EB', background: on ? '#FFF7E8' : '#fff',
                           boxShadow: on ? `0 0 0 1px ${ORANGE}` : 'none' }}>
                  <div className="flex items-center gap-2" style={{ color: on ? '#B45309' : NAVY }}>
                    {o.icon}<span className="font-semibold text-sm">{o.label}</span>
                  </div>
                  <div className="text-xs text-[#6B7280] mt-1">{o.hint}</div>
                </button>
              )
            })}
          </div>

          {reason === 'external_meeting' && (
            <div className="mt-4 space-y-3">
              <label className="block text-sm font-medium" style={{ color: NAVY }}>Meeting length (minutes)
                <input type="number" min={1} max={90} value={minutes} onChange={e => setMinutes(e.target.value)}
                  className="mt-1 w-full rounded-lg border px-3 py-2 text-sm"
                  style={{ borderColor: meetingBad && minutes ? '#DC2626' : '#E5E7EB' }} placeholder="e.g. 60" />
                <span className="text-xs text-[#6B7280]">Maximum 90 minutes (1.5 hours).</span>
              </label>
              <input value={location} onChange={e => setLocation(e.target.value)} placeholder="Where was it? (optional)"
                className="w-full rounded-lg border px-3 py-2 text-sm" style={{ borderColor: '#E5E7EB' }} />
              <textarea value={justification} onChange={e => setJustification(e.target.value)} rows={2}
                placeholder="What was the meeting about?" className="w-full rounded-lg border px-3 py-2 text-sm" style={{ borderColor: '#E5E7EB' }} />
            </div>
          )}

          {reason === 'client_visit' && (
            <div className="mt-4 space-y-3">
              <input value={clientName} onChange={e => setClientName(e.target.value)} placeholder="Client name *"
                className="w-full rounded-lg border px-3 py-2 text-sm" style={{ borderColor: '#E5E7EB' }} />
              <textarea value={clientReason} onChange={e => setClientReason(e.target.value)} rows={2}
                placeholder="Why did you see the client?" className="w-full rounded-lg border px-3 py-2 text-sm" style={{ borderColor: '#E5E7EB' }} />
              <textarea value={clientOutcome} onChange={e => setClientOutcome(e.target.value)} rows={2}
                placeholder="What happened afterward?" className="w-full rounded-lg border px-3 py-2 text-sm" style={{ borderColor: '#E5E7EB' }} />
              <label className="block text-sm font-medium" style={{ color: NAVY }}>Potential premium (BWP)
                <input type="number" min={0} value={amount} onChange={e => setAmount(e.target.value)}
                  className="mt-1 w-full rounded-lg border px-3 py-2 text-sm" style={{ borderColor: '#E5E7EB' }} placeholder="Optional" />
              </label>
            </div>
          )}

          {(reason === 'on_leave' || reason === 'other') && (
            <textarea value={justification} onChange={e => setJustification(e.target.value)} rows={3}
              placeholder={reason === 'on_leave' ? 'Which leave? (HR will confirm the application)' : 'Explain in your own words'}
              className="mt-4 w-full rounded-lg border px-3 py-2 text-sm" style={{ borderColor: '#E5E7EB' }} />
          )}

          {err && <div className="mt-3 text-sm text-[#B91C1C]">{err}</div>}
        </div>

        {/* Footer */}
        <div className="px-6 py-4 flex items-center justify-end gap-2" style={{ borderTop: '1px solid #EEF0F3' }}>
          <Button variant="ghost" onClick={onClose} disabled={saving}>Cancel</Button>
          <Button variant="accent" onClick={submit} disabled={saving || !reason}>
            <CheckCircle2 className="w-4 h-4 mr-1" /> {saving ? 'Submitting…' : 'Submit reason'}
          </Button>
        </div>
      </div>
    </div>
  )
}

function localISO(d: Date): string {
  // Local calendar date (server is Botswana UTC+2) — NOT toISOString(), which is UTC.
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
function tomorrowISO(): string {
  const d = new Date(); d.setDate(d.getDate() + 1)
  return localISO(d)
}

/** Plan an upcoming out-of-office day in advance (CFO 2026-07-15) — logs the
 * reason before the day so the person isn't flagged as an unexplained no-show. */
function PlanAheadModal({ onClose, onDone }: { onClose: () => void; onDone: (msg: string) => void }) {
  const [workDate, setWorkDate] = useState(tomorrowISO())
  const [reason, setReason] = useState<Reason | null>(null)
  const [note, setNote] = useState('')
  const [clientName, setClientName] = useState('')
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const todayISO = localISO(new Date())

  function validate(): string | null {
    if (!workDate || workDate < todayISO) return 'Pick today or an upcoming day.'
    if (!reason) return 'Please pick a reason.'
    if (reason === 'client_visit' && !clientName.trim()) return 'Please enter the client name.'
    if (reason === 'other' && !note.trim()) return 'Please add a short reason (e.g. “Baker Tilly golf day”).'
    return null
  }
  async function submit() {
    const v = validate(); if (v) { setErr(v); return }
    setSaving(true); setErr(null)
    try {
      const resp = await planAbsence({
        work_date: workDate, reason: reason as Reason, justification: note.trim() || undefined,
        client_name: reason === 'client_visit' ? clientName.trim() : undefined,
      })
      onDone(resp.status === 'justified'
        ? `Thanks — ${workDate} is covered by your approved leave. Your manager will see it.`
        : `Thanks — we've noted you'll be out on ${workDate}. Your manager sees the reason, and you won't be flagged for low hours that day.`)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Could not save — please try again.')
    } finally { setSaving(false) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
         style={{ background: 'rgba(13,27,42,0.55)', backdropFilter: 'blur(2px)' }}>
      <div className="w-full max-w-lg rounded-2xl overflow-hidden shadow-2xl bg-white max-h-[92vh] flex flex-col">
        <div className="px-6 py-4 flex items-start gap-3" style={{ background: NAVY }}>
          <div className="w-9 h-9 rounded-lg flex items-center justify-center shrink-0" style={{ background: 'rgba(244,166,35,0.15)' }}>
            <CalendarPlus className="w-5 h-5" style={{ color: ORANGE }} />
          </div>
          <div className="flex-1">
            <div className="text-white font-bold text-lg" style={{ fontFamily: SERIF }}>Plan an upcoming day</div>
            <div className="text-[#AEB6C2] text-xs mt-0.5">Tell us before you're out — you won't be flagged for it.</div>
          </div>
          <button onClick={onClose} className="text-[#AEB6C2] hover:text-white transition-colors" aria-label="Close"><X className="w-5 h-5" /></button>
        </div>

        <div className="px-6 py-5 overflow-y-auto">
          <label className="block text-sm font-medium mb-4" style={{ color: NAVY }}>Which day?
            <input type="date" value={workDate} min={todayISO} onChange={e => { setWorkDate(e.target.value); setErr(null) }}
              className="mt-1 w-full rounded-lg border px-3 py-2 text-sm" style={{ borderColor: '#E5E7EB' }} />
          </label>

          <div className="text-sm font-medium mb-2" style={{ color: NAVY }}>Why will you be out?</div>
          <div className="grid grid-cols-2 gap-3">
            {OPTIONS.filter(o => o.key !== 'external_meeting').map(o => {
              const on = reason === o.key
              return (
                <button key={o.key} onClick={() => { setReason(o.key); setErr(null) }}
                  className="text-left rounded-xl border p-3 transition-all"
                  style={{ borderColor: on ? ORANGE : '#E5E7EB', background: on ? '#FFF7E8' : '#fff',
                           boxShadow: on ? `0 0 0 1px ${ORANGE}` : 'none' }}>
                  <div className="flex items-center gap-2" style={{ color: on ? '#B45309' : NAVY }}>
                    {o.icon}<span className="font-semibold text-sm">{o.label}</span>
                  </div>
                  <div className="text-xs text-[#6B7280] mt-1">{o.hint}</div>
                </button>
              )
            })}
          </div>

          {reason === 'on_leave' && (
            <div className="mt-4 text-xs rounded-lg px-3 py-2" style={{ background: '#F1F5F9', color: NAVY }}>
              We'll check this against your approved leave. If it isn't approved yet, apply for leave first.
            </div>
          )}
          {reason === 'client_visit' && (
            <input value={clientName} onChange={e => setClientName(e.target.value)} placeholder="Client name *"
              className="mt-4 w-full rounded-lg border px-3 py-2 text-sm" style={{ borderColor: '#E5E7EB' }} />
          )}
          <textarea value={note} onChange={e => setNote(e.target.value)} rows={2}
            placeholder={reason === 'other' ? 'Reason (e.g. “Baker Tilly golf day”)' : 'Anything to add? (optional)'}
            className="mt-4 w-full rounded-lg border px-3 py-2 text-sm" style={{ borderColor: '#E5E7EB' }} />

          {err && <div className="mt-3 text-sm text-[#B91C1C]">{err}</div>}
        </div>

        <div className="px-6 py-4 flex items-center justify-end gap-2" style={{ borderTop: '1px solid #EEF0F3' }}>
          <Button variant="ghost" onClick={onClose} disabled={saving}>Cancel</Button>
          <Button variant="accent" onClick={submit} disabled={saving || !reason}>
            <CheckCircle2 className="w-4 h-4 mr-1" /> {saving ? 'Saving…' : 'Save plan'}
          </Button>
        </div>
      </div>
    </div>
  )
}

export default function MyBriefPage() {
  const [brief, setBrief] = useState<MyBrief | null>(null)
  const [active, setActive] = useState<MyBriefDay | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [planning, setPlanning] = useState(false)

  useEffect(() => {
    getMyBrief().then(b => { setBrief(b); if (b.days.length) setActive(b.days[0]) })
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load'))
  }, [])

  function done(dateStr: string, status: string) {
    setBrief(b => b ? { ...b, days: b.days.filter(d => d.work_date !== dateStr) } : b)
    setActive(null)
    setNotice(status === 'unjustified'
      ? 'Your reason was recorded, but an external meeting covers at most 1.5 hours — the rest of that day still counts as unjustified.'
      : status === 'explained'
      ? 'Thank you — your explanation was sent to your manager for review.'
      : 'Thank you — your reason was recorded.')
  }
  const days = brief?.days ?? []

  return (
    <div className="min-h-screen bg-[#F8F9FB]">
      <TopBar title="My Daily Brief" breadcrumbs={[{ label: 'HRIS', href: '/hris' }, { label: 'My Daily Brief' }]} />
      <div className="max-w-3xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        <div className="flex items-center gap-3 mb-5">
          <div className="w-10 h-10 rounded-lg flex items-center justify-center" style={{ background: NAVY }}>
            <Clock className="w-5 h-5" style={{ color: ORANGE }} />
          </div>
          <div>
            <h1 className="text-2xl font-bold" style={{ color: NAVY, fontFamily: SERIF }}>My Daily Brief</h1>
            <p className="text-sm text-[#6B7280]">Days that need a quick explanation of your hours.</p>
          </div>
        </div>

        {/* Plan-ahead — record an upcoming out-of-office day before it happens */}
        <Card className="mb-4">
          <CardContent className="py-4 flex items-center gap-3">
            <div className="w-10 h-10 rounded-lg flex items-center justify-center shrink-0" style={{ background: '#EEF4FF' }}>
              <CalendarPlus className="w-5 h-5" style={{ color: '#1D4ED8' }} />
            </div>
            <div className="flex-1">
              <div className="font-semibold" style={{ color: NAVY }}>Out of office soon?</div>
              <div className="text-xs text-[#6B7280]">Golf day, client visit, leave or an off-site coming up? Tell us in advance so you're not flagged for low hours.</div>
            </div>
            <Button variant="accent" onClick={() => setPlanning(true)}>Plan a day</Button>
          </CardContent>
        </Card>

        {error && <div className="text-sm text-[#B91C1C]">{error}</div>}
        {notice && (
          <div className="mb-4 text-sm rounded-lg px-4 py-3" style={{ background: '#FFF7E8', border: '1px solid #F3E4C4', color: NAVY }}>
            {notice}
          </div>
        )}

        {!error && days.length === 0 && (
          <Card><CardContent className="py-10 text-center">
            <CheckCircle2 className="w-8 h-8 mx-auto mb-2" style={{ color: '#059669' }} />
            <div className="font-semibold" style={{ color: NAVY }}>All caught up</div>
            <div className="text-sm text-[#6B7280] mt-1">Nothing to explain right now.</div>
          </CardContent></Card>
        )}

        <div className="space-y-2">
          {days.map(d => (
            <Card key={d.work_date}>
              <CardContent className="py-4 flex items-center gap-3">
                <CalendarDays className="w-5 h-5" style={{ color: ORANGE }} />
                <div className="flex-1">
                  <div className="font-semibold" style={{ color: NAVY }}>{d.work_date}</div>
                  <div className="text-xs text-[#6B7280]">{d.tracked}h of {d.required}h · {d.shortfall}h short</div>
                </div>
                <Button variant="accent" onClick={() => setActive(d)}>Explain</Button>
              </CardContent>
            </Card>
          ))}
        </div>
      </div>

      {active && <JustificationModal day={active} onClose={() => setActive(null)} onDone={done} />}
      {planning && <PlanAheadModal onClose={() => setPlanning(false)} onDone={(msg) => { setPlanning(false); setNotice(msg) }} />}
    </div>
  )
}
