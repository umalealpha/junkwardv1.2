'use client'

/** /app/snap — "Snap anything" (CFO 2026-09-03). One camera button. Photo an
 * invoice / receipt / supplier quote / anything → Omni says what it is and opens
 * the right flow with the photo already attached:
 *   POST /snap/classify/   multipart `image` → { kind, confidence, hint, suggested: { route, fields } }
 * The photo + fields are handed to the next screen through sessionStorage
 * (snapHandoff.ts) — nothing sensitive goes in the URL. An identity document is
 * refused by the server (DPA) and this screen shows only its sentence. */
import { useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import { Camera, ClipboardList, FileText, Images, Receipt, RotateCcw } from 'lucide-react'
import { compressImage, reauthOn401 } from '@/app/(customer)/api'
import { C, serif } from '@/app/(customer)/ui'
import { useStaffBase } from './useStaffBase'
import { Card, ScreenFrame, ServerMessage, errText, formatServerErrors, ghostBtn, primaryBtn, rawStaffFetch } from './StaffFormKit'
import { fileToDataUrl, saveSnapHandoff, type SnapKind, type SnapRoute } from './snapHandoff'

interface SnapResult {
  kind: SnapKind
  confidence: number
  hint: string
  suggested: { route: SnapRoute | null; fields?: Record<string, string> }
}
const KINDS: readonly SnapKind[] = ['invoice', 'receipt', 'quote', 'id_document', 'other']
const isKind = (v: unknown): v is SnapKind => typeof v === 'string' && (KINDS as readonly string[]).includes(v)

/** The three flows Snap can open, in the order they are offered when picking by hand. */
const FLOWS: { route: SnapRoute; kind: SnapKind; label: string; sub: string; Icon: typeof FileText }[] = [
  { route: 'raise-payment', kind: 'invoice', label: 'Raise a payment', sub: 'A supplier invoice to be paid', Icon: FileText },
  { route: 'receipt', kind: 'receipt', label: 'Claim a receipt', sub: 'Something you already paid for', Icon: Receipt },
  { route: 'raise-po', kind: 'quote', label: 'Raise a PO', sub: 'A quote before the work is done', Icon: ClipboardList },
]
const KIND_WORD: Record<SnapKind, string> = { invoice: 'invoice', receipt: 'receipt', quote: 'quote', id_document: 'identity document', other: 'document' }

function parseResult(body: Record<string, unknown>): SnapResult | null {
  if (!isKind(body.kind)) return null
  const s = (body.suggested && typeof body.suggested === 'object' ? body.suggested : {}) as { route?: unknown; fields?: unknown }
  const route = s.route === 'raise-payment' || s.route === 'receipt' || s.route === 'raise-po' ? s.route : null
  const fields: Record<string, string> = {}
  if (s.fields && typeof s.fields === 'object') {
    for (const [k, v] of Object.entries(s.fields as Record<string, unknown>)) if (typeof v === 'string') fields[k] = v
  }
  return { kind: body.kind, confidence: typeof body.confidence === 'number' ? body.confidence : 0, hint: typeof body.hint === 'string' ? body.hint : '', suggested: { route, fields } }
}

export default function SnapScreen() {
  const base = useStaffBase()
  const router = useRouter()
  const [busy, setBusy] = useState(false)
  const [photo, setPhoto] = useState<{ file: File; url: string } | null>(null)
  const [result, setResult] = useState<SnapResult | null>(null)
  const [manual, setManual] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const camRef = useRef<HTMLInputElement | null>(null)
  const galleryRef = useRef<HTMLInputElement | null>(null)

  const reset = () => { setResult(null); setManual(false); setError(null); setPhoto(p => { if (p) URL.revokeObjectURL(p.url); return null }) }

  const onPick = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const raw = e.target.files?.[0]
    e.target.value = ''
    if (!raw) return
    reset(); setBusy(true)
    try {
      const f = await compressImage(raw)             // a 3–12 MB camera shot → a few hundred KB
      setPhoto({ file: f, url: URL.createObjectURL(f) })
      const fd = new FormData(); fd.append('image', f)
      const r = await rawStaffFetch('/snap/classify/', { method: 'POST', body: fd })
      if (!r.ok) {
        setError(r.status === 429 ? 'Too many snaps in one minute — give it a moment and try again.' : formatServerErrors(r.body, r.status))
        return
      }
      const parsed = parseResult(r.body)
      if (!parsed) { setError('Omni sent back something this screen did not understand. Pick the flow yourself.'); return }
      setResult(parsed)
    } catch (err) {
      if (!reauthOn401(err)) setError(errText(err, 'Could not send that photo.'))
    } finally { setBusy(false) }
  }

  /** Hand the photo + fields to the chosen flow and go there. */
  const go = async (route: SnapRoute, kind: SnapKind, fields: Record<string, string>) => {
    if (photo) {
      try {
        saveSnapHandoff({ kind, fields, photo: await fileToDataUrl(photo.file), name: photo.file.name, type: photo.file.type, at: Date.now() })
      } catch { /* the flow still opens — the person re-snaps there */ }
    }
    router.push(`${base}/${route}`)
  }

  const flowFor = (route: SnapRoute | null) => FLOWS.find(f => f.route === route)
  const canRoute = result && result.kind !== 'id_document' && result.suggested.route
  const showManual = manual || (result && result.kind !== 'id_document' && !result.suggested.route) || !!error

  return (
    <ScreenFrame title="Snap" base={base}>
      <input ref={camRef} type="file" accept="image/*" capture="environment" style={{ display: 'none' }} onChange={onPick} aria-hidden="true" tabIndex={-1} />
      <input ref={galleryRef} type="file" accept="image/*" style={{ display: 'none' }} onChange={onPick} aria-hidden="true" tabIndex={-1} />

      {!photo && !busy && (
        <>
          <div style={{ textAlign: 'center', padding: '8px 4px 0' }}>
            <h2 style={{ fontFamily: serif, fontWeight: 800, fontSize: 26, color: C.ink, margin: 0 }}>Snap anything</h2>
            <p style={{ color: C.inkSoft, fontSize: 14, lineHeight: 1.55, margin: '8px 0 0' }}>
              An invoice, a till slip, a supplier quote. Omni works out what it is and opens the right flow with the photo already attached.
            </p>
          </div>
          <button onClick={() => camRef.current?.click()} aria-label="Open the camera"
            style={{ width: '100%', minHeight: 240, borderRadius: 24, border: 'none', cursor: 'pointer',
                     background: `linear-gradient(160deg, ${C.navy} 0%, ${C.navy2} 100%)`, color: '#fff',
                     display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 14,
                     boxShadow: '0 18px 40px -22px rgba(15,28,44,0.7)' }}>
            <span style={{ width: 88, height: 88, borderRadius: 999, display: 'grid', placeItems: 'center',
                           background: `linear-gradient(135deg, ${C.orange}, ${C.orangeDeep})`, boxShadow: '0 10px 30px -10px rgba(244,166,35,0.8)' }}>
              <Camera size={40} strokeWidth={2.2} aria-hidden="true" />
            </span>
            <span style={{ fontFamily: serif, fontWeight: 800, fontSize: 22 }}>Take a photo</span>
            <span style={{ fontSize: 13, opacity: 0.8 }}>Invoice · receipt · quote</span>
          </button>
          <button onClick={() => galleryRef.current?.click()} style={{ ...ghostBtn, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8 }}>
            <Images size={16} aria-hidden="true" /> Choose from my photos
          </button>
        </>
      )}

      {busy && (
        <Card style={{ textAlign: 'center', padding: 28 }} >
          <p role="status" style={{ margin: 0, fontFamily: serif, fontWeight: 800, fontSize: 20, color: C.ink }}>Looking at it…</p>
          <p style={{ margin: '6px 0 0', fontSize: 13, color: C.inkSoft }}>A second or two. The photo is not kept.</p>
        </Card>
      )}

      {photo && !busy && (
        <div style={{ display: 'flex', gap: 14, alignItems: 'flex-start' }}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={photo.url} alt="The photo you snapped" style={{ width: 96, height: 96, objectFit: 'cover', borderRadius: 16, border: `1px solid ${C.line}`, flexShrink: 0 }} />
          <div style={{ flex: 1 }}>
            <h2 style={{ fontFamily: serif, fontWeight: 800, fontSize: 22, color: C.ink, margin: 0, lineHeight: 1.2 }}>
              {result?.kind === 'id_document' ? 'Not through Snap' : result && result.kind !== 'other' && !error ? `It's ${result.kind === 'invoice' ? 'an' : 'a'} ${KIND_WORD[result.kind]}` : 'What is it?'}
            </h2>
            {result?.hint && !error && <p style={{ margin: '6px 0 0', fontSize: 14, color: C.inkSoft, lineHeight: 1.5 }}>{result.hint}</p>}
          </div>
        </div>
      )}

      {error && <ServerMessage text={error} tone="error" />}

      {/* An identity document: the sentence, and nothing else (DPA). */}
      {result?.kind === 'id_document' && !busy && (
        <button onClick={reset} style={{ ...primaryBtn(false) }}><RotateCcw size={16} aria-hidden="true" /> Snap something else</button>
      )}

      {canRoute && !manual && !busy && result && (() => {
        const flow = flowFor(result.suggested.route)
        if (!flow) return null
        const fields = result.suggested.fields ?? {}
        const shown = (['vendor', 'total', 'currency', 'date', 'reference'] as const).filter(k => fields[k])
        return (
          <>
            {shown.length > 0 && (
              <Card style={{ padding: 14 }}>
                <dl style={{ margin: 0, display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '6px 14px', fontSize: 13.5 }}>
                  {shown.map(k => (
                    <div key={k} style={{ display: 'contents' }}>
                      <dt style={{ color: C.inkSoft, fontWeight: 700, textTransform: 'capitalize' }}>{k}</dt>
                      <dd style={{ margin: 0, color: C.ink, fontVariantNumeric: 'tabular-nums', overflowWrap: 'anywhere' }}>{fields[k]}</dd>
                    </div>
                  ))}
                </dl>
                <p style={{ margin: '10px 0 0', fontSize: 11.5, color: C.inkSoft }}>What Omni read — you check and correct it on the next screen.</p>
              </Card>
            )}
            <button onClick={() => go(flow.route, result.kind, fields)} style={{ ...primaryBtn(false), minHeight: 56, fontSize: 16 }}>
              <flow.Icon size={18} aria-hidden="true" /> Continue as {KIND_WORD[result.kind]} → {flow.label}
            </button>
            <button onClick={() => setManual(true)} style={ghostBtn}>Not right — pick manually</button>
          </>
        )
      })()}

      {showManual && !busy && result?.kind !== 'id_document' && (
        <>
          <p style={{ margin: 0, fontSize: 13, color: C.inkSoft, textAlign: 'center' }}>{photo ? 'Which flow should this photo open?' : 'Or open a flow directly'}</p>
          {FLOWS.map(f => (
            <button key={f.route} onClick={() => go(f.route, f.kind, result?.suggested.fields ?? {})}
              style={{ ...ghostBtn, minHeight: 60, display: 'flex', alignItems: 'center', gap: 12, textAlign: 'left', padding: '10px 16px' }}>
              <span style={{ width: 40, height: 40, borderRadius: 12, display: 'grid', placeItems: 'center', background: '#FFF7ED', color: C.orangeDeep, flexShrink: 0 }}><f.Icon size={18} aria-hidden="true" /></span>
              <span><b style={{ display: 'block', fontSize: 14, color: C.ink }}>{f.label}</b><span style={{ fontSize: 12, fontWeight: 500, color: C.inkSoft }}>{f.sub}</span></span>
            </button>
          ))}
          {photo && <button onClick={reset} style={{ ...ghostBtn, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8 }}><RotateCcw size={14} aria-hidden="true" /> Snap again</button>}
        </>
      )}

      <p style={{ margin: 0, fontSize: 11.5, color: C.inkSoft, textAlign: 'center' }}>Omni looks at the photo once and keeps nothing. Identity documents are not accepted.</p>
    </ScreenFrame>
  )
}
