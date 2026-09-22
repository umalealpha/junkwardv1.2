'use client'

/**
 * /buy-salvage — public-facing salvage parts storefront.
 *
 * No auth required. Backed by:
 *   GET  /api/v1/salvage/public/items/        — catalogue
 *   POST /api/v1/salvage/public/quotes/       — submit an offer
 *
 * The route lives OUTSIDE the (dashboard) group so it isn't wrapped by
 * MsalAuthProvider's signed-in guard. Members of the public hit this
 * page directly from a marketing link or a sticker on the salvage yard.
 */

import { useEffect, useMemo, useState } from 'react'
import Image from 'next/image'
import { Search, Loader2, AlertCircle, CheckCircle2, ChevronLeft } from 'lucide-react'

const API = '/api/v1'   // same-origin via Next rewrites

interface PublicItem {
  id: string
  item_code: string
  part_name: string
  part_description: string
  category_name: string | null
  vehicle_brand_name: string | null
  vehicle_model_name: string | null
  vehicle_year: number | null
  vehicle_colour: string | null
  condition: string
  asking_price: string
  primary_image: string | null
}

const fmt = (n: string | number) =>
  `BWP ${Number(n).toLocaleString('en-BW', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

export default function BuySalvagePage() {
  const [items, setItems] = useState<PublicItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState<string | null>(null)
  const [search, setSearch]   = useState('')
  const [selected, setSelected] = useState<PublicItem | null>(null)

  // offer form
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [phone, setPhone] = useState('')
  const [company, setCompany] = useState('')
  const [offered, setOffered] = useState('')
  const [message, setMessage] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [submitted, setSubmitted] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    fetch(`${API}/salvage/public/items/?max_price=10000000`)
      .then(r => r.ok ? r.json() : Promise.reject(r.statusText))
      .then(j => setItems(j.results || []))
      .catch(e => setError(typeof e === 'string' ? e : 'Failed to load catalogue.'))
      .finally(() => setLoading(false))
  }, [])

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!q) return items
    return items.filter(i =>
      i.part_name.toLowerCase().includes(q)
      || (i.vehicle_brand_name || '').toLowerCase().includes(q)
      || (i.vehicle_model_name || '').toLowerCase().includes(q)
      || (i.category_name || '').toLowerCase().includes(q),
    )
  }, [items, search])

  const submitOffer = async () => {
    if (!selected) return
    if (!name.trim() || !phone.trim() || !offered) {
      alert('Name, phone and offered price are required.'); return
    }
    setSubmitting(true)
    try {
      const res = await fetch(`${API}/salvage/public/quotes/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          item: selected.id, buyer_name: name, buyer_email: email,
          buyer_phone: phone, buyer_company: company,
          offered_price: offered, message,
        }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail || `HTTP ${res.status}`)
      }
      const body = await res.json()
      setSubmitted(body.message || 'Thank you. Your offer has been received.')
      setName(''); setEmail(''); setPhone(''); setCompany(''); setOffered(''); setMessage('')
    } catch (e) {
      alert(`Submission failed: ${e instanceof Error ? e.message : 'unknown'}`)
    } finally { setSubmitting(false) }
  }

  return (
    <div style={{ minHeight: '100vh', background: '#0D1B2A', color: '#fff', fontFamily: 'system-ui, -apple-system, sans-serif' }}>
      {/* Header */}
      <header style={{ borderBottom: '1px solid #1f2a3a', padding: '16px 24px', display: 'flex', alignItems: 'center', gap: 16 }}>
        <Image src="/brand/landing/alpha-text-white.png" alt="Alpha Direct" width={120} height={40} />
        <div style={{ width: 1, height: 24, background: '#334' }} />
        <div>
          <div style={{ fontSize: 16, fontWeight: 700, letterSpacing: 0.3 }}>Motor Liquidators</div>
          <div style={{ fontSize: 11, color: '#94A3B8', letterSpacing: 1 }}>SALVAGE PARTS · GABORONE</div>
        </div>
      </header>

      {/* Hero */}
      {!selected && !submitted && (
        <div style={{ padding: '32px 24px 16px', maxWidth: 1200, margin: '0 auto' }}>
          <h1 style={{ fontSize: 28, fontWeight: 700, margin: 0 }}>
            Genuine salvage parts.
            <span style={{ color: '#F07F00' }}> Direct from Alpha Direct.</span>
          </h1>
          <p style={{ color: '#94A3B8', marginTop: 8, fontSize: 14, maxWidth: 640 }}>
            Submit your best offer on any part below. Our team reviews every offer within
            one business day and contacts you on the phone or email you provide.
          </p>
          <div style={{ marginTop: 16, position: 'relative', maxWidth: 480 }}>
            <Search size={14} style={{ position: 'absolute', left: 10, top: 12, color: '#94A3B8' }} />
            <input value={search} onChange={e => setSearch(e.target.value)}
              placeholder="Search by part, brand or model…"
              style={{ width: '100%', padding: '10px 12px 10px 32px', background: '#152033', border: '1px solid #1f2a3a', color: '#fff', borderRadius: 8, fontSize: 14 }} />
          </div>
        </div>
      )}

      {/* Body */}
      <div style={{ padding: '16px 24px 80px', maxWidth: 1200, margin: '0 auto' }}>
        {submitted ? (
          <SuccessCard message={submitted} onBack={() => { setSubmitted(null); setSelected(null) }} />
        ) : selected ? (
          <DetailView
            item={selected} onBack={() => setSelected(null)}
            name={name} setName={setName} email={email} setEmail={setEmail}
            phone={phone} setPhone={setPhone} company={company} setCompany={setCompany}
            offered={offered} setOffered={setOffered} message={message} setMessage={setMessage}
            submitting={submitting} submit={submitOffer}
          />
        ) : error ? (
          <div style={{ background: '#3a1014', color: '#fda4af', padding: 16, borderRadius: 8, display: 'flex', alignItems: 'center', gap: 8 }}>
            <AlertCircle size={16} /> {error}
          </div>
        ) : loading ? (
          <div style={{ padding: 64, textAlign: 'center', color: '#94A3B8' }}><Loader2 className="animate-spin" /></div>
        ) : (
          <Grid items={filtered} onPick={setSelected} />
        )}
      </div>

      <footer style={{ borderTop: '1px solid #1f2a3a', padding: '16px 24px', textAlign: 'center', color: '#64748b', fontSize: 12 }}>
        © Alpha Direct Insurance · Motor Liquidators · Gaborone, Botswana
      </footer>
    </div>
  )
}

