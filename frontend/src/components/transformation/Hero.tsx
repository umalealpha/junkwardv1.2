'use client'

import type { Theme } from '@/lib/themes'
import type { TransformationClock } from '@/lib/api'
import { useCountUp } from './useCountUp'
import { clampPct, fmtDate } from './format'

interface HeroProps {
  theme: Theme
  headline: string
  overallPercent: number
  timePercent: number
  clock: TransformationClock
  reduceMotion: boolean
}

const RADIUS = 45
const CIRCUMFERENCE = 2 * Math.PI * RADIUS

// The whole point of the board in one glance: work done vs. time used. When
// the work line is behind the time line the ring reads as a warning, not a
// neutral progress bar — that tension is the headline, not a footnote.
export function Hero({ theme, headline, overallPercent, timePercent, clock, reduceMotion }: HeroProps) {
  const overall = clampPct(overallPercent)
  const timeUsed = clampPct(timePercent)
  const behind = overall < timeUsed
  const ringColor = behind ? theme.wr : theme.ok
  const animatedOverall = useCountUp(overall, 1100, !reduceMotion)
  const animatedDays = useCountUp(Math.max(0, clock.days_remaining), 1100, !reduceMotion)

  const offset = CIRCUMFERENCE - (CIRCUMFERENCE * animatedOverall) / 100
  const timeAngle = (timeUsed / 100) * 360

  return (
    <div
      className="rounded-2xl overflow-hidden relative px-6 py-10 sm:px-10 sm:py-14"
      style={{ background: '#0B0B3B' }}
    >
      {/* Faint Botswana-flag-blue wash, decorative only. */}
      <div
        aria-hidden="true"
        className="absolute inset-0 pointer-events-none"
        style={{
          background: 'radial-gradient(60vmax 40vmax at 20% -10%, rgba(0,158,73,0.12), transparent 55%), '
            + 'radial-gradient(50vmax 40vmax at 100% 100%, rgba(244,166,35,0.10), transparent 55%)',
        }}
      />
      <div className="relative flex flex-col lg:flex-row items-center lg:items-stretch gap-10">
        <div className="flex-1 text-center lg:text-left">
          <h1
            className="font-bold text-white leading-[1.05] tracking-tight"
            style={{ fontSize: 'clamp(2rem, 1.2rem + 3.6vw, 3.75rem)' }}
          >
            {headline} <span aria-hidden="true">🇧🇼</span>
          </h1>
          <p className="mt-4 text-sm sm:text-base text-white/70 max-w-xl mx-auto lg:mx-0">
            Programme window {fmtDate(clock.start)} → {fmtDate(clock.end)}.
          </p>

          <div className="mt-8 flex items-center justify-center lg:justify-start gap-8 flex-wrap">
            <div>
              <div
                className="font-bold text-white tabular-nums"
                // An explicit line-height: without one the number inherits a
                // small absolute line-height from an ancestor, overflows its
                // line box and lands on top of the caption below it.
                style={{ fontSize: 'clamp(2.5rem, 2rem + 2vw, 4rem)', lineHeight: 1.1 }}
                aria-label={`${Math.round(animatedDays)} days remaining, to 20 January 2027`}
              >
                {Math.round(animatedDays)}
              </div>
              <div className="text-xs uppercase tracking-wide text-white/60 mt-1">
                days remaining · to 20 Jan 2027
              </div>
            </div>
            <div className="h-12 w-px bg-white/15 hidden sm:block" aria-hidden="true" />
            <div className="text-sm text-white/80">
              <div>Day {clock.days_elapsed} of {clock.days_total}</div>
              {/* White text always — a colour on the navy hero can wash out
                  under the Professional theme repaint, so status rides on a
                  small ring + bold weight instead of a text colour swap. */}
              <div className="mt-1 flex items-center gap-2 font-semibold text-white">
                <span
                  aria-hidden="true"
                  className="inline-block w-2 h-2 rounded-full ring-2 ring-white/40"
                  style={{ background: behind ? '#F07F00' : '#5FEAB0' }}
                />
                {behind
                  ? `Work is ${timeUsed - overall} points behind the clock`
                  : `Work is ahead of the clock by ${overall - timeUsed} points`}
              </div>
            </div>
          </div>
        </div>

        <div className="flex-shrink-0 flex items-center justify-center">
          <svg
            width="220" height="220" viewBox="0 0 100 100"
            role="img"
            aria-label={`Overall progress ${overall} percent, against ${timeUsed} percent of the time used`}
          >
            <circle cx="50" cy="50" r={RADIUS} fill="none" stroke="rgba(255,255,255,0.12)" strokeWidth="8" />
            {/* Time-used marker ring, drawn as a thin dashed track. */}
            <circle
              cx="50" cy="50" r={RADIUS + 2.5} fill="none"
              stroke="rgba(255,255,255,0.35)" strokeWidth="1.5"
              strokeDasharray={`${(CIRCUMFERENCE * timeUsed) / 100} ${CIRCUMFERENCE}`}
              transform="rotate(-90 50 50)"
            />
            <circle
              cx="50" cy="50" r={RADIUS} fill="none"
              stroke={ringColor} strokeWidth="8" strokeLinecap="round"
              strokeDasharray={CIRCUMFERENCE}
              strokeDashoffset={reduceMotion ? CIRCUMFERENCE - (CIRCUMFERENCE * overall) / 100 : offset}
              transform="rotate(-90 50 50)"
              style={{ transition: reduceMotion ? 'none' : 'stroke-dashoffset 120ms linear' }}
            />
            {/* Tick at the time-used angle, for a direct visual read of "here is now". */}
            <line
              x1="50" y1="2" x2="50" y2="9"
              stroke="#F07F00" strokeWidth="2"
              transform={`rotate(${timeAngle - 90} 50 50)`}
            />
            <text x="50" y="46" textAnchor="middle" className="tabular-nums" fontSize="18" fontWeight="700" fill="#FFFFFF">
              {Math.round(animatedOverall)}%
            </text>
            <text x="50" y="60" textAnchor="middle" fontSize="6.5" fill="rgba(255,255,255,0.65)">
              work done
            </text>
          </svg>
        </div>
      </div>
    </div>
  )
}
