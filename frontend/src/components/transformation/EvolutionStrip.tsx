'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import type { Theme } from '@/lib/themes'
import type { TransformationEvolution, TransformationStage } from '@/lib/api'
import { PartyPopper, X } from 'lucide-react'
import { STAGE_FIGURES, type FigureMode } from './evolutionFigures'
import { clampPct } from './format'

interface EvolutionStripProps {
  theme: Theme
  evolution: TransformationEvolution | undefined
  reduceMotion: boolean
}

// 5 evenly spaced stage centres across a 0..1000 viewBox, with margin so the
// end figures and their props never clip.
const STAGE_X = [95, 300, 500, 700, 905]
const GROUND_Y = 208
const VIEWBOX = '0 0 1000 260'
const LAST_SEEN_KEY = 'tb_evolution_last_seen_index'

function markerX(idx: number, intoPct: number, isFinal: boolean): number {
  if (isFinal) return STAGE_X[STAGE_X.length - 1]
  const from = STAGE_X[idx] ?? STAGE_X[0]
  const to = STAGE_X[Math.min(idx + 1, STAGE_X.length - 1)]
  return from + (to - from) * (clampPct(intoPct) / 100)
}

function stageMode(stage: TransformationStage): FigureMode {
  if (stage.is_current) return 'current'
  return stage.reached ? 'achieved' : 'ghost'
}

/**
 * The "March of Progress" strip — CFO 2026-09-20's own picture, straight from
 * a hunched Homo Papyrus buried in paper to a friendly, glowing Machina
 * Automatica. Renders nothing if `evolution` is absent (an older cached
 * PulseSnapshot payload predates this field) rather than crash the page.
 */
