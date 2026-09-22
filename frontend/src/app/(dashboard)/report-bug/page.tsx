'use client'

/**
 * /report-bug — OMNI Obsidian "Something not right?" experience.
 *
 * Two steps, one route, one endpoint:
 *   step 'choose' → three 3D-tilt glass cards (Bug / Login Issue / New Feature)
 *   step 'form'   → the existing premium form (reused validation + submit)
 *
 * The submission path is UNCHANGED: submitBugReport(description, files, pageUrl),
 * MAX_SHOTS/type/byte checks identical. The backend has no category field, so an
 * intent simply prefixes the description with a tag — no payload-shape change.
 *
 * CFO 2026-07-31: added the "Ask for a New Feature" intent so the staff auto-reply
 * has a real second door (`/report-bug?intent=feature` deep-links straight to it).
 * A feature request needs 25 words and NO screenshots — there is nothing broken to
 * photograph, and demanding two shots pushed people back to emailing the CFO.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import { getToken, submitBugReport } from '@/lib/api'
import { sendFailureMessage } from './sendFailure'
import {
  Upload, X, CheckCircle2, AlertCircle, Image as ImageIcon, Send,
  ArrowRight, ArrowLeft, LifeBuoy, Lightbulb, FileText,
} from 'lucide-react'

const MIN_WORDS = 50
const MIN_SHOTS = 2   // CFO 2026-06-10: was 3 — lowered to 2
const MAX_SHOTS = 10
const MAX_BYTES = 10 * 1024 * 1024
const OK_TYPES  = ['image/png', 'image/jpeg', 'image/jpg', 'image/webp', 'image/gif']
// CFO 2026-09-08 (asked for on the feature page by Oratile Tlhomelang): a request
// is often already written up in Excel/Word/PDF. Documents ride the same email as
// the screenshots; only IMAGES count toward the minimum-screenshot rule.
const DOC_TYPES = [
  'application/pdf',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  'application/msword',
  'application/vnd.ms-excel',
  'text/csv',
]
const isImage = (f: File) => OK_TYPES.includes((f.type || '').toLowerCase())


type Intent = 'bug' | 'login' | 'feature'
interface Shot { file: File; url: string }   // url is '' for documents (no preview)

const COPY: Record<Intent, {
  title: string; sub: string; placeholder: string; tag: string
  minWords: number; minShots: number
}> = {
  bug: {
    title: 'Report a Bug',
    sub: 'Found something broken or not working as expected? Let us know and we’ll squash it.',
    placeholder: 'What happened? What did you expect instead? Which page / button / report? Steps to reproduce. Any error message you saw. The more detail, the faster we fix it.',
    tag: '',
    minWords: MIN_WORDS, minShots: MIN_SHOTS,
  },
  login: {
    title: 'Report a Login Issue',
    sub: 'Can’t log in, locked out, or having access problems? We’ll help you get back in.',
    placeholder: 'What happens when you try to sign in? Any error or code on screen? Roughly when did it start? Which browser/device? Have you tried before successfully? The more detail, the faster we help.',
    tag: '[LOGIN ISSUE] ',
    minWords: MIN_WORDS, minShots: MIN_SHOTS,
  },
  feature: {
    title: 'Ask for a New Feature',
    sub: 'Something Omni should be able to do but can’t yet? Ask for it — this is where new features start.',
    placeholder: 'What would you like Omni to do? Who would use it and how often? What do you do today instead (Excel, email, by hand)? What would it save you? Screenshots are optional here.',
    tag: '[FEATURE REQUEST] ',
    minWords: 25, minShots: 0,
  },
}

/** Pointer-driven 3D tilt for a card (±8°). Sets --rx/--ry CSS vars. */
function useTilt() {
  const ref = useRef<HTMLElement | null>(null)
  const onMove = (e: React.PointerEvent) => {
    const el = ref.current; if (!el) return
    const r = el.getBoundingClientRect()
    const px = (e.clientX - r.left) / r.width - 0.5
    const py = (e.clientY - r.top) / r.height - 0.5
    el.style.setProperty('--ry', `${(px * 16).toFixed(2)}deg`)
    el.style.setProperty('--rx', `${(-py * 16).toFixed(2)}deg`)
  }
  const reset = () => {
    const el = ref.current; if (!el) return
    el.style.setProperty('--rx', '0deg'); el.style.setProperty('--ry', '0deg')
  }
  return { ref, onMove, reset }
}

