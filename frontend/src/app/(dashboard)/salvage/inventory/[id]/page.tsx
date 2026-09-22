'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import {
  ArrowLeft, AlertCircle, Tag, Hash, Car, MapPin, Package, Building2,
  HandCoins, Check, X, RotateCcw, Plus,
  Camera, ImagePlus, Star, Trash2, Loader2,
  Pencil, Ban, Save,
} from 'lucide-react'
import { TopBar } from '@/components/layout/TopBar'
import { useTheme } from '@/contexts/ThemeContext'
import { apiFetch } from '@/lib/api'

interface SalvageImage {
  id: string
  image: string
  caption: string
  ordering: number
}

interface SalvageItemDetail {
  id: string
  item_code: string
  claim_number: string
  policy_number: string
  part_name: string
  part_description: string
  quantity: number
  category_name?: string | null
  vehicle_brand_name?: string | null
  vehicle_model_name?: string | null
  vehicle_year?: number | null
  vehicle_colour: string
  vin_number: string
  condition: string
  status: string
  asking_price: string
  reserve_price: string
  location: string
  company_code?: string | null
  created_by?: string | null
  posted_at?: string | null
  images: SalvageImage[]
}

export default function SalvageItemDetailPage() {
  const { theme } = useTheme()
  const params = useParams<{ id: string }>()
  const id = params?.id
  const [item, setItem] = useState<SalvageItemDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!id) return
    apiFetch<SalvageItemDetail>(`/salvage-items/${id}/`)
      .then(setItem)
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false))
  }, [id])

  return (
    <div className="min-h-screen" style={{ background: theme.bg }}>
      <TopBar title="Salvage · Item" />

      <div className="p-4 lg:p-6 max-w-[1200px] mx-auto space-y-5">
        <Link href="/salvage/inventory"
              className="inline-flex items-center gap-1.5 text-xs font-semibold"
              style={{ color: theme.orange }}>
          <ArrowLeft className="w-3.5 h-3.5" /> Back to inventory
        </Link>

        {loading && (
          <div className="rounded-xl p-6 text-sm" style={{
            background: theme.card, border: `1px solid ${theme.cardBdr}`, color: theme.t3,
          }}>Loading…</div>
        )}

        {error && (
          <div className="rounded-md p-3 flex items-start gap-2"
               style={{ background: theme.erB, border: `1px solid ${theme.er}30` }}>
            <AlertCircle className="w-4 h-4 mt-0.5" style={{ color: theme.er }} />
            <div>
              <p className="text-sm font-semibold" style={{ color: theme.er }}>
                Couldn&apos;t load this item
              </p>
              <p className="text-xs mt-0.5" style={{ color: theme.er, opacity: 0.7 }}>{error}</p>
            </div>
          </div>
        )}

        {item && (
          <>
            {/* Header card */}
            <div className="rounded-2xl p-6 lg:p-8 relative overflow-hidden"
                 style={{ background: theme.card, border: `1px solid ${theme.cardBdr}`, boxShadow: theme.cardSh }}>
              <div className="absolute -top-24 -right-20 w-80 h-80 rounded-full pointer-events-none"
                   style={{
                     background: `radial-gradient(circle at 30% 30%, rgba(240,127,0,0.10) 0%, transparent 60%)`,
                     filter: 'blur(30px)',
                   }} />
              <div className="relative">
                <div className="flex items-center gap-2 mb-2">
                  <span className="font-mono text-xs px-2 py-0.5 rounded"
                        style={{ background: theme.g100, color: theme.t2 }}>
                    {item.item_code}
                  </span>
                  <StatusPill theme={theme} status={item.status} />
                  <span className="text-[10px] uppercase tracking-wider font-semibold px-2 py-0.5 rounded-full"
                        style={{ background: theme.g100, color: theme.t2 }}>
                    {item.condition}
                  </span>
                </div>
                <h1 className="font-display-tight text-3xl lg:text-4xl font-bold mb-2"
                    style={{ color: theme.navy }}>
                  {item.part_name}
                </h1>
                {item.part_description && (
                  <p className="font-display text-sm italic max-w-2xl" style={{ color: theme.t2 }}>
                    {item.part_description}
                  </p>
                )}

                <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mt-6 pt-6 border-t"
                     style={{ borderColor: theme.cardBdr }}>
                  <Price label="Asking" theme={theme} value={item.asking_price} accent />
                  <Price label="Reserve" theme={theme} value={item.reserve_price} />
                  <div>
                    <div className="text-[10px] uppercase tracking-wider font-semibold mb-1"
                         style={{ color: theme.t3 }}>Quantity</div>
                    <div className="font-display-tight text-2xl font-bold tabular-nums"
                         style={{ color: theme.navy }}>{item.quantity}</div>
                  </div>
                  <div>
                    <div className="text-[10px] uppercase tracking-wider font-semibold mb-1"
                         style={{ color: theme.t3 }}>Location</div>
                    <div className="font-display-tight text-base font-bold truncate"
                         style={{ color: theme.navy }}>
                      {item.location || '—'}
                    </div>
                  </div>
                </div>
              </div>
            </div>

            {/* Detail grid */}
            <div className="grid lg:grid-cols-2 gap-4">
              <DetailCard theme={theme} icon={Car} title="Vehicle">
                <Row theme={theme} label="Brand"  value={item.vehicle_brand_name || '—'} />
                <Row theme={theme} label="Model"  value={item.vehicle_model_name || '—'} />
                <Row theme={theme} label="Year"   value={item.vehicle_year?.toString() || '—'} />
                <Row theme={theme} label="Colour" value={item.vehicle_colour || '—'} />
                <Row theme={theme} label="VIN"    value={item.vin_number || '—'} mono />
              </DetailCard>

              <DetailCard theme={theme} icon={Tag} title="Classification">
                <Row theme={theme} label="Category" value={item.category_name || '—'} />
                <Row theme={theme} label="Status"   value={item.status.replaceAll('_', ' ')} />
                <Row theme={theme} label="Condition" value={item.condition} />
                <Row theme={theme} label="Company"  value={item.company_code || '—'} />
                <Row theme={theme} label="Posted"   value={item.posted_at ? new Date(item.posted_at).toLocaleDateString('en-BW') : '—'} />
              </DetailCard>

              <DetailCard theme={theme} icon={Hash} title="Claim Reference">
                <Row theme={theme} label="Claim number"  value={item.claim_number  || '—'} mono />
                <Row theme={theme} label="Policy number" value={item.policy_number || '—'} mono />
              </DetailCard>

              <DetailCard theme={theme} icon={Package} title="Stock">
                <Row theme={theme} label="Item code" value={item.item_code} mono />
                <Row theme={theme} label="Quantity"  value={String(item.quantity)} />
                <Row theme={theme} label="Location"  value={item.location || '—'} />
              </DetailCard>
            </div>

            {/* Edit + Void — Kgosi Seboko, 18-Sep-2026: "delete & edit feature
                to fix mistakes". Edit rewrites the whitelisted fields; Void
                marks the row cancelled and posts a reversing JE. Both are
                hidden once the row is terminal (sold/disposed/scrapped/voided)
                so nobody can silently rewrite already-booked history. */}
            <EditVoidPanel theme={theme} item={item}
                           onChanged={fresh => setItem(fresh)} />

            {/* Photos — Bharath Balasubramanian, 16-Sep-2026: "We must be able
                to attach photos to each salvage on our salvage management in
                OMNI." The gallery itself has been here since Phase 1, but the
                card rendered ONLY when photos already existed and nothing could
                ever put one there, so on a live yard of 52 items it had never
                appeared once. It is now always on screen, with the upload in
                it. */}
            <PhotosCard theme={theme} itemId={item.id} images={item.images || []}
                        onChange={imgs => setItem({ ...item, images: imgs })} />

            {/* Offers (buyer quotes) — bug 090f1a2e (Kagiso Seboko, 17-Sep) */}
            <OffersPanel theme={theme} itemId={item.id} />
          </>
        )}
      </div>
    </div>
  )
}

