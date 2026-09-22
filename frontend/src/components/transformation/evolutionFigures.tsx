'use client'

import type { ReactNode } from 'react'

// The five stages of the "March of Progress" strip.
//
// CFO, 2026-09-20, on the first version: "first a human being, then the
// evolution happens and we can have a cool-looking AI picture, rather than a
// human again there."
//
// The first version drew five people walking — the joke from the famous
// print, but it said the wrong thing: it ended with another person standing
// there, as if the destination were more people doing more work. This set
// tells the real story instead:
//
//   1  a person, hunched under a stack of paper
//   2  the same person upright, working a screen
//   3  a machine arm bolted to the shoulder takes half the load
//   4  the body begins dissolving into data
//   5  no person at all — a luminous AI core
//
// Drawing frame: each figure is drawn in its OWN natural coordinates
// (x 0..200, ground at y=268, centre x=100) and then mapped into the strip's
// local frame by `Stand`, so the shapes stay readable numbers instead of a
// pile of hand-scaled decimals. The strip still places each figure at
// x in [-40, 40], y in [0, 150] with the feet on the ground at y=150.

export type FigureMode = 'achieved' | 'current' | 'ghost'

interface FigureProps {
  mode: FigureMode
  /** Theme accent — the "energy" colour: screens, joints, data, the core. */
  accentColor: string
  /** Theme navy — the body colour. */
  baseColor: string
}

/** Maps a figure's own 200x300 drawing box into the strip's frame, and
 *  applies the stage's state.
 *
 *  Ghost stages fade rather than switching to a dashed outline: these figures
 *  carry gradients and glows, and an outline-only pass renders them as a
 *  tangle of unfilled shapes. Fading keeps the silhouette readable. */
function Stand({ mode, children }: { mode: FigureMode; children: ReactNode }) {
  return (
    <g
      transform="translate(0,150) scale(0.6) translate(-100,-268)"
      opacity={mode === 'ghost' ? 0.36 : 1}
    >
      {children}
    </g>
  )
}

/** Gradients for one figure. Ids are namespaced per stage so the five
 *  definitions never collide in the single shared <svg>. */
function Defs({ uid, baseColor, accentColor }: { uid: string; baseColor: string; accentColor: string }) {
  return (
    <defs>
      {/* Left-to-right fade, for the body coming apart into data. */}
      <linearGradient id={`tb-dissolve-${uid}`} x1="0" y1="0" x2="1" y2="0">
        <stop offset="0%" stopColor={baseColor} />
        <stop offset="52%" stopColor={baseColor} />
        <stop offset="100%" stopColor={baseColor} stopOpacity="0.15" />
      </linearGradient>
      <radialGradient id={`tb-core-${uid}`} cx="50%" cy="50%" r="50%">
        <stop offset="0%" stopColor="#FFD9A8" />
        <stop offset="42%" stopColor={accentColor} />
        <stop offset="100%" stopColor={accentColor} stopOpacity="0.15" />
      </radialGradient>
      <radialGradient id={`tb-halo-${uid}`} cx="50%" cy="50%" r="50%">
        <stop offset="0%" stopColor={accentColor} stopOpacity="0.3" />
        <stop offset="100%" stopColor={accentColor} stopOpacity="0" />
      </radialGradient>
    </defs>
  )
}

/** A human body — shoulders, waist, two legs. Deliberately a person, not a
 *  stick figure, because stage 5 has to feel like a departure from it. */
function Person({ fill, lean, legFill }: { fill: string; lean: number; legFill?: string }) {
  return (
    <g transform={`rotate(${lean} 100 210)`}>
      <circle cx="100" cy="78" r="17" fill={fill} />
      <path
        d="M100 96 C 118 96 128 108 130 124 L 134 168 C 135 178 128 182 124 172
           L 118 146 L 120 198 L 80 198 L 82 146 L 76 172 C 72 182 65 178 66 168
           L 70 124 C 72 108 82 96 100 96 Z"
        fill={fill}
      />
      <path d="M82 194 L76 268 L93 268 L97 194 Z" fill={legFill ?? fill} />
      <path d="M103 194 L107 268 L124 268 L118 194 Z" fill={fill} />
    </g>
  )
}

