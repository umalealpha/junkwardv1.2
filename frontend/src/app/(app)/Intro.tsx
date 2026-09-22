'use client'

/**
 * Intro — the Alpha Omni opening (5 s, tap to skip).
 *
 * ⛔ FROZEN DESIGN — do not restyle, recolour or "harmonise" this screen.
 * The CFO supplied the finished design on 2026-09-08 ("Omni Amendments — FINAL
 * pack", artboard `2a — Orbit, white ground`, 1080 × 2340) with the decision
 * written down twice: apply it exactly, then lock it. The palette below is the
 * DESIGNED palette — deep navy #0B0B3B, orange #F07F00, light blue #40A8E0 —
 * and it is deliberately NOT the house brand navy/orange. Do not change it.
 *
 * The picture: a white ground; the real Alpha Direct mark on a white disc at the
 * centre of a faint orbit; a blue arc and an orange arc turning at different
 * speeds with a dot riding each; "Omni" in Kaushan Script; the line "One system.
 * Every decision."; then the loading strip and "Tap to skip".
 *
 * Fidelity: the design is an absolutely-positioned 1080 × 2340 stage, so it is
 * ported at those exact pixel values and scaled to fit the screen. That keeps
 * every measurement in the design's own numbers — nothing is re-guessed in
 * viewport units, and anyone can diff this file against the artboard.
 *
 * Behaviour is unchanged and still comes from introGate.ts: once per app open,
 * 5 s, tap anywhere to skip, and under prefers-reduced-motion the finished frame
 * is shown at once and the intro is over in 1.6 s.
 */
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { INTRO_MS } from './introGate'
import { sans } from './ui'

const LEAVE_MS = 450
const REDUCED_MS = 1600
const STATUS: Array<[number, string]> = [
  [0, 'Securing your session'],
  [1500, 'Syncing approvals'],
  [3000, 'Preparing your work'],
  [4300, 'Ready'],
]

// ── The designed stage. Every number below is the artboard's own value. ──────
const STAGE = { w: 1080, h: 2340 }
const NAVY = '#0B0B3B'
const ORANGE = '#F07F00'
const BLUE = '#40A8E0'
const PALE_BLUE = 'rgba(127,212,255,0.55)'

/** One orbit ring: `inset` into the 1120 px orbit box, plus its hairline. */
const RINGS: Array<{ inset: number; border: string }> = [
  { inset: 120, border: 'rgba(11,11,59,0.10)' },
  { inset: 220, border: 'rgba(11,11,59,0.14)' },
  { inset: 330, border: 'rgba(11,11,59,0.08)' },
]