/**
 * The photo panel. Upload, make-main, remove.
 *
 * Every call returns the item's whole ordered photo list, so the panel never
 * has to refetch the item or guess at the new order — it renders what the
 * server just told it. "Main" is simply the photo at ordering 0, which is the
 * one primary_image serves to the inventory table.
 */
function PhotosCard({
  theme, itemId, images, onChange,
}: {
  theme: any
  itemId: string
  images: SalvageImage[]
  onChange: (imgs: SalvageImage[]) => void
}) {
  const fileRef = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [caption, setCaption] = useState('')
  const [dragging, setDragging] = useState(false)

  const MAX = 20

  async function send(path: string, init: RequestInit, label: string) {
    setErr(null)
    setBusy(label)
    try {
      const res = await apiFetch<{ count: number; images: SalvageImage[] }>(path, init)
      onChange(res.images)
      return true
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'That did not work — please try again.')
      return false
    } finally {
      setBusy(null)
    }
  }

  async function upload(files: FileList | File[]) {
    const list = Array.from(files)
    if (!list.length) return
    if (images.length + list.length > MAX) {
      setErr(`${MAX} photos is the limit for one item — this one already has ${images.length}.`)
      return
    }
    const form = new FormData()
    for (const f of list) form.append('images', f)
    if (caption.trim()) form.append('caption', caption.trim())
    const ok = await send(`/salvage-items/${itemId}/images/`,
                          { method: 'POST', body: form }, 'upload')
    if (ok) {
      setCaption('')
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const full = images.length >= MAX

  return (
    <div className="rounded-xl p-5"
         style={{ background: theme.card, border: `1px solid ${theme.cardBdr}`, boxShadow: theme.cardSh }}>
      <div className="flex items-center justify-between gap-3 mb-1">
        <div className="flex items-center gap-2">
          <Camera className="w-4 h-4" style={{ color: theme.orange }} strokeWidth={2} />
          <h3 className="font-display text-lg font-bold" style={{ color: theme.navy }}>
            Photos
          </h3>
          <span className="text-[11px] tabular-nums" style={{ color: theme.t3 }}>
            {images.length} of {MAX}
          </span>
        </div>
        <button type="button" disabled={full || busy === 'upload'}
                onClick={() => fileRef.current?.click()}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold
                           transition-transform duration-150 active:scale-[0.97] disabled:opacity-40
                           disabled:cursor-not-allowed"
                style={{ background: theme.orange, color: '#fff' }}>
          {busy === 'upload'
            ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
            : <ImagePlus className="w-3.5 h-3.5" />}
          {busy === 'upload' ? 'Adding…' : 'Add photos'}
        </button>
      </div>
      <p className="text-xs mb-4" style={{ color: theme.t3 }}>
        JPG, PNG or WEBP · up to 10 MB each. The first photo is the one
        shown in the inventory list.
      </p>

      <input ref={fileRef} type="file" multiple accept="image/*" className="hidden"
             onChange={e => e.target.files && upload(e.target.files)} />

      {err && (
        <div className="rounded-md p-2.5 mb-4 flex items-start gap-2"
             style={{ background: theme.erB, border: `1px solid ${theme.er}30` }}>
          <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" style={{ color: theme.er }} />
          <p className="text-xs" style={{ color: theme.er }}>{err}</p>
        </div>
      )}

      <input value={caption} onChange={e => setCaption(e.target.value)}
             maxLength={200}
             placeholder="Caption for the next photos you add (optional)"
             className="w-full rounded-lg px-3 py-2 text-sm mb-4 outline-none"
             style={{ background: theme.g100, color: theme.text,
                      border: `1px solid ${theme.cardBdr}` }} />

      {images.length === 0 ? (
        <div onDragOver={e => { e.preventDefault(); setDragging(true) }}
             onDragLeave={() => setDragging(false)}
             onDrop={e => { e.preventDefault(); setDragging(false); upload(e.dataTransfer.files) }}
             onClick={() => fileRef.current?.click()}
             className="rounded-xl p-8 text-center cursor-pointer transition-colors duration-150"
             style={{
               border: `1.5px dashed ${dragging ? theme.orange : theme.cardBdr}`,
               background: dragging ? `${theme.orange}0D` : 'transparent',
             }}>
          <Camera className="w-6 h-6 mx-auto mb-2" style={{ color: theme.t3 }} strokeWidth={1.5} />
          <p className="text-sm font-semibold" style={{ color: theme.t2 }}>
            No photos on this salvage yet
          </p>
          <p className="text-xs mt-1" style={{ color: theme.t3 }}>
            Drop them here, or click to choose from this computer or phone
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          {images.map((img, i) => (
            <div key={img.id} className="group relative rounded-lg overflow-hidden border"
                 style={{ borderColor: i === 0 ? theme.orange : theme.cardBdr }}>
              <a href={img.image} target="_blank" rel="noopener" className="block">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={img.image} alt={img.caption || 'salvage'}
                     className="w-full h-32 object-cover" />
              </a>
              {i === 0 && (
                <span className="absolute top-1.5 left-1.5 inline-flex items-center gap-1 px-1.5 py-0.5
                                 rounded-full text-[9px] font-bold uppercase tracking-wider"
                      style={{ background: theme.orange, color: '#fff' }}>
                  <Star className="w-2.5 h-2.5" strokeWidth={3} /> Main
                </span>
              )}
              <div className="absolute inset-x-0 bottom-0 flex opacity-0 group-hover:opacity-100
                              focus-within:opacity-100 transition-opacity duration-150">
                {i !== 0 && (
                  <button type="button" disabled={!!busy}
                          onClick={() => send(`/salvage-items/${itemId}/images/${img.id}/primary/`,
                                              { method: 'POST' }, img.id)}
                          className="flex-1 py-1.5 text-[10px] font-semibold uppercase tracking-wider
                                     disabled:opacity-50"
                          style={{ background: 'rgba(29,50,112,0.92)', color: '#fff' }}>
                    Make main
                  </button>
                )}
                <button type="button" disabled={!!busy}
                        onClick={() => send(`/salvage-items/${itemId}/images/${img.id}/`,
                                            { method: 'DELETE' }, img.id)}
                        aria-label="Remove this photo"
                        className="px-3 py-1.5 disabled:opacity-50"
                        style={{ background: 'rgba(185,28,28,0.92)', color: '#fff' }}>
                  {busy === img.id
                    ? <Loader2 className="w-3 h-3 animate-spin" />
                    : <Trash2 className="w-3 h-3" />}
                </button>
              </div>
              {img.caption && (
                <div className="px-2 py-1 text-[11px] truncate" style={{ color: theme.t2 }}>
                  {img.caption}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function Price({
  theme, label, value, accent,
}: { theme: any; label: string; value: string; accent?: boolean }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider font-semibold mb-1"
           style={{ color: theme.t3 }}>{label}</div>
      <div className="font-display-tight text-2xl font-bold tabular-nums"
           style={{ color: accent ? theme.orange : theme.navy }}>
        BWP {Number(value || 0).toLocaleString('en-BW')}
      </div>
    </div>
  )
}

function DetailCard({
  theme, icon: Icon, title, children,
}: { theme: any; icon: any; title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl p-5"
         style={{ background: theme.card, border: `1px solid ${theme.cardBdr}`, boxShadow: theme.cardSh }}>
      <div className="flex items-center gap-2 mb-3">
        <Icon className="w-4 h-4" style={{ color: theme.orange }} strokeWidth={2} />
        <h3 className="font-display text-base font-bold" style={{ color: theme.navy }}>
          {title}
        </h3>
      </div>
      <div className="space-y-2 text-sm">
        {children}
      </div>
    </div>
  )
}

function Row({
  theme, label, value, mono,
}: { theme: any; label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <span className="text-xs" style={{ color: theme.t3 }}>{label}</span>
      <span className={mono ? 'font-mono text-xs' : 'text-sm'}
            style={{ color: theme.text }}>
        {value}
      </span>
    </div>
  )
}

interface BuyerQuote {
  id: string
  item: string
  buyer_name: string
  buyer_phone: string
  buyer_email: string
  buyer_company: string
  offered_price: string
  counter_price: string | null
  message: string
  status: 'pending' | 'under_review' | 'accepted' | 'rejected' | 'countered'
  review_notes: string
  reviewed_at: string | null
  created_at: string
}

function OffersPanel({ theme, itemId }: { theme: any; itemId: string }) {
  const [quotes, setQuotes]     = useState<BuyerQuote[]>([])
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState<string | null>(null)
  const [showForm, setShowForm] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    apiFetch<{ results?: BuyerQuote[] } | BuyerQuote[]>(
      `/salvage/buyer-quotes/?item=${itemId}&page_size=100`
    )
      .then(r => {
        const rows = Array.isArray(r) ? r : (r.results || [])
        rows.sort((a, b) => Number(b.offered_price) - Number(a.offered_price))
        setQuotes(rows)
      })
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load offers'))
      .finally(() => setLoading(false))
  }, [itemId])

  useEffect(() => { load() }, [load])

  return (
    <div className="rounded-xl p-5"
         style={{ background: theme.card, border: `1px solid ${theme.cardBdr}`, boxShadow: theme.cardSh }}>
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <HandCoins className="w-4 h-4" style={{ color: theme.orange }} strokeWidth={2} />
          <h3 className="font-display text-lg font-bold" style={{ color: theme.navy }}>
            Offers
          </h3>
          <span className="text-xs px-2 py-0.5 rounded-full"
                style={{ background: theme.g100, color: theme.t2 }}>
            {quotes.length}
          </span>
        </div>
        <button onClick={() => setShowForm(v => !v)}
                className="inline-flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-md"
                style={{ background: theme.orange, color: '#fff' }}>
          <Plus className="w-3.5 h-3.5" /> {showForm ? 'Close' : 'Add offer'}
        </button>
      </div>

      {showForm && (
        <AddOfferForm theme={theme} itemId={itemId}
                      onSaved={() => { setShowForm(false); load() }} />
      )}

      {loading && <div className="text-xs" style={{ color: theme.t3 }}>Loading offers…</div>}

      {error && (
        <div className="rounded-md p-3 flex items-start gap-2 mb-3"
             style={{ background: theme.erB, border: `1px solid ${theme.er}30` }}>
          <AlertCircle className="w-4 h-4 mt-0.5" style={{ color: theme.er }} />
          <p className="text-xs" style={{ color: theme.er }}>{error}</p>
        </div>
      )}

      {!loading && !error && quotes.length === 0 && (
        <p className="text-xs italic" style={{ color: theme.t3 }}>
          No offers yet. Click <b>Add offer</b> to record one.
        </p>
      )}

      {quotes.length > 0 && (
        <div className="space-y-2">
          {quotes.map(q => (
            <OfferRow key={q.id} theme={theme} q={q} onChanged={load} />
          ))}
        </div>
      )}
    </div>
  )
}

function AddOfferForm({
  theme, itemId, onSaved,
}: { theme: any; itemId: string; onSaved: () => void }) {
  const [buyerName, setBuyerName]   = useState('')
  const [buyerPhone, setBuyerPhone] = useState('')
  const [buyerEmail, setBuyerEmail] = useState('')
  const [price, setPrice]           = useState('')
  const [message, setMessage]       = useState('')
  const [saving, setSaving]         = useState(false)
  const [err, setErr]               = useState<string | null>(null)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setErr(null)
    if (!buyerName.trim() || !buyerPhone.trim() || !price) {
      setErr('Buyer name, phone and price are required.'); return
    }
    setSaving(true)
    try {
      await apiFetch(`/salvage/buyer-quotes/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          item: itemId,
          buyer_name: buyerName.trim(),
          buyer_phone: buyerPhone.trim(),
          buyer_email: buyerEmail.trim(),
          offered_price: price,
          message: message.trim(),
        }),
      })
      onSaved()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to save offer')
    } finally {
      setSaving(false)
    }
  }

  const inputStyle = {
    background: '#fff', border: `1px solid ${theme.cardBdr}`, color: theme.text,
  } as React.CSSProperties

  return (
    <form onSubmit={submit} className="rounded-lg p-4 mb-4 grid grid-cols-1 md:grid-cols-2 gap-3"
          style={{ background: theme.g100, border: `1px solid ${theme.cardBdr}` }}>
      <label className="text-xs" style={{ color: theme.t2 }}>
        Buyer name *
        <input value={buyerName} onChange={e => setBuyerName(e.target.value)}
               className="mt-1 w-full text-sm px-2 py-1.5 rounded" style={inputStyle} />
      </label>
      <label className="text-xs" style={{ color: theme.t2 }}>
        Phone *
        <input value={buyerPhone} onChange={e => setBuyerPhone(e.target.value)}
               className="mt-1 w-full text-sm px-2 py-1.5 rounded" style={inputStyle} />
      </label>
      <label className="text-xs" style={{ color: theme.t2 }}>
        Email (optional)
        <input type="email" value={buyerEmail} onChange={e => setBuyerEmail(e.target.value)}
               className="mt-1 w-full text-sm px-2 py-1.5 rounded" style={inputStyle} />
      </label>
      <label className="text-xs" style={{ color: theme.t2 }}>
        Price offered (BWP) *
        <input type="number" step="0.01" min="0" value={price}
               onChange={e => setPrice(e.target.value)}
               className="mt-1 w-full text-sm px-2 py-1.5 rounded tabular-nums" style={inputStyle} />
      </label>
      <label className="text-xs md:col-span-2" style={{ color: theme.t2 }}>
        Notes (optional)
        <textarea value={message} onChange={e => setMessage(e.target.value)}
                  rows={2}
                  className="mt-1 w-full text-sm px-2 py-1.5 rounded" style={inputStyle} />
      </label>
      {err && (
        <div className="md:col-span-2 text-xs" style={{ color: theme.er }}>{err}</div>
      )}
      <div className="md:col-span-2 flex items-center gap-2">
        <button type="submit" disabled={saving}
                className="text-xs font-semibold px-3 py-1.5 rounded-md disabled:opacity-50"
                style={{ background: theme.navy, color: '#fff' }}>
          {saving ? 'Saving…' : 'Save offer'}
        </button>
        <p className="text-[11px] italic" style={{ color: theme.t3 }}>
          To attach photos, upload them to this salvage item&apos;s Photos section above.
        </p>
      </div>
    </form>
  )
}

function OfferRow({
  theme, q, onChanged,
}: { theme: any; q: BuyerQuote; onChanged: () => void }) {
  const [busy, setBusy]   = useState(false)
  const [err,  setErr]    = useState<string | null>(null)
  const [countering, setCountering] = useState(false)
  const [counterPrice, setCounterPrice] = useState('')

  async function decide(decision: 'accepted' | 'rejected' | 'countered') {
    setErr(null)
    if (decision === 'countered' && !counterPrice) {
      setCountering(true); return
    }
    setBusy(true)
    try {
      const body: Record<string, unknown> = { decision }
      if (decision === 'countered') body.counter_price = counterPrice
      await apiFetch(`/salvage/buyer-quotes/${q.id}/review/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      setCountering(false)
      setCounterPrice('')
      onChanged()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed')
    } finally {
      setBusy(false)
    }
  }

  const badge: Record<BuyerQuote['status'], { bg: string; fg: string; label: string }> = {
    pending:      { bg: '#FFFBEB', fg: '#D97706', label: 'Pending review' },
    under_review: { bg: '#EFF6FF', fg: '#1D4ED8', label: 'Under review' },
    accepted:     { bg: '#ECFDF5', fg: '#059669', label: 'Accepted' },
    rejected:     { bg: '#FEF2F2', fg: '#DC2626', label: 'Rejected' },
    countered:    { bg: '#F5F3FF', fg: '#7C3AED', label: 'Countered' },
  }
  const b = badge[q.status]
  const canDecide = q.status !== 'accepted' && q.status !== 'rejected'

  return (
    <div className="rounded-lg p-3"
         style={{ background: '#fff', border: `1px solid ${theme.cardBdr}` }}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-semibold text-sm" style={{ color: theme.navy }}>
              {q.buyer_name}
            </span>
            <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase tracking-wider"
                  style={{ background: b.bg, color: b.fg }}>{b.label}</span>
          </div>
          <div className="text-xs mt-0.5" style={{ color: theme.t2 }}>
            {q.buyer_phone}
            {q.buyer_email && <> · {q.buyer_email}</>}
            {q.buyer_company && <> · {q.buyer_company}</>}
          </div>
          {q.message && (
            <div className="text-xs italic mt-1" style={{ color: theme.t3 }}>
              &ldquo;{q.message}&rdquo;
            </div>
          )}
          <div className="text-[11px] mt-1" style={{ color: theme.t3 }}>
            Submitted {new Date(q.created_at).toLocaleString('en-BW')}
          </div>
        </div>
        <div className="text-right shrink-0">
          <div className="font-display-tight text-xl font-bold tabular-nums"
               style={{ color: theme.orange }}>
            BWP {Number(q.offered_price || 0).toLocaleString('en-BW', {
              minimumFractionDigits: 2, maximumFractionDigits: 2,
            })}
          </div>
          {q.counter_price && (
            <div className="text-[11px] tabular-nums" style={{ color: theme.t2 }}>
              Counter: BWP {Number(q.counter_price).toLocaleString('en-BW', {
                minimumFractionDigits: 2, maximumFractionDigits: 2,
              })}
            </div>
          )}
        </div>
      </div>

      {countering && (
        <div className="mt-2 flex items-center gap-2">
          <input type="number" step="0.01" min="0" value={counterPrice}
                 onChange={e => setCounterPrice(e.target.value)}
                 placeholder="Counter price (BWP)"
                 className="text-xs px-2 py-1 rounded flex-1"
                 style={{ background: '#fff', border: `1px solid ${theme.cardBdr}`, color: theme.text }} />
          <button onClick={() => decide('countered')} disabled={busy || !counterPrice}
                  className="text-[11px] font-semibold px-2 py-1 rounded disabled:opacity-50"
                  style={{ background: '#7C3AED', color: '#fff' }}>
            Send counter
          </button>
          <button onClick={() => { setCountering(false); setCounterPrice('') }}
                  className="text-[11px] px-2 py-1" style={{ color: theme.t3 }}>
            Cancel
          </button>
        </div>
      )}

      {canDecide && !countering && (
        <div className="mt-2 flex items-center gap-2">
          <button onClick={() => decide('accepted')} disabled={busy}
                  className="inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-1 rounded disabled:opacity-50"
                  style={{ background: '#ECFDF5', color: '#059669', border: '1px solid #A7F3D0' }}>
            <Check className="w-3 h-3" /> Accept
          </button>
          <button onClick={() => decide('rejected')} disabled={busy}
                  className="inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-1 rounded disabled:opacity-50"
                  style={{ background: '#FEF2F2', color: '#DC2626', border: '1px solid #FECACA' }}>
            <X className="w-3 h-3" /> Reject
          </button>
          <button onClick={() => setCountering(true)} disabled={busy}
                  className="inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-1 rounded disabled:opacity-50"
                  style={{ background: '#F5F3FF', color: '#7C3AED', border: '1px solid #DDD6FE' }}>
            <RotateCcw className="w-3 h-3" /> Counter
          </button>
          {q.reviewed_at && q.review_notes && (
            <span className="text-[11px] italic" style={{ color: theme.t3 }}>
              Note: {q.review_notes}
            </span>
          )}
        </div>
      )}

      {err && <div className="mt-2 text-[11px]" style={{ color: theme.er }}>{err}</div>}
    </div>
  )
}

function EditVoidPanel({
  theme, item, onChanged,
}: {
  theme: any
  item: SalvageItemDetail
  onChanged: (fresh: SalvageItemDetail) => void
}) {
  const frozen = ['sold', 'disposed', 'scrapped', 'voided'].includes(item.status)
  const [mode, setMode] = useState<'idle' | 'edit' | 'void'>('idle')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [reason, setReason] = useState('')
  const [form, setForm] = useState({
    part_name: item.part_name,
    part_description: item.part_description || '',
    quantity: item.quantity,
    vehicle_year: item.vehicle_year ?? '',
    vehicle_colour: item.vehicle_colour || '',
    vin_number: item.vin_number || '',
    location: item.location || '',
    asking_price: item.asking_price,
    reserve_price: item.reserve_price,
  })

  async function saveEdit() {
    setBusy(true); setErr(null)
    try {
      const body: Record<string, unknown> = { ...form }
      if (body.vehicle_year === '') body.vehicle_year = null
      const fresh = await apiFetch<SalvageItemDetail>(
        `/salvage-items/${item.id}/`,
        { method: 'PATCH', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body) },
      )
      onChanged(fresh); setMode('idle')
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Save failed')
    } finally { setBusy(false) }
  }

  async function doVoid() {
    if (!reason.trim()) { setErr('Please say why this row is being cancelled.'); return }
    setBusy(true); setErr(null)
    try {
      const fresh = await apiFetch<SalvageItemDetail>(
        `/salvage-items/${item.id}/void/`,
        { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ reason: reason.trim() }) },
      )
      onChanged(fresh); setMode('idle'); setReason('')
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Void failed')
    } finally { setBusy(false) }
  }

  if (frozen) return null

  return (
    <div className="rounded-xl p-5"
         style={{ background: theme.card, border: `1px solid ${theme.cardBdr}`, boxShadow: theme.cardSh }}>
      {mode === 'idle' && (
        <div className="flex items-center justify-between gap-3">
          <div>
            <h3 className="font-display text-base font-bold" style={{ color: theme.navy }}>
              Correct a mistake on this row
            </h3>
            <p className="text-xs mt-0.5" style={{ color: theme.t3 }}>
              Edit fixes a typo. Void cancels the row and reverses the intake entry —
              use it when the wrong claim or item was booked in.
            </p>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button type="button" onClick={() => setMode('edit')}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold"
                    style={{ background: theme.g100, color: theme.navy,
                             border: `1px solid ${theme.cardBdr}` }}>
              <Pencil className="w-3.5 h-3.5" /> Edit
            </button>
            <button type="button" onClick={() => setMode('void')}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold"
                    style={{ background: '#FEF2F2', color: '#DC2626',
                             border: '1px solid #FCA5A5' }}>
              <Ban className="w-3.5 h-3.5" /> Void
            </button>
          </div>
        </div>
      )}

      {mode === 'edit' && (
        <div className="space-y-3">
          <h3 className="font-display text-base font-bold" style={{ color: theme.navy }}>
            Fix typos on this row
          </h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <Field label="Part name">
              <input value={form.part_name}
                     onChange={e => setForm({ ...form, part_name: e.target.value })}
                     className="w-full rounded-md px-2 py-1.5 text-sm outline-none"
                     style={{ background: theme.bg, color: theme.text,
                              border: `1px solid ${theme.cardBdr}` }} />
            </Field>
            <Field label="Quantity">
              <input type="number" min={1} value={form.quantity}
                     onChange={e => setForm({ ...form, quantity: Number(e.target.value) })}
                     className="w-full rounded-md px-2 py-1.5 text-sm outline-none tabular-nums"
                     style={{ background: theme.bg, color: theme.text,
                              border: `1px solid ${theme.cardBdr}` }} />
            </Field>
            <Field label="VIN">
              <input value={form.vin_number}
                     onChange={e => setForm({ ...form, vin_number: e.target.value })}
                     className="w-full rounded-md px-2 py-1.5 text-sm font-mono outline-none"
                     style={{ background: theme.bg, color: theme.text,
                              border: `1px solid ${theme.cardBdr}` }} />
            </Field>
            <Field label="Colour">
              <input value={form.vehicle_colour}
                     onChange={e => setForm({ ...form, vehicle_colour: e.target.value })}
                     className="w-full rounded-md px-2 py-1.5 text-sm outline-none"
                     style={{ background: theme.bg, color: theme.text,
                              border: `1px solid ${theme.cardBdr}` }} />
            </Field>
            <Field label="Year">
              <input type="number" value={form.vehicle_year as number | string}
                     onChange={e => setForm({ ...form, vehicle_year: e.target.value })}
                     className="w-full rounded-md px-2 py-1.5 text-sm outline-none tabular-nums"
                     style={{ background: theme.bg, color: theme.text,
                              border: `1px solid ${theme.cardBdr}` }} />
            </Field>
            <Field label="Location">
              <input value={form.location}
                     onChange={e => setForm({ ...form, location: e.target.value })}
                     className="w-full rounded-md px-2 py-1.5 text-sm outline-none"
                     style={{ background: theme.bg, color: theme.text,
                              border: `1px solid ${theme.cardBdr}` }} />
            </Field>
            <Field label="Asking price (BWP)">
              <input value={form.asking_price}
                     onChange={e => setForm({ ...form, asking_price: e.target.value })}
                     className="w-full rounded-md px-2 py-1.5 text-sm outline-none tabular-nums"
                     style={{ background: theme.bg, color: theme.text,
                              border: `1px solid ${theme.cardBdr}` }} />
            </Field>
            <Field label="Reserve price (BWP)">
              <input value={form.reserve_price}
                     onChange={e => setForm({ ...form, reserve_price: e.target.value })}
                     className="w-full rounded-md px-2 py-1.5 text-sm outline-none tabular-nums"
                     style={{ background: theme.bg, color: theme.text,
                              border: `1px solid ${theme.cardBdr}` }} />
            </Field>
          </div>
          <Field label="Description">
            <textarea value={form.part_description} rows={2}
                      onChange={e => setForm({ ...form, part_description: e.target.value })}
                      className="w-full rounded-md px-2 py-1.5 text-sm outline-none"
                      style={{ background: theme.bg, color: theme.text,
                               border: `1px solid ${theme.cardBdr}` }} />
          </Field>
          {err && <p className="text-xs" style={{ color: theme.er }}>{err}</p>}
          <div className="flex items-center justify-end gap-2 pt-1">
            <button type="button" onClick={() => setMode('idle')} disabled={busy}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold"
                    style={{ background: theme.g100, color: theme.t2 }}>
              <X className="w-3.5 h-3.5" /> Cancel
            </button>
            <button type="button" onClick={saveEdit} disabled={busy}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold"
                    style={{ background: theme.orange, color: '#fff' }}>
              {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
              {busy ? 'Saving…' : 'Save changes'}
            </button>
          </div>
        </div>
      )}

      {mode === 'void' && (
        <div className="space-y-3">
          <h3 className="font-display text-base font-bold" style={{ color: '#DC2626' }}>
            Cancel this row
          </h3>
          <p className="text-xs" style={{ color: theme.t2 }}>
            The row stays in the audit log but is marked <b>voided</b> and its
            intake entry is reversed. Reports treat it as gone.
          </p>
          <Field label="Reason (required — everyone will see this)">
            <textarea value={reason} onChange={e => setReason(e.target.value)}
                      rows={2} placeholder="e.g. Duplicate of ML-0007, wrong claim number typed"
                      className="w-full rounded-md px-2 py-1.5 text-sm outline-none"
                      style={{ background: theme.bg, color: theme.text,
                               border: `1px solid ${theme.cardBdr}` }} />
          </Field>
          {err && <p className="text-xs" style={{ color: theme.er }}>{err}</p>}
          <div className="flex items-center justify-end gap-2 pt-1">
            <button type="button" onClick={() => { setMode('idle'); setErr(null) }} disabled={busy}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold"
                    style={{ background: theme.g100, color: theme.t2 }}>
              <X className="w-3.5 h-3.5" /> Keep row
            </button>
            <button type="button" onClick={doVoid} disabled={busy}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold"
                    style={{ background: '#DC2626', color: '#fff' }}>
              {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Ban className="w-3.5 h-3.5" />}
              {busy ? 'Voiding…' : 'Void row'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="block text-[10px] uppercase tracking-wider font-semibold mb-1 opacity-70">
        {label}
      </span>
      {children}
    </label>
  )
}

function StatusPill({ theme, status }: { theme: any; status: string }) {
  const map: Record<string, { bg: string; fg: string }> = {
    available: { bg: '#ECFDF5', fg: '#059669' },
    reserved:  { bg: '#FFFBEB', fg: '#D97706' },
    sold:      { bg: '#F5F3FF', fg: '#7C3AED' },
    on_hold:   { bg: theme.g100, fg: theme.t2 },
    scrapped:  { bg: '#FEF2F2', fg: '#DC2626' },
    voided:    { bg: '#F3F4F6', fg: '#6B7280' },
  }
  const c = map[status] || { bg: theme.g100, fg: theme.t2 }
  return (
    <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase tracking-wider"
          style={{ background: c.bg, color: c.fg }}>
      {status.replaceAll('_', ' ')}
    </span>
  )
}
