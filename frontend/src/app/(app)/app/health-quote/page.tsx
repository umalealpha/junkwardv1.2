'use client'
// Omni Mobile — Health cover quote (CFO request).
// Tap man/woman, pick a plan + age → live OFFICE price (the same rater the saved
// quotes use), then generate + email the quote PDF to the client. Reuses the
// existing healthcare engine: POST /health/quotes/price/ (live price),
// POST /health/quotes/ (create), POST /health/quotes/<id>/email/ (email PDF).
import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { ChevronLeft, Check, Loader2, Mail } from 'lucide-react'
import { afetch } from '../../api'
import { C, card, serif, headerPad, h } from '../../ui'

type Gender = 'F' | 'M'
interface Price { premium_excl: string; vat: string; premium_incl: string; age_band: string; currency: string }
interface Created { id: string; ref?: string }

// The five office plans (stable, CFO-confirmed). The PRICE always comes from the
// server so the phone can never show a wrong premium.
const PLANS: { key: string; label: string }[] = [
  { key: 'AD_LITE', label: 'AD Lite' },
  { key: 'AD_ESSENTIAL', label: 'AD Essential' },
  { key: 'AD_CORE', label: 'AD Core' },
  { key: 'AD_PREMIER', label: 'AD Premier' },
  { key: 'AD_STATUS', label: 'AD Status' },
]