function Grid({ items, onPick }: { items: PublicItem[]; onPick: (i: PublicItem) => void }) {
  if (items.length === 0) {
    return <div style={{ padding: 64, textAlign: 'center', color: '#94A3B8' }}>No parts match your search.</div>
  }
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 16 }}>
      {items.map(i => (
        <button key={i.id} onClick={() => onPick(i)}
          style={{ textAlign: 'left', background: '#152033', border: '1px solid #1f2a3a', borderRadius: 10, padding: 0, cursor: 'pointer', color: '#fff', overflow: 'hidden' }}>
          <div style={{ height: 160, background: '#0D1B2A', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#334' }}>
            {/* `unoptimized` is required, not cosmetic: primary_image is now an
                ABSOLUTE url on omni.alphadirect.co.bw, and next.config.ts has no
                `images` block, so next/image routes a remote src through
                /_next/image and gets a 400 for an unconfigured host — every tile
                on this public page would stay broken. (Fable, 16-Sep-2026.) */}
            {i.primary_image ? <Image src={i.primary_image} alt={i.part_name} width={260} height={160} unoptimized style={{ objectFit: 'cover' }} /> : 'No photo'}
          </div>
          <div style={{ padding: 12 }}>
            <div style={{ fontSize: 11, color: '#F07F00', letterSpacing: 1, textTransform: 'uppercase' }}>{i.category_name || 'Part'}</div>
            <div style={{ fontWeight: 600, marginTop: 4 }}>{i.part_name}</div>
            <div style={{ color: '#94A3B8', fontSize: 12, marginTop: 2 }}>
              {i.vehicle_brand_name} {i.vehicle_model_name} {i.vehicle_year ? `· ${i.vehicle_year}` : ''}
            </div>
            <div style={{ marginTop: 8, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontSize: 11, color: '#94A3B8', textTransform: 'capitalize' }}>condition: {i.condition}</span>
              <span style={{ fontWeight: 700, fontSize: 14, color: '#F07F00' }}>{fmt(i.asking_price)}</span>
            </div>
          </div>
        </button>
      ))}
    </div>
  )
}