// ─── Stage 0 — Homo Papyrus: a person, buried in paper ─────────────────────
export function PapyrusFigure({ mode, accentColor, baseColor }: FigureProps) {
  return (
    <Stand mode={mode}>
      <Person fill={baseColor} lean={-11} />
      <g transform="rotate(-11 100 210)">
        {/* Forearm under the stack, so the paper is carried and not floating. */}
        <path d="M116 132 L150 122" stroke={baseColor} strokeWidth={12} strokeLinecap="round" />
        <g transform="translate(126,108) rotate(9)">
          <rect x="0" y="0" width="46" height="10" rx="2" fill={baseColor} />
          <rect x="-2" y="-11" width="50" height="10" rx="2" fill={baseColor} opacity={0.82} />
          <rect x="1" y="-22" width="44" height="10" rx="2" fill={baseColor} opacity={0.64} />
          <rect x="-3" y="-33" width="50" height="10" rx="2" fill={baseColor} opacity={0.46} />
        </g>
        {/* Two sheets escaping the top — the stack is losing the fight. */}
        <rect x="150" y="62" width="30" height="9" rx="2" fill={accentColor}
              opacity={0.55} transform="rotate(28 165 66)" />
        <rect x="160" y="44" width="24" height="8" rx="2" fill={baseColor}
              opacity={0.2} transform="rotate(-18 172 48)" />
      </g>
    </Stand>
  )
}

// ─── Stage 1 — Homo Tabulatus: upright, working a screen ───────────────────
export function TabulatorFigure({ mode, accentColor, baseColor }: FigureProps) {
  return (
    <Stand mode={mode}>
      <Person fill={baseColor} lean={-5} />
      <g transform="rotate(-5 100 210)">
        <rect x="112" y="150" width="62" height="40" rx="4" fill={baseColor} />
        <rect x="117" y="155" width="52" height="30" rx="2" fill={accentColor} opacity={0.85} />
        <path d="M112 190 L174 190 L182 200 L104 200 Z" fill={baseColor} opacity={0.75} />
        <path d="M128 168 h30 M128 176 h22" stroke={baseColor} strokeWidth={2.5}
              opacity={0.45} strokeLinecap="round" />
      </g>
    </Stand>
  )
}

// ─── Stage 2 — Homo Clickus: a machine arm takes half the load ─────────────
export function ClickusFigure({ mode, accentColor, baseColor }: FigureProps) {
  return (
    <Stand mode={mode}>
      <Person fill={baseColor} lean={0} />
      <g>
        {/* Bolted at the shoulder, so it reads as an exoskeleton the person
            is still driving — not a separate robot standing beside them. */}
        <circle cx="124" cy="116" r="9" fill={baseColor} opacity={0.75} />
        <path d="M124 116 L158 140" stroke={baseColor} strokeWidth={11}
              strokeLinecap="round" opacity={0.6} />
        <circle cx="158" cy="140" r="7.5" fill={accentColor} />
        <path d="M158 140 L156 186" stroke={baseColor} strokeWidth={9}
              strokeLinecap="round" opacity={0.6} />
        <circle cx="156" cy="186" r="6" fill={accentColor} opacity={0.85} />
        <path d="M156 192 l-9 12 M156 192 l9 12" stroke={baseColor} strokeWidth={4.5}
              strokeLinecap="round" opacity={0.6} />
        <rect x="140" y="212" width="32" height="30" rx="4" fill={baseColor} opacity={0.26} />
        <path d="M100 100 v92" stroke={accentColor} strokeWidth={2} opacity={0.3}
              strokeDasharray="4 5" />
      </g>
    </Stand>
  )
}

// ─── Stage 3 — Homo Cyborgus: the body dissolving into data ────────────────
const DISSOLVE_NODES: Array<[number, number, number, number]> = [
  [140, 112, 3.4, 0.95], [156, 132, 2.6, 0.8], [148, 158, 3.0, 0.85],
  [166, 106, 2.2, 0.6], [162, 176, 2.4, 0.7], [176, 146, 1.9, 0.5],
  [152, 200, 2.7, 0.65], [174, 196, 2.0, 0.42], [186, 122, 1.7, 0.35],
  [168, 226, 2.2, 0.38], [188, 168, 1.6, 0.3],
]
const DISSOLVE_LINKS: Array<[number, number, number, number]> = [
  [140, 112, 156, 132], [156, 132, 148, 158], [148, 158, 162, 176],
  [156, 132, 166, 106], [162, 176, 152, 200], [166, 106, 186, 122],
  [162, 176, 176, 146],
]

