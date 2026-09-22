'use client'

/** /m/staff/checkins — your monthly performance check-ins: read what your
 * manager recorded, respond, and SIGN it from your phone. */
import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { ArrowLeft, PenLine } from 'lucide-react'
import { hfetch } from '@/app/(customer)/api'
import { C, serif, sans, card, headerPad } from '@/app/(customer)/ui'
import { useStaffBase } from './useStaffBase'

interface CheckIn {
  id: string; period_month: number; period_year: number; overall_rating: string
  strengths: string; concerns: string; manager_comments: string
  employee_response: string; employee_decision: string; manager_signed_at: string | null
  employee_signed_at: string | null; is_locked: boolean
  // Posted by Omni because the manager never responded, plus the state of the
  // employee's right of reply (CFO 2026-08-26).
  auto_posted: boolean; employee_requested_comments: boolean
  manager_followup: string
}
const MONTHS = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
// Backend PerformanceCheckRating codes → readable labels + colour.
const RATING_LABEL: Record<string, string> = { EX: 'Exceeds expectations', ME: 'Meets expectations', PA: 'Partially meets', BE: 'Below expectations', SB: 'Significantly below',
  // Omni posted this month because the manager did not respond. It carries no
  // rating on purpose — without a label here the screen showed a bare 'NR'.
  NR: 'Not rated — your manager did not respond' }
const RATING_COLOR: Record<string, string> = { EX: '#047857', ME: '#047857', PA: '#B45309', BE: '#B45309', SB: '#B91C1C' }

