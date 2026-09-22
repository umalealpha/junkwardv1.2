'use client'

/** /app/review-explanations · /m/staff/review-explanations — managers / HR
 * approve or reject staff explanations for short Time Doctor days. Same two
 * endpoints as the desktop review (justifications/pending + review); the
 * server decides who may see this (_can_view) and blocks reviewing your own
 * day — this screen shows its words verbatim. */
import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { ArrowLeft, CheckCircle2, XCircle } from 'lucide-react'
import { sfetch, ApiError, reauthOn401 } from '@/app/(customer)/api'
import { C, serif, sans, card, headerPad, safeBottom } from '@/app/(customer)/ui'
import { useStaffBase } from './useStaffBase'
import { inputStyle, labelStyle, ghostBtn, RetryBanner, ServerMessage, Toast, errText } from './StaffFormKit'

interface Item {
  id: string; employee: string; work_date: string; required: string; tracked: string; justified_hours: string
  reason: string; justification: string; location: string; meeting_minutes: number | null; responded_at: string | null
}
interface Pending { items: Item[]; aria_summary: string }
interface ReviewBody { id: string; decision: 'approve' | 'reject'; note?: string }

const REASON_LABEL: Record<string, string> = { on_leave: 'On leave', external_meeting: 'External meeting', client_visit: 'Client visit', other: 'Other' }
const niceDate = (iso: string) => {
  const d = new Date(`${iso}T00:00:00`)
  return isNaN(d.getTime()) ? iso : d.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' })
}
const hrs = (s: string) => { const n = Number(s); return isFinite(n) ? `${n.toFixed(2).replace(/\.?0+$/, '')}h` : `${s}h` }
const srOnly: React.CSSProperties = { position: 'absolute', width: 1, height: 1, padding: 0, margin: -1, overflow: 'hidden', clip: 'rect(0,0,0,0)', whiteSpace: 'nowrap', border: 0 }

