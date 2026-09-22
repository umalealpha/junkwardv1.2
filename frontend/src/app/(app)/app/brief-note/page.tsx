'use client'
// Omni Mobile — morning brief note (CFO 2026-09-10).
// 25 words to the CEO or the CFO, once a morning. "We want to limit
// communications and wasting the time of the CEO" — so THE LIMIT IS THE
// FEATURE: one note per person per space per morning, editable and
// withdrawable right up until the brief goes out, frozen the moment it does.
//
// The server is idempotent on (audience, author, for_date), so a double-tap
// replaces the note instead of adding one — no client_key needed here (the
// quick-task habit does not apply). Gating is the SCREEN's job, not the home
// tile's: the tile shows for everyone and this page says what you may do.
import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { ChevronLeft, Loader2, Trash2 } from 'lucide-react'
import {
  getBriefNoteAccess, getBriefNoteSpace, writeBriefNote, withdrawBriefNote,
  type BriefAudience, type BriefNoteAccess, type BriefNoteSpace,
} from '../../api'
import { C, card, serif, sans, headerPad, h } from '../../ui'

const inputStyle: React.CSSProperties = {
  width: '100%', boxSizing: 'border-box', padding: '13px 14px', border: 'none',
  background: '#F0F2F5', borderRadius: 12, fontSize: 15, color: C.ink,
  outline: 'none', fontFamily: sans,
}
const SPACE_LABEL: Record<BriefAudience, string> = { ceo: 'CEO', cfo: 'CFO' }

/** The morning this note lands in, in human words. Built from the Y-M-D parts,
 * never new Date('2026-09-11') — that parses as UTC midnight and reads as the
 * day before in a negative-offset browser. */
function humanMorning(ymd: string): string {
  const [y, m, d] = ymd.split('-').map(Number)
  if (!y || !m || !d) return ymd
  const target = new Date(y, m - 1, d)
  const today = new Date(); today.setHours(0, 0, 0, 0)
  const days = Math.round((target.getTime() - today.getTime()) / 864e5)
  const pretty = target.toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' })
  if (days <= 0) return `this morning, ${pretty}`
  if (days === 1) return `tomorrow morning, ${pretty}`
  return pretty
}

