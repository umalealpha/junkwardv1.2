'use client'

import type { Theme } from '@/lib/themes'
import type { TransformationMonth } from '@/lib/api'
import { clampPct, fmtMoneyCompact, safeNum } from './format'

interface PathStepperProps {
  theme: Theme
  months: TransformationMonth[]
  reduceMotion: boolean
}

// The 4-month journey as a horizontal stepper. Each node is a mini ring
// (percent complete) plus the count/FTE/saving facts; nodes are joined by a
// track whose fill animates to the node's own percent — never a shared
// average, each segment tells its own month's truth.
export function PathStepper({ theme, months, reduceMotion }: PathStepperProps) {
  return (
    <div
      className="grid gap-4"
      style={{ gridTemplateColumns: `repeat(${Math.max(months.length, 1)}, minmax(0, 1fr))` }}
    >
      {months.map((m, idx) => {
        const pct = clampPct(m.percent)
        const complete = m.count > 0 && m.done === m.count
        return (
          <div key={m.month} className="relative flex flex-col items-center text-center">
            {idx > 0 && (
              <div
                aria-hidden="true"
                className="absolute top-6 right-1/2 h-0.5 w-full -z-0"
                style={{ background: theme.cardBdr }}
              />
            )}
            <div
              className="relative z-10 w-12 h-12 rounded-full flex items-center justify-center font-bold text-sm"
              style={{
                background: complete ? theme.ok : theme.card,
                color: complete ? '#FFFFFF' : theme.navy,
                border: `2px solid ${complete ? theme.ok : theme.orange}`,
              }}
            >
              {pct}%
            </div>
            <div className="mt-3 text-sm font-semibold" style={{ color: theme.navy }}>
              Month {m.month}
            </div>
            <div className="text-xs mt-0.5" style={{ color: theme.t2 }}>
              {m.done}/{m.count} done
            </div>
            <div className="w-full h-1.5 rounded-full overflow-hidden mt-2" style={{ background: theme.g100 }}>
              <div
                className="tb-fill h-full rounded-full"
                style={{
                  background: theme.orange,
                  width: `${pct}%`,
                  animation: reduceMotion ? 'none' : `tb-fill 700ms cubic-bezier(0.16,1,0.3,1) ${idx * 120}ms both`,
                }}
              />
            </div>
            <div className="mt-2 text-xs space-y-0.5" style={{ color: theme.t2 }}>
              <div>{safeNum(m.fte_released, 1)} FTE released</div>
              <div>{fmtMoneyCompact(m.saving_bwp)} saving</div>
            </div>
          </div>
        )
      })}
    </div>
  )
}
