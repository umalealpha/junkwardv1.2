'use client'

/**
 * AccessPortalShell — the CFO's Omni sign-in surface.
 *
 * A presentational two-column layout ONLY. It carries no auth logic: the three
 * sign-in surfaces (/staff-login, / and /login) each keep their own real auth
 * calls and drop their step content in as {children}.
 *
 *   LEFT  (white)   — the brand moment: an orbit system around the Alpha Direct
 *                     mark, the "Omni" wordmark, then whatever the page passes
 *                     as children (the form / the two sign-in buttons).
 *   RIGHT (#000000) — "the Omni brain": a raster brain on pure black, wired to
 *                     eight labelled nodes by animated SVG synapses, under a
 *                     four-scene story that rotates forever.
 *
 * ── Redesigned 2026-09-12 from `design_handoff_omni_signin` (README +
 * `reference/Omni Sign In.dc.html`, which is the source of truth). ──
 *
 * WHY THE MOTION IS WRITTEN OUT IN FULL, AND WHY IT LOOKS OVER-SPECIFIED:
 * the previous handoff of this design lost its animations, and the designer's
 * README opens by saying that is the one failure to avoid — "the motion is not
 * decoration, it is the concept". So every @keyframes block, duration, easing,
 * delay and iteration count below is the reference file's own value, copied
 * verbatim rather than rounded to something tidier. The per-index formulas for
 * the node dots and the synapses (`2.8 + (k % 4) * 0.6` and `2.6 + (k % 5) *
 * 0.6`, staggered by `k * 0.4` / `k * 0.35`) are what stops the eight nodes
 * firing in unison; they are computed, never collapsed to one shared value.
 * If you are tempted to simplify any of it, change the mechanism, not the value.
 *
 * PURE BLACK IS DELIBERATE. omni-brain.png has a black ground, so the panel is
 * #000000 and the asset's ground disappears into it. Painting the panel navy
 * (or blending the image) makes it show as a visible rectangle — the designer
 * records that `mix-blend-mode: screen` was already tried and failed, because a
 * filter/transform on any ancestor creates an isolated stacking context and the
 * blend stops compositing against the panel. The brain stage below carries
 * exactly such a `filter: blur()` during the dissolve, so that route is closed.
 *
 * NO CDN. The two PNGs ship in /public/brand; Kaushan Script + Inter come
 * through next/font in the root layout, i.e. downloaded at build time and
 * served from our own origin. Nothing here reaches the network at runtime.
 *
 * `prefers-reduced-motion` is the ONLY permitted reduction: the loops freeze and
 * the scene rotation degrades to a plain opacity fade (see the media query at
 * the foot of the stylesheet).
 */
import { useEffect, useState, type ReactNode } from 'react'

/* ── Design tokens (handoff §"Colour") ──────────────────────────────────── */
const NAVY = '#0B0B3B'          // navy ink — all text and buttons on white
const ORANGE = '#F07F00'        // the single focal accent
const SYNAPSE = '#F0A040'       // travelling synapse pulse only
const PANEL = '#000000'         // right-hand panel — MUST stay pure black

export const OAP_SANS =
  "var(--font-inter),Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
// The "Omni" wordmark's face. The handoff is explicit: do not substitute
// another script face, it is the brand signature.
export const OAP_SCRIPT = "var(--font-kaushan),'Kaushan Script',cursive"
// Kept for callers that still ask for the old serif token. The 2026-09-12
// design sets everything except the wordmark in Inter.
export const OAP_SERIF =
  "'Book Antiqua','Palatino Linotype',Palatino,Georgia,serif"

export type SecurityPoint = { title: string; body: string }

/* The assurance row at the foot of the black panel. The design shows the three
 * titles only; the bodies stay for screen readers and the hover tooltip, which
 * is where two earlier CFO directives put the actual security wording. */
const DEFAULT_POINTS: SecurityPoint[] = [
  {
    title: 'Named accounts only',
    body: 'Every sign-in is tied to a real Alpha Direct staff account. There are no shared or anonymous logins.',
  },
  {
    title: 'Two steps, every time',
    body: 'Your password is only the first step. A one-time code sent to your work inbox confirms it is really you.',
  },
  {
    title: 'Sign-ins are logged',
    body: 'Sign-ins are recorded and reviewed. Never sign in on a shared or public machine, and never share your credentials.',
  },
]

/* ── The four scenes (handoff §"Scene content", verbatim) ───────────────────
 * Labels run left column top→bottom, then right column top→bottom, matching
 * SPOTS below. */
type Scene = {
  mode: string
  labels: string[]
  headline: string
  body: string
  features: { title: string; body: string }[]
}