export default function BriefNoteScreen() {
  const [access, setAccess] = useState<BriefNoteAccess | null>(null)
  const [accessErr, setAccessErr] = useState('')
  const [audience, setAudience] = useState<BriefAudience | null>(null)
  const [space, setSpace] = useState<BriefNoteSpace | null>(null)
  const [spaceErr, setSpaceErr] = useState('')
  const [body, setBody] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [toast, setToast] = useState('')
  const show = (m: string) => { setToast(m); setTimeout(() => setToast(''), 4000) }

  useEffect(() => {
    getBriefNoteAccess()
      .then(a => { setAccess(a); setAudience(a.audiences[0] ?? null); setAccessErr('') })
      // A failed load is an error, never "you have no space" — that would tell
      // an executive he has been shut out when the phone simply lost signal.
      .catch(e => setAccessErr((e as Error)?.message || 'Could not check your access. Try again.'))
  }, [])

  const load = useCallback((a: BriefAudience) => {
    setSpace(null); setSpaceErr(''); setErr('')
    getBriefNoteSpace(a)
      .then(s => { setSpace(s); setBody(s.my_note?.body || '') })
      .catch(e => setSpaceErr((e as Error)?.message || 'Could not open that space. Try again.'))
  }, [])

  useEffect(() => { if (audience) load(audience) }, [audience, load])

  // Counted at render, every keystroke — never parked in state by an effect,
  // which is how a counter ends up one word behind the box.
  const words = body.trim() ? body.trim().split(/\s+/).length : 0
  const limit = space?.word_limit ?? access?.word_limit ?? 25
  const over = words > limit
  const mine = space?.my_note ?? null
  const locked = !!mine?.locked
  const ready = words > 0 && !over && !busy

  const save = async () => {
    if (!audience || !ready) return
    setBusy(true); setErr('')
    try {
      await writeBriefNote(audience, body.trim())
      show(mine ? 'Note updated' : 'Note queued for the brief')
      load(audience)
    } catch (e) {
      setErr((e as Error)?.message || 'Could not save the note. Try again.')
    } finally { setBusy(false) }
  }

  const withdraw = async () => {
    if (!mine || !audience || busy) return
    setBusy(true); setErr('')
    try {
      await withdrawBriefNote(mine.id)
      setBody(''); show('Note withdrawn')
      load(audience)
    } catch (e) {
      setErr((e as Error)?.message || 'Could not withdraw the note. Try again.')
    } finally { setBusy(false) }
  }

  return (
    <main>
      <Header />
      <section style={{ padding: '0 16px', marginTop: -28, display: 'grid', gap: 12 }}>
        {access === null && !accessErr && (
          <div className="oa-rise" style={{ ...card, padding: 20, textAlign: 'center', color: C.inkSoft }}>
            <Loader2 size={20} className="oa-spin" />
          </div>
        )}

        {accessErr && (
          <div role="alert" className="oa-rise" style={{ ...card, padding: 14, borderLeft: `4px solid ${C.red}`, color: C.red, fontSize: 14 }}>{accessErr}</div>
        )}

        {access !== null && access.audiences.length === 0 && (
          <div className="oa-rise" style={{ ...card, padding: 20 }}>
            <div style={h(17)}>This isn&rsquo;t open to you yet.</div>
            <p style={{ margin: '8px 0 0', fontSize: 14, color: C.inkSoft, lineHeight: 1.5 }}>
              The morning brief spaces are limited on purpose. Use Quick task or Report a bug in the meantime.
            </p>
          </div>
        )}

        {access !== null && access.audiences.length > 1 && (
          <div role="group" aria-label="Which morning brief" className="oa-rise"
               style={{ ...card, padding: 6, display: 'flex', gap: 6 }}>
            {access.audiences.map(a => (
              <button key={a} onClick={() => setAudience(a)} aria-pressed={audience === a} className="oa-press"
                      style={{ flex: 1, minHeight: 44, borderRadius: 14, border: 0, cursor: 'pointer', fontFamily: sans,
                               background: audience === a ? C.navy : 'transparent', color: audience === a ? '#fff' : C.ink,
                               fontWeight: 700, fontSize: 15 }}>
                {SPACE_LABEL[a]}
              </button>
            ))}
          </div>
        )}

        {audience && space === null && !spaceErr && (
          <div style={{ ...card, padding: 20, textAlign: 'center', color: C.inkSoft }}>
            <Loader2 size={20} className="oa-spin" />
          </div>
        )}

        {spaceErr && (
          <div role="alert" style={{ ...card, padding: 14, borderLeft: `4px solid ${C.red}`, color: C.red, fontSize: 14 }}>{spaceErr}</div>
        )}

        {audience && space && (
          <div className="oa-rise oa-rise-2" style={{ ...card, padding: 16, display: 'grid', gap: 12 }}>
            <div>
              <div style={h(17)}>{SPACE_LABEL[audience]} morning brief</div>
              <p style={{ margin: '6px 0 0', fontSize: 13, color: C.inkSoft, lineHeight: 1.5 }}>
                Goes out {humanMorning(space.for_date)}.
                {space.shared ? ' Everyone in this space sees it.' : ' Only they see it.'}
              </p>
            </div>

            {locked ? (
              <p style={{ margin: 0, fontSize: 14, color: C.ink, lineHeight: 1.5 }}>
                Your note has already gone out in this morning&rsquo;s brief, so it can no longer be
                changed or withdrawn. Your next one goes in the following brief.
                <span style={{ display: 'block', marginTop: 10, padding: '10px 12px', background: '#F0F2F5', borderRadius: 12, fontSize: 14 }}>
                  &ldquo;{mine?.body}&rdquo;
                </span>
              </p>
            ) : !space.can_post ? (
              <p style={{ margin: 0, fontSize: 14, color: C.inkSoft, lineHeight: 1.5 }}>
                You can read this space but not write in it.
              </p>
            ) : (
              <>
                <textarea value={body} onChange={e => setBody(e.target.value)} rows={3} autoFocus
                          aria-label={`Your note to the ${SPACE_LABEL[audience]}`}
                          placeholder={`One thing the ${SPACE_LABEL[audience]} should know tomorrow. ${limit} words.`}
                          style={{ ...inputStyle, resize: 'none', lineHeight: 1.4 }} />

                <p aria-live="polite" style={{ fontSize: 12.5, margin: 0, fontWeight: over ? 700 : 400, color: over ? C.red : C.inkSoft }}>
                  {words} / {limit} words{over ? ` — ${words - limit} too many. The cap is the point.` : ''}
                </p>

                {err && <div role="alert" style={{ borderLeft: `4px solid ${C.red}`, paddingLeft: 10, color: C.red, fontSize: 14, lineHeight: 1.5 }}>{err}</div>}

                <button onClick={save} disabled={!ready}
                        style={{ width: '100%', padding: 16, border: 'none', borderRadius: 12, cursor: ready ? 'pointer' : 'default',
                                 background: ready ? C.orange : '#D7DBE0', color: ready ? C.navy : '#8A9099',
                                 fontSize: 16, fontWeight: 700, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8 }}>
                  {busy ? <Loader2 size={18} className="oa-spin" /> : null}
                  {busy ? 'Saving…' : mine ? 'Update' : 'Send to the brief'}
                </button>

                {mine && (
                  <button onClick={withdraw} disabled={busy} className="oa-press"
                          style={{ minHeight: 44, borderRadius: 12, border: `1px solid ${C.line}`, background: C.card, color: C.red,
                                   fontWeight: 700, fontSize: 14, cursor: busy ? 'default' : 'pointer', fontFamily: sans,
                                   display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8 }}>
                    <Trash2 size={16} /> Withdraw
                  </button>
                )}
              </>
            )}
          </div>
        )}
      </section>

      {/* The CEO space is shared on purpose - the seven see each other's notes
          so two of them never raise the same thing. The CFO space is not
          shared, and the server already withholds other people's notes there,
          so this block simply never has anything to show. */}
      {(space?.shared || space?.can_read_all) && space.notes.filter(n => !n.is_mine).length > 0 && (
        <section style={{ ...card, margin: '0 16px 24px', padding: 16, display: 'grid', gap: 14 }}>
          <h2 style={{ fontFamily: serif, fontSize: 16, fontWeight: 700, margin: 0, color: C.ink }}>
            Also going in this morning
          </h2>
          {space.notes.filter(n => !n.is_mine).map(n => (
            <div key={n.id} style={{ display: 'grid', gap: 3 }}>
              <span style={{ fontSize: 11, letterSpacing: 0.4, textTransform: 'uppercase', color: C.inkSoft }}>
                {n.author}
              </span>
              <span style={{ fontFamily: serif, fontSize: 14, lineHeight: '21px', color: C.ink }}>
                &ldquo;{n.body}&rdquo;
              </span>
            </div>
          ))}
        </section>
      )}

      {toast && (
        <div role="status" style={{ position: 'fixed', left: 16, right: 16, bottom: 100, background: C.navy, color: '#fff', padding: '13px 16px', borderRadius: 12, fontSize: 14, zIndex: 60 }}>
          {toast}
        </div>
      )}
    </main>
  )
}

function Header() {
  return (
    <header style={{ background: C.navy, color: '#fff', padding: headerPad, paddingBottom: 44, display: 'flex', alignItems: 'center', gap: 10 }}>
      <Link href="/app" aria-label="Home" className="oa-press" style={{ color: '#fff', display: 'grid', placeItems: 'center', width: 36, height: 36, marginLeft: -8 }}><ChevronLeft size={24} /></Link>
      <div>
        <h1 style={{ fontFamily: serif, fontSize: 24, fontWeight: 700, lineHeight: 1, margin: 0 }}>Morning brief note</h1>
        <div style={{ opacity: 0.8, fontSize: 13, marginTop: 4 }}>25 words, once a morning</div>
      </div>
    </header>
  )
}