export default function ReviewExplanationsScreen() {
  const base = useStaffBase()
  const [items, setItems] = useState<Item[] | null>(null)
  const [aria, setAria] = useState('')
  const [forbidden, setForbidden] = useState(false)
  const [loadErr, setLoadErr] = useState<string | null>(null)
  const [active, setActive] = useState<Item | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  // Filters (CFO 4-Sep-2026: "I should be able to filter by date or staff or reason").
  // Client-side over the loaded queue — the list is a few hundred rows at most.
  const [fStaff, setFStaff] = useState('')
  const [fReason, setFReason] = useState('')
  const [fFrom, setFFrom] = useState('')
  const [fTo, setFTo] = useState('')
  const filtering = !!(fStaff || fReason || fFrom || fTo)
  const staffOptions = useMemo(() => [...new Set((items || []).map(i => i.employee || 'Unknown'))].sort(), [items])
  const reasonOptions = useMemo(() => [...new Set((items || []).map(i => i.reason))].sort(), [items])
  const filtered = useMemo(() => (items || []).filter(i =>
    (!fStaff || (i.employee || 'Unknown') === fStaff) && (!fReason || i.reason === fReason)
    && (!fFrom || i.work_date >= fFrom) && (!fTo || i.work_date <= fTo)), [items, fStaff, fReason, fFrom, fTo])
  const show = (m: string) => { setToast(m); setTimeout(() => setToast(null), 4000) }

  const load = useCallback(() => {
    setLoadErr(null)
    sfetch<Pending>('/timedoctor/justifications/pending/?no_ai=1')
      .then(d => { setItems(d.items || []); setAria(d.aria_summary || '') })
      .catch((e: unknown) => {
        if (reauthOn401(e)) return
        if (e instanceof ApiError && e.status === 403) { setForbidden(true); return }
        setLoadErr(errText(e, 'Could not load the queue.'))
      })
  }, [])
  useEffect(() => { load() }, [load])

  // Grouped by person, newest day first inside each group.
  const groups = useMemo(() => {
    const m = new Map<string, Item[]>()
    for (const it of filtered) { const k = it.employee || 'Unknown'; m.set(k, [...(m.get(k) || []), it]) }
    return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0]))
      .map(([who, rows]) => [who, [...rows].sort((a, b) => b.work_date.localeCompare(a.work_date))] as const)
  }, [filtered])
  const waiting = items?.length ?? 0

  function onDecided(it: Item, decision: 'approve' | 'reject') {
    setItems(list => {
      const next = (list || []).filter(x => x.id !== it.id)
      // A filter pointing at a person/reason that no longer exists would blank the queue.
      setFStaff(s => (s && !next.some(x => (x.employee || 'Unknown') === s)) ? '' : s)
      setFReason(r => (r && !next.some(x => x.reason === r)) ? '' : r)
      return next
    })
    setActive(null)
    show(decision === 'approve' ? `Approved — ${it.employee}, ${niceDate(it.work_date)}.` : `Rejected — ${it.employee}, ${niceDate(it.work_date)}. That day counts against leave.`)
  }

  return (
    <div style={{ background: C.surface, minHeight: '100vh', fontFamily: sans }}>
      <header style={{ display: 'flex', alignItems: 'center', gap: 12, padding: headerPad, background: C.navy, color: '#fff' }}>
        <Link href={base} aria-label="Back" style={{ color: '#fff', display: 'flex', minWidth: 44, minHeight: 44, alignItems: 'center' }}><ArrowLeft size={20} /></Link>
        <h1 style={{ fontFamily: serif, fontWeight: 800, fontSize: 20, margin: 0, color: '#fff', flex: 1 }}>Review explanations</h1>
        {items !== null && !forbidden && (
          <span style={{ fontSize: 12, fontWeight: 800, background: 'var(--ao-orange-wash, rgba(240,127,0,0.14))', color: C.orange, padding: '6px 10px', borderRadius: 999, whiteSpace: 'nowrap' }}>{waiting} waiting</span>
        )}
      </header>

      <main style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
        {aria && <p aria-live="polite" style={srOnly}>{aria}</p>}

        {forbidden && (
          <div style={{ ...card, padding: 24, textAlign: 'center' }}>
            <p style={{ margin: 0, fontWeight: 800, color: C.ink, fontSize: 16 }}>Only managers see this</p>
            <p style={{ color: C.inkSoft, fontSize: 13, margin: '6px 0 16px', lineHeight: 1.5 }}>This queue is for managers and HR. Your own short days are under &ldquo;My hours&rdquo;.</p>
            <Link href={base} style={{ ...ghostBtn, display: 'inline-flex', alignItems: 'center', textDecoration: 'none' }}>Back to Omni</Link>
          </div>
        )}

        {!forbidden && (
          <>
            <p style={{ color: C.inkSoft, fontSize: 13, margin: 0, lineHeight: 1.5 }}>Short days your team explained. Approve clears the day; reject sends it back as a leave deduction.</p>
            {loadErr && <RetryBanner message={loadErr} onRetry={load} />}
            {items && items.length > 0 && (
              <section aria-label="Filters" style={{ ...card, padding: 12 }}>
                <div style={{ display: 'flex', gap: 8 }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <label style={{ ...labelStyle, marginTop: 0 }} htmlFor="rx-staff">Staff</label>
                    <select id="rx-staff" value={fStaff} onChange={e => setFStaff(e.target.value)} style={inputStyle}>
                      <option value="">Everyone</option>
                      {staffOptions.map(n => <option key={n} value={n}>{n}</option>)}
                    </select>
                  </div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <label style={{ ...labelStyle, marginTop: 0 }} htmlFor="rx-reason">Reason</label>
                    <select id="rx-reason" value={fReason} onChange={e => setFReason(e.target.value)} style={inputStyle}>
                      <option value="">Any reason</option>
                      {reasonOptions.map(r => <option key={r} value={r}>{REASON_LABEL[r] || r}</option>)}
                    </select>
                  </div>
                </div>
                <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <label style={labelStyle} htmlFor="rx-from">From</label>
                    <input id="rx-from" type="date" value={fFrom} onChange={e => setFFrom(e.target.value)} style={inputStyle} />
                  </div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <label style={labelStyle} htmlFor="rx-to">To</label>
                    <input id="rx-to" type="date" value={fTo} onChange={e => setFTo(e.target.value)} style={inputStyle} />
                  </div>
                  {filtering && <button onClick={() => { setFStaff(''); setFReason(''); setFFrom(''); setFTo('') }} style={{ ...ghostBtn, whiteSpace: 'nowrap' }}>Clear</button>}
                </div>
                {filtering && <p style={{ margin: '8px 0 0', fontSize: 12.5, color: C.inkSoft }}>Showing {filtered.length} of {items.length}.</p>}
              </section>
            )}
            {items && items.length > 0 && filtered.length === 0 && (
              <p style={{ color: C.inkSoft, fontSize: 14, textAlign: 'center', margin: '12px 0' }}>Nothing matches those filters.</p>
            )}
            {items === null && !loadErr && <p style={{ color: C.inkSoft, fontSize: 13, textAlign: 'center', margin: '24px 0' }}>Loading…</p>}
            {items && items.length === 0 && (
              <div style={{ ...card, padding: 24, textAlign: 'center' }}>
                <CheckCircle2 size={30} style={{ color: '#047857' }} aria-hidden="true" />
                <p style={{ margin: '8px 0 0', fontWeight: 700, color: C.ink, fontSize: 15 }}>Nothing waiting for review.</p>
              </div>
            )}
            {groups.map(([who, rows]) => (
              <section key={who} aria-label={who} style={{ ...card, padding: 14 }}>
                <h2 style={{ fontFamily: sans, fontSize: 15, fontWeight: 800, color: C.ink, margin: '0 0 4px' }}>{who} <span style={{ color: C.inkSoft, fontWeight: 600 }}>({rows.length})</span></h2>
                {rows.map(it => (
                  <button key={it.id} onClick={() => setActive(it)}
                    style={{ display: 'block', width: '100%', textAlign: 'left', background: 'none', border: 'none', borderTop: `1px solid ${C.line}`, padding: '11px 0', cursor: 'pointer', fontFamily: sans, minHeight: 44 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'baseline' }}>
                      <b style={{ color: C.ink, fontSize: 14.5 }}>{niceDate(it.work_date)} · {REASON_LABEL[it.reason] || it.reason}</b>
                      <span style={{ color: C.inkSoft, fontSize: 12, whiteSpace: 'nowrap' }}>{hrs(it.tracked)} of {hrs(it.required)}</span>
                    </div>
                    <div style={{ color: C.inkSoft, fontSize: 13, marginTop: 3, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {it.justification || <i>No explanation written</i>}
                    </div>
                  </button>
                ))}
              </section>
            ))}
          </>
        )}

        {active && <DecideSheet item={active} onClose={() => setActive(null)} onDone={onDecided} />}
        <Toast text={toast} />
      </main>
    </div>
  )
}

function DecideSheet({ item, onClose, onDone }: { item: Item; onClose: () => void; onDone: (it: Item, d: 'approve' | 'reject') => void }) {
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function decide(decision: 'approve' | 'reject') {
    if (decision === 'reject' && !note.trim()) { setErr('Give a reason for rejecting — the person sees it.'); return }
    const body: ReviewBody = { id: item.id, decision }
    if (note.trim()) body.note = note.trim()
    setBusy(true); setErr(null)
    try {
      await sfetch<{ id: string; status: string }>('/timedoctor/justifications/review/', { method: 'POST', body: JSON.stringify(body) })
      onDone(item, decision)
    } catch (e) { if (reauthOn401(e)) return; setErr(errText(e, 'Could not save the decision.')) }
    finally { setBusy(false) }
  }

  const facts: [string, string][] = [
    ['Required', hrs(item.required)], ['Tracked', hrs(item.tracked)], ['Claims to cover', hrs(item.justified_hours)],
  ]
  if (item.meeting_minutes) facts.push(['Meeting length', `${item.meeting_minutes} min`])
  if (item.location) facts.push(['Where', item.location])

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(15,28,44,0.55)', display: 'flex', alignItems: 'flex-end', zIndex: 65 }} onClick={() => !busy && onClose()}>
      <div role="dialog" aria-modal="true" aria-labelledby="decide-title" onClick={e => e.stopPropagation()}
        style={{ background: C.card, width: '100%', maxHeight: '92vh', overflowY: 'auto', borderRadius: '22px 22px 0 0', padding: `20px 18px calc(24px + ${safeBottom})` }}>
        <h2 id="decide-title" style={{ fontFamily: serif, fontSize: 18, color: C.ink, margin: 0 }}>{item.employee}</h2>
        <p style={{ color: C.inkSoft, fontSize: 12.5, margin: '4px 0 12px' }}>{niceDate(item.work_date)} · {REASON_LABEL[item.reason] || item.reason}</p>

        <dl style={{ margin: 0, display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '4px 12px', fontSize: 13 }}>
          {facts.map(([k, v]) => (<Fragment key={k}><dt style={{ color: C.inkSoft, margin: 0 }}>{k}</dt><dd style={{ color: C.ink, fontWeight: 700, margin: 0 }}>{v}</dd></Fragment>))}
        </dl>

        <p style={{ fontSize: 13.5, color: C.ink, margin: '12px 0 0', background: '#F6F7F9', borderRadius: 10, padding: '10px 12px', lineHeight: 1.5, whiteSpace: 'pre-line' }}>
          {item.justification || <i style={{ color: C.inkSoft }}>No explanation written.</i>}
        </p>

        <label htmlFor="review-note" style={labelStyle}>Note — optional to approve, required to reject</label>
        <textarea id="review-note" value={note} onChange={e => { setNote(e.target.value); setErr(null) }} rows={2} style={{ ...inputStyle, resize: 'vertical' }} placeholder="The person sees this" />

        {err && <div style={{ marginTop: 12 }}><ServerMessage text={err} tone="error" /></div>}

        <div style={{ display: 'flex', gap: 10, marginTop: 14 }}>
          <button onClick={() => decide('approve')} disabled={busy}
            style={{ flex: 1, minHeight: 48, padding: 12, borderRadius: 999, border: 'none', background: '#047857', color: '#fff', fontWeight: 800, fontSize: 14, cursor: 'pointer', fontFamily: sans, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6, opacity: busy ? 0.6 : 1 }}>
            <CheckCircle2 size={16} aria-hidden="true" /> Approve
          </button>
          <button onClick={() => decide('reject')} disabled={busy}
            style={{ flex: 1, minHeight: 48, padding: 12, borderRadius: 999, border: `1px solid ${C.line}`, background: C.card, color: '#B91C1C', fontWeight: 800, fontSize: 14, cursor: 'pointer', fontFamily: sans, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6, opacity: busy ? 0.6 : 1 }}>
            <XCircle size={16} aria-hidden="true" /> Reject
          </button>
        </div>
        <button onClick={onClose} disabled={busy} style={{ width: '100%', marginTop: 10, minHeight: 44, padding: 10, borderRadius: 999, border: 'none', background: 'none', color: C.inkSoft, fontWeight: 700, fontSize: 14, cursor: 'pointer', fontFamily: sans }}>Cancel</button>
      </div>
    </div>
  )
}