export function EvolutionStrip({ theme, evolution, reduceMotion }: EvolutionStripProps) {
  const [openTooltip, setOpenTooltip] = useState<string | null>(null)
  const [celebrate, setCelebrate] = useState<TransformationStage | null>(null)
  const [dismissed, setDismissed] = useState(false)
  const mountedRef = useRef(false)
  const rafCleanup = useRef<number | null>(null)
  const [animatedX, setAnimatedX] = useState<number | null>(null)

  const targetX = evolution
    ? markerX(evolution.current_index, evolution.percent_into_stage, evolution.is_final)
    : 0

  // Detect a fresh arrival at a new stage vs. the viewer's last visit, and
  // celebrate exactly once. try/catch throughout — localStorage can throw in
  // private-browsing mode, and that must never take the strip down.
  useEffect(() => {
    if (!evolution) return
    let lastSeen: number | null = null
    try {
      const raw = window.localStorage.getItem(LAST_SEEN_KEY)
      lastSeen = raw === null ? null : parseInt(raw, 10)
    } catch { lastSeen = null }

    if (lastSeen !== null && !isNaN(lastSeen) && lastSeen < evolution.current_index) {
      setCelebrate(evolution.current)
    }
    try { window.localStorage.setItem(LAST_SEEN_KEY, String(evolution.current_index)) } catch { /* ignore */ }
    // Only ever run this check once per stage change, not on every re-render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [evolution?.current_index])

  // Walk the marker in from the start of the current stage on first paint,
  // then let ordinary re-renders animate straight to the new target — a
  // double rAF so the browser paints the "from" position before transitioning.
  useEffect(() => {
    if (!evolution) return
    if (!mountedRef.current) {
      mountedRef.current = true
      if (reduceMotion) { setAnimatedX(targetX); return }
      setAnimatedX(STAGE_X[evolution.current_index] ?? STAGE_X[0])
      const raf1 = requestAnimationFrame(() => {
        const raf2 = requestAnimationFrame(() => setAnimatedX(targetX))
        rafCleanup.current = raf2
      })
      rafCleanup.current = raf1
      return () => { if (rafCleanup.current) cancelAnimationFrame(rafCleanup.current) }
    }
    setAnimatedX(targetX)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetX, reduceMotion, evolution?.current_index])

  const ariaLabel = useMemo(() => {
    if (!evolution) return ''
    const base = `${evolution.current.name}, "${evolution.current.nickname}" — ${evolution.percent}% of the way to fully AI.`
    if (evolution.is_final) return `${base} ${evolution.headline}.`
    return `${base} ${evolution.points_to_next} points to go to become ${evolution.next?.name}.`
  }, [evolution])

  if (!evolution) return null

  const shownX = animatedX ?? targetX

  return (
    <div
      className="rounded-2xl overflow-hidden"
      style={{ background: theme.card, border: `1px solid ${theme.cardBdr}`, boxShadow: theme.cardSh }}
    >
      {celebrate && !dismissed && (
        <div
          className="flex items-center gap-3 px-5 py-3"
          style={{ background: theme.oL, borderBottom: `1px solid ${theme.cardBdr}` }}
        >
          <PartyPopper className="w-5 h-5 flex-shrink-0" style={{ color: theme.orangeText }} strokeWidth={1.8} />
          <p className="text-sm flex-1" style={{ color: theme.orangeText }}>
            <span className="font-semibold">You evolved!</span> The company is now {celebrate.name} — &ldquo;{celebrate.nickname}&rdquo;.
          </p>
          <button
            type="button"
            aria-label="Dismiss"
            onClick={() => setDismissed(true)}
            className="flex-shrink-0"
            style={{ color: theme.orangeText }}
          >
            <X className="w-4 h-4" strokeWidth={1.8} />
          </button>
        </div>
      )}

      <div className="px-4 sm:px-6 pt-6">
        <div
          role="img"
          aria-label={ariaLabel}
          className="relative"
          style={{ height: 'clamp(200px, 28vw, 300px)' }}
        >
          <svg viewBox={VIEWBOX} preserveAspectRatio="xMidYMax meet" className="w-full h-full" aria-hidden="true">
            <line x1={STAGE_X[0] - 55} y1={GROUND_Y} x2={STAGE_X[4] + 55} y2={GROUND_Y} stroke={theme.cardBdr} strokeWidth={3} strokeLinecap="round" />

            {evolution.stages.map((stage, i) => {
              const mode = stageMode(stage)
              const Figure = STAGE_FIGURES[stage.key]
              if (!Figure) return null
              const scale = mode === 'current' ? 1.24 : mode === 'achieved' ? 1 : 0.94
              const isCelebratingThis = !!celebrate && stage.is_current && !reduceMotion
              const shouldBob = mode === 'current' && !reduceMotion && !isCelebratingThis
              return (
                // Placement and animation MUST live on different elements.
                // An SVG `transform` attribute is a presentation attribute, so
                // a CSS animation on the same element replaces it outright —
                // the figure loses its slot and lands at the origin, clipped
                // in the corner, with its place on the strip left empty.
                <g
                  key={stage.key}
                  transform={`translate(${STAGE_X[i]},${GROUND_Y}) scale(${scale}) translate(0,-150)`}
                  style={{ color: theme.navy }}
                >
                  <g className={isCelebratingThis ? 'tb-arrive' : shouldBob ? 'tb-bob' : undefined}>
                    <Figure
                      mode={mode}
                      baseColor={mode === 'current' ? theme.navy : theme.t2}
                      accentColor={mode === 'ghost' ? theme.t3 : theme.orange}
                    />
                  </g>
                </g>
              )
            })}

            {/* "We are here" marker — creeps between silhouettes with
                percent_into_stage rather than only jumping at the five
                thresholds. */}
            <g
              transform={`translate(${shownX},0)`}
              style={{ transition: reduceMotion ? 'none' : 'transform 900ms cubic-bezier(0.16,1,0.3,1)' }}
            >
              <line x1={0} y1={GROUND_Y + 4} x2={0} y2={GROUND_Y - 172} stroke={theme.orange} strokeWidth={2} strokeDasharray="3 5" opacity={0.7} />
              <circle cx={0} cy={GROUND_Y - 178} r={7} fill={theme.orange} />
              <circle cx={0} cy={GROUND_Y - 178} r={11} fill="none" stroke={theme.orange} strokeWidth={1.5} opacity={0.4} />
              <text x={0} y={GROUND_Y - 188} textAnchor="middle" fontSize={14} fontWeight={700} fill={theme.navy}>
                {evolution.headline}
              </text>
            </g>
          </svg>

          {/* Accessible, keyboard-reachable per-stage tooltips — a sibling
              overlay, deliberately OUTSIDE the role="img" SVG above so these
              buttons stay in the accessibility tree instead of being
              swallowed as decorative. */}
          <div className="absolute inset-0 pointer-events-none">
            {evolution.stages.map((stage, i) => {
              const leftPct = (STAGE_X[i] / 1000) * 100
              const open = openTooltip === stage.key
              return (
                <div
                  key={stage.key}
                  className="absolute pointer-events-auto"
                  style={{ left: `${leftPct}%`, bottom: 0, transform: 'translateX(-50%)' }}
                >
                  <button
                    type="button"
                    className="w-10 h-10 sm:w-12 sm:h-12 rounded-full"
                    style={{ outlineOffset: 2 }}
                    onFocus={() => setOpenTooltip(stage.key)}
                    onBlur={() => setOpenTooltip((cur) => (cur === stage.key ? null : cur))}
                    onMouseEnter={() => setOpenTooltip(stage.key)}
                    onMouseLeave={() => setOpenTooltip((cur) => (cur === stage.key ? null : cur))}
                  >
                    <span className="sr-only">
                      {stage.name}, {stage.nickname}
                      {stage.is_current ? ' — current stage' : stage.reached ? ' — reached' : ' — not reached yet'}
                    </span>
                  </button>
                  {open && (
                    <div
                      role="tooltip"
                      className="absolute bottom-full mb-2 left-1/2 -translate-x-1/2 w-48 rounded-lg p-2.5 text-xs z-10"
                      style={{ background: theme.navy, color: '#FFFFFF' }}
                    >
                      <div className="font-semibold">{stage.name}</div>
                      <div className="opacity-80">&ldquo;{stage.nickname}&rdquo;</div>
                      <div className="mt-1 opacity-90">{stage.caption}</div>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      </div>

      <div className="px-5 sm:px-6 pb-6 pt-2">
        <div className="flex items-baseline gap-2 flex-wrap">
          <h3 className="text-xl font-bold" style={{ color: theme.navy }}>{evolution.current.name}</h3>
          <span className="text-sm" style={{ color: theme.t2 }}>&ldquo;{evolution.current.nickname}&rdquo;</span>
        </div>
        <p className="text-sm mt-1" style={{ color: theme.text }}>{evolution.current.caption}</p>

        {!evolution.is_final && evolution.next && (
          <div className="mt-3">
            <p className="text-xs" style={{ color: theme.t2 }}>
              <span className="font-semibold" style={{ color: theme.orangeText }}>{evolution.points_to_next}</span> points to become {evolution.next.name}
            </p>
            <div className="h-1.5 rounded-full overflow-hidden mt-1.5 max-w-sm" style={{ background: theme.g100 }}>
              <div
                className="tb-fill h-full rounded-full"
                style={{ background: theme.orange, width: `${clampPct(evolution.percent_into_stage)}%` }}
              />
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