export function Intro({ onDone }: { onDone: () => void }) {
  const reduced = useMemo(() => typeof window !== 'undefined'
    && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches, [])
  const total = reduced ? REDUCED_MS : INTRO_MS
  const [t, setT] = useState(0)
  const [leaving, setLeaving] = useState(false)
  // The stage is a fixed 1080 × 2340; scale it to whatever screen it lands on.
  const [scale, setScale] = useState(0)
  const boxRef = useRef<HTMLDivElement | null>(null)

  useLayoutEffect(() => {
    const fit = () => {
      const el = boxRef.current
      const w = el?.clientWidth || window.innerWidth
      const h = el?.clientHeight || window.innerHeight
      setScale(Math.min(w / STAGE.w, h / STAGE.h))
    }
    fit()
    window.addEventListener('resize', fit)
    return () => window.removeEventListener('resize', fit)
  }, [])

  useEffect(() => {
    const start = performance.now()
    const id = setInterval(() => setT(performance.now() - start), 60)
    const leave = setTimeout(() => setLeaving(true), total - LEAVE_MS)
    const done = setTimeout(onDone, total)
    return () => { clearInterval(id); clearTimeout(leave); clearTimeout(done) }
  }, [onDone, total])

  const skip = () => { setLeaving(true); setTimeout(onDone, 250) }
  const pct = Math.min(100, Math.max(4, (t / total) * 100))
  const status = reduced ? 'Ready' : (STATUS.filter(([at]) => t >= at).pop() ?? STATUS[0])[1]

  return (
    <div ref={boxRef} onClick={skip} role="presentation" data-omni-intro="" style={{
      position: 'fixed', inset: 0, zIndex: 100, cursor: 'pointer', overflow: 'hidden',
      fontFamily: sans, background: '#FFFFFF', color: NAVY,
      opacity: leaving ? 0 : 1, transition: `opacity ${LEAVE_MS}ms ease`,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }}>
      <div aria-hidden="true" style={{
        width: STAGE.w, height: STAGE.h, position: 'relative', flex: 'none',
        background: '#FFFFFF', overflow: 'hidden',
        transform: `scale(${scale})`, transformOrigin: 'center center',
        // Until the fit runs there is no honest scale to draw at, so stay blank
        // rather than flash a full-size stage for one frame.
        visibility: scale ? 'visible' : 'hidden',
      }}>
        {/* ── the orbit ──────────────────────────────────────────────────── */}
        <div style={{ position: 'absolute', left: '50%', top: 300, width: 1120, height: 1120, marginLeft: -560 }}>
          {RINGS.map(r => (
            <div key={r.inset} style={{
              position: 'absolute', inset: r.inset, borderRadius: '50%', border: `1px solid ${r.border}`,
            }} />
          ))}
          {/* the ring that breathes outward */}
          <div className="om-pulsering" style={{
            position: 'absolute', inset: 60, borderRadius: '50%', border: `1px solid ${PALE_BLUE}`,
          }} />
          {/* the two turning arcs — one blue clockwise, one orange the other way */}
          <div className="om-spin-14" style={{
            position: 'absolute', inset: 120, borderRadius: '50%',
            border: '2px solid transparent', borderTopColor: 'rgba(64,168,224,0.85)',
          }} />
          <div className="om-spinrev-9" style={{
            position: 'absolute', inset: 220, borderRadius: '50%',
            border: '2px solid transparent', borderLeftColor: 'rgba(240,127,0,0.85)',
          }} />
          {/* the dots riding those orbits */}
          <div className="om-spin-11" style={{ position: 'absolute', inset: 120 }}>
            <div style={{
              position: 'absolute', left: '50%', top: -9, width: 18, height: 18, marginLeft: -9,
              borderRadius: '50%', background: ORANGE, boxShadow: '0 0 34px 8px rgba(240,127,0,0.55)',
            }} />
          </div>
          <div className="om-spinrev-17" style={{ position: 'absolute', inset: 330 }}>
            <div style={{
              position: 'absolute', left: '50%', top: -6, width: 12, height: 12, marginLeft: -6,
              borderRadius: '50%', background: BLUE, boxShadow: '0 0 24px 6px rgba(64,168,224,0.5)',
            }} />
          </div>
          {/* the warm glow behind the mark */}
          <div className="om-breathe" style={{
            position: 'absolute', left: '50%', top: '50%', width: 640, height: 640, margin: '-320px 0 0 -320px',
            borderRadius: '50%',
            background: 'radial-gradient(circle, rgba(240,127,0,0.16), rgba(255,255,255,0) 66%)',
          }} />
        </div>

        {/* ── the words ──────────────────────────────────────────────────── */}
        <div style={{
          position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column',
          alignItems: 'center', padding: '180px 96px 140px',
        }}>
          {/* The mark is never redrawn and never recoloured — the real asset, on
            * its white disc, centred on the orbit (orbit top 300 + radius 560). */}
          <div style={{
            position: 'absolute', left: '50%', top: 860, width: 420, height: 420, margin: '-210px 0 0 -210px',
            borderRadius: '50%', background: '#FFFFFF',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            filter: 'drop-shadow(0 24px 48px rgba(11,11,59,0.18))',
          }}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src="/brand/ad-mark.png" alt="" style={{ width: 268, display: 'block' }} />
          </div>

          <div style={{ height: 1300 }} />

          <div className="om-fadeup-word" style={{
            fontFamily: 'var(--font-kaushan), "Kaushan Script", cursive',
            fontSize: 285, lineHeight: 1.05, color: NAVY, letterSpacing: '0.01em', whiteSpace: 'nowrap',
          }}>Omni</div>

          <div style={{
            marginTop: 30, width: 140, height: 2,
            background: 'linear-gradient(90deg, rgba(64,168,224,0), rgba(240,127,0,0.95), rgba(64,168,224,0))',
          }} />

          <div className="om-fadeup-tag" style={{
            marginTop: 30, fontSize: 32, fontWeight: 300, letterSpacing: '0.06em', color: 'rgba(11,11,59,0.62)',
          }}>One system. Every decision.</div>

          <div style={{ flex: 1 }} />

          <div style={{ width: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 26 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
              <div className="om-dot" style={{ width: 12, height: 12, borderRadius: '50%', background: ORANGE }} />
              <div style={{
                fontSize: 26, letterSpacing: '0.22em', textTransform: 'uppercase', color: 'rgba(11,11,59,0.55)',
              }}>{status}</div>
            </div>
            <div style={{ width: '100%', height: 5, borderRadius: 3, background: 'rgba(11,11,59,0.10)', overflow: 'hidden' }}>
              <div style={{
                height: '100%', width: `${pct}%`, borderRadius: 3,
                background: `linear-gradient(90deg, ${BLUE}, ${ORANGE})`,
                transition: 'width .12s linear',
              }} />
            </div>
            <div style={{
              marginTop: 34, fontSize: 24, letterSpacing: '0.18em', textTransform: 'uppercase', color: 'rgba(11,11,59,0.35)',
            }}>Tap to skip</div>
          </div>
        </div>
      </div>
    </div>
  )
}