function DetailView(props: {
  item: PublicItem; onBack: () => void;
  name: string; setName: (v: string) => void;
  email: string; setEmail: (v: string) => void;
  phone: string; setPhone: (v: string) => void;
  company: string; setCompany: (v: string) => void;
  offered: string; setOffered: (v: string) => void;
  message: string; setMessage: (v: string) => void;
  submitting: boolean; submit: () => void;
}) {
  const { item: i } = props
  return (
    <div>
      <button onClick={props.onBack}
        style={{ display: 'inline-flex', alignItems: 'center', gap: 4, background: 'transparent', color: '#94A3B8', border: 'none', cursor: 'pointer', marginBottom: 16, fontSize: 13 }}>
        <ChevronLeft size={14} /> Back to all parts
      </button>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 24 }}>
        <div style={{ background: '#152033', borderRadius: 10, padding: 24 }}>
          {i.primary_image
            ? <Image src={i.primary_image} alt={i.part_name} width={500} height={400} unoptimized style={{ objectFit: 'cover', borderRadius: 8 }} />
            : <div style={{ height: 300, background: '#0D1B2A', borderRadius: 8, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#334' }}>No photo</div>}
          <h2 style={{ marginTop: 16, fontSize: 22 }}>{i.part_name}</h2>
          <div style={{ color: '#94A3B8', fontSize: 14 }}>{i.vehicle_brand_name} {i.vehicle_model_name} {i.vehicle_year ? `(${i.vehicle_year})` : ''}</div>
          {i.part_description && <p style={{ color: '#cbd5e1', marginTop: 12, fontSize: 14, lineHeight: 1.5 }}>{i.part_description}</p>}
          <dl style={{ marginTop: 16, fontSize: 13 }}>
            <Row k="Item code" v={i.item_code} />
            <Row k="Category" v={i.category_name || '—'} />
            <Row k="Condition" v={i.condition} />
            <Row k="Colour" v={i.vehicle_colour || '—'} />
            <Row k="Asking price" v={fmt(i.asking_price)} highlight />
          </dl>
        </div>
        <div style={{ background: '#152033', borderRadius: 10, padding: 24 }}>
          <h3 style={{ marginTop: 0, fontSize: 18 }}>Submit your offer</h3>
          <p style={{ color: '#94A3B8', fontSize: 13, marginTop: 4 }}>
            We review every offer. You&apos;ll hear back within one business day.
          </p>
          <Field label="Your name *"><input style={inp} value={props.name} onChange={e => props.setName(e.target.value)} /></Field>
          <Field label="Phone *"><input style={inp} value={props.phone} onChange={e => props.setPhone(e.target.value)} placeholder="+267 …" /></Field>
          <Field label="Email"><input style={inp} type="email" value={props.email} onChange={e => props.setEmail(e.target.value)} /></Field>
          <Field label="Company / trading name"><input style={inp} value={props.company} onChange={e => props.setCompany(e.target.value)} /></Field>
          <Field label="Offered price (BWP) *">
            <input style={inp} type="number" min="0" step="0.01" value={props.offered} onChange={e => props.setOffered(e.target.value)} />
          </Field>
          <Field label="Message (optional)">
            <textarea style={{ ...inp, resize: 'vertical', minHeight: 80 }} value={props.message} onChange={e => props.setMessage(e.target.value)} />
          </Field>
          <button onClick={props.submit} disabled={props.submitting}
            style={{ marginTop: 12, padding: '12px 18px', background: '#F07F00', color: '#fff', border: 'none', borderRadius: 8, fontSize: 14, fontWeight: 600, cursor: 'pointer', width: '100%', opacity: props.submitting ? 0.6 : 1 }}>
            {props.submitting ? 'Submitting…' : 'Submit offer'}
          </button>
          <p style={{ color: '#64748b', fontSize: 11, marginTop: 12 }}>
            By submitting an offer you consent to Alpha Direct contacting you about this part.
            Your information is stored securely and used only for this transaction.
          </p>
        </div>
      </div>
    </div>
  )
}

function SuccessCard({ message, onBack }: { message: string; onBack: () => void }) {
  return (
    <div style={{ background: '#152033', borderRadius: 10, padding: 32, maxWidth: 480, margin: '64px auto', textAlign: 'center' }}>
      <CheckCircle2 size={48} color="#16a34a" style={{ margin: '0 auto 16px' }} />
      <h2 style={{ marginTop: 0, fontSize: 20 }}>Offer received</h2>
      <p style={{ color: '#cbd5e1', fontSize: 14 }}>{message}</p>
      <button onClick={onBack}
        style={{ marginTop: 16, padding: '10px 18px', background: '#F07F00', color: '#fff', border: 'none', borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: 'pointer' }}>
        Browse more parts
      </button>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ marginTop: 10 }}>
      <label style={{ display: 'block', fontSize: 11, color: '#94A3B8', marginBottom: 4, textTransform: 'uppercase', letterSpacing: 0.5 }}>{label}</label>
      {children}
    </div>
  )
}

function Row({ k, v, highlight }: { k: string; v: string; highlight?: boolean }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 0', borderBottom: '1px solid #1f2a3a' }}>
      <dt style={{ color: '#94A3B8' }}>{k}</dt>
      <dd style={{ margin: 0, fontWeight: highlight ? 700 : 500, color: highlight ? '#F07F00' : '#fff' }}>{v}</dd>
    </div>
  )
}

const inp: React.CSSProperties = {
  width: '100%', padding: '10px 12px', background: '#0D1B2A', border: '1px solid #1f2a3a',
  color: '#fff', borderRadius: 6, fontSize: 13, boxSizing: 'border-box',
}