const SCENES: Scene[] = [
  {
    mode: 'reading',
    labels: ['Inbox', 'Bank feed', 'Payroll', 'Ledger', 'CEO brief', 'Tasks', 'Claims', 'Reporting'],
    headline: 'One system that reads, checks and remembers.',
    body: "Omni holds Alpha Direct's finance, people and claims records. The reading happens overnight; the deciding waits for you.",
    features: [
      { title: 'Reads the bank statement before you wake', body: 'Matches what FNB says has cleared against every open payment and closes the ones that are paid.' },
      { title: 'Writes the morning brief', body: 'Reads the inbox overnight, drafts the 7am brief and turns loose ends into tasks with an owner.' },
      { title: 'Checks every module while you sleep', body: 'Runs the overnight checks across finance, people and claims and lists what needs a human by 8:30.' },
    ],
  },
  {
    mode: 'watching',
    labels: ['Exposure', 'Cash flow', 'Reserves', 'Run-off', 'Fraud signal', 'Renewals', 'Severity', 'Treaty'],
    headline: 'It notices what nobody reported.',
    body: 'Somewhere in ninety thousand records a pattern shifts. Omni sees it long before it becomes a line on a board pack.',
    features: [
      { title: 'Watches the book breathe', body: 'Tracks exposure, frequency and severity as they move, and speaks up the week a line starts to drift.' },
      { title: 'Flags the claim that does not fit', body: 'Weighs every new claim against the shape of the thousands before it and quietly raises a hand.' },
      { title: 'Knows a renewal before the broker calls', body: 'Reads the run-off on every policy and marks the ones that need a conversation, not a reminder.' },
    ],
  },
  {
    mode: 'remembering',
    labels: ['Every policy', 'Every claim', 'Every email', 'Provenance', 'Decisions', 'Audit trail', 'Seven years', 'Recall'],
    headline: 'It forgets nothing, and it can prove it.',
    body: 'Every record, correction and approval is kept, indexed and returned the moment someone asks — with the working shown.',
    features: [
      { title: 'Remembers the decision and who made it', body: 'Every approval, override and note sits against the record with a name and a timestamp.' },
      { title: 'Answers from seven years of history', body: 'Ask in plain words. Omni reads the archive, cites the documents and never guesses.' },
      { title: 'Keeps the audit trail nobody has to assemble', body: 'What NBFIRA asks for in March has been quietly written since January.' },
    ],
  },
  {
    mode: 'deciding',
    labels: ['Forecast', 'Pricing', 'Scenario', 'Margin', 'Solvency', 'Capital', 'Signal', 'Board pack'],
    headline: 'It reads the same records forward.',
    body: 'The book, modelled ahead. Omni shows where the year bends, and what it would take to bend it back.',
    features: [
      { title: 'Prices from what actually happened', body: 'Every quote carries the weight of the claims this book has already paid.' },
      { title: 'Tests the year before it arrives', body: 'Runs the loss shocks, treaty changes and capital strain, then names the breaking point.' },
      { title: 'Builds the board pack from the ledger', body: 'Figures assembled from source, traceable to the entry, never retyped from a spreadsheet.' },
    ],
  },
]

/* Node anchor points, in the SVG overlay's 0–100 viewBox. Four down the left
 * edge, four down the right; the hub is the orange core, which sits at the
 * brain's optical centre (top: 40%), not the geometric one. */
const SPOTS: [number, number][] = [
  [0, 10], [0, 32], [0, 56], [0, 80],
  [100, 10], [100, 32], [100, 56], [100, 80],
]
const HUB: [number, number] = [50, 40]

/* Per-index motion, computed exactly as the reference does. Both arrays are
 * scene-independent — only the labels change — so they are built once at module
 * load rather than on every render. */
const NODE_MOTION = SPOTS.map(([x, y], k) => ({
  x,
  y,
  left: x < 50,
  dur: (2.8 + (k % 4) * 0.6).toFixed(1),
  del: (k * 0.4).toFixed(2),
}))
const EDGES = SPOTS.map(([x, y], k) => ({
  x1: x,
  y1: y,
  x2: HUB[0],
  y2: HUB[1],
  dur: (2.6 + (k % 5) * 0.6).toFixed(1),
  del: (k * 0.35).toFixed(2),
}))

/* Shared control styling. Exported as className constants so the three pages
 * render identical inputs/buttons; the CSS itself is injected once by the shell. */
export const OAP = {
  field: 'oap-field',
  label: 'oap-label',
  primaryBtn: 'oap-btn oap-btn-primary',
  msBtn: 'oap-btn oap-btn-ms',
  linkBtn: 'oap-linkbtn',
  notice: 'oap-notice',
  error: 'oap-error',
  pwWrap: 'oap-pw-wrap',
  pwToggle: 'oap-pw-toggle',
  orModule: 'oap-or',
} as const

/* Inline "four squares" Microsoft mark — no external image. Tile colours are
 * the reference file's (#7FBA00, not the README's typo'd #7BA00). */