export function CyborgusFigure({ mode, accentColor, baseColor }: FigureProps) {
  const uid = 'cyborgus'
  const fade = `url(#tb-dissolve-${uid})`
  return (
    <Stand mode={mode}>
      <Defs uid={uid} baseColor={baseColor} accentColor={accentColor} />
      <circle cx="100" cy="78" r="17" fill={fade} />
      <path
        d="M100 96 C 118 96 128 108 130 124 L 134 168 C 135 178 128 182 124 172
           L 118 146 L 120 198 L 80 198 L 82 146 L 76 172 C 72 182 65 178 66 168
           L 70 124 C 72 108 82 96 100 96 Z"
        fill={fade}
      />
      <path d="M82 194 L76 268 L93 268 L97 194 Z" fill={baseColor} />
      <path d="M103 194 L107 268 L124 268 L118 194 Z" fill={fade} />
      {/* Circuit seams across the chest. */}
      <path d="M92 108 h18 M88 130 h26 M94 152 h20" stroke={accentColor}
            strokeWidth={2} opacity={0.55} strokeLinecap="round" />
      {DISSOLVE_LINKS.map(([x1, y1, x2, y2]) => (
        <path key={`l${x1}-${y1}-${x2}-${y2}`} d={`M${x1} ${y1} L${x2} ${y2}`}
              stroke={accentColor} strokeWidth={1.1} opacity={0.28} />
      ))}
      {DISSOLVE_NODES.map(([cx, cy, r, o]) => (
        <circle key={`n${cx}-${cy}`} cx={cx} cy={cy} r={r} fill={accentColor} opacity={o} />
      ))}
    </Stand>
  )
}

// ─── Stage 4 — Machina Automatica: no person at all ────────────────────────
const CORE_NODES: Array<[number, number]> = [
  [100, 92], [146, 128], [150, 190], [100, 226], [50, 190], [54, 128],
]

export function AutomaticusFigure({ mode, accentColor, baseColor }: FigureProps) {
  const uid = 'automaticus'
  return (
    <Stand mode={mode}>
      <Defs uid={uid} baseColor={baseColor} accentColor={accentColor} />
      <circle cx="100" cy="159" r="96" fill={`url(#tb-halo-${uid})`} />
      <ellipse cx="100" cy="159" rx="86" ry="30" fill="none" stroke={baseColor}
               strokeWidth={2.2} opacity={0.3} transform="rotate(-22 100 159)" />
      <ellipse cx="100" cy="159" rx="86" ry="30" fill="none" stroke={baseColor}
               strokeWidth={2.2} opacity={0.22} transform="rotate(34 100 159)" />
      <ellipse cx="100" cy="159" rx="70" ry="70" fill="none" stroke={accentColor}
               strokeWidth={1.4} opacity={0.28} strokeDasharray="5 9" />
      {CORE_NODES.map(([x1, y1], i) =>
        CORE_NODES.slice(i + 1).map(([x2, y2]) => (
          <path key={`w${x1}-${y1}-${x2}-${y2}`} d={`M${x1} ${y1} L${x2} ${y2}`}
                stroke={accentColor} strokeWidth={1.3} opacity={0.34} />
        )),
      )}
      {CORE_NODES.map(([x, y]) => (
        <path key={`s${x}-${y}`} d={`M100 159 L${x} ${y}`} stroke={accentColor}
              strokeWidth={1.6} opacity={0.5} />
      ))}
      <path d="M100 116 L138 138 L138 180 L100 202 L62 180 L62 138 Z" fill={baseColor} />
      <path d="M100 128 L127 144 L127 174 L100 190 L73 174 L73 144 Z"
            fill={`url(#tb-core-${uid})`} />
      <circle cx="100" cy="159" r="10" fill="#FFF3E2" />
      {CORE_NODES.map(([x, y]) => (
        <g key={`c${x}-${y}`}>
          <circle cx={x} cy={y} r={10} fill={accentColor} opacity={0.18} />
          <circle cx={x} cy={y} r={5.4} fill={accentColor} />
        </g>
      ))}
      <path d="M42 268 h116" stroke={accentColor} strokeWidth={2} opacity={0.25}
            strokeLinecap="round" />
    </Stand>
  )
}

export const STAGE_FIGURES: Record<string, (props: FigureProps) => ReturnType<typeof PapyrusFigure>> = {
  papyrus: PapyrusFigure,
  tabulator: TabulatorFigure,
  clickus: ClickusFigure,
  cyborgus: CyborgusFigure,
  automaticus: AutomaticusFigure,
}
