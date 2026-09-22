'use client'

/** /m/staff/card-spend — company-card bills (CFO 2026-08-07, extended 2026-09-05).
 *
 * Two jobs on one screen:
 *   1. BILLS TO EXPLAIN — anything Finance is asking about, or a charge on your
 *      statement with no receipt. Tap one, snap the slip, say ~25 words, done.
 *   2. LOG A SPEND — capture a slip the moment you pay. The photo is NEVER
 *      blocked on the words: snap now, say it later (it drops into the list above
 *      until you add the reason). "Executives are lazy to write 50 words" — so it
 *      is 25, and you can just TALK it with the mic (CFO 2026-09-05).
 *
 * TWO upload buttons on purpose: "Take a photo" forces the camera; "Choose a
 * file" has no capture, so Android offers Drive, Files and the gallery.
 */
import { useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { ArrowLeft, Camera, FolderOpen, X, Check, Mic, MicOff, HelpCircle } from 'lucide-react'
import { sfetch, compressImage, reauthOn401 } from '@/app/(customer)/api'
import { C, serif, sans, card, headerPad } from '@/app/(customer)/ui'
import { useStaffBase } from './useStaffBase'
import { localYmd } from '@/lib/utils'

const WORDS_REQUIRED = 25

interface Card { id: string; label: string; last4: string; currency: string; holder: string }
interface Spend {
  id: string; card: string; card_label: string; spent_on: string; merchant: string
  amount: string; what_for: string; has_receipt: boolean; is_explained: boolean
  status: string; status_label: string; finance_note: string
}
interface Line {
  id: string; kind: 'line'; card: string; card_label: string; posted_on: string
  description: string; amount: string
}
// What we are answering: a queried/unexplained spend, a bare statement line, or
// nothing (a fresh capture).
type Target = { kind: 'spend'; spend: Spend } | { kind: 'line'; line: Line } | null

const inputStyle: React.CSSProperties = {
  width: '100%', boxSizing: 'border-box', padding: '13px 14px', border: 'none',
  background: '#F0F2F5', borderRadius: 12, fontSize: 15, color: C.ink,
  outline: 'none', fontFamily: sans,
}
const today = () => localYmd(new Date())
const wordCount = (t: string) => (t.trim() ? t.trim().split(/\s+/).length : 0)

export default function CardSpend() {
  const base = useStaffBase()
  const [cards, setCards] = useState<Card[]>([])
  const [openSpends, setOpenSpends] = useState<Spend[]>([])
  const [openLines, setOpenLines] = useState<Line[]>([])

  const [target, setTarget] = useState<Target>(null)
  const [cardId, setCardId] = useState('')
  const [amount, setAmount] = useState('')
  const [merchant, setMerchant] = useState('')
  const [whatFor, setWhatFor] = useState('')
  const [spentOn, setSpentOn] = useState(today())
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)
  const [toast, setToast] = useState<string | null>(null)
  const [listening, setListening] = useState(false)

  const camRef = useRef<HTMLInputElement | null>(null)
  const fileRef = useRef<HTMLInputElement | null>(null)
  const recRef = useRef<any>(null)
  const show = (m: string) => { setToast(m); setTimeout(() => setToast(null), 4000) }

  const loadCards = () => sfetch<{ cards: Card[] }>('/company-cards/')
    .then(d => { setCards(d.cards || []); if ((d.cards || []).length === 1) setCardId(d.cards[0].id) })
    .catch(e => { if (!reauthOn401(e)) show('Could not load your cards — check your connection.') })

  const loadOpen = () => sfetch<{ spends: Spend[]; lines: Line[] }>('/company-cards/my-open-items/')
    .then(d => { setOpenSpends(d.spends || []); setOpenLines(d.lines || []) })
    .catch(() => { /* the list is a bonus; never block the capture form on it */ })

  useEffect(() => { loadCards(); loadOpen() }, [])   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview) }, [preview])

  // ── the phone mic: speak the reason instead of thumbing it out ──────────────
  const micSupported = typeof window !== 'undefined' &&
    !!((window as any).SpeechRecognition || (window as any).webkitSpeechRecognition)
  const toggleMic = () => {
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition
    if (!SR) return
    if (listening) { recRef.current?.stop(); return }
    const r = new SR()
    r.lang = 'en-GB'; r.interimResults = false; r.continuous = true
    r.onresult = (e: any) => {
      let s = ''
      for (let i = e.resultIndex; i < e.results.length; i++) s += e.results[i][0].transcript
      setWhatFor(w => (w.trim() ? w.trim() + ' ' : '') + s.trim())
    }
    r.onerror = () => setListening(false)
    r.onend = () => setListening(false)
    recRef.current = r
    try { r.start(); setListening(true) } catch { setListening(false) }
  }
  const stopMic = () => { try { recRef.current?.stop() } catch { /* noop */ } setListening(false) }

  const onPick = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const raw = e.target.files?.[0]
    e.target.value = ''
    if (!raw) return
    const f = raw.type.startsWith('image/') ? await compressImage(raw) : raw
    if (preview) URL.revokeObjectURL(preview)
    setFile(f)
    setPreview(f.type.startsWith('image/') ? URL.createObjectURL(f) : null)
  }

  const resetForm = () => {
    stopMic()
    setTarget(null); setAmount(''); setMerchant(''); setWhatFor(''); setSpentOn(today())
    if (preview) URL.revokeObjectURL(preview)
    setFile(null); setPreview(null); setDone(false)
    if (cards.length === 1) setCardId(cards[0].id); else setCardId('')
  }

  // Prefill the form to answer an open item and jump to it.
  const answerSpend = (s: Spend) => {
    resetForm()
    setTarget({ kind: 'spend', spend: s })
    setCardId(s.card); setAmount(s.amount); setMerchant(s.merchant)
    setSpentOn(s.spent_on); setWhatFor(s.is_explained ? s.what_for : '')
    setTimeout(() => document.getElementById('answer-form')?.scrollIntoView({ behavior: 'smooth' }), 60)
  }
  const answerLine = (l: Line) => {
    resetForm()
    setTarget({ kind: 'line', line: l })
    setCardId(l.card); setAmount(l.amount); setMerchant(l.description); setSpentOn(l.posted_on)
    setTimeout(() => document.getElementById('answer-form')?.scrollIntoView({ behavior: 'smooth' }), 60)
  }

  const wc = wordCount(whatFor)
  const answering = target !== null
  // Answering must hit 25 words. A fresh capture never blocks the photo: three
  // characters is enough to save, the words can come later.
  const ready = answering
    ? (cardId && Number(amount) > 0 && spentOn && wc >= WORDS_REQUIRED)
    : (cardId && Number(amount) > 0 && spentOn && whatFor.trim().length >= 3)

  const submit = async () => {
    if (!ready || busy) return
    stopMic(); setBusy(true)
    try {
      const fd = new FormData()
      if (target?.kind === 'spend') {
        fd.append('what_for', whatFor.trim())
        if (file) fd.append('receipt', file)
        await sfetch(`/company-cards/spends/${target.spend.id}/explain/`, { method: 'POST', body: fd })
      } else {
        // A fresh capture, or answering a statement line (which creates the spend
        // that the matcher then links to the line).
        fd.append('card', cardId)
        fd.append('amount', amount)
        fd.append('spent_on', spentOn)
        fd.append('what_for', whatFor.trim())
        if (merchant.trim()) fd.append('merchant', merchant.trim())
        if (file) fd.append('receipt', file)
        await sfetch('/company-cards/spends/', { method: 'POST', body: fd })
      }
      setDone(true)
      loadOpen()
    } catch (e) {
      if (!reauthOn401(e)) show(e instanceof Error ? e.message : 'Could not save it.')
    } finally {
      setBusy(false)
    }
  }

  const openCount = openSpends.length + openLines.length

  return (
    <div style={{ minHeight: '100vh', background: C.surface, fontFamily: sans, paddingBottom: 90 }}>
      <header style={{ background: C.navy, padding: headerPad }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <Link href={base} aria-label="Back" style={{ color: '#fff', display: 'grid', placeItems: 'center', minWidth: 44, minHeight: 44, margin: '-12px 0 -12px -12px' }}>
            <ArrowLeft size={22} aria-hidden />
          </Link>
          <h1 style={{ fontFamily: serif, fontSize: 24, fontWeight: 700, margin: 0, color: '#fff' }}>Company card</h1>
        </div>
      </header>

      <main style={{ padding: 16 }}>
        {cards.length === 0 && (
          <div style={{ ...card, padding: 18, color: C.inkSoft, fontSize: 14 }}>
            You do not have a company card on Omni. If that is wrong, tell Finance.
          </div>
        )}

        {done && (
          <div style={{ ...card, padding: 20, textAlign: 'center' }}>
            <Check size={34} style={{ color: '#047857' }} />
            <div style={{ fontFamily: serif, fontSize: 20, fontWeight: 700, color: C.ink, marginTop: 6 }}>
              Sharp sharp!
            </div>
            <p style={{ color: C.inkSoft, fontSize: 14, margin: '6px 0 16px' }}>
              {answering ? 'Finance has it. Nothing else for you to do.'
                         : 'Saved. Finance will code it to the right account.'}
            </p>
            <button onClick={resetForm} style={{ width: '100%', padding: 15, border: 'none', borderRadius: 12, background: C.navy, color: '#fff', fontSize: 16, fontWeight: 700 }}>
              {openCount > 0 ? `Back to your ${openCount} bill${openCount > 1 ? 's' : ''} to explain` : 'Log another'}
            </button>
          </div>
        )}

        {/* ── BILLS TO EXPLAIN ─────────────────────────────────────────────── */}
        {!done && openCount > 0 && (
          <section style={{ marginBottom: 18 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '2px 2px 10px' }}>
              <HelpCircle size={18} style={{ color: C.orange }} />
              <h2 style={{ fontFamily: serif, fontSize: 18, fontWeight: 700, margin: 0, color: C.ink }}>
                Bills to explain ({openCount})
              </h2>
            </div>

            {openSpends.map(s => (
              <button key={s.id} onClick={() => answerSpend(s)}
                      style={{ ...card, width: '100%', textAlign: 'left', padding: 14, marginBottom: 10, border: s.status === 'queried' ? `1.5px solid ${C.orange}` : undefined }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10 }}>
                  <span style={{ fontWeight: 700, color: C.ink, fontSize: 14 }}>{s.merchant || 'Card charge'}</span>
                  <span style={{ fontWeight: 700, color: C.ink, fontSize: 14 }}>BWP {s.amount}</span>
                </div>
                <div style={{ color: C.inkSoft, fontSize: 12.5, marginTop: 2 }}>{s.spent_on} · {s.card_label}</div>
                {s.status === 'queried' && s.finance_note && (
                  <div style={{ marginTop: 8, background: '#FFF7EC', border: '1px solid #FCE3BD', borderRadius: 10, padding: '8px 10px', color: '#92400E', fontSize: 13 }}>
                    Finance asks: {s.finance_note}
                  </div>
                )}
                {s.status !== 'queried' && !s.is_explained && (
                  <div style={{ marginTop: 6, color: C.orange, fontSize: 12.5, fontWeight: 600 }}>Add a few words → tap to explain</div>
                )}
              </button>
            ))}

            {openLines.map(l => (
              <button key={l.id} onClick={() => answerLine(l)}
                      style={{ ...card, width: '100%', textAlign: 'left', padding: 14, marginBottom: 10 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10 }}>
                  <span style={{ fontWeight: 700, color: C.ink, fontSize: 14 }}>{l.description || 'Statement charge'}</span>
                  <span style={{ fontWeight: 700, color: C.ink, fontSize: 14 }}>BWP {l.amount}</span>
                </div>
                <div style={{ color: C.inkSoft, fontSize: 12.5, marginTop: 2 }}>{l.posted_on} · {l.card_label} · no receipt yet</div>
                <div style={{ marginTop: 6, color: C.orange, fontSize: 12.5, fontWeight: 600 }}>Attach the slip → tap to explain</div>
              </button>
            ))}
          </section>
        )}

        {/* ── THE FORM (fresh capture, or answering the selected item) ──────── */}
        {!done && cards.length > 0 && (
          <div id="answer-form">
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', margin: '2px 2px 10px' }}>
              <h2 style={{ fontFamily: serif, fontSize: 18, fontWeight: 700, margin: 0, color: C.ink }}>
                {answering ? 'Explain this charge' : 'Log a spend'}
              </h2>
              {answering && (
                <button onClick={resetForm} style={{ border: 'none', background: 'transparent', color: C.inkSoft, fontSize: 13, fontWeight: 600 }}>
                  Cancel
                </button>
              )}
            </div>

            {/* Two ways in: the camera, and everything else (Drive, Files, gallery). */}
            <div style={{ display: 'flex', gap: 10, marginBottom: 12 }}>
              <button onClick={() => camRef.current?.click()}
                      style={{ flex: 1, padding: '18px 10px', border: `2px dashed ${C.orange}`, borderRadius: 14, background: '#FFF7EC', color: '#92400E', fontSize: 14, fontWeight: 700, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6 }}>
                <Camera size={22} />Take a photo
              </button>
              <button onClick={() => fileRef.current?.click()}
                      style={{ flex: 1, padding: '18px 10px', border: '2px dashed #C7CDD6', borderRadius: 14, background: '#F7F8FA', color: C.ink, fontSize: 14, fontWeight: 700, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6 }}>
                <FolderOpen size={22} />Choose a file
              </button>
            </div>
            <p style={{ color: C.inkSoft, fontSize: 12, margin: '-4px 0 14px', textAlign: 'center' }}>
              &ldquo;Choose a file&rdquo; opens Google Drive, Files and your gallery.
            </p>
            <input ref={camRef} type="file" accept="image/*" capture="environment" style={{ display: 'none' }} onChange={onPick} />
            <input ref={fileRef} type="file" accept="image/*,application/pdf" style={{ display: 'none' }} onChange={onPick} />

            {file && (
              <div style={{ ...card, padding: 12, marginBottom: 14, display: 'flex', alignItems: 'center', gap: 12 }}>
                {preview
                  ? <img src={preview} alt="" style={{ width: 54, height: 54, objectFit: 'cover', borderRadius: 8 }} />
                  : <div style={{ width: 54, height: 54, borderRadius: 8, background: '#F0F2F5', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 11, color: C.inkSoft }}>PDF</div>}
                <div style={{ flex: 1, fontSize: 13, color: C.ink, overflow: 'hidden', textOverflow: 'ellipsis' }}>{file.name}</div>
                <button onClick={() => { if (preview) URL.revokeObjectURL(preview); setFile(null); setPreview(null) }}
                        aria-label="Remove" style={{ border: 'none', background: 'transparent', color: C.inkSoft, minWidth: 44, minHeight: 44, display: 'grid', placeItems: 'center' }}>
                  <X size={18} />
                </button>
              </div>
            )}

            <div style={{ ...card, padding: 16, display: 'grid', gap: 12 }}>
              {cards.length > 1 && !answering && (
                <select value={cardId} onChange={e => setCardId(e.target.value)} aria-label="Which card" style={inputStyle}>
                  <option value="">Which card?</option>
                  {cards.map(c => <option key={c.id} value={c.id}>{c.label} ••••{c.last4}</option>)}
                </select>
              )}
              <div style={{ display: 'flex', gap: 10 }}>
                <input value={amount} onChange={e => setAmount(e.target.value)} inputMode="decimal"
                       placeholder="Amount (BWP)" aria-label="Amount (BWP)" readOnly={answering}
                       style={{ ...inputStyle, flex: 1, opacity: answering ? 0.7 : 1 }} />
                <input value={spentOn} onChange={e => setSpentOn(e.target.value)} type="date" max={today()}
                       aria-label="Date spent" readOnly={answering}
                       style={{ ...inputStyle, flex: 1, opacity: answering ? 0.7 : 1 }} />
              </div>
              {!answering && (
                <input value={merchant} onChange={e => setMerchant(e.target.value)}
                       placeholder="Where (optional) — e.g. Sanitas" aria-label="Where (optional)" style={inputStyle} />
              )}

              {/* The explanation — a real few words, spoken or typed. */}
              <div style={{ position: 'relative' }}>
                <textarea value={whatFor} onChange={e => setWhatFor(e.target.value)} rows={3}
                          placeholder={answering
                            ? 'What was it, who was it for, and why did Alpha Direct pay? Tap the mic and just say it.'
                            : 'What was it for? One line is fine now — add the detail later.'}
                          aria-label="What it was for"
                          style={{ ...inputStyle, resize: 'none', paddingRight: 52, lineHeight: 1.4 }} />
                {micSupported && (
                  <button onClick={toggleMic} aria-label={listening ? 'Stop dictation' : 'Speak the reason'}
                          style={{ position: 'absolute', right: 8, top: 8, width: 38, height: 38, borderRadius: 10, border: 'none',
                                   background: listening ? C.orange : '#E7EAEF', color: listening ? '#fff' : C.ink,
                                   display: 'grid', placeItems: 'center' }}>
                    {listening ? <MicOff size={18} /> : <Mic size={18} />}
                  </button>
                )}
              </div>

              {answering ? (
                <p style={{ fontSize: 12.5, margin: 0, color: wc >= WORDS_REQUIRED ? '#047857' : C.inkSoft }}>
                  {wc >= WORDS_REQUIRED
                    ? `${wc} words — that's plenty. ✓`
                    : `${WORDS_REQUIRED - wc} more word${WORDS_REQUIRED - wc === 1 ? '' : 's'} (at least ${WORDS_REQUIRED}).`}
                  {micSupported && wc < WORDS_REQUIRED && <> Tap the mic and just say it.</>}
                </p>
              ) : (
                <p style={{ color: C.inkSoft, fontSize: 12, margin: 0 }}>
                  Snap now, say it later — the photo saves either way. Finance may ask for a fuller reason.
                </p>
              )}

              <button onClick={submit} disabled={!ready || busy}
                      style={{ width: '100%', padding: 16, border: 'none', borderRadius: 12,
                               background: ready && !busy ? C.orange : '#D7DBE0', color: ready && !busy ? C.navy : '#8A9099',
                               fontSize: 16, fontWeight: 700 }}>
                {busy ? 'Saving…'
                  : answering ? (wc >= WORDS_REQUIRED ? 'Send to Finance' : `${WORDS_REQUIRED - wc} more words`)
                  : 'Save this spend'}
              </button>
            </div>
          </div>
        )}
      </main>

      {toast && (
        <div style={{ position: 'fixed', left: 16, right: 16, bottom: 100, background: C.navy, color: '#fff', padding: '13px 16px', borderRadius: 12, fontSize: 14, zIndex: 60 }}>
          {toast}
        </div>
      )}
    </div>
  )
}