function pula(n: string | number): string {
  const v = typeof n === 'string' ? parseFloat(n) : n
  return isNaN(v) ? '—' : `P ${v.toLocaleString('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

export default function HealthQuote() {
  const [gender, setGender] = useState<Gender>('F')
  const [tier, setTier] = useState('')
  const [age, setAge] = useState('')
  const [price, setPrice] = useState<Price | null>(null)
  const [priceErr, setPriceErr] = useState('')
  const [pricing, setPricing] = useState(false)

  const [client, setClient] = useState('')
  const [email, setEmail] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [sent, setSent] = useState<{ to: string } | null>(null)
  const [createdId, setCreatedId] = useState('')

  const ageN = parseInt(age, 10)
  const ready = !!tier && ageN > 0 && ageN < 120

  // Live price whenever gender / plan / age is complete.
  useEffect(() => {
    if (!ready) { setPrice(null); setPriceErr(''); return }
    let alive = true
    setPricing(true); setPriceErr('')
    const t = setTimeout(() => {
      afetch<Price>('/health/quotes/price/', {
        method: 'POST',
        body: JSON.stringify({ tier, gender, member_type: 'main', age: ageN }),
      })
        .then(p => { if (alive) { setPrice(p); setPriceErr('') } })
        .catch(e => { if (alive) { setPrice(null); setPriceErr((e as Error)?.message || 'No price for that combination.') } })
        .finally(() => { if (alive) setPricing(false) })
    }, 250)
    return () => { alive = false; clearTimeout(t) }
  }, [gender, tier, ageN, ready])

  // Drop the created quote id whenever a quote-defining input changes, so a
  // resend after a change re-creates rather than emailing the old combination.
  useEffect(() => { setCreatedId('') }, [tier, ageN, gender, client, email])

  const canSend = ready && !pricing && !!price && client.trim().length > 1 && /.+@.+\..+/.test(email) && !busy

  const send = async () => {
    if (!canSend) return
    setBusy(true); setErr('')
    try {
      // Create the quote once; a retry after an email failure re-uses the same
      // row instead of minting a duplicate draft in the desktop register.
      let id = createdId
      if (!id) {
        const q = await afetch<Created>('/health/quotes/', {
          method: 'POST',
          body: JSON.stringify({
            client_name: client.trim(),
            contact_email: email.trim(),
            members: [{ full_name: client.trim(), member_type: 'main', gender, age: ageN, tier }],
          }),
        })
        id = q.id
        setCreatedId(id)
      }
      await afetch<{ emailed: boolean }>(`/health/quotes/${id}/email/`, {
        method: 'POST',
        body: JSON.stringify({ to: email.trim() }),
      })
      setSent({ to: email.trim() })
    } catch (e) {
      setErr((e as Error)?.message || 'Could not generate or email the quote. Try again.')
    } finally { setBusy(false) }
  }

  const planLabel = useMemo(() => PLANS.find(p => p.key === tier)?.label || '', [tier])

  if (sent) {
    return (
      <main>
        <Header />
        <section style={{ padding: '0 16px', marginTop: -28, display: 'grid', gap: 12 }}>
          <div className="oa-rise" style={{ ...card, padding: 22, textAlign: 'center' }}>
            <div style={{ display: 'grid', placeItems: 'center', width: 56, height: 56, borderRadius: 999, background: 'rgba(4,120,87,0.12)', margin: '0 auto 12px' }}><Check size={28} color={C.green} /></div>
            <div style={{ fontFamily: serif, fontSize: 20, fontWeight: 700, color: C.head }}>Quote sent</div>
            <div style={{ color: C.inkSoft, fontSize: 14, marginTop: 4 }}>The health cover quote is on its way to {sent.to}.</div>
          </div>
          <button onClick={() => { setSent(null); setClient(''); setEmail(''); setPrice(null); setTier(''); setAge(''); setCreatedId('') }}
            className="oa-press" style={{ minHeight: 48, borderRadius: 12, border: `1px solid ${C.line}`, background: C.card, color: C.head, fontWeight: 700, fontSize: 15 }}>
            New quote
          </button>
          <Link href="/app" className="oa-press" style={{ textAlign: 'center', color: C.inkSoft, fontSize: 14, padding: 8, textDecoration: 'none' }}>Back to home</Link>
        </section>
      </main>
    )
  }

  return (
    <main>
      <Header />
      <section style={{ padding: '0 16px', marginTop: -28, display: 'grid', gap: 14 }}>
        {/* Who is it for — the man/woman choice drives the gender rate. */}
        <div className="oa-rise" style={{ ...card, padding: 16 }}>
          <div style={h(15)}>Who is it for?</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginTop: 12 }}>
            {(['F', 'M'] as Gender[]).map(g => (
              <button key={g} onClick={() => setGender(g)} className="oa-press"
                style={{ border: `2px solid ${gender === g ? C.orange : C.line}`, borderRadius: 16, background: C.card, padding: 10, cursor: 'pointer', display: 'grid', justifyItems: 'center', gap: 6 }}>
                <img src={`/health/${g === 'F' ? 'woman' : 'man'}.webp`} alt={g === 'F' ? 'Woman' : 'Man'}
                  style={{ width: '100%', height: 128, objectFit: 'contain', borderRadius: 10 }} />
                <span style={{ fontWeight: 700, fontSize: 15, color: gender === g ? C.navy : C.inkSoft }}>{g === 'F' ? 'Woman' : 'Man'}</span>
              </button>
            ))}
          </div>
        </div>

        {/* Plan + age */}
        <div className="oa-rise oa-rise-2" style={{ ...card, padding: 16, display: 'grid', gap: 14 }}>
          <div>
            <div style={{ fontSize: 13, color: C.inkSoft, fontWeight: 600, marginBottom: 6 }}>Plan</div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {PLANS.map(p => (
                <button key={p.key} onClick={() => setTier(p.key)} className="oa-press"
                  style={{ minHeight: 40, padding: '0 14px', borderRadius: 999, fontSize: 14, fontWeight: 600, cursor: 'pointer',
                    border: `1px solid ${tier === p.key ? C.navy : C.line}`, background: tier === p.key ? C.navy : C.card, color: tier === p.key ? '#fff' : C.ink }}>
                  {p.label}
                </button>
              ))}
            </div>
          </div>
          <label style={{ display: 'grid', gap: 6 }}>
            <span style={{ fontSize: 13, color: C.inkSoft, fontWeight: 600 }}>Age</span>
            <input value={age} onChange={e => setAge(e.target.value.replace(/[^0-9]/g, '').slice(0, 3))} inputMode="numeric" placeholder="e.g. 34"
              style={{ padding: 12, borderRadius: 12, border: `1px solid ${C.line}`, fontSize: 16, color: C.ink }} />
          </label>
        </div>

        {/* Live price */}
        <div className="oa-rise oa-rise-2" style={{ ...card, padding: 18, borderLeft: `4px solid ${C.orange}` }}>
          <div style={{ fontSize: 12, letterSpacing: '0.08em', textTransform: 'uppercase', color: C.inkSoft, fontWeight: 600 }}>Monthly premium</div>
          {!ready && <div style={{ color: C.inkSoft, fontSize: 14, marginTop: 6 }}>Pick a plan and enter an age to see the price.</div>}
          {ready && pricing && <div style={{ marginTop: 8 }}><Loader2 size={22} className="oa-spin" color={C.head} /></div>}
          {ready && !pricing && priceErr && <div role="alert" style={{ color: C.red, fontSize: 14, marginTop: 6 }}>{priceErr}</div>}
          {ready && !pricing && price && (
            <>
              <div style={{ fontFamily: serif, fontSize: 40, fontWeight: 700, color: C.head, lineHeight: 1.1, marginTop: 2 }}>{pula(price.premium_incl)}</div>
              <div style={{ fontSize: 13, color: C.inkSoft, marginTop: 2 }}>{planLabel} · {gender === 'F' ? 'Woman' : 'Man'} · incl. VAT ({pula(price.premium_excl)} + {pula(price.vat)} VAT)</div>
            </>
          )}
        </div>

        {/* Client + send */}
        <div className="oa-rise oa-rise-3" style={{ ...card, padding: 16, display: 'grid', gap: 12 }}>
          <div style={h(15)}>Send the quote</div>
          <input value={client} onChange={e => setClient(e.target.value)} placeholder="Client name"
            style={{ padding: 12, borderRadius: 12, border: `1px solid ${C.line}`, fontSize: 16, color: C.ink }} />
          <input value={email} onChange={e => setEmail(e.target.value)} type="email" inputMode="email" placeholder="Client email"
            style={{ padding: 12, borderRadius: 12, border: `1px solid ${C.line}`, fontSize: 16, color: C.ink }} />
          {err && <div role="alert" style={{ color: C.red, fontSize: 14 }}>{err}</div>}
          <button onClick={send} disabled={!canSend} className="oa-press"
            style={{ minHeight: 52, borderRadius: 14, border: 0, background: canSend ? C.orange : C.line, color: canSend ? C.navy : C.inkSoft, fontWeight: 800, fontSize: 16, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8 }}>
            {busy ? <Loader2 size={20} className="oa-spin" /> : <Mail size={20} />}{busy ? 'Sending…' : 'Generate & email quote'}
          </button>
        </div>
      </section>
    </main>
  )
}

function Header() {
  return (
    <header style={{ background: C.navy, color: '#fff', padding: headerPad, paddingBottom: 44, display: 'flex', alignItems: 'center', gap: 10 }}>
      <Link href="/app/work" aria-label="Back" className="oa-press" style={{ color: '#fff', display: 'grid', placeItems: 'center', width: 36, height: 36, marginLeft: -8 }}><ChevronLeft size={24} /></Link>
      <div>
        <h1 style={{ fontFamily: serif, fontSize: 24, fontWeight: 700, lineHeight: 1, margin: 0 }}>Health cover quote</h1>
        <div style={{ opacity: 0.8, fontSize: 13, marginTop: 4 }}>Price it and email it in seconds</div>
      </div>
    </header>
  )
}
