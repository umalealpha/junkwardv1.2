'use client'

/** /m/staff/leave — apply for leave, see your requests, approve your team's.
 * Drives the SAME HRIS endpoints as the desktop /hris/leave page. */
import { useCallback, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { ArrowLeft } from 'lucide-react'
import { hfetch } from '@/app/(customer)/api'
import { C, serif, sans, h, card, headerPad } from '@/app/(customer)/ui'
import { useStaffBase } from './useStaffBase'

interface Balance { code: string; name: string; remaining: number; available?: number }
interface MyReq { id: string; leave_type: string; start_date: string; end_date: string; days: number; day_breakdown?: string; status: string; status_label: string; decision_notes?: string; can_cancel?: boolean }

const inputStyle: React.CSSProperties = { width: '100%', boxSizing: 'border-box', padding: '13px 14px', border: 'none', background: '#F0F2F5', borderRadius: 12, fontSize: 15, color: C.ink, outline: 'none', fontFamily: sans }
const STATUS_COLOR: Record<string, string> = { approved: '#047857', rejected: '#B91C1C', refused: '#B91C1C', pending: '#B45309' }

// Fixed reason categories (Unami Butale, HR, 2026-07-27) — the employee picks
// one instead of being forced to justify the leave. Mirrors the desktop
// /hris/leave picker and LeaveRequest.ReasonCategory exactly. The phone kept
// the old 15-word mandate nine months after HR reversed it (CFO 2026-08-03).
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

// Company-discretion leave (CFO 2026-09-10) — mirrors
// hris/discretionary_leave.DISCRETIONARY_TYPES. These carry a compulsory
// question set and a 50-word motivation, which is a website job, not a phone
// one; the phone says so plainly rather than failing at the server.
const DISCRETIONARY = ['compassionate', 'study', 'special']

export default function StaffLeave() {
  const base = useStaffBase()
  // Approve tab removed 2026-07-22 — leave approvals now live on the unified
  // /m/staff/approvals dashboard (one place for every sign-off). This page is
  // apply + my-requests only.
  const [tab, setTab] = useState<'apply' | 'mine'>('apply')
  const [balances, setBalances] = useState<Balance[]>([])
  const [mine, setMine] = useState<MyReq[]>([])
  const [toast, setToast] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const [type, setType] = useState('annual')
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [halfDay, setHalfDay] = useState(false)
  const [startDT, setStartDT] = useState<'am' | 'pm'>('am')
  const [reason, setReason] = useState('')
  // Fixed category replaces the forced free-text why (Unami 2026-07-27).
  const [reasonCategory, setReasonCategory] = useState('')
  const certRef = useRef<HTMLInputElement | null>(null)

  const show = (m: string) => { setToast(m); setTimeout(() => setToast(null), 3500) }

  const load = useCallback(() => {
    hfetch<{ balances: Balance[] }>('/leave-balances/').then(d => setBalances(d.balances || [])).catch(() => {})
    hfetch<{ requests?: MyReq[] } | MyReq[]>('/leave-requests/mine/')
      .then(d => setMine(Array.isArray(d) ? d : (d.requests || []))).catch(() => {})
  }, [])
  useEffect(() => { load() }, [load])

  async function apply() {
    // Discretionary leave (CFO 2026-09-10) needs a compulsory question set, a
    // 50-word motivation and a signed undertaking. That does not belong on a
    // phone form, and letting them submit here would only bounce off the
    // server with a wall of errors — so send them to the full site instead.
    if (DISCRETIONARY.includes(type)) {
      show('This leave needs a written application on the Omni website. Open omni.alphadirect.co.bw and go to HR › Leave.')
      return
    }
    if (!start || !end) { show('Pick the start and end dates.'); return }
    // No forced justification (Unami 2026-07-27) — one tap on a category, and
    // "Prefer not to say" is a valid answer. The note underneath is optional.
    if (!reasonCategory) { show('Pick a reason category (you do not have to explain further).'); return }
    setBusy(true)
    try {
      const fd = new FormData()
      fd.append('type', type); fd.append('start_date', start); fd.append('end_date', end)
      if (halfDay) { fd.append('start_day_type', startDT); fd.append('end_day_type', 'full') }
      fd.append('reason_category', reasonCategory)
      if (reason) fd.append('reason', reason)
      if (certRef.current?.files?.[0]) fd.append('certificate', certRef.current.files[0])
      await hfetch('/leave-requests/', { method: 'POST', body: fd }, false)
      show('Leave request sent to your approver. ✅')
      setStart(''); setEnd(''); setReason(''); setReasonCategory(''); setHalfDay(false)
      if (certRef.current) certRef.current.value = ''
      load(); setTab('mine')
    } catch (e) { show(e instanceof Error ? e.message : 'Could not submit.') }
    finally { setBusy(false) }
  }

  async function cancelReq(id: string) {
    if (!window.confirm('Cancel this leave request? This cannot be undone.')) return
    setBusy(true)
    try {
      await hfetch(`/leave-requests/${id}/cancel/`, { method: 'POST', body: JSON.stringify({}) })
      show('Leave request cancelled.')
      load()
    } catch (e) { show(e instanceof Error ? e.message : 'Could not cancel.') }
    finally { setBusy(false) }
  }

  const annual = balances.find(b => b.code === 'annual')

  return (
    <div style={{ background: C.surface, minHeight: '100vh', fontFamily: sans }}>
      <header style={{ display: 'flex', alignItems: 'center', gap: 12, padding: headerPad, background: C.navy, color: '#fff' }}>
        <Link href={base} aria-label="Back" style={{ color: '#fff', display: 'grid', placeItems: 'center', minWidth: 44, minHeight: 44, margin: '-12px 0 -12px -12px' }}><ArrowLeft size={20} /></Link>
        <h1 style={{ fontFamily: serif, fontWeight: 800, fontSize: 20, margin: 0 }}>Leave</h1>
      </header>

      {/* tabs */}
      <div style={{ display: 'flex', gap: 8, padding: '14px 16px 0' }}>
        {([['apply', 'Apply'], ['mine', 'My requests']] as const).map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)}
            style={{ flex: 1, padding: '10px 4px', borderRadius: 999, border: 'none', fontWeight: 700, fontSize: 13, cursor: 'pointer', fontFamily: sans,
              background: tab === k ? C.navy : '#fff', color: tab === k ? '#fff' : C.inkSoft, boxShadow: tab === k ? 'none' : `inset 0 0 0 1px ${C.line}` }}>
            {label}
          </button>
        ))}
      </div>

      <main style={{ padding: 16 }}>
        {tab === 'apply' && (
          <div style={{ ...card, padding: 18 }}>
            {annual && (
              <p style={{ margin: '0 0 14px', fontSize: 13, color: C.inkSoft }}>
                Annual leave available now: <b style={{ color: C.ink }}>{annual.available ?? annual.remaining} days</b>
              </p>)}
            {/* Every control carries its visible caption (label htmlFor / aria-label) —
                axe flagged the two dates and three selects as unlabelled (2026-09-03). */}
            <label htmlFor="leave-type" style={{ fontSize: 12, fontWeight: 700, color: C.inkSoft }}>Leave type</label>
            <select id="leave-type" value={type} onChange={e => setType(e.target.value)} style={{ ...inputStyle, margin: '6px 0 12px' }}>
              {['annual', 'sick', 'maternity', 'paternity', 'compassionate', 'study', 'special'].map(t =>
                <option key={t} value={t}>{t[0].toUpperCase() + t.slice(1)}</option>)}
            </select>
            {DISCRETIONARY.includes(type) && (
              <div style={{ background: '#FEF2F2', border: '2px solid #DC2626', borderRadius: 12, padding: 12, margin: '0 0 12px' }}>
                <p style={{ margin: 0, fontSize: 13, fontWeight: 800, color: '#DC2626' }}>
                  This is not normal leave
                </p>
                <p style={{ margin: '4px 0 0', fontSize: 12.5, color: C.ink, lineHeight: 1.5 }}>
                  {type[0].toUpperCase() + type.slice(1)} leave is granted at the company's
                  discretion. You must answer a set of questions, write at least 50 words,
                  and the CFO signs it off after your manager. Apply on the Omni website
                  (HR › Leave) — this phone form cannot take it. Do not stay away from work
                  until you are told it is approved.
                </p>
              </div>
            )}
            <div style={{ display: 'flex', gap: 10 }}>
              <div style={{ flex: 1 }}>
                <label htmlFor="leave-start" style={{ fontSize: 12, fontWeight: 700, color: C.inkSoft }}>First day</label>
                <input id="leave-start" type="date" value={start} onChange={e => setStart(e.target.value)} style={{ ...inputStyle, marginTop: 6 }} />
              </div>
              <div style={{ flex: 1 }}>
                <label htmlFor="leave-end" style={{ fontSize: 12, fontWeight: 700, color: C.inkSoft }}>Last day</label>
                <input id="leave-end" type="date" value={end} onChange={e => setEnd(e.target.value)} style={{ ...inputStyle, marginTop: 6 }} />
              </div>
            </div>
            <label style={{ display: 'flex', alignItems: 'center', gap: 10, margin: '8px 0', minHeight: 44, fontSize: 13, color: C.ink, cursor: 'pointer' }}>
              <input type="checkbox" checked={halfDay} onChange={e => setHalfDay(e.target.checked)} style={{ width: 24, height: 24, accentColor: C.orange, flexShrink: 0, margin: 0 }} /> Half day
            </label>
            {halfDay && (
              <select value={startDT} onChange={e => setStartDT(e.target.value as 'am' | 'pm')} aria-label="Half day — morning or afternoon" style={{ ...inputStyle, marginBottom: 12 }}>
                <option value="am">Morning (AM)</option>
                <option value="pm">Afternoon (PM)</option>
              </select>)}
            <label htmlFor="leave-reason" style={{ fontSize: 12, fontWeight: 700, color: C.inkSoft }}>Reason</label>
            <select id="leave-reason" value={reasonCategory} onChange={e => setReasonCategory(e.target.value)}
              style={{ ...inputStyle, margin: '6px 0 4px' }}>
              <option value="">Choose one…</option>
              {REASON_CATEGORIES.map(r => <option key={r.v} value={r.v}>{r.label}</option>)}
            </select>
            <p style={{ fontSize: 11.5, margin: '0 0 12px', color: C.inkSoft }}>
              You do not have to explain why you are taking leave.
            </p>
            <label htmlFor="leave-notes" style={{ fontSize: 12, fontWeight: 700, color: C.inkSoft }}>Anything to add (optional)</label>
            <textarea id="leave-notes" value={reason} onChange={e => setReason(e.target.value)} rows={2}
              placeholder="Optional — leave blank if you prefer"
              style={{ ...inputStyle, margin: '6px 0 12px', resize: 'vertical' }} />
            {type === 'sick' && (
              <label style={{ display: 'block', fontSize: 13, color: C.ink, margin: '0 0 12px' }}>
                <span style={{ fontWeight: 700, fontSize: 12, color: C.inkSoft }}>Sick note (required)</span><br />
                <input ref={certRef} type="file" accept=".pdf,image/*" aria-label="Sick note (required)" style={{ marginTop: 6, fontSize: 13, minHeight: 44 }} />
              </label>)}
            <button onClick={apply} disabled={busy}
              style={{ width: '100%', padding: 14, borderRadius: 999, border: 'none', background: `linear-gradient(135deg, ${C.orange}, ${C.orangeDeep})`, color: C.head, fontWeight: 800, fontSize: 15, cursor: 'pointer', fontFamily: sans, opacity: busy ? 0.6 : 1 }}>
              {busy ? 'Sending…' : 'Submit leave request'}
            </button>
          </div>
        )}

        {tab === 'mine' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {mine.length === 0 && <p style={{ color: C.inkSoft, fontSize: 14, textAlign: 'center', marginTop: 24 }}>No leave requests yet.</p>}
            {mine.map(r => (
              <div key={r.id} style={{ ...card, padding: 16 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <b style={{ color: C.ink, fontSize: 15 }}>{r.leave_type}</b>
                  <span style={{ fontSize: 12, fontWeight: 800, color: STATUS_COLOR[r.status] || C.inkSoft }}>{r.status_label || r.status}</span>
                </div>
                <p style={{ color: C.inkSoft, fontSize: 13, margin: '6px 0 0' }}>{r.day_breakdown || `${r.days} day(s) · ${r.start_date} → ${r.end_date}`}</p>
                {r.decision_notes && <p style={{ color: '#B91C1C', fontSize: 12.5, margin: '6px 0 0' }}>↩ {r.decision_notes}</p>}
                {r.can_cancel && (
                  <button onClick={() => cancelReq(r.id)} disabled={busy}
                    style={{ marginTop: 10, minHeight: 44, padding: '8px 14px', borderRadius: 999, border: '1px solid #e5e7eb', background: C.card, color: C.inkSoft, fontWeight: 700, fontSize: 13, cursor: 'pointer', opacity: busy ? 0.6 : 1 }}>
                    Cancel request
                  </button>
                )}
              </div>))}
          </div>
        )}

      </main>

      {toast && (
        <div style={{ position: 'fixed', bottom: 32, left: '50%', transform: 'translateX(-50%)', background: C.navy, color: '#fff', padding: '11px 20px', borderRadius: 999, fontSize: 13, fontWeight: 600, zIndex: 40, maxWidth: '88%', textAlign: 'center' }}>
          {toast}
        </div>)}
    </div>
  )
}
