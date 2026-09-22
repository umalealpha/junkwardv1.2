'use client'

/** /app/pulse · /m/staff/pulse — the weekly mood check-in (CFO pick 2026-09-03).
 * Same endpoints as the desktop pulse widget (hris/pulse_views.py):
 *   GET  /api/v1/hris/pulse/me/      → { submitted_this_week, score }
 *   POST /api/v1/hris/pulse/submit/  { score: 1-5, comment? }   (one per ISO week; upsert)
 * Anonymous by design — there is NO team/aggregate view on the phone; the
 * dashboard is HR-only on the desktop. */
import { useCallback, useEffect, useState } from 'react'
import { reauthOn401, sfetch } from '@/app/(customer)/api'
import { C } from '@/app/(customer)/ui'
import { useStaffBase } from './useStaffBase'
import {
  Card, RetryBanner, ScreenFrame, ServerMessage, Toast, errText, formatServerErrors,
  ghostBtn, inputStyle, labelStyle, primaryBtn, rawStaffFetch,
} from './StaffFormKit'

interface Me { submitted_this_week: boolean; score: number | null }
const SCORES: { v: number; emoji: string; label: string }[] = [
  { v: 1, emoji: '😞', label: 'Rough' },
  { v: 2, emoji: '😕', label: 'Meh' },
  { v: 3, emoji: '😐', label: 'Okay' },
  { v: 4, emoji: '🙂', label: 'Good' },
  { v: 5, emoji: '😄', label: 'Great' },
]
const MAX_COMMENT = 1000

export default function PulseScreen() {
  const base = useStaffBase()
  const [me, setMe] = useState<Me | null>(null)
  const [loadErr, setLoadErr] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const [score, setScore] = useState<number | null>(null)
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)
  const [serverErr, setServerErr] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  const show = (m: string) => { setToast(m); setTimeout(() => setToast(null), 4000) }

  const load = useCallback(() => {
    setLoadErr(null)
    sfetch<Me>('/hris/pulse/me/').then(d => { setMe(d); setScore(d.score) })
      .catch(e => { if (!reauthOn401(e)) setLoadErr(errText(e, 'Could not load this week’s check-in.')) })
  }, [])
  useEffect(() => { load() }, [load])

  async function submit() {
    setServerErr(null)
    if (score == null) { show('Tap how your week is going.'); return }
    setBusy(true)
    try {
      // Server reads request.data.get('score') and request.data.get('comment').
      const r = await rawStaffFetch('/hris/pulse/submit/', { method: 'POST', body: JSON.stringify({ score, comment: comment.trim() }) })
      if (!r.ok) { setServerErr(formatServerErrors(r.body, r.status)); return }
      show('Thanks — noted for this week. ✅'); setEditing(false); setComment(''); load()
    } catch (e) { if (!reauthOn401(e)) setServerErr(errText(e, 'Could not send your check-in.')) }
    finally { setBusy(false) }
  }

  const done = !!me?.submitted_this_week && !editing
  const chosen = SCORES.find(s => s.v === me?.score)

  return (
    <ScreenFrame title="Pulse" base={base}>
      {loadErr && <RetryBanner message={loadErr} onRetry={load} />}
      <Card>
        {me === null && !loadErr && <p style={{ color: C.inkSoft, fontSize: 13, margin: 0 }}>Loading…</p>}

        {me && done && (
          <div style={{ textAlign: 'center', padding: '10px 0' }}>
            <div aria-hidden="true" style={{ fontSize: 56, lineHeight: 1 }}>{chosen?.emoji ?? '✅'}</div>
            <p style={{ margin: '12px 0 4px', fontWeight: 800, color: C.ink, fontSize: 17 }}>Thanks — see you next week.</p>
            <p style={{ margin: 0, color: C.inkSoft, fontSize: 13 }}>
              You said this week is <b style={{ color: C.ink }}>{chosen?.label ?? me.score}</b>. Your answer is anonymous — HR only ever sees team averages.
            </p>
            <button onClick={() => setEditing(true)} style={{ ...ghostBtn, marginTop: 14 }}>Change my answer</button>
          </div>
        )}

        {me && !done && (
          <>
            <p style={{ margin: 0, fontWeight: 800, color: C.ink, fontSize: 17 }}>How is your week going?</p>
            <p style={{ margin: '4px 0 14px', color: C.inkSoft, fontSize: 13, lineHeight: 1.5 }}>One tap, once a week. It is anonymous — HR only ever sees team averages, never who said what.</p>
            <div role="radiogroup" aria-label="How is your week going" style={{ display: 'flex', gap: 6 }}>
              {SCORES.map(s => {
                const on = score === s.v
                return (
                  <button key={s.v} role="radio" aria-checked={on} aria-label={`${s.label} — ${s.v} of 5`} onClick={() => setScore(s.v)}
                    style={{ flex: 1, minHeight: 72, borderRadius: 16, cursor: 'pointer', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 4,
                      border: on ? `2px solid ${C.navy}` : `1px solid ${C.line}`, background: on ? '#FFF7ED' : '#fff', padding: '8px 2px' }}>
                    <span aria-hidden="true" style={{ fontSize: 28, lineHeight: 1 }}>{s.emoji}</span>
                    <span style={{ fontSize: 11, fontWeight: 700, color: C.ink }}>{s.label}</span>
                  </button>
                )
              })}
            </div>
            <label htmlFor="pulse-note" style={labelStyle}>Anything to add (optional)</label>
            <textarea id="pulse-note" value={comment} onChange={e => setComment(e.target.value.slice(0, MAX_COMMENT))} rows={3}
              placeholder="Optional — what made it that way?" style={{ ...inputStyle, resize: 'vertical' }} />
            {serverErr && <div style={{ marginTop: 12 }}><ServerMessage text={serverErr} tone="error" /></div>}
            <button onClick={submit} disabled={busy} style={{ ...primaryBtn(busy), marginTop: 14, color: '#0D1B2A' }}>
              {busy ? 'Sending…' : 'Send my check-in'}
            </button>
            {editing && <button onClick={() => setEditing(false)} disabled={busy} style={{ width: '100%', marginTop: 8, minHeight: 44, borderRadius: 999, border: 'none', background: 'none', color: C.inkSoft, fontWeight: 700, fontSize: 14, cursor: 'pointer' }}>Keep my earlier answer</button>}
          </>
        )}
      </Card>
      <Toast text={toast} />
    </ScreenFrame>
  )
}