export function MicrosoftMark() {
  return (
    <span className="oap-ms-mark" aria-hidden="true">
      <i style={{ background: '#F25022' }} />
      <i style={{ background: '#7FBA00' }} />
      <i style={{ background: '#00A4EF' }} />
      <i style={{ background: '#FFB900' }} />
    </span>
  )
}

/* ── The orbit system on the white column ──────────────────────────────────
 * Six concentric layers positioned by `inset` percentage so the whole thing
 * scales with the square box and needs no media queries. */
function Orbit() {
  return (
    <div className="oap-orbit">
      <div className="oap-ring oap-ring-1" />
      <div className="oap-ring oap-ring-2" />
      <div className="oap-ring oap-ring-3" />
      <div className="oap-pulsering" />
      <div className="oap-arc oap-arc-blue" />
      <div className="oap-arc oap-arc-orange" />
      <div className="oap-orbiter oap-orbiter-orange"><i /></div>
      <div className="oap-orbiter oap-orbiter-blue"><i /></div>
      <div className="oap-bloom" />
      {/* Plain <img>: a static brand PNG in /public. Next/Image buys nothing
          here and adds optimizer indirection on the sign-in critical path. */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src="/brand/alpha-direct-mark-clean.png"
        alt="Alpha Direct Insurance"
        className="oap-mark"
      />
    </div>
  )
}

export default function AccessPortalShell({
  children,
  securityPoints = DEFAULT_POINTS,
}: {
  children: ReactNode
  securityPoints?: SecurityPoint[]
}) {
  /* The whole right panel is two numbers. `i` picks the scene; `fade` drives
   * opacity/blur/drift on three blocks at once. */
  const [i, setI] = useState(0)
  const [fade, setFade] = useState(1)

  useEffect(() => {
    /* Two-phase crossfade, per the handoff: begin the dissolve, and only swap
     * the content 2s later while it is invisible. A CSS-only crossfade cannot
     * do this — it would cut to the new copy the instant the class changed,
     * which is the "hard swap" the designer warns reads as broken. */
    let swap: ReturnType<typeof setTimeout> | undefined
    const timer = setInterval(() => {
      setFade(0)
      swap = setTimeout(() => {
        setI(prev => (prev + 1) % SCENES.length)
        setFade(1)
      }, 2000)
    }, 10000)
    return () => {
      clearInterval(timer)
      if (swap) clearTimeout(swap)
    }
  }, [])

  const sc = SCENES[i]
  // Derived from `fade`, exactly as the reference computes them.
  const blur = fade ? '0px' : '9px'
  const scale = fade ? 1 : 1.045
  const shift = fade ? '0px' : '14px'

  return (
    <main className="oap-root">
      {/* LEFT — white, the brand moment */}
      <section className="oap-left">
        <div className="oap-spacer-top" />
        <div className="oap-brand">
          <Orbit />
          <div className="oap-signin-wordmark">Omni</div>
        </div>
        <div className="oap-spacer-mid" />
        {/* The pages' own headings, copy, buttons and forms land here. */}
        <div className="oap-signin">{children}</div>
      </section>

      {/* RIGHT — pure black, "the Omni brain" */}
      <aside className="oap-right" aria-label="About Omni">
        {/* Eyebrow: the live scene mode, and four ticks. Both track `i`
            INSTANTLY — the designer is explicit that they do not fade, so the
            viewer can see the machine change its mind before the picture
            catches up. */}
        <div className="oap-eyebrow">
          <div className="oap-eyebrow-left">
            <span className="oap-eyebrow-dot" />
            <span>The Omni brain</span>
            <span className="oap-eyebrow-sep">·</span>
            <span className="oap-mode">{sc.mode}</span>
          </div>
          <div className="oap-ticks">
            {SCENES.map((s, k) => (
              <div
                key={s.mode}
                className={k === i ? 'oap-tick oap-tick-on' : 'oap-tick'}
              />
            ))}
          </div>
        </div>

        <div
          className="oap-stage"
          style={{ opacity: fade, filter: `blur(${blur})`, transform: `scale(${scale})` }}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/brand/omni-brain.png" alt="The Omni brain" className="oap-brain" />

          <svg className="oap-synapses" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
            {/* Travelling pulses first, static rails over them — the reference's
                own paint order, which keeps the rail reading as a hairline
                rather than a halo around each pulse. */}
            {EDGES.map((e, k) => (
              <line
                key={`pulse-${k}`}
                className="oap-synapse"
                x1={e.x1} y1={e.y1} x2={e.x2} y2={e.y2}
                vectorEffect="non-scaling-stroke"
                style={{ animationDuration: `${e.dur}s`, animationDelay: `${e.del}s` }}
              />
            ))}
            {EDGES.map((e, k) => (
              <line
                key={`rail-${k}`}
                className="oap-rail"
                x1={e.x1} y1={e.y1} x2={e.x2} y2={e.y2}
                vectorEffect="non-scaling-stroke"
              />
            ))}
          </svg>

          <div className="oap-core" />

          {NODE_MOTION.map((n, k) => (
            <div
              key={sc.labels[k]}
              className={n.left ? 'oap-node oap-node-l' : 'oap-node oap-node-r'}
              style={{ left: `${n.x}%`, top: `${n.y}%` }}
            >
              <div
                className="oap-node-dot"
                style={{ animationDuration: `${n.dur}s`, animationDelay: `${n.del}s` }}
              />
              <div className="oap-node-wire" />
              <div className="oap-node-label">{sc.labels[k]}</div>
            </div>
          ))}
        </div>

        <div className="oap-rule" />

        {/* The copy lags the brain by .15s and the feature rows by .3s. That
            stagger is the whole effect: three blocks defocusing on slightly
            different clocks reads as depth, one block reads as a fade. */}
        <div
          className="oap-story"
          style={{ opacity: fade, filter: `blur(${blur})`, transform: `translateY(${shift})` }}
        >
          <div className="oap-headline">{sc.headline}</div>
          <div className="oap-body">{sc.body}</div>
        </div>

        <div
          className="oap-features"
          style={{ opacity: fade, filter: `blur(${blur})`, transform: `translateY(${shift})` }}
        >
          {sc.features.map(f => (
            <div key={f.title} className="oap-feature">
              <div className="oap-feature-dash" />
              <div className="oap-feature-text">
                <div className="oap-feature-title">{f.title}</div>
                <div className="oap-feature-body">{f.body}</div>
              </div>
            </div>
          ))}
        </div>

        <ul className="oap-assurances" aria-label="How access is protected">
          {securityPoints.map(p => (
            <li key={p.title} className="oap-assurance" title={p.body}>
              <span className="oap-assurance-dot" aria-hidden="true" />
              {p.title}
              <span className="oap-sr">. {p.body}</span>
            </li>
          ))}
        </ul>
      </aside>

      <style>{CSS}</style>
    </main>
  )
}

const CSS = `
/* ── KEYFRAMES — verbatim from the handoff's ANIMATION SPEC. Do not retune. ── */
@keyframes om-spin      { to { transform: rotate(360deg) } }
@keyframes om-spinrev   { to { transform: rotate(-360deg) } }
@keyframes om-breathe   { 0%,100% { transform:scale(1); opacity:.4 }
                          50%     { transform:scale(1.12); opacity:.8 } }
@keyframes om-pulsering { 0%   { transform:scale(.8); opacity:.45 }
                          100% { transform:scale(1.8); opacity:0 } }
@keyframes om-fadeup    { 0%   { transform:translateY(18px); opacity:0 }
                          100% { transform:translateY(0);   opacity:1 } }
@keyframes om-node      { 0%,100% { opacity:.5; transform:scale(.9) }
                          50%     { opacity:1;  transform:scale(1.3) } }
@keyframes om-synapse   { 0%   { stroke-dashoffset:220 }
                          100% { stroke-dashoffset:0 } }
@keyframes om-core      { 0%,100% { opacity:.45; transform:translate(-50%,-50%) scale(1) }
                          50%     { opacity:.95; transform:translate(-50%,-50%) scale(1.18) } }

/* ── Layout ──────────────────────────────────────────────────────────────── */
.oap-root {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(420px, 100%), 1fr));
  min-height: 100vh;
  min-height: 100dvh;
  /* CFO 2026-09-18: the sign-in must fit ONE screen. min-height alone lets
   * the grid grow taller when a long form (reset-password: three fields +
   * submit) pushes past the viewport, and the ~63px overflow was reported
   * on /staff-login. Capping height with height: 100dvh bounds the root,
   * and .oap-signin's own overflow-y: auto scrolls inside its column
   * instead of pushing the page over. NOTE: no backticks in this comment;
   * the whole CSS block sits inside a JS template literal, so a backtick
   * would end it early (that is what broke the first deploy attempt). */
  height: 100dvh;
  background: #ffffff;
  color: ${NAVY};
  font-family: ${OAP_SANS};
}

/* ── LEFT column — white ─────────────────────────────────────────────────── */
.oap-left {
  position: relative;
  overflow: hidden;
  background: #ffffff;
  display: flex;
  flex-direction: column;
  /* Vertical padding is capped by screen height so the whole column fits one
     screen on a short laptop (CFO 17-Sep-2026: "fit it into a page"); the
     horizontal padding keeps the original width breathing room. */
  padding: clamp(12px, 2.4vh, 48px) clamp(28px, 4vw, 64px);
}
.oap-spacer-top { flex: 1; min-height: clamp(4px, 1vh, 40px); }
.oap-spacer-mid { flex: 1; min-height: clamp(4px, 1vh, 28px); }
.oap-brand {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: clamp(6px, 1.2vh, 30px);
  padding: clamp(4px, 0.8vh, 40px) 0;
}

/* Orbit: everything is inset-positioned inside one square box, so the system
   scales as a unit and never needs a breakpoint. */
.oap-orbit {
  position: relative;
  /* Also capped by 42% of the screen height, so on a short laptop the orbit
     shrinks and rises instead of pushing the wordmark and sign-in off-screen.
     On a tall monitor 42vh is larger than 460px, so the design is unchanged. */
  width: min(460px, 78vw, 30vh);
  aspect-ratio: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}
.oap-ring, .oap-pulsering, .oap-arc, .oap-orbiter {
  position: absolute;
  border-radius: 50%;
}
.oap-ring-1 { inset: 11%; border: 1px solid rgba(11,11,59,.10); }
.oap-ring-2 { inset: 20%; border: 1px solid rgba(11,11,59,.14); }
.oap-ring-3 { inset: 30%; border: 1px solid rgba(11,11,59,.08); }
.oap-pulsering {
  inset: 5%;
  border: 1px solid rgba(127,212,255,.55);
  animation: om-pulsering 5.5s ease-out infinite;
}
.oap-arc { border: 2px solid transparent; }
.oap-arc-blue {
  inset: 11%;
  border-top-color: rgba(64,168,224,.85);
  animation: om-spin 14s linear infinite;
}
.oap-arc-orange {
  inset: 20%;
  border-left-color: rgba(240,127,0,.85);
  animation: om-spinrev 9s linear infinite;
}
/* The particle rides a rotating wrapper rather than a path: one transform, no
   layout work per frame, and the orbit radius is just the wrapper's inset. */
.oap-orbiter i { position: absolute; left: 50%; border-radius: 50%; display: block; }
.oap-orbiter-orange { inset: 11%; animation: om-spin 11s linear infinite; }
.oap-orbiter-orange i {
  top: -7px; width: 14px; height: 14px; margin-left: -7px;
  background: ${ORANGE}; box-shadow: 0 0 26px 7px rgba(240,127,0,.5);
}
.oap-orbiter-blue { inset: 30%; animation: om-spinrev 17s linear infinite; }
.oap-orbiter-blue i {
  top: -5px; width: 10px; height: 10px; margin-left: -5px;
  background: #40A8E0; box-shadow: 0 0 20px 5px rgba(64,168,224,.45);
}
/* Centred by inset, NOT by translate(-50%,-50%). om-breathe's keyframes set
   transform: scale(...) outright, so a centring translate on the same element
   would be overwritten the moment the animation ran and the bloom would sit
   off-centre. inset: 21% is the same 58% box with nothing for the animation to
   clobber — which keeps the keyframes verbatim AND the bloom actually centred. */
.oap-bloom {
  position: absolute;
  inset: 21%;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(240,127,0,.14), rgba(255,255,255,0) 66%);
  animation: om-breathe 7s ease-in-out infinite;
}
.oap-mark {
  position: relative;
  width: 40%;
  height: 40%;
  display: block;
  filter: drop-shadow(0 18px 36px rgba(11,11,59,.16));
}

/* The wordmark rises in ONCE on mount and then holds still — "both" so it keeps
   the end state and never replays. */
.oap-signin-wordmark {
  position: relative;
  font-family: ${OAP_SCRIPT};
  font-weight: 400;
  /* Height-capped too (min of the width clamp and 13vh) so the wordmark shrinks
     on a short screen and the sign-in stays above the fold. */
  font-size: min(clamp(48px, 9vw, 140px), 10.5vh);
  line-height: 1.02;
  letter-spacing: .01em;
  color: ${NAVY};
  white-space: nowrap;
  animation: om-fadeup 1.1s ease-out both;
}
/* WHY THIS EXISTS, and why it needs four classes.
 * globals.css carries a blanket theme override —
 *   html.theme-professional *:not(.font-mono-nums):not([class*="mono"]):not(code)
 *   :not(pre):not(kbd) { font-family: var(--font-sans) !important; }
 * — put there to flatten components that hardcode a serif. It also flattens this
 * wordmark, so the script "Omni" renders as plain sans. That rule scores (0,3,4),
 * so a plain .oap-signin-wordmark cannot win even with !important — specificity
 * decides between two !important rules. Four classes beat its three. Do not
 * shorten this selector. */
.oap-root .oap-left .oap-brand .oap-signin-wordmark {
  font-family: ${OAP_SCRIPT} !important;
}

/* The sign-in block. The pages pass their content in flat, so the rhythm is
   margins here rather than a flex gap: 14px heading→copy, 26px copy→CTA,
   18px between the controls. */
.oap-signin {
  position: relative;
  max-width: 520px;
  width: 100%;
  margin: 0 auto;
  padding-bottom: clamp(16px, 3vw, 40px);
  /* If the form itself is taller than the column (staff-login's reset step
   * has three fields plus submit), scroll INSIDE this box, not the page.
   * The brand + orbit stay pinned, no ~63px page overflow (CFO 18-Sep). */
  max-height: 100%;
  overflow-y: auto;
  overscroll-behavior: contain;
}
.oap-h1 {
  font-size: 17px;
  font-weight: 600;
  letter-spacing: 0;
  line-height: 1.4;
  margin: 0 0 14px;
  color: ${NAVY};
}
.oap-sub {
  font-size: 17px;
  line-height: 1.6;
  color: rgba(11,11,59,.62);
  margin: 0 0 26px;
  text-wrap: pretty;
}

/* ── Controls ────────────────────────────────────────────────────────────── */
.oap-label {
  display: block;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.01em;
  color: rgba(11,11,59,.72);
  margin-bottom: 6px;
}
.oap-field {
  width: 100%;
  box-sizing: border-box;
  padding: 16px 16px;
  border: 1px solid rgba(11,11,59,.18);
  border-radius: 10px;
  background: #ffffff;
  color: ${NAVY};
  font-size: 15px;
  font-family: ${OAP_SANS};
  outline: none;
  transition: border-color .15s ease, box-shadow .15s ease;
}
.oap-field::placeholder { color: rgba(11,11,59,.38); }
.oap-field:focus {
  border-color: ${ORANGE};
  box-shadow: 0 0 0 3px rgba(240,127,0,0.15);
}
.oap-pw-wrap { position: relative; }
.oap-pw-wrap .oap-field { padding-right: 68px; }
.oap-pw-toggle {
  position: absolute;
  top: 50%; right: 10px;
  transform: translateY(-50%);
  border: none;
  background: transparent;
  color: rgba(11,11,59,.62);
  font-size: 12px;
  font-weight: 600;
  font-family: ${OAP_SANS};
  cursor: pointer;
  padding: 6px 8px;
  border-radius: 6px;
}
.oap-pw-toggle:hover { color: ${NAVY}; background: rgba(11,11,59,.06); }
.oap-trust {
  display: flex; align-items: center; gap: 9px;
  margin-top: 14px;
  font-size: 13px;
  color: rgba(11,11,59,.72);
  cursor: pointer;
  user-select: none;
}
.oap-trust input { width: 16px; height: 16px; accent-color: ${ORANGE}; cursor: pointer; }

.oap-btn {
  width: 100%;
  box-sizing: border-box;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  border-radius: 10px;
  font-size: 17px;
  font-family: ${OAP_SANS};
  cursor: pointer;
  text-decoration: none;
  transition: background .15s ease, border-color .15s ease, transform .05s ease, opacity .15s ease;
}
.oap-btn + .oap-btn { margin-top: 18px; }
.oap-btn:active { transform: translateY(1px); }
.oap-btn-primary {
  border: 0;
  padding: 22px 24px;
  font-weight: 600;
  color: #ffffff;
  background: ${NAVY};
  box-shadow: 0 12px 28px rgba(11,11,59,.18);
}
.oap-btn-primary:hover { background: #15156B; }
.oap-btn-primary:disabled { opacity: 0.6; cursor: not-allowed; }
/* The primary CTA is an <a> on the root landing. globals.css paints links per
   theme (html.theme-professional a { color }) at higher specificity than
   .oap-btn-primary, which turned the button text blue. Pin it to white. */
.oap-root a.oap-btn-primary,
.oap-root a.oap-btn-primary:hover,
.oap-root a.oap-btn-primary:visited { color: #ffffff; }
.oap-btn-ms {
  padding: 21px 24px;
  font-weight: 500;
  color: ${NAVY};
  background: #ffffff;
  border: 1px solid rgba(11,11,59,.18);
}
.oap-btn-ms:hover { border-color: rgba(11,11,59,.4); }
.oap-btn-ms:disabled { opacity: 0.6; cursor: not-allowed; }

.oap-ms-mark {
  width: 18px; height: 18px; flex: 0 0 18px;
  display: grid; grid-template-columns: 1fr 1fr; gap: 2px;
}
.oap-ms-mark i { display: block; }

.oap-or {
  display: flex;
  align-items: center;
  gap: 16px;
  margin: 18px 0;
  color: rgba(11,11,59,.42);
  font-size: 14px;
}
.oap-or::before, .oap-or::after {
  content: '';
  flex: 1;
  height: 1px;
  background: rgba(11,11,59,.14);
}

.oap-linkbtn {
  display: inline-block;
  border: none;
  background: none;
  padding: 0;
  color: rgba(11,11,59,.62);
  font-size: 13px;
  font-family: ${OAP_SANS};
  cursor: pointer;
  text-decoration: underline;
  text-underline-offset: 2px;
}
.oap-linkbtn:hover { color: ${ORANGE}; }

.oap-notice {
  margin-top: 16px;
  padding: 12px 14px;
  border-radius: 10px;
  font-size: 13px;
  line-height: 1.5;
  background: rgba(240,127,0,0.08);
  border: 1px solid rgba(240,127,0,0.35);
  color: #8a4b00;
}
.oap-error {
  margin-top: 16px;
  padding: 12px 14px;
  border-radius: 10px;
  font-size: 13.5px;
  line-height: 1.5;
  background: #fdecee;
  border: 1px solid #f2b8bd;
  color: #b0121f;
}

/* ── RIGHT column — the Omni brain, on PURE BLACK ────────────────────────── */
.oap-right {
  position: relative;
  overflow: hidden;
  background: ${PANEL};
  display: flex;
  flex-direction: column;
  /* Gap + vertical padding track screen height so the whole story panel fits one
     screen on a laptop (CFO 17-Sep-2026). overflow:hidden already clips, but the
     vh sizing means nothing needs clipping down to ~700px tall. */
  gap: clamp(8px, 1.3vh, 36px);
  padding: clamp(18px, 2.6vh, 64px) clamp(28px, 4vw, 64px);
  color: #ffffff;
}

.oap-eyebrow {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
}
.oap-eyebrow-left {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: .28em;
  text-transform: uppercase;
  color: rgba(255,255,255,.5);
}
.oap-eyebrow-dot {
  width: 7px; height: 7px;
  border-radius: 50%;
  background: ${ORANGE};
  box-shadow: 0 0 12px 3px rgba(240,127,0,.5);
}
.oap-eyebrow-sep { color: rgba(255,255,255,.3); }
.oap-mode { color: #7FD4FF; letter-spacing: .2em; }
.oap-ticks { display: flex; gap: 8px; }
.oap-tick {
  width: 22px; height: 2px;
  border-radius: 1px;
  background: rgba(255,255,255,.2);
  transition: background .5s ease;
}
.oap-tick-on { background: ${ORANGE}; }

/* The dissolve. opacity/filter on 1.6s and transform on 2.4s deliberately do
   NOT finish together — the drift outlasts the blur, which is what makes it
   read as a defocus rather than a cut. */
.oap-stage {
  position: relative;
  width: 100%;
  aspect-ratio: 16/11;
  /* Height-capped so the brain shrinks on a short screen instead of forcing the
     whole page to scroll. On a tall monitor 23vh is well below 440px, so the
     brain stays generous there. */
  min-height: clamp(150px, 24vh, 320px);
  max-height: 30vh;
  transition: opacity 1.6s cubic-bezier(.4,0,.2,1),
              filter 1.6s cubic-bezier(.4,0,.2,1),
              transform 2.4s cubic-bezier(.4,0,.2,1);
}
.oap-brain {
  position: absolute;
  left: 50%; top: 40%;
  /* Sized by the STAGE HEIGHT, not its width, so the brain always fits inside its
     box and its crown is never clipped at the top of the panel (CFO 17-Sep-2026:
     "the top of the brain is missing" after the stage was shortened to fit one
     screen). width:auto keeps the aspect ratio; the height tracks the stage.
     Centred at top:40%, so the top edge sits at (40% - height/2) of the stage;
     at 96% that is -8%, which pushed the crown UP over the eyebrow row and hid
     the live mode word after "The Omni brain ·" (CFO 17-Sep-2026: "the brain is
     cutting the reading"). Capped at 78% the top edge stays inside the stage on
     any height, and the shorter width opens the side gutter the node labels need
     — shrinking the IMAGE, not the type, exactly as the handoff requires.
     The max-width is container-relative, not a bare %: the node labels are a
     roughly fixed pixel width, so a % gutter starves them on a narrow panel and
     they touch the brain (they did at ~1280px). calc(100% - 340px) instead
     RESERVES ~170px of label gutter each side at any panel width, and the 72%
     ceiling keeps the brain from ballooning on a very wide one. */
  height: 78%;
  width: auto;
  max-width: min(72%, calc(100% - 340px));
  transform: translate(-50%,-50%);
  pointer-events: none;
}
.oap-synapses {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  overflow: visible;
}
/* stroke-dasharray "14 206" is a 14-long lit segment in a 220 gap, and om-synapse
   walks dashoffset 220 → 0, so ONE pulse travels node → core per iteration.
   non-scaling-stroke keeps the hairline honest under preserveAspectRatio="none",
   which otherwise stretches the stroke with the box. */
.oap-synapse {
  stroke: ${SYNAPSE};
  stroke-width: 0.9;
  stroke-opacity: 0.55;
  stroke-linecap: round;
  stroke-dasharray: 14 206;
  animation-name: om-synapse;
  animation-timing-function: linear;
  animation-iteration-count: infinite;
}
.oap-rail { stroke: rgba(240,127,0,0.18); stroke-width: 0.4; }

.oap-core {
  position: absolute;
  left: 50%; top: 40%;
  width: 14px; height: 14px;
  transform: translate(-50%,-50%);
  border-radius: 50%;
  background: ${ORANGE};
  box-shadow: 0 0 46px 14px rgba(240,127,0,.45);
  animation: om-core 3.4s ease-in-out infinite;
}

.oap-node {
  position: absolute;
  display: flex;
  align-items: center;
  gap: 10px;
  white-space: nowrap;
}
.oap-node-l { flex-direction: row;         transform: translate(0, -50%); }
.oap-node-r { flex-direction: row-reverse; transform: translate(-100%, -50%); }
.oap-node-dot {
  width: 5px; height: 5px; flex: none;
  border-radius: 50%;
  background: ${ORANGE};
  box-shadow: 0 0 12px 3px rgba(240,127,0,.5);
  animation-name: om-node;
  animation-timing-function: ease-in-out;
  animation-iteration-count: infinite;
}
.oap-node-wire {
  width: 16px; height: 1px; flex: none;
  background: linear-gradient(90deg, rgba(240,127,0,.55), rgba(127,212,255,.25));
}
.oap-node-label {
  font-size: 12px;
  font-weight: 500;
  letter-spacing: .16em;
  text-transform: uppercase;
  color: rgba(255,255,255,.88);
}

.oap-rule { height: 1px; width: 100%; background: rgba(255,255,255,.85); }

.oap-story {
  display: flex;
  flex-direction: column;
  gap: 14px;
  max-width: 640px;
  transition: opacity 1.5s cubic-bezier(.4,0,.2,1) .15s,
              filter 1.5s ease .15s,
              transform 1.8s cubic-bezier(.4,0,.2,1) .15s;
}
.oap-headline { font-size: 15px; font-weight: 700; line-height: 1.6; color: #fff; text-wrap: pretty; }
.oap-body { font-size: 15px; line-height: 1.6; color: rgba(255,255,255,.74); text-wrap: pretty; }

.oap-features {
  display: flex;
  flex-direction: column;
  transition: opacity 1.5s cubic-bezier(.4,0,.2,1) .3s,
              filter 1.5s ease .3s,
              transform 1.8s cubic-bezier(.4,0,.2,1) .3s;
}
.oap-feature {
  display: flex;
  gap: 16px;
  /* vh padding so three features compress on a short screen (CFO 17-Sep-2026). */
  padding: clamp(4px, 0.85vh, 20px) 0;
  border-top: 1px solid rgba(255,255,255,.14);
}
.oap-feature-dash { width: 14px; height: 2px; margin-top: 10px; flex: none; background: ${ORANGE}; }
.oap-feature-text { display: flex; flex-direction: column; gap: 7px; }
.oap-feature-title { font-size: 15px; font-weight: 600; color: #fff; }
.oap-feature-body { font-size: 14px; line-height: 1.6; color: rgba(255,255,255,.68); text-wrap: pretty; }

.oap-assurances {
  list-style: none;
  margin: 0;
  padding: 24px 0 0;
  display: flex;
  flex-wrap: wrap;
  gap: 28px;
  border-top: 1px solid rgba(255,255,255,.14);
}
.oap-assurance {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
  color: rgba(255,255,255,.78);
  cursor: help;
}
.oap-assurance-dot { width: 8px; height: 8px; border-radius: 50%; background: ${ORANGE}; flex: 0 0 8px; }
.oap-sr {
  position: absolute;
  width: 1px; height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}

/* If the panel is narrow enough that the node labels reach the brain, shrink
   the IMAGE, never the type — the handoff is explicit about which gives way. */
@media (max-width: 1100px) {
  .oap-brain { width: 52%; }
}
@media (max-width: 840px) {
  .oap-brain { width: 46%; }
  .oap-node-label { letter-spacing: .1em; }
}

/* ── The ONLY permitted reduction ────────────────────────────────────────────
 * Freeze the orbit/synapse/node loops, and let the scene rotation degrade to a
 * plain opacity fade: the blur and the drift are neutralised with !important
 * because they are applied as inline style from the fade state. The rotation
 * itself keeps running — the handoff asks for the fade, not for a static page. */
@media (prefers-reduced-motion: reduce) {
  .oap-pulsering, .oap-arc, .oap-orbiter, .oap-bloom,
  .oap-signin-wordmark, .oap-core, .oap-node-dot, .oap-synapse {
    animation: none !important;
  }
  .oap-synapse { stroke-dashoffset: 0; }
  .oap-bloom { opacity: .6; }
  .oap-stage, .oap-story, .oap-features {
    filter: none !important;
    transform: none !important;
    transition: opacity .6s ease !important;
  }
  .oap-btn, .oap-field, .oap-pw-toggle, .oap-linkbtn, .oap-tick { transition: none; }
}
`