export default function ReportBugPage() {
  const router = useRouter()
  const [intent, setIntent]           = useState<Intent | null>(null)
  const [description, setDescription] = useState('')
  const [pageUrl, setPageUrl]         = useState('')
  const [shots, setShots]             = useState<Shot[]>([])
  const [submitting, setSubmitting]   = useState(false)
  const [error, setError]             = useState<string | null>(null)
  const [done, setDone]               = useState<{ id: string; emailed: boolean; message: string } | null>(null)
  const fileRef = useRef<HTMLInputElement | null>(null)

  const bugTilt = useTilt()
  const loginTilt = useTilt()
  const featureTilt = useTilt()

  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return }
    if (typeof document !== 'undefined' && document.referrer) {
      try {
        const u = new URL(document.referrer)
        if (u.origin === window.location.origin) setPageUrl(u.pathname + u.search)
      } catch { /* ignore */ }
    }
    // Deep link from the staff auto-reply email: /report-bug?intent=feature.
    // Read from window rather than useSearchParams() — this page is a client
    // component and useSearchParams forces a Suspense boundary at build time.
    const wanted = new URLSearchParams(window.location.search).get('intent')
    if (wanted === 'feature' || wanted === 'bug' || wanted === 'login') setIntent(wanted)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => () => { shots.forEach(s => { if (s.url) URL.revokeObjectURL(s.url) }) }, [shots])

  const minWords = intent ? COPY[intent].minWords : MIN_WORDS
  const minShots = intent ? COPY[intent].minShots : MIN_SHOTS

  const wordCount = useMemo(
    () => description.split(/\s+/).filter(Boolean).length, [description])
  const wordsOk = wordCount >= minWords
  const imageCount = shots.filter(s => isImage(s.file)).length
  const shotsOk = imageCount >= minShots && shots.length <= MAX_SHOTS
  const canSubmit = wordsOk && shotsOk && !submitting

  const addFiles = (files: FileList | null) => {
    if (!files) return
    setError(null)
    const next: Shot[] = []
    for (const f of Array.from(files)) {
      const mime = (f.type || '').toLowerCase()
      if (!OK_TYPES.includes(mime) && !DOC_TYPES.includes(mime)) {
        setError(`"${f.name}" is not an image or a document (PNG/JPG/WebP/GIF, PDF, Word, Excel or CSV).`); continue
      }
      if (f.size > MAX_BYTES) {
        setError(`"${f.name}" is larger than 10 MB. Please compress it.`); continue
      }
      next.push({ file: f, url: isImage(f) ? URL.createObjectURL(f) : '' })
    }
    setShots(prev => {
      const merged = [...prev, ...next]
      if (merged.length > MAX_SHOTS) {
        setError(`Maximum ${MAX_SHOTS} screenshots.`)
        merged.slice(MAX_SHOTS).forEach(s => { if (s.url) URL.revokeObjectURL(s.url) })
        return merged.slice(0, MAX_SHOTS)
      }
      return merged
    })
    if (fileRef.current) fileRef.current.value = ''
  }

  const removeShot = (idx: number) => {
    setShots(prev => {
      const copy = [...prev]
      const [gone] = copy.splice(idx, 1)
      if (gone?.url) URL.revokeObjectURL(gone.url)
      return copy
    })
  }

  const onSubmit = async () => {
    if (!canSubmit || !intent) return
    setSubmitting(true); setError(null)
    try {
      // Login path tags the text (backend has no category field) — payload shape unchanged.
      const body = (COPY[intent].tag + description.trim())
      const res = await submitBugReport(body, shots.map(s => s.file), pageUrl.trim())
      setDone(res)
      shots.forEach(s => { if (s.url) URL.revokeObjectURL(s.url) })
      setShots([]); setDescription(''); setPageUrl('')
    } catch (e: any) {
      setError(sendFailureMessage(e))
    } finally { setSubmitting(false) }
  }

  // ── Success ───────────────────────────────────────────────────────────────
  if (done) {
    return (
      <div className="obsidian-scene flex items-center justify-center p-6">
        <div className="ob-glass relative z-10 max-w-md w-full p-10 text-center">
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-full ob-glow-ring mb-5"
               style={{ background: 'var(--ad-orange-soft)' }}>
            <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="#34D399" strokeWidth="2.5"
                 strokeLinecap="round" strokeLinejoin="round">
              <path className="ob-check" d="M20 6 9 17l-5-5" />
            </svg>
          </div>
          <h2 className="text-2xl mb-1" style={{ fontFamily: 'var(--font-serif, Georgia, serif)', color: 'var(--ob-text-primary)' }}>
            Report sent
          </h2>
          <p className="text-sm mt-1" style={{ color: 'var(--ob-text-secondary)' }}>{done.message}</p>
          <p className="text-xs mt-2 font-mono" style={{ color: 'var(--ob-text-muted)' }}>Ref: {done.id}</p>
          <p className="text-sm mt-3" style={{ color: 'var(--ob-text-secondary)' }}>
            We&rsquo;ll email you whenever the status changes — track it under{' '}
            <button className="font-semibold underline decoration-dotted" style={{ color: 'var(--ad-orange)' }}
              onClick={() => router.push('/bug-reports')}>Bug Reports</button>.
          </p>
          <div className="flex items-center justify-center gap-3 mt-7">
            <button onClick={() => router.push('/dashboard')}
              className="px-4 py-2 rounded-lg text-sm font-medium"
              style={{ border: '1px solid var(--obsidian-line)', color: 'var(--ob-text-secondary)' }}>
              Back to dashboard
            </button>
            <button onClick={() => { setDone(null); setIntent(null) }}
              className="px-4 py-2 rounded-lg text-sm font-semibold"
              style={{ background: 'var(--ad-orange)', color: '#0B0B12' }}>
              Report another
            </button>
          </div>
        </div>
      </div>
    )
  }

  // ── Step 1: chooser ─────────────────────────────────────────────────────────
  if (!intent) {
    const cards: { key: Intent; img: string; tilt: ReturnType<typeof useTilt> }[] = [
      { key: 'bug',     img: '/brand/icon-bug-3d.png',   tilt: bugTilt },
      { key: 'login',   img: '/brand/icon-login-3d.png', tilt: loginTilt },
      { key: 'feature', img: '',                         tilt: featureTilt },
    ]
    return (
      <div className="obsidian-scene px-6 py-8 flex flex-col">
        <div className="relative z-10 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none"><path d="M12 2 22 12 12 22 2 12Z" stroke="#F07F00" strokeWidth="1.6"/><path d="M12 7 17 12 12 17 7 12Z" fill="#F07F00" opacity="0.85"/></svg>
            <div className="leading-tight">
              <div className="text-sm font-semibold" style={{ color: 'var(--ob-text-primary)' }}>OMNI Obsidian</div>
              <div className="text-[10px] tracking-widest uppercase" style={{ color: 'var(--ob-text-muted)' }}>Financial ERP</div>
            </div>
          </div>
          <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs"
            style={{ border: '1px solid var(--obsidian-line)', color: 'var(--ob-text-secondary)' }}>
            <LifeBuoy className="w-3.5 h-3.5" /> Support Center
          </span>
        </div>

        <div className="relative z-10 flex-1 flex flex-col items-center justify-center">
          <h1 className="text-center text-4xl sm:text-5xl mb-2" style={{ fontFamily: 'var(--font-serif, Georgia, serif)', color: 'var(--ob-text-primary)' }}>
            Something not right? <span style={{ color: 'var(--ad-orange)' }}>Tell us.</span>
          </h1>
          <p className="text-center text-sm mb-10" style={{ color: 'var(--ob-text-secondary)' }}>
            We&rsquo;re here to fix it fast and make your experience better.
          </p>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-6 w-full max-w-5xl">
            {cards.map(({ key, img, tilt }) => (
              <button
                key={key}
                ref={(el) => { tilt.ref.current = el }}
                onPointerMove={tilt.onMove}
                onPointerLeave={tilt.reset}
                onClick={() => { setError(null); setIntent(key) }}
                className="ob-glass tilt-card group text-left p-7 sm:p-9 flex flex-col items-center"
                style={{ minHeight: 300 }}
              >
                {img ? (
                  /* eslint-disable-next-line @next/next/no-img-element */
                  <img src={img} alt="" className="tilt-float w-28 h-28 object-contain mb-5"
                       style={{ filter: 'drop-shadow(0 8px 24px rgba(240,127,0,0.45))' }} />
                ) : (
                  <span className="tilt-float w-28 h-28 mb-5 rounded-full inline-flex items-center justify-center ob-glow-ring"
                        style={{ background: 'var(--ad-orange-soft)', filter: 'drop-shadow(0 8px 24px rgba(240,127,0,0.45))' }}>
                    <Lightbulb className="w-14 h-14" style={{ color: 'var(--ad-orange)' }} />
                  </span>
                )}
                <h2 className="text-2xl mb-2 text-center" style={{ fontFamily: 'var(--font-serif, Georgia, serif)', color: 'var(--ob-text-primary)' }}>
                  {COPY[key].title}
                </h2>
                <p className="text-center text-sm mb-6" style={{ color: 'var(--ob-text-secondary)' }}>{COPY[key].sub}</p>
                <span className="mt-auto inline-flex items-center justify-center w-10 h-10 rounded-full ob-glow-ring"
                      style={{ background: 'var(--ad-orange-soft)' }}>
                  <ArrowRight className="w-4 h-4" style={{ color: 'var(--ad-orange)' }} />
                </span>
              </button>
            ))}
          </div>
        </div>
      </div>
    )
  }

  // ── Step 2: form ─────────────────────────────────────────────────────────────
  const c = COPY[intent]
  return (
    <div className="obsidian-scene px-6 py-8">
      <div className="relative z-10 max-w-2xl mx-auto">
        <button onClick={() => { setIntent(null); setError(null) }}
          className="inline-flex items-center gap-1.5 text-sm mb-5" style={{ color: 'var(--ob-text-secondary)' }}>
          <ArrowLeft className="w-4 h-4" /> Back
        </button>

        <h1 className="text-3xl mb-1" style={{ fontFamily: 'var(--font-serif, Georgia, serif)', color: 'var(--ob-text-primary)' }}>{c.title}</h1>
        <p className="text-sm mb-6" style={{ color: 'var(--ob-text-secondary)' }}>
          This goes straight to the Exco board inbox. Please give at least <b style={{ color: 'var(--ob-text-primary)' }}>{minWords} words</b>
          {minShots > 0 ? (
            <> and <b style={{ color: 'var(--ob-text-primary)' }}>{minShots} screenshots</b> so we can act fast.</>
          ) : (
            <> so we understand the idea. Screenshots are optional.</>
          )}
        </p>

        {/* Description */}
        <div className="ob-glass p-5 mb-4">
          <div className="flex items-center justify-between mb-2">
            <label className="text-xs font-semibold uppercase tracking-wider" style={{ color: 'var(--ob-text-secondary)' }}>
              {intent === 'login' ? 'Describe the login problem'
                : intent === 'feature' ? 'Describe the feature you want'
                : 'Describe the problem'}
            </label>
            <span className="text-sm font-medium" style={{ color: wordsOk ? 'var(--positive)' : 'var(--ad-orange)' }}>
              {wordCount} / {minWords} words {wordsOk ? '✓' : '— almost there'}
            </span>
          </div>
          <textarea
            value={description} onChange={(e) => setDescription(e.target.value)} rows={7}
            placeholder={c.placeholder}
            className="w-full rounded-lg px-3 py-2 text-sm focus:outline-none transition-all"
            style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid var(--obsidian-line)', color: 'var(--ob-text-primary)' }}
          />
          <div className="mt-3">
            <label className="block text-xs mb-1" style={{ color: 'var(--ob-text-muted)' }}>
              Where in omni did this happen? <span>(optional)</span>
            </label>
            <input type="text" value={pageUrl} onChange={(e) => setPageUrl(e.target.value)}
              placeholder="/reports/balance-sheet  or  a description of the page"
              className="w-full rounded-lg px-3 py-2 text-sm focus:outline-none"
              style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid var(--obsidian-line)', color: 'var(--ob-text-primary)' }}
            />
          </div>
        </div>

        {/* Screenshots */}
        <div className="ob-glass p-5 mb-4">
          <div className="flex items-center justify-between mb-2">
            <label className="text-xs font-semibold uppercase tracking-wider" style={{ color: 'var(--ob-text-secondary)' }}>
              Screenshots &amp; documents {minShots === 0 && <span style={{ color: 'var(--ob-text-muted)' }}>(optional)</span>}
            </label>
            <span className="text-sm font-medium" style={{ color: shotsOk ? 'var(--positive)' : 'var(--ad-orange)' }}>
              {minShots === 0
                ? `${shots.length} attached`
                : `${imageCount} / ${minShots} min ${shotsOk ? '✓' : ''}`}
            </span>
          </div>
          <button type="button" onClick={() => fileRef.current?.click()}
            className="w-full rounded-lg py-8 flex flex-col items-center justify-center gap-2 transition-colors"
            style={{ border: '2px dashed var(--obsidian-line)' }}>
            <Upload className="w-6 h-6" style={{ color: 'var(--ob-text-muted)' }} />
            <span className="text-sm font-medium" style={{ color: 'var(--ob-text-secondary)' }}>
              Click to add screenshots or a document
            </span>
            <span className="text-xs" style={{ color: 'var(--ob-text-muted)' }}>
              PNG / JPG / WebP / GIF · PDF / Word / Excel / CSV · up to 10 MB each ·{' '}
              {minShots === 0 ? `optional, max ${MAX_SHOTS} files` : `min ${minShots} screenshots, max ${MAX_SHOTS} files`}
            </span>
          </button>
          <input ref={fileRef} type="file" multiple className="hidden"
            accept={[...OK_TYPES, ...DOC_TYPES, '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.csv'].join(',')}
            onChange={(e) => addFiles(e.target.files)} />
          {shots.length > 0 && (
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 mt-4">
              {shots.map((s, i) => (
                <div key={i} className="relative group rounded-lg overflow-hidden" style={{ border: '1px solid var(--obsidian-line)' }}>
                  {s.url ? (
                    /* eslint-disable-next-line @next/next/no-img-element */
                    <img src={s.url} alt={s.file.name} className="w-full h-28 object-cover" />
                  ) : (
                    <div className="w-full h-28 flex items-center justify-center"
                         style={{ background: 'rgba(255,255,255,0.04)' }}>
                      <FileText className="w-9 h-9" style={{ color: 'var(--ad-orange)' }} />
                    </div>
                  )}
                  <button type="button" onClick={() => removeShot(i)}
                    className="absolute top-1 right-1 w-6 h-6 rounded-full bg-black/70 text-white flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
                    aria-label="Remove"><X className="w-3.5 h-3.5" /></button>
                  <div className="px-2 py-1 text-[10px] truncate flex items-center gap-1" style={{ color: 'var(--ob-text-muted)' }}>
                    {s.url
                      ? <ImageIcon className="w-3 h-3 flex-shrink-0" />
                      : <FileText className="w-3 h-3 flex-shrink-0" />}
                    {s.file.name}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {error && (
          <div className="rounded-lg px-4 py-3 flex items-start gap-2 text-sm mb-4"
            style={{ background: 'rgba(248,113,113,0.1)', border: '1px solid rgba(248,113,113,0.3)', color: 'var(--negative)' }}>
            <AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5" /><span>{error}</span>
          </div>
        )}

        <div className="flex items-center justify-between gap-3">
          <p className="text-xs" style={{ color: 'var(--ob-text-muted)' }}>
            {canSubmit
              ? 'Ready to send.'
              : minShots === 0
                ? `Need ${minWords}+ words to submit.`
                : `Need ${minWords}+ words and ${minShots}+ screenshots to submit.`}
          </p>
          <button onClick={onSubmit} disabled={!canSubmit}
            className="inline-flex items-center gap-2 px-5 py-2.5 rounded-lg text-sm font-semibold transition-transform active:scale-[0.97] disabled:opacity-40 disabled:cursor-not-allowed"
            style={{ background: 'var(--ad-orange)', color: '#0B0B12' }}>
            <Send className="w-4 h-4" /> {submitting ? 'Sending…' : `Send ${
              intent === 'login' ? 'login issue' : intent === 'feature' ? 'feature request' : 'bug report'
            }`}
          </button>
        </div>
      </div>
    </div>
  )
}
