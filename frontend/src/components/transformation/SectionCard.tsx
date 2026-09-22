'use client'

import type { ReactNode } from 'react'
import type { Theme } from '@/lib/themes'

interface SectionCardProps {
  theme: Theme
  title: string
  subtitle?: string
  right?: ReactNode
  children: ReactNode
  /** Staggered entrance index — 0-based. Ignored under reduced motion. */
  index?: number
  reduceMotion: boolean
  id?: string
}

// One card shell for every board section: title + optional subtitle + an
// optional right-aligned control, with a staggered fade/rise entrance that
// only ever animates transform + opacity (compositor-friendly) and snaps to
// the resting state immediately when reduced motion is on.
export function SectionCard({
  theme, title, subtitle, right, children, index = 0, reduceMotion, id,
}: SectionCardProps) {
  return (
    <section
      id={id}
      aria-labelledby={id ? `${id}-h` : undefined}
      className="rounded-xl overflow-hidden"
      style={{
        background: theme.card,
        border: `1px solid ${theme.cardBdr}`,
        boxShadow: theme.cardSh,
        opacity: reduceMotion ? 1 : 0,
        transform: reduceMotion ? 'none' : 'translateY(14px)',
        animation: reduceMotion ? 'none' : `tb-rise 560ms cubic-bezier(0.16,1,0.3,1) ${Math.min(index, 8) * 70}ms forwards`,
      }}
    >
      <div className="px-5 py-4 flex items-start justify-between gap-3 border-b" style={{ borderColor: theme.cardBdr }}>
        <div>
          <h2 id={id ? `${id}-h` : undefined} className="text-base font-semibold" style={{ color: theme.navy }}>
            {title}
          </h2>
          {subtitle && (
            <p className="text-xs mt-0.5" style={{ color: theme.t2 }}>{subtitle}</p>
          )}
        </div>
        {right}
      </div>
      <div className="p-5">{children}</div>
    </section>
  )
}