export default function StaffCheckins() {
  const base = useStaffBase()
  const [rows, setRows] = useState<CheckIn[] | null>(null)
  const [err, setErr] = useState('')
  const [signFor, setSignFor] = useState<CheckIn | null>(null)
  const [response, setResponse] = useState('')
  const [decision, setDecision] = useState('')   // accept | partial | decline
  const [askFor, setAskFor] = useState<CheckIn | null>(null)
  const [askReason, setAskReason] = useState('')
  const wc = (s: string) => s.trim().split(/\s+/).filter(Boolean).length
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState<string | null>(null)
  const show = (m: string) => { setToast(m); setTimeout(() => setToast(null), 4000) }

  const load = useCallback(() => {
    // ?mine=1 → the caller's OWN check-ins, even if they are also a manager/HR.
    hfetch<{ checkins?: CheckIn[]; results?: CheckIn[] } | CheckIn[]>('/performance/checkins/?mine=1')
      .then(d => setRows(Array.isArray(d) ? d : (d.checkins || d.results || [])))
      .catch(e => { setRows([]); setErr(e instanceof Error ? e.message : 'Could not load.') })
  }, [])
  useEffect(() => { load() }, [load])

  // Partial / decline must be explained to the manager in >= 50 words.
  const needsReason = decision === 'partial' || decision === 'decline'
  const canSubmit = !!decision && (!needsReason || wc(response) >= 50)

  async function sign() {
    if (!signFor || !canSubmit) return
    setBusy(true)
    try {
      await hfetch(`/performance/checkins/${signFor.id}/?mine=1`, {
        method: 'PATCH',
        body: JSON.stringify({
          sign: true,   // server stamps the authoritative signature time
          employee_decision: decision,
          ...(response.trim() ? { employee_response: response.trim() } : {}),
        }),
      })
      show('Response sent. ✍️'); setSignFor(null); setResponse(''); setDecision(''); load()
    } catch (e) { show(e instanceof Error ? e.message : 'Could not send — try on desktop.') }
    finally { setBusy(false) }
  }

  // CFO 2026-08-26: "the employee doesn't accept the feedback and rejects and
  // requests the manager to put additional comments so it's going to be fair."
  async function askForComments() {
    if (!askFor || askReason.trim().length < 30) return
    setBusy(true)
    try {
      await hfetch(`/performance/checkins/${askFor.id}/?mine=1`, {
        method: 'PATCH',
        body: JSON.stringify({
          request_manager_comments: true,
          employee_response: askReason.trim(),
        }),
      })
      show('Sent to your manager. They have to answer you.')
      setAskFor(null); setAskReason(''); load()
    } catch (e) { show(e instanceof Error ? e.message : 'Could not send - try again.') }
    finally { setBusy(false) }
  }

  return (
    <div style={{ background: C.surface, minHeight: '100vh', fontFamily: sans }}>
      <header style={{ display: 'flex', alignItems: 'center', gap: 12, padding: headerPad, background: C.navy, color: '#fff' }}>
        <Link href={base} aria-label="Back" style={{ color: '#fff', display: 'grid', placeItems: 'center', minWidth: 44, minHeight: 44, margin: '-12px 0 -12px -12px' }}><ArrowLeft size={20} /></Link>
        <h1 style={{ fontFamily: serif, fontWeight: 800, fontSize: 20, margin: 0 }}>My check-ins</h1>
      </header>

      <main style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
        {rows === null && <p style={{ color: C.inkSoft, fontSize: 14, textAlign: 'center', marginTop: 24 }}>Loading…</p>}
        {rows !== null && rows.length === 0 && (
          <p style={{ color: C.inkSoft, fontSize: 14, textAlign: 'center', marginTop: 24 }}>{err || 'No check-ins recorded yet.'}</p>)}
        {(rows || []).map(r => (
          <div key={r.id} style={{ ...card, padding: 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <b style={{ color: C.ink, fontSize: 15 }}>{MONTHS[r.period_month] || r.period_month} {r.period_year}</b>
              <span style={{ fontSize: 12, fontWeight: 800, color: RATING_COLOR[r.overall_rating] || C.inkSoft }}>{RATING_LABEL[r.overall_rating] || r.overall_rating || '—'}</span>
            </div>
            {r.strengths && <p style={{ color: C.inkSoft, fontSize: 12.5, margin: '6px 0 0' }}>💪 {r.strengths}</p>}
            {r.concerns && <p style={{ color: '#B45309', fontSize: 12.5, margin: '4px 0 0' }}>⚠ {r.concerns}</p>}
            {r.manager_comments && <p style={{ color: C.inkSoft, fontSize: 12.5, margin: '4px 0 0' }}>🗒 {r.manager_comments}</p>}
            {r.auto_posted && (
              <p style={{ background: '#FDF3E3', color: '#8A5A00', fontSize: 12, borderRadius: 8, padding: '8px 10px', margin: '8px 0 0' }}>
                Recorded by Omni &mdash; your manager did not reply, so there is no rating.
                If you do not agree, ask them to comment.
              </p>)}
            {r.employee_requested_comments && (
              <p role="status" style={{ background: '#EEF2F6', color: '#475569', fontSize: 12, borderRadius: 8, padding: '8px 10px', margin: '8px 0 0' }}>
                Waiting for your manager to comment. It stays on their list until they do.
              </p>)}
            {r.manager_followup && (
              <p style={{ background: '#E8F5EE', color: '#166534', fontSize: 12.5, borderRadius: 8, padding: '8px 10px', margin: '8px 0 0' }}>
                Your manager replied: {r.manager_followup}
              </p>)}
            <div style={{ marginTop: 10, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span style={{ fontSize: 11.5, color: C.inkSoft }}>
                Manager {r.manager_signed_at ? 'signed ✓' : 'not signed'} · You {r.employee_signed_at ? 'signed ✓' : 'not signed'}
              </span>
              <span style={{ display: 'flex', gap: 8 }}>
              {!r.is_locked && !r.employee_requested_comments && (
                <button onClick={() => { setAskFor(r); setAskReason(r.employee_response || '') }}
                  style={{ padding: '8px 14px', borderRadius: 999, border: `1.5px solid ${C.navy}`, background: C.card, color: C.head, fontWeight: 800, fontSize: 12.5, cursor: 'pointer', fontFamily: sans }}>
                  I do not agree
                </button>)}
              {!r.employee_signed_at && !r.is_locked && (
                <button onClick={() => { setSignFor(r); setResponse(r.employee_response || ''); setDecision(r.employee_decision || '') }}
                  style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '8px 16px', borderRadius: 999, border: 'none', background: `linear-gradient(135deg, ${C.orange}, ${C.orangeDeep})`, color: C.head, fontWeight: 800, fontSize: 12.5, cursor: 'pointer', fontFamily: sans }}>
                  <PenLine size={14} /> Sign
                </button>)}
              </span>
            </div>
          </div>))}
      </main>

      {askFor && (
        <div role="dialog" aria-modal="true" onClick={() => !busy && setAskFor(null)}
          style={{ position: 'fixed', inset: 0, background: 'rgba(15,28,44,0.55)', display: 'flex', alignItems: 'flex-end', zIndex: 60 }}>
          <div onClick={e => e.stopPropagation()} style={{ background: C.card, width: '100%', borderRadius: '22px 22px 0 0', padding: '20px 18px 30px' }}>
            <b style={{ fontFamily: serif, fontSize: 18, color: C.ink }}>Ask your manager to comment</b>
            <p style={{ color: C.inkSoft, fontSize: 12.5, margin: '6px 0 10px' }}>
              Say what you disagree with. Your manager has to answer you &mdash; it stays on
              their list until they do. You do not have to accept this feedback.
            </p>
            <textarea value={askReason} onChange={e => setAskReason(e.target.value)} rows={5} aria-label="What is wrong, in your own words"
              placeholder="What is wrong, in your own words&hellip;"
              style={{ width: '100%', boxSizing: 'border-box', padding: '13px 14px', border: askReason.trim().length < 30 ? '1.5px solid #DC2626' : 'none', background: '#F0F2F5', borderRadius: 12, fontSize: 15, color: C.ink, outline: 'none', fontFamily: sans, resize: 'vertical' }} />
            <div style={{ fontSize: 12, marginTop: 4, color: askReason.trim().length >= 30 ? '#047857' : '#B91C1C' }}>
              {askReason.trim().length}/30 characters{askReason.trim().length >= 30 ? '' : ' - say a bit more so your manager can answer it'}
            </div>
            <button onClick={askForComments} disabled={busy || askReason.trim().length < 30}
              style={{ marginTop: 12, width: '100%', padding: 14, borderRadius: 999, border: 'none', background: C.navy, color: '#fff', fontWeight: 800, fontSize: 15, cursor: askReason.trim().length >= 30 ? 'pointer' : 'not-allowed', fontFamily: sans, opacity: (busy || askReason.trim().length < 30) ? 0.6 : 1 }}>
              {busy ? 'Sending…' : 'Send to my manager'}
            </button>
          </div>
        </div>)}

      {signFor && (
        <div role="dialog" aria-modal="true" onClick={() => !busy && setSignFor(null)}
          style={{ position: 'fixed', inset: 0, background: 'rgba(15,28,44,0.55)', display: 'flex', alignItems: 'flex-end', zIndex: 60 }}>
          <div onClick={e => e.stopPropagation()} style={{ background: C.card, width: '100%', borderRadius: '22px 22px 0 0', padding: '20px 18px 30px' }}>
            <b style={{ fontFamily: serif, fontSize: 18, color: C.ink }}>Respond to {MONTHS[signFor.period_month]} {signFor.period_year}</b>
            <p style={{ color: C.inkSoft, fontSize: 12.5, margin: '6px 0 10px' }}>Tell your manager whether you accept this feedback. This also signs that you have read it.</p>
            {([['accept', 'Accept', '#047857'], ['partial', 'Partially accept', '#B45309'], ['decline', 'Do not accept', '#B91C1C']] as const).map(([val, lbl, col]) => (
              <button key={val} onClick={() => setDecision(val)}
                style={{ width: '100%', textAlign: 'left', marginBottom: 8, padding: '11px 14px', borderRadius: 12, cursor: 'pointer', fontFamily: sans, fontSize: 14, fontWeight: 700,
                  border: `2px solid ${decision === val ? col : '#E5E7EB'}`, background: decision === val ? `${col}18` : '#fff', color: decision === val ? col : C.ink }}>
                {decision === val ? '● ' : '○ '}{lbl}
              </button>))}
            <textarea value={response} onChange={e => setResponse(e.target.value)} rows={4} aria-label="Your response to your manager"
              placeholder={needsReason ? 'Explain to your manager why (at least 50 words)…' : 'Your comment (optional)'}
              style={{ width: '100%', boxSizing: 'border-box', padding: '13px 14px', border: needsReason && wc(response) < 50 ? '1.5px solid #DC2626' : 'none', background: '#F0F2F5', borderRadius: 12, fontSize: 15, color: C.ink, outline: 'none', fontFamily: sans, resize: 'vertical', marginTop: 4 }} />
            {needsReason && (
              <div style={{ fontSize: 12, marginTop: 4, color: wc(response) >= 50 ? '#047857' : '#B91C1C' }}>
                {wc(response)}/50 words{wc(response) >= 50 ? ' ✓' : ' — a reason is required to partially accept or decline'}
              </div>)}
            <button onClick={sign} disabled={busy || !canSubmit}
              style={{ marginTop: 12, width: '100%', padding: 14, borderRadius: 999, border: 'none', background: `linear-gradient(135deg, ${C.orange}, ${C.orangeDeep})`, color: C.head, fontWeight: 800, fontSize: 15, cursor: canSubmit ? 'pointer' : 'not-allowed', fontFamily: sans, opacity: (busy || !canSubmit) ? 0.6 : 1 }}>
              {busy ? 'Sending…' : 'Send response & sign'}
            </button>
          </div>
        </div>)}

      {toast && (
        <div style={{ position: 'fixed', bottom: 32, left: '50%', transform: 'translateX(-50%)', background: C.navy, color: '#fff', padding: '11px 20px', borderRadius: 999, fontSize: 13, fontWeight: 600, zIndex: 70, maxWidth: '88%', textAlign: 'center' }}>
          {toast}
        </div>)}
    </div>
  )
}
