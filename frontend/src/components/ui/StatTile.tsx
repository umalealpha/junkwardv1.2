'use client'

/**
 * StatTile — the KPI tile used across the redesigned Omni screens (2026-09-02).
 *
 * One shared tile so every page's headline numbers read as siblings of the
 * Payment Requests board: a clean surface, a thin accent rule, an uppercase
 * label, one big tabular figure and a quiet sub-line. Colours come from the
 * active theme, so it is slate-blue under the Professional theme and follows
 * whatever theme is set — never a hardcoded brand colour.
 */

import type { LucideIcon } from 'lucide-react'
import { useTheme } from '@/contexts/ThemeContext'

type Tone = 'accent' | 'pos' | 'warn' | 'neg' | 'teal' | 'info'

export function StatTile({
  label, value, sub, icon: Icon, tone = 'accent', subTone,
}: {
  label: string
  value: React.ReactNode
  sub?: React.ReactNode
  icon?: LucideIcon
  tone?: Tone
  subTone?: Tone
}) {
  const { theme } = useTheme()
  const accent: Record<Tone, string> = {
    accent: theme.orange, pos: theme.ok, warn: theme.wr,
    neg: theme.er, teal: theme.teal, info: theme.inf,
  }
  const rule = accent[tone]
  const subColor = subTone ? accent[subTone] : theme.t3

  return (
    <div
      className="relative overflow-hidden rounded-xl p-4"
      style={{ background: theme.card, border: `1px solid ${theme.cardBdr}`, boxShadow: theme.cardSh }}
    >
      <span className="absolute left-0 top-0 bottom-0 w-[3px]" style={{ background: rule }} />
      <div className="flex items-center justify-between">
        <span
          className="text-[10.5px] font-semibold uppercase tracking-[0.09em]"
          style={{ color: theme.t2 }}
        >
          {label}
        </span>
        {Icon && <Icon className="w-[15px] h-[15px]" style={{ color: rule }} strokeWidth={1.8} />}
      </div>
      <div
        className="mt-2 text-[26px] leading-none font-semibold tabular-nums tracking-[-0.03em]"
        style={{ color: theme.text }}
      >
        {value}
      </div>
      {sub != null && (
        <div className="mt-1.5 text-[12px]" style={{ color: subColor }}>
          {sub}
        </div>
      )}
    </div>
  )
}
